import { expect } from '@playwright/test';
import type { ScopePassbandDiagnostics } from '../../../src/lib/runtime/adapters/scope-passband-display';
import { parseCurrentScopeFrame } from '../../../src/lib/runtime/scope-frame';

interface PassbandSample {
  t: number;
  /** 'frame': an animation frame ran; 'dom': the panel's DOM changed. */
  kind: 'frame' | 'dom';
  overlay: boolean;
  gates: ScopePassbandDiagnostics | null;
  state: string | null;
}

declare global {
  interface Window { passbandSamples: PassbandSample[] }
}

/**
 * Records the passband overlay's presence from two independent triggers:
 * every animation frame (what the operator could see) and every DOM change
 * under the document (what the page did). MOR-4214: the first alone made the
 * probe a measure of the host. Under software WebGL on a loaded runner the
 * page can go seconds without producing a frame, and a frame-only sampler
 * then reported "samples do not cover the interval" for a page that was
 * simply not painting. A DOM-driven record is made at the moment of each
 * change whatever the frame rate, so a missing sample means "nothing
 * changed", never "the sampler was not running".
 */
export async function installSampler(page: import('@playwright/test').Page): Promise<void> {
  await page.addInitScript(() => {
    window.passbandSamples = [];
    const snapshot = (kind: 'frame' | 'dom') => {
      const panel = document.querySelector('.spectrum-panel');
      const overlay = panel?.querySelector('.waterfall-content .passband-overlay');
      const gatesRaw = panel?.getAttribute('data-passband-gates');
      const sample: PassbandSample = {
        t: performance.now(),
        kind,
        overlay: overlay instanceof HTMLElement && overlay.getBoundingClientRect().width > 0,
        gates: gatesRaw ? JSON.parse(gatesRaw) as ScopePassbandDiagnostics : null,
        state: panel?.getAttribute('data-passband-state') ?? null,
      };
      // A DOM record that repeats the previous state adds nothing.
      const last = window.passbandSamples.at(-1);
      if (kind === 'dom' && last && last.overlay === sample.overlay && last.state === sample.state
        && JSON.stringify(last.gates) === JSON.stringify(sample.gates)) return;
      window.passbandSamples.push(sample);
    };
    const loop = () => {
      snapshot('frame');
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    new MutationObserver(() => snapshot('dom')).observe(document, {
      subtree: true, childList: true, attributes: true,
    });
  });
}

interface Gap {
  startMs: number;
  /** Animation frames that ran with the overlay absent (what could be seen). */
  frames: number;
  durationMs: number;
  /** Gate JSON at the last good sample and the first bad one. */
  before: ScopePassbandDiagnostics | null;
  atDrop: ScopePassbandDiagnostics | null;
  stateAtDrop: string | null;
}

/**
 * Runs of absent overlay inside [fromT, toT], plus the gate diff at each
 * drop. The page is in the state of the last sample before the window until
 * something changes, so no sample needs to land inside the window for the
 * interval to be covered.
 *
 * Only painted frames are evidence of what an operator saw. A run opens at the
 * first absent sample (frame or DOM) and `frames` counts the absent frames in
 * it. A run a frame has seen (`frames > 0`) is closed only by a frame that
 * shows the overlay, so a DOM-only restoration between two absent frames does
 * not split one outage. A run no frame has seen (`frames === 0`) is closed by
 * the first sample that shows the overlay, so an unpainted hole never merges
 * into, or hides, a later painted outage. `paintedOutages` keeps the runs a
 * frame saw.
 *
 * A run still open when the window starts (absent in the last sample before
 * it, or a painted outage a DOM restoration has not yet closed) continues
 * into the window as a gap starting at the window start, keeping the painted
 * frames already counted.
 */
export function passbandGaps(samples: PassbandSample[], fromT: number, toT: number): Gap[] {
  if (!samples.some((sample) => sample.t < fromT)) {
    throw new Error('Passband sampler recorded nothing before the stimulus interval');
  }
  const gaps: Gap[] = [];
  let run: Gap | null = null;
  let lastGood: PassbandSample | null = null;
  let entered = false;
  for (const sample of samples) {
    if (sample.t > toT) break;
    if (!entered && sample.t >= fromT) {
      entered = true;
      if (run !== null) {
        run = { ...run, startMs: fromT, durationMs: 0 };
        gaps.push(run);
      }
    }
    if (sample.overlay) {
      if (run) {
        run.durationMs = sample.t - run.startMs;
        if (sample.kind === 'frame' || run.frames === 0) run = null;
      }
      lastGood = sample;
      continue;
    }
    if (run === null) {
      run = {
        startMs: sample.t, frames: 0, durationMs: 0,
        before: lastGood?.gates ?? null, atDrop: sample.gates, stateAtDrop: sample.state,
      };
      if (entered) gaps.push(run);
    }
    if (sample.kind === 'frame') run.frames += 1;
    run.durationMs = sample.t - run.startMs;
  }
  if (!entered && run !== null) gaps.push({ ...run, startMs: fromT, durationMs: 0 });
  return gaps;
}

/** Gaps an operator could have seen (at least one painted absent frame) longer than `minMs`. */
export function paintedOutages(gaps: Gap[], minMs: number): Gap[] {
  return gaps.filter((gap) => gap.frames > 0 && gap.durationMs > minMs);
}

export function gateDiff(gap: Gap): Record<string, unknown> {
  const diff: Record<string, unknown> = {};
  if (!gap.before || !gap.atDrop) return diff;
  for (const key of (Object.keys(gap.atDrop) as Array<keyof ScopePassbandDiagnostics>)) {
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
export async function settlePassband(page: import('@playwright/test').Page, port: number): Promise<void> {
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


/** A no-op control cannot establish overlay continuity during a stimulus. */
export function requireProbeStimulus(before: number, after: number, direction: 'up' | 'down'): void {
  if (!Number.isFinite(before) || !Number.isFinite(after)
    || (direction === 'up' ? after <= before : after >= before)) {
    throw new Error('Passband stimulus did not advance');
  }
}

export function readPassbandDiagnostics(raw: string | null): ScopePassbandDiagnostics | null {
  return raw === null ? null : JSON.parse(raw) as ScopePassbandDiagnostics;
}

export function watchScopeFrames(page: import('@playwright/test').Page) {
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

