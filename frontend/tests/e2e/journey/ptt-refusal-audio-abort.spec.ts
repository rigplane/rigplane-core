import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { startJourneyServer, type JourneyServerHandle } from './server-process';

/**
 * MOR-4651 / MOR-4469 product-entry-point regressions. The isolated tests
 * pin the units (browser-dependencies.isolated, websocket-transport.
 * isolated); the owner's test rule also demands the changed flows through
 * the production UI against the real local HTTP/WS handlers, with the
 * hardware faked the way the journey pack fakes it.
 *
 * Scenario one (MOR-4651): a live control session with degraded radio
 * health refuses a PTT press with the health gate's own warning and must
 * NOT claim the control link was lost on top of it. The journey fake's
 * --rig-drop-file seam drops the rig link the way a cable pull does while
 * the control WebSocket stays connected; sendCommand's health gate (MOR-
 * 1422) then refuses ptt_on before it is sent, and that refusal may not
 * raise the sticky controllerLost error that belongs to a session that
 * really is gone.
 *
 * Scenario two (MOR-4469): stopping the rx-audio LIVE monitor while its
 * WebSocket is still connecting must abort the connection attempt itself.
 * The establishment window is held deterministically by routing the audio
 * WebSocket through a no-op Playwright route whose handler stays pending
 * (Playwright force-opens an unconnected route once its handler returns),
 * so the page-side socket never opens until the test releases the hold.
 */

// Distinct from every other spec-spawned journey port (18768 restart,
// 18769 drop/audio): these run sequentially, but reuse is refused anyway.
const PTT_DROP_PORT = 18770;
const PTT_DROP_URL = `http://127.0.0.1:${PTT_DROP_PORT}`;
const AUDIO_HOLD_PORT = 18771;
const AUDIO_HOLD_URL = `http://127.0.0.1:${AUDIO_HOLD_PORT}`;

/** Records the name of every command frame the page sends (send order). */
async function installCommandWitness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const win = window as Window & { __WS_COMMANDS__?: { name: string }[] };
    win.__WS_COMMANDS__ = [];
    const OriginalWebSocket = window.WebSocket;
    class WitnessWebSocket extends OriginalWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
        const originalSend = this.send.bind(this);
        this.send = (data: string | ArrayBufferLike | Blob | ArrayBufferView) => {
          if (typeof data === 'string') {
            try {
              const parsed = JSON.parse(data) as Record<string, unknown>;
              if (parsed.type === 'cmd' || parsed.type === 'command') {
                win.__WS_COMMANDS__?.push({ name: String(parsed.name) });
              }
            } catch {
              // Ignore non-JSON frames.
            }
          }
          return originalSend(data);
        };
      }
    }
    Object.setPrototypeOf(WitnessWebSocket, OriginalWebSocket);
    Object.defineProperty(WitnessWebSocket, 'CONNECTING', { value: OriginalWebSocket.CONNECTING });
    Object.defineProperty(WitnessWebSocket, 'OPEN', { value: OriginalWebSocket.OPEN });
    Object.defineProperty(WitnessWebSocket, 'CLOSING', { value: OriginalWebSocket.CLOSING });
    Object.defineProperty(WitnessWebSocket, 'CLOSED', { value: OriginalWebSocket.CLOSED });
    window.WebSocket = WitnessWebSocket as typeof WebSocket;
  });
}

test('degraded radio health refuses PTT without claiming a lost control link', async ({ page }) => {
  test.setTimeout(90_000);
  const dir = await mkdtemp(path.join(tmpdir(), 'rig-drop-ptt-'));
  const dropFile = path.join(dir, 'drop');
  await installCommandWitness(page);
  // The phone skin: its PTT fab sends ptt_on where the desktop button would
  // stay disabled with managed transmit unavailable on this fake.
  await page.setViewportSize({ width: 430, height: 932 });

  const sentPttOn = () =>
    page.evaluate(() => {
      const win = window as Window & { __WS_COMMANDS__?: { name: string }[] };
      return (win.__WS_COMMANDS__ ?? []).filter((command) => command.name === 'ptt_on').length;
    });
  const rigConnected = async () =>
    ((await (await page.request.get(`${PTT_DROP_URL}/api/v1/state`)).json()) as {
      connection?: { rigConnected?: boolean };
    }).connection?.rigConnected;

  let server: JourneyServerHandle | undefined;
  try {
    server = await startJourneyServer(PTT_DROP_PORT, 14_074_000, ['--rig-drop-file', dropFile]);
    await page.goto(`${PTT_DROP_URL}/?ui=v2`, { waitUntil: 'domcontentloaded' });

    const ptt = page.getByRole('button', { name: 'Push to talk' });
    await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
    await expect(ptt).toBeVisible();

    // Drop the rig link the way a cable pull does. The CONTROL session
    // stays connected -- that is what makes the PTT refusal below a
    // degraded-health refusal, not a lost control link.
    await writeFile(dropFile, 'drop');
    await expect.poll(rigConnected).toBe(false);
    await page.waitForTimeout(1_000); // let the rigConnected=false update reach the page
    await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();

    // Press and hold: the command gate refuses ptt_on before it is sent.
    await ptt.hover();
    await page.mouse.down();
    await page.waitForTimeout(500);
    await page.mouse.up();

    // No ptt_on ever left the page ...
    expect(await sentPttOn()).toBe(0);
    // ... the gate's own warning is what the operator sees (auto-dismissed
    // after 5 s, so assert promptly after the press) ...
    await expect(
      page.locator('.toast-container .toast.warning').filter({ hasText: 'Command not sent' }),
    ).toBeVisible();
    // ... and the sticky controllerLost error -- "Remote control was lost.
    // Reconnect." -- never fires on a live session. The warning being up
    // proves the gate already returned, so the error it must not emit has
    // had its chance; settle one beat before counting.
    await page.waitForTimeout(300);
    await expect(page.locator('.toast-container .toast.error')).toHaveCount(0);
    // The fab is back to idle and the radio never keyed.
    await expect(ptt).not.toHaveClass(/ptt-fab-held|ptt-fab-latched/);
    expect(await rigConnected()).toBe(false);
  } finally {
    await server?.kill();
    await rm(dir, { recursive: true, force: true });
  }
});

test('stopping audio while it connects aborts the attempt without a later start', async ({ page }) => {
  test.setTimeout(90_000);
  const audioStarts: string[] = [];
  const pageInitiatedCloses: number[] = [];
  let holdAudioHandshake = true;
  await page.routeWebSocket(/\/api\/v1\/audio/, (socket) => {
    socket.onMessage((message) => {
      try {
        const frame = JSON.parse(String(message)) as { type?: string; direction?: string };
        if (frame.type === 'audio_start') audioStarts.push(String(frame.direction ?? ''));
      } catch {
        // Binary audio frames are not JSON.
      }
    });
    // The abort pin: this fires exactly when the PAGE closes the socket --
    // which, while the socket is still connecting, is MOR-4469's fix in
    // action (the old close() deferred the teardown and never closed it).
    socket.onClose(() => pageInitiatedCloses.push(Date.now()));
    if (!holdAudioHandshake) {
      socket.connectToServer();
      return;
    }
    // Keep this handler pending: Playwright force-opens an unconnected
    // route once its handler returns, so the held socket stays CONNECTING
    // for exactly as long as this promise never resolves.
    return new Promise<void>(() => {});
  });

  const reportedAudioClients = async () =>
    ((await (await page.request.get(`${AUDIO_HOLD_URL}/api/v1/state`)).json()) as {
      wsClients?: { audio?: number };
    }).wsClients?.audio;

  let server: JourneyServerHandle | undefined;
  try {
    server = await startJourneyServer(AUDIO_HOLD_PORT, 7_030_000, ['--audio']);
    await page.goto(`${AUDIO_HOLD_URL}/?ui=v2`, { waitUntil: 'domcontentloaded' });

    const monitor = page.locator('[data-testid="rx-audio-monitor"]');
    const liveChoice = page.locator('[data-testid="rx-audio-monitor-live"]');
    const localChoice = page.locator('[data-testid="rx-audio-monitor-local"]');
    await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'local');
    await expect(liveChoice).toBeVisible();
    await expect(liveChoice).toHaveAttribute('data-live-link', 'false');
    await expect.poll(reportedAudioClients).toBe(0);

    // Start LIVE audio against the held handshake: the demand is on, but
    // the audio socket cannot open and nothing is sent anywhere.
    await liveChoice.click();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'live');
    await expect(liveChoice).toHaveAttribute('aria-checked', 'true');
    await page.waitForTimeout(1_000);
    expect(audioStarts).toEqual([]);
    expect(pageInitiatedCloses).toEqual([]);
    await expect(liveChoice).toHaveAttribute('data-live-link', 'false');
    await expect.poll(reportedAudioClients).toBe(0);

    // Stop while still connecting (the operator's audio-stop action): the
    // connection attempt is cancelled at once ...
    await localChoice.click();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'local');
    await expect.poll(() => pageInitiatedCloses.length).toBe(1);
    // ... and no late audio start comes out of the aborted attempt.
    await page.waitForTimeout(800);
    expect(audioStarts).toEqual([]);
    await expect.poll(reportedAudioClients).toBe(0);

    // A later explicit start works normally: release the hold and go LIVE
    // again -- a fresh socket opens, exactly one audio_start rx is sent on
    // it, and the server finally counts its audio client.
    holdAudioHandshake = false;
    await liveChoice.click();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'live');
    await expect(liveChoice).toHaveAttribute('data-live-link', 'true');
    await expect.poll(() => audioStarts.filter((direction) => direction === 'rx').length).toBe(1);
    await expect.poll(reportedAudioClients).toBe(1);
    expect(pageInitiatedCloses).toHaveLength(1);
  } finally {
    await server?.kill();
  }
});
