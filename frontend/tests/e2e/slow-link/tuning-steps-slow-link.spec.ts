import { expect, test, type Locator, type Page } from '@playwright/test';
import { startJourneyServer } from '../journey/server-process';
import type { RadioIntent } from '../../../src/lib/runtime/commands/radio-intents';
import type { WsCommand, WsIncoming } from '../../../src/lib/types/protocol';
import type { ServerStatePublic } from '../../../src/lib/types/state';

/**
 * MOR-4393 — step tuning under downlink lag, real WebServer + WebKit,
 * loopback PanoramaSimulator with `--response-delay` (the harness knob in
 * tests/e2e/panorama_web_server.py): every CI-V response — writes,
 * transceive echoes, poll readbacks — arrives late, so the client observes
 * older own-target frequencies while newer tuning intent already exists.
 * The WebServer answers WS-queued set_freq at enqueue, so delaying only the
 * radio-side CI-V ACK never leaves a frontend lifecycle pending; the row
 * additionally holds the command responses on the browser's control socket
 * (Playwright routeWebSocket), so an echo of an own target reaches the client
 * while that command's lifecycle is still pending — no ACK marker yet.
 *
 * Eight ArrowLeft presses build one command burst; the operator then
 * grabs the panorama and drags before any echo is back. The drag starts a
 * new gesture (absolute target) and the release a further one, while poll
 * readbacks of the OLDER press and drag targets keep reaching the browser —
 * after the release, with the release target still in flight. The next
 * wheel step must accumulate onto the release target (release + 1 kHz); a
 * client that cannot associate those older own-target echoes with the same
 * burst treats them as foreign changes, aborts the drag and cold-resets the
 * step onto a stale value. The drag keeps sliding until an echo has arrived
 * (the operator sees the lag and keeps going), then releases a few ticks
 * later, so the next echo is provably older than the release.
 *
 * Kept out of tests/e2e/journey/ on purpose: the journey playwright config
 * sweeps every `*.spec.ts` in that directory.
 */

/** Delay of command responses on the browser's control socket. Longer than the
 * poll-readback echo of the radio (about 0.7 s at the harness delay), shorter
 * than the 5 s command timeout. */
const ACK_LAG_MS = 2_500;

type SetFreqParams = Extract<RadioIntent, { name: 'set_freq' }>['params'];
type SetFreqWireCommand = WsCommand & { name: 'set_freq'; params: SetFreqParams };

function isSetFreqWireCommand(value: unknown): value is SetFreqWireCommand {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  if (record.type !== 'cmd' || record.name !== 'set_freq') return false;
  const params = record.params as Partial<SetFreqParams> | null;
  if (typeof params !== 'object' || params === null) return false;
  const { freq, receiver } = params;
  return typeof freq === 'number' && Number.isSafeInteger(freq)
    && (receiver === undefined || receiver === 0 || receiver === 1);
}

async function fetchState(page: Page, port: number): Promise<ServerStatePublic> {
  const response = await page.request.get(`http://127.0.0.1:${port}/api/v1/state`);
  return (await response.json()) as ServerStatePublic;
}

async function readoutHz(readout: Locator): Promise<number> {
  return Number((((await readout.textContent()) ?? '').replace(/\D/g, '')));
}

test('IC-705 slow link: paced drag, release, delayed own-target echo, step lands on release + 1 kHz', async ({ page }, info) => {
  const port = Number(process.env.MOR4393_PORT ?? 18849);
  const base = 14_250_000;
  const server = await startJourneyServer(port, base,
    ['--model', 'IC-705', '--evidence', info.outputPath('radio.jsonl'),
      '--response-delay', '0.5'], 'tests/e2e/panorama_web_server.py');
  try {
    const sentFreqs: number[] = [];
    let scopeFrames = 0;
    // Scope frames are binary on /api/v1/scope; the panel's drag gesture is
    // inert until the live scope plane (and its geometry) exists.
    page.on('websocket', socket => {
      if (new URL(socket.url()).pathname !== '/api/v1/scope') return;
      socket.on('framereceived', ({ payload }) => {
        if (typeof payload !== 'string') scopeFrames++;
      });
    });
    // Slow ACK path: the command responses reach the browser ACK_LAG_MS late
    // (everything else on the control socket passes through unchanged), so
    // the radio readbacks of our own targets arrive while their command
    // lifecycles are still pending — the association-unavailable window.
    await page.routeWebSocket(/\/api\/v1\/ws(\?.*)?$/, ws => {
      const upstream = ws.connectToServer();
      ws.onMessage(message => {
        if (typeof message === 'string') {
          const command: unknown = JSON.parse(message) as unknown;
          if (isSetFreqWireCommand(command)) sentFreqs.push(command.params.freq);
        }
        upstream.send(message);
      });
      upstream.onMessage(message => {
        const incoming = typeof message === 'string' ? (JSON.parse(message) as WsIncoming) : null;
        if (incoming?.type === 'response') setTimeout(() => ws.send(message), ACK_LAG_MS);
        else ws.send(message);
      });
    });
    const stateFreqHz = async (): Promise<number | null> =>
      (await fetchState(page, port)).main.freqHz;
    const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    // The tune path reads the observed freqHz from radio state — wait until
    // the browser itself shows the base before gesturing.
    await expect.poll(stateFreqHz, { timeout: 30_000 }).toBe(base);
    await expect(readout).toHaveText(/14\s*\.\s*250/, { timeout: 30_000 });
    await expect.poll(() => scopeFrames, { timeout: 30_000 }).toBeGreaterThan(10);
    // Eight ArrowLeft presses (tuning step 1 kHz) lower the frequency in one live burst; the
    // operator then grabs the panorama before the first echo is back.
    for (let press = 0; press < 8; press++) {
      await page.keyboard.press('ArrowLeft');
      await page.waitForTimeout(150);
    }
    const pressTargets = [...sentFreqs];
    expect(pressTargets.length).toBeGreaterThan(1);
    const pressMax = Math.max(...pressTargets);
    expect(pressMax).toBeLessThan(base);
    // Real paced drag: slide left (raises the frequency) in 25 px moves, each
    // past the pacing interval, until the drag target is well above the base
    // (every press target lies below it) and an own-target echo has reached the radio state during the
    // drag, then three more moves, then release.
    const area = page.locator('.spectrum-area').first();
    await expect(area).toBeVisible({ timeout: 30_000 });
    const box = await area.boundingBox();
    expect(box).not.toBeNull();
    const y = box!.y + box!.height * 0.3;
    const startX = box!.x + box!.width * 0.7;
    await page.mouse.move(startX, y);
    await page.mouse.down();
    let moves = 0;
    const slide = async (): Promise<void> => {
      moves += 1;
      await page.mouse.move(startX - moves * 25, y, { steps: 3 });
      await page.waitForTimeout(150);
    };
    let seen = await stateFreqHz();
    let echoDuringDrag = false;
    for (let guard = 0; guard < 60; guard++) {
      await slide();
      const now = await stateFreqHz();
      if (now !== seen) echoDuringDrag = true;
      seen = now;
      if (echoDuringDrag && sentFreqs.at(-1)! > base + 2_000) break;
    }
    expect(echoDuringDrag).toBe(true);
    for (let move = 0; move < 3; move++) await slide();
    await page.mouse.up();
    expect(sentFreqs.length).toBeGreaterThan(pressTargets.length + 1);
    const release = sentFreqs.at(-1)!;
    // The drag starts from the observed (still base) frequency, so its own
    // targets restart below the press targets; they rise monotonically and
    // the release lands above every press and drag target — only then does
    // a backward cold reset onto a stale own-target echo violate the
    // monotonicity asserted below.
    const dragSends = sentFreqs.slice(pressTargets.length);
    for (let index = 1; index < dragSends.length; index++) {
      expect(dragSends[index]!).toBeGreaterThanOrEqual(dragSends[index - 1]!);
    }
    for (const freq of sentFreqs) expect(release).toBeGreaterThanOrEqual(freq);
    const expected = release + 1_000;
    // Wait for the stale echo: an OLDER own target, strictly below the
    // release, reaching the browser after the release while the release
    // target is still in flight.
    await expect.poll(async () => {
      const freq = await stateFreqHz();
      return freq !== null && freq < release && sentFreqs.includes(freq);
    }, { timeout: 15_000, intervals: [50] }).toBe(true);
    await page.waitForTimeout(100);
    // The condition must still hold at the moment of the step.
    expect((await stateFreqHz()) ?? Infinity).toBeLessThan(release);
    await page.mouse.wheel(0, -120);
    // The fixed code accumulates onto the release target; the pre-fix code
    // cold-resets onto the stale echo and steps backward.
    await expect
      .poll(async () => readoutHz(readout), { timeout: 20_000 })
      .toBe(expected);
    await expect
      .poll(stateFreqHz, { timeout: 30_000, intervals: [500] })
      .toBe(expected);
    expect(sentFreqs.at(-1)).toBe(expected);
    // Nothing emitted from the release on may step backward.
    const fromRelease = sentFreqs.slice(sentFreqs.lastIndexOf(release));
    for (let index = 1; index < fromRelease.length; index++) {
      expect(fromRelease[index]!).toBeGreaterThanOrEqual(fromRelease[index - 1]!);
    }
  } finally {
    await server.kill();
  }
});
