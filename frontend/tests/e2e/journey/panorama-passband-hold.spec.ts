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

import { installSampler, settlePassband, passbandGaps, gateDiff } from './panorama-passband-probe';

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
