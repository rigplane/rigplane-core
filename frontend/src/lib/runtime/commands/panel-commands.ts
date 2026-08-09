/**
 * Runtime command handler factories for panel controls.
 *
 * This module duplicates the relevant `make*Handlers` factories from
 * `components-v2/wiring/command-bus` so that `lib/runtime/adapters` can
 * import them without creating a `lib/runtime` → `components-v2` dependency.
 *
 * Do NOT import from `components-v2/*` here.  Pure filter helpers are
 * inlined below (originate from `components-v2/panels/filter-controls`)
 * until a neutral `$lib/radio/filter-controls` module is introduced by #996.
 *
 * See issue #999, parent #959 (M-4).
 */

import { sendCommand } from '$lib/transport/ws-client';
import {
  getActiveReceiver,
  getRadioState,
  patchRadioState,
} from '$lib/stores/radio.svelte';
import { getCapabilities, getControlRange } from '$lib/stores/capabilities.svelte';
import { isFieldAvailable } from '$lib/state/field-status';
import { runtime } from '../frontend-runtime';
import { consumePendingFocus } from '$lib/radio/pending-focus';
import { getModeFilter } from '$lib/radio/mode-filter-memory';
import { modInputCommand, modInputStateKey } from '$lib/radio/mod-input';
import { nbDepthDisplayToRaw, nrDisplayToRaw } from '$lib/radio/filter-controls';
import { dispatchRadioIntent, isNormalizedLevel, type RadioIntent } from './radio-intents';

/* ── Shared helpers ──────────────────────────────────────────────── */

type Receiver = 0 | 1;

const A03A_INTENT_NAMES = new Set<RadioIntent['name']>([
  'set_data_mode', 'set_data_off_mod_input', 'set_data1_mod_input', 'set_data2_mod_input', 'set_data3_mod_input', 'set_filter', 'set_filter_shape',
  'set_filter_width', 'set_if_shift', 'set_pbt_inner', 'set_pbt_outer',
]);

function cmd(name: string, params: Record<string, unknown> = {}): void {
  if (A03A_INTENT_NAMES.has(name as RadioIntent['name'])) {
    dispatchRadioIntent({ name, params } as RadioIntent);
    return;
  }
  sendCommand(name, params);
}

function knownActiveReceiver(field?: string, target?: 'MAIN' | 'SUB'): Receiver | null {
  const state = getRadioState();
  const receiverName = target ?? state?.active;
  if (
    !state
    || (receiverName !== 'MAIN' && receiverName !== 'SUB')
    || (!target && !isFieldAvailable(state, 'active'))
  ) return null;
  if (receiverName === 'SUB') {
    const receivers = getCapabilities()?.receivers;
    if (!Number.isSafeInteger(receivers) || (receivers as number) < 2 || !state.sub) return null;
  } else if (!state.main) return null;
  const receiver = receiverName === 'SUB' ? 1 : 0;
  const prefix = receiver === 1 ? 'sub' : 'main';
  return field && !isFieldAvailable(state, `${prefix}.${field}`) ? null : receiver;
}

function hasCapability(name: string): boolean {
  return getCapabilities()?.capabilities.includes(name) ?? false;
}

function knownReceiverField(field: string): Receiver | null {
  const receiver = knownActiveReceiver(field);
  if (receiver === null) return null;
  const state = getRadioState();
  const target = receiver === 1 ? state?.sub : state?.main;
  const value = (target as unknown as Record<string, unknown> | null | undefined)?.[field];
  return value === undefined || value === null ? null : receiver;
}

function knownTopLevelField(field: string): boolean {
  const state = getRadioState();
  const value = (state as unknown as Record<string, unknown> | null)?.[field];
  return state !== null && value !== undefined && value !== null && isFieldAvailable(state, field);
}

/* ── Inlined PBT / IF-shift helpers (from filter-controls.ts) ────── */
// TODO: replace with `$lib/radio/filter-controls` once #996 lands.

const FILTER_BIPOLAR_MIN = -1200;
const FILTER_BIPOLAR_MAX = 1200;

const PBT_DEFAULTS = { rawCenter: 128, displayMin: -1200, displayMax: 1200 } as const;

function pbtRange() {
  try {
    const ctrl = getControlRange('pbt_inner');
    if (
      ctrl &&
      ctrl.raw_center !== undefined &&
      ctrl.display_min !== undefined &&
      ctrl.display_max !== undefined
    ) {
      return {
        rawCenter: ctrl.raw_center,
        displayMin: ctrl.display_min,
        displayMax: ctrl.display_max,
      };
    }
  } catch {
    // capabilities store not available (e.g. in tests)
  }
  return PBT_DEFAULTS;
}

function pbtHzToRaw(hz: number): number {
  const { rawCenter, displayMax } = pbtRange();
  const raw = Math.round(hz * (rawCenter / displayMax) + rawCenter);
  return Math.max(0, Math.min(255, raw));
}

function clampToBipolarRange(value: number): number {
  return Math.max(FILTER_BIPOLAR_MIN, Math.min(FILTER_BIPOLAR_MAX, Math.round(value)));
}

function deriveIfShift(pbtInner: number, pbtOuter: number): number {
  return clampToBipolarRange((pbtInner + pbtOuter) / 2);
}

function mapIfShiftToPbt(
  targetIfShift: number,
  currentPbtInner: number,
  currentPbtOuter: number,
): { pbtInner: number; pbtOuter: number } {
  const currentIfShift = deriveIfShift(currentPbtInner, currentPbtOuter);
  const delta = clampToBipolarRange(targetIfShift) - currentIfShift;
  return {
    pbtInner: clampToBipolarRange(currentPbtInner + delta),
    pbtOuter: clampToBipolarRange(currentPbtOuter + delta),
  };
}

/* ── AGC Handlers ────────────────────────────────────────────────── */

export function makeAgcHandlers() {
  return {
    onAgcModeChange: (mode: number) => {
      const receiver = knownReceiverField('agc');
      if (!hasCapability('agc') || receiver === null || !Number.isSafeInteger(mode)) return;
      dispatchRadioIntent({ name: 'set_agc', params: { mode, receiver } });
    },
  };
}

/* ── Mode Handlers ───────────────────────────────────────────────── */

export function makeModeHandlers() {
  return {
    onModeChange: (mode: string) => {
      const pending = consumePendingFocus();
      const receiver = knownActiveReceiver('mode', pending ?? undefined);
      if (receiver === null) return;
      // MOR-495: recall the destination mode's remembered filter so the web
      // mirrors the front panel (mode-only 0x06 would force the radio's
      // mode-default filter, e.g. USB → FIL2).  Unseen mode → mode-only.
      const filter = getModeFilter(mode);
      if (filter !== undefined) {
        dispatchRadioIntent({ name: 'set_mode', params: { mode, filter, receiver } });
      } else {
        dispatchRadioIntent({ name: 'set_mode', params: { mode, receiver } });
      }
    },
    onDataModeChange: (mode: number) => {
      const receiver = knownActiveReceiver('dataMode');
      if (receiver === null) return;
      cmd('set_data_mode', { mode, receiver });
    },
    onModInputChange: (source: number) => {
      // MOR-616: route the new source to the active receiver's DATA group
      // (DATA OFF/1/2/3 MOD, CI-V 0x1A 05 00 0x91-0x94). The command
      // lifecycle stays separate until canonical readback confirms truth.
      const receiver = knownActiveReceiver('dataMode');
      const dataMode = receiver === null ? null : getActiveReceiver()?.dataMode;
      const state = getRadioState();
      if (
        !state
        || !Number.isSafeInteger(dataMode)
        || (dataMode as number) < 0
        || (dataMode as number) > 3
        || !isFieldAvailable(state, modInputStateKey(dataMode as number))
      ) return;
      cmd(modInputCommand(dataMode as number), { source });
    },
  };
}

/* ── Antenna Handlers ────────────────────────────────────────────── */

export function makeAntennaHandlers() {
  return {
    onSelectAnt1: () => {
      const state = getRadioState();
      if ((getCapabilities()?.antennas ?? 0) < 2 || !knownTopLevelField('rxAntenna1')
        || typeof state?.rxAntenna1 !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_antenna_1', params: { on: state.rxAntenna1 } });
    },
    onSelectAnt2: () => {
      const state = getRadioState();
      if ((getCapabilities()?.antennas ?? 0) < 2 || !knownTopLevelField('rxAntenna2')
        || typeof state?.rxAntenna2 !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_antenna_2', params: { on: state.rxAntenna2 } });
    },
    onToggleRxAnt: () => {
      const state = getRadioState();
      const tx = state?.txAntenna;
      if ((getCapabilities()?.antennas ?? 0) < 2 || !hasCapability('rx_antenna')
        || !knownTopLevelField('txAntenna') || (tx !== 1 && tx !== 2)) return;
      const field = tx === 2 ? 'rxAntenna2' : 'rxAntenna1';
      const current = tx === 2 ? state?.rxAntenna2 : state?.rxAntenna1;
      if (!knownTopLevelField(field) || typeof current !== 'boolean') return;
      const next = !current;
      if (tx === 2) {
        dispatchRadioIntent({ name: 'set_rx_antenna_ant2', params: { on: next } });
      } else {
        dispatchRadioIntent({ name: 'set_rx_antenna_ant1', params: { on: next } });
      }
    },
  };
}

/* ── RF Front End Handlers ───────────────────────────────────────── */

export function makeRfFrontEndHandlers() {
  return {
    onAttChange: (db: number) => {
      const receiver = knownActiveReceiver('att');
      if (receiver === null || !Number.isSafeInteger(db)) return;
      dispatchRadioIntent({ name: 'set_attenuator', params: { db, receiver } });
    },
    onPreChange: (level: number) => {
      const receiver = knownActiveReceiver('preamp');
      if (receiver === null || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_preamp', params: { level, receiver } });
    },
    onRfGainChange: (level: number) => {
      const receiver = knownActiveReceiver('rfGain');
      if (receiver === null || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_rf_gain', params: { level, receiver } });
    },
    onSquelchChange: (level: number) => {
      const receiver = knownActiveReceiver('squelch');
      if (receiver === null || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_squelch', params: { level, receiver } });
    },
    onDigiSelToggle: (on: boolean) => {
      const receiver = knownActiveReceiver('digisel');
      if (receiver === null || typeof on !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_digisel', params: { on, receiver } });
    },
    onIpPlusToggle: (on: boolean) => {
      const receiver = knownActiveReceiver('ipplus');
      if (receiver === null || typeof on !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_ip_plus', params: { on, receiver } });
    },
  };
}

/* ── RIT / XIT Handlers ──────────────────────────────────────────── */

export function makeRitXitHandlers() {
  return {
    onRitToggle: () => {
      const state = getRadioState();
      if (!hasCapability('rit') || knownActiveReceiver() === null
        || !knownTopLevelField('ritOn') || typeof state?.ritOn !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_rit_status', params: { on: !state.ritOn } });
    },
    onXitToggle: () => {
      const state = getRadioState();
      if (!hasCapability('xit') || knownActiveReceiver() === null
        || !knownTopLevelField('ritTx') || typeof state?.ritTx !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_rit_tx_status', params: { on: !state.ritTx } });
    },
    onRitOffsetChange: (hz: number) => {
      if (!hasCapability('rit') || knownActiveReceiver() === null
        || !knownTopLevelField('ritFreq') || !Number.isSafeInteger(hz)) return;
      dispatchRadioIntent({ name: 'set_rit_frequency', params: { freq: hz } });
    },
    onXitOffsetChange: (hz: number) => {
      // RIT and XIT share the same offset register
      if (!hasCapability('xit') || knownActiveReceiver() === null
        || !knownTopLevelField('ritFreq') || !Number.isSafeInteger(hz)) return;
      dispatchRadioIntent({ name: 'set_rit_frequency', params: { freq: hz } });
    },
    onClear: () => {
      if ((!hasCapability('rit') && !hasCapability('xit')) || knownActiveReceiver() === null
        || !knownTopLevelField('ritFreq')) return;
      dispatchRadioIntent({ name: 'set_rit_frequency', params: { freq: 0 } });
    },
  };
}

/* ── Scan Handlers ───────────────────────────────────────────────── */

export function makeScanHandlers() {
  return {
    onScanStart: (type: number) => {
      if (!Number.isSafeInteger(type)) return;
      dispatchRadioIntent({ name: 'scan_start', params: { type } });
    },
    onScanStop: () => {
      dispatchRadioIntent({ name: 'scan_stop', params: {} });
    },
    onDfSpanChange: (span: number) => {
      if (!Number.isSafeInteger(span)) return;
      dispatchRadioIntent({ name: 'scan_set_df_span', params: { span } });
    },
    onResumeChange: (mode: number) => {
      if (!Number.isSafeInteger(mode)) return;
      dispatchRadioIntent({ name: 'scan_set_resume', params: { mode } });
    },
  };
}

/* ── Meter Handlers ──────────────────────────────────────────────── */

export function makeMeterHandlers() {
  return {
    onMeterSourceChange: (source: string) => {
      patchRadioState({ meterSource: source as 'S' | 'SWR' | 'POWER' });
    },
  };
}

/* ── CW Panel Handlers ───────────────────────────────────────────── */

export function makeCwPanelHandlers() {
  return {
    onCwPitchChange: (value: number) => {
      if (!hasCapability('cw') || !knownTopLevelField('cwPitch') || !Number.isSafeInteger(value)) return;
      dispatchRadioIntent({ name: 'set_cw_pitch', params: { value } });
    },
    onKeySpeedChange: (speed: number) => {
      if (!hasCapability('cw') || !knownTopLevelField('keySpeed') || !Number.isSafeInteger(speed)) return;
      dispatchRadioIntent({ name: 'set_key_speed', params: { speed } });
    },
    onBreakInToggle: () => {
      const current = getRadioState()?.breakIn;
      if (!hasCapability('cw') || !hasCapability('break_in') || !knownTopLevelField('breakIn')
        || !Number.isSafeInteger(current)) return;
      dispatchRadioIntent({ name: 'set_break_in', params: { mode: (current as number) > 0 ? 0 : 1 } });
    },
    onBreakInModeChange: (mode: number) => {
      if (!hasCapability('cw') || !hasCapability('break_in') || !knownTopLevelField('breakIn')
        || !Number.isSafeInteger(mode)) return;
      dispatchRadioIntent({ name: 'set_break_in', params: { mode } });
    },
    onApfChange: (mode: number) => {
      const receiver = knownReceiverField('apfTypeLevel');
      if (!hasCapability('cw') || !hasCapability('apf') || receiver === null
        || !Number.isSafeInteger(mode)) return;
      dispatchRadioIntent({ name: 'set_apf', params: { mode, receiver } });
    },
    onTwinPeakToggle: () => {
      const receiver = knownReceiverField('twinPeakFilter');
      const state = getRadioState();
      const rx = receiver === 0 ? state?.main : state?.sub;
      if (!hasCapability('cw') || !hasCapability('twin_peak') || receiver === null
        || typeof rx?.twinPeakFilter !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_twin_peak', params: { on: !rx.twinPeakFilter, receiver } });
    },
    onAutoTune: () => {
      if (!hasCapability('cw') || knownActiveReceiver() === null) return;
      dispatchRadioIntent({ name: 'cw_auto_tune', params: {} });
    },
    onWpmChange: (speed: number) => {
      if (!hasCapability('cw') || !knownTopLevelField('keySpeed') || !Number.isSafeInteger(speed)) return;
      dispatchRadioIntent({ name: 'set_key_speed', params: { speed } });
    },
    onBreakInDelayChange: (level: number) => {
      if (!hasCapability('cw') || !hasCapability('break_in') || !knownTopLevelField('breakInDelay')
        || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_break_in_delay', params: { level } });
    },
    onSidetonePitchChange: (value: number) => {
      if (!hasCapability('cw') || !knownTopLevelField('cwPitch') || !Number.isSafeInteger(value)) return;
      dispatchRadioIntent({ name: 'set_cw_pitch', params: { value } });
    },
    onSidetoneLevelChange: (level: number) => {
      if (!hasCapability('cw') || !knownTopLevelField('monitorGain') || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_monitor_gain', params: { level } });
    },
    onReversePaddleToggle: () => {
      const current = getRadioState()?.dashRatio;
      if (!hasCapability('cw') || !knownTopLevelField('dashRatio')
        || !Number.isSafeInteger(current)) return;
      dispatchRadioIntent({ name: 'set_dash_ratio', params: { ratio: (current as number) < 0 ? 0 : -1 } });
    },
  };
}

/* ── DSP Handlers ────────────────────────────────────────────────── */

export function makeDspHandlers() {
  return {
    onNrModeChange: (mode: number) => {
      const receiver = knownReceiverField('nr');
      if (!hasCapability('nr') || receiver === null || !Number.isSafeInteger(mode)) return;
      dispatchRadioIntent({ name: 'set_nr', params: { on: mode > 0, receiver } });
    },
    onNrLevelChange: (level: number) => {
      const receiver = knownReceiverField('nrLevel');
      if (!hasCapability('nr') || receiver === null || !Number.isFinite(level)) return;
      // MOR-490: slider is 0-15 (front-panel scale); wire is 0-255 BCD.
      const raw = nrDisplayToRaw(level);
      dispatchRadioIntent({ name: 'set_nr_level', params: { level: raw, receiver } });
    },
    onNbToggle: (on: boolean) => {
      const receiver = knownReceiverField('nb');
      if (!hasCapability('nb') || receiver === null || typeof on !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_nb', params: { on, receiver } });
    },
    onNbLevelChange: (level: number) => {
      const receiver = knownReceiverField('nbLevel');
      if (!hasCapability('nb') || receiver === null || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_nb_level', params: { level, receiver } });
    },
    onNotchModeChange: (mode: string) => {
      const receiver = knownReceiverField('autoNotch');
      if (!hasCapability('notch') || receiver === null
        || knownReceiverField('manualNotch') !== receiver
        || (mode !== 'auto' && mode !== 'manual' && mode !== 'off')) return;
      if (mode === 'auto') {
        dispatchRadioIntent({ name: 'set_auto_notch', params: { on: true, receiver } });
      } else if (mode === 'manual') {
        dispatchRadioIntent({ name: 'set_manual_notch', params: { on: true, receiver } });
      } else {
        dispatchRadioIntent({ name: 'set_auto_notch', params: { on: false, receiver } });
        dispatchRadioIntent({ name: 'set_manual_notch', params: { on: false, receiver } });
      }
    },
    onNotchFreqChange: (value: number) => {
      const receiver = knownActiveReceiver();
      if (!hasCapability('notch') || receiver === null || !knownTopLevelField('notchFilter')
        || !Number.isSafeInteger(value)) return;
      dispatchRadioIntent({ name: 'set_notch_filter', params: { value, receiver } });
    },
    onNbDepthChange: (level: number) => {
      // MOR-498: slider is 1-10 (front-panel scale); wire is 0-9.  Store the
      // wire value the backend expects (the adapter offsets wire -> display).
      if (!getCapabilities()?.controls?.nb_depth || knownActiveReceiver() === null
        || !knownTopLevelField('nbDepth') || !Number.isFinite(level)) return;
      const wire = nbDepthDisplayToRaw(level);
      dispatchRadioIntent({ name: 'set_nb_depth', params: { level: wire } });
    },
    onNbWidthChange: (level: number) => {
      if (!getCapabilities()?.controls?.nb_depth || knownActiveReceiver() === null
        || !knownTopLevelField('nbWidth') || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_nb_width', params: { level } });
    },
    onManualNotchWidthChange: (value: number) => {
      const receiver = knownReceiverField('manualNotchWidth');
      if (!hasCapability('notch') || receiver === null || !Number.isSafeInteger(value)) return;
      dispatchRadioIntent({ name: 'set_manual_notch_width', params: { value, receiver } });
    },
    onAgcTimeChange: (value: number) => {
      const receiver = knownReceiverField('agcTimeConstant');
      if (!hasCapability('agc') || receiver === null || !Number.isSafeInteger(value)) return;
      dispatchRadioIntent({ name: 'set_agc_time_constant', params: { value, receiver } });
    },
  };
}

/* ── TX Handlers ─────────────────────────────────────────────────── */

export function makeTxHandlers() {
  return {
    onRfPowerChange: (level: number) => {
      if (!hasCapability('tx') || !knownTopLevelField('powerLevel') || !Number.isFinite(level)) return;
      dispatchRadioIntent({ name: 'set_rf_power', params: { level } });
    },
    onMicGainChange: (level: number) => {
      if (!hasCapability('tx') || !knownTopLevelField('micGain') || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_mic_gain', params: { level } });
    },
    onAtuToggle: () => {
      const current = getRadioState()?.tunerStatus;
      if (!hasCapability('tx') || !hasCapability('tuner') || !knownTopLevelField('tunerStatus')
        || !Number.isSafeInteger(current)) return;
      dispatchRadioIntent({ name: 'set_tuner_status', params: { value: (current as number) > 0 ? 0 : 1 } });
    },
    onAtuTune: () => {
      if (!hasCapability('tx') || !hasCapability('tuner') || !knownTopLevelField('tunerStatus')) return;
      dispatchRadioIntent({ name: 'set_tuner_status', params: { value: 2 } });
    },
    onVoxToggle: () => {
      const current = getRadioState()?.voxOn;
      if (!hasCapability('tx') || !hasCapability('vox') || !knownTopLevelField('voxOn')
        || typeof current !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_vox', params: { on: !current } });
    },
    onCompToggle: () => {
      const current = getRadioState()?.compressorOn;
      if (!hasCapability('tx') || !hasCapability('compressor') || !knownTopLevelField('compressorOn')
        || typeof current !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_compressor', params: { on: !current } });
    },
    onCompLevelChange: (level: number) => {
      if (!hasCapability('tx') || !hasCapability('compressor') || !knownTopLevelField('compressorLevel')
        || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_compressor_level', params: { level } });
    },
    onMonToggle: () => {
      const current = getRadioState()?.monitorOn;
      if (!hasCapability('tx') || !hasCapability('monitor') || !knownTopLevelField('monitorOn')
        || typeof current !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_monitor', params: { on: !current } });
    },
    onMonLevelChange: (level: number) => {
      if (!hasCapability('tx') || !hasCapability('monitor') || !knownTopLevelField('monitorGain')
        || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_monitor_gain', params: { level } });
    },
    onDriveGainChange: (level: number) => {
      if (!hasCapability('tx') || !hasCapability('drive_gain') || !knownTopLevelField('driveGain')
        || !Number.isSafeInteger(level)) return;
      dispatchRadioIntent({ name: 'set_drive_gain', params: { level } });
    },
  };
}

/* ── Filter Handlers ─────────────────────────────────────────────── */

export function makeFilterHandlers() {
  return {
    onFilterChange: (filter: number) => {
      const receiver = knownActiveReceiver('filter');
      if (receiver === null) return;
      cmd('set_filter', { filter, receiver });
    },
    onFilterWidthChange: (() => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      return (width: number) => {
        if (knownActiveReceiver('filterWidth') === null) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          const receiver = knownActiveReceiver('filterWidth');
          if (receiver !== null) cmd('set_filter_width', { width, receiver });
        }, 200);
      };
    })(),
    onFilterShapeChange: (shape: number) => {
      const receiver = knownActiveReceiver('filterShape');
      if (receiver === null) return;
      cmd('set_filter_shape', { shape, receiver });
    },
    onFilterPresetChange: (() => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      return (filter: number, width: number) => {
        const receiver = knownActiveReceiver('filter');
        const activeFilter = receiver === null ? null : getActiveReceiver()?.filter;
        if (receiver === null || !Number.isSafeInteger(activeFilter)) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          const currentReceiver = knownActiveReceiver('filter');
          const currentActive = currentReceiver === null ? null : getActiveReceiver()?.filter;
          if (currentReceiver === null || !Number.isSafeInteger(currentActive)) return;
          if (filter !== currentActive) {
            cmd('set_filter', { filter, receiver: currentReceiver });
          }
          cmd('set_filter_width', { width, receiver: currentReceiver });
          if (filter !== currentActive) {
            cmd('set_filter', { filter: currentActive as number, receiver: currentReceiver });
          }
        }, 200);
      };
    })(),
    onFilterDefaults: (defaults: number[]) => {
      const receiver = knownActiveReceiver('filterWidth');
      const activeFilter = receiver === null ? null : getActiveReceiver()?.filter;
      if (receiver === null || !Number.isSafeInteger(activeFilter)) return;
      for (let i = 0; i < defaults.length; i++) {
        const filter = i + 1;
        if (filter !== activeFilter) {
          cmd('set_filter', { filter, receiver });
        }
        cmd('set_filter_width', { width: defaults[i], receiver });
      }
      if ((activeFilter as number) <= defaults.length) {
        cmd('set_filter', { filter: activeFilter as number, receiver });
      }
    },
    onIfShiftChange: (value: number) => {
      const caps = getCapabilities();
      if (!caps) return;
      if (caps.capabilities.includes('if_shift')) {
        const receiver = knownActiveReceiver('ifShift');
        if (receiver !== null) cmd('set_if_shift', { offset: value, receiver });
      } else if (caps.capabilities.includes('pbt')) {
        const receiver = knownActiveReceiver('pbtInner');
        const state = getRadioState();
        const activeRx = getActiveReceiver();
        const prefix = receiver === 1 ? 'sub' : 'main';
        if (
          receiver === null
          || !state
          || !isFieldAvailable(state, `${prefix}.pbtOuter`)
          || typeof activeRx?.pbtInner !== 'number'
          || typeof activeRx.pbtOuter !== 'number'
        ) return;
        const { pbtInner, pbtOuter } = mapIfShiftToPbt(
          value,
          activeRx.pbtInner,
          activeRx.pbtOuter,
        );
        cmd('set_pbt_inner', { value: pbtHzToRaw(pbtInner), receiver });
        cmd('set_pbt_outer', { value: pbtHzToRaw(pbtOuter), receiver });
      }
    },
    onPbtInnerChange: (value: number) => {
      const receiver = knownActiveReceiver('pbtInner');
      if (receiver === null) return;
      cmd('set_pbt_inner', { value: pbtHzToRaw(value), receiver });
    },
    onPbtOuterChange: (value: number) => {
      const receiver = knownActiveReceiver('pbtOuter');
      if (receiver === null) return;
      cmd('set_pbt_outer', { value: pbtHzToRaw(value), receiver });
    },
    onPbtReset: () => {
      const receiver = knownActiveReceiver('pbtInner');
      const state = getRadioState();
      const prefix = receiver === 1 ? 'sub' : 'main';
      if (receiver === null || !state || !isFieldAvailable(state, `${prefix}.pbtOuter`)) return;
      const center = pbtHzToRaw(0);
      cmd('set_pbt_inner', { value: center, receiver });
      cmd('set_pbt_outer', { value: center, receiver });
    },
  };
}

/* ── Band Handlers ───────────────────────────────────────────────── */

export function makeBandHandlers() {
  return {
    onBandSelect: (_name: string, freq: number, bsrCode?: number) => {
      const receiver = knownReceiverField('freqHz');
      if (receiver === null || !Number.isSafeInteger(freq)
        || (bsrCode !== undefined && !Number.isSafeInteger(bsrCode))) return;
      if (bsrCode !== undefined) {
        dispatchRadioIntent({ name: 'set_band', params: { band: bsrCode } });
      } else {
        dispatchRadioIntent({ name: 'set_freq', params: { freq, receiver } });
      }
    },
  };
}

/* ── Preset Handlers ─────────────────────────────────────────────── */

export function makePresetHandlers() {
  const select = (freq: number, mode: string, filter = 1): void => {
    const receiver = knownReceiverField('freqHz');
    if (receiver === null || knownReceiverField('mode') !== receiver
      || !Number.isSafeInteger(freq) || typeof mode !== 'string' || mode.length === 0
      || !Number.isSafeInteger(filter)) return;
    dispatchRadioIntent({ name: 'set_freq', params: { freq, receiver } });
    dispatchRadioIntent({ name: 'set_mode', params: { mode, filter, receiver } });
  };
  return {
    onPresetSelect: select,
    onFreqPreset: select,
  };
}

/* ── RX Audio Handlers ───────────────────────────────────────────── */

let savedAfLevel: number | null = null;

export function makeRxAudioHandlers() {
  return {
    onMonitorModeChange: (mode: string) => {
      if (mode !== 'live' && mode !== 'mute' && mode !== 'local' && mode !== 'radio') return;
      if (mode === 'live') {
        runtime.setMuted(false);
        if (savedAfLevel !== null) {
          const receiver = knownReceiverField('afLevel');
          if (hasCapability('af_level') && receiver !== null) {
            dispatchRadioIntent({ name: 'set_af_level', params: { level: savedAfLevel, receiver } });
          }
          savedAfLevel = null;
        }
        runtime.setRxLive(true);
        return;
      }

      runtime.setRxLive(false);

      if (mode === 'mute') {
        runtime.setMuted(true);
        const receiver = knownReceiverField('afLevel');
        const state = getRadioState();
        const currentAf = receiver === 1 ? state?.sub?.afLevel : state?.main?.afLevel;
        if (hasCapability('af_level') && receiver !== null && isNormalizedLevel(currentAf)) {
          if (savedAfLevel === null) savedAfLevel = currentAf;
          dispatchRadioIntent({ name: 'set_af_level', params: { level: 0, receiver } });
        }
      } else {
        runtime.setMuted(false);
        if (savedAfLevel !== null) {
          const receiver = knownReceiverField('afLevel');
          if (hasCapability('af_level') && receiver !== null) {
            dispatchRadioIntent({ name: 'set_af_level', params: { level: savedAfLevel, receiver } });
          }
          savedAfLevel = null;
        }
      }
    },
    onAfLevelChange: (level: number) => {
      if (!isNormalizedLevel(level)) return;
      if (runtime.rxEnabled) {
        runtime.setRxVolume(level);
        runtime.setVolume(Math.round(level * 100));
      } else {
        const receiver = knownReceiverField('afLevel');
        if (!hasCapability('af_level') || receiver === null) return;
        dispatchRadioIntent({ name: 'set_af_level', params: { level, receiver } });
      }
    },
  };
}
