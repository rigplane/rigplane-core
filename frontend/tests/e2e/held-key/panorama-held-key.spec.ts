// Acceptance for MOR-4451: holding an arrow key in CENTER follow, the
// panorama view and marker must never move BACKWARDS while the VFO readout
// moves forward. The follow confirmed leg used to centre on the passband
// tuple's held frequency (stale for seconds during a burst), so every pending
// clear retargeted the glide backwards (0.5-1.6 kHz here; 10-24 kHz combined
// with #4565) — the fix centres it on the radio's confirmed frequency.
//
// Run on the INTEGRATED tree (needs #4555 for connecting under latency and
// #4565's follow-grant fix, which makes the server send the full requested
// view with a reduced covered part during the lag):
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
//
// Latency model: a real LAN/Wi-Fi link delays both directions. The journey
// server takes one one-way knob per direction — --uplink-delay holds every
// inbound request in the simulator's response_delay dispatch delay, and
// --response-delay/--response-jitter hold every CI-V reply in the shared
// outbound CI-V FaultPlan — so the round-trip time is uplink + downlink,
// each applied once (pinned by tests/test_panorama_harness.py).
//
// The matrix rows below stay downlink-only (no --uplink-delay) and assert
// the monotonic and passband shape plus zero transient hatch, WITHOUT
// requiring partially covered frames: no held-key burst against the real
// handler observes the lag transient at all. The response delay postpones
// the waves and the frequency readback equally, so the wire view
// (re-resolved per frame on the observed frequency) and the wave coverage
// stay concentric however fast the VFO moves — measured on the integrated
// tree: 3125 Hz view at 1 kHz/d300ms, 1562.5 Hz view at 1 kHz/d300ms, and
// 3125 Hz view at 5 kHz/d300ms (a 605 kHz burst) ALL show burstCovMin ==
// burstViewMin to the hertz with partials=0. Requiring partials here would
// red forever on correct behaviour, so no matrix row requires them — and
// there is deliberately NO dedicated partials row in this file: partials
// are structurally absent after fixes 1+3, so chasing them reds forever on
// correct behaviour. The seam contract (offset coverage never hatches; a
// truly narrower native always hatches) stays pinned where offsets ARE
// synthesizable — the requireTransientSeamObserved unit suite (synthetic
// offsets, red-then-green in 06e0ef9be..abb2b09ed).
//
// The control row is the owner rule of 2026-11-14: the radio span preset
// narrows to 25 kHz from the front panel
// (--external-span-after-s/--external-span-hz) AFTER setup, with no zoom
// presses after it. It asserts NO radio span write after the external
// change (zero new 0x15 span-set receipts in radio.jsonl past the
// setup snapshot), that the displayed view ADOPTS the narrowed native
// (frames carry view width == coverage width == the narrowed native —
// 50 kHz for the 25 kHz preset — and the span readout shows ±25k), and NO
// .scope-hatch in the adopted state. Measured on the integrated tree:
// before view=100000/cov=100000, after view=50000/cov=50000, span text
// ±25k, span writes 0, hatch 0.
//
// The must-hatch row pins an explicit NON-follow window wider than the
// radio's widest preset coverage — the 10 m band preset (28.0–29.7 MHz,
// 1.7 MHz) against the 500000 widest span preset (1 MHz full width) — so
// the uncovered margins hatch with reason "view wider than coverage". It
// asserts hatchCount > 0 on the rAF samples and that the wire frames show
// view (1 700 000 Hz) wider than coverage (1 000 000 Hz). The server
// legitimately re-picks the covering preset on the explicit subscribe
// (MOR-4452); even at the widest preset the view stays uncovered, so the
// hatch is stable, never a transient.
import type { Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame, parseScopeGranted } from '../../../src/lib/runtime/scope-frame';
import type { ScopeGranted, ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import { readScopeWriteReceipts } from '../journey/panorama-receipt';
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
import {
  checkAdvanceAndSettle,
  classifyHeldKeyCatch,
  requirePopulatedSeries,
} from './held-key-analysis';

/** One rAF sample of the held-key burst: VFO readout, marker geometry, the rendered view and the hatch count. */
interface HeldKeySample {
  tPerf: number;
  tWall: number;
  overlay: boolean;
  hatchCount: number;
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
        hatchCount: panel?.querySelectorAll('.scope-hatch').length ?? 0,
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
  frames: Array<{ view: number[]; coverage: number[]; receivedAt: number; tWall: number }>;
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

/**
 * Hot columns between two adjacent spectrum-canvas rows inside one view
 * edge strip. MOR-4451 canvas-hatch probe: the canvas hatch is a diagonal
 * line pattern (a 1px stroke every 8px at slope 1), so consecutive rows
 * differ at every line crossing; plain background, grid lines, labels and
 * the (absent, gapped) trace are all identical across two adjacent rows.
 * Returns -1 when the canvas is missing or unsized. Pixel sampling is the
 * robust read here because it observes the rendered output itself: it works
 * unchanged on the unfixed tree (where the panel exposes no hatch decision)
 * and after the fix, with no dependence on DOM the fix may reshape.
 */
async function canvasEdgeHotColumns(page: Page, side: 'left' | 'right'): Promise<number> {
  return await page.evaluate((edge) => {
    const canvas = document.querySelector('.spectrum-area canvas');
    if (!(canvas instanceof HTMLCanvasElement)) return -1;
    const ctx = canvas.getContext('2d');
    if (!ctx) return -1;
    const w = canvas.width, h = canvas.height;
    if (w < 40 || h < 20) return -1;
    const n = Math.max(8, Math.floor(w * 0.012));
    const x = edge === 'right' ? w - n - 2 : 2;
    const y = Math.floor(h * 0.47);
    const a = ctx.getImageData(x, y, n, 1).data;
    const b = ctx.getImageData(x, y + 1, n, 1).data;
    let hot = 0;
    for (let i = 0; i < a.length; i += 4) {
      if (Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]) > 24) hot++;
    }
    return hot;
  }, side);
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
        let serverReady = false;
        let server: JourneyServerHandle | undefined;
        // Declared before the try: the catch skip-rule reads frames, so it
        // must be visible there (a `const` inside the try is not).
        const sent: Array<ScopeSubscription & { type: string }> = [];
        const grants: Array<{ tWall: number; grant: ScopeGranted }> = [];
        const frames: Array<{ view: number[]; coverage: number[]; receivedAt: number; tWall: number }> = [];
        const setFreqCommands: Array<{ tWall: number; freqHz: number | null }> = [];
        try {
          server = await startJourneyServer(port, 14_250_000,
            ['--model', 'IC-705', '--evidence', evidence,
              '--response-delay', String(latency.delay),
              '--response-jitter', String(latency.jitter),
              '--span-hz', '250000',
              '--seed', String(seed)],
            'tests/e2e/panorama_web_server.py');
          serverReady = true;
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
                    tWall: Date.now(),
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
          const burstStartWall = Date.now();
          const burstStartPerf = await page.evaluate(() => performance.now());
          await page.keyboard.down('ArrowRight');
          for (let i = 0; i < 120; i++) {
            await page.keyboard.down('ArrowRight');
            await page.waitForTimeout(33);
          }
          await page.keyboard.up('ArrowRight');
          const burstEndWall = Date.now();
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
          // Every rendered series must be measured: a broken VFO selector or
          // a frozen viewport is trivially monotonic, so an empty, all-missing
          // or frozen series fails here instead of passing below. The marker
          // and the view centre must additionally travel with the burst and
          // rest at the confirmed frequency. These throw — never a skip.
          requirePopulatedSeries('vfo', vfo);
          requirePopulatedSeries('marker', markerSeries);
          requirePopulatedSeries('centre', center);
          // The marker is pixel-quantized: allow half a step plus one pixel.
          const pxScales = inWindow
            .filter((s) => s.gates !== null && Number.isFinite(s.gates.viewStartHz)
              && Number.isFinite(s.gates.viewEndHz) && Number.isFinite(s.areaWidth) && s.areaWidth > 0)
            .map((s) => (s.gates!.viewEndHz - s.gates!.viewStartHz) / s.areaWidth);
          const hzPerPx = pxScales.length > 0 ? pxScales[pxScales.length - 1] : grantedWidthHz / 800;
          const markerAdvance = checkAdvanceAndSettle({
            name: 'marker', values: inWindow.map((s) => ({ t: s.tPerf, v: markerFrequency(s) })),
            confirmedHz: afterHz, toleranceHz: toleranceHz + hzPerPx, minimumAdvanceHz: stepHz,
          });
          const centreAdvance = checkAdvanceAndSettle({
            name: 'centre', values: inWindow.map((s) => ({ t: s.tPerf, v: viewCenterHz(s) })),
            confirmedHz: afterHz, toleranceHz, minimumAdvanceHz: stepHz,
          });
          // Burst-window transient populations (partials counted for the log
          // line only — NOT required here, see the header).
          const burstSamples = inWindow.filter((s) => s.tPerf >= burstStartPerf && s.tPerf <= burstEndPerf);
          const burstPartials = frames.filter((f) => f.tWall >= burstStartWall && f.tWall <= burstEndWall
            && (f.view[1] - f.view[0]) - (f.coverage[1] - f.coverage[0]) > 1);
          const hatchedBurst = burstSamples.filter((s) => s.hatchCount > 0);
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
          const burstWallFrames = frames.filter((f) => f.tWall >= burstStartWall && f.tWall <= burstEndWall);
          const burstCovMin = Math.min(...burstWallFrames.map((f) => f.coverage[1] - f.coverage[0]));
          const burstViewMin = Math.min(...burstWallFrames.map((f) => f.view[1] - f.view[0]));
          console.log(`[held-key ${zoom.name} ${latency.name} step=${stepHz}] ` +
            `vfo maxBackward=${vfo.maxBackwardHz}Hz marker maxBackward=${markerSeries.maxBackwardHz}Hz ` +
            `center maxBackward=${center.maxBackwardHz}Hz longGaps=${longGaps.length} ` +
            `marker advance=${markerAdvance.advanceHz}Hz settle=${markerAdvance.settleHz}Hz ` +
            `centre advance=${centreAdvance.advanceHz}Hz settle=${centreAdvance.settleHz}Hz ` +
            `partials=${burstPartials.length} burstCovMin=${burstCovMin}Hz burstViewMin=${burstViewMin}Hz ` +
            `before=${beforeHz} after=${afterHz} setFreq=${setFreqCommands.length}`);

          // (t) transient seam: no margin may hatch during the burst while the
          // native window covers the requested view. Partial coverage is NOT
          // required here (see the header: bursts stay concentric, so zero
          // partials are legitimate) — the seam contract stays pinned by the
          // unit suites (see the header).
          expect(hatchedBurst,
            `transient margin hatched in ${hatchedBurst.length} burst samples`).toEqual([]);

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
          // server never starts, or no scope frames arrive at all within
          // the existing timeout. A setup expectation that fails after
          // frames flow is a FAILURE with the original error, never a skip.
          const verdict = classifyHeldKeyCatch({
            setupDone, serverReady, framesObserved: frames.length, error,
            row: `${zoom.name}, ${latency.name} delay=${latency.delay}s ` +
              `jitter=${latency.jitter}s, step ${stepHz}Hz`,
          });
          if (verdict.disposition === 'skip') {
            test.skip(true, verdict.reason);
            return;
          }
          throw verdict.error;
        } finally {
          await server?.kill();
        }
      });
    }
  }
}

// Must-hatch row: an explicit NON-follow window wider than the radio's
// widest preset coverage MUST hatch. The VFO starts at 28.5 MHz and the
// 10 m band preset pins the 28.0–29.7 MHz window (1.7 MHz); the server
// legitimately re-picks the covering preset on the explicit subscribe
// (MOR-4452), but even the widest 500000 preset only covers 1 MHz, so the
// uncovered margins keep hatching with reason "view wider than coverage".
// Asserts hatchCount > 0 on the rAF samples and that the wire frames show
// view (1 700 000 Hz) wider than coverage (1 000 000 Hz). Needs the same
// integrated tree as the rows above.
test('IC-705 pinned 10m band preset wider than any native stays hatched', async ({ page }, info) => {
  test.setTimeout(180_000);
  const port = 21598;
  const seed = 1234;
  const freqHz = 28_500_000;
  const bandView = { startHz: 28_000_000, endHz: 29_700_000 };
  const evidence = info.outputPath('radio.jsonl');
  let setupDone = false;
  let serverReady = false;
  let server: JourneyServerHandle | undefined;
  const sent: Array<ScopeSubscription & { type: string }> = [];
  const grants: Array<{ tWall: number; grant: ScopeGranted }> = [];
  const frames: Array<{ view: number[]; coverage: number[]; receivedAt: number; tWall: number }> = [];
  try {
    server = await startJourneyServer(port, freqHz,
      ['--model', 'IC-705', '--evidence', evidence,
        '--seed', String(seed)],
      'tests/e2e/panorama_web_server.py');
    serverReady = true;
    const watch = watchPanoramaErrors(page);
    page.on('websocket', (socket) => {
      if (new URL(socket.url()).pathname === '/api/v1/ws') return;
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
              tWall: Date.now(),
            });
          }
        }
      });
    });
    await installHeldKeySampler(page);
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
    const marker = page.locator('.waterfall-content .tune-line');
    await expect(marker).toBeVisible({ timeout: 30_000 });

    // CTR (CENTER) scope mode, same as the matrix rows.
    const ctr = page.getByRole('radio', { name: 'CTR' }).first();
    if (!(await ctr.isChecked())) await ctr.click();
    await expect(ctr).toBeChecked();

    // Pin the 10 m band preset: the first preset option after the
    // placeholder. The VFO (28.5 MHz) stays inside the pinned window, so
    // the pin holds (MOR-4387: the pin retires on position, and no tuning
    // happens in this row).
    await page.getByRole('button', { name: 'More scope controls' }).first().click();
    await page.locator('[data-testid="scope-more-panel"] [data-testid="scope-preset-select"]')
      .selectOption({ index: 1 });
    await page.keyboard.press('Escape');
    await expect.poll(() => sent.at(-1)?.view, { timeout: 30_000 }).toEqual(bandView);
    setupDone = true;

    // Wait until the wire shows the pinned view wider than the delivered
    // coverage: the explicit subscribe re-picks the covering preset, but
    // even the widest 500000 preset only covers 1 MHz of the 1.7 MHz view.
    await expect.poll(() => {
      const latest = frames.at(-1);
      if (!latest) return null;
      return [latest.view[1] - latest.view[0], latest.coverage[1] - latest.coverage[0]];
    }, { timeout: 120_000 }).toEqual([bandView.endHz - bandView.startHz, 1_000_000]);
    const stableWall = Date.now();
    await page.waitForTimeout(2000);

    // The stable gapped state above proves the wire side; the hatch must be
    // visible on the rendered samples too.
    const samples = await page.evaluate(() => window.heldKeySamples);
    const stableSamples = samples.filter((s) => s.tWall >= stableWall);
    const hatched = stableSamples.filter((s) => s.hatchCount > 0);
    const latest = frames.at(-1)!;
    const latestViewHz = latest.view[1] - latest.view[0];
    const latestCovHz = latest.coverage[1] - latest.coverage[0];
    console.log(`[held-key must-hatch band-10m] ` +
      `samples=${stableSamples.length} hatched=${hatched.length} ` +
      `view=${latestViewHz}Hz cov=${latestCovHz}Hz`);
    await info.attach('held-key-must-hatch', {
      body: JSON.stringify({
        params: { freqHz, bandView, seed },
        latestView: latest.view, latestCoverage: latest.coverage,
        grants: grants.length, subscribes: sent.length,
        samples: stableSamples.length, hatched: hatched.length,
      }, null, 2),
      contentType: 'application/json',
    });

    // Positive proof the seam was exercised: the wire shows the pinned
    // 1.7 MHz view with only the 1 MHz widest-native coverage.
    expect(latestViewHz, 'pinned band view lost its width').toBe(bandView.endHz - bandView.startHz);
    expect(latestCovHz, 'coverage is not the widest preset coverage').toBe(1_000_000);
    expect(latestViewHz - latestCovHz,
      'wire shows no uncovered margin under the pinned view').toBeGreaterThan(1);
    // The uncovered margins ARE hatched, with the wider-than-coverage
    // reason — never plain background, never a transient.
    expect(stableSamples.length,
      'too few stable samples to judge the must-hatch').toBeGreaterThan(10);
    expect(hatched.length,
      `explicit view wider than coverage hatched only ${hatched.length}/${stableSamples.length} stable samples`).toBeGreaterThan(0);
    const hatch = page.locator('.spectrum-panel .scope-hatch').first();
    await expect(hatch).toBeVisible();
    await expect(hatch).toHaveAttribute('title', 'View wider than the delivered scope coverage');

    expectPanoramaSettled(watch);
  } catch (error) {
    const verdict = classifyHeldKeyCatch({
      setupDone, serverReady, framesObserved: frames.length, error,
      row: 'must-hatch (pinned 10m band view wider than the widest native)',
    });
    if (verdict.disposition === 'skip') {
      test.skip(true, verdict.reason);
      return;
    }
    throw verdict.error;
  } finally {
    await server?.kill();
  }
});

// Control rows: the owner rule of 2026-11-14 in BOTH event orders. The
// radio starts on the 50000 preset (100 kHz native, the default follow
// width) and the harness narrows the span preset to 25 kHz from the front
// panel (--external-span-after-s/--external-span-hz) AFTER setup, with no
// zoom presses after it. --external-span-skew-s staggers the two legs of
// that change deterministically: a negative skew switches the span readback
// first (the order the old code happened to survive), a positive skew
// switches the streamed wave first (the order that exposed MOR-4451: the
// clipped frames arrive before the span-index readback). Each row asserts
// NO radio span write after the change (MOR-4385 adoption: the grant
// clips, nothing is written back), that the displayed view TRACKS the
// narrowed native (frames carry view width == coverage width == 50 kHz
// and the span readout shows ±25k — with no zoom intent the view follows
// the wire, so there is no follow width to adopt), and NO .scope-hatch
// in the adopted state. No latency matrix.
// Needs the same integrated tree as the rows above.
for (const { order, skew, port } of [
  { order: 'readback-first', skew: -20, port: 21594 },
  { order: 'frames-first', skew: 20, port: 21595 },
] as const) {
test(`IC-705 external span narrowing adopts without a write and without hatch (control, ${order})`, async ({ page }, info) => {
  test.setTimeout(240_000);
  const seed = 1234;
  const externalAfterS = 90;
  const evidence = info.outputPath('radio.jsonl');
  let setupDone = false;
  let serverReady = false;
  let server: JourneyServerHandle | undefined;
  const sent: Array<ScopeSubscription & { type: string }> = [];
  const grants: Array<{ tWall: number; grant: ScopeGranted }> = [];
  const frames: Array<{ view: number[]; coverage: number[]; receivedAt: number; tWall: number }> = [];
  // Span-set receipts in the evidence file: 0x15 scope writes whose data
  // carries the 6-byte span value (a 1-byte 0x15 is a span read, not a
  // write). The simulator appends one line per write, same machine clock.
  const spanSetCount = async (): Promise<number> =>
    (await readScopeWriteReceipts(evidence))
      .filter((receipt) => receipt.sub === 0x15 && receipt.data.length === 6).length;
  // Total receipts, for the counter-liveness guard below: the initial
  // subscribe may legitimately write no span-set at all (the radio is
  // already on the covering preset), so spanSetsBefore == 0 is a valid
  // baseline — but an empty evidence file would make the no-new-writes
  // assertion below pass vacuously.
  const receiptCount = async (): Promise<number> =>
    (await readScopeWriteReceipts(evidence)).length;
  const startedWall = Date.now();
  try {
    server = await startJourneyServer(port, 14_250_000,
      ['--model', 'IC-705', '--evidence', evidence,
        '--span-hz', '50000',
        // Fires after setup so the snapshot below predates the change and
        // the adoption poll starts waiting BEFORE the switch. The skew
        // makes the readback/wave arrival order deterministic (see above).
        '--external-span-after-s', String(externalAfterS), '--external-span-hz', '25000',
        '--external-span-skew-s', String(skew),
        '--seed', String(seed)],
      'tests/e2e/panorama_web_server.py');
    serverReady = true;
    const watch = watchPanoramaErrors(page);
    page.on('websocket', (socket) => {
      if (new URL(socket.url()).pathname === '/api/v1/ws') return;
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
              tWall: Date.now(),
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

    // CTR (CENTER) scope mode, same as the burst rows.
    const ctr = page.getByRole('radio', { name: 'CTR' }).first();
    if (!(await ctr.isChecked())) await ctr.click();
    await expect(ctr).toBeChecked();

    await settlePassband(page, port);
    await page.waitForTimeout(1000);

    // The follow view comes from the server grant, never hard-coded: the
    // default 100 kHz follow against the 50000 preset's 100 kHz full width.
    await expect.poll(() => grants.length).toBeGreaterThan(0);
    const grantedView = grants.at(-1)!.grant.view;
    const grantedWidthHz = grantedView.endHz - grantedView.startHz;
    expect(grantedWidthHz, 'control row must start on the 100 kHz full width of the 50000 preset')
      .toBe(100_000);
    const expectedView = [BASE_FREQ_HZ - grantedWidthHz / 2, BASE_FREQ_HZ + grantedWidthHz / 2];
    await expect.poll(() => frames.at(-1)?.view).toEqual(expectedView);
    setupDone = true;

    // Snapshot the span-set receipts BEFORE the external change can fire:
    // setup must finish well inside the change delay, or the "no write
    // after the change" assertion below would compare against a
    // post-change baseline and pass vacuously.
    const setupElapsedS = (Date.now() - startedWall) / 1000;
    expect(setupElapsedS,
      `setup took ${setupElapsedS.toFixed(0)}s; the external change fires at ${externalAfterS}s`).toBeLessThan(externalAfterS - 15);
    // Every pre-change frame shows the full 100 kHz view covered: no zoom
    // presses happened and the change has not fired yet.
    expect(frames.length, 'no scope frames arrived during setup').toBeGreaterThan(0);
    expect(frames.every((f) => f.view[1] - f.view[0] === 100_000 && f.coverage[1] - f.coverage[0] === 100_000),
      'pre-change frames do not all show view=100000/cov=100000').toBe(true);
    const spanSetsBefore = await spanSetCount();
    expect(await receiptCount(),
      'evidence file is empty — the no-new-writes assertion would pass vacuously').toBeGreaterThan(0);

    // Wait until the displayed view adopts the narrowed native: frames
    // carry view width == coverage width == the 25 kHz preset's 50 kHz
    // full width, and the span readout follows.
    await expect.poll(() => {
      const latest = frames.at(-1);
      if (!latest) return null;
      return [latest.view[1] - latest.view[0], latest.coverage[1] - latest.coverage[0]];
    }, { timeout: 120_000 }).toEqual([50_000, 50_000]);
    const adoptedWall = Date.now();
    // The readout only changes once the lagging readback leg arrives and
    // the client adopts — up to |skew| after the wire narrowed — so the
    // budget must cover the stagger, not just the render.
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±25k', { timeout: 60_000 });
    await page.waitForTimeout(2500);

    const adoptedSamples = (await page.evaluate(() => window.heldKeySamples))
      .filter((s) => s.tWall >= adoptedWall);
    const hatchedAdopted = adoptedSamples.filter((s) => s.hatchCount > 0);
    const latest = frames.at(-1)!;
    const latestViewHz = latest.view[1] - latest.view[0];
    const latestCovHz = latest.coverage[1] - latest.coverage[0];
    const spanSetsAfter = await spanSetCount();
    console.log(`[held-key control ${order} owner-rule] ` +
      `samples=${adoptedSamples.length} hatched=${hatchedAdopted.length} ` +
      `view=${latestViewHz}Hz cov=${latestCovHz}Hz ` +
      `spanSetsBefore=${spanSetsBefore} spanSetsAfter=${spanSetsAfter}`);
    await info.attach(`held-key-control-${order}`, {
      body: JSON.stringify({
        params: { spanHz: 50_000, externalSpanHz: 25_000, seed, order, skew },
        grantedWidthHz, adoptedWall,
        latestView: latest.view, latestCoverage: latest.coverage,
        spanSetsBefore, spanSetsAfter,
        samples: adoptedSamples.length, hatched: hatchedAdopted.length,
      }, null, 2),
      contentType: 'application/json',
    });

    // (1) NO radio span write after the external change: the setup
    // snapshot predates the change and no gesture follows it, so any new
    // span-set receipt is a write-back.
    expect(spanSetsAfter,
      `radio span written ${spanSetsAfter - spanSetsBefore}x after the external change`).toBe(spanSetsBefore);
    // (2) the displayed view tracks the narrowed native: the wire frames
    // carry view width == coverage width == that width, while the follow
    // request itself is untouched by the external change (with no zoom
    // intent there is no follow width to adopt — the zoomed row below
    // pins the follow-subscription adoption).
    expect(sent.at(-1)?.view,
      `follow request disturbed by the external change (${order})`).toEqual({ follow: 'vfo', spanHz: 100_000 });
    expect(latestViewHz, 'adopted view is not the narrowed native width').toBe(50_000);
    expect(latestCovHz, 'adopted coverage is not the narrowed native width').toBe(50_000);
    // (3) NO hatch in the adopted state: a fully covered view never
    // hatches, even right after an external narrowing.
    expect(adoptedSamples.length,
      'too few adopted-state samples to judge the control hatch').toBeGreaterThan(10);
    expect(hatchedAdopted,
      `adopted fully-covered view hatched in ${hatchedAdopted.length} samples`).toEqual([]);

    expectPanoramaSettled(watch);
  } catch (error) {
    const verdict = classifyHeldKeyCatch({
      setupDone, serverReady, framesObserved: frames.length, error,
      row: `owner-rule control ${order} (external span 25kHz under a 100kHz follow view)`,
    });
    if (verdict.disposition === 'skip') {
      test.skip(true, verdict.reason);
      return;
    }
    throw verdict.error;
  } finally {
    await server?.kill();
  }
});
} // control order loop

// Zoomed adoption row (MOR-4451): the follow view is zoomed out twice to
// 400 kHz on the ±250k preset (span text ±200k) and the front panel narrows
// the span to ±100k AFTER setup, with no presses after it. The server clips
// the follow frames to the narrowed 200 kHz native first (resolve_follow)
// and the span-index readback follows 20 s later (frames-first skew), so on
// the unfixed tree the adoption guard — which demanded the frame view width
// to equal the follow width — misses the narrowing forever: the view stays
// 400 kHz and hatches. The row asserts the adopted state: the follow
// subscription moved to 200 kHz, frames carry view width == coverage width
// == 200 kHz, the span readout shows ±100k, zero span writes after the
// change, and zero .scope-hatch in the adopted rAF samples. The canvas
// half of the panel's hatch decision (the ranges handed to the canvas
// renderer) is pinned by the SpectrumPanel component adoption tests, where
// the adopted paint is deterministic — a pixel probe here would also see
// the live trace slope on the fully covered edge strips. Needs the same
// integrated tree as the rows above.
test('IC-705 zoomed follow adopts a front-panel narrowing without a write and without hatch', async ({ page }, info) => {
  test.setTimeout(300_000);
  const port = 21598;
  const seed = 1234;
  const externalAfterS = 100;
  const evidence = info.outputPath('radio.jsonl');
  let setupDone = false;
  let serverReady = false;
  let server: JourneyServerHandle | undefined;
  const sent: Array<ScopeSubscription & { type: string }> = [];
  const grants: Array<{ tWall: number; grant: ScopeGranted }> = [];
  const frames: Array<{ view: number[]; coverage: number[]; receivedAt: number; tWall: number }> = [];
  const spanSetCount = async (): Promise<number> =>
    (await readScopeWriteReceipts(evidence))
      .filter((receipt) => receipt.sub === 0x15 && receipt.data.length === 6).length;
  const receiptCount = async (): Promise<number> =>
    (await readScopeWriteReceipts(evidence)).length;
  const startedWall = Date.now();
  try {
    server = await startJourneyServer(port, 14_250_000,
      ['--model', 'IC-705', '--evidence', evidence,
        '--span-hz', '250000',
        // Frames-first order: the clipped wave arrives at the switch and
        // the span readback follows 20 s later — the order that reds on
        // the unfixed tree.
        '--external-span-after-s', String(externalAfterS), '--external-span-hz', '100000',
        '--external-span-skew-s', '20',
        '--seed', String(seed)],
      'tests/e2e/panorama_web_server.py');
    serverReady = true;
    const watch = watchPanoramaErrors(page);
    page.on('websocket', (socket) => {
      if (new URL(socket.url()).pathname === '/api/v1/ws') return;
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
              tWall: Date.now(),
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

    // CTR (CENTER) scope mode, same as the burst rows.
    const ctr = page.getByRole('radio', { name: 'CTR' }).first();
    if (!(await ctr.isChecked())) await ctr.click();
    await expect(ctr).toBeChecked();

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
    await expect.poll(() => {
      const grant = grants.at(-1)?.grant;
      return grant ? grant.view.endHz - grant.view.startHz : 0;
    }).toBe(400_000);
    const zoomedView = [BASE_FREQ_HZ - 200_000, BASE_FREQ_HZ + 200_000];
    await expect.poll(() => frames.at(-1)?.view).toEqual(zoomedView);
    // Frames before the zoom settled carry the narrower pre-zoom views —
    // the pre-change check below only covers frames from here on. Wait
    // for at least one post-zoom frame so the slice below is never
    // vacuously empty.
    const zoomedFrameIdx = frames.length;
    await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(zoomedFrameIdx);
    setupDone = true;

    // Snapshot the span-set receipts BEFORE the external change can fire:
    // setup must finish well inside the change delay, or the "no write
    // after the change" assertion below would compare against a
    // post-change baseline and pass vacuously.
    const setupElapsedS = (Date.now() - startedWall) / 1000;
    expect(setupElapsedS,
      `setup took ${setupElapsedS.toFixed(0)}s; the external change fires at ${externalAfterS}s`).toBeLessThan(externalAfterS - 15);
    expect(frames.length, 'no scope frames arrived during setup').toBeGreaterThan(zoomedFrameIdx);
    expect(frames.slice(zoomedFrameIdx).every((f) => f.view[1] - f.view[0] === 400_000 && f.coverage[1] - f.coverage[0] === 400_000),
      'pre-change frames do not all show view=400000/cov=400000').toBe(true);
    const spanSetsBefore = await spanSetCount();
    expect(await receiptCount(),
      'evidence file is empty — the no-new-writes assertion would pass vacuously').toBeGreaterThan(0);

    // Wait until the narrowed native reaches the wire: the server clips
    // the follow frames to the 200 kHz native (view == coverage == 200k).
    // The wire clips on the unfixed tree too — the follow-subscription
    // assertion below is the one that reds there.
    await expect.poll(() => {
      const latest = frames.at(-1);
      if (!latest) return null;
      return [latest.view[1] - latest.view[0], latest.coverage[1] - latest.coverage[0]];
    }, { timeout: 180_000 }).toEqual([200_000, 200_000]);
    // Adoption is the follow subscription moving to the narrowed width:
    // only from here is the view adopted. (The readout already shows
    // ±100k from the clipped frame width, on either tree — it is not the
    // adoption signal. On the unfixed tree this poll never fires: that
    // timeout is the red proof.)
    await expect.poll(() => sent.at(-1)?.view, { timeout: 60_000 })
      .toEqual({ follow: 'vfo', spanHz: 200_000 });
    const adoptedWall = Date.now();
    await expect(page.getByTestId('scope-span-value').first()).toHaveText('±100k');
    await page.waitForTimeout(2500);

    const adoptedSamples = (await page.evaluate(() => window.heldKeySamples))
      .filter((s) => s.tWall >= adoptedWall);
    const hatchedAdopted = adoptedSamples.filter((s) => s.hatchCount > 0);
    const latest = frames.at(-1)!;
    const latestViewHz = latest.view[1] - latest.view[0];
    const latestCovHz = latest.coverage[1] - latest.coverage[0];
    const spanSetsAfter = await spanSetCount();
    console.log(`[held-key zoomed adoption] ` +
      `samples=${adoptedSamples.length} hatched=${hatchedAdopted.length} ` +
      `view=${latestViewHz}Hz cov=${latestCovHz}Hz ` +
      `spanSetsBefore=${spanSetsBefore} spanSetsAfter=${spanSetsAfter}`);
    await info.attach('held-key-zoomed-adoption', {
      body: JSON.stringify({
        params: { spanHz: 250_000, externalSpanHz: 100_000, skew: 20, seed },
        latestView: latest.view, latestCoverage: latest.coverage,
        spanSetsBefore, spanSetsAfter,
        samples: adoptedSamples.length, hatched: hatchedAdopted.length,
      }, null, 2),
      contentType: 'application/json',
    });

    // (1) NO radio span write after the external change: the setup
    // snapshot predates the change and no gesture follows it, so any new
    // span-set receipt is a write-back.
    expect(spanSetsAfter,
      `radio span written ${spanSetsAfter - spanSetsBefore}x after the external change`).toBe(spanSetsBefore);
    // (2) the zoomed follow view adopted the narrowed native: the follow
    // subscription itself moved from 400 kHz to the 200 kHz native (pinned
    // by the adoption poll above), and the wire frames carry view width
    // == coverage width == that width.
    expect(sent.at(-1)?.view,
      'zoomed follow subscription did not adopt the narrowed native').toEqual({ follow: 'vfo', spanHz: 200_000 });
    expect(latestViewHz, 'adopted view is not the narrowed native width').toBe(200_000);
    expect(latestCovHz, 'adopted coverage is not the narrowed native width').toBe(200_000);
    // (3) NO hatch in the adopted state: a fully covered view never
    // hatches. The canvas half of the panel's hatch decision (the ranges
    // handed to the canvas renderer) is pinned at the component level,
    // where it is deterministic: a pixel probe here would also see the
    // live trace slope on the fully covered edge strips.
    expect(adoptedSamples.length,
      'too few adopted-state samples to judge the zoomed adoption hatch').toBeGreaterThan(10);
    expect(hatchedAdopted,
      `adopted fully-covered view hatched in ${hatchedAdopted.length} samples`).toEqual([]);

    expectPanoramaSettled(watch);
  } catch (error) {
    const verdict = classifyHeldKeyCatch({
      setupDone, serverReady, framesObserved: frames.length, error,
      row: 'zoomed adoption (external span 100kHz under a 400kHz follow view)',
    });
    if (verdict.disposition === 'skip') {
      test.skip(true, verdict.reason);
      return;
    }
    throw verdict.error;
  } finally {
    await server?.kill();
  }
});

// MOR-4451 Filter Center rows (the owner's build-7 defect, real IC-705 in
// LSB with scope center type 0): idle CTR with the simulator's scope center
// type at Filter Center (--scope-center-type 0), LSB and USB. The streamed
// wave centres on the passband centre (3000 Hz filter ⇒ 1500 Hz off the
// VFO), so the radio native stays at the requested width while the wire
// frames carry the offset intersection (1500 Hz narrower on one side). An
// offset-but-equal-width native is a transient lag, never a
// hatch: the rows assert no hatch in the DOM (.scope-hatch = 0) AND none on
// the canvas (pixel-sampled edge strip, see canvasEdgeHotColumns). Needs the
// same integrated tree as the rows above.
for (const { mode, port, stripSide } of [
  { mode: 'LSB', port: 21596, stripSide: 'right' },
  { mode: 'USB', port: 21597, stripSide: 'left' },
] as const) {
  test(`IC-705 Filter Center ${mode} idle CTR shows offset coverage at equal width with no hatch`, async ({ page }, info) => {
    test.setTimeout(180_000);
    const seed = 1234;
    const evidence = info.outputPath('radio.jsonl');
    let setupDone = false;
    let serverReady = false;
    let server: JourneyServerHandle | undefined;
    const sent: Array<ScopeSubscription & { type: string }> = [];
    const grants: Array<{ tWall: number; grant: ScopeGranted }> = [];
    const frames: Array<{ view: number[]; coverage: number[]; receivedAt: number; tWall: number }> = [];
    try {
      server = await startJourneyServer(port, 14_250_000,
        ['--model', 'IC-705', '--evidence', evidence,
          '--span-hz', '50000',
          '--scope-center-type', '0',
          '--seed', String(seed)],
        'tests/e2e/panorama_web_server.py');
      serverReady = true;
      const watch = watchPanoramaErrors(page);
      page.on('websocket', (socket) => {
        if (new URL(socket.url()).pathname === '/api/v1/ws') return;
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
                tWall: Date.now(),
              });
            }
          }
        });
      });
      await installHeldKeySampler(page);
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
      const marker = page.locator('.waterfall-content .tune-line');
      await expect(marker).toBeVisible({ timeout: 30_000 });

      // CTR (CENTER) scope mode — the owner's failing shape.
      const ctr = page.getByRole('radio', { name: 'CTR' }).first();
      if (!(await ctr.isChecked())) await ctr.click();
      await expect(ctr).toBeChecked();

      // Sideband + 3000 Hz filter: the passband centre sits 1500 Hz off the
      // VFO, so Filter Center streams the wave 1500 Hz off at equal width.
      const serverState = async () => (await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json()) as {
        main?: { freqHz?: number; mode?: string; filterWidth?: number };
      };
      await expect.poll(async () => (await serverState()).main?.filterWidth, { timeout: 30_000 }).toBe(2400);
      await page.locator(`[data-testid="standard-mode-${mode}"]`).getByRole('radio').click();
      await page.locator('[data-testid="filter-width"] input[type=range]').fill('3000');
      await expect.poll(serverState).toMatchObject({ main: { mode, filterWidth: 3000 } });
      await expect(page.locator('.waterfall-content .passband-overlay')).toBeVisible({ timeout: 30_000 });
      setupDone = true;

      // The follow view comes from the server grant, never hard-coded: the
      // default 100 kHz follow against the 50000 preset's 100 kHz full
      // width. Frames must carry exactly that view.
      await expect.poll(() => grants.length).toBeGreaterThan(0);
      const grantedView = grants.at(-1)!.grant.view;
      const grantedWidthHz = grantedView.endHz - grantedView.startHz;
      expect(grantedWidthHz, 'Filter Center row must run on the 100 kHz full width of the 50000 preset')
        .toBe(100_000);
      const expectedView = [BASE_FREQ_HZ - grantedWidthHz / 2, BASE_FREQ_HZ + grantedWidthHz / 2];
      await expect.poll(() => frames.at(-1)?.view).toEqual(expectedView);

      // The seam is exercised: the radio native stays 100 kHz wide (equal
      // to the requested view) while the delivered wire coverage is the
      // offset intersection — 1500 Hz narrower on the uncovered side. LSB
      // uncovers the right strip, USB the left strip.
      await expect.poll(() => {
        const latest = frames.at(-1);
        if (!latest) return null;
        const viewHz = latest.view[1] - latest.view[0];
        const covHz = latest.coverage[1] - latest.coverage[0];
        const stripHz = stripSide === 'right'
          ? latest.view[1] - latest.coverage[1]
          : latest.coverage[0] - latest.view[0];
        return [viewHz, covHz, Math.round(stripHz)];
      }, { timeout: 120_000 }).toEqual([100_000, 98_500, 1500]);
      const stableWall = Date.now();
      await page.waitForTimeout(2000);

      // (1) No DOM hatch: the offset-but-equal-width native never hatches.
      const stableSamples = (await page.evaluate(() => window.heldKeySamples))
        .filter((s) => s.tWall >= stableWall);
      const hatchedStable = stableSamples.filter((s) => s.hatchCount > 0);
      const latest = frames.at(-1)!;
      const hot = await canvasEdgeHotColumns(page, stripSide);
      console.log(`[held-key filter-center ${mode}] ` +
        `samples=${stableSamples.length} hatched=${hatchedStable.length} hot=${hot} ` +
        `view=[${latest.view}] cov=[${latest.coverage}]`);
      await info.attach(`held-key-filter-center-${mode}`, {
        body: JSON.stringify({
          params: { mode, spanHz: 50_000, scopeCenterType: 0, seed },
          latestView: latest.view, latestCoverage: latest.coverage,
          grants: grants.length, subscribes: sent.length,
          samples: stableSamples.length, hatched: hatchedStable.length, hot,
        }, null, 2),
        contentType: 'application/json',
      });
      expect(stableSamples.length,
        'too few stable samples to judge the Filter Center hatch').toBeGreaterThan(10);
      expect(hatchedStable,
        `offset coverage at equal width hatched the DOM in ${hatchedStable.length} samples`).toEqual([]);
      // (2) No canvas hatch on the uncovered edge strip: plain background,
      // the waterfall's treatment of the same transient lag.
      expect(hot,
        `canvas edge strip shows the hatch pattern (hot columns: ${hot}); ` +
        `the uncovered ${stripSide} strip must stay plain background`).toBe(0);

      expectPanoramaSettled(watch);
    } catch (error) {
      const verdict = classifyHeldKeyCatch({
        setupDone, serverReady, framesObserved: frames.length, error,
        row: `Filter Center ${mode} idle CTR (offset coverage at equal width, no hatch)`,
      });
      if (verdict.disposition === 'skip') {
        test.skip(true, verdict.reason);
        return;
      }
      throw verdict.error;
    } finally {
      await server?.kill();
    }
  });
}
