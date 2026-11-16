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
 * Twelve ArrowRight presses (tuning step 1 kHz, USB default) build one live
 * burst, then an absolute entry-dialog jump releases it: the release starts
 * a fresh association window (keyboard steps carry no paced-drag flag), so a
 * delayed pre-release echo arriving with the next +1 kHz step is
 * unassociated on the pre-fix code — it cold-resets onto the stale echo and
 * the emitted targets step backward. With the fix, the live replacement
 * inherits the anchor and the lifecycle-backed association still matches the
 * earlier in-burst target: the step accumulates onto the release target and
 * every emitted target is non-decreasing.
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

/** Tune the active VFO to an exact MHz value through the entry dialog (journey pattern). */
async function tuneViaEntry(page: Page, mhz: string): Promise<void> {
  await page.locator('.receiver-instrument [data-vfo-freq]').first().click();
  await page.getByTestId('band-entry-input').fill(mhz);
  await expect(page.getByTestId('band-entry-set')).toBeEnabled();
  await page.getByTestId('band-entry-set').click();
}

test('IC-705 slow link: steps, absolute release, delayed echo, step lands on release + 1 kHz', async ({ page }, info) => {
  const port = Number(process.env.MOR4393_PORT ?? 18849);
  const base = 14_250_000;
  const release = 14_300_000;
  const expected = release + 1_000;
  const server = await startJourneyServer(port, base,
    ['--model', 'IC-705', '--evidence', info.outputPath('radio.jsonl'),
      '--response-delay', '0.5'], 'tests/e2e/panorama_web_server.py');
  try {
    const sentFreqs: number[] = [];
    page.on('websocket', socket => {
      if (new URL(socket.url()).pathname !== '/api/v1/ws') return;
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
    // The keyboard tune path reads the observed freqHz from radio state —
    // wait until the browser itself shows the base before pressing.
    await expect.poll(stateFreqHz, { timeout: 30_000 }).toBe(base);
    await expect(readout).toHaveText(/14\s*\.\s*250/, { timeout: 30_000 });
    for (let press = 0; press < 12; press++) {
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(150);
    }
    // Readbacks lag the writes: wait until at least one press echo has
    // landed, so the final step below really carries a pre-release echo.
    await expect.poll(stateFreqHz, { timeout: 30_000 }).not.toBe(base);
    // Absolute release through the entry dialog (fresh association window:
    // keyboard steps carry no paced-drag flag to inherit).
    await tuneViaEntry(page, '14.300');
    // Let a delayed pre-release echo land, then step once from observed
    // (confirmed radio truth — never the burst display) truth.
    await readout.click();
    await page.waitForTimeout(700);
    await page.keyboard.press('ArrowRight');
    // The fixed code accumulates onto the release target; the pre-fix code
    // cold-resets onto the stale echo and steps backward.
    await expect
      .poll(async () => readoutHz(readout), { timeout: 20_000 })
      .toBe(expected);
    await expect
      .poll(stateFreqHz, { timeout: 30_000, intervals: [500] })
      .toBe(expected);
    expect(sentFreqs.length).toBeGreaterThan(1);
    expect(sentFreqs.at(-1)).toBe(expected);
    for (let index = 1; index < sentFreqs.length; index++) {
      expect(sentFreqs[index]!).toBeGreaterThanOrEqual(sentFreqs[index - 1]!);
    }
  } finally {
    await server.kill();
  }
});
