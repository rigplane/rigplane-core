import { createReadStream } from 'node:fs';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame } from '../../../src/lib/runtime/scope-frame';
import type { WsCommand } from '../../../src/lib/types/protocol';
import { startJourneyServer } from './server-process';
import { parseScopeWriteReceipt } from './panorama-receipt';

/**
 * #4565 pre-review item 1: after a span gesture in a radio-owned mode the
 * REPORTED geometry keeps owning the confirmed view. In SCROLL-C: change
 * span, then tune inside the unchanged radio window — the frequency axes
 * stay put, the carrier (drawn on the spectrum canvas, not a DOM element) follows the tune, and no coverage hatch
 * appears. On the old head the span gesture left `followSpanHz` set, so
 * `(radioOwned && follow === null)` turned false and later frames produced
 * a VFO-centred confirmed view: the panorama slid under the marker and the
 * uncovered offset hatched.
 */
test('IC-705: SCROLL-C keeps the reported window across a span gesture and an in-window tune', async ({ page }, info) => {
  const evidence = info.outputPath('radio.jsonl');
  const port = Number(process.env.PANORAMA_SCROLL_PORT ?? 22210);
  const server = await startJourneyServer(port, 14_250_000,
    ['--model', 'IC-705', '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
  try {
    const frames: Array<{ view: number[]; pixels: boolean; receivedAt: number }> = [];
    const commands: WsCommand[] = [];
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') console.log('browser', message.text()); });
    page.on('websocket', socket => {
      if (new URL(socket.url()).pathname === '/api/v1/ws') socket.on('framesent', ({ payload }) => {
        if (typeof payload === 'string') {
          const command = JSON.parse(payload) as WsCommand;
          if (command.type === 'cmd') commands.push(command);
        }
      });
      if (!socket.url().endsWith('/api/v1/scope')) return;
      socket.on('framereceived', ({ payload }) => {
        if (typeof payload !== 'string') {
          const buffer = Uint8Array.from(payload).buffer;
          const parsed = parseCurrentScopeFrame(buffer, { source: 'radio', sourceId: 'radio', epoch: 0 });
          if (parsed.ok) frames.push({
            view: [parsed.frame.viewStartHz, parsed.frame.viewEndHz],
            pixels: parsed.frame.pixels?.some(value => value > 0) === true,
            receivedAt: performance.now(),
          });
        }
      });
    });
    const writes = async () => {
      let contents = '';
      for await (const chunk of createReadStream(evidence, { encoding: 'utf8' })) contents += chunk;
      return contents.trim().split('\n').filter(Boolean).map(parseScopeWriteReceipt);
    };
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
    // The app's first follow subscription writes CENTER + the covering
    // preset (±50k → the 100 kHz-wide window centred on the VFO).
    await expect.poll(() => frames.at(-1)?.view).toEqual([14_200_000, 14_300_000]);
    // SCROLL-C: the mode button in the More panel owns the mode write.
    await page.getByRole('button', { name: 'More scope controls' }).first().click();
    await page.getByTestId('scope-mode-2').first().click();
    await page.keyboard.press('Escape');
    await expect.poll(async () => (await writes()).filter(receipt => receipt.sub === 0x14).at(-1)?.data[1]).toBe(2);
    // The radio keeps its own window in SCROLL-C (same edges here).
    await expect.poll(() => frames.at(-1)?.view).toEqual([14_200_000, 14_300_000]);
    const area = page.locator('.spectrum-area').first();
    const box = await area.boundingBox(); expect(box).not.toBeNull();
    // The span gesture: SPAN− selects the 50 kHz-wide view (the ±25k
    // preset). The write is span-only — SCROLL-C never takes a mode flip.
    const writesAtGesture = (await writes()).length;
    await page.getByRole('button', { name: 'Decrease scope span', exact: true }).first().click();
    await expect.poll(() => frames.at(-1)?.view).toEqual([14_225_000, 14_275_000]);
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±25k');
    const receipts = (await writes()).slice(writesAtGesture);
    expect(receipts.filter(receipt => receipt.sub === 0x15).at(-1)?.span).toBe(25_000);
    expect(receipts.filter(receipt => receipt.sub === 0x14)).toEqual([]);
    // Settle, then snapshot the axes.
    await page.waitForTimeout(1500);
    const axisBefore = await page.locator('.freq-axis .tick').allInnerTexts();
    expect(axisBefore.length).toBeGreaterThanOrEqual(5);
    expect(axisBefore[0]).toBe('14.225');
    expect(axisBefore.at(-1)).toBe('14.275');
    // Tune INSIDE the unchanged radio window: a small drag left (the panorama drag grabs the spectrum: +5 kHz).
    const mark = frames.length;
    await page.mouse.move(box!.x + box!.width * 0.6, box!.y + box!.height * 0.3);
    await page.mouse.down();
    await page.mouse.move(box!.x + box!.width * 0.5, box!.y + box!.height * 0.3, { steps: 5 });
    await page.mouse.up();
    await expect.poll(async () => (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json())
      .toMatchObject({ main: { freqHz: 14_255_000 } });
    // The reported window never moved: every frame since the gesture
    // still carries the radio's edges.
    await page.waitForTimeout(1500);
    const since = frames.slice(mark);
    expect(since.filter(frame => frame.pixels).length).toBeGreaterThan(5);
    expect(since.every(frame => frame.view[0] === 14_225_000 && frame.view[1] === 14_275_000)).toBe(true);
    // Axes stay put (the old head slid them under the VFO), nothing hatches.
    await expect.poll(() => page.locator('.freq-axis .tick').allInnerTexts()).toEqual(axisBefore);
    await expect(area.locator('.scope-hatch')).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await server.kill();
  }
});
