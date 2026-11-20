import { inflateRawSync } from 'node:zlib';
import { expect, type Page } from '@playwright/test';
import { deletePreview, previewBundle, saveBundle } from '../../../src/lib/api/diagnostics';
import { ControllerClient } from '../../../src/lib/transport/controller-client';
import {
  validateManagedTransmitDocument,
  type ManagedTransmitDocument,
} from '../../../src/lib/types/managed-transmit';
import type { ServerStatePublic } from '../../../src/lib/types/state';

/**
 * The station both browsers' rows run against: the Core WebServer over
 * the IC-7610 LAN fake (tests/e2e/remote_web_server.py) plus the test
 * sidecar that exposes the simulator's observable state (keyed, frequency)
 * without any test hook inside src/.
 */

export const STATION_PORT = 18780;
export const STATION_ORIGIN = `http://127.0.0.1:${STATION_PORT}`;
const SIM_ORIGIN = `http://127.0.0.1:${STATION_PORT + 1}`;

/** The sidecar's `GET /sim` payload (producer: SimSnapshot in
 * tests/e2e/remote_web_server.py). */
export interface SimState {
  ptt_on: boolean;
  freq_hz: number;
  /** The simulator's own PTT journal as [unix seconds, keyed]. */
  ptt_transitions: [number, boolean][];
  /** Client audio frames the simulator received with a non-silent payload. */
  tx_audio_signal_frames: number;
}

/** The producer's field types, as the sidecar serves them at
 * `GET /sim/contract` (the annotations of its SimSnapshot response and
 * SimFreqRequest request dataclasses). A change on either side makes the
 * parser/builder throw instead of mis-reading or mis-sending. */
const SIM_CONTRACT: Record<string, Record<string, string>> = {
  SimSnapshot: {
    ptt_on: 'bool',
    freq_hz: 'int',
    ptt_transitions: 'tuple[tuple[float, bool], ...]',
    tx_audio_signal_frames: 'int',
  },
  SimFreqRequest: { hz: 'int' },
};

/** The sidecar's `POST /sim/freq` body (producer: SimFreqRequest). */
export interface SimFreqRequest {
  hz: number;
}

function assertContract(contract: unknown, name: string): void {
  const served =
    typeof contract === 'object' && contract !== null
      ? (contract as Record<string, unknown>)[name]
      : undefined;
  if (JSON.stringify(sortKeys(served)) !== JSON.stringify(sortKeys(SIM_CONTRACT[name]))) {
    throw new Error(`sim contract drift in ${name}: sidecar serves ${JSON.stringify(served)}`);
  }
}

/** Build the /sim/freq body, after checking the served request contract. */
export function buildFreqRequest(contract: unknown, hz: number): SimFreqRequest {
  assertContract(contract, 'SimFreqRequest');
  if (!Number.isInteger(hz) || hz <= 0) throw new Error(`hz must be a positive integer: ${hz}`);
  return { hz };
}

/** Validate a sidecar response against the served contract. */
export function parseSimState(contract: unknown, value: unknown): SimState {
  assertContract(contract, 'SimSnapshot');
  const fields = Object.keys(SIM_CONTRACT.SimSnapshot);
  const bad = (why: string) => new Error(`sim state violates its contract (${why}): ${JSON.stringify(value)}`);
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw bad('not an object');
  const record = value as Record<string, unknown>;
  if (Object.keys(record).sort().join() !== fields.sort().join()) {
    throw bad('keys');
  }
  if (typeof record.ptt_on !== 'boolean') throw bad('ptt_on');
  if (!Number.isInteger(record.freq_hz)) throw bad('freq_hz');
  if (!Number.isInteger(record.tx_audio_signal_frames)) throw bad('tx_audio_signal_frames');
  const transitions = record.ptt_transitions;
  if (
    !Array.isArray(transitions) ||
    !transitions.every(
      (row) =>
        Array.isArray(row) &&
        row.length === 2 &&
        typeof row[0] === 'number' &&
        typeof row[1] === 'boolean',
    )
  ) {
    throw bad('ptt_transitions');
  }
  return record as unknown as SimState;
}

function sortKeys(value: unknown): unknown {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return value;
  return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));
}

/** The server's own view (``GET /api/v1/state`` from Node, not the page),
 * typed with the generated ServerStatePublic contract. */
export async function stationState(): Promise<ServerStatePublic> {
  const response = await fetch(`${STATION_ORIGIN}/api/v1/state`);
  if (!response.ok) throw new Error(`/api/v1/state answered ${response.status}`);
  const state: unknown = await response.json();
  if (typeof state !== 'object' || state === null) throw new Error('/api/v1/state: not an object');
  return state as ServerStatePublic;
}

/** The station's managed-transmit document (``GET /api/v1/managed-transmit``
 * from Node), through the production validator. */
export async function stationManagedTransmit(): Promise<ManagedTransmitDocument> {
  const response = await fetch(`${STATION_ORIGIN}/api/v1/managed-transmit`);
  if (!response.ok) throw new Error(`/api/v1/managed-transmit answered ${response.status}`);
  return validateManagedTransmitDocument(await response.json());
}

/** Wait until the UI's control link is up, read from the server's
 * wsClients count — DOM-free, so it holds in both layouts and browsers. */
export async function expectControlLink(): Promise<void> {
  await expect
    .poll(async () => (await stationState()).wsClients?.control ?? 0)
    .toBeGreaterThan(0);
}

let simContract: Promise<unknown> | null = null;

function servedSimContract(): Promise<unknown> {
  return (simContract ??= fetch(`${SIM_ORIGIN}/sim/contract`).then((r) => r.json()));
}

/** The simulator's current observable state, from the sidecar, validated
 * against the contract the sidecar serves. */
export async function simState(): Promise<SimState> {
  const contract = servedSimContract();
  const response = await fetch(`${SIM_ORIGIN}/sim`);
  if (!response.ok) throw new Error(`sidecar /sim answered ${response.status}`);
  return parseSimState(await contract, await response.json());
}

/** Change the frequency on the station side (silent; the radio's ~2 s
 * state-poll cadence carries it to the UI). */
export async function setStationFrequency(hz: number): Promise<void> {
  const response = await fetch(`${SIM_ORIGIN}/sim/freq`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(buildFreqRequest(await servedSimContract(), hz)),
  });
  if (!response.ok) throw new Error(`sidecar /sim/freq answered ${response.status}`);
}

/** Latency (ms) between the release and the simulator's final unkey, or
 * null while the simulator has not settled unkeyed for this press/release:
 * it must be OFF now, its last journal entry must be an OFF at or after the
 * release, and it must have been keyed between press and release. */
export function unkeyLatencyMs(
  state: SimState,
  pressedAtMs: number,
  releasedAtMs: number,
): number | null {
  if (state.ptt_on) return null;
  const last = state.ptt_transitions.at(-1);
  if (!last || last[1] || last[0] * 1000 < releasedAtMs) return null;
  const keyedByThisPress = state.ptt_transitions.some(
    ([at, keyed]) => keyed && at * 1000 >= pressedAtMs && at * 1000 <= releasedAtMs,
  );
  return keyedByThisPress ? last[0] * 1000 - releasedAtMs : null;
}

/** Wait until the simulator has unkeyed for this press/release and return
 * the latency in ms; rejects if it does not within `timeoutMs`. */
export async function simUnkeyLatencyMs(
  pressedAtMs: number,
  releasedAtMs: number,
  timeoutMs = 10_000,
): Promise<number> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const latency = unkeyLatencyMs(await simState(), pressedAtMs, releasedAtMs);
    if (latency !== null) return latency;
    if (Date.now() > deadline) {
      throw new Error(`simulator did not unkey within ${timeoutMs} ms`);
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

/** The origin a destination compares by: ws/wss are the http/https
 * equivalents of the station origin. */
function httpOrigin(url: string): string {
  const parsed = new URL(url);
  if (parsed.protocol === 'ws:') return `http://${parsed.host}`;
  if (parsed.protocol === 'wss:') return `https://${parsed.host}`;
  return parsed.origin;
}

/** Origins of every http(s)/ws(s) destination that is not the station. */
export function foreignDestinations(urls: readonly string[]): string[] {
  return urls.map(httpOrigin).filter((origin) => origin !== STATION_ORIGIN);
}

/** Every HTTP request and WebSocket the page opened so far (all origins). */
export function trackDestinations(page: Page): () => { requests: string[]; sockets: string[] } {
  const requests: string[] = [];
  const sockets: string[] = [];
  page.on('request', (request) => requests.push(request.url()));
  page.on('websocket', (socket) => sockets.push(socket.url()));
  return () => ({ requests, sockets });
}

/** What the stubbed microphone does: `tone` gives the oscillator stream,
 * `refuse` rejects getUserMedia as a denied permission does. */
export type MicrophoneMode = 'tone' | 'refuse';

/**
 * The ONLY allowed microphone stub (adviser brief §5): replace
 * navigator.mediaDevices.getUserMedia with an oscillator →
 * MediaStreamDestination stream, in page.addInitScript, so it works in
 * Chromium and WebKit alike. Everything after getUserMedia is the
 * production path. In `refuse` mode the same stub rejects with a
 * `NotAllowedError` DOMException instead.
 */
export async function stubMicrophone(page: Page, mode: MicrophoneMode = 'tone'): Promise<void> {
  await page.addInitScript((micMode: MicrophoneMode) => {
    const media = navigator.mediaDevices;
    if (!media) throw new Error('navigator.mediaDevices is missing');
    // Define it on the prototype: WebKit may recreate the navigator
    // .mediaDevices wrapper object, which would drop an instance property.
    const stub = async (constraints?: MediaStreamConstraints) => {
      if (micMode === 'refuse') {
        throw new DOMException('Permission denied', 'NotAllowedError');
      }
      // Honour the audio constraints the page asks for, as a real device
      // does: the page's Opus encoder is configured for that rate and
      // channel count and refuses a stream that differs (a default
      // AudioContext rate and the 2-channel destination fail it).
      (window as unknown as { __micConstraints?: unknown }).__micConstraints = constraints?.audio;
      const audio = constraints?.audio;
      const wanted = typeof audio === 'object' && audio !== null ? audio : {};
      const rate = (wanted.sampleRate as number | undefined) ?? undefined;
      const channels = (wanted.channelCount as number | undefined) ?? undefined;
      const context = rate ? new AudioContext({ sampleRate: rate }) : new AudioContext();
      const oscillator = context.createOscillator();
      const destination = context.createMediaStreamDestination();
      if (channels) destination.channelCount = channels;
      oscillator.frequency.value = 1000;
      oscillator.connect(destination);
      oscillator.start();
      return destination.stream;
    };
    Object.defineProperty(Object.getPrototypeOf(media), 'getUserMedia', {
      value: stub,
      configurable: true,
      writable: true,
    });
  }, mode);
}

/** Open the ordinary UI page served by Core, with the microphone stub in
 * place (rows that never transmit are unaffected by it). */
export async function openRemoteUi(page: Page, microphone: MicrophoneMode = 'tone'): Promise<void> {
  await stubMicrophone(page, microphone);
  await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });
}

/**
 * Count the decoded receive buffers the page hands to its AudioContext for
 * playback (every `AudioBufferSourceNode.start` with a buffer of at least
 * 200 frames), observed unchanged, as the panorama journey witness does.
 */
export async function installPlaybackWitness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const win = window as Window & { __playbackBuffers?: number };
    win.__playbackBuffers = 0;
    const nativeStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args: Parameters<typeof nativeStart>) {
      nativeStart.apply(this, args);
      if (this.buffer && this.buffer.length >= 200) win.__playbackBuffers! += 1;
    };
  });
}

/** Receive buffers scheduled for playback so far (installPlaybackWitness). */
export async function playbackBuffers(page: Page): Promise<number> {
  return page.evaluate(
    () => (window as Window & { __playbackBuffers?: number }).__playbackBuffers ?? 0,
  );
}

/** What the page sent so far: the names of its control-socket commands,
 * the directions of its audio_start frames, and the operations it POSTed
 * to the managed-transmit command endpoint. */
export interface SentTraffic {
  commands: string[];
  audioStarts: string[];
  managedCommands: string[];
}

export function trackSentTraffic(page: Page): () => SentTraffic {
  const traffic: SentTraffic = { commands: [], audioStarts: [], managedCommands: [] };
  const parse = (text: string | null | undefined): Record<string, unknown> | null => {
    try {
      const value: unknown = JSON.parse(text ?? '');
      return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  };
  page.on('websocket', (socket) => {
    socket.on('framesent', ({ payload }) => {
      const frame = typeof payload === 'string' ? parse(payload) : null;
      if (!frame) return;
      if (frame.type === 'cmd' || frame.type === 'command') traffic.commands.push(String(frame.name));
      if (frame.type === 'audio_start') traffic.audioStarts.push(String(frame.direction));
    });
  });
  page.on('request', (request) => {
    if (request.method() !== 'POST') return;
    if (new URL(request.url()).pathname !== '/api/v1/managed-transmit/command') return;
    traffic.managedCommands.push(String(parse(request.postData())?.operation));
  });
  return () => traffic;
}

/** The frequency readout renders MHz.kHz.Hz digit groups; compare digits.
 * `timeoutMs` widens only the station→UI direction, whose freshness the
 * IC-7610 profile serves on a 2 s poll cadence (adaptive decay caps it at
 * 4 s, rigs/ic7610.toml [state_acquisition]). */
export async function expectReadoutHz(
  page: Page,
  hz: number,
  timeoutMs = 10_000,
): Promise<void> {
  const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
  await expect
    .poll(async () => Number(((await readout.textContent()) ?? '').replace(/\D/g, '')), {
      timeout: timeoutMs,
    })
    .toBe(hz);
}

/** Register a first-text-frame listener per channel, send the attaches,
 * resolve with every channel's first text frame. Self-contained: shipped
 * into the page via toString(). */
export async function collectFirstTexts(
  channels: Record<string, EventTarget>,
  send: () => void,
  deadlinesMs: Record<string, number>,
): Promise<Record<string, string>> {
  const first = (label: string) =>
    new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${label} timed out`)), deadlinesMs[label]);
      const onMessage = (event: Event) => {
        const data = (event as MessageEvent).data;
        if (typeof data !== 'string') return;
        clearTimeout(timer);
        channels[label].removeEventListener('message', onMessage);
        resolve(data);
      };
      channels[label].addEventListener('message', onMessage);
    });
  // Every listener exists BEFORE any attach goes out: the channels are
  // independent and unordered, so scope/audio may answer before control.
  const labels = Object.keys(channels);
  const pending = labels.map((label) => first(label));
  send();
  const texts = await Promise.all(pending);
  return Object.fromEntries(labels.map((label, index) => [label, texts[index]]));
}

/** The station's reply to POST /api/v1/transport/webrtc/offer (producer:
 * WebServer._handle_webrtc_offer; a success is `{status: "ok", sessionId,
 * sdp, type}`, a refusal `{status: "error", code}`). The SDP pair is the
 * W3C RTCSessionDescriptionInit and `status` the only discriminator the
 * harness reads; tests/testkit/test_remote_web_server_sidecar.py pins the
 * key set of a REAL reply. */
export type RelayedAnswer = RTCSessionDescriptionInit & { status: 'ok' };

/** Narrow a relayed reply by its discriminator. The SDP itself is not
 * re-checked here: the page's `setRemoteDescription` is its validator. */
export function parseRelayedAnswer(value: unknown): RelayedAnswer {
  if (
    typeof value !== 'object' ||
    value === null ||
    (value as { status?: unknown }).status !== 'ok'
  ) {
    throw new Error(`offer refused: ${JSON.stringify(value)}`);
  }
  return value as RelayedAnswer;
}

/**
 * A controller key for the station, acquired through the PRODUCTION
 * `ControllerClient` (src/lib/transport/controller-client.ts): it POSTs
 * /api/v1/controller, and its own `adopt()` is the only validator of the
 * grant (key shape, generation, ttl_ms, heartbeat_ms, role), so the harness
 * keeps no copy of the grant shape. The client's relative `/api/v1/...`
 * calls are pointed at the station for the duration of the call.
 */
export async function acquireControllerKey(): Promise<string> {
  const client = new ControllerClient();
  try {
    return await withStationFetch(async () => {
      await client.prepare();
      // A local-mode station whose grant the client rejects leaves it
      // sessionless instead of throwing (its own tolerance for older cores).
      const key = client.headers()['X-RigPlane-Controller'];
      if (!key) throw new Error('the station granted no session the ControllerClient accepts');
      return key;
    });
  } finally {
    client.lose(); // stops the heartbeat timer; no DELETE, the TTL reaps it
  }
}

/** Run a production client whose relative `/api/v1/...` calls must reach
 * the station from Node: they are pointed at it for the duration of `run`. */
async function withStationFetch<T>(run: () => Promise<T>): Promise<T> {
  const original = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    original(
      typeof input === 'string' && input.startsWith('/') ? `${STATION_ORIGIN}${input}` : input,
      init,
    )) as typeof fetch;
  try {
    return await run();
  } finally {
    globalThis.fetch = original;
  }
}

/**
 * The log files of the support bundle the station builds for a diagnostics
 * preview, read through the PRODUCTION diagnostics client
 * (src/lib/api/diagnostics.ts: previewBundle, then saveBundle of that same
 * preview, which is the bundle a Send would upload; the preview is deleted
 * afterwards). Returns the text of every `logs/` entry, concatenated.
 */
export async function supportBundleLogs(): Promise<string> {
  return withStationFetch(async () => {
    const preview = await previewBundle({});
    try {
      const zip = Buffer.from(await (await saveBundle(preview.preview_id, preview.csrf_token)).arrayBuffer());
      return [...readZip(zip)]
        .filter(([name]) => name.startsWith('logs/'))
        .map(([, data]) => data.toString('utf8'))
        .join('\n');
    } finally {
      await deletePreview(preview.preview_id, preview.csrf_token);
    }
  });
}

/** Occurrences of `needle` in the support bundle's logs. */
export async function supportBundleLogCount(needle: string): Promise<number> {
  return (await supportBundleLogs()).split(needle).length - 1;
}

/**
 * Entries of a ZIP archive (name -> uncompressed bytes), read from its
 * central directory. Only what Python's zipfile writes for a support bundle
 * is accepted (stored or deflated entries, no ZIP64); anything else throws.
 */
export function readZip(zip: Buffer): Map<string, Buffer> {
  let end = zip.length - 22;
  while (end >= 0 && zip.readUInt32LE(end) !== 0x06054b50) end -= 1;
  if (end < 0) throw new Error('not a ZIP archive: no end-of-central-directory record');
  const entries = new Map<string, Buffer>();
  let at = zip.readUInt32LE(end + 16);
  for (let index = zip.readUInt16LE(end + 10); index > 0; index -= 1) {
    if (zip.readUInt32LE(at) !== 0x02014b50) throw new Error(`bad central directory entry at ${at}`);
    const method = zip.readUInt16LE(at + 10);
    const compressedSize = zip.readUInt32LE(at + 20);
    const nameLength = zip.readUInt16LE(at + 28);
    const local = zip.readUInt32LE(at + 42);
    const name = zip.toString('utf8', at + 46, at + 46 + nameLength);
    if (zip.readUInt32LE(local) !== 0x04034b50) throw new Error(`bad local header for ${name}`);
    const start = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
    const data = zip.subarray(start, start + compressedSize);
    if (method === 0) entries.set(name, data);
    else if (method === 8) entries.set(name, inflateRawSync(data));
    else throw new Error(`unsupported compression method ${method} for ${name}`);
    at += 46 + nameLength + zip.readUInt16LE(at + 30) + zip.readUInt16LE(at + 32);
  }
  return entries;
}
