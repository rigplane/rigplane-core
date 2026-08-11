/**
 * A11/A12 (MOR-1409, Core #2317) — static ownership scan + frozen-fact
 * regression pins for the batch-A/batch-B projection-honesty fixes in
 * `panel-props.ts`.
 *
 * Companion to `panel-props.test.ts`'s per-function behavioral RED tests.
 * This file asserts two things a per-function unit test cannot:
 *
 *  1. none of the SPECIFIC fabricated-default literal source patterns named
 *     by the A11/A12 re-anchor plans' live audits remain anywhere in
 *     `panel-props.ts`'s source text — a belt-and-suspenders sweep on top
 *     of the behavioral tests, so a mutant that restores the literal in a
 *     place no existing behavioral test happens to probe is still caught.
 *     `toFilterProps`/`toAudioSpectrumProps`' `?? 2400` moved here from
 *     `stillPresentOutOfScope` at A12 — A11 narrowed it out (adjudication
 *     5245697359, Core #2317: a `NaN` sentinel there renders the literal
 *     "NaNkHz" in `FilterPanel.svelte`'s BW readout/settings modal, a
 *     formatted-display consumer, not a comparison consumer), and A12 is
 *     granted `FilterPanel.svelte` as a fourth production file specifically
 *     to add that consumer-boundary guard (see `FilterPanel.isolated.test.ts`),
 *     so the fabricated default can now be removed at the source;
 *  2. two frozen facts this gate must NOT disturb: `panel-adapters.ts` still
 *     makes zero `runtime.send()` calls (§3.4 of the plan — it only reads
 *     `runtime.state`/`runtime.caps`), and `RadioLayout.svelte` still has
 *     exactly the same two documented `send()` call sites (correction
 *     5241395868's "only two production callers remain" premise).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const PANEL_PROPS_PATH = resolve(__dirname, '../panel-props.ts');
const PANEL_ADAPTERS_PATH = resolve(__dirname, '../../adapters/panel-adapters.ts');
const RADIO_LAYOUT_PATH = resolve(
  __dirname,
  '../../../../components-v2/layout/RadioLayout.svelte',
);

function readSource(path: string): string {
  return readFileSync(path, 'utf8');
}

/**
 * Slices a single `export function <name>(...) { ... }` body out of the
 * module source, up to (not including) the next top-level `export`. Scoping
 * the scan to one function at a time is deliberate: several batch-B/A12
 * functions this gate must NOT touch (`toCwProps`, `toAudioSpectrumProps`)
 * share IDENTICAL fallback source text with batch-A functions this gate DOES
 * fix (e.g. `currentMode: rx?.mode ?? 'USB',`-shaped text recurs across
 * several functions) — a whole-file substring scan cannot tell those apart
 * without producing a false positive against code this gate must leave
 * alone. `toFilterProps.filterWidth`'s own `?? 2400` is a second example:
 * it now shares its exact fallback text with `toAudioSpectrumProps`' twin,
 * both deliberately out of A11's scope (adjudication 5245697359).
 */
function functionBody(source: string, name: string): string {
  const start = source.indexOf(`export function ${name}(`);
  if (start === -1) throw new Error(`function ${name} not found in panel-props.ts`);
  const next = source.indexOf('\nexport ', start + 1);
  return source.slice(start, next === -1 ? source.length : next);
}

describe('panel-props.ts batch-A/batch-B functions carry no fabricated-default literal (MOR-1409 A11/A12)', () => {
  const source = readSource(PANEL_PROPS_PATH);

  // One row per batch-A/batch-B function (plan §3.3) this program fixes, and
  // the exact source substring the live re-anchor audits named as its
  // plausible-looking fabricated default. On exact base at each gate's own
  // anchor every one of these is present in its named function; after the
  // fix none may remain THERE — functions this gate does NOT touch sharing
  // the same substring elsewhere in the file are exempt (a later/earlier
  // gate's own owner, per the plan's function-level split; see
  // `stillPresentOutOfScope` below for A12's own explicit non-fixes).
  const forbidden: ReadonlyArray<readonly [fn: string, literal: string]> = [
    // A11 (batch-A)
    ['toVfoProps', 'freq: 14074000,'],
    ['toVfoProps', "mode: 'USB',"],
    ['toVfoProps', "filter: 'FIL1',"],
    ['toVfoProps', '?? 14074000'],
    ['toVfoProps', "?? 'USB'"],
    ['toBandSelectorProps', '?? 14074000'],
    ['toFilterProps', "?? 'USB'"],
    ['toFilterProps', "?? ['FIL1', 'FIL2', 'FIL3']"],
    ['toAgcProps', 'agcMode: rx?.agc ?? 2,'],
    ['toAgcProps', 'agcModes: caps?.agcModes ?? [1, 2, 3]'],
    ['toModeProps', "currentMode: rx?.mode ?? 'USB',"],
    ['toAntennaProps', 'antennaCount: caps?.antennas ?? 1,'],
    // A12 (batch-B)
    ['toRitXitProps', 'ritOffset: state?.ritFreq ?? 0,'],
    ['toRitXitProps', 'xitOffset: state?.ritFreq ?? 0,'],
    ['toModeProps', "['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R']"],
    ['toCwProps', 'cwPitch: state?.cwPitch ?? 600,'],
    ['toCwProps', 'keySpeed: state?.keySpeed ?? 12,'],
    ['toCwProps', 'wpm: state?.keySpeed ?? 12,'],
    ['toCwProps', 'sidetonePitch: state?.cwPitch ?? 600,'],
    ['toCwProps', 'sidetoneLevel: state?.monitorGain ?? 128,'],
    ['toCwProps', 'keyerType: 0,'],
    ['toMeterProps', 'sValue: rx?.sMeter ?? 0,'],
    ['toMeterProps', 'signal: rx?.sMeter ?? 0,'],
    ['toMeterProps', 'rfPower: state?.powerMeter ?? 0,'],
    ['toMeterProps', 'swr: state?.swrMeter ?? 0,'],
    ['toMeterProps', 'alc: state?.alcMeter ?? 0,'],
    ['toMeterProps', 'comp: state?.compMeter ?? 0,'],
    ['toMeterProps', 'vd: state?.vdMeter ?? 0,'],
    ['toMeterProps', 'id: state?.idMeter ?? 0,'],
    ['toRxAudioProps', '(rx?.afLevel ?? 0.5)'],
    ['toScanProps', 'scanType: state?.scanType ?? 0,'],
    ['toScanProps', 'scanResumeMode: (state?.scanResumeMode ?? 0)'],
    ['toAudioSpectrumProps', 'filterWidth: rx?.filterWidth ?? 2400,'],
    ['toMemoryPanelProps', 'activeFreqHz: rx?.freqHz ?? 0,'],
    ["toMemoryPanelProps", "activeMode: rx?.mode ?? '',"],
  ];

  it.each(forbidden)('%s does not contain %j', (fn, literal) => {
    expect(functionBody(source, fn)).not.toContain(literal);
  });

  it('toCwProps does not declare a keyerType field at all (dead output, removed not sentineled)', () => {
    expect(functionBody(source, 'toCwProps')).not.toMatch(/\bkeyerType\b/);
  });

  it('toFilterProps.filterWidth no longer fabricates 2400 (A12 fix — was deferred at A11)', () => {
    expect(functionBody(source, 'toFilterProps')).not.toContain('filterWidth: rx?.filterWidth ?? 2400,');
  });

  // Companion positive checks: functions/literals this program deliberately
  // leaves untouched — either a later/earlier gate's own owner, or an A12
  // builder-authored explicit non-fix decision (documented inline and in
  // `panel-props.test.ts`).
  const stillPresentOutOfScope: ReadonlyArray<readonly [fn: string, literal: string]> = [
    // toCwProps' internal mode-gate fallback feeds only apfDisabled/
    // tpfDisabled (always the conservative/disabled direction) — not a
    // fabrication target (plan §7 LOW item).
    ['toCwProps', "?? 'USB'"],
    // contourFreq is not yet exposed in ServerState at all — a placeholder
    // for an unwired feature, a distinct class from every other literal in
    // this sweep (plan §5, "not folded into the mechanical sweep").
    ['toAudioSpectrumProps', 'contourFreq: 128,'],
    // ritActive/xitActive/twinPeak/scanning keep a plain `boolean`
    // contract: RitXitPanel.svelte/CwPanel.svelte/ScanPanel.svelte (none
    // are A12 owners) each pass these straight into a `HardwareButton
    // active={…}` prop typed `boolean | undefined` — widening to
    // `boolean | null` broke `npm run check` in a file A12 is not granted.
    // `false` is the conservative/off reading; each panel is gated on its
    // own `hasCap` check regardless (see panel-props.ts's own header
    // comments on these four fields).
    ['toRitXitProps', 'ritActive: state?.ritOn ?? false,'],
    ['toRitXitProps', 'xitActive: state?.ritTx ?? false,'],
    ['toCwProps', 'twinPeak: rx?.twinPeakFilter ?? false,'],
    ['toScanProps', 'scanning: state?.scanning ?? false,'],
    // toTxProps' entire batch-B family — see the explicit non-fix
    // rationale in panel-props.test.ts's "toTxProps — explicit non-fix"
    // describe block: TxPanel.svelte's settings-modal ValueControl calls
    // use an unguarded displayFn that would render "NaN%" for a non-finite
    // input, and TxPanel.svelte is not one of A12's four granted
    // production files. Deferred whole, not partially (numeric vs.
    // boolean), to keep the family's honesty guarantee internally
    // consistent for a future gate to finish.
    ['toTxProps', 'rfPower: state?.powerLevel ?? 0.5,'],
    ['toTxProps', 'micGain: state?.micGain ?? 128,'],
    ['toTxProps', 'monLevel: state?.monitorGain ?? 128,'],
    ['toTxProps', 'driveGain: state?.driveGain ?? 128,'],
    ['toTxProps', 'voxActive: state?.voxOn ?? false,'],
    ['toTxProps', 'compActive: state?.compressorOn ?? false,'],
  ];

  it.each(stillPresentOutOfScope)(
    '%s (out of this gate\'s scope, explicit non-fix) is untouched — still contains %j',
    (fn, literal) => {
      expect(functionBody(source, fn)).toContain(literal);
    },
  );
});

describe('frozen facts this gate must not disturb (MOR-1409 A11 regression pins)', () => {
  it('panel-adapters.ts makes zero runtime.send() calls (plan §3.4)', () => {
    const source = readSource(PANEL_ADAPTERS_PATH);
    expect(source).not.toContain('runtime.send(');
  });

  // MOR-1409 A13b (correction 5246842617 §5) supersedes this pin: the two
  // documented call sites were RadioLayout's — and the whole method's —
  // last production callers. They are deleted (not migrated to a live
  // binder call — VfoHeader already ignores the legacy props they fed; see
  // `vfo-header.isolated.test.ts`'s `VfoHeader source boundary` pin) in the
  // same head that removes `FrontendRuntime.send()` itself.
  it('RadioLayout.svelte makes zero runtime.send() calls (MOR-1409 A13b — send() is deleted)', () => {
    const source = readSource(RADIO_LAYOUT_PATH);
    expect(source).not.toMatch(/runtime\.send\(/);
  });
});
