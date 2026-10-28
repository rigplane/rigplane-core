import { expect, test, type Locator, type Page } from '@playwright/test';
import { startJourneyServer, type JourneyServerHandle } from './server-process';

/**
 * MOR-3644: the panel memory Store/Clear path end-to-end through the real
 * WebServer into a simulator radio — browser UI click -> makeMemoryHandlers()
 * (frontend/src/lib/runtime/commands/panel-commands.ts) -> WebSocket
 * (set_memory_mode / memory_write / memory_clear) -> server handler
 * (src/rigplane/web/handlers/control.py _enqueue_rc_memory) -> command queue
 * drain -> the fake's MemoryCapable surface. Every radio here is simulated
 * (tests/e2e/memory_journey_web_server.py, MemoryJourneyRadio); nothing in
 * this spec can touch hardware. The fake stands in for the real Icom
 * backend's memory surface (src/rigplane/runtime/radio.py IcomRadio:
 * set_memory_mode / memory_write / memory_to_vfo / memory_clear /
 * read_memory_entries — CI-V 0x08 select, 0x09 write-VFO-to-memory, 0x0A
 * memory-to-VFO, the profile-declared clear, the 1A 00 read).
 *
 * Behaviour pins this spec owns (all simulated):
 *
 * - The UI Store path dispatches set_memory_mode{channel} and then
 *   memory_write{} — the radio-side CI-V 0x09 "write the current VFO into
 *   the selected memory channel", NOT set_memory_contents. memory_write is
 *   deliberately NOT layout-gated: no host-built channel record crosses the
 *   wire (the radio encodes its own record from its current VFO), so the
 *   [memory].write_layout admission gate (admit_memory_write) does not apply
 *   to it. The FTX-1 scenario pins that: a profile with no write layout
 *   still Stores, while Clear — which DOES send a host-encoded clear frame
 *   whose form the layout declares — is refused memory_write_unverified.
 *
 * - The panel never sends a memory group. On a channel-addressed radio
 *   (IC-7610) the server defaults the omitted group to 0 and the clear
 *   proceeds; on a group-addressed radio (IC-705) the same
 *   memory_clear{channel} frame is refused with "a group/band is required
 *   to address a memory channel on this radio" — the IC-705 scenario pins
 *   both the refusal and the grouped form (memory_clear{channel, group:0})
 *   that does clear.
 *
 * - The panel refuses an out-of-range channel (its own 1-99 catalogue
 *   bound) before anything reaches the WebSocket, and the server holds the
 *   profile bound (IC-7610: 1-101) one seam deeper, before enqueue.
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

// Adapted from operator-journey.spec.ts (same file-local pattern: importing
// that spec would register its tests). Keeps the id, name and params of each
// outgoing command frame and the id, error and message of each incoming
// response frame with ok: false.
async function installWebSocketInterceptor(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const win = window as Window & {
      __WS_COMMANDS__?: WsCommandRecord[];
      __WS_REFUSALS__?: WsRefusalRecord[];
    };
    win.__WS_COMMANDS__ = [];
    win.__WS_REFUSALS__ = [];
    const OriginalWebSocket = window.WebSocket;

    class InterceptedWebSocket extends OriginalWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
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

const MEMORY_COMMANDS = ['set_memory_mode', 'memory_write', 'memory_to_vfo', 'memory_clear'];

/** The memory command frames the page has sent so far as {name, params}, in send order. */
async function sentMemoryCommands(page: Page): Promise<Array<{ name: string; params: Record<string, unknown> }>> {
  return (await sentCommands(page, MEMORY_COMMANDS)).map(({ name, params }) => ({ name, params }));
}

/** Error responses the server has sent to the page so far, in arrival order. */
async function receivedRefusals(page: Page): Promise<WsRefusalRecord[]> {
  return page.evaluate(() => {
    const win = window as Window & { __WS_REFUSALS__?: WsRefusalRecord[] };
    return win.__WS_REFUSALS__ ?? [];
  });
}

interface RawCommandReply {
  ok: boolean;
  error?: string;
  message?: string;
  result?: Record<string, unknown>;
}

/**
 * One command on a raw control socket of its own, answered by the real
 * server — the WS-seam half of the assertions the panel cannot express
 * (a grouped clear, an out-of-profile channel, a recall of a cleared
 * channel). The page's own interceptor also sees these frames.
 */
async function sendRawCommand(
  page: Page,
  name: string,
  params: Record<string, unknown>,
): Promise<RawCommandReply> {
  return page.evaluate(
    async ({ name: commandName, params: commandParams }) => {
      const url = new URL('/api/v1/ws', window.location.href);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(url);
      try {
        return await new Promise<RawCommandReply>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('no response within 10 s')), 10_000);
          ws.addEventListener('open', () => {
            ws.send(JSON.stringify({ type: 'cmd', id: 'e2e-raw-memory', name: commandName, params: commandParams }));
          });
          ws.addEventListener('message', (event: MessageEvent) => {
            if (typeof event.data !== 'string') return;
            const parsed = JSON.parse(event.data) as Record<string, unknown>;
            if (parsed.type === 'response' && parsed.id === 'e2e-raw-memory') {
              clearTimeout(timer);
              resolve(parsed as unknown as RawCommandReply);
            }
          });
          ws.addEventListener('error', () => {
            clearTimeout(timer);
            reject(new Error('raw control socket errored'));
          });
        });
      } finally {
        ws.close();
      }
    },
    { name, params },
  );
}

async function expectServerMain(
  page: Page,
  expected: { freqHz?: number; mode?: string },
): Promise<void> {
  await expect
    .poll(async () => (await page.request.get('/api/v1/state')).json())
    .toMatchObject({ main: expected });
}

/** One stored channel as the listing API reads it back from the simulator. */
async function readMemoryEntry(page: Page, channel: number): Promise<Record<string, unknown> | null> {
  const response = await page.request.get(`/api/v1/memories?group=0&start=${channel}&count=1`);
  if (!response.ok()) return null;
  const body = (await response.json()) as { entries?: Array<Record<string, unknown>> };
  return body.entries?.[0] ?? null;
}

/** The readout renders MHz.kHz.Hz digit groups; compare the digits only. */
async function expectReadoutHz(readout: Locator, hz: number): Promise<void> {
  await expect
    .poll(async () => Number(((await readout.textContent()) ?? '').replace(/\D/g, '')))
    .toBe(hz);
}

/** Tune the active VFO through the frequency entry dialog (journey pattern). */
async function tuneViaEntry(page: Page, mhz: string): Promise<void> {
  await page.locator('.receiver-instrument [data-vfo-freq]').first().click();
  await page.getByTestId('band-entry-input').fill(mhz);
  await expect(page.getByTestId('band-entry-set')).toBeEnabled();
  await page.getByTestId('band-entry-set').click();
}

async function openMemorySurface(page: Page) {
  await installWebSocketInterceptor(page);
  await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
  const surface = page.getByTestId('memory-surface');
  await expect(surface).toBeVisible();
  return surface;
}

const MEMORY_SCRIPT = 'tests/e2e/memory_journey_web_server.py';

function startMemoryServer(port: number, model: string): Promise<JourneyServerHandle> {
  return startJourneyServer(port, 14_074_000, ['--model', model], MEMORY_SCRIPT);
}

const IC7610_PORT = 18771;

test.describe('memory seam, IC-7610 simulator (channel-addressed, verified write layout)', () => {
  test.use({ baseURL: `http://127.0.0.1:${IC7610_PORT}` });

  let server: JourneyServerHandle | undefined;
  test.beforeAll(async () => {
    server = await startMemoryServer(IC7610_PORT, 'IC-7610');
  });
  test.afterAll(async () => {
    await server?.kill();
  });

  test('store writes the VFO to the channel, recall reads it back, clear empties it', async ({ page }) => {
    const surface = await openMemorySurface(page);
    const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
    await expectServerMain(page, { freqHz: 14_074_000, mode: 'USB' });
    // The IC-7610 profile declares no memory_read capability: the listing
    // API is off for this radio, so read-back below goes through a recall.
    const listing = await page.request.get('/api/v1/memories?group=0&start=1&count=1');
    expect(listing.status()).toBe(404);

    // Store the active VFO (14.074.000 USB) into empty channel 1: the panel
    // dispatches set_memory_mode{channel:1} then memory_write{} — the
    // radio-side write-VFO-to-memory, not a host-built record.
    await surface.getByTestId('memory-store-toggle').click();
    await surface.locator('.store-input').fill('1');
    await surface.getByTestId('memory-store-confirm').click();
    await expect.poll(() => sentMemoryCommands(page)).toEqual([
      { name: 'set_memory_mode', params: { channel: 1 } },
      { name: 'memory_write', params: {} },
    ]);
    // Visible result: the local catalogue row shows the stored channel.
    await expect(surface.getByTestId('memory-channel-1-freq')).toHaveText('14.074.000');
    await expect(surface.getByTestId('memory-count')).toHaveText('1/99');

    // Tune away, then recall channel 1 through the panel: the simulator's
    // stored contents come back through the real seam (set_freq/set_mode
    // inside the fake) into /api/v1/state and the readout.
    await tuneViaEntry(page, '7.100');
    await expectServerMain(page, { freqHz: 7_100_000 });
    await surface.getByTestId('memory-channel-1-recall').click();
    await expect.poll(() => sentMemoryCommands(page)).toEqual([
      { name: 'set_memory_mode', params: { channel: 1 } },
      { name: 'memory_write', params: {} },
      { name: 'set_memory_mode', params: { channel: 1 } },
      { name: 'memory_to_vfo', params: { channel: 1 } },
    ]);
    await expectServerMain(page, { freqHz: 14_074_000, mode: 'USB' });
    await expectReadoutHz(readout, 14_074_000);

    // Clear channel 1 through the panel's two-step CLR. The panel sends no
    // group; on this channel-addressed radio the server defaults it to 0.
    await tuneViaEntry(page, '7.100');
    await expectServerMain(page, { freqHz: 7_100_000 });
    await surface.getByTestId('memory-channel-1-clear').click();
    await surface.locator('[data-channel="1"]').getByRole('button', { name: 'Yes' }).click();
    await expect.poll(async () => (await sentCommands(page, ['memory_clear'])).map(({ params }) => params))
      .toEqual([{ channel: 1 }]);
    // The verified write layout admitted the clear: no refusal for its id.
    const [clearCommand] = await sentCommands(page, ['memory_clear']);
    expect((await receivedRefusals(page)).filter((refusal) => refusal.id === clearCommand.id)).toEqual([]);

    // Visible result: the row is gone from the local catalogue.
    await expect(surface.getByTestId('memory-channel-1')).toHaveCount(0);
    await expect(surface.getByTestId('memory-count')).toHaveText('0/99');

    // Read back empty: the cleared channel recalls as a no-op on the
    // simulator (the ok reply proves the command executed; the VFO stays
    // at 7.100 MHz instead of snapping back to the stored 14.074 MHz).
    const reply = await sendRawCommand(page, 'memory_to_vfo', { channel: 1 });
    expect(reply).toMatchObject({ ok: true, result: { channel: 1 } });
    await expectServerMain(page, { freqHz: 7_100_000 });
  });

  test('an out-of-range channel is refused before the wire', async ({ page }) => {
    const surface = await openMemorySurface(page);

    // The panel's own catalogue bound is 1-99 (MAX_MEMORY_CHANNELS): typing
    // 100 into the store bar makes onStore refuse the dispatch, so nothing
    // reaches the WebSocket and no refusal comes back.
    await surface.getByTestId('memory-store-toggle').click();
    await surface.locator('.store-input').fill('100');
    await surface.getByTestId('memory-store-confirm').click();
    // Absence assertion: give any (mis-)dispatch a moment to land, then pin.
    await page.waitForTimeout(500);
    expect(await sentCommands(page, MEMORY_COMMANDS)).toEqual([]);
    // The store bar stays open — the refused click did not complete a store.
    await expect(surface.getByTestId('memory-store-confirm')).toBeVisible();
    await expect(surface.getByTestId('memory-count')).toHaveText('0/99');

    // One seam deeper the server holds the profile bound: the IC-7610 admits
    // channels 1-101, so 102 is refused before anything is enqueued.
    const reply = await sendRawCommand(page, 'memory_clear', { channel: 102 });
    expect(reply).toMatchObject({
      ok: false,
      error: 'command_failed',
      message: 'channel must be 1-101, got 102',
    });
  });
});

const IC705_PORT = 18772;

test.describe('memory seam, IC-705 simulator (group-addressed, verified write layout)', () => {
  test.use({ baseURL: `http://127.0.0.1:${IC705_PORT}` });

  let server: JourneyServerHandle | undefined;
  test.beforeAll(async () => {
    server = await startMemoryServer(IC705_PORT, 'IC-705');
  });
  test.afterAll(async () => {
    await server?.kill();
  });

  test('store reads back through the listing API; the panel clear is refused for want of a group', async ({ page }) => {
    const surface = await openMemorySurface(page);
    await expectServerMain(page, { freqHz: 14_074_000, mode: 'USB' });

    // Store channel 5 through the panel. The handler sends
    // set_memory_mode{channel:5} — note the ABSENT group: the panel has no
    // group to send, and the channel-only select is admitted (1-101).
    await surface.getByTestId('memory-store-toggle').click();
    await surface.locator('.store-input').fill('5');
    await surface.getByTestId('memory-store-confirm').click();
    await expect.poll(() => sentMemoryCommands(page)).toEqual([
      { name: 'set_memory_mode', params: { channel: 5 } },
      { name: 'memory_write', params: {} },
    ]);
    await expect(surface.getByTestId('memory-channel-5-freq')).toHaveText('14.074.000');

    // The simulated radio-side write landed: this profile declares
    // memory_read, so the listing API reads the stored channel back.
    await expect.poll(() => readMemoryEntry(page, 5)).toMatchObject({
      group: 0,
      channel: 5,
      empty: false,
      frequencyHz: 14_074_000,
      mode: 'USB',
    });

    // Clear through the panel: the same group-less frame the Store path
    // sends is refused here — the IC-705's memory is group-addressed, and
    // the server never defaults a group on such a radio (a channel-only
    // clear would silently hit group 0).
    await surface.getByTestId('memory-channel-5-clear').click();
    await surface.locator('[data-channel="5"]').getByRole('button', { name: 'Yes' }).click();
    await expect.poll(async () => (await sentCommands(page, ['memory_clear'])).map(({ params }) => params))
      .toEqual([{ channel: 5 }]);
    const [clearCommand] = await sentCommands(page, ['memory_clear']);
    await expect
      .poll(async () => (await receivedRefusals(page)).filter((refusal) => refusal.id === clearCommand.id))
      .toEqual([{
        id: clearCommand.id,
        error: 'command_failed',
        message: 'a group/band is required to address a memory channel on this radio',
      }]);
    // The refusal is visible in the UI, not a silent failure.
    await expect(page.locator('.toast.error')).toContainText('group/band is required');
    // The local catalogue dropped the row on dispatch (it never waits for
    // the server), while the refusal kept the simulator's channel intact.
    await expect(surface.getByTestId('memory-channel-5')).toHaveCount(0);
    await expect.poll(() => readMemoryEntry(page, 5)).toMatchObject({ channel: 5, empty: false });

    // A client that does send the group clears the channel for real.
    const reply = await sendRawCommand(page, 'memory_clear', { channel: 5, group: 0 });
    expect(reply).toMatchObject({ ok: true, result: { channel: 5 } });
    await expect.poll(() => readMemoryEntry(page, 5)).toEqual({ group: 0, channel: 5, empty: true });
  });
});

const FTX1_PORT = 18773;

test.describe('memory seam, FTX-1 simulator (no verified write layout)', () => {
  test.use({ baseURL: `http://127.0.0.1:${FTX1_PORT}` });

  let server: JourneyServerHandle | undefined;
  test.beforeAll(async () => {
    server = await startMemoryServer(FTX1_PORT, 'FTX-1');
  });
  test.afterAll(async () => {
    await server?.kill();
  });

  test('store is admitted (memory_write is not layout-gated); clear is refused memory_write_unverified', async ({ page }) => {
    const surface = await openMemorySurface(page);
    await expectServerMain(page, { freqHz: 14_074_000, mode: 'USB' });

    // Store channel 3: admitted even though the FTX-1 profile declares no
    // [memory].write_layout — memory_write is the radio-side
    // write-VFO-to-memory (the radio encodes its own record; nothing
    // host-built crosses the wire), so the layout gate does not apply to it.
    await surface.getByTestId('memory-store-toggle').click();
    await surface.locator('.store-input').fill('3');
    await surface.getByTestId('memory-store-confirm').click();
    await expect.poll(() => sentMemoryCommands(page)).toEqual([
      { name: 'set_memory_mode', params: { channel: 3 } },
      { name: 'memory_write', params: {} },
    ]);
    await expect(surface.getByTestId('memory-channel-3-freq')).toHaveText('14.074.000');
    // Neither store frame was refused.
    const storeIds = new Set((await sentCommands(page, ['set_memory_mode', 'memory_write'])).map(({ id }) => id));
    expect((await receivedRefusals(page)).filter((refusal) => storeIds.has(refusal.id))).toEqual([]);
    await expect.poll(() => readMemoryEntry(page, 3)).toMatchObject({
      channel: 3,
      empty: false,
      frequencyHz: 14_074_000,
      mode: 'USB',
    });

    // Clear through the panel: the host-encoded clear frame's form comes
    // from the write layout this profile does not declare, so the server
    // refuses before anything is enqueued — with the stable reason code.
    await surface.getByTestId('memory-channel-3-clear').click();
    await surface.locator('[data-channel="3"]').getByRole('button', { name: 'Yes' }).click();
    await expect.poll(async () => (await sentCommands(page, ['memory_clear'])).map(({ params }) => params))
      .toEqual([{ channel: 3 }]);
    const [clearCommand] = await sentCommands(page, ['memory_clear']);
    await expect
      .poll(async () => (await receivedRefusals(page)).filter((refusal) => refusal.id === clearCommand.id))
      .toEqual([{
        id: clearCommand.id,
        error: 'memory_write_unverified',
        message: 'FTX-1 has no verified memory write layout: memory contents '
          + 'writes and clears are refused (declare [memory].write_layout in the profile)',
      }]);
    // The refusal is visible in the UI, not a silent failure.
    await expect(page.locator('.toast.error')).toContainText('no verified memory write layout');
    // And the simulated channel survived: the refusal never reached it.
    await expect.poll(() => readMemoryEntry(page, 3)).toMatchObject({ channel: 3, empty: false });
  });
});
