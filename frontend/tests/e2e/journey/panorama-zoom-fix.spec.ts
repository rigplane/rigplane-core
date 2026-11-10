import { createReadStream } from 'node:fs';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame } from '../../../src/lib/runtime/scope-frame';
import { startJourneyServer } from './server-process';
import { parseScopeWriteReceipt } from './panorama-receipt';
import { expectPanoramaClean, expectPanoramaSettled, watchPanoramaErrors } from './panorama-guards';

// ── MOR-4392 ──
// Item 3 (owner, WebKit, real IC-705): after several ZOOM± and FIX on/off
// the MORE button on the panorama toolbar stopped responding until a page
// reload — a client-side effect loop or uncaught error. Item 2: in FIX the
// VFO marker vanished whenever a CENTER zoom/SPAN step had left a follow
// width behind. Item 4: the filter passband overlay disappeared after
// CTR ↔ FIX switching at ±250k. These specs pin all three on the harness.

function watchScopeFrames(page: import('@playwright/test').Page) {
  const frames: Array<{ view: number[]; pixels: boolean; receivedAt: number }> = [];
  page.on('websocket', (socket) => {
    if (!socket.url().endsWith('/api/v1/scope')) return;
    socket.on('framereceived', ({ payload }) => {
      if (typeof payload === 'string') return;
      const buffer = Uint8Array.from(payload).buffer;
      const parsed = parseCurrentScopeFrame(buffer, { source: 'radio', sourceId: 'radio', epoch: 0 });
      if (parsed.ok) frames.push({ view: [parsed.frame.viewStartHz, parsed.frame.viewEndHz],
        pixels: parsed.frame.pixels?.some((value) => value > 0) === true, receivedAt: performance.now() });
    });
  });
  return frames;
}

async function scopeWriteReceipts(evidence: string) {
  let contents = '';
  for await (const chunk of createReadStream(evidence, { encoding: 'utf8' })) contents += chunk;
  return contents.trim().split('\n').filter(Boolean).map(parseScopeWriteReceipt);
}

for (const [index, [model, withFix]] of ([['IC-705', false], ['IC-7610', true]] as const).entries()) {
  test(`${model}: ZOOM/FIX churn keeps the MORE button alive with zero page errors (MOR-4392)`, async ({ page }, info) => {
    const port = 18910 + index * 2;
    const evidence = info.outputPath('radio.jsonl');
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
    const watch = watchPanoramaErrors(page);
    const frames = watchScopeFrames(page);
    try {
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
      const more = page.getByRole('button', { name: 'More scope controls' }).first();
      await more.click();
      for (let i = 0; i < 3; i++) await page.getByTestId('scope-zoom-in').first().click();
      for (let i = 0; i < 2; i++) await page.getByTestId('scope-zoom-out').first().click();
      await page.keyboard.press('Escape');
      if (withFix) {
        await page.getByRole('radio', { name: 'FIX' }).first().click();
        await expect.poll(() => frames.at(-1)?.view).toEqual([14_200_000, 14_300_000]);
        await page.getByRole('radio', { name: 'CTR' }).first().click();
        await expect.poll(async () => (await scopeWriteReceipts(evidence)).filter(
          (receipt) => receipt.sub === 0x14).at(-1)?.data[1]).toBe(0);
        await more.click();
        await page.getByTestId('scope-zoom-in').first().click();
        await page.keyboard.press('Escape');
      }
      // The owner's dead button: nothing may cover it and the click must open.
      const unobstructed = await more.evaluate((button) => {
        const rect = button.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return hit === button || button.contains(hit);
      });
      expect(unobstructed).toBe(true);
      await more.click();
      await expect(page.locator('[data-testid="scope-more-panel"]')).toBeVisible();
      expectPanoramaClean(watch);
    } finally {
      await server.kill();
    }
  });
}

test('IC-7610: FIX marker and axis use the fixed window after a CENTER zoom (MOR-4392)', async ({ page }, info) => {
  const port = 18914;
  const evidence = info.outputPath('radio.jsonl');
  const server = await startJourneyServer(port, 14_250_000,
    ['--model', 'IC-7610', '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
  const watch = watchPanoramaErrors(page);
  const frames = watchScopeFrames(page);
  try {
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
    // A CENTER zoom leaves a requested width behind; it must not leak into FIX.
    const modeWritesBefore = (await scopeWriteReceipts(evidence)).filter((receipt) => receipt.sub === 0x14).length;
    await page.getByRole('button', { name: 'More scope controls' }).first().click();
    await page.getByTestId('scope-zoom-in').first().click();
    await page.keyboard.press('Escape');
    await page.getByRole('radio', { name: 'FIX' }).first().click();
    await expect.poll(() => frames.at(-1)?.view).toEqual([14_200_000, 14_300_000]);
    const marker = page.locator('.waterfall-content .tune-line');
    await expect(marker).toBeVisible();
    expect(parseFloat(await marker.evaluate((line) => line.style.left))).toBeCloseTo(50, 0);
    const ticks = page.locator('.freq-axis .tick');
    await expect(ticks.first()).toHaveText('14.200');
    await expect(ticks.last()).toHaveText('14.300');
    // A ZOOM inside FIX crops within the fixed window and must not flip the
    // scope mode back to CENTER (owner rule): every 0x14 write since the FIX
    // gesture carries mode 1.
    await page.getByRole('button', { name: 'More scope controls' }).first().click();
    await page.getByTestId('scope-zoom-in').first().click();
    await page.keyboard.press('Escape');
    await expect(ticks.first()).toHaveText('14.225');
    await expect(ticks.last()).toHaveText('14.275');
    await expect(marker).toBeVisible();
    const modeWrites = (await scopeWriteReceipts(evidence)).filter((receipt) => receipt.sub === 0x14);
    expect(modeWrites.length).toBeGreaterThan(modeWritesBefore);
    for (const receipt of modeWrites.slice(modeWritesBefore)) expect(receipt.data[1]).toBe(1);
    // Back to CTR: the marker stays and no page error ever fired.
    await page.getByRole('radio', { name: 'CTR' }).first().click();
    await expect.poll(async () => (await scopeWriteReceipts(evidence)).filter(
      (receipt) => receipt.sub === 0x14).at(-1)?.data[1]).toBe(0);
    await expect(marker).toBeVisible();
    expectPanoramaSettled(watch);
  } finally {
    await server.kill();
  }
});

test('IC-7610: passband overlay survives CTR↔FIX at ±250k, LSB 3 kHz, after a reload (MOR-4392)', async ({ page }, info) => {
  const port = 18916;
  const evidence = info.outputPath('radio.jsonl');
  const server = await startJourneyServer(port, 14_250_000,
    ['--model', 'IC-7610', '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
  const watch = watchPanoramaErrors(page);
  const frames = watchScopeFrames(page);
  const radioState = async () => (await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json()) as {
    main?: { freqHz?: number; mode?: string; filterWidth?: number };
  };
  const stepTo250k = async () => {
    await page.getByRole('button', { name: 'More scope controls' }).first().click();
    await page.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
    await page.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±250k');
    await expect.poll(() => frames.at(-1)?.view).toEqual([14_000_000, 14_500_000]);
  };
  const expectPassband = async (widthHz: number) => {
    const overlay = page.locator('.waterfall-content .passband-overlay');
    const marker = page.locator('.waterfall-content .tune-line');
    await expect(overlay).toBeVisible();
    await expect(marker).toBeVisible();
    const content = await page.locator('.waterfall-content').first().boundingBox();
    const band = await overlay.boundingBox();
    const line = await marker.boundingBox();
    expect(content).not.toBeNull(); expect(band).not.toBeNull(); expect(line).not.toBeNull();
    // The 3 kHz LSB passband sits LEFT of the carrier marker; its right edge
    // is the carrier and its width is widthHz/coverage of the panel (±1 px).
    expect(Math.abs(band!.width - (widthHz / 500_000) * content!.width)).toBeLessThanOrEqual(1);
    expect(Math.abs(band!.x + band!.width - (line!.x + line!.width / 2))).toBeLessThanOrEqual(2);
  };
  try {
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
    await page.locator('[data-testid="standard-mode-LSB"]').getByRole('radio').click();
    await page.locator('[data-testid="filter-width"] input[type=range]').fill('3000');
    await expect.poll(radioState).toMatchObject({ main: { mode: 'LSB', filterWidth: 3000 } });
    await stepTo250k();
    await expectPassband(3000);
    // CTR → FIX → CTR: the passband keeps the same geometry afterwards.
    await page.getByRole('radio', { name: 'FIX' }).first().click();
    await expect.poll(() => frames.at(-1)?.view).toEqual([14_200_000, 14_300_000]);
    await page.getByRole('radio', { name: 'CTR' }).first().click();
    await expect.poll(() => frames.at(-1)?.view).toEqual([14_000_000, 14_500_000]);
    await expectPassband(3000);
    // A reload keeps the radio-side state; stepping back to ±250k must draw
    // the same overlay (the owner's loss survived a reload on build 1).
    const mark = frames.length;
    await page.reload();
    await expect.poll(() => frames.length - mark, { timeout: 30_000 }).toBeGreaterThan(10);
    await expect.poll(radioState).toMatchObject({ main: { mode: 'LSB', filterWidth: 3000 } });
    await stepTo250k();
    await expectPassband(3000);
    expectPanoramaClean(watch);
  } finally {
    await server.kill();
  }
});
