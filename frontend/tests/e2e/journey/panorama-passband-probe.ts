import { expect } from '@playwright/test';

interface PassbandSample {
  t: number;
  overlay: boolean;
  gates: Record<string, unknown> | null;
  state: string | null;
}

declare global {
  interface Window { passbandSamples: PassbandSample[] }
}

export async function installSampler(page: import('@playwright/test').Page): Promise<void> {
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
export function passbandGaps(samples: PassbandSample[], fromT: number, toT: number): Gap[] {
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

export function gateDiff(gap: Gap): Record<string, unknown> {
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

