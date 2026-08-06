/**
 * MOR-1070 stub for `components-v2/wiring/command-bus`.
 *
 * Stubbed so a capture run opens no WebSocket and constructs no AudioContext
 * (the real module pulls in `$lib/transport/ws-client` and
 * `$lib/audio/audio-manager` at module scope) — the harness must be
 * deterministic and offline.
 *
 * MOR-1271: this stub went stale. `SemanticRadioSurfaces.svelte` gained a
 * `txAuxIntents` bundle in the MOR-1244/MOR-1265 vocabulary slices and now
 * imports `makeTxHandlers` and `makeVoxHandlers` alongside `makeVfoHandlers`;
 * the stub exported only the last of the three. A stub that omits a named
 * export fails at MODULE RESOLUTION rather than at call time, so the harness
 * could not build its own entry and NO fixture loaded at all — the whole
 * MOR-1070 verification surface was dark, not merely degraded.
 *
 * MOR-1320: second occurrence of the exact same class. The MOR-1279 rxAudio
 * slice added `makeAudioRoutingHandlers`, `makeModeHandlers` and
 * `makeRxAudioHandlers` (the last re-exported by the real module from
 * `$lib/runtime/commands/panel-commands`) to the wiring's import list, and
 * this stub again omitted all three — dark harness again, silently, until
 * someone happened to run `capture.mjs` by hand. `stub-export-parity.test.ts`
 * (`src/components-v2/wiring/__tests__/`) now asserts stub ⊇ real so this
 * fails CI at commit time instead of at some future capture attempt.
 *
 * That guard checks the WHOLE export surface, not just the names the current
 * wiring tree happens to import — MOR-1271's fix was deliberately minimal
 * ("the drift guard stays in [this ticket]"), so at the time this guard
 * landed the stub was still missing thirteen more factories the real module
 * exports but nothing in the fixture-mounted tree references yet
 * (`makeAgcHandlers`, `makeAntennaHandlers`, `makeBandHandlers`,
 * `makeCwPanelHandlers`, `makeDspHandlers`, `makeFilterHandlers`,
 * `makeKeyboardHandlers`, `makeMeterHandlers`, `makePresetHandlers`,
 * `makeRfFrontEndHandlers`, `makeRitXitHandlers`, `makeScanHandlers`,
 * `makeSystemHandlers`). Full parity is the only guard the test can enforce
 * without also encoding "and here is every place in the tree that imports
 * this module today" — a list that changes on every future wiring PR.
 *
 * The rule this file therefore has to keep: mirror every export the real
 * module has, not only the ones a given fixture exercises. Every handler
 * records rather than commands — the harness asserts that an intent reached
 * the bus, and must never reach a radio.
 */
import { record } from '../harness-state';

/** Records `<channel>.<handler>` with the call's arguments, for a list of handler names. */
function recorders<K extends string>(
  channel: string, names: readonly K[],
): Record<K, (...args: unknown[]) => void> {
  return Object.fromEntries(
    names.map((name) => [name, (...args: unknown[]) => record(`${channel}.${name}`, args)]),
  ) as Record<K, (...args: unknown[]) => void>;
}

/** MOR-1307: `onMainFreqChange`/`onSubFreqChange` are the per-receiver
 *  `set_freq` path the semantic wiring routes BOTH digit tuning (MOR-1322)
 *  and the band surface's frequency entry / no-BSR band fallback through.
 *  Missing here they were not a resolution failure — the names are read off
 *  the returned object, not the module — but the first captured gesture that
 *  reached one would have thrown inside the harness instead of recording. */
export function makeVfoHandlers() {
  return {
    onVfoSelect: (...args: unknown[]): void => record('vfo.select', args),
    onSplitToggle: (...args: unknown[]): void => record('vfo.split', args),
    onDualWatchToggle: (...args: unknown[]): void => record('vfo.dualWatch', args),
    onMainFreqChange: (...args: unknown[]): void => record('vfo.mainFreq', args),
    onSubFreqChange: (...args: unknown[]): void => record('vfo.subFreq', args),
  };
}

export function makeVoxHandlers() {
  return recorders('vox', [
    'onVoxToggle', 'onVoxGainChange', 'onAntiVoxGainChange', 'onVoxDelayChange',
  ] as const);
}

export function makeTxHandlers() {
  return recorders('tx', [
    'onRfPowerChange', 'onMicGainChange', 'onAtuToggle', 'onAtuTune', 'onVoxToggle',
    'onCompToggle', 'onCompLevelChange', 'onMonToggle', 'onMonLevelChange', 'onDriveGainChange',
  ] as const);
}

/** MOR-1279: `onModInputChange` is the one `makeModeHandlers` member the
 *  rxAudio wiring calls directly (the LAN MOD-input remedy); the other two
 *  are mirrored for the same reason every other unused member here is —
 *  a stub that omits a name fails at module resolution, not call time. */
export function makeModeHandlers() {
  return recorders('mode', ['onModeChange', 'onDataModeChange', 'onModInputChange'] as const);
}

/** MOR-1279: dual-RX audio routing (focus / stereo split / per-channel gain).
 *  `restoreFromStorage` is the one member with a real return value in the
 *  shipped module (it reads `localStorage` and answers the restored config)
 *  — the harness must never touch `localStorage`, so the stub answers a
 *  fixed, honest "nothing was stored" shape instead of recording+returning
 *  undefined, which would crash the first destructure. */
export function makeAudioRoutingHandlers() {
  return {
    ...recorders('audioRouting', [
      'onFocusChange', 'onSplitStereoChange', 'onChannelGainChange',
    ] as const),
    restoreFromStorage: (): {
      focus: 'main' | 'sub' | 'both'; split_stereo: boolean;
      main_gain_db: number; sub_gain_db: number;
    } => {
      record('audioRouting.restoreFromStorage', []);
      return { focus: 'both', split_stereo: false, main_gain_db: 0, sub_gain_db: 0 };
    },
  };
}

/** MOR-1279: RX-audio monitor mode + AF level. Re-exported by the real
 *  module from `$lib/runtime/commands/panel-commands` — stubbed directly
 *  here instead, since stubbing `command-bus.ts` wholesale means nothing
 *  reaches that real module through this seam anyway. */
export function makeRxAudioHandlers() {
  return recorders('rxAudio', ['onMonitorModeChange', 'onAfLevelChange'] as const);
}

/* ── MOR-1320: the remaining factories, unreached by the fixture-mounted
 *  tree today but required for `stub-export-parity.test.ts` — see the file
 *  header. One name set per real factory, in the real module's own order. */

export function makeRfFrontEndHandlers() {
  return recorders('rfFrontEnd', [
    'onAttChange', 'onPreChange', 'onRfGainChange', 'onSquelchChange',
    'onDigiSelToggle', 'onIpPlusToggle',
  ] as const);
}

export function makeFilterHandlers() {
  return recorders('filter', [
    'onFilterChange', 'onFilterWidthChange', 'onFilterShapeChange', 'onFilterPresetChange',
    'onFilterDefaults', 'onIfShiftChange', 'onPbtInnerChange', 'onPbtOuterChange', 'onPbtReset',
  ] as const);
}

export function makeAgcHandlers() {
  return recorders('agc', ['onAgcModeChange'] as const);
}

export function makeRitXitHandlers() {
  return recorders('ritXit', [
    'onRitToggle', 'onXitToggle', 'onRitOffsetChange', 'onXitOffsetChange', 'onClear',
  ] as const);
}

export function makeDspHandlers() {
  return recorders('dsp', [
    'onNrModeChange', 'onNrLevelChange', 'onNbToggle', 'onNbLevelChange', 'onNotchModeChange',
    'onNotchFreqChange', 'onNbDepthChange', 'onNbWidthChange', 'onManualNotchWidthChange',
    'onAgcTimeChange',
  ] as const);
}

export function makeCwPanelHandlers() {
  return recorders('cwPanel', [
    'onCwPitchChange', 'onKeySpeedChange', 'onBreakInToggle', 'onBreakInModeChange',
    'onApfChange', 'onTwinPeakToggle', 'onAutoTune', 'onWpmChange', 'onBreakInDelayChange',
    'onSidetonePitchChange', 'onSidetoneLevelChange', 'onReversePaddleToggle', 'onKeyerTypeChange',
  ] as const);
}

export function makePresetHandlers() {
  return recorders('preset', ['onPresetSelect', 'onFreqPreset'] as const);
}

/** MOR-1307: reached by the fixture-mounted tree from this slice on — the
 *  band surface's BSR path calls `onBandSelect`. Name set unchanged. */
export function makeBandHandlers() {
  return recorders('band', ['onBandSelect'] as const);
}

export function makeAntennaHandlers() {
  return recorders('antenna', ['onSelectAnt1', 'onSelectAnt2', 'onToggleRxAnt'] as const);
}

export function makeMeterHandlers() {
  return recorders('meter', ['onMeterSourceChange'] as const);
}

export function makeSystemHandlers() {
  return recorders('system', ['onDialLock', 'onPowerOff', 'onSpeak'] as const);
}

export function makeScanHandlers() {
  return recorders('scan', [
    'onScanStart', 'onScanStop', 'onDfSpanChange', 'onResumeChange',
  ] as const);
}

/** `dispatch` takes the same shape as the real module's — a single
 *  `KeyboardActionConfig` — but `recorders()` only names bare handlers, so
 *  it is built directly rather than forced through that helper. */
export function makeKeyboardHandlers() {
  return {
    dispatch: (...args: unknown[]): void => record('keyboard.dispatch', args),
  };
}
