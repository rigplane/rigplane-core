import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import type { ServerStatePublic } from '../../../src/lib/types/state';
import { journeyPort, startJourneyServer, type JourneyServerHandle } from './server-process';

const PTT_DROP_PORT = journeyPort(18770);
const PTT_DROP_URL = `http://127.0.0.1:${PTT_DROP_PORT}`;
const AUDIO_HOLD_PORT = journeyPort(18771);
const AUDIO_HOLD_URL = `http://127.0.0.1:${AUDIO_HOLD_PORT}`;

test('degraded radio health refuses PTT without claiming a lost control link', async ({ page }) => {
  test.setTimeout(90_000);
  const dir = await mkdtemp(path.join(tmpdir(), 'rig-drop-ptt-'));
  const dropFile = path.join(dir, 'drop');
  /** The server's public state, typed with the generated ServerStatePublic contract. */
  const readState = async (): Promise<ServerStatePublic> =>
    (await (await page.request.get(`${PTT_DROP_URL}/api/v1/state`)).json()) as ServerStatePublic;
  /** The two leaves the assertions below pin. Each is a read through the contract,
   *  so a contract change that renames or drops one of them breaks compilation. */
  const readLinkState = async () => {
    const { connection, wsClients } = await readState();
    return {
      connection: { rigConnected: connection.rigConnected },
      wsClients: { control: wsClients?.control },
    };
  };

  let server: JourneyServerHandle | undefined;
  try {
    server = await startJourneyServer(PTT_DROP_PORT, 14_074_000, ['--rig-drop-file', dropFile]);
    await page.setViewportSize({ width: 430, height: 932 });
    const [control] = await Promise.all([
      page.waitForEvent('websocket', (socket) => new URL(socket.url()).pathname === '/api/v1/ws'),
      page.goto(`${PTT_DROP_URL}/?ui=v2`, { waitUntil: 'domcontentloaded' }),
    ]);
    let sentPttOn = 0;
    control.on('framesent', ({ payload }) => {
      if (typeof payload !== 'string') return;
      const frame = JSON.parse(payload) as { type?: string; name?: string };
      if (frame.type === 'cmd' && frame.name === 'ptt_on') sentPttOn++;
    });

    const ptt = page.getByRole('button', { name: 'Push to talk' });
    const radioLink = page.getByTestId('radio-link-line');
    await expect(ptt).toBeVisible();
    await expect.poll(readLinkState).toMatchObject({
      connection: { rigConnected: true }, wsClients: { control: 1 },
    });
    await expect(radioLink).toHaveCount(0);

    await writeFile(dropFile, 'drop');
    // The phone's health line shows that the browser has applied the link loss.
    await expect(radioLink).toBeVisible();
    await expect.poll(readLinkState).toMatchObject({
      connection: { rigConnected: false }, wsClients: { control: 1 },
    });
    expect(control.isClosed()).toBe(false);

    const warning = page.locator('.toast-container .toast.warning').getByText(
      'Command not sent — link to the radio is degraded', { exact: true },
    );
    const controllerLost = page.locator('.toast-container .toast.error').getByText(
      'Remote control was lost. Reconnect.', { exact: true },
    );
    await ptt.hover();
    try {
      // Hold through the PTT-specific gate outcome, including mic preparation.
      await Promise.all([
        page.waitForEvent('console', (message) =>
          message.text() === '[cmd] blocked while radio health is degraded ptt_on'),
        page.mouse.down(),
      ]);
      await expect(warning).toBeVisible();
      await expect(controllerLost).toHaveCount(0);
    } finally {
      await page.mouse.up();
    }
    expect(sentPttOn).toBe(0);
    expect(control.isClosed()).toBe(false);
    await expect(ptt).not.toHaveClass(/ptt-fab-held|ptt-fab-latched/);
  } finally {
    try {
      await server?.kill();
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  }
});

test('stopping audio while it connects aborts the attempt without a later start', async ({ page }) => {
  test.setTimeout(90_000);
  const audioStarts: string[] = [];
  let audioAttempts = 0;
  let heldCloses = 0;
  let holdAudioHandshake = true;
  await page.routeWebSocket(/\/api\/v1\/audio/, (socket) => {
    audioAttempts++;
    const upstream = holdAudioHandshake ? undefined : socket.connectToServer();
    socket.onMessage((message) => {
      // A message handler takes over forwarding from the page to the server.
      upstream?.send(message);
      if (typeof message !== 'string') return;
      const frame = JSON.parse(message) as { type?: string; direction?: string };
      if (frame.type === 'audio_start') audioStarts.push(String(frame.direction ?? ''));
    });
    if (upstream) return;

    // Playwright opens a mocked socket when the route handler returns.
    // Keep the first handshake pending through the page's close call.
    return new Promise<void>((resolve) => {
      socket.onClose(() => {
        heldCloses++;
        resolve();
      });
    });
  });

  const reportedAudioClients = async () =>
    ((await (await page.request.get(`${AUDIO_HOLD_URL}/api/v1/state`)).json()) as {
      wsClients?: { audio?: number };
    }).wsClients?.audio;

  let server: JourneyServerHandle | undefined;
  try {
    server = await startJourneyServer(AUDIO_HOLD_PORT, 7_030_000, ['--audio']);
    await page.goto(`${AUDIO_HOLD_URL}/?ui=v2`, { waitUntil: 'domcontentloaded' });

    const monitor = page.getByTestId('rx-audio-monitor');
    const liveChoice = page.getByTestId('rx-audio-monitor-live');
    const localChoice = page.getByTestId('rx-audio-monitor-local');
    await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'local');
    await expect(liveChoice).toHaveAttribute('data-live-link', 'false');
    await expect.poll(reportedAudioClients).toBe(0);
    expect(audioAttempts).toBe(0);

    await liveChoice.click();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'live');
    await expect(liveChoice).toHaveAttribute('aria-checked', 'true');
    await expect.poll(() => audioAttempts).toBe(1);
    expect(audioStarts).toEqual([]);
    expect(heldCloses).toBe(0);
    await expect(liveChoice).toHaveAttribute('data-live-link', 'false');
    await expect.poll(reportedAudioClients).toBe(0);

    await localChoice.click();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'local');
    await expect.poll(() => heldCloses).toBe(1);
    await expect.poll(reportedAudioClients).toBe(0);
    expect(audioStarts).toEqual([]);
    expect(audioAttempts).toBe(1);

    // The next explicit start gets a fresh socket and a real RX subscription.
    holdAudioHandshake = false;
    await liveChoice.click();
    await expect(monitor).toHaveAttribute('data-monitor-mode', 'live');
    await expect(liveChoice).toHaveAttribute('data-live-link', 'true');
    await expect.poll(reportedAudioClients).toBe(1);
    expect(audioStarts).toEqual(['rx']);
    expect(audioAttempts).toBe(2);
    expect(heldCloses).toBe(1);
  } finally {
    await server?.kill();
  }
});
