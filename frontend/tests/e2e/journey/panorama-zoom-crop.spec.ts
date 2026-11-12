// MOR-4392 integration: requested-width zoom with native-coverage clipping.
import { createReadStream } from 'node:fs';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame } from '../../../src/lib/runtime/scope-frame';
import type { ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import { startJourneyServer } from './server-process';
import { parseScopeWriteReceipt } from './panorama-receipt';
import { expectPanoramaClean, watchPanoramaErrors } from './panorama-guards';

// IC-705 with MOR-4385 semantics: span choices are real view widths.
const PRESET_WIDTHS = [5_000, 10_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000];
const covering = (requestedHz: number) => PRESET_WIDTHS.find((width) => width >= requestedHz)!;

function watchScope(page: import('@playwright/test').Page, tag: string) {
  const sent: Array<ScopeSubscription & { type: string }> = [];
  const frames: Array<{ view: number[]; coverage: number[]; width: number; receivedAt: number }> = [];
  page.on('websocket', (socket) => {
    if (!socket.url().endsWith('/api/v1/scope')) return;
    socket.on('framesent', ({ payload }) => {
      if (typeof payload === 'string') {
        const message = JSON.parse(payload) as ScopeSubscription & { type: string };
        if (message.type === 'subscribe') sent.push(message);
      }
    });
    socket.on('framereceived', ({ payload }) => {
      if (typeof payload === 'string') return;
      const buffer = Uint8Array.from(payload).buffer;
      const parsed = parseCurrentScopeFrame(buffer, { source: 'radio', sourceId: 'radio', epoch: 0 });
      if (parsed.ok) frames.push({ view: [parsed.frame.viewStartHz, parsed.frame.viewEndHz],
        coverage: [parsed.frame.coverageStartHz, parsed.frame.coverageEndHz],
        width: parsed.frame.width, receivedAt: performance.now() });
    });
  });
  return { tag, sent, frames };
}

async function spanWrites(evidence: string) {
  let contents = '';
  for await (const chunk of createReadStream(evidence, { encoding: 'utf8' })) contents += chunk;
  return contents.trim().split('\n').filter(Boolean).map(parseScopeWriteReceipt)
    .filter((receipt) => receipt.sub === 0x15);
}

test('IC-705: ZOOM crops the requested window and writes only at preset boundaries (MOR-4392)', async ({ page }, info) => {
  const port = Number(process.env.PANORAMA_PORT ?? 18930);
  const evidence = info.outputPath('radio.jsonl');
  const server = await startJourneyServer(port, 14_250_000,
    ['--model', 'IC-705', '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
  const watch = watchPanoramaErrors(page);
  const scope = watchScope(page, 'main');
  try {
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => scope.frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
    const marker = page.locator('.waterfall-content .tune-line');
    await expect(marker).toBeVisible();
    // Start at ±250k (500 kHz coverage) through the span control.
    await page.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
    await page.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±250k');
    await expect.poll(() => scope.frames.at(-1)?.view).toEqual([14_000_000, 14_500_000]);

    const zoomIn = async () => {
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      await page.getByTestId('scope-zoom-in').first().click();
      await page.keyboard.press('Escape');
    };
    const zoomOut = async () => {
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      await page.getByTestId('scope-zoom-out').first().click();
      await page.keyboard.press('Escape');
    };
    const expectRequested = async (requestedHz: number, writesBefore: number, expectWrite: boolean) => {
      const freq = 14_250_000;
      // The subscription carries the requested width; the granted view IS the
      // requested window (never the native coverage), coverage the preset.
      await expect.poll(() => scope.sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: requestedHz });
      // The wire frame's coverage is clipped to the granted view by design;
      // the native preset is pinned by the write receipts below.
      await expect.poll(() => scope.frames.at(-1)?.view).toEqual(
        [freq - requestedHz / 2, freq + requestedHz / 2]);
      const writes = (await spanWrites(evidence)).length;
      expect(writes - writesBefore).toBe(expectWrite ? 1 : 0);
      await expect(marker).toBeVisible();
    };

    // ZOOM+ ladder: one 0x15 write per preset boundary, zero between.
    let requested = 500_000;
    const ladder = [250_000, 125_000, 62_500, 31_250, 15_625, 7_812.5, 3_906.25];
    for (const next of ladder) {
      const before = (await spanWrites(evidence)).length;
      await zoomIn();
      requested = next;
      await expectRequested(next, before, covering(next) !== covering(next * 2));
    }
    // Below the narrowest preset: pure crop, zero writes, down to the floor.
    for (let i = 0; i < 3; i++) {
      const before = (await spanWrites(evidence)).length;
      await zoomIn();
      requested /= 2;
      await expectRequested(requested, before, false);
    }
    // The floor of a few native bins of the narrowest span choice stops
    // further zoom-in (no-op, no write).
    const floorWrites = (await spanWrites(evidence)).length;
    const bins = scope.frames.at(-1)!.width;
    const floorHz = 4 * (5_000 / bins);
    const sentSpan = () => (scope.sent.at(-1)?.view as { spanHz?: number }).spanHz ?? 0;
    let floorRequest = requested;
    for (let i = 0; i < 16 && floorRequest > floorHz; i++) {
      const expected = Math.max(floorRequest / 2, floorHz);
      await zoomIn();
      await expect.poll(sentSpan).toBeCloseTo(expected, 4);
      floorRequest = sentSpan();
    }
    expect(floorRequest).toBeCloseTo(floorHz, 4);
    await zoomIn();
    expect(sentSpan()).toBe(floorRequest);
    expect((await spanWrites(evidence)).length).toBe(floorWrites);
    // Rapid ZOOM± inside the sub-preset region: zero writes.
    for (let i = 0; i < 3; i++) { await zoomOut(); await zoomIn(); }
    expect((await spanWrites(evidence)).length).toBe(floorWrites);

    // ZOOM− ladder back up: symmetric writes, then a no-op at the widest.
    let current = floorRequest;
    while (current < 1_000_000) {
      const before = (await spanWrites(evidence)).length;
      await zoomOut();
      const next = Math.min(current * 2, 1_000_000);
      await expectRequested(next, before, covering(next) !== covering(current));
      current = next;
    }
    const widestWrites = (await spanWrites(evidence)).length;
    await zoomOut();
    expect((scope.sent.at(-1)?.view as { spanHz?: number }).spanHz).toBe(1_000_000);
    expect((await spanWrites(evidence)).length).toBe(widestWrites);

    // The marker follows the VFO at any crop level.
    await page.keyboard.press('ArrowLeft');
    await expect(marker).toBeVisible();
    expectPanoramaClean(watch);
  } finally {
    await server.kill();
  }
});

test('IC-705: an external coverage change is adopted with zero writes of our own (MOR-4392)', async ({ page, context }, info) => {
  const port = Number(process.env.PANORAMA_PORT ?? 18930) + 1;
  const evidence = info.outputPath('radio.jsonl');
  const server = await startJourneyServer(port, 14_250_000,
    ['--model', 'IC-705', '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
  const watch = watchPanoramaErrors(page);
  const own = watchScope(page, 'own');
  try {
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => own.frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
    // Zoom to a 50 kHz requested width.
    await page.getByRole('button', { name: 'More scope controls' }).first().click();
    await page.getByTestId('scope-zoom-in').first().click();
    await page.keyboard.press('Escape');
    await expect.poll(() => own.sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 50_000 });
    const ownSubscriptions = own.sent.length;
    // A second client narrows native coverage below our 50 kHz intent.
    const other = await context.newPage();
    const otherScope = watchScope(other, 'other');
    await other.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => otherScope.frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
    const writesBefore = (await spanWrites(evidence)).length;
    await other.getByRole('button', { name: 'Decrease scope span', exact: true }).first().click();
    await other.getByRole('button', { name: 'Decrease scope span', exact: true }).first().click();
    await expect.poll(() => own.frames.at(-1)?.coverage).toEqual([14_240_000, 14_260_000]);
    await expect.poll(() => own.frames.at(-1)?.view).toEqual([14_240_000, 14_260_000]);
    // A clipped delivery does not acknowledge a different zoom intent.
    expect(own.sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 50_000 });
    expect((await spanWrites(evidence)).length - writesBefore).toBe(2);
    await other.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
    await other.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
    await expect.poll(() => otherScope.frames.at(-1)?.coverage).toEqual([14_200_000, 14_300_000]);
    await expect.poll(() => own.frames.at(-1)?.view).toEqual([14_225_000, 14_275_000]);
    expect(own.sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 50_000 });
    expect(own.sent.length).toBe(ownSubscriptions);
    expect((await spanWrites(evidence)).length - writesBefore).toBe(4);
    await other.close();
    expectPanoramaClean(watch);
  } finally {
    await server.kill();
  }
});
