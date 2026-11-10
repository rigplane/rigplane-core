// MOR-4388 acceptance matrix — typed contracts shared by every harness piece.
//
// Everything crossing a process boundary (the Python simulator's evidence
// files, the fixture sidecar, the per-cell result JSONs the aggregator
// reads) has a declared type and a parser here; no re-guessed dict keys.

import { parseScopeWriteReceipt, type ScopeWriteReceipt } from '../journey/panorama-receipt';

export type MatrixPath =
  | 'click-waterfall'
  | 'click-spectrum'
  | 'drag-pan'
  | 'arrows'
  | 'wheel'
  | 'digit-wheel'
  | 'span-steps'
  | 'ctr-fix'
  | 'band-presets'
  | 'zoom'
  | 'band-change'
  | 'mode-change'
  | 'reconnect'
  | 'hold'
  | 'fullscreen'
  | 'resize-dpr'
  | 'two-clients'
  | 'ack-tracker'
  | 'external-change';

export const MATRIX_PATHS: readonly MatrixPath[] = [
  'click-waterfall', 'click-spectrum', 'drag-pan', 'arrows', 'wheel', 'digit-wheel',
  'span-steps', 'ctr-fix', 'band-presets', 'zoom', 'band-change', 'mode-change',
  'reconnect', 'hold', 'fullscreen', 'resize-dpr', 'two-clients',
  'ack-tracker', 'external-change',
];

export type RadioKind = 'ic705' | 'ic7300' | 'ic9700' | 'ic7610' | 'audiofft' | 'iqfft';

export interface MatrixRadio {
  readonly kind: RadioKind;
  readonly model: string;
  readonly source: 'hardware' | 'audio_fft' | 'iq_fft';
  readonly serverArgs: readonly string[];
  readonly note?: string;
}

export const MATRIX_RADIOS: Readonly<Record<RadioKind, MatrixRadio>> = {
  ic705: { kind: 'ic705', model: 'IC-705', source: 'hardware', serverArgs: [] },
  ic7300: { kind: 'ic7300', model: 'IC-7300', source: 'hardware', serverArgs: [] },
  ic9700: { kind: 'ic9700', model: 'IC-9700', source: 'hardware', serverArgs: [] },
  ic7610: { kind: 'ic7610', model: 'IC-7610', source: 'hardware', serverArgs: [] },
  audiofft: {
    kind: 'audiofft', model: 'IC-705', source: 'audio_fft',
    serverArgs: ['--scope-source', 'audio_fft', '--enable-audio'],
    note: 'audio-FFT source through the IC-705 simulator PCM tap; the source itself is radio-independent',
  },
  iqfft: {
    kind: 'iqfft', model: 'IC-705', source: 'iq_fft',
    serverArgs: ['--scope-source', 'sdr', '--iq'],
    note: 'deterministic FakeIqSource (src/rigplane/sdr/fake.py), one tone at +96 kHz',
  },
};

export interface MatrixCell {
  readonly id: string;
  readonly path: MatrixPath;
  readonly radio: RadioKind;
  readonly spanHz: number | null;
  readonly dpr: 1 | 2;
  /** Extra simulator server args for this cell (ack-tracker slow-link mode). */
  readonly serverArgs: readonly string[];
}

/**
 * What invariant 5 ("no false NAKs") demands of the path window.
 * The default tolerates bare-read refusals — the simulator NAKs background
 * poll reads it does not model (the real radio answers them) — but a
 * refused write or action is never a simulator gap and fails. The
 * ack-tracker cell additionally demands its armed refusal to have flown
 * (the scenario actually triggered) with no refused writes.
 */
export type NakExpectation =
  | { kind: 'reads-tolerated' }
  | { kind: 'armed-refusal'; cmd: number; sub: number | null };

/** One server → client command failure observed on the control socket. */
export interface CommandErrorObservation {
  readonly id: string | null;
  readonly message: string;
}

/** One external-change step and what the UI/server showed after it. */
export interface ExternalStepReport {
  readonly step: string;
  readonly change: string;
  readonly spanLabel: string | null;
  readonly viewStartHz: number | null;
  readonly viewEndHz: number | null;
  readonly coverageStartHz: number | null;
  readonly coverageEndHz: number | null;
  readonly stateFreqHz: number | null;
  readonly writesDelta: number;
  readonly grantsDelta: number;
  readonly setFreqDelta: number;
  readonly ok: boolean;
  readonly note: string;
}

export type InvariantId = 1 | 2 | 3 | 4 | 5 | 6 | 7;

/**
 * The control-socket id of the ack-tracker path's forced background read
 * (matrix-paths forceBackgroundTunerRead). The page's socket tracker ignores
 * this id: its refused answer is the scenario working, not a page failure.
 */
export const ACK_TRACKER_PROBE_ID = 'mor4388-ack-probe';
export type InvariantStatus = 'pass' | 'fail' | 'na' | 'expected-fail';

export type EvidenceValue = string | number | boolean | null;
export type Evidence = Readonly<Record<string, EvidenceValue>>;

export interface InvariantResult {
  readonly invariant: InvariantId;
  readonly status: InvariantStatus;
  readonly summary: string;
  readonly evidence: Evidence;
}

export interface CellResultMeta {
  readonly radioModel: string;
  readonly source: string;
  readonly sourceBadge: string | null;
  readonly finalFreqHz: number | null;
  readonly finalViewHz: readonly [number, number] | null;
  readonly frames: number;
  readonly grants: number;
  readonly spanChoicesHz: readonly number[];
}

export interface CellResult {
  readonly cell: MatrixCell;
  readonly status: 'pass' | 'fail' | 'na' | 'error';
  readonly error: string | null;
  readonly invariants: readonly InvariantResult[];
  readonly png: string | null;
  readonly meta: CellResultMeta;
}

export interface MatrixResults {
  readonly run: {
    readonly base: string;
    readonly scope: 'subset' | 'full';
    readonly startedAt: string;
    readonly durationS: number;
  };
  readonly cells: readonly CellResult[];
}

// ── Python evidence parsers ────────────────────────────────────────────────

export interface NakReceipt {
  readonly ts: number;
  readonly toAddr: number;
  readonly frmAddr: number;
  /** Hex of the refused CI-V frame; empty when the NAK answered a non-CI-V handshake packet. */
  readonly civHex: string;
}

export function parseNakReceipt(line: string): NakReceipt {
  const value: unknown = JSON.parse(line);
  if (typeof value !== 'object' || value === null) throw new Error('invalid NAK receipt');
  const record = value as Record<string, unknown>;
  if (typeof record.ts !== 'number' || typeof record.to_addr !== 'number'
    || typeof record.frm_addr !== 'number' || typeof record.civ_hex !== 'string') {
    throw new Error('invalid NAK receipt');
  }
  return { ts: record.ts, toAddr: record.to_addr, frmAddr: record.frm_addr, civHex: record.civ_hex };
}

export function parseReceiptLines<T>(text: string, parse: (line: string) => T): T[] {
  return text.trim().split('\n').filter(Boolean).map(parse);
}

export type { ScopeWriteReceipt };
export { parseScopeWriteReceipt };

// ── Fixture sidecar (tests/e2e/panorama_matrix_fixture.py) ────────────────

export interface FixtureSidecar {
  readonly grantedText: string;
  readonly frames: number;
  readonly nativeWidth: number;
  readonly viewStartHz: number;
  readonly viewEndHz: number;
  readonly durationS: number;
  readonly frameOffsetsS: readonly number[];
  readonly source: string;
}

export function parseFixtureSidecar(text: string): FixtureSidecar {
  const value: unknown = JSON.parse(text);
  if (typeof value !== 'object' || value === null) throw new Error('invalid fixture sidecar');
  const record = value as Record<string, unknown>;
  const int = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v);
  if (typeof record.grantedText !== 'string' || !int(record.frames) || !int(record.nativeWidth)
    || !int(record.viewStartHz) || !int(record.viewEndHz) || typeof record.durationS !== 'number'
    || typeof record.source !== 'string' || !Array.isArray(record.frameOffsetsS)
    || !record.frameOffsetsS.every((v): v is number => typeof v === 'number' && v >= 0)) {
    throw new Error('invalid fixture sidecar');
  }
  return {
    grantedText: record.grantedText, frames: record.frames, nativeWidth: record.nativeWidth,
    viewStartHz: record.viewStartHz, viewEndHz: record.viewEndHz, durationS: record.durationS,
    frameOffsetsS: record.frameOffsetsS, source: record.source,
  };
}

// ── Cell generation ────────────────────────────────────────────────────────

export type MatrixScope = 'subset' | 'full';

const SUBSET_SPAN = 100_000;
const SUBSET_SPAN_VARIANTS = [5_000, 500_000];
const SPAN_VARIANT_PATHS: readonly MatrixPath[] = ['click-spectrum', 'drag-pan', 'zoom', 'span-steps'];

function cell(
  path: MatrixPath, radio: RadioKind, spanHz: number | null,
  dpr: 1 | 2 = 1, serverArgs: readonly string[] = [],
): MatrixCell {
  const span = spanHz === null ? 'default' : String(spanHz);
  const dprTag = dpr === 2 ? '-dpr2' : '';
  return { id: `${radio}-${path}-${span}${dprTag}`, path, radio, spanHz, dpr, serverArgs };
}

// The ack-tracker cell models a slow radio link with one refused
// background read: every CI-V answer lands past the 120 ms ack-tracker
// grace, and the next matching 1C 01 (tuner status) read gets one FA.
const ACK_TRACKER_ARGS: readonly string[] = ['--slow-civ-ms', '150', '--refuse-once', '1C:01'];

/**
 * The subset follows the brief's representative plan: every path × IC-705
 * (at the default 100 kHz plus span variants where the span matters), one
 * profile per other radio type × the default span, the audio-FFT and IQ
 * sources at their own defaults, and one DPR-2 pass. The full matrix
 * crosses every preset with every path for every profile.
 */
export function generateCells(scope: MatrixScope): MatrixCell[] {
  const cells: MatrixCell[] = [];
  const radios: RadioKind[] = ['ic705', 'ic7300', 'ic9700', 'ic7610', 'audiofft', 'iqfft'];
  for (const radio of radios) {
    const preset = radio === 'audiofft' || radio === 'iqfft';
    for (const path of MATRIX_PATHS) {
      const extraArgs = path === 'ack-tracker' ? ACK_TRACKER_ARGS : [];
      if (scope === 'subset') {
        cells.push(cell(path, radio, preset ? null : SUBSET_SPAN, 1, extraArgs));
      } else {
        const spans = preset ? [null] : [2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, null];
        for (const span of spans) cells.push(cell(path, radio, span, 1, extraArgs));
      }
    }
  }
  if (scope === 'subset') {
    for (const path of SPAN_VARIANT_PATHS) for (const span of SUBSET_SPAN_VARIANTS) cells.push(cell(path, 'ic705', span));
    cells.push(cell('resize-dpr', 'ic705', SUBSET_SPAN, 2));
  }
  return cells;
}
