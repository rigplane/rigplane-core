import { expect, test, type Locator, type Page } from '@playwright/test';

/**
 * Operator journey against the real WebServer with the in-memory IC-7610
 * SerialMockRadio (tests/e2e/journey_web_server.py): page loads and the
 * control link is connected, then tune, change mode, narrow the filter.
 * Tune, mode and filter width are each read back three ways: the command frame
 * the page sent over the WebSocket, the server's /api/v1/state, and the DOM.
 */

interface WsCommandRecord {
  name: string;
  params: Record<string, unknown>;
}

const JOURNEY_COMMANDS = ['set_freq', 'set_mode', 'set_filter_width'];

// Adapted from installWebSocketInterceptor in v2-ui-interactive.impl.ts (not
// imported: importing that file registers its tests); keeps only name and params.
async function installWebSocketInterceptor(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const win = window as Window & { __WS_COMMANDS__?: WsCommandRecord[] };
    win.__WS_COMMANDS__ = [];
    const OriginalWebSocket = window.WebSocket;

    class InterceptedWebSocket extends OriginalWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
        const originalSend = this.send.bind(this);
        this.send = (data: string | ArrayBufferLike | Blob | ArrayBufferView) => {
          if (typeof data === 'string') {
            try {
              const parsed = JSON.parse(data) as Record<string, unknown>;
              if (parsed.type === 'cmd' || parsed.type === 'command') {
                win.__WS_COMMANDS__?.push({
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

/** Journey command frames the page has sent so far, in send order. */
async function sentJourneyCommands(page: Page): Promise<WsCommandRecord[]> {
  return page.evaluate((names) => {
    const win = window as Window & { __WS_COMMANDS__?: WsCommandRecord[] };
    return (win.__WS_COMMANDS__ ?? []).filter((command) => names.includes(command.name));
  }, JOURNEY_COMMANDS);
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
