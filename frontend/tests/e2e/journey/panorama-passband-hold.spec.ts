import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { startJourneyServer } from './server-process';
import { expectPanoramaSettled, watchPanoramaErrors } from './panorama-guards';

// ── MOR-4387 owner build-4, item 1: the passband overlay must not blink ──
// The owner saw the passband vanish for ~1 s during 12 quick arrow-key
// presses and again when ZOOM crossed a coverage preset. The overlay draws
// only while `tuneVisible && spanHz > 0 && pbWidthPct > 0`
// (SpectrumPanel.svelte), gated on the spectrum authority's
// mode/filterWidth/ifShift freshness, the managed passband tuple state and
// the viewport containment. This spec samples the whole gate chain
// (`data-passband-gates` on the panel root) once per animation frame while
// driving the two scenarios, so the regression assertion names the gate
// that dropped instead of just "overlay gone".

interface PassbandSample {
  t: number;
  overlay: boolean;
  gates: Record<string, unknown> | null;
  state: string | null;
}

declare global {
  interface Window { passbandSamples: PassbandSample[] }
}

async function installSampler(page: import('@playwright/test').Page): Promise<void> {
  await page.addInitScript(() => {
    window.passbandSamples = [];
    const loop = () => {
      const panel = document.querySelector('.spectrum-panel');
      const overlay = panel?.querySelector('.waterfall-content .passband-overlay');
      const gatesRaw = panel?.getAttribute('data-passband-gates');
      window.passbandSamples.push({
        t: performance.now(),
        overlay: overlay instanceof HTMLElement && overlay.getBoundingClientRect().width > 0,
        gates: gatesRaw ? JSON.parse(gatesRaw) as Record<string, unknown> : null,
        state: panel?.getAttribute('data-passband-state') ?? null,
      });
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
}

interface Gap {
  startMs: number;
  frames: number;
  durationMs: number;
  /** Gate JSON at the last good frame and the first bad frame. */
  before: Record<string, unknown> | null;
  atDrop: Record<string, unknown> | null;
  stateAtDrop: string | null;
}

/** Contiguous runs of absent overlay inside [fromT, toT], plus the gate diff at each drop. */
function passbandGaps(samples: PassbandSample[], fromT: number, toT: number): Gap[] {
  const windowed = samples.filter((sample) => sample.t >= fromT && sample.t <= toT);
  const gaps: Gap[] = [];
  let run: Gap | null = null;
  let lastGood: PassbandSample | null = null;
  for (const sample of windowed) {
    if (sample.overlay) {
      run = null;
      lastGood = sample;
      continue;
    }
    if (run === null) {
      run = {
        startMs: sample.t, frames: 0, durationMs: 0,
        before: lastGood?.gates ?? null, atDrop: sample.gates, stateAtDrop: sample.state,
      };
      gaps.push(run);
    }
    run.frames += 1;
    run.durationMs = sample.t - run.startMs;
  }
  return gaps;
}

function gateDiff(gap: Gap): Record<string, unknown> {
  const diff: Record<string, unknown> = {};
  if (!gap.before || !gap.atDrop) return diff;
  for (const key of Object.keys(gap.atDrop)) {
    if (JSON.stringify(gap.before[key]) !== JSON.stringify(gap.atDrop[key])) {
      diff[key] = { before: gap.before[key], at: gap.atDrop[key] };
    }
  }
  return diff;
}

/**
 * Bring the simulator into a state where the passband overlay can draw at
 * all: the harness models CI-V 1A 03 (filter width) and 14 07/08 (PBT), but
 * the tuple's evidence chain needs a confirmed mode+filter+width set first
 * (same setup as panorama-zoom-fix.spec.ts's passband spec). Dumps the last
 * gate sample when the overlay never appears, so a setup failure names the
 * missing leg instead of a bare timeout.
 */
async function settlePassband(page: import('@playwright/test').Page, port: number): Promise<void> {
  const radioState = async () => (await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json()) as {
    main?: { freqHz?: number; mode?: string; filterWidth?: number };
  };
  await expect.poll(async () => (await radioState()).main?.filterWidth, { timeout: 30_000 }).toBe(2400);
  await page.locator('[data-testid="standard-mode-LSB"]').getByRole('radio').click();
  await page.locator('[data-testid="filter-width"] input[type=range]').fill('3000');
  await expect.poll(radioState).toMatchObject({ main: { mode: 'LSB', filterWidth: 3000 } });
  const overlay = page.locator('.waterfall-content .passband-overlay');
  try {
    await expect(overlay).toBeVisible({ timeout: 30_000 });
  } catch (error) {
    const last = await page.evaluate(() => window.passbandSamples.at(-1) ?? null);
    console.log(`[passband-setup] overlay never appeared; last sample: ${JSON.stringify(last)}`);
    throw error;
  }
}

for (const [index, model] of ['IC-705', 'IC-7300'].entries()) {
  test(`${model}: passband overlay holds through a 12-step arrow burst (MOR-4387 owner b4)`, async ({ page }, info) => {
    const port = 18920 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    const watch = watchPanoramaErrors(page);
    try {
      await installSampler(page);
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      await settlePassband(page, port);
      await page.waitForTimeout(1000);
      const fromT = await page.evaluate(() => performance.now());
      // Human-speed burst: 12 ArrowRight presses ~100 ms apart.
      for (let i = 0; i < 12; i++) {
        await page.keyboard.press('ArrowRight');
        await page.waitForTimeout(100);
      }
      await page.waitForTimeout(2000);
      const toT = await page.evaluate(() => performance.now());
      const samples = await page.evaluate(() => window.passbandSamples);
      const gaps = passbandGaps(samples, fromT, toT);
      const report = gaps.map((gap) => ({ ...gap, gateDiff: gateDiff(gap) }));
      await writeFile(info.outputPath('passband-burst.json'),
        JSON.stringify({ model, fromT, toT, gaps: report, samples }, null, 2));
      console.log(`[passband-burst ${model}] gaps=${report.length}`);
      for (const gap of report) {
        console.log(`  gap ${gap.frames} frames ${gap.durationMs.toFixed(0)} ms ` +
          `state=${gap.stateAtDrop} diff=${JSON.stringify(gateDiff(gap))}`);
      }
      // The passband may drop for at most one animation frame (a benign
      // re-render beat); anything longer is the owner's blink.
      for (const gap of gaps) {
        expect(gap.frames, `overlay absent ${gap.frames} frames (${gap.durationMs.toFixed(0)} ms); ` +
          `gate diff: ${JSON.stringify(gateDiff(gap))}`).toBeLessThanOrEqual(1);
      }
      expectPanoramaSettled(watch);
    } finally {
      await server.kill();
    }
  });

  test(`${model}: passband overlay holds through a ZOOM preset switch in CENTER (MOR-4387 owner b4)`, async ({ page }, info) => {
    const port = 18930 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    const watch = watchPanoramaErrors(page);
    try {
      await installSampler(page);
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      await settlePassband(page, port);
      await page.waitForTimeout(1000);
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      const fromT = await page.evaluate(() => performance.now());
      // ZOOM+ twice: each halves the requested width; on the simulator's
      // preset ladder (±50k → ±25k → …) each press switches the granted
      // coverage preset — the regrant that blinked the passband on b4.
      await page.getByTestId('scope-zoom-in').first().click();
      await page.waitForTimeout(800);
      await page.getByTestId('scope-zoom-in').first().click();
      await page.keyboard.press('Escape');
      await page.waitForTimeout(2000);
      const toT = await page.evaluate(() => performance.now());
      const samples = await page.evaluate(() => window.passbandSamples);
      const gaps = passbandGaps(samples, fromT, toT);
      const report = gaps.map((gap) => ({ ...gap, gateDiff: gateDiff(gap) }));
      await writeFile(info.outputPath('passband-zoom.json'),
        JSON.stringify({ model, fromT, toT, gaps: report, samples }, null, 2));
      console.log(`[passband-zoom ${model}] gaps=${report.length}`);
      for (const gap of report) {
        console.log(`  gap ${gap.frames} frames ${gap.durationMs.toFixed(0)} ms ` +
          `state=${gap.stateAtDrop} diff=${JSON.stringify(gateDiff(gap))}`);
      }
      for (const gap of gaps) {
        expect(gap.frames, `overlay absent ${gap.frames} frames (${gap.durationMs.toFixed(0)} ms); ` +
          `gate diff: ${JSON.stringify(gateDiff(gap))}`).toBeLessThanOrEqual(1);
      }
      expectPanoramaSettled(watch);
    } finally {
      await server.kill();
    }
  });
}
