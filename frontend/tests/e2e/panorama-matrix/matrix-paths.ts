// MOR-4388 — the interaction paths of the acceptance matrix.
//
// Every function performs one path's UI actions against a prepared page,
// waits for the scope to settle, and returns what the invariants should
// compare against (expected scope writes, expected set_freq semantics,
// per-invariant N/A reasons). Controls that do not exist for a source kind
// (FIX/HOLD on the audio-FFT scope, presets on radios without span
// presets) turn the cell into an honest 'na' instead of a failure.

import { expect, type Browser, type Page } from '@playwright/test';
import { readFile, stat, writeFile } from 'node:fs/promises';
import type {
  ExternalStepReport, InvariantId, NakExpectation,
} from './matrix-contract';
import { parseNakReceipt, parseReceiptLines, parseScopeWriteReceipt } from './matrix-contract';
import type { PageObservations } from './matrix-instrument';

export type WriteExpectation =
  | { kind: 'none' }
  | { kind: 'contains'; subs: readonly number[]; forbidden: readonly number[] };

export type SetFreqExpectation =
  | { kind: 'no-change' }
  | { kind: 'strict'; values: readonly number[]; toleranceHz: number; note: string }
  | { kind: 'consistency'; note: string };

export interface PathReport {
  readonly summary: string;
  readonly na: Partial<Record<InvariantId, string>>;
  readonly writes: WriteExpectation;
  readonly setFreq: SetFreqExpectation;
  readonly clickTargetHz: number | null;
  /** The simulator's injected-bump fraction of the radio window (0.625 centre, 0.40 fixed edges); default 0.625. */
  readonly bumpFraction?: number;
  /** Invariant 5 demand; default { kind: 'reads-tolerated' } (only the ack-tracker cell overrides). */
  readonly naks?: NakExpectation;
  /** Command errors (failed WS responses, server errors) seen during the path; default []. */
  readonly commandErrors?: string[];
  /** External-change step observations (only the external-change path sets it). */
  readonly external?: ExternalStepReport[];
}

export interface PathContext {
  readonly page: Page;
  readonly browser: Browser;
  readonly port: number;
  readonly radioKind: string;
  readonly source: 'hardware' | 'audio_fft' | 'iq_fft';
  readonly spanHz: number | null;
  readonly observations: PageObservations;
  readonly evidencePath: string;
  readonly restartServer: () => Promise<void>;
  readonly currentSpanHz: () => number | null;
}

// ── shared helpers ─────────────────────────────────────────────────────────

async function stateFreq(page: Page, port: number): Promise<number> {
  const response = await page.request.get(`http://127.0.0.1:${port}/api/v1/state`);
  const state = await response.json();
  return state.main.freqHz as number;
}

async function vfoDisplayHz(page: Page): Promise<number | null> {
  const text = await page.locator('.receiver-instrument [data-vfo-freq]').first().innerText();
  const digits = Number(text.replace(/\D/g, ''));
  return Number.isFinite(digits) && digits > 0 ? digits : null;
}

async function waitForFrames(observations: PageObservations, count: number, timeoutMs = 20_000): Promise<void> {
  await expect.poll(() => observations.frames.length, { timeout: timeoutMs }).toBeGreaterThan(count);
}

async function waitForFreqChanged(page: Page, port: number, fromHz: number, timeoutMs = 15_000): Promise<number> {
  let current = fromHz;
  await expect.poll(async () => {
    current = await stateFreq(page, port);
    return current !== fromHz;
  }, { timeout: timeoutMs }).toBe(true);
  return current;
}

/** Read the tuning step (Hz) from the STEP readout; null when unparseable. */
async function readStepHz(page: Page): Promise<number | null> {
  const text = await page.getByTestId('toolbar-row-step').first().innerText().catch(() => '');
  const match = text.match(/(\d+(?:\.\d+)?)\s*(k|M|G)?/i);
  if (!match) return null;
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value <= 0) return null;
  const scale = match[2]?.toLowerCase() === 'm' ? 1e6 : match[2]?.toLowerCase() === 'g' ? 1e9 : match[2] ? 1e3 : 1;
  return Math.round(value * scale);
}

async function spectrumArea(page: Page): Promise<{ x: number; y: number; width: number; height: number }> {
  const box = await page.locator('.spectrum-area').first().boundingBox();
  expect(box, 'the spectrum area must be rendered').not.toBeNull();
  return { x: box!.x, y: box!.y, width: box!.width, height: box!.height };
}

async function clickFraction(page: Page, area: 'spectrum' | 'waterfall', fraction: number): Promise<number> {
  const box = area === 'spectrum'
    ? await page.locator('.spectrum-area').first().boundingBox()
    : await page.locator('.waterfall-content').first().boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width * fraction, box!.y + box!.height * 0.4);
  return fraction;
}

/** Fraction of the CURRENT drawn axis at a page x position (for expectations). */
async function axisFractionForX(page: Page, area: 'spectrum' | 'waterfall', fraction: number): Promise<number | null> {
  const axis = await page.evaluate(() => {
    const history = window.__matrix?.axisHistory ?? [];
    for (let index = history.length - 1; index >= 0; index--) {
      const labels = history[index].labels.map(Number).filter(Number.isFinite);
      if (labels.length >= 2) return { start: Math.min(...labels), end: Math.max(...labels) };
    }
    return null;
  });
  if (!axis) return null;
  // The drawn plot area is inset from the canvas box by the axis margin;
  // the marker check later interpolates on the labels themselves, so the
  // click target only needs the axis fraction — approximated by the raw
  // box fraction, which the panel's own click-to-tune also uses.
  void area;
  return axis.start + fraction * (axis.end - axis.start);
}

async function settle(page: Page, ms = 2500): Promise<void> {
  await page.waitForTimeout(ms);
}

const NO_WRITES: WriteExpectation = { kind: 'none' };
// Tunes never touch the radio scope on rc8: WebRadioScopeWriter.set_center
// returns without writing when receiver/mode/span are unchanged
// (src/rigplane/web/scope_sources.py), and a tune changes none of them —
// the view follows the VFO frontend-side. Any radio write during a tune
// path is therefore stray, on every source kind.

// ── paths ──────────────────────────────────────────────────────────────────

async function pathClickTune(ctx: PathContext, area: 'spectrum' | 'waterfall', fraction: number): Promise<PathReport> {
  const axisMHz = await axisFractionForX(ctx.page, area, fraction);
  const fromHz = await stateFreq(ctx.page, ctx.port);
  await clickFraction(ctx.page, area, fraction);
  const toHz = await waitForFreqChanged(ctx.page, ctx.port, fromHz);
  await settle(ctx.page);
  const step = await readStepHz(ctx.page);
  const targetHz = axisMHz === null ? null : Math.round(axisMHz * 1e6);
  const note = `clicked ${area} at fraction ${fraction}; axis target ${targetHz ?? '?'} Hz, step ${step ?? '?'} Hz`;
  if (targetHz !== null) {
    return {
      summary: note, na: {}, writes: NO_WRITES,
      setFreq: { kind: 'strict', values: [toHz], toleranceHz: Math.max(step ?? 1000, 1000), note },
      clickTargetHz: targetHz,
    };
  }
  return {
    summary: `${note}; axis unavailable, consistency only`, na: {}, writes: NO_WRITES,
    setFreq: { kind: 'consistency', note }, clickTargetHz: null,
  };
}

async function pathDragPan(ctx: PathContext): Promise<PathReport> {
  const box = await spectrumArea(ctx.page);
  const fromHz = await stateFreq(ctx.page, ctx.port);
  await ctx.page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.3);
  await ctx.page.mouse.down();
  await ctx.page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.3, { steps: 5 });
  await ctx.page.mouse.up();
  await settle(ctx.page, 3500);
  const toHz = await stateFreq(ctx.page, ctx.port);
  const tuned = toHz !== fromHz;
  const note = `drag 0.60→0.45 of the spectrum width; frequency ${fromHz}→${toHz} (${tuned ? 'tuned with the drag (rc8 centre-mode semantics)' : 'not tuned'})`;
  return {
    summary: note,
    na: {},
    writes: NO_WRITES,
    setFreq: tuned
      ? { kind: 'consistency', note: `${note}; direction semantics is the design's call (MOR-4387), consistency checked only` }
      : { kind: 'no-change' },
    clickTargetHz: null,
  };
}

async function tuneStepsAfterClick(ctx: PathContext, method: 'arrows' | 'wheel'): Promise<PathReport> {
  const fromHz = await stateFreq(ctx.page, ctx.port);
  await clickFraction(ctx.page, 'spectrum', 0.68);
  const clickedHz = await waitForFreqChanged(ctx.page, ctx.port, fromHz);
  await settle(ctx.page);
  const step = await readStepHz(ctx.page);
  const presses = 3;
  if (method === 'arrows') {
    for (let index = 0; index < presses; index++) await ctx.page.keyboard.press('ArrowRight');
  } else {
    const box = await spectrumArea(ctx.page);
    await ctx.page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.3);
    for (let index = 0; index < presses; index++) await ctx.page.mouse.wheel(0, -120);
  }
  await settle(ctx.page);
  const finalHz = await stateFreq(ctx.page, ctx.port);
  if (step === null) {
    return {
      summary: `${method} tuning after a click-tune; step readout unparseable, uniform-delta consistency checked`,
      na: {}, writes: NO_WRITES,
      setFreq: { kind: 'consistency', note: `click ${clickedHz}, final ${finalHz}` },
      clickTargetHz: null,
    };
  }
  const expected = clickedHz + presses * step;
  return {
    summary: `${method} tuning ×${presses} after a click-tune with step ${step} Hz`,
    na: {},
    writes: NO_WRITES,
    setFreq: { kind: 'strict', values: [expected], toleranceHz: Math.max(1, Math.round(step / 100)), note: `click ${clickedHz} + ${presses}×${step}` },
    clickTargetHz: null,
  };
}

async function pathDigitWheel(ctx: PathContext): Promise<PathReport> {
  const digits = ctx.page.locator('[data-vfo-freq] .digit');
  const count = await digits.count();
  if (count < 5) {
    return {
      summary: 'no interactive frequency digits rendered', na: { 6: 'no digit readout to wheel' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  const fromHz = await stateFreq(ctx.page, ctx.port);
  // Rightmost digit is index count-1 (1 Hz place); four places left is 10 kHz.
  const target = digits.nth(count - 5);
  await target.scrollIntoViewIfNeeded();
  await target.hover();
  await ctx.page.mouse.wheel(0, 120);
  await settle(ctx.page);
  const toHz = await waitForFreqChanged(ctx.page, ctx.port, fromHz, 10_000).catch(() => fromHz);
  const place = 10_000;
  return {
    summary: `wheel-down on the 10 kHz digit (${fromHz} → ${toHz})`,
    na: {}, writes: NO_WRITES,
    setFreq: toHz === fromHz
      ? { kind: 'consistency', note: 'digit wheel produced no frequency change' }
      : { kind: 'strict', values: [fromHz - place], toleranceHz: 1, note: `one notch down on the 10 kHz digit of ${fromHz}` },
    clickTargetHz: null,
  };
}

async function pathSpanSteps(ctx: PathContext): Promise<PathReport> {
  const isAudio = ctx.source !== 'hardware';
  if (isAudio) {
    return {
      summary: 'SPAN steps on a non-hardware scope source',
      na: { 3: 'no radio span presets on this source', 4: 'no radio scope writes possible' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  const decrease = ctx.page.getByRole('button', { name: 'Decrease scope span', exact: true }).first();
  const increase = ctx.page.getByRole('button', { name: 'Increase scope span', exact: true }).first();
  await expect(decrease, 'the SPAN stepper must be rendered').toBeVisible();
  // Walk every preset downward, then back up past the start: one grant per
  // step, every step a 0x15 radio write, nothing else written.
  for (let index = 0; index < 10; index++) {
    if (await decrease.isDisabled()) break;
    await decrease.click();
    await ctx.page.waitForTimeout(700);
  }
  for (let index = 0; index < 6; index++) {
    if (await increase.isDisabled()) break;
    await increase.click();
    await ctx.page.waitForTimeout(700);
  }
  await settle(ctx.page);
  return {
    summary: 'stepped SPAN through every preset downward and part-way back up',
    na: {},
    writes: { kind: 'contains', subs: [0x15], forbidden: [0x1e] },
    setFreq: { kind: 'no-change' },
    clickTargetHz: null,
  };
}

async function pathCtrFix(ctx: PathContext): Promise<PathReport> {
  if (ctx.source !== 'hardware') {
    return {
      summary: 'CTR/FIX on a non-hardware scope source',
      na: { 3: 'no radio scope mode on this source', 4: 'no radio scope writes possible' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  const fix = ctx.page.getByRole('button', { name: 'FIX', exact: true }).first();
  if (!(await fix.count())) {
    return {
      summary: 'no FIX key rendered', na: { 3: 'FIX key absent' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  await fix.click();
  await settle(ctx.page, 3500);
  return {
    summary: 'switched the scope mode to FIX (fixed edges 14.2–14.3 MHz)',
    na: {},
    bumpFraction: 0.40,
    writes: { kind: 'contains', subs: [0x14], forbidden: [] },
    setFreq: { kind: 'no-change' },
    clickTargetHz: null,
  };
}

async function pathBandPresets(ctx: PathContext): Promise<PathReport> {
  if (ctx.source !== 'hardware') {
    return {
      summary: 'band presets on a non-hardware scope source',
      na: { 3: 'no band presets on this source' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  const select = ctx.page.getByTestId('scope-preset-select').first();
  if (!(await select.count())) {
    return {
      summary: 'no scope preset select rendered', na: { 3: 'preset select absent' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  const options = await select.locator('option').evaluateAll(elements =>
    elements.map(option => ({ value: (option as HTMLOptionElement).value, label: option.textContent ?? '' })));
  const band = options.find(option => option.value !== '' && /\d/.test(option.label));
  if (!band) {
    return {
      summary: 'preset select has no band options', na: { 3: 'no band options' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  await select.selectOption({ label: band.label });
  await settle(ctx.page, 3500);
  return {
    summary: `applied the band preset "${band.label}"`,
    na: {},
    writes: { kind: 'contains', subs: [], forbidden: [] },
    setFreq: { kind: 'no-change' },
    clickTargetHz: null,
  };
}

async function pathZoom(ctx: PathContext): Promise<PathReport> {
  const more = ctx.page.getByRole('button', { name: 'More scope controls' }).first();
  if (await more.count()) {
    await more.click();
  }
  const zoomIn = ctx.page.getByTestId('scope-zoom-in').first();
  await expect(zoomIn, 'the zoom key must be rendered').toBeVisible();
  const initialView = ctx.observations.grants.at(-1)?.view;
  const initialWidth = initialView ? initialView.endHz - initialView.startHz : null;
  await zoomIn.click();
  await ctx.page.waitForTimeout(1200);
  await zoomIn.click();
  await ctx.page.waitForTimeout(1200);
  // Non-preset width: zoom on top of a band preset when one exists.
  let nonPresetNote = 'zoom-in ×2 from the current view (widths stay preset-aligned when the start width is a preset)';
  const select = ctx.page.getByTestId('scope-preset-select').first();
  if (ctx.source === 'hardware' && (await select.count())) {
    const options = await select.locator('option').evaluateAll(elements =>
      elements.map(option => ({ value: (option as HTMLOptionElement).value, label: option.textContent ?? '' })));
    const band = options.find(option => option.value !== '' && /\d/.test(option.label));
    if (band) {
      await select.selectOption({ label: band.label });
      await ctx.page.waitForTimeout(1500);
      await zoomIn.click();
      await ctx.page.waitForTimeout(1200);
      nonPresetNote = `plus band preset "${band.label}" then one zoom-in (a non-preset width on rc8 semantics)`;
    }
  }
  await settle(ctx.page);
  await ctx.page.keyboard.press('Escape').catch(() => {});
  return {
    summary: `zoom-in ×2${nonPresetNote}; initial grant width ${initialWidth ?? '?'} Hz`,
    na: {}, writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
  };
}

async function pathBandChange(ctx: PathContext): Promise<PathReport> {
  const fromHz = await stateFreq(ctx.page, ctx.port);
  for (const name of ['40m', '20m', '80m', '15m']) {
    const button = ctx.page.getByRole('button', { name, exact: true }).first();
    if (await button.count()) {
      await button.click();
      const toHz = await waitForFreqChanged(ctx.page, ctx.port, fromHz, 10_000).catch(() => fromHz);
      await settle(ctx.page, 3500);
      return {
        summary: `band button ${name}: ${fromHz} → ${toHz}`,
        na: {},
        writes: NO_WRITES,
        setFreq: toHz === fromHz
          ? { kind: 'consistency', note: `band button ${name} did not change the frequency` }
          : { kind: 'consistency', note: `band change tunes into the band; exact register value is the band data's call` },
        clickTargetHz: null,
      };
    }
  }
  return {
    summary: 'no band buttons rendered', na: { 1: 'no band buttons to change the band with', 6: 'no band buttons' },
    writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
  };
}

async function pathModeChange(ctx: PathContext): Promise<PathReport> {
  const fromHz = await stateFreq(ctx.page, ctx.port);
  const candidates = ['LSB', 'FM', 'AM', 'USB'];
  for (const name of candidates) {
    // Anchor at the start to tolerate extra accessible-name content; a
    // superset of the old exact match.
    const button = ctx.page.getByRole('button', { name: new RegExp(`^${name}(\\s|$)`) }).first();
    if (await button.count()) {
      await button.click();
      await settle(ctx.page, 3500);
      const toHz = await stateFreq(ctx.page, ctx.port);
      return {
        summary: `mode button ${name}; frequency ${fromHz} → ${toHz} (must not tune)`,
        na: {}, writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
      };
    }
  }
  return {
    summary: 'no mode buttons rendered', na: { 6: 'no mode buttons found' },
    writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
  };
}

async function pathReconnect(ctx: PathContext): Promise<PathReport> {
  await waitForFrames(ctx.observations, 5);
  const beforeRestart = ctx.observations.frames.length;
  await ctx.restartServer();
  // The page must re-grant on its own and frames must resume past the
  // pre-restart count.
  await expect.poll(() => ctx.observations.frames.length, { timeout: 45_000 })
    .toBeGreaterThan(beforeRestart + 5);
  await settle(ctx.page);
  return {
    summary: 'killed and restarted the simulator server on the same port; scope re-granted and frames resumed',
    na: {},
    // The evidence file restarts empty with the server, so the checked
    // window holds only post-restart writes: re-granting legitimately
    // re-programs mode/span; fixed-edge writes would be stray.
    writes: { kind: 'contains', subs: [], forbidden: [0x1e] },
    setFreq: { kind: 'no-change' }, clickTargetHz: null,
  };
}

async function pathHold(ctx: PathContext): Promise<PathReport> {
  if (ctx.source !== 'hardware') {
    return {
      summary: 'HOLD on a non-hardware scope source',
      na: { 4: 'no radio scope hold on this source' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  const hold = ctx.page.getByTestId('scope-hold').first();
  if (!(await hold.count())) {
    return {
      summary: 'no HOLD key rendered', na: { 4: 'HOLD key absent' },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  await hold.click();
  await ctx.page.waitForTimeout(3000);
  return {
    summary: 'enabled scope HOLD (radio-held scope output)',
    na: {},
    writes: { kind: 'contains', subs: [0x17], forbidden: [] },
    setFreq: { kind: 'no-change' }, clickTargetHz: null,
  };
}

async function pathFullscreen(ctx: PathContext): Promise<PathReport> {
  const toggle = ctx.page.getByTitle('Toggle fullscreen').first();
  if (!(await toggle.count())) {
    return {
      summary: 'no fullscreen key rendered', na: {},
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  await toggle.click();
  await settle(ctx.page, 3000);
  return {
    summary: 'toggled the panorama fullscreen; geometry changed and re-settled',
    na: {}, writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
  };
}

async function pathResizeDpr(ctx: PathContext): Promise<PathReport> {
  await ctx.page.setViewportSize({ width: 1280, height: 900 });
  await settle(ctx.page, 2000);
  await ctx.page.setViewportSize({ width: 1728, height: 1200 });
  await settle(ctx.page, 3000);
  return {
    summary: 'resized the viewport 1728×1200 → 1280×900 → back',
    na: {}, writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
  };
}

async function pathTwoClients(ctx: PathContext): Promise<PathReport> {
  const second = await ctx.browser.newContext({ viewport: { width: 1728, height: 1200 } });
  try {
    const page2 = await second.newPage();
    await page2.goto(`http://127.0.0.1:${ctx.port}/?ui=v2`);
    await ctx.page.waitForTimeout(4000);
    const fromHz = await stateFreq(ctx.page, ctx.port);
    await clickFraction(ctx.page, 'spectrum', 0.68);
    const toHz = await waitForFreqChanged(ctx.page, ctx.port, fromHz);
    await settle(ctx.page, 3000);
    const display2 = await page2.locator('.receiver-instrument [data-vfo-freq]').first().innerText()
      .catch(() => '');
    return {
      summary: `second client opened; client 1 click-tuned ${fromHz}→${toHz}; client 2 shows ${display2.replace(/\D/g, '')}`,
      // The path performs a click-tune, so a hardware re-centre is
      // legitimate; only a non-hardware source must stay writeless.
      na: {}, writes: NO_WRITES,
      setFreq: { kind: 'consistency', note: 'two-client tuning consistency: both displays must agree with the state' },
      clickTargetHz: null,
    };
  } finally {
    await second.close();
  }
}

// ── dispatch ───────────────────────────────────────────────────────────────

export type PathId = import('./matrix-contract').MatrixPath;

export async function runPath(path: PathId, ctx: PathContext): Promise<PathReport> {
  switch (path) {
    case 'click-waterfall': return pathClickTune(ctx, 'waterfall', 0.32);
    case 'click-spectrum': return pathClickTune(ctx, 'spectrum', 0.68);
    case 'drag-pan': return pathDragPan(ctx);
    case 'arrows': return tuneStepsAfterClick(ctx, 'arrows');
    case 'wheel': return tuneStepsAfterClick(ctx, 'wheel');
    case 'digit-wheel': return pathDigitWheel(ctx);
    case 'span-steps': return pathSpanSteps(ctx);
    case 'ctr-fix': return pathCtrFix(ctx);
    case 'band-presets': return pathBandPresets(ctx);
    case 'zoom': return pathZoom(ctx);
    case 'band-change': return pathBandChange(ctx);
    case 'mode-change': return pathModeChange(ctx);
    case 'reconnect': return pathReconnect(ctx);
    case 'hold': return pathHold(ctx);
    case 'fullscreen': return pathFullscreen(ctx);
    case 'resize-dpr': return pathResizeDpr(ctx);
    case 'two-clients': return pathTwoClients(ctx);
    case 'ack-tracker': return pathAckTracker(ctx);
    case 'external-change': return pathExternalChange(ctx);
  }
}

// ── ack-tracker (MOR-4388 + MOR-4384): no false NAKs on a slow link ─────────

async function receiptCounts(evidencePath: string): Promise<{ writes: number; civNaks: number }> {
  const read = async <T>(file: string, parse: (line: string) => T): Promise<T[]> => {
    try {
      return parseReceiptLines(await readFile(file, 'utf8'), parse);
    } catch {
      return [];
    }
  };
  const writes = await read(evidencePath, parseScopeWriteReceipt);
  const naks = await read(`${evidencePath.slice(0, -'.jsonl'.length)}.nak.jsonl`, parseNakReceipt);
  return { writes: writes.length, civNaks: naks.filter(nak => nak.civHex.length > 0).length };
}

async function forceBackgroundTunerRead(page: Page, port: number): Promise<string> {
  // D6: the armed 1C:01 refusal can only trigger while a tuner-status read
  // is on the wire, and Core never polls it during 5 fast click-tunes (rc8:
  // zero 1C:01 frames in the whole run) — so the path sends one itself. A
  // second, subscription-free socket on the page's own origin behaves like a
  // second tab that never subscribes (the two-clients path proves attach is
  // safe): no scope traffic, nothing the page observes. RX-safe — a bare
  // get_tuner_status read never transmits. Fired just before the tune loop,
  // the slow link (150 ms deferral) lands its FA mid-tune, which is the
  // MOR-4384 overlap under test.
  return page.evaluate((targetPort: number) => new Promise<string>(resolve => {
    let settled = false;
    const done = (outcome: string): void => { if (!settled) { settled = true; resolve(outcome); } };
    const ws = new WebSocket(`ws://127.0.0.1:${targetPort}/api/v1/ws`);
    const timer = setTimeout(() => { try { ws.close(); } catch { /* noop */ } done('timeout'); }, 10_000);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'cmd', name: 'get_tuner_status', id: 'mor4388-ack-probe', params: {} }));
    ws.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== 'string') return;
      try {
        const incoming = JSON.parse(event.data) as { id?: string; ok?: boolean };
        if (incoming.id === 'mor4388-ack-probe') {
          clearTimeout(timer);
          try { ws.close(); } catch { /* noop */ }
          done(incoming.ok === false ? 'refused' : 'answered');
        }
      } catch { /* ignore non-JSON frames */ }
    };
    ws.onerror = () => { clearTimeout(timer); done('ws-error'); };
    ws.onclose = () => { clearTimeout(timer); done('closed-no-answer'); };
  }), port);
}

async function pathAckTracker(ctx: PathContext): Promise<PathReport> {
  const errorsBefore = ctx.observations.commandErrors.length;
  // Several click-tunes raise the odds that a late FA is in flight while a
  // set_freq waiter is registered (the MOR-4384 misattribution needs that
  // overlap); every tune must still land and report success. pathClickTune
  // throws when a tune never lands, so reaching the return means all five
  // applied on the radio.
  const probe = forceBackgroundTunerRead(ctx.page, ctx.port);
  let last: PathReport | null = null;
  for (let attempt = 0; attempt < 5; attempt++) {
    last = await pathClickTune(ctx, 'spectrum', 0.30 + 0.08 * attempt);
  }
  const probeOutcome = await probe;
  const commandErrors = ctx.observations.commandErrors.slice(errorsBefore).map(entry => entry.message);
  return {
    summary: `5 click-tunes on a slow link (150 ms answers, one armed 1C:01 refusal, forced-read outcome: ${probeOutcome}); ${commandErrors.length} command error(s) during the path`,
    na: {},
    writes: NO_WRITES,
    setFreq: last?.setFreq ?? { kind: 'consistency', note: 'no tune completed' },
    clickTargetHz: last?.clickTargetHz ?? null,
    naks: { kind: 'armed-refusal', cmd: 0x1c, sub: 0x01 },
    commandErrors,
  };
}

// ── external change on the radio (owner row, MOR-4388 notes-next) ───────────
// The change is applied directly to simulator state (trigger file), never
// via Core: no CI-V from us, no scope-write receipts. The owner invariant:
// RigPlane adopts the radio's value (SPAN label + view follow the new
// coverage, zoom stays a crop inside), no hatch, zero scope writes from us,
// no loop (no set_freq from us, no re-subscribe storm).

async function readSpanLabel(page: Page): Promise<string | null> {
  const text = await page.getByTestId('scope-span-value').first().innerText().catch(() => '');
  return text === '' ? null : text;
}

interface ExternalSnapshot {
  readonly view: readonly [number, number] | null;
  readonly coverage: readonly [number, number] | null;
  readonly writes: number;
  readonly grants: number;
  readonly setFreqs: number;
  readonly freq: number;
  readonly spanLabel: string | null;
}

async function pathExternalChange(ctx: PathContext): Promise<PathReport> {
  if (ctx.source !== 'hardware') {
    return {
      summary: 'external radio change on a non-hardware scope source',
      na: {
        1: 'no radio VFO on this source',
        2: 'no radio span on this source',
        3: 'no radio span presets on this source',
        4: 'no radio scope writes possible',
      },
      writes: NO_WRITES, setFreq: { kind: 'no-change' }, clickTargetHz: null,
    };
  }
  const triggerFile = `${ctx.evidencePath.slice(0, -'.jsonl'.length)}.extchange.json`;
  const failures: string[] = [];
  const steps: ExternalStepReport[] = [];

  const snapshot = async (): Promise<ExternalSnapshot> => {
    const lastFrame = ctx.observations.frames.at(-1) ?? null;
    const lastGrant = ctx.observations.grants.at(-1) ?? null;
    const counts = await receiptCounts(ctx.evidencePath);
    return {
      view: lastFrame ? [lastFrame.view[0], lastFrame.view[1]]
        : lastGrant ? [lastGrant.view.startHz, lastGrant.view.endHz] : null,
      coverage: lastFrame ? [lastFrame.coverage[0], lastFrame.coverage[1]] : null,
      writes: counts.writes,
      grants: ctx.observations.grants.length,
      setFreqs: ctx.observations.commands.filter(command => command.name === 'set_freq').length,
      freq: await stateFreq(ctx.page, ctx.port),
      spanLabel: await readSpanLabel(ctx.page),
    };
  };

  const applyExternal = async (step: string, change: Record<string, unknown>): Promise<boolean> => {
    await writeFile(triggerFile, JSON.stringify(change));
    try {
      await expect.poll(async () => {
        try {
          await stat(triggerFile);
          return 'pending';
        } catch {
          return 'applied';
        }
      }, { timeout: 10_000 }).toBe('applied');
      return true;
    } catch {
      failures.push(`${step}: the simulator did not apply ${JSON.stringify(change)} within 10 s`);
      return false;
    }
  };

  const recordStep = async (
    step: string, change: Record<string, unknown>, note: string, ok: boolean,
    before: ExternalSnapshot, after: ExternalSnapshot,
  ): Promise<void> => {
    if (!ok) failures.push(`${step}: ${note}`);
    steps.push({
      step, change: JSON.stringify(change), spanLabel: after.spanLabel,
      viewStartHz: after.view?.[0] ?? null, viewEndHz: after.view?.[1] ?? null,
      coverageStartHz: after.coverage?.[0] ?? null, coverageEndHz: after.coverage?.[1] ?? null,
      stateFreqHz: after.freq,
      writesDelta: after.writes - before.writes,
      grantsDelta: after.grants - before.grants,
      setFreqDelta: after.setFreqs - before.setFreqs,
      ok, note,
    });
  };

  const quiet = (before: ExternalSnapshot, after: ExternalSnapshot): string | null => {
    if (after.writes - before.writes !== 0) return `us-scope-writes=${after.writes - before.writes}`;
    if (after.setFreqs - before.setFreqs !== 0) return `us-set_freq=${after.setFreqs - before.setFreqs}`;
    if (after.grants - before.grants > 2) return `grants-storm=${after.grants - before.grants}`;
    return null;
  };

  // 1. VFO knob on the radio: 14.25 → 14.18 MHz.
  let before = await snapshot();
  if (await applyExternal('vfo', { freq_hz: 14_180_000 })) {
    await settle(ctx.page, 3000);
    const after = await snapshot();
    const adopted = after.freq === 14_180_000;
    const noise = quiet(before, after);
    await recordStep('vfo', { freq_hz: 14_180_000 },
      adopted ? (noise ?? 'state follows the radio VFO') : `state freq ${after.freq}, radio at 14180000`,
      adopted && noise === null, before, after);
    before = after;
  }
  // 2. Span knob on the radio: 100 kHz → 50 kHz.
  if (await applyExternal('span', { span_hz: 50_000 })) {
    await settle(ctx.page, 3000);
    const after = await snapshot();
    const width = after.view === null ? null : after.view[1] - after.view[0];
    const adopted = width === 50_000;
    const labelMoved = after.spanLabel !== before.spanLabel;
    const noise = quiet(before, after);
    await recordStep('span', { span_hz: 50_000 },
      !adopted ? `view width ${width}, radio span 50000`
        : !labelMoved ? `SPAN label stuck at ${after.spanLabel}`
        : (noise ?? 'view and SPAN label follow the radio span'),
      adopted && labelMoved && noise === null, before, after);
    before = after;
  }
  // 3. Scope mode on the radio: centre → FIX (fixed edges 14.2–14.3 MHz).
  if (await applyExternal('mode-fixed', { scope_mode: 1 })) {
    await settle(ctx.page, 3000);
    const after = await snapshot();
    const adopted = after.coverage?.[0] === 14_200_000 && after.coverage?.[1] === 14_300_000;
    const noise = quiet(before, after);
    await recordStep('mode-fixed', { scope_mode: 1 },
      adopted ? (noise ?? 'coverage follows the fixed edges')
        : `coverage ${after.coverage?.join('..')}, fixed 14200000..14300000`,
      adopted && noise === null, before, after);
    before = after;
  }
  // 4. Restore the default state so the post-path invariants evaluate.
  if (await applyExternal('restore', { scope_mode: 0, span_hz: 100_000, freq_hz: 14_250_000 })) {
    await settle(ctx.page, 3000);
    const after = await snapshot();
    const width = after.view === null ? null : after.view[1] - after.view[0];
    const restored = after.freq === 14_250_000 && width === 100_000;
    const noise = quiet(before, after);
    await recordStep('restore', { scope_mode: 0, span_hz: 100_000, freq_hz: 14_250_000 },
      restored ? (noise ?? 'default state restored') : `freq ${after.freq}, width ${width}`,
      restored && noise === null, before, after);
  }

  if (failures.length > 0) {
    throw new Error(`external radio change not adopted:\n${failures.join('\n')}\nsteps: ${JSON.stringify(steps)}`);
  }
  return {
    summary: `external VFO/span/mode/restore adopted: ${steps.map(step => `${step.step}(${step.note})`).join('; ')}`,
    na: { 3: 'the radio span is set externally; no covering-preset request exists' },
    writes: NO_WRITES,
    setFreq: { kind: 'no-change' },
    clickTargetHz: null,
    external: steps,
  };
}

export { stateFreq, vfoDisplayHz, waitForFrames, settle, readStepHz };
