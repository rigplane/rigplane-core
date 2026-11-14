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
import type { Page, TestInfo } from '@playwright/test';
import { createReadStream } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { parseScopeWriteReceipt } from './panorama-receipt';
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

/** Per-event context for one backward move or passband gap (round-2 analysis). */
interface MoveContext {
  held: boolean;
  inflightFreqHz: number | null;
  inflightAgeMs: number | null;
  confirmedHz: number | null;
  gatesFreqHz: number | null;
  tupleState: string | null;
  grantsNear: Array<{ dtMs: number; view: unknown; coverage: unknown }>;
  subscribesNear: Array<{ dtMs: number; view: unknown }>;
}

interface BackwardMoveDetail extends BackwardMove {
  tWall: number;
  context: MoveContext;
}

interface MonotonicReport {
  first: BackwardMove | null;
  maxBackwardHz: number;
  samples: number;
  missing: number;
  all?: BackwardMoveDetail[];
}

interface PassbandGapDetail {
  startRelMs: number;
  frames: number;
  durationMs: number;
  gateDiff: Record<string, unknown>;
  context?: MoveContext;
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
    responseReorderP?: number;
    responseDuplicateP?: number;
    viewKind?: string;
    holdIterations?: number;
  };
  burst: { startPerf: number; endPerf: number; settleEndPerf: number };
  setup: {
    spanValue: string | null;
    grantedView: unknown;
    framesView: number[] | null;
    beforeHz: number;
    afterHz: number;
    framesCoverage?: number[] | null;
    coverageWidthHz?: number | null;
    spanWrites?: Array<{ sub: number; span: number }>;
  };
  monotonic: {
    toleranceHz: number;
    vfo: MonotonicReport;
    marker: MonotonicReport;
    center: MonotonicReport;
  };
  passband: {
    gaps: PassbandGapDetail[];
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
      test.skip(process.env.HELD_KEY_ROUND1 === '1',
        'round-1 matrix (4 s hold); set HELD_KEY_ROUND1=1 to run');
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
        await writeFile(info.outputPath('held-key-timeline.json'), JSON.stringify(timeline, null, 2));
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

// ---------------------------------------------------------------------------
// Round 2 (owner conditions 2026-11-14): 9 s hold at ~30 Hz auto-repeat,
// the owner's latency shapes (0.2+0.1, 0.35+0.15, 0.5+0.2, plus one
// 0.35+0.15 case with reorder/duplicate 0.05), on two cropped views:
// ±200k (400 kHz follow, covering preset stays 250000) and ±125k
// (250 kHz follow via span control to ±250k + one ZOOM+: 250 kHz is not a
// radio preset width, so the covering preset stays 250000 and the request
// is a pure client crop). 100 Hz steps run only for 0.35+0.15/±200k.
// Every backward move and every passband gap carries its in-hold/in-flight
// context in the attached timeline. The round-1 assertions are unchanged.
// ---------------------------------------------------------------------------

const ROUND2_BASE_PORT = 18980;
const ROUND2_HOLD_ITERATIONS = 270; // ~9 s at ~30 Hz auto-repeat
const ROUND2_SETTLE_MS = 3000;

const ROUND2_LATENCIES = [
  { name: 'd200ms-j100ms', delay: 0.2, jitter: 0.1, reorder: 0, duplicate: 0 },
  { name: 'd350ms-j150ms', delay: 0.35, jitter: 0.15, reorder: 0, duplicate: 0 },
  { name: 'd500ms-j200ms', delay: 0.5, jitter: 0.2, reorder: 0, duplicate: 0 },
  { name: 'd350ms-j150ms-r5-d5', delay: 0.35, jitter: 0.15, reorder: 0.05, duplicate: 0.05 },
] as const;

interface Round2View {
  name: string;
  spanText: string;
  spanHz: number;
  frameView: [number, number];
}

const ROUND2_VIEWS: Round2View[] = [
  { name: 'pm200k', spanText: '±200k', spanHz: 400_000, frameView: [14_050_000, 14_450_000] },
  { name: 'pm125k', spanText: '±125k', spanHz: 250_000, frameView: [14_125_000, 14_375_000] },
];

interface Round2CaseDef {
  latency: (typeof ROUND2_LATENCIES)[number];
  view: Round2View;
  stepHz: number;
}

const ROUND2_CASES: Round2CaseDef[] = [];
for (const latency of ROUND2_LATENCIES) {
  for (const view of ROUND2_VIEWS) {
    // ONE faults case (pm200k): it isolates the reorder/duplicate variable
    // against the plain 0.35+0.15/pm200k case.
    if (latency.name === 'd350ms-j150ms-r5-d5' && view.name === 'pm125k') continue;
    ROUND2_CASES.push({ latency, view, stepHz: 1_000 });
  }
}
ROUND2_CASES.push({ latency: ROUND2_LATENCIES[1]!, view: ROUND2_VIEWS[0]!, stepHz: 100 });

/** 0x15 receipts from the simulator evidence: what the radio was asked for. */
async function readSpanWrites(evidencePath: string): Promise<Array<{ sub: number; span: number }>> {
  let contents = '';
  for await (const chunk of createReadStream(evidencePath, { encoding: 'utf8' })) contents += chunk;
  const out: Array<{ sub: number; span: number }> = [];
  for (const line of contents.trim().split('\n')) {
    if (!line.trim()) continue;
    try {
      const receipt = parseScopeWriteReceipt(line);
      if (receipt.sub === 0x15) out.push({ sub: receipt.sub, span: receipt.span });
    } catch {
      // The evidence interleaves with the run; skip a partial trailing line.
    }
  }
  return out;
}

/** Running-max check that also keeps every backward move with its sample. */
function checkMonotonicFull(
  values: Array<{ t: number; tWall: number; v: number | null; sample: HeldKeySample }>,
  fromPerf: number,
  tolHz: number,
): {
  first: BackwardMove | null; maxBackwardHz: number; samples: number; missing: number;
  all: Array<{ tRelMs: number; tWall: number; valueHz: number; prevMaxHz: number; sample: HeldKeySample }>;
} {
  let max = -Infinity;
  let first: BackwardMove | null = null;
  let maxBackwardHz = 0;
  let samples = 0;
  let missing = 0;
  const all: Array<{ tRelMs: number; tWall: number; valueHz: number; prevMaxHz: number; sample: HeldKeySample }> = [];
  for (const { t, tWall, v, sample } of values) {
    if (v === null || !Number.isFinite(v)) {
      missing += 1;
      continue;
    }
    samples += 1;
    if (v < max - tolHz) {
      maxBackwardHz = Math.max(maxBackwardHz, max - v);
      const move = { tRelMs: t - fromPerf, valueHz: v, prevMaxHz: max };
      first ??= move;
      all.push({ ...move, tWall, sample });
    } else if (v > max) {
      max = v;
    }
  }
  return { first, maxBackwardHz, samples, missing, all };
}

function buildMoveContext(
  tPerf: number,
  tWall: number,
  sample: HeldKeySample | null,
  burstEndPerf: number,
  inWindow: HeldKeySample[],
  setFreqCommands: Array<{ tWall: number; freqHz: number | null }>,
  grants: Array<{ tWall: number; grant: ScopeGranted }>,
  sent: Array<ScopeSubscription & { type: string }>,
  subTimes: number[],
): MoveContext {
  let inflight: { tWall: number; freqHz: number | null } | null = null;
  for (const cmd of setFreqCommands) {
    if (cmd.tWall <= tWall) inflight = cmd;
    else break;
  }
  let confirmed: number | null = null;
  for (let i = inWindow.length - 1; i >= 0; i--) {
    const s = inWindow[i]!;
    if (s.tPerf > tPerf) continue;
    const v = parseVfoText(s.vfoText);
    if (v !== null) {
      confirmed = v;
      break;
    }
  }
  const grantsNear = grants
    .filter((g) => Math.abs(g.tWall - tWall) <= 200)
    .slice(-5)
    .map((g) => ({ dtMs: g.tWall - tWall, view: g.grant.view, coverage: g.grant.coverage }));
  const subscribesNear: Array<{ dtMs: number; view: unknown }> = [];
  for (let i = 0; i < sent.length; i++) {
    const dtMs = subTimes[i]! - tWall;
    if (Math.abs(dtMs) <= 200) subscribesNear.push({ dtMs, view: sent[i]!.view });
  }
  return {
    held: tPerf <= burstEndPerf,
    inflightFreqHz: inflight?.freqHz ?? null,
    inflightAgeMs: inflight === null ? null : tWall - inflight.tWall,
    confirmedHz: confirmed,
    gatesFreqHz: sample?.gates?.freqHz ?? null,
    tupleState: sample?.state ?? null,
    grantsNear,
    subscribesNear: subscribesNear.slice(-5),
  };
}

async function runRound2Case(page: Page, info: TestInfo, def: Round2CaseDef, port: number): Promise<void> {
  const { latency, view, stepHz } = def;
  test.setTimeout(150_000);
  const seed = 1234;
  const evidence = await info.outputPath('radio.jsonl');
  const server = await startJourneyServer(port, 14_250_000,
    ['--model', 'IC-705', '--evidence', evidence,
      '--response-delay', String(latency.delay),
      '--response-jitter', String(latency.jitter),
      '--response-reorder', String(latency.reorder),
      '--response-duplicate', String(latency.duplicate),
      '--span-hz', '250000',
      '--seed', String(seed)],
    'tests/e2e/panorama_web_server.py');
  const watch = watchPanoramaErrors(page);
  try {
    const sent: Array<ScopeSubscription & { type: string }> = [];
    const subTimes: number[] = [];
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
          if (message.type === 'subscribe') {
            sent.push(message);
            subTimes.push(Date.now());
          }
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

    if (view.name === 'pm200k') {
      // View zoomed to ±200 kHz: two ZOOM− steps from the 100 kHz default
      // follow width. The smallest covering radio preset stays 250000, so
      // this is a pure client crop with no span change of its own.
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      await page.getByTestId('scope-zoom-out').first().click();
      await page.getByTestId('scope-zoom-out').first().click();
      await page.keyboard.press('Escape');
    } else {
      // ±125 kHz: span control up to ±250k (500 kHz follow), then one ZOOM+.
      // 250 kHz is not a radio preset width and its covering preset is
      // 250000, so the radio span never moves and the request stays a crop.
      await page.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
      await page.getByRole('button', { name: 'Increase scope span', exact: true }).first().click();
      await expect(page.getByTestId('scope-span-value').first()).toHaveText('±250k');
      await expect.poll(() => sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 500_000 });
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      await page.getByTestId('scope-zoom-in').first().click();
      await page.keyboard.press('Escape');
    }
    await expect.poll(() => sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: view.spanHz });
    await expect(page.getByTestId('scope-span-value').first()).toHaveText(view.spanText);
    await expect.poll(() => frames.at(-1)?.view).toEqual([...view.frameView]);
    // The radio span stays 250000 (500 kHz native coverage around the VFO).
    await expect.poll(() => frames.at(-1)?.coverage).toEqual([14_000_000, 14_500_000]);

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
    const coverageBefore = frames.at(-1)?.coverage ?? null;
    const beforeHz = await radioFrequency(page, port);

    // The owner's gesture, extended: hold ArrowRight for ~9 s. Repeated
    // down() dispatches keydown with repeat=true while the key stays held.
    const burstStartPerf = await page.evaluate(() => performance.now());
    await page.keyboard.down('ArrowRight');
    for (let i = 0; i < ROUND2_HOLD_ITERATIONS; i++) {
      await page.keyboard.down('ArrowRight');
      await page.waitForTimeout(33);
    }
    await page.keyboard.up('ArrowRight');
    const burstEndPerf = await page.evaluate(() => performance.now());
    await page.waitForTimeout(ROUND2_SETTLE_MS);
    const settleEndPerf = await page.evaluate(() => performance.now());
    const afterHz = await radioFrequency(page, port);

    // The burst must have moved the VFO at all — otherwise monotonicity
    // and overlay continuity below would pass vacuously.
    expect(afterHz, 'held-key burst did not move the VFO; the stimulus is invalid').toBeGreaterThan(beforeHz);

    const samples = await page.evaluate(() => window.heldKeySamples);
    const passbandSamples = await page.evaluate(() => window.passbandSamples);
    const inWindow = samples.filter((s) => s.tPerf >= burstStartPerf && s.tPerf <= settleEndPerf);
    const toleranceHz = stepHz / 2;
    const seriesOf = (fn: (s: HeldKeySample) => number | null) =>
      inWindow.map((s) => ({ t: s.tPerf, tWall: s.tWall, v: fn(s), sample: s }));
    const toDetails = (
      moves: Array<{ tRelMs: number; tWall: number; valueHz: number; prevMaxHz: number; sample: HeldKeySample }>,
    ): BackwardMoveDetail[] => moves.map((m) => ({
      tRelMs: m.tRelMs,
      valueHz: m.valueHz,
      prevMaxHz: m.prevMaxHz,
      tWall: m.tWall,
      context: buildMoveContext(m.tRelMs + burstStartPerf, m.tWall, m.sample,
        burstEndPerf, inWindow, setFreqCommands, grants, sent, subTimes),
    }));
    const vfoFull = checkMonotonicFull(seriesOf((s) => parseVfoText(s.vfoText)), burstStartPerf, toleranceHz);
    const markerFull = checkMonotonicFull(seriesOf(markerFrequency), burstStartPerf, toleranceHz);
    const centerFull = checkMonotonicFull(seriesOf(viewCenterHz), burstStartPerf, toleranceHz);
    const vfoAll = toDetails(vfoFull.all);
    const markerAll = toDetails(markerFull.all);
    const centerAll = toDetails(centerFull.all);
    const vfo = { first: vfoFull.first, maxBackwardHz: vfoFull.maxBackwardHz, samples: vfoFull.samples, missing: vfoFull.missing, all: vfoAll };
    const markerSeries = { first: markerFull.first, maxBackwardHz: markerFull.maxBackwardHz, samples: markerFull.samples, missing: markerFull.missing, all: markerAll };
    const center = { first: centerFull.first, maxBackwardHz: centerFull.maxBackwardHz, samples: centerFull.samples, missing: centerFull.missing, all: centerAll };

    const gaps = passbandGaps(passbandSamples, burstStartPerf, settleEndPerf);
    const nearestSample = (tPerf: number): HeldKeySample | null => {
      let best: HeldKeySample | null = null;
      let bestDt = Infinity;
      for (const s of inWindow) {
        const dt = Math.abs(s.tPerf - tPerf);
        if (dt < bestDt) {
          bestDt = dt;
          best = s;
        }
      }
      return best;
    };
    const gapDetails: PassbandGapDetail[] = gaps.map((gap) => {
      const at = nearestSample(gap.startMs);
      return {
        startRelMs: gap.startMs - burstStartPerf,
        frames: gap.frames,
        durationMs: gap.durationMs,
        gateDiff: gateDiff(gap),
        context: buildMoveContext(gap.startMs, at?.tWall ?? Date.now(), at,
          burstEndPerf, inWindow, setFreqCommands, grants, sent, subTimes),
      };
    });
    const longGaps = gapDetails.filter((gap) => gap.durationMs > 200);

    const spanText = await page.getByTestId('scope-span-value').first().innerText().catch(() => null);
    const spanWrites = await readSpanWrites(evidence);
    const coverageWidthHz = coverageBefore === null ? null : coverageBefore[1]! - coverageBefore[0]!;
    const timeline: HeldKeyTimeline = {
      params: {
        latency: latency.name, responseDelayS: latency.delay, responseJitterS: latency.jitter,
        stepHz, seed, spanHz: 250_000, freqHz: 14_250_000,
        responseReorderP: latency.reorder, responseDuplicateP: latency.duplicate,
        viewKind: view.name, holdIterations: ROUND2_HOLD_ITERATIONS,
      },
      burst: { startPerf: burstStartPerf, endPerf: burstEndPerf, settleEndPerf },
      setup: {
        spanValue: spanText, grantedView: sent.at(-1)?.view ?? null,
        framesView: frames.at(-1)?.view ?? null, beforeHz, afterHz,
        framesCoverage: coverageBefore, coverageWidthHz, spanWrites,
      },
      monotonic: { toleranceHz, vfo, marker: markerSeries, center },
      passband: { gaps: gapDetails },
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
    await writeFile(await info.outputPath('held-key-timeline.json'), JSON.stringify(timeline, null, 2));

    const split = (all: BackwardMoveDetail[]) => ({
      during: all.filter((d) => d.context.held),
      after: all.filter((d) => !d.context.held),
    });
    const sv = split(vfoAll);
    const sm = split(markerAll);
    const sc = split(centerAll);
    const firstInHold = [...vfoAll, ...markerAll, ...centerAll]
      .filter((d) => d.context.held)
      .sort((a, b) => a.tRelMs - b.tRelMs)[0] ?? null;
    const longestGap = gapDetails.reduce((m, g) => Math.max(m, g.durationMs), 0);
    console.log(`[held-key round2 ${latency.name} ${view.name} step=${stepHz}] ` +
      `vfo back=${vfoAll.length} (hold=${sv.during.length} max=${Math.max(0, ...sv.during.map((d) => d.prevMaxHz - d.valueHz))}Hz ` +
      `firstAt=${sv.during[0] ? `+${sv.during[0].tRelMs.toFixed(0)}ms` : 'n/a'}) ` +
      `marker back=${markerAll.length} (hold=${sm.during.length}) center back=${centerAll.length} (hold=${sc.during.length}) ` +
      `gaps=${gapDetails.length} longest=${longestGap.toFixed(0)}ms ` +
      (firstInHold === null ? 'no in-hold backward move' :
        `firstInHold at +${firstInHold.tRelMs.toFixed(0)}ms inflight=${firstInHold.context.inflightFreqHz} ` +
        `confirmed=${firstInHold.context.confirmedHz} gates=${firstInHold.context.gatesFreqHz} ` +
        `state=${firstInHold.context.tupleState}`) + ` ` +
      `before=${beforeHz} after=${afterHz} setFreq=${setFreqCommands.length} ` +
      `coverage=${coverageWidthHz} spanWrites=[${spanWrites.map((w) => w.span).join(',')}]`);

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
}

for (const [caseIndex, def] of ROUND2_CASES.entries()) {
  test(`IC-705: held ArrowRight 9s stays monotonic with the passband present (round2, ${def.latency.name}, ${def.view.name}, step ${def.stepHz}Hz)`,
    async ({ page }, info) => {
      await runRound2Case(page, info, def, ROUND2_BASE_PORT + caseIndex);
    });
}
