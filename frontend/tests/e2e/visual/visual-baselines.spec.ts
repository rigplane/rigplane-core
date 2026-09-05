/**
 * MOR-1090 — approved pixel-diff baselines.
 *
 * Representative slice of the MOR-1070/1085 60-capture matrix (not the full
 * matrix — see fixtures/approved-baselines/README.md for why and for the
 * rationale table), plus the MOR-1088 mobile PTT pair. Every cockpit case
 * drives the SAME `fixtures/index.html` entry `capture.mjs` uses, so the
 * fixture ids and URL contract are shared, not re-invented. `runAssertions`
 * (the deterministic manifest/assertion layer) already gates behavior on
 * every push via `capture.mjs`'s own harness; this file is the perceptual
 * pixel layer on top, per the MOR-1087/1282/1088 ruling that raw PNG bytes
 * are not a valid identity check on this host.
 */
import { test, expect } from '@playwright/test';

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 375, height: 812 };
const PTT_SERVER = {
  held: {
    phase: 'active', intent: 'momentary', radioTx: 'on', txRisk: 'confirmed-on',
    fault: null, faultDetail: null, fresh: true, releaseRequired: true,
    remainingMs: 12_000, lastOperation: 'ptt_on',
  },
  rx: {
    phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none',
    fault: null, faultDetail: null, fresh: true, releaseRequired: false,
    remainingMs: null, lastOperation: 'force_receive',
  },
} as const;
/**
 * MOR-2243 — narrower than the peer-split glass's own 1280px native canvas,
 * so `computeStageScale` resolves below 1 (1100/1280 ≈ 0.859; the group's
 * 0.5 floor stays inert). At DESKTOP the same fixture resolves
 * `min(1280/1280, 800/540, MAX_STAGE_SCALE)` = exactly 1 — the identity
 * transform — so a baseline taken only there pins the one configuration in
 * which `ScaledStage`'s scaling path does not run.
 */
const NARROW = { width: 1100, height: 800 };
/** Fixed instant for `freezeClock`; only its constancy matters, not its value. */
const FROZEN_CLOCK = new Date('2026-01-01T14:32:00Z');

interface Spec {
  name: string;
  fixture: string;
  display?: 'centerstage';
  language?: 'studioline' | 'fieldline';
  mode?: 'light';
  viewport?: typeof DESKTOP;
  /**
   * Pin `Date` before the page loads. `PeerSplitLayout.svelte` renders a live
   * wall clock inside the glass (`setInterval(…, 30_000)`, a UTC and a local
   * `HH:MM` span), so without this its digits differ between the run that
   * captured the baseline and every later run — legitimate variation that
   * would spend part of the `maxDiffPixelRatio` budget every run and shrink
   * what remains for catching a real regression. Opt-in per spec: every
   * capture approved before MOR-2243 was taken without it.
   */
  freezeClock?: boolean;
}

/**
 * Topology × {RX, TX, fault} × language, per MOR-1090's acceptance text,
 * plus two additions from the independent verification's adequacy ranking
 * (`verify-mor-1090.md` §4/§10.6): a light-mode capture (light is a separate
 * token resolution from dark — see MOR-1073/1074 — and none of the original
 * 12 exercised it) and a fault × non-default-language capture (fault only
 * ever appeared in the default language before).
 *
 * MOR-2243 appends the two `peer-split-chassis` rows, which sit outside that
 * grid entirely: a different layout (`PeerSplitLayout`, not the cockpit),
 * captured at two frame sizes because at 1280x800 the stage transform
 * resolves to the identity.
 */
const COCKPIT: Spec[] = [
  { name: 'dual-main-sub--desktop', fixture: 'topology-2-main-sub' },
  { name: 'topology-1-single--desktop', fixture: 'topology-1-single' },
  { name: 'topology-2-ab-shared--desktop', fixture: 'topology-2-ab-shared' },
  { name: 'tx-phase-rx--desktop', fixture: 'tx-phase-rx' },
  { name: 'tx-phase-tx--desktop', fixture: 'tx-phase-tx' },
  { name: 'tx-phase-fault--desktop', fixture: 'tx-phase-fault' },
  { name: 'dual-main-sub--desktop--studioline', fixture: 'topology-2-main-sub', language: 'studioline' },
  { name: 'dual-main-sub--desktop--fieldline', fixture: 'topology-2-main-sub', language: 'fieldline' },
  { name: 'tx-phase-tx--desktop--studioline', fixture: 'tx-phase-tx', language: 'studioline' },
  { name: 'dual-main-sub--phone-portrait', fixture: 'topology-2-main-sub', viewport: PHONE },
  {
    name: 'dual-main-sub--desktop--studioline--light',
    fixture: 'topology-2-main-sub',
    language: 'studioline',
    mode: 'light',
  },
  { name: 'tx-phase-fault--desktop--fieldline', fixture: 'tx-phase-fault', language: 'fieldline' },
  { name: 'peer-split-chassis--desktop', fixture: 'peer-split-chassis', freezeClock: true },
  { name: 'centerstage--desktop', fixture: 'peer-split-chassis', display: 'centerstage', freezeClock: true },
  { name: 'centerstage--1100x800', fixture: 'peer-split-chassis', display: 'centerstage', viewport: NARROW, freezeClock: true },
  { name: 'unified-instrument--desktop', fixture: 'lcd-unified-instrument' },
  { name: 'panadapter-first--desktop', fixture: 'lcd-panadapter-first' },
  {
    name: 'peer-split-chassis--1100x800',
    fixture: 'peer-split-chassis',
    viewport: NARROW,
    freezeClock: true,
  },
  { name: 'unified-instrument--1100x800', fixture: 'lcd-unified-instrument', viewport: NARROW },
  { name: 'panadapter-first--1100x800', fixture: 'lcd-panadapter-first', viewport: NARROW },
];

for (const spec of COCKPIT) {
  test(spec.name, async ({ page }) => {
    // Before `goto`: the clock is installed into the page's init scripts, so
    // it has to be in place before any of the page's own script reads `Date`.
    if (spec.freezeClock) await page.clock.setFixedTime(FROZEN_CLOCK);
    await page.setViewportSize(spec.viewport ?? DESKTOP);
    const url = `/fixtures/index.html?fixture=${spec.fixture}&theme=v2`
      + (spec.language ? `&language=${spec.language}` : '')
      + (spec.mode ? `&mode=${spec.mode}` : '')
      + (spec.display ? `&display=${spec.display}` : '');
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForSelector('body[data-harness-ready="true"]');
    await expect(page).toHaveScreenshot(`${spec.name}.png`, { animations: 'disabled', caret: 'hide' });
  });
}

/** The MOR-1088 mobile PTT pair: idle FAB and a real pointer-held FAB. */
test('ptt-idle--mobile', async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto('/fixtures/ptt-harness.html', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  await expect(page).toHaveScreenshot('ptt-idle--mobile.png', { animations: 'disabled' });
});

test('ptt-held--mobile', async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto('/fixtures/ptt-harness.html', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  // A real `page.mouse` press (not a hand-built PointerEvent) so Chromium
  // grants an actual active pointer — `PttFab`'s `setPointerCapture` call
  // throws on a synthetic pointerId with no matching live pointer. Held past
  // the 50ms guard, mirroring capture-ptt.mjs's own gesture technique.
  const box = (await page.locator('.ptt-fab').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await expect.poll(() => page.evaluate(() => window.__ptt.deliveryTrace()))
    .toEqual(['ws.ptt_on']);
  await page.evaluate((snapshot) => window.__ptt.emitServerSnapshot(snapshot), PTT_SERVER.held);
  await page.waitForSelector('.ptt-fab.ptt-fab-held', { timeout: 2_000 });
  await expect(page).toHaveScreenshot('ptt-held--mobile.png', { animations: 'disabled' });
  await page.mouse.up();
  await expect.poll(() => page.evaluate(() => window.__ptt.deliveryTrace()))
    .toEqual(['ws.ptt_on', 'ws.ptt_off']);
  await page.evaluate((snapshot) => window.__ptt.emitServerSnapshot(snapshot), PTT_SERVER.rx);
});

for (const compact of [true, false]) for (const stateText of ['IDLE', 'STALE', '?']) {
  test(`TX lower state clearance -- ${compact ? 'compact' : 'default'} -- ${stateText}`, async ({ page }, testInfo) => {
    await page.goto('/fixtures/index.html?fixture=topology-1-single&theme=v2');
    await page.waitForSelector('body[data-harness-ready="true"]');
    const geometry = await page.evaluate(async ({ compact, stateText }) => {
      const runtimePath = '/@id/svelte';
      const meterPath = '/src/components-v2/meters/LinearSMeter.svelte';
      const { mount, flushSync, unmount } = await import(runtimePath);
      const { default: Meter } = await import(meterPath);
      const target = document.createElement('div');
      target.style.width = '600px';
      document.body.append(target);
      const component = mount(Meter, { target, props: {
        value: 0, label: 'S', compact,
        lowerScale: { label: 'SWR', ticks: [{ value: 0, label: '1' }, { value: 1, label: '∞' }],
          valueFraction: 0, fault: false, relevant: false, stateText },
      } });
      flushSync();
      await document.fonts.ready;
      try {
        const lower = target.querySelector('[data-lower-relevant]')!;
        const state = [...lower.querySelectorAll('text')].find((node) => node.textContent === stateText)!;
        const label = lower.querySelector('[data-lower-row-label]')!;
        const readouts = [...target.querySelectorAll('[data-main-relevant]:last-child text')];
        const box = (node: Element) => {
          const { x, y, width, height, bottom } = node.getBoundingClientRect();
          return { x, y, width, height, bottom };
        };
        const measure = () => {
          const status = box(state);
          const readings = readouts.map(box);
          return { status, readings, lowerTop: box(label).y, bottom: box(target.querySelector('svg')!).bottom,
            overlaps: readings.map((reading) => Math.max(0, Math.min(status.x + status.width, reading.x + reading.width)
              - Math.max(status.x, reading.x)) * Math.max(0, Math.min(status.bottom, reading.bottom) - Math.max(status.y, reading.y))),
          };
        };
        const actual = measure();
        const baseline = state.getAttribute('dominant-baseline');
        state.removeAttribute('dominant-baseline');
        const oldOverlap = measure();
        if (baseline !== null) state.setAttribute('dominant-baseline', baseline);
        return { actual, oldOverlap, restored: measure(), readoutText: readouts.map((node) => node.textContent) };
      } finally { await unmount(component); target.remove(); }
    }, { compact, stateText });
    await testInfo.attach('lower-state-geometry', { body: JSON.stringify(geometry, null, 2), contentType: 'application/json' });
    expect(geometry.readoutText.some((text) => text?.includes('dBm'))).toBe(true);
    expect(geometry.actual.readings).toHaveLength(2);
    expect(geometry.actual.status.width).toBeGreaterThan(0);
    expect(geometry.actual.overlaps).toEqual([0, 0]);
    expect(geometry.actual.status.y).toBeGreaterThanOrEqual(geometry.actual.lowerTop - 0.5);
    expect(geometry.actual.status.bottom).toBeLessThanOrEqual(geometry.actual.bottom);
    expect(Math.max(...geometry.oldOverlap.overlaps)).toBeGreaterThan(0);
    expect(geometry.oldOverlap.readings).toEqual(geometry.actual.readings);
    expect(geometry.restored).toEqual(geometry.actual);
  });
}

const LCD_TX_GEOMETRY = [
  ['peer-split', 'peer-split-chassis&display=peer', 'peer-split-display', 540],
  ['dominant', 'lcd-unified-instrument', 'dominant-unified-display', 540],
  ['centerstage', 'peer-split-chassis&display=centerstage', 'centerstage-display', 540],
  ['panadapter', 'lcd-panadapter-first', 'panadapter-display', 594],
] as const;
for (const viewport of [DESKTOP, NARROW]) for (const [variant, fixture, testId, nativeHeight] of LCD_TX_GEOMETRY) {
  test(`LCD TX geometry -- ${variant} -- ${viewport.width}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto(`/fixtures/index.html?fixture=${fixture}&theme=v2`);
    await page.waitForSelector('body[data-harness-ready="true"]');
    await page.evaluate(() => document.fonts.ready);
    const result = await page.getByTestId(testId).evaluate((display) => {
      const group = display.querySelector<HTMLElement>('[data-testid="lcd-tx-scales"]')!;
      const stage = display.closest<HTMLElement>('.scaled-stage')!;
      const box = (node: Element) => {
        const { x, y, width, height } = node.getBoundingClientRect();
        return { x, y, width, height };
      };
      const overlap = (a: Element, b: Element) => {
        const aa = a.getBoundingClientRect(), bb = b.getBoundingClientRect();
        return Math.max(0, Math.min(aa.right, bb.right) - Math.max(aa.left, bb.left))
          * Math.max(0, Math.min(aa.bottom, bb.bottom) - Math.max(aa.top, bb.top));
      };
      const contained = (child: Element, parent: Element) => {
        const a = child.getBoundingClientRect(), b = parent.getBoundingClientRect();
        return a.left >= b.left - 0.5 && a.right <= b.right + 0.5 && a.top >= b.top - 0.5 && a.bottom <= b.bottom + 0.5;
      };
      const protectedNodes = [...display.querySelectorAll('.frequency,.s-meter,.scope-block,.rf-plot,.af-block')];
      const before = protectedNodes.map(box);
      const cells = [...group.querySelectorAll<HTMLElement>('[data-tx-scale]')];
      const geometry = cells.map((cell) => {
        const label = cell.querySelector('small')!, value = cell.querySelector('.readout')!, track = cell.querySelector('svg')!;
        return { label: label.textContent, box: box(cell), textHeight: box(value).height,
          nativeFontSize: parseFloat(getComputedStyle(value).fontSize),
          contained: [label, value, track].every((node) => contained(node, cell)) && contained(cell, display),
          overlap: overlap(label, value), overflow: cell.scrollWidth > cell.clientWidth,
          segments: cell.querySelectorAll('[data-tx-segment]').length,
          segmentsVisible: [...cell.querySelectorAll('[data-tx-segment]')].every((node) => box(node).width > 0 && box(node).height > 0),
          measured: cell.querySelectorAll('[data-tx-fill]').length,
        };
      });
      const otherText = [...display.querySelectorAll('small')].filter((node) => ['VD', 'ID', 'COMP'].includes(node.textContent ?? '')).map((node) => node.parentElement!);
      const overlaps = otherText.flatMap((node) => cells.map((cell) => overlap(node, cell)));
      group.style.display = 'none';
      const withoutScales = protectedNodes.map(box);
      group.style.removeProperty('display');
      group.style.width = '20px'; group.style.flex = 'none';
      const squeezedOverflow = group.scrollWidth > group.clientWidth;
      group.style.removeProperty('width'); group.style.removeProperty('flex');
      return { group: box(group), geometry, overlaps, squeezedOverflow, before, withoutScales,
        restored: protectedNodes.map(box), stage: { width: stage.offsetWidth, height: stage.offsetHeight },
        railHeight: display.querySelector<HTMLElement>('.aux-rail,.telemetry-only,[data-testid="panadapter-telemetry"]')!.offsetHeight,
        controls: display.querySelectorAll('button,input,select,[tabindex]').length };
    });
    await testInfo.attach('lcd-tx-geometry', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
    expect(result.stage).toEqual({ width: 1280, height: nativeHeight });
    expect(result.railHeight).toBe(variant === 'dominant' ? 35 : variant === 'panadapter' ? 62 : 31);
    expect(result.geometry.map((cell) => cell.label)).toEqual(['PWR', 'SWR', 'ALC']);
    for (const cell of result.geometry) {
      expect(cell.contained).toBe(true); expect(cell.overflow).toBe(false); expect(cell.overlap).toBe(0);
      expect(cell.segments).toBe(20); expect(cell.segmentsVisible).toBe(true); expect(cell.measured).toBe(0);
      expect(cell.nativeFontSize).toBeGreaterThanOrEqual(10); expect(cell.textHeight).toBeGreaterThan(0);
    }
    expect(result.overlaps.every((area) => area === 0)).toBe(true);
    expect(result.before.length).toBeGreaterThan(3);
    expect(result.withoutScales).toEqual(result.before); expect(result.restored).toEqual(result.before);
    expect(result.squeezedOverflow).toBe(true); expect(result.controls).toBe(0);
  });
}
