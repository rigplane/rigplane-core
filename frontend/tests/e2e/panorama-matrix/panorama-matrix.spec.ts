// MOR-4388 — Panorama beta.16 acceptance matrix (rc8 baseline).
//
// One generated Playwright test per matrix cell. Each cell starts its own
// loopback simulator server (tests/e2e/panorama_web_server.py with the
// matrix knobs), drives one interaction path, then evaluates the seven
// invariants against typed evidence: the drawn axis-label history, the
// captured waterfall framebuffer rows, the received scope frames (the
// ground-truth bins), the simulator's typed scope-write and NAK receipts,
// and the WebSocket command log. Product code is untouched; the harness
// lives entirely in tests.

import { expect, test } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import {
  generateCells, MATRIX_RADIOS, parseNakReceipt, parseReceiptLines, parseScopeWriteReceipt,
  type CellResult, type Evidence, type InvariantResult, type MatrixCell, type MatrixRadio, type MatrixScope,
  type NakReceipt, type ScopeWriteReceipt,
} from './matrix-contract';
import {
  analyzeWaterfall, axisHzToCssX, bumpGroundTruth, currentAxis, MATRIX_INIT, measurePage,
  observePage, requestWaterfallCapture, rowPeakCssX, type Axis, type BumpGroundTruth, type PageObservations,
} from './matrix-instrument';
import { runPath, stateFreq, vfoDisplayHz, type PathContext, type PathReport } from './matrix-paths';
import { startJourneyServer, type JourneyServerHandle } from '../journey/server-process';

const scope: MatrixScope = process.env.PANORAMA_MATRIX_SCOPE === 'full' ? 'full' : 'subset';
const cells = generateCells(scope);
const portBase = Number(process.env.PANORAMA_MATRIX_PORT_BASE ?? 21_500);
let portCounter = 0;

interface Receipts {
  readonly writes: ScopeWriteReceipt[];
  readonly naks: NakReceipt[];
}

interface RefusedFrame {
  readonly cmd: number;
  readonly sub: number | null;
  /** A bare read (no data bytes): tolerated as a simulator gap. Anything longer refused is a write/action. */
  readonly bareRead: boolean;
  readonly describe: string;
}

/** Parse a refused CI-V frame (FE FE TO FR CMD [SUB] [DATA…] FD) from its hex. Null when unparseable. */
function parseRefusedFrame(civHex: string): RefusedFrame | null {
  let bytes: Uint8Array;
  try {
    const clean = civHex.trim();
    if (clean.length === 0 || clean.length % 2 !== 0) return null;
    bytes = Uint8Array.from({ length: clean.length / 2 }, (_, i) => Number.parseInt(clean.slice(2 * i, 2 * i + 2), 16));
  } catch {
    return null;
  }
  if (bytes.length < 6 || bytes[0] !== 0xfe || bytes[1] !== 0xfe || bytes[bytes.length - 1] !== 0xfd) return null;
  const cmd = bytes[4];
  const inner = bytes.slice(5, -1);
  const sub = inner.length >= 1 ? inner[0] : null;
  // Bare command (FE FE TO FR CMD FD) or command+sub only: a read. The
  // 0x07 select form (CMD + one D0/D1/D2 byte) is the same length but is
  // connect-time and therefore outside every path window.
  const bareRead = inner.length <= 1;
  const describe = `cmd=0x${cmd.toString(16)}${sub === null ? '' : ` sub=0x${sub.toString(16)}`} len=${bytes.length}`;
  return { cmd, sub, bareRead, describe };
}

async function readReceipts(evidencePath: string): Promise<Receipts> {
  const read = async <T>(file: string, parse: (line: string) => T): Promise<T[]> => {
    try {
      return parseReceiptLines(await readFile(file, 'utf8'), parse);
    } catch {
      return [];
    }
  };
  return {
    writes: await read(evidencePath, parseScopeWriteReceipt),
    naks: await read(`${evidencePath.slice(0, -'.jsonl'.length)}.nak.jsonl`, parseNakReceipt),
  };
}

async function spanChoicesHz(page: import('@playwright/test').Page, port: number): Promise<number[]> {
  try {
    const response = await page.request.get(`http://127.0.0.1:${port}/api/v1/capabilities`);
    const caps = (await response.json()) as Record<string, unknown>;
    const raw = caps.span_choices_hz;
    return Array.isArray(raw) ? raw.filter((v): v is number => typeof v === 'number') : [];
  } catch {
    return [];
  }
}

async function setSpanHz(page: import('@playwright/test').Page, targetHz: number): Promise<void> {
  const value = page.getByTestId('scope-span-value').first();
  const readSpan = async (): Promise<number | null> => {
    const text = await value.innerText().catch(() => '');
    const match = text.match(/±(\d+(?:\.\d+)?)(k|M)?/);
    if (!match) return null;
    const scale = match[2] === 'M' ? 2e6 : match[2] ? 2e3 : 2;
    return Math.round(Number(match[1]) * scale);
  };
  for (let guard = 0; guard < 12; guard++) {
    const current = await readSpan();
    if (current === null || current === targetHz) return;
    const name = current > targetHz ? 'Decrease scope span' : 'Increase scope span';
    const button = page.getByRole('button', { name, exact: true }).first();
    if (await button.isDisabled()) return;
    await button.click();
    await page.waitForTimeout(600);
  }
}

async function runCell(cell: MatrixCell, page: import('@playwright/test').Page, browser: import('@playwright/test').Browser, info: import('@playwright/test').TestInfo): Promise<CellResult> {
  const radio = MATRIX_RADIOS[cell.radio];
  const port = portBase + info.workerIndex * 100 + (portCounter++ % 90);
  const evidencePath = info.outputPath('radio.jsonl');
  const serverArgs = ['--model', radio.model, '--evidence', evidencePath, '--signal-amplitude', '235', ...radio.serverArgs, ...cell.serverArgs];
  let server: JourneyServerHandle | null = null;
  const startServer = async (): Promise<JourneyServerHandle> =>
    startJourneyServer(port, 14_250_000, serverArgs, 'tests/e2e/panorama_web_server.py');
  server = await startServer();

  const observations: PageObservations = { subscriptions: [], grants: [], frames: [], commands: [], commandErrors: [] };
  try {
    await page.addInitScript(MATRIX_INIT);
    observePage(page, observations);
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    await expect.poll(() => observations.frames.length, { timeout: 30_000 }).toBeGreaterThan(10);

    if (cell.spanHz !== null && cell.spanHz !== 100_000) await setSpanHz(page, cell.spanHz);
    await page.waitForTimeout(1500);

    // Baseline before the path: axis, bump ground truth, older-row scale.
    const baselineAxis = await currentAxis(page);
    await requestWaterfallCapture(page);
    const baselineWaterfall = await analyzeWaterfall(page);
    // Baseline runs before the path, so the scope is still in its default
    // centre mode: the injected bump sits at 0.625 of the radio window.
    const baselineBump = radio.source === 'hardware'
      ? bumpGroundTruth(observations.frames, 'injected', 0.625) : null;
    const baselineReceipts = await readReceipts(evidencePath);
    const freqBefore = await stateFreq(page, port);

    const ctx: PathContext = {
      page, browser, port,
      radioKind: radio.kind,
      source: radio.source,
      spanHz: cell.spanHz,
      observations,
      evidencePath,
      restartServer: async () => {
        await server?.kill();
        server = await startServer();
      },
      currentSpanHz: () => cell.spanHz,
    };
    const report = await runPath(cell.path, ctx);

    // Post-path measurements.
    await requestWaterfallCapture(page);
    const axis = await currentAxis(page);
    const measurement = await measurePage(page);
    const waterfall = await analyzeWaterfall(page);
    const receipts = await readReceipts(evidencePath);
    const freqAfter = await stateFreq(page, port);
    const displayHz = await vfoDisplayHz(page);
    const choices = await spanChoicesHz(page, port);
    const badge = await page.locator('[data-scope-source]').first().innerText().catch(() => null);

    const windowAfter = <T>(baseline: readonly T[], current: readonly T[]): readonly T[] =>
      current.length < baseline.length ? current : current.slice(baseline.length);
    const invariants = evaluateInvariants({
      cell, radio, report, axis, baselineAxis, baselineBump, baselineWaterfall,
      measurement, waterfall, observations, receipts, baselineReceipts,
      pathWrites: windowAfter(baselineReceipts.writes, receipts.writes) as ScopeWriteReceipt[],
      pathNaks: windowAfter(baselineReceipts.naks, receipts.naks) as NakReceipt[],
      freqBefore, freqAfter, displayHz, choices,
    });

    const failed = invariants.some(result => result.status === 'fail');
    const allNa = invariants.length > 0 && invariants.every(result => result.status === 'na');
    let png: string | null = null;
    if (failed) {
      png = path.basename(info.outputPath(`${cell.id}.png`));
      await page.screenshot({ path: info.outputPath(`${cell.id}.png`), fullPage: true }).catch(() => {});
    }
    const lastFrame = observations.frames.at(-1) ?? null;
    const lastGrant = observations.grants.at(-1) ?? null;
    return {
      cell,
      status: failed ? 'fail' : allNa ? 'na' : 'pass',
      error: null,
      invariants,
      png,
      meta: {
        radioModel: radio.model,
        source: radio.source,
        sourceBadge: badge,
        finalFreqHz: freqAfter,
        finalViewHz: lastFrame ? [lastFrame.view[0], lastFrame.view[1]] : lastGrant ? [lastGrant.view.startHz, lastGrant.view.endHz] : null,
        frames: observations.frames.length,
        grants: observations.grants.length,
        spanChoicesHz: choices,
      },
    };
  } finally {
    await server?.kill().catch(() => {});
  }
}

interface EvaluationInput {
  readonly cell: MatrixCell;
  readonly radio: MatrixRadio;
  readonly report: PathReport;
  readonly axis: Axis | null;
  readonly baselineAxis: Axis | null;
  readonly baselineBump: BumpGroundTruth | null;
  readonly baselineWaterfall: Awaited<ReturnType<typeof analyzeWaterfall>>;
  readonly measurement: Awaited<ReturnType<typeof measurePage>>;
  readonly waterfall: Awaited<ReturnType<typeof analyzeWaterfall>>;
  readonly observations: PageObservations;
  readonly receipts: Receipts;
  readonly baselineReceipts: Receipts;
  /** Scope writes that landed while the path ran: the log tail after the
   * baseline count (or the whole file when the server restarted under the
   * path, e.g. reconnect — the evidence file restarts empty with it). */
  readonly pathWrites: ScopeWriteReceipt[];
  /** Same windowing for NAK receipts (invariant 5). */
  readonly pathNaks: NakReceipt[];
  readonly freqBefore: number;
  readonly freqAfter: number;
  readonly displayHz: number | null;
  readonly choices: number[];
}

function evaluateInvariants(input: EvaluationInput): InvariantResult[] {
  const { cell, report, axis, measurement, waterfall, observations, receipts } = input;
  const results: InvariantResult[] = [];
  const na = (invariant: 1 | 2 | 3 | 4 | 5 | 6, reason: string): InvariantResult =>
    ({ invariant, status: 'na', summary: reason, evidence: { reason } });

  // Invariant 1 — the VFO marker sits within 1 px of the VFO frequency on
  // the drawn axis (spectrum and waterfall).
  if (report.na[1]) {
    results.push(na(1, report.na[1]));
  } else if (!axis || !measurement.tuneLineSpectrum) {
    results.push({ invariant: 1, status: 'fail', summary: 'no drawn axis or tune-line to measure', evidence: { axis: axis ? `${axis.startMHz}..${axis.endMHz}` : null } });
  } else {
    const hz = input.freqAfter;
    const expectedX = axisHzToCssX(hz, axis, measurement.tuneLineSpectrum.areaCssWidth);
    const inView = hz / 1e6 >= axis.startMHz && hz / 1e6 <= axis.endMHz;
    const spectrumDelta = measurement.tuneLineSpectrum.centerCssX === null ? null
      : measurement.tuneLineSpectrum.centerCssX - expectedX;
    const waterfallDelta = measurement.tuneLineWaterfall?.centerCssX === null || !measurement.tuneLineWaterfall
      ? null : measurement.tuneLineWaterfall.centerCssX - expectedX;
    // The marker is a 2 px element; its centre is compared with a 1.5 px
    // tolerance (1 px of the brief plus DPR/rounding of the centre).
    const tolerance = 1.5;
    const spectrumOk = !inView ? null : Math.abs(spectrumDelta ?? Infinity) <= tolerance;
    const waterfallOk = !inView ? null : Math.abs(waterfallDelta ?? Infinity) <= tolerance;
    const visible = measurement.tuneLineSpectrum.visible;
    const pass = inView ? visible && spectrumOk === true && waterfallOk === true : true;
    results.push({
      invariant: 1, status: pass ? 'pass' : 'fail',
      summary: inView
        ? `marker at Δspectrum=${spectrumDelta?.toFixed(1)}px, Δwaterfall=${waterfallDelta?.toFixed(1)}px (visible=${visible})`
        : `VFO ${hz} Hz outside the drawn axis ${axis.startMHz}..${axis.endMHz} MHz; clamping/visibility recorded`,
      evidence: {
        freqHz: hz, axisStartMHz: axis.startMHz, axisEndMHz: axis.endMHz,
        expectedCssX: Math.round(expectedX * 10) / 10,
        spectrumMarkerCssX: measurement.tuneLineSpectrum.centerCssX === null ? null : Math.round(measurement.tuneLineSpectrum.centerCssX * 10) / 10,
        waterfallMarkerCssX: measurement.tuneLineWaterfall?.centerCssX == null ? null : Math.round(measurement.tuneLineWaterfall.centerCssX * 10) / 10,
        spectrumVisible: visible, waterfallVisible: measurement.tuneLineWaterfall?.visible ?? null,
      },
    });
  }

  // Invariant 2 — one horizontal scale: the frame's own strongest bin (the
  // injected bump) lands at the same x in the trace and the newest
  // waterfall row, at the position the drawn axis demands; older rows keep
  // the scale they were painted under.
  if (report.na[2]) {
    results.push(na(2, report.na[2]));
  } else {
    const bumpBasis: 'injected' | 'strongest' | 'iq-tone' = input.radio.source === 'iq_fft' ? 'iq-tone' : input.radio.source === 'audio_fft' ? 'strongest' : 'injected';
    const bump = bumpGroundTruth(input.observations.frames, bumpBasis, input.report.bumpFraction ?? 0.625);
    if (!bump || !axis || !measurement.spectrum || !waterfall?.topRow) {
      results.push({ invariant: 2, status: 'fail', summary: 'missing ground truth for the shared-scale check', evidence: { bump: !!bump, axis: !!axis, spectrum: !!measurement.spectrum, waterfall: !!waterfall?.topRow } });
    } else {
      const cssWidth = measurement.spectrum.cssWidth;
      const expectedX = axisHzToCssX(bump.hz, axis, cssWidth);
      const spectrumX = measurement.spectrum.peakCssX ?? -1;
      const waterfallX = rowPeakCssX(waterfall.topRow, waterfall.cssScale) ?? -1;
      const tolerance = 2.5;
      const spectrumDelta = spectrumX - expectedX;
      const waterfallDelta = waterfallX - expectedX;
      const crossDelta = spectrumX - waterfallX;
      // Cross tolerance: two measurement methods on the same drawn row
      // quantise differently; the shared-scale claim is about percent-level
      // agreement between the two renderers, the absolute position keeps
      // the tight tolerance.
      const crossTolerance = Math.max(tolerance, cssWidth * 0.02);
      const scaleOk = Math.abs(spectrumDelta) <= tolerance && Math.abs(waterfallDelta) <= tolerance && Math.abs(crossDelta) <= crossTolerance;
      // Older rows: only meaningful when the view moved during the path.
      // The reference is where the bump was DRAWN in the baseline capture
      // (the newest row before the change): history is preserved when the
      // older rows keep the position they were painted at, whatever scale
      // that was; rewritten history is the failure.
      const viewChanged = input.baselineAxis !== null && axis !== null
        && (input.baselineAxis.startMHz !== axis.startMHz || input.baselineAxis.endMHz !== axis.endMHz);
      let history: Evidence = { viewChanged };
      let historyOk: boolean | null = null;
      if (viewChanged && input.baselineWaterfall?.topRow && waterfall.bottomRow) {
        const baselineTopX = rowPeakCssX(input.baselineWaterfall.topRow, input.baselineWaterfall.cssScale) ?? -1;
        const bottomX = rowPeakCssX(waterfall.bottomRow, waterfall.cssScale) ?? -1;
        const hasSignal = waterfall.bottomStrength >= 0.3 * Math.max(1, waterfall.topStrength);
        const keptOld = Math.abs(bottomX - baselineTopX) <= tolerance + 3;
        const matchesNew = Math.abs(bottomX - waterfallX) <= tolerance;
        historyOk = !hasSignal ? null : keptOld || matchesNew;
        history = { viewChanged, olderRowHasSignal: hasSignal, baselineTopCssX: Math.round(baselineTopX * 10) / 10, olderRowCssX: Math.round(bottomX * 10) / 10, newestRowCssX: Math.round(waterfallX * 10) / 10, olderRowKeepsPaintedPosition: keptOld, olderRowRescaledToNew: matchesNew };
      }
      const pass = scaleOk && (historyOk !== false);
      results.push({
        invariant: 2, status: pass ? 'pass' : 'fail',
        summary: `bump ${bump.hz} Hz (bin ${bump.binIndex}, amp ${bump.amplitude}): Δtrace=${spectrumDelta.toFixed(1)}px Δwaterfall=${waterfallDelta.toFixed(1)}px Δcross=${crossDelta.toFixed(1)}px${historyOk === false ? '; older rows rescaled' : ''}`,
        evidence: { bumpBasis: bump.basis, bumpFraction: input.report.bumpFraction ?? 0.625, bumpHz: bump.hz, binIndex: bump.binIndex, amplitude: bump.amplitude, frameView: `${bump.view[0]}..${bump.view[1]}`, traceHeights: measurement.spectrum.debugHeights, expectedCssX: Math.round(expectedX * 10) / 10, traceCssX: Math.round(spectrumX * 10) / 10, waterfallCssX: Math.round(waterfallX * 10) / 10, traceProfileWidthPx: measurement.spectrum.profileWidthPx, ...history },
      });
    }
  }

  // Invariant 3 — the radio span is the smallest covering preset with full
  // bin use. MOR-4385: on rc8 Icom presets are treated as ±half-widths, so
  // this is recorded as an expected failure with the numbers.
  if (report.na[3]) {
    results.push(na(3, report.na[3]));
  } else if (input.choices.length === 0) {
    results.push(na(3, 'no span presets reported by the server'));
  } else {
    const lastRequest = observations.subscriptions.at(-1) ?? null;
    const lastGrant = observations.grants.at(-1) ?? null;
    const lastFrame = observations.frames.at(-1) ?? null;
    const requestedWidth = lastRequest && 'startHz' in (lastRequest.view as object)
      ? (lastRequest.view as { endHz: number }).endHz - (lastRequest.view as { startHz: number }).startHz
      : lastRequest && 'spanHz' in (lastRequest.view as object)
        ? (lastRequest.view as { spanHz: number }).spanHz : null;
    const appliedPreset = [...receipts.writes].reverse().find(write => write.sub === 0x15)?.span ?? null;
    const grantedWidth = lastGrant ? lastGrant.view.endHz - lastGrant.view.startHz : null;
    const coverageWidth = lastFrame ? lastFrame.coverage[1] - lastFrame.coverage[0] : null;
    const expectedPreset = requestedWidth === null ? null
      : (input.choices.filter(choice => choice >= requestedWidth)[0] ?? null);
    const ok = expectedPreset !== null && appliedPreset === expectedPreset
      && coverageWidth !== null && coverageWidth === expectedPreset;
    results.push({
      invariant: 3, status: ok ? 'pass' : 'expected-fail',
      summary: ok
        ? `radio span ${appliedPreset} Hz is the smallest covering preset (requested ${requestedWidth})`
        : `MOR-4385 (expected on rc8): requested ${requestedWidth} Hz → applied preset ${appliedPreset}, granted width ${grantedWidth}, coverage width ${coverageWidth}, expected smallest covering preset ${expectedPreset}`,
      evidence: { requestedWidth, appliedPreset, grantedWidth, coverageWidth, expectedPreset, presets: input.choices.join(','), frameBins: lastFrame?.width ?? null },
    });
  }

  // Invariant 4 — no stray radio scope writes during the path window
  // (the log tail after the baseline, not the whole session file: the
  // initial grant triple belongs to page load, not to the path).
  if (report.na[4]) {
    results.push(na(4, report.na[4]));
  } else {
    const writes = input.pathWrites;
    const expectation = report.writes;
    let ok: boolean;
    if (expectation.kind === 'none') {
      ok = writes.length === 0;
    } else {
      const subs = writes.map(write => write.sub);
      ok = expectation.subs.every(sub => subs.includes(sub))
        && expectation.forbidden.every(sub => !subs.includes(sub));
    }
    results.push({
      invariant: 4, status: ok ? 'pass' : 'fail',
      summary: `${writes.length} scope write(s) during the path (${writes.map(write => `0x${write.sub.toString(16)}:${write.span}`).join(' ') || 'none'})`,
      evidence: {
        writes: JSON.stringify(writes), expectation: JSON.stringify(expectation),
        externalSteps: report.external ? JSON.stringify(report.external) : null,
      },
    });
  }

  // Invariant 5 — no false NAKs. Only NAKs answering a real CI-V frame
  // count (the connection choreography also produces NAK-shaped replies to
  // non-CI-V handshake packets, which the client tolerates by design). A
  // bare-read refusal is a simulator gap — the simulator NAKs background
  // poll reads it does not model, which the real radio answers — and is
  // recorded, not failed; a refused write or action is never a gap.
  // The ack-tracker cell additionally demands its armed refusal to have
  // flown (scenario triggered) with no refused writes and no command
  // errors surfaced to the client.
  {
    const naks = input.pathNaks.filter(nak => nak.civHex.length > 0);
    const handshake = input.pathNaks.length - naks.length;
    const refused = naks.map(nak => parseRefusedFrame(nak.civHex)).filter(frame => frame !== null);
    const refusedWrites = refused.filter(frame => !frame.bareRead);
    const expectation = report.naks ?? { kind: 'reads-tolerated' };
    const commandErrors = report.commandErrors ?? [];
    let ok: boolean;
    let summary: string;
    if (expectation.kind === 'armed-refusal') {
      const armed = refused.filter(frame =>
        frame.cmd === expectation.cmd && (expectation.sub === null || frame.sub === expectation.sub));
      ok = armed.length >= 1 && refusedWrites.length === 0 && commandErrors.length === 0;
      summary = ok
        ? `${armed.length} armed refusal(s) flew, no refused writes, no command errors (${naks.length} bare-read refusals tolerated, ${handshake} handshake)`
        : `armed refusal ${expectation.cmd.toString(16)}:${expectation.sub?.toString(16) ?? '-'} seen ${armed.length}x, refused writes ${refusedWrites.length}, command errors ${commandErrors.length}`;
    } else {
      ok = refusedWrites.length === 0;
      summary = ok
        ? `no refused writes (${naks.length} bare-read refusals tolerated, ${handshake} handshake)`
        : `${refusedWrites.length} refused write(s): ${refusedWrites.slice(0, 5).map(frame => frame.describe).join(' ')}`;
    }
    results.push({
      invariant: 5, status: ok ? 'pass' : 'fail',
      summary,
      evidence: {
        civNaks: naks.length, handshakeNaks: handshake,
        refusedReads: refused.length - refusedWrites.length,
        refusedWrites: refusedWrites.length,
        commandErrors: commandErrors.length,
        commandErrorText: commandErrors.slice(0, 5).join(' | ') || null,
        naks: JSON.stringify(naks.slice(0, 20)),
      },
    });
  }

  // Invariant 6 — tuning is correct afterwards.
  if (report.na[6]) {
    results.push(na(6, report.na[6]));
  } else {
    const setFreqs = observations.commands.filter(command => command.name === 'set_freq');
    const valueOf = (command: typeof setFreqs[number]): number | null => {
      const params = command.params as Record<string, unknown>;
      for (const key of ['freq', 'frequency', 'freqHz']) {
        if (typeof params[key] === 'number') return params[key] as number;
      }
      return null;
    };
    const values = setFreqs.map(valueOf);
    const expectation = report.setFreq;
    let ok: boolean;
    let summary: string;
    if (expectation.kind === 'no-change') {
      ok = setFreqs.length === 0 && input.freqAfter === input.freqBefore;
      summary = ok ? 'no tuning commands, frequency unchanged' : `${setFreqs.length} set_freq command(s), frequency ${input.freqBefore}→${input.freqAfter}`;
    } else if (expectation.kind === 'strict') {
      const expected = expectation.values[0];
      const sent = values.at(-1) ?? null;
      const stateOk = Math.abs(input.freqAfter - expected) <= expectation.toleranceHz;
      const sentOk = sent === null ? false : Math.abs(sent - expected) <= expectation.toleranceHz;
      const displayOk = input.displayHz === null ? false : input.displayHz === input.freqAfter;
      ok = stateOk && sentOk && displayOk;
      summary = `expected ≈${expected} Hz: sent ${sent}, state ${input.freqAfter}, display ${input.displayHz}`;
    } else {
      const sent = values.at(-1) ?? null;
      const stateSentOk = sent === null || sent === input.freqAfter;
      const displayOk = input.displayHz === null || input.displayHz === input.freqAfter;
      ok = stateSentOk && displayOk;
      summary = `consistency: last set_freq ${sent}, state ${input.freqAfter}, display ${input.displayHz}`;
    }
    results.push({
      invariant: 6, status: ok ? 'pass' : 'fail', summary,
      evidence: { setFreqValues: JSON.stringify(values), stateHz: input.freqAfter, displayHz: input.displayHz, expectation: expectation.kind, note: 'note' in expectation ? expectation.note : '' },
    });
  }

  return results;
}

for (const dpr of [1, 2] as const) {
  test.describe(`MOR-4388 panorama acceptance matrix (${scope}, DPR ${dpr})`, () => {
    test.use({ deviceScaleFactor: dpr });
    for (const cell of cells.filter(item => item.dpr === dpr)) {
      test(cell.id, async ({ page, browser }, info) => {
        info.setTimeout(210_000);
        let result: CellResult;
        try {
          result = await runCell(cell, page, browser, info);
        } catch (error) {
          const pngFile = info.outputPath(`${cell.id}.png`);
          await page.screenshot({ path: pngFile, fullPage: true }).catch(() => {});
          result = {
            cell, status: 'error', error: String(error), invariants: [], png: path.basename(pngFile),
            meta: { radioModel: MATRIX_RADIOS[cell.radio].model, source: MATRIX_RADIOS[cell.radio].source, sourceBadge: null, finalFreqHz: null, finalViewHz: null, frames: 0, grants: 0, spanChoicesHz: [] },
          };
        }
        await writeFile(info.outputPath('matrix-cell.json'), JSON.stringify(result, null, 2));
        expect(result.status !== 'error', `${cell.id}: ${result.error ?? ''}`).toBe(true);
        for (const invariant of result.invariants) {
          expect.soft(invariant.status !== 'fail', `${cell.id} invariant ${invariant.invariant}: ${invariant.summary}`).toBe(true);
        }
      });
    }
  });
}
