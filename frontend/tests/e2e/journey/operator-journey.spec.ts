import { writeFile } from 'node:fs/promises';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { parseCurrentScopeFrame, parseScopeGranted, type ScopeFrame } from '../../../src/lib/runtime/scope-frame';
import type { ScopeGranted, ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import { loadScopeWireGolden, scopeWire } from '../../../src/lib/runtime/__tests__/scope-wire-fixture';
import { startJourneyServer, type JourneyServerHandle } from './server-process';

/**
 * Operator journey against the real WebServer with the in-memory IC-7610
 * SerialMockRadio (tests/e2e/journey_web_server.py): page loads and the
 * control link is connected, then tune, change mode, narrow the filter.
 * Tune, mode and filter width are each read back three ways: the command frame
 * the page sent over the WebSocket, the server's /api/v1/state, and the DOM.
 *
 * A second scenario runs against a server started with --read-only (second
 * webServer entry in playwright.journey.config.ts): the phone layout's PTT
 * press sends ptt_on and the server refuses it. SerialMockRadio opens no
 * device, and on this fake a ptt_on is refused with or without --read-only
 * (no managed-transmit authority is composed).
 *
 * The remaining two scenarios start their fakes (and the killed server's
 * replacement) themselves via server-process.ts so they can SIGKILL the
 * process group mid-session and restart on the same port: one asserts the
 * control link and FRESH state, the other (started with --audio) asserts the
 * rx-audio LIVE monitor choice and that RX audio is re-armed after the
 * restart, proven by the audio_start wire frames and wsClients.audio.
 */

interface WsCommandRecord {
  id: string;
  name: string;
  params: Record<string, unknown>;
}

interface WsRefusalRecord {
  id: string;
  error: string;
  message: string;
}

interface WsAudioStartRecord {
  socket: string;
  direction: string;
}

interface WsAudioFrameRecord {
  socket: string;
  type: string;
  message?: string;
}

const JOURNEY_COMMANDS = ['set_freq', 'set_mode', 'set_filter_width'];

// Adapted from installWebSocketInterceptor in v2-ui-interactive.impl.ts (not
// imported: importing that file registers its tests); keeps the id, name and
// params of each outgoing command frame, the id, error and message of each
// incoming response frame with ok: false, and the path of each socket that
// opened. For /api/v1/audio sockets it also keeps each outgoing audio_start
// frame's direction and each incoming audio_format / error frame (audio_stats
// and binary audio frames are filtered out by type).
const AUDIO_SOCKET_PATH = '/api/v1/audio';

async function installWebSocketInterceptor(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const win = window as Window & {
      __WS_COMMANDS__?: WsCommandRecord[];
      __WS_REFUSALS__?: WsRefusalRecord[];
      __WS_OPENED__?: string[];
      __WS_AUDIO_STARTS__?: WsAudioStartRecord[];
      __WS_AUDIO_FRAMES__?: WsAudioFrameRecord[];
    };
    win.__WS_COMMANDS__ = [];
    win.__WS_REFUSALS__ = [];
    win.__WS_OPENED__ = [];
    win.__WS_AUDIO_STARTS__ = [];
    win.__WS_AUDIO_FRAMES__ = [];
    const OriginalWebSocket = window.WebSocket;

    class InterceptedWebSocket extends OriginalWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
        const socketPath = new URL(String(url), window.location.href).pathname;
        this.addEventListener('open', () => {
          win.__WS_OPENED__?.push(socketPath);
        });
        this.addEventListener('message', (event: MessageEvent) => {
          if (typeof event.data !== 'string') return;
          try {
            const parsed = JSON.parse(event.data) as Record<string, unknown>;
            if (parsed.type === 'response' && parsed.ok === false) {
              win.__WS_REFUSALS__?.push({
                id: String(parsed.id),
                error: String(parsed.error),
                message: String(parsed.message),
              });
            }
            if (
              socketPath === '/api/v1/audio'
              && (parsed.type === 'audio_format' || parsed.type === 'error')
            ) {
              win.__WS_AUDIO_FRAMES__?.push(
                parsed.type === 'error'
                  ? { socket: socketPath, type: 'error', message: String(parsed.message) }
                  : { socket: socketPath, type: 'audio_format' },
              );
            }
          } catch {
            // Ignore non-JSON frames.
          }
        });
        const originalSend = this.send.bind(this);
        this.send = (data: string | ArrayBufferLike | Blob | ArrayBufferView) => {
          if (typeof data === 'string') {
            try {
              const parsed = JSON.parse(data) as Record<string, unknown>;
              if (parsed.type === 'cmd' || parsed.type === 'command') {
                win.__WS_COMMANDS__?.push({
                  id: String(parsed.id),
                  name: String(parsed.name),
                  params: (parsed.params ?? {}) as Record<string, unknown>,
                });
              }
              if (socketPath === '/api/v1/audio' && parsed.type === 'audio_start') {
                win.__WS_AUDIO_STARTS__?.push({
                  socket: socketPath,
                  direction: String(parsed.direction),
                });
              }
            } catch {
              // Ignore non-JSON frames.
            }
          }
          return originalSend(data);
        };
      }
    }

    Object.setPrototypeOf(InterceptedWebSocket, OriginalWebSocket);
    Object.defineProperty(InterceptedWebSocket, 'CONNECTING', { value: OriginalWebSocket.CONNECTING });
    Object.defineProperty(InterceptedWebSocket, 'OPEN', { value: OriginalWebSocket.OPEN });
    Object.defineProperty(InterceptedWebSocket, 'CLOSING', { value: OriginalWebSocket.CLOSING });
    Object.defineProperty(InterceptedWebSocket, 'CLOSED', { value: OriginalWebSocket.CLOSED });
    window.WebSocket = InterceptedWebSocket as typeof WebSocket;
  });
}

/** Command frames with the given names the page has sent so far, in send order. */
async function sentCommands(page: Page, names: string[]): Promise<WsCommandRecord[]> {
  return page.evaluate((wanted) => {
    const win = window as Window & { __WS_COMMANDS__?: WsCommandRecord[] };
    return (win.__WS_COMMANDS__ ?? []).filter((command) => wanted.includes(command.name));
  }, names);
}

/** Journey command frames the page has sent so far, in send order. */
async function sentJourneyCommands(page: Page): Promise<WsCommandRecord[]> {
  return sentCommands(page, JOURNEY_COMMANDS);
}

/** Paths of the WebSockets that have opened so far, in open order. */
async function openedSockets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const win = window as Window & { __WS_OPENED__?: string[] };
    return win.__WS_OPENED__ ?? [];
  });
}

/** audio_start frames the page has sent on the audio socket, in send order. */
async function audioStarts(page: Page): Promise<WsAudioStartRecord[]> {
  return page.evaluate(() => {
    const win = window as Window & { __WS_AUDIO_STARTS__?: WsAudioStartRecord[] };
    return win.__WS_AUDIO_STARTS__ ?? [];
  });
}

/** audio_format / error frames the audio socket has received, in arrival order. */
async function audioFrames(page: Page): Promise<WsAudioFrameRecord[]> {
  return page.evaluate(() => {
    const win = window as Window & { __WS_AUDIO_FRAMES__?: WsAudioFrameRecord[] };
    return win.__WS_AUDIO_FRAMES__ ?? [];
  });
}

/** Error responses the server has sent to the page so far, in arrival order. */
async function receivedRefusals(page: Page): Promise<WsRefusalRecord[]> {
  return page.evaluate(() => {
    const win = window as Window & { __WS_REFUSALS__?: WsRefusalRecord[] };
    return win.__WS_REFUSALS__ ?? [];
  });
}

async function sentParams(page: Page, name: string): Promise<Array<Record<string, unknown>>> {
  return (await sentJourneyCommands(page))
    .filter((command) => command.name === name)
    .map((command) => command.params);
}

async function expectServerMain(
  page: Page,
  expected: { freqHz?: number; mode?: string; filterWidth?: number },
): Promise<void> {
  await expect
    .poll(async () => (await page.request.get('/api/v1/state')).json())
    .toMatchObject({ main: expected });
}

/** The readout renders MHz.kHz.Hz digit groups; compare the digits only. */
async function expectReadoutHz(readout: Locator, hz: number): Promise<void> {
  await expect
    .poll(async () => Number(((await readout.textContent()) ?? '').replace(/\D/g, '')))
    .toBe(hz);
}

test('operator journey: connect, tune, change mode, narrow the filter', async ({ page }) => {
  await installWebSocketInterceptor(page);
  await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });

  const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
  const modeKey = (name: string) => page.locator(`[data-testid="standard-mode-${name}"]`).getByRole('radio');
  const width = page.locator('[data-testid="filter-width"] input[type=range]');

  // Connect: the control link reads connected and the fake's baseline is on screen.
  await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
  await expect(page.locator('.control-link-lost')).toHaveCount(0);
  await expectServerMain(page, { freqHz: 14_074_000, mode: 'USB', filterWidth: 2400 });
  await expectReadoutHz(readout, 14_074_000);
  await expect(modeKey('USB')).toHaveAttribute('aria-checked', 'true');
  await expect(width).toHaveAttribute('aria-valuenow', '2400');
  expect(await sentJourneyCommands(page)).toEqual([]);

  // Tune: type 7.100 MHz into the frequency entry dialog.
  await readout.click();
  await page.getByTestId('band-entry-input').fill('7.100');
  await expect(page.getByTestId('band-entry-set')).toBeEnabled();
  await page.getByTestId('band-entry-set').click();
  await expect.poll(() => sentParams(page, 'set_freq')).toEqual([{ freq: 7_100_000, receiver: 0 }]);
  await expectServerMain(page, { freqHz: 7_100_000, mode: 'USB' });
  await expectReadoutHz(readout, 7_100_000);
  await expect(page.getByTestId('frequency-entry-dialog-panel')).toHaveCount(0);

  // Mode: USB -> LSB.
  await modeKey('LSB').click();
  await expect.poll(() => sentParams(page, 'set_mode')).toEqual([{ mode: 'LSB', receiver: 0 }]);
  await expectServerMain(page, { freqHz: 7_100_000, mode: 'LSB' });
  await expect(modeKey('LSB')).toHaveAttribute('aria-checked', 'true');
  await expect(modeKey('USB')).toHaveAttribute('aria-checked', 'false');

  // Filter width: 2400 -> 1800 Hz.
  await width.fill('1800');
  await expect.poll(() => sentParams(page, 'set_filter_width')).toEqual([{ width: 1800, receiver: 0 }]);
  await expectServerMain(page, { freqHz: 7_100_000, mode: 'LSB', filterWidth: 1800 });
  await expect(width).toHaveAttribute('aria-valuenow', '1800');

  // Among set_freq, set_mode and set_filter_width the page sent exactly these three frames, in this order.
  expect((await sentJourneyCommands(page)).map(({ name, params }) => [name, params])).toEqual([
    ['set_freq', { freq: 7_100_000, receiver: 0 }],
    ['set_mode', { mode: 'LSB', receiver: 0 }],
    ['set_filter_width', { width: 1800, receiver: 0 }],
  ]);
});

// The second webServer entry in playwright.journey.config.ts, started with --read-only.
const READ_ONLY_URL = 'http://127.0.0.1:18767';

// Phone layout: its "Push to talk" button sends ptt_on although /api/v1/managed-transmit
// reports unavailable on this fake; the desktop layout's PTT button is disabled in that state.
test.describe('read-only server, phone layout', () => {
  test.use({ baseURL: READ_ONLY_URL, viewport: { width: 430, height: 932 } });

  test('operator journey: PTT is refused by the read-only gate', async ({ page, context }) => {
    await context.grantPermissions(['microphone'], { origin: READ_ONLY_URL });
    await installWebSocketInterceptor(page);
    await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });

    const ptt = page.getByRole('button', { name: 'Push to talk' });
    const reportedPtt = async () => ((await (await page.request.get('/api/v1/state')).json()) as { ptt?: unknown }).ptt;

    // Idle before the press: the control socket is open, the button reads PTT and the server
    // does not report PTT on.
    await expect.poll(() => openedSockets(page)).toContain('/api/v1/ws');
    await expect(ptt).toBeVisible();
    await expect(ptt).toHaveText('PTT');
    expect(await reportedPtt()).not.toBe(true);

    // Press and hold: the page starts the microphone, then sends one ptt_on.
    await ptt.hover();
    await page.mouse.down();
    await expect.poll(() => sentCommands(page, ['ptt_on'])).toHaveLength(1);
    const [pttOn] = await sentCommands(page, ['ptt_on']);
    expect(pttOn.params).toEqual({});

    // The server answers that command id with the read-only gate's refusal. On a writable
    // fake the same press is refused as radio_nak / "managed transmit authority unavailable".
    await expect
      .poll(async () => (await receivedRefusals(page)).filter((refusal) => refusal.id === pttOn.id))
      .toEqual([{ id: pttOn.id, error: 'command_failed', message: 'read-only mode: ptt_on rejected' }]);

    // After the refusal the button is idle and the server still does not report PTT on.
    await expect(ptt).toHaveText('PTT');
    await expect(ptt).not.toHaveClass(/ptt-fab-held|ptt-fab-latched/);
    expect(await reportedPtt()).not.toBe(true);

    await page.mouse.up();
  });
});

// The third fake is not a config webServer entry: the spec starts it (and its
// replacement) itself via server-process.ts, so it can SIGKILL the process
// group mid-session and restart on the same port.
const RESTART_PORT = 18768;
const RESTART_URL = `http://127.0.0.1:${RESTART_PORT}`;
const FREQ_A = 7_030_000;
const FREQ_B = 14_100_000;

test.describe('server killed and restarted on the same port', () => {
  test.use({ baseURL: RESTART_URL });

  const servers: JourneyServerHandle[] = [];
  test.afterAll(async () => {
    for (const server of servers) {
      await server.kill();
    }
  });

  test('operator journey: the UI shows the outage and then FRESH state', async ({ page }) => {
    test.setTimeout(90_000);
    await installWebSocketInterceptor(page);
    const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
    const controlSockets = async () =>
      (await openedSockets(page)).filter((socketPath) => socketPath === '/api/v1/ws');

    const first = await startJourneyServer(RESTART_PORT, FREQ_A);
    servers.push(first);
    try {
      await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });

      // Before the kill: the readout shows frequency A and the control link is connected.
      await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
      await expect(page.locator('.control-link-lost')).toHaveCount(0);
      expect(await controlSockets()).toHaveLength(1);
      await expectReadoutHz(readout, FREQ_A);

      // SIGKILL the whole process group; the control link is lost.
      await first.kill();

      // After SIGKILL: the UI shows the link as lost, both through the banner
      // and through the control indicator's title.
      await expect(page.locator('.control-link-lost')).toBeVisible();
      await expect(page.locator('.control-link-lost')).toHaveText('Control link lost');
      await expect(page.locator('[role="status"][title="Control WebSocket: disconnected"]')).toBeVisible();

      // Restart on the same port with frequency B, promptly: the client's
      // reconnect backoff starts at 1 s and doubles (ws-client.ts calcBackoff),
      // so the next attempt must land within seconds, not minutes.
      servers.push(await startJourneyServer(RESTART_PORT, FREQ_B));

      // The client reconnects on its own (a second control socket opened) and
      // the link shows connected again. 30 s is well under the 90 s test timeout.
      await expect.poll(controlSockets, { timeout: 30_000 }).toHaveLength(2);
      await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
      await expect(page.locator('.control-link-lost')).toHaveCount(0);

      // FRESH state: the readout and /api/v1/state report B.
      await expectReadoutHz(readout, FREQ_B);
      await expectServerMain(page, { freqHz: FREQ_B });
    } finally {
      await first.kill().catch(() => undefined);
    }
  });
});

// The fourth fake is also spec-spawned, but with --audio: the server keeps the
// profile's audio capability, so the page offers the rx-audio LIVE monitor
// choice. The DOM alone cannot prove the re-arm (data-live-link turns true at
// socket open, before audio_start is sent or accepted), so the pins also read
// the wire frames and the server's wsClients.audio count.
const AUDIO_PORT = 18769;
const AUDIO_URL = `http://127.0.0.1:${AUDIO_PORT}`;
const AUDIO_FREQ = 7_030_000;

test.describe('audio server killed and restarted on the same port', () => {
  test.use({ baseURL: AUDIO_URL });

  const servers: JourneyServerHandle[] = [];
  test.afterAll(async () => {
    for (const server of servers) {
      await server.kill();
    }
  });

  test('operator journey: RX audio is re-armed after the server restart', async ({ page }) => {
    test.setTimeout(120_000);
    await installWebSocketInterceptor(page);
    const monitor = page.locator('[data-testid="rx-audio-monitor"]');
    const liveChoice = page.locator('[data-testid="rx-audio-monitor-live"]');
    const audioSocketCount = async () =>
      (await openedSockets(page)).filter((socketPath) => socketPath === AUDIO_SOCKET_PATH).length;
    const rxStartCount = async () =>
      (await audioStarts(page)).filter((frame) => frame.direction === 'rx').length;
    const audioErrorFrames = async () =>
      (await audioFrames(page)).filter((frame) => frame.type === 'error');
    const reportedAudioClients = async () =>
      ((await (await page.request.get('/api/v1/state')).json()) as {
        wsClients?: { audio?: number };
      }).wsClients?.audio;

    const first = await startJourneyServer(AUDIO_PORT, AUDIO_FREQ, ['--audio']);
    servers.push(first);
    try {
      await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });

      // Before: monitor mode is local, the LIVE choice is offered with the
      // link down, no audio socket has opened and the server counts none.
      await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
      await expect(monitor).toHaveAttribute('data-monitor-mode', 'local');
      await expect(liveChoice).toBeVisible();
      await expect(liveChoice).toHaveAttribute('data-live-link', 'false');
      expect(await audioSocketCount()).toBe(0);
      await expect.poll(reportedAudioClients).toBe(0);

      // Click LIVE: mode live, one /api/v1/audio socket, one outgoing
      // audio_start rx frame on it, an audio_format frame back, one server
      // audio client.
      await liveChoice.click();
      await expect(monitor).toHaveAttribute('data-monitor-mode', 'live');
      await expect(liveChoice).toHaveAttribute('aria-checked', 'true');
      await expect(liveChoice).toHaveAttribute('data-live-link', 'true');
      await expect.poll(audioSocketCount).toBe(1);
      await expect.poll(rxStartCount).toBe(1);
      await expect
        .poll(async () => (await audioFrames(page)).filter((frame) => frame.type === 'audio_format').length)
        .toBeGreaterThanOrEqual(1);
      await expect.poll(reportedAudioClients).toBe(1);

      // SIGKILL the whole process group; the control link is lost. The
      // rx-audio panel's capabilities are cleared on control loss, so its DOM
      // is not asserted during the outage.
      await first.kill();
      await expect(page.locator('.control-link-lost')).toBeVisible();
      await expect(page.locator('.control-link-lost')).toHaveText('Control link lost');
      await expect(page.locator('[role="status"][title="Control WebSocket: disconnected"]')).toBeVisible();

      // Restart with --audio on the same port: a second audio socket opens on
      // its own and carries a second audio_start rx frame (the client's
      // control reconnect restores capabilities and the still-held rx-audio
      // lease re-arms startRx). Socket and frame counts are "at least two"
      // because a reconnect attempt racing the outage teardown can open an
      // extra socket. The NEW process reports exactly one audio client, the
      // DOM is back to live with the link up, and no error frame arrived on
      // any audio socket.
      servers.push(await startJourneyServer(AUDIO_PORT, AUDIO_FREQ, ['--audio']));
      await expect.poll(audioSocketCount, { timeout: 30_000 }).toBeGreaterThanOrEqual(2);
      await expect.poll(rxStartCount, { timeout: 30_000 }).toBeGreaterThanOrEqual(2);
      await expect.poll(reportedAudioClients, { timeout: 30_000 }).toBe(1);
      await expect(monitor).toHaveAttribute('data-monitor-mode', 'live');
      await expect(liveChoice).toHaveAttribute('data-live-link', 'true');
      expect(await audioErrorFrames()).toEqual([]);
    } finally {
      await first.kill().catch(() => undefined);
    }
  });
});

interface PanoramaWitness {
  requests: ScopeSubscription[];
  grants: ScopeGranted[];
  acknowledgments: string[];
  active: boolean;
  pinned: boolean;
  baselineSignal: number;
  pixelLosses: number;
  refusals: string[];
  changingMain: number;
  advancingSpectrum: number;
  main: number;
  sub: number;
  audioWire: number;
  pcmBuffers: number;
  pcm: { at: number; state: string; rate: number; scheduledAt: number; frames: number }[];
  finiteSamples: number;
  nonzeroSamples: number;
  invalidSamples: number;
  subChecks: number;
  losses: number;
  latestMain: number;
  lastReceiver: number;
  frames: { at: number; source: ScopeFrame['source']; sourceId: string; receiver: number; sequence: number;
    pixels: number; start: number; end: number; coverageStart: number; coverageEnd: number }[];
  samples: { at: number; main: number; mounted: boolean; width: number; height: number; signal: number }[];
}

async function installPanoramaWitness(page: Page): Promise<void> {
  const observe = (parseScopeFrame: typeof parseCurrentScopeFrame, parseGrant: typeof parseScopeGranted) => {
    // Exercise the production PCM fallback when optional WebCodecs is absent.
    Object.defineProperty(window, 'AudioDecoder', { configurable: true, value: undefined });
    const win = window as Window & { __PANORAMA__?: PanoramaWitness };
    const w: PanoramaWitness = {
      requests: [], grants: [],
      acknowledgments: [], active: false, pinned: false, baselineSignal: 0, pixelLosses: 0, main: 0, sub: 0, audioWire: 0, pcmBuffers: 0,
      refusals: [], changingMain: 0, advancingSpectrum: 0,
      finiteSamples: 0, nonzeroSamples: 0, invalidSamples: 0, subChecks: 0, losses: 0,
      latestMain: -1, lastReceiver: -1, frames: [], samples: [], pcm: [],
    };
    win.__PANORAMA__ = w;
    let panel: HTMLElement | null = null;
    let canvas: HTMLCanvasElement | null = null;
    let width = 0;
    let height = 0;
    let mainPixels = '';
    let renderedSignal: number | null = null;
    const selector = '.spectrum-panel[data-waterfall]:not(.audio-fft)';
    // The production canvas is transparent. Exclude its ten/five grid lines,
    // bottom labels, edges, and central tuning/passband overlays. The remaining
    // opaque pixels can only come from the actual trace/fill, never DOM geometry.
    const signalPixels = (target: HTMLCanvasElement | null): number => {
      if (!target?.isConnected || !target.width || !target.height) return 0;
      const context = target.getContext('2d');
      if (!context) return 0;
      const { width: cw, height: ch } = target;
      const rgba = context.getImageData(0, 0, cw, ch).data;
      const scale = cw / Math.max(1, target.getBoundingClientRect().width);
      let count = 0;
      for (let y = Math.ceil(ch * 0.1); y < ch - 20 * scale; y += 2) {
        if (Math.abs(y - Math.round(y / (ch / 5)) * (ch / 5)) < 3 * scale) continue;
        for (let x = Math.ceil(cw * 0.05); x < cw * 0.95; x += 2) {
          if (x > cw * 0.4 && x < cw * 0.6) continue;
          if (Math.abs(x - Math.round(x / (cw / 10)) * (cw / 10)) < 3 * scale) continue;
          if (rgba[(y * cw + x) * 4 + 3] > 32) count += 1;
        }
      }
      return count;
    };
    const nativeStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args: Parameters<typeof nativeStart>) {
      nativeStart.apply(this, args);
      // Observe the decoded buffer handed to the actual AudioContext unchanged.
      const buffer = this.buffer;
      if (!buffer || buffer.length < 200) return;
      w.pcmBuffers += 1;
      if (w.pcm.length >= 120) w.pcm.shift();
      w.pcm.push({ at: performance.now(), state: this.context.state,
        rate: buffer.sampleRate, scheduledAt: args[0] ?? 0, frames: buffer.length });
      for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
        for (const sample of buffer.getChannelData(channel)) {
          if (!Number.isFinite(sample)) w.invalidSamples += 1;
          else {
            w.finiteSamples += 1;
            if (sample !== 0) w.nonzeroSamples += 1;
          }
        }
      }
    };
    const NativeSocket = window.WebSocket;
    class ObservedSocket extends NativeSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
        const path = new URL(String(url), location.href).pathname;
        const send = this.send.bind(this);
        this.send = data => {
          send(data);
          if (path !== '/api/v1/scope' || typeof data !== 'string') return;
          const request = JSON.parse(data) as ScopeSubscription & { type: string };
          if (request.type === 'subscribe' && request.sourceId === 'radio' && request.receiver === 0) {
            w.requests.push(request);
            // Demand alien SUB frames on this connection without replacing MAIN.
            const spanHz = 'spanHz' in request.view ? request.view.spanHz : request.view.endHz - request.view.startHz;
            send(JSON.stringify({ ...request, receiver: 1, view: { follow: 'vfo', spanHz } }));
          }
        };
        this.addEventListener('message', (event: MessageEvent) => {
          if (typeof event.data === 'string') {
            try {
              const message = JSON.parse(event.data);
              if (message.type === 'response' && message.ok === true) w.acknowledgments.push(String(message.id));
              const grant = parseGrant(message);
              if (grant?.sourceId === 'radio') w.grants.push(grant);
              if (path === '/api/v1/scope' && message.type === 'error') w.refusals.push(message.message);
            } catch { /* Non-JSON control traffic is irrelevant to this witness. */ }
            return;
          }
          if (!(event.data instanceof ArrayBuffer)) return;
          const bytes = new Uint8Array(event.data);
          if (path === '/api/v1/audio' && bytes.length > 8 && bytes[0] === 0x10 && bytes[1] === 2) {
            w.audioWire += 1;
          }
          if (path !== '/api/v1/scope') return;
          const parsed = parseScopeFrame(event.data, { source: 'radio', sourceId: 'radio', epoch: 0 });
          if (!parsed.ok || parsed.frame.source !== 'radio' || parsed.frame.outOfRange || !parsed.frame.pixels) return;
          const { receiver, width: pixels, viewStartHz: start, viewEndHz: end, seq: sequence } = parsed.frame;
          if (receiver !== 0 && receiver !== 1) return;
          if (receiver === 0 && w.refusals.length > 0) {
            const nextPixels = Array.from(parsed.frame.pixels).join(',');
            if (mainPixels && nextPixels !== mainPixels) w.changingMain += 1;
            mainPixels = nextPixels;
          }
          if (receiver === 0) { w.main += 1; w.latestMain = sequence; }
          else w.sub += 1;
          w.lastReceiver = receiver;
          if (w.frames.length >= 240) w.frames.shift();
          w.frames.push({ at: performance.now(), source: parsed.frame.source, sourceId: parsed.frame.sourceId,
            receiver, sequence, pixels, start, end,
            coverageStart: parsed.frame.coverageStartHz, coverageEnd: parsed.frame.coverageEndHz });
          if (!w.active) return;
          const mainSequence = w.latestMain;
          requestAnimationFrame(() => requestAnimationFrame(() => {
            const current = document.querySelector<HTMLElement>(selector);
            const currentCanvas = current?.querySelector<HTMLCanvasElement>('.spectrum-area canvas') ?? null;
            const rect = currentCanvas?.getBoundingClientRect();
            if (receiver === 0 && w.pinned && w.refusals.length > 0 && currentCanvas === canvas) {
              const signal = signalPixels(currentCanvas);
              if (renderedSignal !== null && signal !== renderedSignal) w.advancingSpectrum += 1;
              renderedSignal = signal;
            }
            if (receiver === 0 && w.lastReceiver === 0 && current && currentCanvas && rect && rect.width > 0 && rect.height > 0 && !w.pinned) {
              const signal = signalPixels(currentCanvas);
              if (signal < 20) return;
              w.baselineSignal = signal;
              panel = current;
              canvas = currentCanvas;
              width = rect.width;
              height = rect.height;
              w.pinned = true;
            }
            // Sample after rendering, and only before the next MAIN can conceal a loss.
            if (receiver !== 1 || !w.pinned || w.latestMain !== mainSequence || w.lastReceiver !== 1) return;
            const mounted = current === panel && currentCanvas === canvas && !!canvas?.isConnected
              && !!rect && Math.abs(rect.width - width) < 1 && Math.abs(rect.height - height) < 1
              && !!current && getComputedStyle(current).visibility !== 'hidden' && getComputedStyle(current).display !== 'none'
              && !current.querySelector('.scope-disconnected-overlay')
              && !!current.querySelector('.spectrum-container .span-indicators')
              && (current.querySelector('.waterfall-area canvas')?.getBoundingClientRect().height ?? 0) > 0;
            const signal = signalPixels(currentCanvas);
            w.subChecks += 1;
            if (signal < w.baselineSignal * 0.5) w.pixelLosses += 1;
            if (!mounted) w.losses += 1;
            if (w.samples.length < 120) w.samples.push({
              at: performance.now(), main: mainSequence, mounted, signal,
              width: rect?.width ?? 0, height: rect?.height ?? 0,
            });
          }));
        });
      }
    }
    window.WebSocket = ObservedSocket;
  };
  await page.addInitScript(`(${observe.toString()})(${parseCurrentScopeFrame.toString()}, ${parseScopeGranted.toString()});`);
}

async function panoramaWitness(page: Page): Promise<PanoramaWitness> {
  return page.evaluate(() => (window as Window & { __PANORAMA__?: PanoramaWitness }).__PANORAMA__!);
}

test('MOR-4273 panorama witness accepts producer frames and requests both receivers', async ({ page }) => {
  const { buffer, expected } = loadScopeWireGolden('partial_coverage.bin');
  const golden = Buffer.from(buffer);
  const wrongVersion = Buffer.from(golden);
  wrongVersion[0] = 1;
  const iq = Buffer.from(loadScopeWireGolden('iq_fft.bin').buffer);
  const outOfRange = Buffer.from(loadScopeWireGolden('out_of_range.bin').buffer);
  const sub = Buffer.from(scopeWire({ sourceId: 'radio:sub', receiver: 1 }));
  const requests: ScopeSubscription[] = [];
  await page.routeWebSocket('ws://scope-witness.invalid/api/v1/scope', socket => {
    socket.onMessage(message => {
      const request = JSON.parse(String(message)) as ScopeSubscription & { type: string };
      if (request.type === 'subscribe') {
        requests.push(request);
        if (request.receiver === 0) {
          socket.send(wrongVersion); socket.send(iq); socket.send(outOfRange); socket.send(golden);
        } else if (request.receiver === 1) socket.send(sub);
      }
    });
  });
  await installPanoramaWitness(page);
  await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });
  const request: ScopeSubscription = {
    sourceId: 'radio', receiver: 0, view: { follow: 'vfo', spanHz: 200_000 }, width: 689, maxFps: 20,
  };
  await page.evaluate(request => {
    const socket = new WebSocket('ws://scope-witness.invalid/api/v1/scope');
    socket.binaryType = 'arraybuffer';
    socket.addEventListener('open', () => socket.send(JSON.stringify({ type: 'subscribe', ...request })));
  }, request);
  await expect.poll(async () => (await panoramaWitness(page)).frames).toEqual([
    expect.objectContaining({ source: expected.source, sourceId: expected.source_id,
      receiver: expected.receiver, sequence: expected.seq, pixels: expected.width,
      start: expected.view_start_hz, end: expected.view_end_hz,
      coverageStart: expected.coverage_start_hz, coverageEnd: expected.coverage_end_hz }),
    expect.objectContaining({ source: 'radio', sourceId: 'radio:sub', receiver: 1, pixels: 4 }),
  ]);
  await expect.poll(() => requests.map(request => request.receiver)).toEqual([0, 1]);
  expect((await panoramaWitness(page)).main).toBe(1);
  expect((await panoramaWitness(page)).sub).toBe(1);
});

const panoramaTest = test.extend({
  launchOptions: {
    ...(process.env.RIGPLANE_JOURNEY_CHROMIUM
      ? { executablePath: process.env.RIGPLANE_JOURNEY_CHROMIUM }
      : {}),
    args: ['--mute-audio'],
  },
});

panoramaTest.describe('simulated mixed receiver panorama with real PCM', () => {
  const url = 'http://127.0.0.1:18770';
  panoramaTest.use({
    baseURL: url,
  });

  panoramaTest('MOR-3513 mixed scope preserves MAIN while PCM and controls advance', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const server = await startJourneyServer(18770, 14_286_000, ['--panorama-audio']);
    try {
      await installWebSocketInterceptor(page);
      await installPanoramaWitness(page);
      await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });
      await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
      await expect.poll(async () => (await page.request.get('/api/v1/state')).json()).toMatchObject({ active: 'MAIN' });
      await page.getByTestId('rx-audio-monitor-live').click();
      await expect.poll(async () => (await panoramaWitness(page)).main).toBeGreaterThan(3);
      await expect.poll(async () => (await panoramaWitness(page)).sub).toBeGreaterThan(3);
      await expect.poll(async () => (await panoramaWitness(page)).pcmBuffers).toBeGreaterThan(5);
      await expect.poll(async () => (await panoramaWitness(page)).audioWire).toBeGreaterThan(5);
      await page.evaluate(() => { (window as Window & { __PANORAMA__?: PanoramaWitness }).__PANORAMA__!.active = true; });
      const before = await panoramaWitness(page);
      expect(before.pcm.at(-1)?.state).toBe('running');
      await expectServerMain(page, { freqHz: 14_286_000 });
      expect(before.nonzeroSamples).toBeGreaterThan(0);
      expect(before.invalidSamples).toBe(0);
      const modeKey = page.getByTestId('standard-mode-LSB').getByRole('radio');
      await expect.poll(async () => (await panoramaWitness(page)).pinned).toBe(true);
      const requestsBefore = (await panoramaWitness(page)).requests.length;
      const refusalsBefore = (await panoramaWitness(page)).refusals.length;
      await modeKey.click();
      await expect.poll(async () => (await panoramaWitness(page)).requests.slice(requestsBefore)
        .some(request => 'startHz' in request.view)).toBe(true);
      await expect.poll(async () => (await panoramaWitness(page)).refusals.length).toBeGreaterThan(refusalsBefore);
      await expect.poll(() => sentParams(page, 'set_mode')).toContainEqual({ mode: 'LSB', receiver: 0 });
      await expectServerMain(page, { mode: 'LSB' });
      await expect(modeKey).toHaveAttribute('aria-checked', 'true');
      const command = (await sentCommands(page, ['set_mode'])).at(-1)!;
      await expect.poll(async () => (await panoramaWitness(page)).acknowledgments).toContain(command.id);
      // Audio/control health is independent of the visual regression target.
      await expect.poll(async () => (await panoramaWitness(page)).main - before.main).toBeGreaterThan(3);
      await expect.poll(async () => (await panoramaWitness(page)).sub - before.sub).toBeGreaterThan(15);
      await expect.poll(async () => (await panoramaWitness(page)).pcmBuffers - before.pcmBuffers).toBeGreaterThan(10);
      const after = await panoramaWitness(page);
      // These preconditions must pass even on the deliberately unchanged RED controller.
      expect(after.main - before.main).toBeGreaterThan(3);
      expect(after.sub - before.sub).toBeGreaterThan(15);
      expect(after.audioWire - before.audioWire).toBeGreaterThan(10);
      expect(after.pcmBuffers - before.pcmBuffers).toBeGreaterThan(10);
      expect(after.finiteSamples - before.finiteSamples).toBeGreaterThan(1000);
      expect(after.nonzeroSamples - before.nonzeroSamples).toBeGreaterThan(1000);
      expect(after.invalidSamples).toBe(0);
      const intervalPcm = after.pcm.filter((sample) => sample.at > (before.pcm.at(-1)?.at ?? 0));
      expect(intervalPcm.length).toBeGreaterThan(10);
      expect(intervalPcm.every((sample) => sample.state === 'running' && sample.rate === 48_000)).toBe(true);
      expect(after.requests).toContainEqual(expect.objectContaining({ receiver: 0, view: { follow: 'vfo', spanHz: 100_000 } }));
      const mainGrant = after.grants.filter(grant => grant.receiver === 0).at(-1)!;
      expect(mainGrant.view).toEqual({ startHz: 14_236_000, endHz: 14_336_000 });
      expect(after.frames.filter((frame) => frame.receiver === 0)
        .every((frame) => frame.start === mainGrant.view.startHz && frame.end === mainGrant.view.endHz)).toBe(true);
      // Persist independent checks before a failing visual target can stop the test.
      const healthPath = testInfo.outputPath('joint-health.json');
      await writeFile(healthPath, JSON.stringify({ before, after, command }, null, 2));
      await testInfo.attach('joint-health.json', { path: healthPath, contentType: 'application/json' });
      const beforePath = testInfo.outputPath('panorama-before.png');
      await page.screenshot({ path: beforePath });
      await testInfo.attach('panorama-before.png', { path: beforePath, contentType: 'image/png' });

      await expect.poll(async () => (await panoramaWitness(page)).baselineSignal,
        { message: 'Actual MAIN signal must render while mixed scope, PCM and controls are healthy' }).toBeGreaterThanOrEqual(20);
      await expect.poll(async () => (await panoramaWitness(page)).subChecks).toBeGreaterThanOrEqual(20);
      await expect.poll(async () => (await panoramaWitness(page)).advancingSpectrum,
        { message: 'Mounted MAIN spectrum must advance after refusal across SUB-only intervals' }).toBeGreaterThan(2);
      const rendered = await panoramaWitness(page);
      expect(rendered.changingMain, 'MAIN wire pixels must change after negotiation refusal').toBeGreaterThan(2);
      expect.soft(rendered.advancingSpectrum, 'Mounted MAIN spectrum must advance after refusal across SUB-only intervals').toBeGreaterThan(2);
      await expect.soft(page.locator('[data-testid="toolbar-scope-status"]')).toContainText('Scope view refused');
      await expect(page.locator('.spectrum-panel').getByRole('status').filter({ hasText: 'Scope view refused' })).toBeVisible();
      expect(rendered.pixelLosses, 'Actual MAIN signal pixels cleared during a SUB-only interval').toBe(0);
      expect(rendered.losses, 'MAIN canvas disappeared or changed geometry after valid SUB before next MAIN').toBe(0);
    } finally {
      try {
        if (!page.isClosed()) {
          const afterPath = testInfo.outputPath('panorama-after.png');
          const timelinePath = testInfo.outputPath('panorama-pcm-timeline.json');
          await page.screenshot({ path: afterPath });
          await writeFile(timelinePath, JSON.stringify(await panoramaWitness(page), null, 2) ?? '{}');
          await testInfo.attach('panorama-after.png', { path: afterPath, contentType: 'image/png' });
          await testInfo.attach('panorama-pcm-timeline.json', { path: timelinePath, contentType: 'application/json' });
        }
      } finally {
        await server.kill();
      }
    }
  });
});
