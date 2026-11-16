import { expect, test, type Locator, type Page } from '@playwright/test';
import { startJourneyServer } from '../journey/server-process';
import type { RadioIntent } from '../../../src/lib/runtime/commands/radio-intents';
import type { WsCommand } from '../../../src/lib/types/protocol';
import type { ServerStatePublic } from '../../../src/lib/types/state';

/**
 * MOR-4393 — step tuning under downlink lag, real WebServer + WebKit,
 * loopback PanoramaSimulator with `--response-delay` (the harness knob in
 * tests/e2e/panorama_web_server.py): every CI-V response — writes,
 * transceive echoes, poll readbacks — arrives late, so the client observes
 * older own-target frequencies while newer tuning intent already exists.
 * (The WebServer answers WS-queued set_freq at enqueue, so delaying only
 * the radio-side CI-V ACK never leaves a frontend lifecycle pending and
 * cannot produce the unassociated echo this row needs.)
 *
 * A real paced drag on the panorama sends one set_freq per pacing tick; the
 * release then sends the absolute target and starts a fresh command burst.
 * With lag, a poll readback of an OLDER own drag target reaches the browser
 * after the release, while the release target is still in flight. The next
 * wheel step must accumulate onto the release target (release + 1 kHz); a
 * client that cannot associate the older echo with the same gesture
 * cold-resets onto it and steps backward. The drag keeps sliding until the
 * first echo has arrived (a user sees the lag and keeps going), then
 * releases a few ticks later, so the next echo is provably older than the
 * release.
 *
 * Kept out of tests/e2e/journey/ on purpose: the journey playwright config
 * sweeps every `*.spec.ts` in that directory.
 */

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
    page.on('websocket', socket => {
      const path = new URL(socket.url()).pathname;
      // Scope frames are binary on /api/v1/scope; the panel's drag gesture is
      // inert until the live scope plane (and its geometry) exists.
      if (path === '/api/v1/scope') {
        socket.on('framereceived', ({ payload }) => {
          if (typeof payload !== 'string') scopeFrames++;
        });
        return;
      }
      if (path !== '/api/v1/ws') return;
      socket.on('framesent', ({ payload }) => {
        if (typeof payload !== 'string') return;
        const command: unknown = JSON.parse(payload) as unknown;
        if (isSetFreqWireCommand(command)) sentFreqs.push(command.params.freq);
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
    // Real paced drag: slide left (raises the frequency) in 25 px moves, each
    // past the pacing interval, until the first own-target echo reaches the
    // radio state, then three more moves, then release.
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
    for (let move = 0; move < 3; move++) await slide();
    for (let guard = 0; guard < 40 && (await stateFreqHz()) === base; guard++) await slide();
    expect(await stateFreqHz()).not.toBe(base);
    for (let move = 0; move < 3; move++) await slide();
    await page.mouse.up();
    // At least one intermediate drag target plus the release target.
    expect(sentFreqs.length).toBeGreaterThan(2);
    const release = sentFreqs.at(-1)!;
    // The release must land above every intermediate target: only then does
    // a backward cold reset onto a stale own-target echo violate the
    // monotonicity asserted below.
    for (const freq of sentFreqs) expect(release).toBeGreaterThanOrEqual(freq);
    expect(sentFreqs[0]).toBeGreaterThan(base);
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
    for (let index = 1; index < sentFreqs.length; index++) {
      expect(sentFreqs[index]!).toBeGreaterThanOrEqual(sentFreqs[index - 1]!);
    }
  } finally {
    await server.kill();
  }
});
