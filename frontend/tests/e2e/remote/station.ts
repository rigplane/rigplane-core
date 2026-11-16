import { expect, type Page } from '@playwright/test';
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
}

/** The producer's field types, as the sidecar serves them at
 * `GET /sim/contract` (the SimSnapshot annotations). A change on either
 * side makes `parseSimState` throw instead of mis-reading. */
const SIM_CONTRACT: Record<string, string> = {
  ptt_on: 'bool',
  freq_hz: 'int',
  ptt_transitions: 'tuple[tuple[float, bool], ...]',
};

/** Validate a sidecar response against the served contract. */
export function parseSimState(contract: unknown, value: unknown): SimState {
  if (JSON.stringify(sortKeys(contract)) !== JSON.stringify(sortKeys(SIM_CONTRACT))) {
    throw new Error(`sim contract drift: sidecar serves ${JSON.stringify(contract)}`);
  }
  const bad = (why: string) => new Error(`sim state violates its contract (${why}): ${JSON.stringify(value)}`);
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw bad('not an object');
  const record = value as Record<string, unknown>;
  if (Object.keys(record).sort().join() !== Object.keys(SIM_CONTRACT).sort().join()) {
    throw bad('keys');
  }
  if (typeof record.ptt_on !== 'boolean') throw bad('ptt_on');
  if (!Number.isInteger(record.freq_hz)) throw bad('freq_hz');
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

/** Wait until the UI's control link is up, read from the server's
 * wsClients count — DOM-free, so it holds in both layouts and browsers. */
export async function expectControlLink(): Promise<void> {
  await expect
    .poll(async () => (await stationState()).wsClients?.control ?? 0)
    .toBeGreaterThan(0);
}

let simContract: Promise<unknown> | null = null;

/** The simulator's current observable state, from the sidecar, validated
 * against the contract the sidecar serves. */
export async function simState(): Promise<SimState> {
  simContract ??= fetch(`${SIM_ORIGIN}/sim/contract`).then((r) => r.json());
  const response = await fetch(`${SIM_ORIGIN}/sim`);
  if (!response.ok) throw new Error(`sidecar /sim answered ${response.status}`);
  return parseSimState(await simContract, await response.json());
}

/** Change the frequency on the station side (silent; the radio's ~2 s
 * state-poll cadence carries it to the UI). */
export async function setStationFrequency(hz: number): Promise<void> {
  const response = await fetch(`${SIM_ORIGIN}/sim/freq`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ hz }),
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

/**
 * The ONLY allowed microphone stub (adviser brief §5): replace
 * navigator.mediaDevices.getUserMedia with an oscillator →
 * MediaStreamDestination stream, in page.addInitScript, so it works in
 * Chromium and WebKit alike. Everything after getUserMedia is the
 * production path.
 */
export async function stubMicrophone(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const media = navigator.mediaDevices;
    if (!media) throw new Error('navigator.mediaDevices is missing');
    // Define it on the prototype: WebKit may recreate the navigator
    // .mediaDevices wrapper object, which would drop an instance property.
    const stub = async (constraints?: MediaStreamConstraints) => {
      // Honour the audio constraints the page asks for, as a real device
      // does: the page's Opus encoder is configured for that rate and
      // channel count and refuses a stream that differs (a default
      // AudioContext rate and the 2-channel destination fail it).
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
  });
}

/** Open the ordinary UI page served by Core, with the microphone stub in
 * place (rows that never transmit are unaffected by it). */
export async function openRemoteUi(page: Page): Promise<void> {
  await stubMicrophone(page);
  await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });
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
 * WebServer._handle_webrtc_offer, `{"status": "ok", sdp, type}`); the SDP
 * pair is the W3C RTCSessionDescriptionInit. */
export type RelayedAnswer = RTCSessionDescriptionInit & { status: 'ok'; sdp: string };

export function parseRelayedAnswer(value: unknown): RelayedAnswer {
  const record = value as Record<string, unknown> | null;
  if (
    typeof record !== 'object' ||
    record === null ||
    record.status !== 'ok' ||
    typeof record.sdp !== 'string' ||
    record.type !== 'answer'
  ) {
    throw new Error(`offer refused or malformed answer: ${JSON.stringify(value)}`);
  }
  return { status: 'ok', sdp: record.sdp, type: 'answer' };
}

/** The controller grant of POST /api/v1/controller. The frontend's own
 * `Grant` type is private to controller-client.ts (not exported), so the
 * fields the probe uses are checked here exactly as its `adopt()` does. */
export interface ControllerGrant {
  controller_key: string;
  generation: number;
}

export function parseControllerGrant(value: unknown): ControllerGrant {
  const record = value as Record<string, unknown> | null;
  if (
    typeof record !== 'object' ||
    record === null ||
    typeof record.controller_key !== 'string' ||
    !/^[0-9a-f]{64}$/.test(record.controller_key) ||
    typeof record.generation !== 'number' ||
    !Number.isSafeInteger(record.generation) ||
    record.generation < 0
  ) {
    throw new Error(`controller grant malformed: ${JSON.stringify(value)}`);
  }
  return { controller_key: record.controller_key, generation: record.generation };
}
