// Acceptance for MOR-4451: holding an arrow key in CENTER follow, the
// panorama view and marker must never move BACKWARDS while the VFO readout
// moves forward. The follow confirmed leg used to centre on the passband
// tuple's held frequency (stale for seconds during a burst), so every pending
// clear retargeted the glide backwards (0.5-1.6 kHz here; 10-24 kHz combined
// with #4565) — the fix centres it on the radio's confirmed frequency.
//
// Run on the INTEGRATED tree (needs #4555 for connecting under latency and
// #4565's follow-grant fix):
//
//   npx playwright test -c ./playwright.held-key.config.ts
//
// It is not part of the per-PR CI sweep on purpose (the journey and panorama
// configs do not match this directory), same reason as the slow-link spec's
// header. A row that cannot connect SKIPS LOUDLY at runtime with the row
// name and delay in the skip reason (visible in the report) instead of
// asserting — the monotonic and overlay assertions below are never weakened.
// The integrated-tree run must show ZERO skips: any skip there is a failure
// of this acceptance.
import type { Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
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
} from '../journey/panorama-passband-probe';
import { startJourneyServer } from '../journey/server-process';
import type { JourneyServerHandle } from '../journey/server-process';
import { expectPanoramaSettled, watchPanoramaErrors } from '../journey/panorama-guards';

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
    zoom: string;
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
  { name: 'd50ms', delay: 0.05, jitter: 0 },
  { name: 'd50ms-j50ms', delay: 0.05, jitter: 0.05 },
  { name: 'd100ms', delay: 0.1, jitter: 0 },
  { name: 'd300ms', delay: 0.3, jitter: 0 },
] as const;

const STEPS = [1_000, 100] as const;

const BASE_FREQ_HZ = 14_250_000;

const ZOOMS = [
  { name: 'zoomed', spanHz: 400_000, spanText: '±200k' },
  // Native means no zoom crop by the user: the expected frame view is never
  // hard-coded here — it is derived from the server grant below (follow view
  // centred on the base frequency with the granted width), so it tracks the
  // client's default follow width on any UI (100 kHz here, ±250 kHz with
  // #4565's 250000 preset) instead of the radio span.
  { name: 'native', spanHz: null, spanText: null },
] as const;

let caseIndex = 0;
for (const zoom of ZOOMS) {
  for (const latency of LATENCIES) {
    for (const stepHz of STEPS) {
      const port = 21500 + (caseIndex++);
      test(`IC-705 held ArrowRight stays monotonic with the passband present (${zoom.name}, ${latency.name}, step ${stepHz}Hz)`, async ({ page }, info) => {
        const seed = 1234;
        const evidence = info.outputPath('radio.jsonl');
        // Rows past the latency this branch can connect at never reach
        // setup: only a genuine connect failure (no scope frames at all)
        // skips at runtime (detected here); a setup expectation that fails
        // after frames flow is a FAILURE, never a skip. Never weaken the
        // asserts.
        let setupDone = false;
        let server: JourneyServerHandle | undefined;
        // Declared before the try: the catch skip-rule reads frames, so it
        // must be visible there (a `const` inside the try is not).
        const sent: Array<ScopeSubscription & { type: string }> = [];
        const grants: Array<{ tWall: number; grant: ScopeGranted }> = [];
        const frames: Array<{ view: number[]; coverage: number[]; receivedAt: number }> = [];
        const setFreqCommands: Array<{ tWall: number; freqHz: number | null }> = [];
        try {
          server = await startJourneyServer(port, 14_250_000,
            ['--model', 'IC-705', '--evidence', evidence,
              '--response-delay', String(latency.delay),
              '--response-jitter', String(latency.jitter),
              '--span-hz', '250000',
              '--seed', String(seed)],
            'tests/e2e/panorama_web_server.py');
          const watch = watchPanoramaErrors(page);
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

          if (zoom.name === 'zoomed') {
            // View zoomed to ±200 kHz: two ZOOM− steps from the 100 kHz
            // default follow width. The smallest covering radio preset stays
            // 250000, so this is a pure client crop with no span write of
            // its own.
            await page.getByRole('button', { name: 'More scope controls' }).first().click();
            await page.getByTestId('scope-zoom-out').first().click();
            await page.getByTestId('scope-zoom-out').first().click();
            await page.keyboard.press('Escape');
            await expect.poll(() => sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: zoom.spanHz });
            await expect(page.getByTestId('scope-span-value').first()).toHaveText(zoom.spanText!);
            // The server must have granted the zoomed width before frames
            // can carry it — otherwise the expectation below could pin a
            // stale pre-zoom grant and pass vacuously.
            await expect.poll(() => {
              const grant = grants.at(-1)?.grant;
              return grant ? grant.view.endHz - grant.view.startHz : 0;
            }).toBe(zoom.spanHz);
          }
          // The expected frame view comes from the server grant, never from
          // a hard-coded span: follow view centred on the base frequency
          // with the granted width. Frames must carry exactly that view.
          await expect.poll(() => grants.length).toBeGreaterThan(0);
          const grantedView = grants.at(-1)!.grant.view;
          const grantedWidthHz = grantedView.endHz - grantedView.startHz;
          const expectedView = [BASE_FREQ_HZ - grantedWidthHz / 2, BASE_FREQ_HZ + grantedWidthHz / 2];
          await expect.poll(() => frames.at(-1)?.view).toEqual(expectedView);

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
          setupDone = true;
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
            params: { zoom: zoom.name, latency: latency.name, responseDelayS: latency.delay,
              responseJitterS: latency.jitter, stepHz, seed, spanHz: 250_000, freqHz: 14_250_000 },
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
          await writeFile(info.outputPath('held-key-timeline.json'), JSON.stringify(timeline, null, 2));
          console.log(`[held-key ${zoom.name} ${latency.name} step=${stepHz}] ` +
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
        } catch (error) {
          // A skip is allowed ONLY for a genuine connect failure: the
          // server exits before /api/v1/state answers, or no scope frames
          // arrive at all within the existing timeout. A setup expectation
          // that fails after frames flow is a FAILURE, never a skip.
          if (!setupDone && frames.length === 0) {
            test.skip(true,
              `MOR-4451 held-key acceptance (${zoom.name}, ${latency.name} ` +
              `delay=${latency.delay}s jitter=${latency.jitter}s, step ${stepHz}Hz): ` +
              `cannot connect on this tree ` +
              `(${error instanceof Error ? error.message.split('\n')[0] : String(error)})`);
            return;
          }
          throw error;
        } finally {
          await server?.kill();
        }
      });
    }
  }
}
