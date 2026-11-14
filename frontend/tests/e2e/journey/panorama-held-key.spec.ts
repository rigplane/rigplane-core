// MOR held-key repro (owner, real IC-705 over LAN, integration build c5bbe782a):
// holding an arrow key down (OS key auto-repeat) starts moving the panorama,
// but after some steps it starts jerking back and forth, and after some steps
// the filter passband disappears and comes back after about a second. CTR
// (CENTER) scope mode, follow, radio span preset 250000, view zoomed inside.
//
// TEST CODE ONLY: this spec pins the two symptoms as RED assertions against
// the loopback PanoramaSimulator with simulated CI-V readback latency
// (--response-delay/--response-jitter in tests/e2e/panorama_web_server.py).
// (a) while the key is held and until settle, the displayed VFO frequency,
// the marker's frequency (marker x mapped through the current view) and the
// view centre never move backwards by more than half a step; (b) the passband
// overlay is never absent for more than 200 ms from burst start to settle
// end. If nothing reproduces, the spec reports the observed maxima instead
// of being tuned until it fails.
import type { Page } from '@playwright/test';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame, parseScopeGranted } from '../../../src/lib/runtime/scope-frame';
import type { ScopeGranted, ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import type { WsCommand } from '../../../src/lib/types/protocol';
import type { ScopePassbandDiagnostics } from '../../../src/lib/runtime/adapters/scope-passband-display';
import type { ServerState } from '../../../src/lib/types/state';
import {
  installSampler,
  passbandGaps,
  gateDiff,
  settlePassband,
} from './panorama-passband-probe';
import { startJourneyServer } from './server-process';
import { expectPanoramaSettled, watchPanoramaErrors } from './panorama-guards';

/** One rAF sample of the held-key burst: VFO readout, marker geometry and the rendered view. */
interface HeldKeySample {
  tPerf: number;
  tWall: number;
  overlay: boolean;
  gates: ScopePassbandDiagnostics | null;
  state: string | null;
  vfoText: string | null;
  markerX: number | null;
  areaLeft: number;
  areaWidth: number;
}

declare global {
  interface Window { heldKeySamples: HeldKeySample[] }
}

/** rAF sampler modelled on installSampler, plus the VFO readout and marker/view geometry. */
async function installHeldKeySampler(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.heldKeySamples = [];
    const loop = () => {
      const panel = document.querySelector('.spectrum-panel');
      const overlay = panel?.querySelector('.waterfall-content .passband-overlay');
      const marker = panel?.querySelector('.waterfall-content .tune-line');
      const area = document.querySelector('.spectrum-area');
      const gatesRaw = panel?.getAttribute('data-passband-gates');
      const markerRect = marker instanceof HTMLElement ? marker.getBoundingClientRect() : null;
      const areaRect = area instanceof HTMLElement ? area.getBoundingClientRect() : null;
      window.heldKeySamples.push({
        tPerf: performance.now(),
        tWall: Date.now(),
        overlay: overlay instanceof HTMLElement && overlay.getBoundingClientRect().width > 0,
        gates: gatesRaw ? JSON.parse(gatesRaw) as ScopePassbandDiagnostics : null,
        state: panel?.getAttribute('data-passband-state') ?? null,
        vfoText: document.querySelector('.receiver-instrument [data-vfo-freq]')?.textContent ?? null,
        markerX: markerRect ? markerRect.left + markerRect.width / 2 : null,
        areaLeft: areaRect?.left ?? NaN,
        areaWidth: areaRect?.width ?? NaN,
      });
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
}

interface HeldKeyTimeline {
  params: {
    latency: string;
    responseDelayS: number;
    responseJitterS: number;
    stepHz: number;
    seed: number;
    spanHz: number;
    freqHz: number;
  };
  burst: { startPerf: number; endPerf: number; settleEndPerf: number };
  setup: {
    spanValue: string | null;
    grantedView: unknown;
    framesView: number[] | null;
    beforeHz: number;
    afterHz: number;
  };
  monotonic: {
    toleranceHz: number;
    vfo: { first: BackwardMove | null; maxBackwardHz: number; samples: number; missing: number };
    marker: { first: BackwardMove | null; maxBackwardHz: number; samples: number; missing: number };
    center: { first: BackwardMove | null; maxBackwardHz: number; samples: number; missing: number };
  };
  passband: {
    gaps: Array<{ startRelMs: number; frames: number; durationMs: number; gateDiff: Record<string, unknown> }>;
  };
  setFreqCommands: Array<{ tWall: number; freqHz: number | null }>;
  grants: Array<{ tWall: number; grant: ScopeGranted }>;
  subscribes: ScopeSubscription[];
  frames: Array<{ view: number[]; coverage: number[]; receivedAt: number }>;
  samples: HeldKeySample[];
}

interface BackwardMove {
  tRelMs: number;
  valueHz: number;
  prevMaxHz: number;
}

function parseVfoText(raw: string | null): number | null {
  if (!raw) return null;
  const digits = raw.replace(/\D/g, '');
  return digits ? Number(digits) : null;
}

function markerFrequency(sample: HeldKeySample): number | null {
  const gates = sample.gates;
  if (!gates || sample.markerX === null) return null;
  if (!Number.isFinite(sample.areaWidth) || sample.areaWidth <= 0) return null;
  if (!Number.isFinite(gates.viewStartHz) || !Number.isFinite(gates.viewEndHz)) return null;
  const fraction = (sample.markerX - sample.areaLeft) / sample.areaWidth;
  return gates.viewStartHz + fraction * (gates.viewEndHz - gates.viewStartHz);
}

function viewCenterHz(sample: HeldKeySample): number | null {
  const gates = sample.gates;
  if (!gates || !Number.isFinite(gates.viewStartHz) || !Number.isFinite(gates.viewEndHz)) return null;
  return (gates.viewStartHz + gates.viewEndHz) / 2;
}

/** Running-max check: no value may fall below an earlier one by more than tolHz. */
function checkMonotonic(
  values: Array<{ t: number; v: number | null }>,
  fromPerf: number,
  tolHz: number,
): { first: BackwardMove | null; maxBackwardHz: number; samples: number; missing: number } {
  let max = -Infinity;
  let first: BackwardMove | null = null;
  let maxBackwardHz = 0;
  let samples = 0;
  let missing = 0;
  for (const { t, v } of values) {
    if (v === null || !Number.isFinite(v)) {
      missing += 1;
      continue;
    }
    samples += 1;
    if (v < max - tolHz) {
      maxBackwardHz = Math.max(maxBackwardHz, max - v);
      first ??= { tRelMs: t - fromPerf, valueHz: v, prevMaxHz: max };
    } else if (v > max) {
      max = v;
    }
  }
  return { first, maxBackwardHz, samples, missing };
}

async function radioFrequency(page: Page, port: number): Promise<number> {
  const state = await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json() as ServerState;
  return state.main?.freqHz ?? NaN;
}

const LATENCIES = [
  { name: 'd0', delay: 0, jitter: 0 },
  { name: 'd100ms', delay: 0.1, jitter: 0 },
  { name: 'd300ms', delay: 0.3, jitter: 0 },
  { name: 'd150ms-j100ms', delay: 0.15, jitter: 0.1 },
] as const;

const STEPS = [1_000, 100] as const;

for (const [latencyIndex, latency] of LATENCIES.entries()) {
  for (const [stepIndex, stepHz] of STEPS.entries()) {
    test(`IC-705: held ArrowRight stays monotonic with the passband present (${latency.name}, step ${stepHz}Hz)`, async ({ page }, info) => {
      const port = 18960 + latencyIndex * STEPS.length + stepIndex;
      const seed = 1234;
      const evidence = info.outputPath('radio.jsonl');
      const server = await startJourneyServer(port, 14_250_000,
        ['--model', 'IC-705', '--evidence', evidence,
          '--response-delay', String(latency.delay),
          '--response-jitter', String(latency.jitter),
          '--span-hz', '250000',
          '--seed', String(seed)],
        'tests/e2e/panorama_web_server.py');
      const watch = watchPanoramaErrors(page);
      try {
        const sent: Array<ScopeSubscription & { type: string }> = [];
        const grants: Array<{ tWall: number; grant: ScopeGranted }> = [];
        const frames: Array<{ view: number[]; coverage: number[]; receivedAt: number }> = [];
        const setFreqCommands: Array<{ tWall: number; freqHz: number | null }> = [];
        page.on('websocket', (socket) => {
          if (new URL(socket.url()).pathname === '/api/v1/ws') {
            socket.on('framesent', ({ payload }) => {
              if (typeof payload === 'string') {
                const command = JSON.parse(payload) as WsCommand;
                if (command.type === 'cmd' && command.name === 'set_freq') {
                  setFreqCommands.push({
                    tWall: Date.now(),
                    freqHz: (command.params as { freq?: number } | undefined)?.freq ?? null,
                  });
                }
              }
            });
            return;
          }
          if (!socket.url().endsWith('/api/v1/scope')) return;
          socket.on('framesent', ({ payload }) => {
            if (typeof payload === 'string') {
              const message = JSON.parse(payload) as ScopeSubscription & { type: string };
              if (message.type === 'subscribe') sent.push(message);
            }
          });
          socket.on('framereceived', ({ payload }) => {
            if (typeof payload === 'string') {
              const grant = parseScopeGranted(JSON.parse(payload));
              if (grant) grants.push({ tWall: Date.now(), grant });
            } else {
              const buffer = Uint8Array.from(payload).buffer;
              const parsed = parseCurrentScopeFrame(buffer, { source: 'radio', sourceId: 'radio', epoch: 0 });
              if (parsed.ok) {
                frames.push({
                  view: [parsed.frame.viewStartHz, parsed.frame.viewEndHz],
                  coverage: [parsed.frame.coverageStartHz, parsed.frame.coverageEndHz],
                  receivedAt: performance.now(),
                });
              }
            }
          });
        });
        await installSampler(page);
        await installHeldKeySampler(page);
        await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
        await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
        const marker = page.locator('.waterfall-content .tune-line');
        await expect(marker).toBeVisible({ timeout: 30_000 });

        // CTR (CENTER) scope mode — the owner's failing shape.
        const ctr = page.getByRole('radio', { name: 'CTR' }).first();
        if (!(await ctr.isChecked())) await ctr.click();
        await expect(ctr).toBeChecked();

        // Passband overlay must be drawable before the burst (mode + filter
        // width evidence chain, as in panorama-passband-hold).
        await settlePassband(page, port);
        await page.waitForTimeout(1000);

        // View zoomed to ±200 kHz: two ZOOM− steps from the 100 kHz default
        // follow width. The smallest covering radio preset stays 250000, so
        // this is a pure client crop with no span write of its own.
        await page.getByRole('button', { name: 'More scope controls' }).first().click();
        await page.getByTestId('scope-zoom-out').first().click();
        await page.getByTestId('scope-zoom-out').first().click();
        await page.keyboard.press('Escape');
        await expect.poll(() => sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 400_000 });
        await expect(page.getByTestId('scope-span-value').first()).toHaveText('±200k');
        await expect.poll(() => frames.at(-1)?.view).toEqual([14_050_000, 14_450_000]);

        // Tuning step: 1 kHz is the LSB auto default; smaller steps cycle down.
        if (stepHz !== 1_000) {
          for (let i = 0; i < 3; i++) {
            await page.getByRole('button', { name: 'Decrease tuning step', exact: true }).first().click();
          }
        }
        await expect.poll(() => page.getByTestId('toolbar-row-step').first().innerText())
          .toContain(stepHz === 1_000 ? '1kHz' : '100Hz');

        // Arrow keys belong to tuning only after leaving any editor.
        await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
        await page.waitForTimeout(1000);
        const beforeHz = await radioFrequency(page, port);

        // The owner's gesture: hold ArrowRight for 4 s. Repeated down()
        // dispatches keydown with repeat=true while the key stays held.
        const burstStartPerf = await page.evaluate(() => performance.now());
        await page.keyboard.down('ArrowRight');
        for (let i = 0; i < 120; i++) {
          await page.keyboard.down('ArrowRight');
          await page.waitForTimeout(33);
        }
        await page.keyboard.up('ArrowRight');
        const burstEndPerf = await page.evaluate(() => performance.now());
        await page.waitForTimeout(3000);
        const settleEndPerf = await page.evaluate(() => performance.now());
        const afterHz = await radioFrequency(page, port);

        // The burst must have moved the VFO at all — otherwise monotonicity
        // and overlay continuity below would pass vacuously.
        expect(afterHz, 'held-key burst did not move the VFO; the stimulus is invalid').toBeGreaterThan(beforeHz);

        const samples = await page.evaluate(() => window.heldKeySamples);
        const passbandSamples = await page.evaluate(() => window.passbandSamples);
        const inWindow = samples.filter((s) => s.tPerf >= burstStartPerf && s.tPerf <= settleEndPerf);
        const toleranceHz = stepHz / 2;
        const vfo = checkMonotonic(
          inWindow.map((s) => ({ t: s.tPerf, v: parseVfoText(s.vfoText) })), burstStartPerf, toleranceHz);
        const markerSeries = checkMonotonic(
          inWindow.map((s) => ({ t: s.tPerf, v: markerFrequency(s) })), burstStartPerf, toleranceHz);
        const center = checkMonotonic(
          inWindow.map((s) => ({ t: s.tPerf, v: viewCenterHz(s) })), burstStartPerf, toleranceHz);
        const gaps = passbandGaps(passbandSamples, burstStartPerf, settleEndPerf);
        const longGaps = gaps.filter((gap) => gap.durationMs > 200).map((gap) => ({
          startRelMs: gap.startMs - burstStartPerf,
          frames: gap.frames,
          durationMs: gap.durationMs,
          gateDiff: gateDiff(gap),
        }));

        const spanText = await page.getByTestId('scope-span-value').first().innerText().catch(() => null);
        const timeline: HeldKeyTimeline = {
          params: { latency: latency.name, responseDelayS: latency.delay, responseJitterS: latency.jitter,
            stepHz, seed, spanHz: 250_000, freqHz: 14_250_000 },
          burst: { startPerf: burstStartPerf, endPerf: burstEndPerf, settleEndPerf },
          setup: { spanValue: spanText, grantedView: sent.at(-1)?.view ?? null,
            framesView: frames.at(-1)?.view ?? null, beforeHz, afterHz },
          monotonic: { toleranceHz, vfo, marker: markerSeries, center },
          passband: { gaps: longGaps },
          setFreqCommands,
          grants,
          subscribes: sent,
          frames,
          samples,
        };
        await info.attach('held-key-timeline', {
          body: JSON.stringify(timeline, null, 2),
          contentType: 'application/json',
        });
        console.log(`[held-key ${latency.name} step=${stepHz}] ` +
          `vfo maxBackward=${vfo.maxBackwardHz}Hz marker maxBackward=${markerSeries.maxBackwardHz}Hz ` +
          `center maxBackward=${center.maxBackwardHz}Hz longGaps=${longGaps.length} ` +
          `before=${beforeHz} after=${afterHz} setFreq=${setFreqCommands.length}`);

        // (a) monotonic: no series may move backwards by more than half a step.
        expect.soft(vfo.first,
          `VFO moved backwards ${vfo.maxBackwardHz}Hz at +${vfo.first?.tRelMs.toFixed(0)}ms ` +
          `(value=${vfo.first?.valueHz}, prevMax=${vfo.first?.prevMaxHz}, tol=${toleranceHz}Hz)`).toBeNull();
        expect.soft(markerSeries.first,
          `marker moved backwards ${markerSeries.maxBackwardHz}Hz at +${markerSeries.first?.tRelMs.toFixed(0)}ms ` +
          `(value=${markerSeries.first?.valueHz}, prevMax=${markerSeries.first?.prevMaxHz}, tol=${toleranceHz}Hz)`).toBeNull();
        expect.soft(center.first,
          `view centre moved backwards ${center.maxBackwardHz}Hz at +${center.first?.tRelMs.toFixed(0)}ms ` +
          `(value=${center.first?.valueHz}, prevMax=${center.first?.prevMaxHz}, tol=${toleranceHz}Hz)`).toBeNull();
        // (b) the passband overlay is never absent for more than 200 ms.
        expect.soft(longGaps,
          `passband absent ${longGaps.length}x >200ms; first at +${longGaps[0]?.startRelMs.toFixed(0)}ms ` +
          `for ${longGaps[0]?.durationMs.toFixed(0)}ms (${longGaps[0]?.frames} frames), ` +
          `gate diff: ${JSON.stringify(longGaps[0]?.gateDiff)}`).toEqual([]);

        expectPanoramaSettled(watch);
      } finally {
        await server.kill();
      }
    });
  }
}
