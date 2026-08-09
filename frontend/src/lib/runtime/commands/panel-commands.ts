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
import {
  capabilitiesMatchGeneration,
  getCapabilities,
  getControlRange,
} from '$lib/stores/capabilities.svelte';
import { isFieldAvailable } from '$lib/state/field-status';
import { runtime } from '../frontend-runtime';
import { consumePendingFocus, setPendingFocus } from '$lib/radio/pending-focus';
import { getModeFilter } from '$lib/radio/mode-filter-memory';
import { modInputCommand, modInputStateKey } from '$lib/radio/mod-input';
import { nbDepthDisplayToRaw, nrDisplayToRaw } from '$lib/radio/filter-controls';
import { audioManager } from '$lib/audio/audio-manager';
import { getTuningStep } from '$lib/stores/tuning.svelte';
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

function currentA03cContext() {
  const state = getRadioState();
  const caps = getCapabilities();
  if (!state || !caps || !capabilitiesMatchGeneration(state.providerGeneration)) return null;
  const expectedReceivers = caps.vfoScheme === 'single' || caps.vfoScheme === 'ab' ? 1
    : caps.vfoScheme === 'ab_shared' || caps.vfoScheme === 'main_sub' ? 2 : 0;
  if (expectedReceivers === 0 || caps.receivers !== expectedReceivers) return null;
  return { state, caps };
}

function knownA03cTopLevelField(
  context: NonNullable<ReturnType<typeof currentA03cContext>>,
  field: string,
): boolean {
  const value = (context.state as unknown as Record<string, unknown>)[field];
  return value !== undefined && value !== null && isFieldAvailable(context.state, field);
}

function knownA03cReceiver(
  context: NonNullable<ReturnType<typeof currentA03cContext>>,
  target: 'MAIN' | 'SUB',
  field?: string,
): Receiver | null {
  if (target !== 'MAIN' && target !== 'SUB') return null;
  if (target === 'SUB'
    && (context.caps.receivers < 2 || !context.caps.capabilities.includes('dual_rx'))) return null;
  const receiver: Receiver = target === 'SUB' ? 1 : 0;
  const state = receiver === 1 ? context.state.sub : context.state.main;
  if (!state) return null;
  if (!field) return receiver;
  const value = (state as unknown as Record<string, unknown>)[field];
  const path = `${receiver === 1 ? 'sub' : 'main'}.${field}`;
  return value !== undefined && value !== null && isFieldAvailable(context.state, path)
    ? receiver : null;
}

function toggleVox(): void {
  const context = currentA03cContext();
  const current = context?.state.voxOn;
  if (!context || !context.caps.capabilities.includes('tx')
    || !context.caps.capabilities.includes('vox')
    || !knownA03cTopLevelField(context, 'voxOn') || typeof current !== 'boolean') return;
  dispatchRadioIntent({ name: 'set_vox', params: { on: !current } });
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

/* ── Dual-RX audio routing (#756) ──────────────────────────────────
 * UI surface for the pipeline plumbed in #755 (audio_config WS +
 * CI-V Phones L/R Mix) and #757 (RxPlayer routing graph).  Three
 * widgets: focus selector, stereo split toggle, per-channel gain.
 */

const LS_FOCUS = 'icom.audio.focus';
const LS_SPLIT = 'icom.audio.split_stereo';
const LS_MAIN_DB = 'icom.audio.main_gain_db';
const LS_SUB_DB = 'icom.audio.sub_gain_db';

type AudioFocus = 'main' | 'sub' | 'both';

function _ls<T>(key: string, parse: (raw: string) => T | null): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return null;
    return parse(raw);
  } catch {
    return null;
  }
}

function _lsSet(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* quota / private mode — ignore */ }
}

export function makeAudioRoutingHandlers() {
  return {
    onFocusChange: (focus: AudioFocus) => {
      audioManager.setAudioConfig({ focus });
      _lsSet(LS_FOCUS, focus);
    },
    onSplitStereoChange: (on: boolean) => {
      audioManager.setAudioConfig({ split_stereo: on });
      _lsSet(LS_SPLIT, on ? '1' : '0');
    },
    onChannelGainChange: (channel: 'main' | 'sub', db: number) => {
      const safe = Number.isFinite(db) ? db : 0;
      if (channel === 'main') {
        audioManager.setAudioConfig({ main_gain_db: safe });
        _lsSet(LS_MAIN_DB, String(safe));
      } else {
        audioManager.setAudioConfig({ sub_gain_db: safe });
        _lsSet(LS_SUB_DB, String(safe));
      }
    },
    restoreFromStorage: () => {
      const focus = _ls<AudioFocus>(LS_FOCUS, (r) =>
        r === 'main' || r === 'sub' || r === 'both' ? r : null
      );
      const split = _ls<boolean>(LS_SPLIT, (r) => r === '1');
      const mainDb = _ls<number>(LS_MAIN_DB, (r) => {
        const n = Number(r);
        return Number.isFinite(n) ? n : null;
      });
      const subDb = _ls<number>(LS_SUB_DB, (r) => {
        const n = Number(r);
        return Number.isFinite(n) ? n : null;
      });
      const cfg: Record<string, unknown> = {};
      if (focus !== null) cfg.focus = focus;
      if (split !== null) cfg.split_stereo = split;
      if (mainDb !== null) cfg.main_gain_db = mainDb;
      if (subDb !== null) cfg.sub_gain_db = subDb;
      if (Object.keys(cfg).length > 0) {
        audioManager.setAudioConfig(cfg);
      }
      return {
        focus: focus ?? 'both' as AudioFocus,
        split_stereo: split ?? false,
        main_gain_db: mainDb ?? 0,
        sub_gain_db: subDb ?? 0,
      };
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
    onAutoNotchToggle: () => {
      const receiver = knownReceiverField('autoNotch');
      const current = receiver === 0 ? getRadioState()?.main?.autoNotch : getRadioState()?.sub?.autoNotch;
      if (!hasCapability('notch') || receiver === null || typeof current !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_auto_notch', params: { on: !current, receiver } });
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
    onVoxToggle: toggleVox,
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

/* ── VFO Handlers ────────────────────────────────────────────────── */

function activateReceiver(
  target: 'MAIN' | 'SUB',
  context = currentA03cContext(),
): boolean {
  if (!context || knownA03cReceiver(context, target) === null) return false;
  dispatchRadioIntent({ name: 'set_vfo', params: { vfo: target } });
  audioManager.setAudioConfig({ focus: target === 'SUB' ? 'sub' : 'main' });
  return true;
}

function focusModePanel(target: 'MAIN' | 'SUB'): void {
  if (!activateReceiver(target)) return;
  setPendingFocus(target);
  const modePanel = document.querySelector<HTMLElement>('[data-mode-panel="true"]');
  if (!modePanel) return;
  modePanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  modePanel.dataset.highlight = 'true';
  window.setTimeout(() => {
    if (modePanel.dataset.highlight === 'true') delete modePanel.dataset.highlight;
  }, 1200);
}

function supportsVfoSlot(
  context: NonNullable<ReturnType<typeof currentA03cContext>>,
  slot: 'A' | 'B' | null,
): boolean {
  if (slot === null) return true;
  if (slot !== 'A' && slot !== 'B') return false;
  return context.caps.vfoScheme === 'ab' || context.caps.vfoScheme === 'main_sub';
}

export function makeVfoHandlers() {
  return {
    onSwap: () => {
      const context = currentA03cContext();
      if (!context || context.caps.vfoScheme === 'single') return;
      dispatchRadioIntent({ name: 'vfo_swap', params: {} });
    },
    onEqual: () => {
      const context = currentA03cContext();
      if (!context || context.caps.vfoScheme === 'single') return;
      dispatchRadioIntent({ name: 'vfo_equalize', params: {} });
    },
    onSplitToggle: () => {
      const context = currentA03cContext();
      const current = context?.state.split;
      if (!context || !context.caps.capabilities.includes('split')
        || !knownA03cTopLevelField(context, 'split') || typeof current !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_split', params: { on: !current } });
    },
    onMainVfoClick: () => { activateReceiver('MAIN'); },
    onSubVfoClick: () => { activateReceiver('SUB'); },
    onVfoSelect: (receiver: 'MAIN' | 'SUB', slot: 'A' | 'B' | null) => {
      const context = currentA03cContext();
      if (!context || !knownA03cTopLevelField(context, 'active')
        || knownA03cReceiver(context, receiver) === null
        || !supportsVfoSlot(context, slot)) return;
      if (context.state.active !== receiver && !activateReceiver(receiver, context)) return;
      if (slot !== null) dispatchRadioIntent({ name: 'set_vfo', params: { vfo: slot } });
    },
    onMainModeClick: () => focusModePanel('MAIN'),
    onSubModeClick: () => focusModePanel('SUB'),
    onMainFreqChange: (freq: number) => {
      const context = currentA03cContext();
      if (!context || knownA03cReceiver(context, 'MAIN', 'freqHz') !== 0
        || !Number.isSafeInteger(freq)) return;
      dispatchRadioIntent({ name: 'set_freq', params: { freq, receiver: 0 } });
    },
    onSubFreqChange: (freq: number) => {
      const context = currentA03cContext();
      if (!context || knownA03cReceiver(context, 'SUB', 'freqHz') !== 1
        || !Number.isSafeInteger(freq)) return;
      dispatchRadioIntent({ name: 'set_freq', params: { freq, receiver: 1 } });
    },
    onFreqChange: (freq: number, receiver?: Receiver) => {
      const context = currentA03cContext();
      const target = receiver === 0 ? 'MAIN' : receiver === 1 ? 'SUB' : null;
      if (!context || target === null || knownA03cReceiver(context, target, 'freqHz') !== receiver
        || !Number.isSafeInteger(freq)) return;
      dispatchRadioIntent({ name: 'set_freq', params: { freq, receiver } });
    },
    onModeChange: (mode: string, receiver?: Receiver) => {
      const context = currentA03cContext();
      const target = receiver === 0 ? 'MAIN' : receiver === 1 ? 'SUB' : null;
      if (!context || target === null || knownA03cReceiver(context, target, 'mode') !== receiver
        || typeof mode !== 'string' || mode.length === 0) return;
      dispatchRadioIntent({ name: 'set_mode', params: { mode, receiver } });
    },
    onFilterChange: (filter: number, receiver?: Receiver) => {
      const context = currentA03cContext();
      const target = receiver === 0 ? 'MAIN' : receiver === 1 ? 'SUB' : null;
      if (!context || target === null || knownA03cReceiver(context, target, 'filter') !== receiver
        || !Number.isSafeInteger(filter)) return;
      dispatchRadioIntent({ name: 'set_filter', params: { filter, receiver } });
    },
    onDualWatchToggle: (on: boolean) => {
      const context = currentA03cContext();
      if (!context || context.caps.receivers < 2
        || !context.caps.capabilities.includes('dual_rx')
        || !context.caps.capabilities.includes('dual_watch')
        || !knownA03cTopLevelField(context, 'dualWatch') || typeof on !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_dual_watch', params: { on } });
    },
    onQuickDw: () => {
      const context = currentA03cContext();
      if (!context || context.caps.receivers < 2
        || !context.caps.capabilities.includes('dual_rx')
        || !context.caps.capabilities.includes('dual_watch')
        || !knownA03cTopLevelField(context, 'dualWatch')) return;
      dispatchRadioIntent({ name: 'quick_dualwatch', params: {} });
    },
    onQuickSplit: () => {
      const context = currentA03cContext();
      if (!context || context.caps.vfoScheme === 'single'
        || !context.caps.capabilities.includes('split')
        || !knownA03cTopLevelField(context, 'split')) return;
      dispatchRadioIntent({ name: 'quick_split', params: {} });
    },
    onTrackingToggle: (on: boolean) => {
      const context = currentA03cContext();
      if (!context || context.caps.receivers < 2
        || !context.caps.capabilities.includes('dual_rx')
        || !context.caps.capabilities.includes('main_sub_tracking')
        || !knownA03cTopLevelField(context, 'mainSubTracking') || typeof on !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_main_sub_tracking', params: { on } });
    },
  };
}

/* ── Keyboard radio delegation (MOR-1409 A03d1a) ─────────────────── */

type KeyboardRadioAction = { action: string; params?: Record<string, unknown> };
type KeyboardContext = NonNullable<ReturnType<typeof currentA03cContext>> & { receiver: Receiver };

const KEYBOARD_RADIO_ACTIONS = new Set([
  'tune', 'band_select', 'mode_select', 'cycle_data_mode', 'cycle_filter',
  'cycle_preamp', 'cycle_att', 'cycle_agc', 'toggle_nr', 'toggle_nb',
  'toggle_auto_notch', 'toggle_ip_plus',
]);

function currentKeyboardContext(): KeyboardContext | null {
  const context = currentA03cContext();
  const active = context?.state.active;
  if (!context || !knownA03cTopLevelField(context, 'active') || (active !== 'MAIN' && active !== 'SUB')) return null;
  const receiver = knownA03cReceiver(context, active);
  return receiver === null ? null : { ...context, receiver };
}

function keyboardReceiverField(context: KeyboardContext, field: string): boolean {
  const receiver = context.receiver === 0 ? context.state.main : context.state.sub;
  const value = receiver?.[field as never];
  return value !== undefined && value !== null
    && isFieldAvailable(context.state, `${context.receiver === 0 ? 'main' : 'sub'}.${field}`);
}

function keyboardCycle(values: unknown, current: unknown): number | null {
  if (!Array.isArray(values) || values.length === 0 || !Number.isSafeInteger(current)
    || !values.every(Number.isSafeInteger)) return null;
  const index = values.indexOf(current);
  return index < 0 ? null : values[(index + 1) % values.length] as number;
}

function keyboardDirection(value: unknown): 'up' | 'down' | null {
  return value === 'up' || value === 'down' ? value : null;
}

function keyboardParams(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null ? value as Record<string, unknown> : null;
}

/** Handles only the A03d1a radio family; invalid recognized actions fail closed. */
export function dispatchKeyboardRadioAction({ action, params }: KeyboardRadioAction): boolean {
  if (!KEYBOARD_RADIO_ACTIONS.has(action)) return false;
  const safeParams = params === undefined ? {} : keyboardParams(params);
  if (safeParams === null) return true;
  const context = currentKeyboardContext();
  if (!context) return true;
  const receiver = context.receiver;
  const rx = receiver === 0 ? context.state.main : context.state.sub;
  const has = (name: string) => context.caps.capabilities.includes(name);

  switch (action) {
    case 'tune': {
      const step = getTuningStep();
      const direction = keyboardDirection(safeParams.direction);
      const delta = Number.isSafeInteger(safeParams.deltaHz) ? safeParams.deltaHz
        : direction && Number.isSafeInteger(step) && step > 0 ? (direction === 'down' ? -step : step) : null;
      const frequency = rx?.freqHz;
      const target = typeof delta === 'number' && Number.isSafeInteger(frequency) ? frequency + delta : null;
      if (keyboardReceiverField(context, 'freqHz') && Number.isSafeInteger(step) && step > 0
        && typeof frequency === 'number' && Number.isSafeInteger(frequency) && frequency > 0
        && typeof target === 'number' && Number.isSafeInteger(target) && target > 0) makeVfoHandlers().onFreqChange(target, receiver);
      return true;
    }
    case 'band_select': {
      const bsr = safeParams.index;
      const frequency = rx?.freqHz;
      if (has('bsr') && keyboardReceiverField(context, 'freqHz') && typeof bsr === 'number'
        && Number.isSafeInteger(bsr) && bsr > 0 && typeof frequency === 'number'
        && Number.isSafeInteger(frequency) && frequency > 0) makeBandHandlers().onBandSelect('', frequency, bsr);
      return true;
    }
    case 'mode_select':
      if (keyboardReceiverField(context, 'mode') && typeof safeParams.mode === 'string' && safeParams.mode.length > 0
        && context.caps.modes.includes(safeParams.mode)) makeModeHandlers().onModeChange(safeParams.mode);
      return true;
    case 'cycle_data_mode': {
      const count = context.caps.dataModeCount;
      if (keyboardReceiverField(context, 'dataMode') && typeof count === 'number' && Number.isSafeInteger(count) && count > 0
        && Number.isSafeInteger(rx?.dataMode) && rx.dataMode >= 0 && rx.dataMode < count) {
        makeModeHandlers().onDataModeChange((rx.dataMode + 1) % count);
      }
      return true;
    }
    case 'cycle_filter': {
      const delta = safeParams.step === -1 || safeParams.step === 1 ? safeParams.step
        : safeParams.direction === 'wider' ? -1 : safeParams.direction === 'narrower' ? 1 : null;
      const count = context.caps.filters.length;
      const filter = rx?.filter;
      if (keyboardReceiverField(context, 'filter') && typeof filter === 'number' && Number.isSafeInteger(filter)
        && filter >= 1 && filter <= count && count > 0 && delta !== null) {
        makeFilterHandlers().onFilterChange(((filter - 1 + delta + count) % count) + 1);
      }
      return true;
    }
    case 'cycle_preamp': {
      const next = keyboardCycle(context.caps.preValues, rx?.preamp);
      if (has('preamp') && keyboardReceiverField(context, 'preamp') && next !== null) makeRfFrontEndHandlers().onPreChange(next);
      return true;
    }
    case 'cycle_att': {
      const next = keyboardCycle(context.caps.attValues, rx?.att);
      if (has('attenuator') && keyboardReceiverField(context, 'att') && next !== null) makeRfFrontEndHandlers().onAttChange(next);
      return true;
    }
    case 'cycle_agc': {
      const next = keyboardCycle(context.caps.agcModes, rx?.agc);
      if (has('agc') && keyboardReceiverField(context, 'agc') && next !== null) makeAgcHandlers().onAgcModeChange(next);
      return true;
    }
    case 'toggle_nr':
      if (has('nr') && keyboardReceiverField(context, 'nr') && typeof rx?.nr === 'boolean') makeDspHandlers().onNrModeChange(rx.nr ? 0 : 1);
      return true;
    case 'toggle_nb':
      if (has('nb') && keyboardReceiverField(context, 'nb') && typeof rx?.nb === 'boolean') makeDspHandlers().onNbToggle(!rx.nb);
      return true;
    case 'toggle_auto_notch':
      if (has('notch') && keyboardReceiverField(context, 'autoNotch') && typeof rx?.autoNotch === 'boolean') makeDspHandlers().onAutoNotchToggle();
      return true;
    case 'toggle_ip_plus':
      if (has('ip_plus') && keyboardReceiverField(context, 'ipplus') && typeof rx?.ipplus === 'boolean') makeRfFrontEndHandlers().onIpPlusToggle(!rx.ipplus);
      return true;
    default:
      return true;
  }
}

/* ── VOX Handlers ────────────────────────────────────────────────── */

function knownVoxLevel(field: 'voxGain' | 'antiVoxGain' | 'voxDelay', level: number): boolean {
  const context = currentA03cContext();
  return context !== null && context.caps.capabilities.includes('tx')
    && context.caps.capabilities.includes('vox')
    && knownA03cTopLevelField(context, field) && Number.isSafeInteger(level);
}

export function makeVoxHandlers() {
  return {
    onVoxToggle: toggleVox,
    onVoxGainChange: (level: number) => {
      if (!knownVoxLevel('voxGain', level)) return;
      dispatchRadioIntent({ name: 'set_vox_gain', params: { level } });
    },
    onAntiVoxGainChange: (level: number) => {
      if (!knownVoxLevel('antiVoxGain', level)) return;
      dispatchRadioIntent({ name: 'set_anti_vox_gain', params: { level } });
    },
    onVoxDelayChange: (level: number) => {
      if (!knownVoxLevel('voxDelay', level)) return;
      dispatchRadioIntent({ name: 'set_vox_delay', params: { level } });
    },
  };
}
