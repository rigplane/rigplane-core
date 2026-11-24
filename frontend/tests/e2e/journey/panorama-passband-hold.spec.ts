import type { Page, TestInfo } from '@playwright/test';
import type { ScopePassbandDiagnostics } from '../../../src/lib/runtime/adapters/scope-passband-display';
import type { ServerState } from '../../../src/lib/types/state';
import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { startJourneyServer } from './server-process';
import { openPanorama } from './panorama-ready';
import { expectPanoramaSettled, watchPanoramaErrors } from './panorama-guards';
import {
  expectFollowWindowOnVfo, gateDiff, installSampler, missingLivePassband, passbandGaps, settlePassband, watchScopeFrames,
} from './panorama-passband-probe';

// ── MOR-4387 owner build-4, item 1: the passband overlay must not blink ──
// The owner saw the passband vanish for ~1 s during 12 quick arrow-key
// presses and again when ZOOM crossed a coverage preset. The overlay draws
// only while `tuneVisible && spanHz > 0 && pbWidthPct > 0`
// (SpectrumPanel.svelte), gated on the spectrum authority's
// mode/filterWidth/ifShift freshness, the managed passband tuple state and
// viewport containment. The DOM probe records changes to these gates even
// when the browser has no animation frame scheduled.

async function radioFrequency(page: Page, port: number): Promise<number> {
  const state = await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json() as ServerState;
  return state.main?.freqHz ?? NaN;
}

async function expectPassbandHeld(
  page: Page, info: TestInfo, fromT: number, file: string, endGates: Partial<ScopePassbandDiagnostics>,
): Promise<void> {
  let toT = fromT;
  await expect.poll(async () => {
    const sample = await page.evaluate(() => window.passbandSamples.at(-1));
    toT = sample?.t ?? fromT;
    return sample;
  }).toMatchObject({ overlay: true, gates: { spanPositive: true, ...endGates } });
  expect(toT).toBeGreaterThan(fromT);
  const samples = await page.evaluate(() => window.passbandSamples);
  const gaps = passbandGaps(samples, fromT, toT).map(gap => ({ ...gap, gateDiff: gateDiff(gap) }));
  await writeFile(info.outputPath(file), JSON.stringify({ fromT, toT, gaps, samples }, null, 2));
  // A scope outage clears the sample plane. Once it is live, the overlay
  // must be present throughout the transition, including the first live view.
  const missing = missingLivePassband(samples, fromT, toT);
  expect(missing, 'passband overlay missing from a live scope view').toBeUndefined();
}

for (const [index, model] of ['IC-705', 'IC-7300'].entries()) {
  test(`${model}: passband overlay holds through a 12-step arrow burst (MOR-4387 owner b4)`, async ({ page }, info) => {
    const port = 18920 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    const watch = watchPanoramaErrors(page);
    const frames = watchScopeFrames(page);
    try {
      await installSampler(page);
      await openPanorama(page, `http://127.0.0.1:${port}/?ui=v2`);
      await settlePassband(page, port);
      // Width setup can leave a native range input focused; arrow keys
      // belong to tuning only after leaving that editor.
      await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
      const beforeHz = await radioFrequency(page, port);
      const fromT = await page.evaluate(() => performance.now());
      // Human-speed burst: 12 ArrowRight presses ~100 ms apart.
      for (let i = 0; i < 12; i++) {
        await page.keyboard.press('ArrowRight');
        await page.waitForTimeout(100);
      }
      const finalHz = beforeHz + 12_000;
      await expect.poll(() => radioFrequency(page, port)).toBe(finalHz);
      await expectFollowWindowOnVfo(frames, () => radioFrequency(page, port), beforeHz);
      await expectPassbandHeld(page, info, fromT, 'passband-burst.json', { freqHz: finalHz });
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
    const frames = watchScopeFrames(page);
    try {
      await installSampler(page);
      await openPanorama(page, `http://127.0.0.1:${port}/?ui=v2`);
      await settlePassband(page, port);
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      const liveFrameWidth = () => {
        const frame = frames.at(-1);
        return frame?.pixels ? frame.view[1]! - frame.view[0]! : null;
      };
      await expect.poll(liveFrameWidth).toBe(100_000);
      const fromT = await page.evaluate(() => performance.now());
      // ZOOM+ twice: each halves the requested width; on the simulator's
      // preset ladder (±50k → ±25k → …) each press switches the granted
      // coverage preset — the regrant that blinked the passband on b4.
      await page.getByTestId('scope-zoom-in').first().click();
      await expect.poll(liveFrameWidth).toBe(50_000);
      await page.getByTestId('scope-zoom-in').first().click();
      await page.keyboard.press('Escape');
      await expect.poll(liveFrameWidth).toBe(25_000);
      await expectPassbandHeld(page, info, fromT, 'passband-zoom.json', {
        viewStartHz: 14_237_500, viewEndHz: 14_262_500,
      });
      expectPanoramaSettled(watch);
    } finally {
      await server.kill();
    }
  });

  test(`${model}: passband overlay holds through arrow retunes inside a pinned band preset (explicit view)`, async ({ page }, info) => {
    const port = 18940 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    const watch = watchPanoramaErrors(page);
    const frames = watchScopeFrames(page);
    try {
      await installSampler(page);
      await openPanorama(page, `http://127.0.0.1:${port}/?ui=v2`);
      await settlePassband(page, port);
      // The B key = the band the VFO is in: an ABSOLUTE view (explicitView).
      await page.getByTestId('scope-zoom-band').first().click();
      await expect.poll(() => frames.at(-1)).toMatchObject({ pixels: true, view: [14_000_000, 14_350_000] });
      const pin = { proportional: true, viewStartHz: 14_000_000, viewEndHz: 14_350_000 };
      await expect.poll(() => page.evaluate(() => window.passbandSamples.at(-1)))
        .toMatchObject({ overlay: true, gates: pin });
      await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
      const beforeHz = await radioFrequency(page, port);
      const fromT = await page.evaluate(() => performance.now());
      for (let i = 0; i < 3; i++) {
        await page.keyboard.press('ArrowRight');
        await page.waitForTimeout(700);
      }
      const finalHz = beforeHz + 3_000;
      await expect.poll(() => radioFrequency(page, port)).toBe(finalHz);
      await expectPassbandHeld(page, info, fromT, 'passband-preset-retune.json', { ...pin, freqHz: finalHz });
      expectPanoramaSettled(watch);
    } finally {
      await server.kill();
    }
  });
}
