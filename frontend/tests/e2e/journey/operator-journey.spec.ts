import { expect, test, type Locator, type Page } from '@playwright/test';
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

const JOURNEY_COMMANDS = ['set_freq', 'set_mode', 'set_filter_width'];

// Adapted from installWebSocketInterceptor in v2-ui-interactive.impl.ts (not
// imported: importing that file registers its tests); keeps the id, name and
// params of each outgoing command frame, the id, error and message of each
// incoming response frame with ok: false, and the path of each socket that opened.
async function installWebSocketInterceptor(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const win = window as Window & {
      __WS_COMMANDS__?: WsCommandRecord[];
      __WS_REFUSALS__?: WsRefusalRecord[];
      __WS_OPENED__?: string[];
    };
    win.__WS_COMMANDS__ = [];
    win.__WS_REFUSALS__ = [];
    win.__WS_OPENED__ = [];
    const OriginalWebSocket = window.WebSocket;

    class InterceptedWebSocket extends OriginalWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
        this.addEventListener('open', () => {
          win.__WS_OPENED__?.push(new URL(String(url), window.location.href).pathname);
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
