import { createReadStream } from 'node:fs';
import { rename, writeFile } from 'node:fs/promises';
import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { parseCurrentScopeFrame } from '../../../src/lib/runtime/scope-frame';
import { startJourneyServer, type JourneyServerHandle } from './server-process';
import { parseScopeWriteReceipt, type ScopeWriteReceipt } from './panorama-receipt';
import { expectPanoramaSettled, watchPanoramaErrors } from './panorama-guards';

/**
 * MOR-4532: the panel's follow width is a REQUEST, not a width. While the
 * newest request is not settled by a grant, the display and the SPAN step
 * base come from that request (fast presses accumulate); once a grant
 * settles it, they come from the frame geometry, and the subscription keeps
 * the confirmed width.
 *
 * All rows run on the IC-705 panorama simulator. Window widths are the
 * published span choices (full view widths); a 27 15 receipt carries the
 * radio's half-width preset, so a 20 kHz window is the 10 kHz preset.
 * Every wait is on frames, subscriptions or write receipts.
 */

interface Harness {
  readonly server: JourneyServerHandle;
  readonly evidence: string;
  readonly frames: Array<{ view: number[]; coverage: number[] }>;
  readonly subscribes: Array<{ spanHz?: number; requestSeq?: number; width?: number; socket: number }>;
  sockets(): number;
  writes(): Promise<ScopeWriteReceipt[]>;
  width(): number;
  coverageWidth(): number;
  choices: number[];
}

async function boot(page: Page, info: TestInfo, port: number): Promise<Harness> {
  const evidence = info.outputPath('radio.jsonl');
  const server = await startJourneyServer(port, 14_250_000,
    ['--model', 'IC-705', '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
  const frames: Harness['frames'] = [];
  const subscribes: Harness['subscribes'] = [];
  let sockets = 0;
  page.on('websocket', socket => {
    if (!socket.url().endsWith('/api/v1/scope')) return;
    const index = ++sockets;
    socket.on('framesent', ({ payload }) => {
      if (typeof payload !== 'string') return;
      const message = JSON.parse(payload) as { type?: string; view?: { spanHz?: number }; requestSeq?: number; width?: number };
      if (message.type === 'subscribe' && message.view) {
        subscribes.push({ spanHz: message.view.spanHz, requestSeq: message.requestSeq, width: message.width, socket: index });
      }
    });
    socket.on('framereceived', ({ payload }) => {
      if (typeof payload === 'string') return;
      const parsed = parseCurrentScopeFrame(Uint8Array.from(payload).buffer, { source: 'radio', sourceId: 'radio', epoch: 0 });
      if (parsed.ok) frames.push({ view: [parsed.frame.viewStartHz, parsed.frame.viewEndHz],
        coverage: [parsed.frame.coverageStartHz, parsed.frame.coverageEndHz] });
    });
  });
  const writes = async () => {
    let contents = '';
    for await (const chunk of createReadStream(evidence, { encoding: 'utf8' })) contents += chunk;
    return contents.trim().split('\n').filter(Boolean).map(parseScopeWriteReceipt);
  };
  await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
  await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
  const caps = await (await page.request.get(`http://127.0.0.1:${port}/api/v1/capabilities`)).json() as { span_choices_hz: number[] };
  return {
    server, evidence, frames, subscribes, writes, sockets: () => sockets,
    width: () => { const v = frames.at(-1)?.view; return v ? v[1] - v[0] : 0; },
    coverageWidth: () => { const c = frames.at(-1)?.coverage; return c ? c[1] - c[0] : 0; },
    choices: [...caps.span_choices_hz].sort((a, b) => a - b),
  };
}

/** Frames strictly newer than now, so a snapshot is never the pre-change shape. */
async function settleOnFrames(h: Harness, count: number): Promise<void> {
  const mark = h.frames.length;
  await expect.poll(() => h.frames.length, { timeout: 15_000 }).toBeGreaterThan(mark + count);
}

async function switchToScrollC(page: Page, h: Harness): Promise<void> {
  await page.getByRole('button', { name: 'More scope controls' }).first().click();
  await page.getByTestId('scope-mode-2').first().click();
  await expect(page.getByTestId('scope-mode-2').first()).toHaveAttribute('aria-checked', 'true');
  await page.keyboard.press('Escape');
  await expect.poll(async () => (await h.writes()).filter(receipt => receipt.sub === 0x14).at(-1)?.data[1]).toBe(2);
  await settleOnFrames(h, 10);
}

/**
 * A 200 kHz request answered by the radio, then an EXTERNAL narrowing to a
 * 50 kHz window (front panel: the simulator's one-shot `<evidence>.span`
 * hook, no Core write). Returns the receipt count at the narrowing.
 */
async function wideThenExternalNarrow(page: Page, h: Harness): Promise<number> {
  const wide = 200_000;
  for (let step = 0; step < 6 && h.width() !== wide; step++) {
    const before = h.width();
    await page.keyboard.press(']');
    await expect.poll(() => h.width(), { timeout: 10_000 }).not.toBe(before);
  }
  expect(h.width()).toBe(wide);
  // The radio ANSWERED the wide request (receipt + its window streams)
  // before the front-panel change, so no paced write can land after it.
  await expect.poll(async () => (await h.writes()).filter(r => r.sub === 0x15).at(-1)?.span, { timeout: 30_000 }).toBe(wide / 2);
  await expect.poll(() => h.coverageWidth(), { timeout: 30_000 }).toBe(wide);
  await settleOnFrames(h, 8);
  const receiptsBefore = (await h.writes()).length;
  // Atomic: the simulator's pump must never read a half-written file.
  const hook = h.evidence.replace('.jsonl', '.span');
  await writeFile(`${hook}.tmp`, '25000');
  await rename(`${hook}.tmp`, hook);
  await expect.poll(() => h.coverageWidth(), { timeout: 10_000 }).toBe(50_000);
  return receiptsBefore;
}

/**
 * Rows 1 and 5: the axes and the readout follow the radio's window,
 * nothing hatches, nothing is written back, and the next SPAN− steps down
 * from the radio's window (the 20 kHz window, the 10 kHz preset).
 */
test('IC-705 CENTER: an external narrowing after a wide request is the new width', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_FOLLOW_PORT_1 ?? 22320));
  try {
    const narrow = 50_000;
    const receiptsBefore = await wideThenExternalNarrow(page, h);
    await settleOnFrames(h, 20);
    const area = page.locator('.spectrum-area').first();
    // CENTER labels are real frequencies (MHz, 3 decimals): the edges are `narrow` apart.
    await expect.poll(async () => {
      const axis = await page.locator('.freq-axis .tick').allInnerTexts();
      return Math.round((Number(axis.at(-1)) - Number(axis[0])) * 1e6);
    }).toBe(narrow);
    await expect(page.getByTestId('scope-span-value').first()).toHaveText(`±${narrow / 2000}k`);
    await expect(area.locator('.scope-hatch')).toHaveCount(0);
    // Row 5: nothing was written back without a user action.
    expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15)).toEqual([]);

    const expected = [...h.choices].reverse().find(choice => choice < narrow)!;
    expect(expected).toBe(20_000);
    await page.keyboard.press('[');
    await expect.poll(() => h.width(), { timeout: 10_000 }).toBe(expected);
    const after = (await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15);
    expect(after.map(r => r.span)).toEqual([expected / 2]);
    expect(h.subscribes.at(-1)?.spanHz).toBe(expected);
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

for (const scope of ['CENTER', 'SCROLL-C'] as const) {
  /**
   * Row 2. Two SPAN presses before the first grant are two steps: the first
   * from the observed window, the second from the unanswered request.
   */
  test(`IC-705 ${scope}: two fast SPAN− presses are two steps`, async ({ page }, info) => {
    const watch = watchPanoramaErrors(page);
    const h = await boot(page, info, Number(process.env[`PANORAMA_FOLLOW_PORT_2_${scope}`] ?? (scope === 'CENTER' ? 22321 : 22322)));
    try {
      await expect.poll(() => h.width(), { timeout: 15_000 }).toBe(100_000);
      // CENTER: the observed window is an external 50 kHz narrowing of an
      // answered 200 kHz request; SCROLL-C:
      // the radio's own 100 kHz window. Two presses: 20k/10k, 50k/20k.
      if (scope === 'SCROLL-C') await switchToScrollC(page, h);
      else await wideThenExternalNarrow(page, h);
      const [observed, expected] = scope === 'CENTER' ? [50_000, 10_000] : [100_000, 20_000];
      await settleOnFrames(h, 10);
      expect(h.coverageWidth()).toBe(observed);
      const receiptsBefore = (await h.writes()).length;
      await page.keyboard.press('[');
      await page.keyboard.press('[');
      await expect.poll(() => h.width(), { timeout: 10_000 }).toBe(expected);
      await settleOnFrames(h, 10);
      expect(h.width()).toBe(expected);
      expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).at(-1)?.span).toBe(expected / 2);
      await expect(page.getByTestId('scope-span-value').first()).toHaveText(`±${expected / 2000}k`);
      expectPanoramaSettled(watch);
    } finally {
      await h.server.kill();
    }
  });
}

/** Row 3. Five fast clicks before the first answer still accumulate (SCROLL-C). */
test('IC-705 SCROLL-C: five fast SPAN+ clicks accumulate', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_FOLLOW_PORT_3 ?? 22323));
  try {
    await expect.poll(() => h.width(), { timeout: 15_000 }).toBe(100_000);
    await switchToScrollC(page, h);
    // Down to the narrowest window first (four presses), settled on frames.
    for (let i = 0; i < 4; i++) await page.keyboard.press('[');
    await expect.poll(() => h.width(), { timeout: 10_000 }).toBe(5_000);
    await settleOnFrames(h, 10);
    const receiptsBefore = (await h.writes()).length;
    const increase = page.getByRole('button', { name: 'Increase scope span', exact: true }).first();
    for (let i = 0; i < 5; i++) await increase.click();
    await expect.poll(() => h.width(), { timeout: 10_000 }).toBe(200_000);
    await settleOnFrames(h, 10);
    expect(h.width()).toBe(200_000);
    expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).at(-1)?.span).toBe(100_000);
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

for (const change of ['resize', 'reload'] as const) {
  /**
   * Adviser decision (b): the follow width in a subscription changes only on
   * a user action, and the server writes a span only for a NEW requestSeq. A
   * resize re-sends the same request; a reload is a fresh connection whose
   * subscription is seq 0. Neither writes the radio's externally set span
   * back, and the display follows the radio.
   */
  test(`IC-705 CENTER: a ${change} after an external narrowing writes nothing and shows the radio's window`, async ({ page }, info) => {
    const watch = watchPanoramaErrors(page);
    const h = await boot(page, info, Number(process.env[`PANORAMA_FOLLOW_PORT_${change}`] ?? (change === 'resize' ? 22325 : 22326)));
    try {
      const receiptsBefore = await wideThenExternalNarrow(page, h);
      await settleOnFrames(h, 10);
      const sentBefore = h.subscribes.length;
      const socketBefore = h.sockets();
      if (change === 'resize') await page.setViewportSize({ width: 1200, height: 1200 });
      else await page.reload();
      // A resize re-sends on the same socket; a reload subscribes on a new one.
      const after = () => h.subscribes.slice(sentBefore).filter(message => change === 'resize' || message.socket > socketBefore);
      await expect.poll(() => after().length, { timeout: 15_000 }).toBeGreaterThan(0);
      if (change === 'reload') expect(after().every(message => message.requestSeq === 0)).toBe(true);
      await settleOnFrames(h, 30);
      expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15)).toEqual([]);
      expect(h.coverageWidth()).toBe(50_000);
      await expect.poll(async () => {
        const axis = await page.locator('.freq-axis .tick').allInnerTexts();
        return Math.round((Number(axis.at(-1)) - Number(axis[0])) * 1e6);
      }).toBe(50_000);
      await expect(page.getByTestId('scope-span-value').first()).toHaveText('±25k');
      expectPanoramaSettled(watch);
    } finally {
      await h.server.kill();
    }
  });
}

/**
 * Adviser decision (b), the write-back risk: the radio streams its OLD window
 * for a moment after it ACKed a span write (the simulator's one-shot
 * `<evidence>.stale` hook). The display may show the stale frames, but the
 * subscription keeps the request and nothing is written back.
 */
test('IC-705 CENTER: stale old-width frames right after the span ACK change no subscription and write nothing', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_FOLLOW_PORT_STALE ?? 22327));
  try {
    await expect.poll(() => h.width(), { timeout: 15_000 }).toBe(100_000);
    await settleOnFrames(h, 10);
    const hook = h.evidence.replace('.jsonl', '.stale');
    await writeFile(`${hook}.tmp`, '0.4');
    await rename(`${hook}.tmp`, hook);
    const receiptsBefore = (await h.writes()).length;
    const sentBefore = h.subscribes.length;
    await page.keyboard.press(']');
    await expect.poll(() => h.width(), { timeout: 10_000 }).toBe(200_000);
    await settleOnFrames(h, 30);
    expect(h.width()).toBe(200_000);
    expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).map(r => r.span)).toEqual([100_000]);
    const sent = h.subscribes.slice(sentBefore);
    expect(sent.length).toBeGreaterThan(0);
    expect(sent.every(message => message.spanHz === 200_000 && message.requestSeq === sent[0]!.requestSeq)).toBe(true);
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±100k');
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

/**
 * Pre-review P1 #2: ZOOM after an external narrowing of a settled request
 * zooms from the shown window, as SPAN does: ZOOM+ from the displayed 50 kHz
 * asks for 25 kHz, and ZOOM− expands from the displayed width.
 */
test('IC-705 CENTER: ZOOM after an external narrowing zooms from the shown window', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_FOLLOW_PORT_ZOOM ?? 22328));
  try {
    await wideThenExternalNarrow(page, h);
    await settleOnFrames(h, 20);
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±25k');
    const sentBefore = h.subscribes.length;
    await page.getByRole('button', { name: 'More scope controls' }).first().click();
    await page.getByTestId('scope-zoom-in').first().click();
    await expect.poll(() => h.subscribes.slice(sentBefore).at(-1)?.spanHz, { timeout: 10_000 }).toBe(25_000);
    await expect.poll(() => h.width(), { timeout: 10_000 }).toBe(25_000);
    await settleOnFrames(h, 20);
    const zoomOutFrom = h.subscribes.length;
    await page.getByTestId('scope-zoom-out').first().click();
    await expect.poll(() => h.subscribes.slice(zoomOutFrom).at(-1)?.spanHz, { timeout: 10_000 }).toBe(50_000);
    await expect.poll(() => h.width(), { timeout: 10_000 }).toBe(50_000);
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

/**
 * Pre-review P1 #1: a presentation switch remounts the panel while the App's
 * swap bridge keeps the scope socket. The next SPAN press must carry a
 * requestSeq above the last one on that socket, or the server ignores it.
 */
test('IC-705: after a presentation switch on the same socket the next SPAN press changes the radio span', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_FOLLOW_PORT_SWITCH ?? 22329));
  try {
    await expect.poll(() => h.width(), { timeout: 15_000 }).toBe(100_000);
    for (const expected of [50_000, 20_000]) {
      await page.keyboard.press('[');
      await expect.poll(() => h.coverageWidth(), { timeout: 10_000 }).toBe(expected);
      await settleOnFrames(h, 10);
    }
    const highest = Math.max(...h.subscribes.map(message => message.requestSeq ?? 0));
    expect(highest).toBeGreaterThan(1);
    const sockets = h.sockets();
    // Narrow viewport: the mobile presentation; back: the desktop one.
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.spectrum-panel').first()).toBeVisible();
    await page.setViewportSize({ width: 1728, height: 1200 });
    await expect(page.getByTestId('scope-span-value').first()).toBeVisible();
    await settleOnFrames(h, 30);
    expect(h.sockets()).toBe(sockets);
    // The next SPAN+ steps from the radio's window as it streams now.
    const observed = h.coverageWidth();
    const next = h.choices.find(choice => choice > observed)!;
    const receiptsBefore = (await h.writes()).length;
    const sentBefore = h.subscribes.length;
    await page.keyboard.press(']');
    await expect.poll(() => h.coverageWidth(), { timeout: 10_000 }).toBe(next);
    expect(Math.max(...h.subscribes.slice(sentBefore).map(message => message.requestSeq ?? 0))).toBeGreaterThan(highest);
    expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).map(r => r.span)).toEqual([next / 2]);
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

/** A user-chosen 20 kHz window (two SPAN− presses from the radio's 100 kHz), answered by the radio. */
async function chooseNarrowSpan(page: Page, h: Harness): Promise<void> {
  await expect.poll(() => h.width(), { timeout: 15_000 }).toBe(100_000);
  for (const expected of [50_000, 20_000]) {
    await page.keyboard.press('[');
    await expect.poll(() => h.coverageWidth(), { timeout: 10_000 }).toBe(expected);
    await settleOnFrames(h, 10);
  }
  await expect.poll(async () => (await h.writes()).filter(r => r.sub === 0x15).at(-1)?.span).toBe(10_000);
}

/** Desktop → mobile → desktop on the same scope socket (the App's swap bridge keeps it). */
async function swapPresentations(page: Page, h: Harness): Promise<void> {
  const sockets = h.sockets();
  const sentBefore = h.subscribes.length;
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.spectrum-panel').first()).toBeVisible();
  await settleOnFrames(h, 20);
  await page.setViewportSize({ width: 1728, height: 1200 });
  await expect(page.getByTestId('scope-span-value').first()).toBeVisible();
  await settleOnFrames(h, 30);
  expect(h.sockets()).toBe(sockets);
  expect(h.subscribes.length).toBeGreaterThan(sentBefore);
}

/**
 * MOR-4534 row 1: a presentation switch keeps the scope socket, and the
 * incoming panel subscribes before the outgoing one is released, so the hub
 * never sees zero subscribers: neither restore-on-close nor a re-assert
 * writes a span during the swap, and the panel shows the chosen window from
 * the grant.
 */
test('IC-705: a presentation switch after a user-chosen span writes no span and keeps the window', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_SWAP_PORT_1 ?? 22340));
  try {
    await chooseNarrowSpan(page, h);
    const receiptsBefore = (await h.writes()).length;
    await swapPresentations(page, h);
    expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15)).toEqual([]);
    expect(h.coverageWidth()).toBe(20_000);
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±10k');
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

/**
 * MOR-4534 row 2: after the swap the first SPAN− steps from the chosen
 * window and IS written, with a requestSeq above every one sent before.
 */
test('IC-705: the first SPAN press after a presentation switch is written from the chosen window', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_SWAP_PORT_2 ?? 22341));
  try {
    await chooseNarrowSpan(page, h);
    const highest = Math.max(...h.subscribes.map(message => message.requestSeq ?? 0));
    await swapPresentations(page, h);
    const receiptsBefore = (await h.writes()).length;
    const sentBefore = h.subscribes.length;
    await page.keyboard.press('[');
    await expect.poll(async () => (await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).map(r => r.span),
      { timeout: 10_000 }).toEqual([5_000]);
    await expect.poll(() => h.coverageWidth(), { timeout: 10_000 }).toBe(10_000);
    expect(Math.max(...h.subscribes.slice(sentBefore).map(message => message.requestSeq ?? 0))).toBeGreaterThan(highest);
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

/**
 * MOR-4534 row 3 (must-not): a real close — the last panel goes with its
 * socket — still restores the pre-session span at once.
 */
test('IC-705: closing the page after a user-chosen span restores the radio span at once', async ({ page }, info) => {
  const h = await boot(page, info, Number(process.env.PANORAMA_SWAP_PORT_3 ?? 22342));
  try {
    await chooseNarrowSpan(page, h);
    const receiptsBefore = (await h.writes()).length;
    await page.close();
    await expect.poll(async () => (await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).map(r => r.span),
      { timeout: 5_000 }).toEqual([50_000]);
  } finally {
    await h.server.kill();
  }
});

/** Presses SPAN until the radio streams `target`, then waits for its 27 15 receipt. */
async function chooseSpan(page: Page, h: Harness, target: number): Promise<void> {
  await expect.poll(() => h.width(), { timeout: 15_000 }).toBe(100_000);
  for (let step = 0; step < 6 && h.coverageWidth() !== target; step++) {
    const before = h.coverageWidth();
    await page.keyboard.press(target > before ? ']' : '[');
    await expect.poll(() => h.coverageWidth(), { timeout: 10_000 }).not.toBe(before);
    await settleOnFrames(h, 8);
  }
  expect(h.coverageWidth()).toBe(target);
  await expect.poll(async () => (await h.writes()).filter(r => r.sub === 0x15).at(-1)?.span).toBe(target / 2);
}

async function expectFollowWindow(page: Page, width: number): Promise<void> {
  await expect.poll(async () => {
    const axis = await page.locator('.freq-axis .tick').allInnerTexts();
    return [axis[0], axis.at(-1)];
  }).toEqual([`-${width / 2000}k`, `+${width / 2000}k`]);
  await expect(page.getByTestId('scope-span-value').first()).toHaveText(`±${width / 2000}k`);
}

for (const [chosen, key, next] of [[200_000, ']', 500_000], [500_000, '[', 200_000]] as const) {
  /**
   * MOR-4534 pre-review P1 #2: the incoming panel carries the outgoing
   * request (its width and requestSeq) instead of the 100 kHz default, so a
   * span above 100 kHz is neither cropped nor written; axes, readout and the
   * next step all come from the chosen window.
   */
  test(`IC-705: a presentation switch keeps a chosen ${chosen / 1000} kHz window and steps from it`, async ({ page }, info) => {
    const watch = watchPanoramaErrors(page);
    const h = await boot(page, info, Number(process.env[`PANORAMA_SWAP_PORT_${chosen}`] ?? (chosen === 200_000 ? 22343 : 22344)));
    try {
      await chooseSpan(page, h, chosen);
      const receiptsBefore = (await h.writes()).length;
      await swapPresentations(page, h);
      expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15)).toEqual([]);
      expect(h.coverageWidth()).toBe(chosen);
      expect(h.width()).toBe(chosen);
      await expectFollowWindow(page, chosen);
      await page.keyboard.press(key);
      await expect.poll(async () => (await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).map(r => r.span),
        { timeout: 10_000 }).toEqual([next / 2]);
      await expect.poll(() => h.coverageWidth(), { timeout: 10_000 }).toBe(next);
      expectPanoramaSettled(watch);
    } finally {
      await h.server.kill();
    }
  });
}

/**
 * MOR-4534 pre-review P1 #1: rotating the phone replaces the portrait panel
 * with the landscape one inside the mobile presentation (no presentation
 * switch). The same surface hold covers it: zero span writes, the chosen
 * window is kept, and the next SPAN steps from it.
 */
test('IC-705: rotating the phone after a chosen 200 kHz window writes no span and keeps the window', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const h = await boot(page, info, Number(process.env.PANORAMA_SWAP_PORT_ROTATE ?? 22345));
  try {
    await chooseSpan(page, h, 200_000);
    const sockets = h.sockets();
    const receiptsBefore = (await h.writes()).length;
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.spectrum-panel').first()).toBeVisible();
    await settleOnFrames(h, 20);
    await page.setViewportSize({ width: 844, height: 390 });
    await expect(page.locator('.m-landscape .spectrum-panel').first()).toBeVisible();
    await settleOnFrames(h, 20);
    // Landscape entry requests fullscreen (MobileRadioLayout); Chromium resizes
    // only a normal window, as in mobile-ptt-hold.spec.ts. Same page, same socket.
    await page.evaluate(async () => {
      if (document.fullscreenElement) await document.exitFullscreen();
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.m-landscape')).toHaveCount(0);
    await settleOnFrames(h, 20);
    await page.setViewportSize({ width: 1728, height: 1200 });
    await expect(page.getByTestId('scope-span-value').first()).toBeVisible();
    await settleOnFrames(h, 30);
    expect(h.sockets()).toBe(sockets);
    expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15)).toEqual([]);
    expect(h.coverageWidth()).toBe(200_000);
    await expectFollowWindow(page, 200_000);
    await page.keyboard.press(']');
    await expect.poll(async () => (await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15).map(r => r.span),
      { timeout: 10_000 }).toEqual([250_000]);
    expectPanoramaSettled(watch);
  } finally {
    await h.server.kill();
  }
});

/**
 * MOR-4534 pre-review P2 #5: the dual-sdr-face presentation mounts passive
 * radio follow surfaces (ReceiverScopeWaterfall: no user span request). Switching to it
 * after a chosen 20 kHz window writes no span: an automatic follow
 * subscription carries requestSeq 0 through the shared surface mechanism.
 */
test('IC-705: switching to the dual-sdr-face presentation after a chosen span writes no span', async ({ page }, info) => {
  const h = await boot(page, info, Number(process.env.PANORAMA_SWAP_PORT_PANADAPTER ?? 22346));
  try {
    await chooseSpan(page, h, 20_000);
    const receiptsBefore = (await h.writes()).length;
    const sentBefore = h.subscribes.length;
    await page.getByLabel('Select UI skin').first().selectOption('dual-sdr-face');
    await expect(page.locator('canvas[aria-label="Receiver spectrum"]').first()).toBeAttached();
    await expect.poll(() => h.subscribes.length, { timeout: 15_000 }).toBeGreaterThan(sentBefore);
    await settleOnFrames(h, 30);
    expect(h.subscribes.slice(sentBefore).every(message => message.requestSeq !== undefined)).toBe(true);
    expect((await h.writes()).slice(receiptsBefore).filter(r => r.sub === 0x15)).toEqual([]);
    expect(h.coverageWidth()).toBe(20_000);
  } finally {
    await h.server.kill();
  }
});
