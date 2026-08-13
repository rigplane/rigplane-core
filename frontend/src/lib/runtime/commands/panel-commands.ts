/**
 * Runtime command handler factories for panel controls.
 *
 * This module canonically owns the panel command factories. The legacy
 * `components-v2/wiring/command-bus` is an identity-preserving compatibility
 * re-export only.
 *
 * Do NOT import from `components-v2/*` here.  Pure filter helpers are
 * inlined below (originate from `components-v2/panels/filter-controls`)
 * until a neutral `$lib/radio/filter-controls` module is introduced by #996.
 *
 * See issue #999, parent #959 (M-4).
 */

import {
  getActiveReceiver,
  getRadioState,
} from '$lib/stores/radio.svelte';
import {
  capabilitiesMatchGeneration,
  getCapabilities,
} from '$lib/stores/capabilities.svelte';
import { getFieldStatus, isFieldAvailable } from '$lib/state/field-status';
import { runtime } from '../frontend-runtime';
import { consumePendingFocus, setPendingFocus } from '$lib/radio/pending-focus';
import { getModeFilter } from '$lib/radio/mode-filter-memory';
import { relativeVfoIdentityUnknown, resolveFilterModeConfig } from '../props/panel-props';
import type { FilterModeConfig, FilterSegmentConfig } from '$lib/types/capabilities';
import { modInputCommand, modInputStateKey } from '$lib/radio/mod-input';
import {
  mapIfShiftToPbt, nbDepthDisplayToRaw, nrDisplayToRaw, pbtHzToRaw, pbtRangeFromCaps,
  quantizeFilterWidthToRule,
} from '$lib/radio/filter-controls';
import { audioManager } from '$lib/audio/audio-manager';
import { adjustTuningStep, getTuningStep } from '$lib/stores/tuning.svelte';
import { currentControlSessionEpoch, dispatchRadioIntent, isNormalizedLevel } from './radio-intents';
import { getSharedTuningAccumulator } from './tuning-accumulator';

/* ── Shared helpers ──────────────────────────────────────────────── */

type Receiver = 0 | 1;

function knownActiveReceiver(field?: string, target?: 'MAIN' | 'SUB'): Receiver | null {
  const state = getRadioState();
  if (!state) return null;
  // MOR-1418: on a single-receiver radio, `active` (MAIN/SUB) is
  // structurally unobservable — no CI-V echo ever sets it, so it never
  // becomes an observed field. The active receiver is tautologically MAIN
  // in that case, so an unobserved `active` must not block the write.
  // Dual-RX radios are unaffected: `active` observation is still required
  // whenever it is actually observed (or on any radio with receivers > 1).
  const activeObserved = isFieldAvailable(state, 'active');
  const singleReceiver = getCapabilities()?.receivers === 1;
  const receiverName = target
    ?? (activeObserved ? state.active : singleReceiver ? 'MAIN' : undefined);
  if (receiverName !== 'MAIN' && receiverName !== 'SUB') return null;
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

/* ── PBT / IF-shift helpers ──────────────────────────────────────── */
// MOR-1291: the local re-implementations that used to live here (`pbtRange`,
// `pbtHzToRaw`, `clampToBipolarRange`, `deriveIfShift`, `mapIfShiftToPbt`)
// are gone — this module now imports the ONE shipped versions from
// `$lib/radio/filter-controls` (the neutral module #996 introduced). The
// duplicate `pbtRange()` silently fell back to an IC-7610-shaped default
// (rawCenter 128, ±1200 Hz) via the capabilities STORE whenever a radio's
// caps omitted `controls.pbt_inner` — exactly the fabricated-command class
// this ticket closes. `pbtRangeFromCaps(getCapabilities())` below is called
// at each PBT command site instead, and the handler bails (emits nothing)
// when it comes back `undefined`, rather than ever falling through to
// `pbtHzToRaw`'s own store-fallback branch.

/* ── Memory Handlers ─────────────────────────────────────────────── */

type MemorySnapshot = { frequencyHz: number | null; mode: string | null };

function observedAvailableField(
  state: NonNullable<ReturnType<typeof getRadioState>>,
  path: string,
): boolean {
  const status = getFieldStatus(state, path);
  return status?.observed === true && status.freshness === 'fresh'
    && status.availability === 'available';
}

function currentMemorySnapshot(): MemorySnapshot | null {
  const context = currentA03cContext();
  // MOR-1423: same single-receiver `active` bypass as knownActiveReceiver
  // (MOR-1418) — on a one-receiver radio, active is tautologically MAIN
  // even when structurally unobservable. Dual-RX still requires observation.
  const activeObserved = context !== null && observedAvailableField(context.state, 'active');
  const singleReceiver = context?.caps.receivers === 1;
  const active = context
    && (activeObserved ? context.state.active : singleReceiver ? 'MAIN' : undefined);
  if (!context
    || (active !== 'MAIN' && active !== 'SUB')
    || knownA03cReceiver(context, active) === null) return null;

  const receiverKey = active === 'SUB' ? 'sub' : 'main';
  const receiver = context.state[receiverKey];
  if (!receiver) return null;

  let source: { freqHz?: number; mode?: string } = receiver;
  let base = `${receiverKey}.`;
  const relative = context.caps.vfoScheme === 'ab'
    && relativeVfoIdentityUnknown(context.state, context.caps, receiverKey);
  const unslotted = context.caps.vfoScheme === 'single'
    || context.caps.vfoScheme === 'ab_shared';
  if (!relative && !unslotted) {
    const slot = receiver.activeSlot;
    if (!observedAvailableField(context.state, `${receiverKey}.activeSlot`)
      || (slot !== 'A' && slot !== 'B')) return null;
    const slotKey = slot === 'A' ? 'vfoA' : 'vfoB';
    const slotted = receiver[slotKey];
    if (!slotted) return null;
    source = slotted;
    base = `${receiverKey}.${slotKey}.`;
  }

  const frequencyHz = source.freqHz;
  const mode = source.mode;
  return {
    frequencyHz: observedAvailableField(context.state, `${base}freqHz`)
      && Number.isSafeInteger(frequencyHz) && (frequencyHz as number) > 0
      ? frequencyHz as number : null,
    mode: observedAvailableField(context.state, `${base}mode`)
      && typeof mode === 'string' && mode.length > 0 ? mode : null,
  };
}

function validMemoryChannel(channel: number): boolean {
  return Number.isSafeInteger(channel) && channel >= 1 && channel <= 99;
}

export function makeMemoryHandlers() {
  return {
    onRecall: (channel: number): boolean => {
      if (!validMemoryChannel(channel) || currentMemorySnapshot() === null) return false;
      dispatchRadioIntent({ name: 'set_memory_mode', params: { channel } });
      dispatchRadioIntent({ name: 'memory_to_vfo', params: { channel } });
      return true;
    },
    onStore: (channel: number, frequencyHz: number, mode: string): boolean => {
      const snapshot = currentMemorySnapshot();
      if (!validMemoryChannel(channel) || !snapshot || snapshot.frequencyHz === null
        || snapshot.mode === null || frequencyHz !== snapshot.frequencyHz
        || mode !== snapshot.mode) return false;
      dispatchRadioIntent({ name: 'set_memory_mode', params: { channel } });
      dispatchRadioIntent({ name: 'memory_write', params: {} });
      return true;
    },
    onClear: (channel: number): boolean => {
      if (!validMemoryChannel(channel) || currentMemorySnapshot() === null) return false;
      dispatchRadioIntent({ name: 'memory_clear', params: { channel } });
      return true;
    },
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
      dispatchRadioIntent({ name: 'set_data_mode', params: { mode, receiver } });
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
      dispatchRadioIntent({ name: modInputCommand(dataMode as number), params: { source } });
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

const positiveSafeInteger = (value: unknown): value is number =>
  Number.isSafeInteger(value) && (value as number) > 0;

function validFilterSegments(segments: readonly FilterSegmentConfig[], minHz: number, maxHz: number): boolean {
  if (segments.length === 0 || segments[0].hzMin !== minHz
    || segments[segments.length - 1].hzMax !== maxHz) return false;
  let previousHzMax = 0;
  let previousIndexMax = -1;
  for (const segment of segments) {
    if (!positiveSafeInteger(segment.hzMin) || !positiveSafeInteger(segment.hzMax)
      || !positiveSafeInteger(segment.stepHz) || !Number.isSafeInteger(segment.indexMin)
      || segment.indexMin < 0 || segment.hzMin > segment.hzMax
      || (segment.hzMax - segment.hzMin) % segment.stepHz !== 0
      || segment.hzMin <= previousHzMax || segment.indexMin <= previousIndexMax) return false;
    previousHzMax = segment.hzMax;
    previousIndexMax = segment.indexMin + ((segment.hzMax - segment.hzMin) / segment.stepHz);
    if (!Number.isSafeInteger(previousIndexMax)) return false;
  }
  return true;
}

function validResolvedFilterWidth(width: number, rule: FilterModeConfig | null): boolean {
  if (!positiveSafeInteger(width) || !rule || rule.fixed) return false;
  const hasTable = rule.table !== undefined;
  const hasSegments = rule.segments !== undefined;
  if (hasTable && hasSegments) return false;
  if (hasTable) {
    const table = rule.table!;
    if (table.length === 0 || table.some((value, index) =>
      !positiveSafeInteger(value) || (index > 0 && value <= table[index - 1]))) return false;
    if ((rule.minHz !== undefined && rule.minHz !== table[0])
      || (rule.maxHz !== undefined && rule.maxHz !== table[table.length - 1])) return false;
    return table.includes(width);
  }
  if (hasSegments) {
    if (!positiveSafeInteger(rule.minHz) || !positiveSafeInteger(rule.maxHz)
      || rule.minHz > rule.maxHz
      || !validFilterSegments(rule.segments!, rule.minHz, rule.maxHz)) return false;
    const segment = rule.segments!.find(({ hzMin, hzMax }) => width >= hzMin && width <= hzMax);
    return segment !== undefined && (width - segment.hzMin) % segment.stepHz === 0;
  }
  return positiveSafeInteger(rule.minHz) && positiveSafeInteger(rule.maxHz)
    && positiveSafeInteger(rule.stepHz) && rule.minHz <= rule.maxHz
    && (rule.maxHz - rule.minHz) % rule.stepHz === 0
    && width >= rule.minHz && width <= rule.maxHz
    && (width - rule.minHz) % rule.stepHz === 0;
}

/**
 * The active filter-width rule for `receiver` (MOR-1518) — the SAME
 * `resolveFilterModeConfig(caps, mode, dataMode)` resolution
 * `onFilterWidthCommit` already reads via its own `currentA03cContext()`
 * (below), but usable from the debounced/preset handlers, which key off
 * `knownActiveReceiver` rather than the stricter A06a1 context. `null` when
 * state/caps are unavailable or the mode has no declared rule — the
 * capability-absent case `quantizeFilterWidthToRule` itself falls back to
 * the plain clamp for.
 */
function activeFilterRule(receiver: Receiver): FilterModeConfig | null {
  const state = getRadioState();
  const caps = getCapabilities();
  if (!state || !caps) return null;
  const rx = receiver === 1 ? state.sub : state.main;
  return resolveFilterModeConfig(caps, rx?.mode, rx?.dataMode);
}

export function makeFilterHandlers() {
  return {
    onFilterChange: (filter: number) => {
      const receiver = knownActiveReceiver('filter');
      if (receiver === null) return;
      dispatchRadioIntent({ name: 'set_filter', params: { filter, receiver } });
    },
    // MOR-1518: `width` here is a raw, possibly mid-drag or mid-keystroke
    // slider value — the native `<input type="range">` in `FilterSurface`/
    // `FilterPanel` steps by a single fixed increment that cannot match
    // every mode's radio-declared step (the IC-7300's own USB/LSB/CW/RTTY
    // rules widen from 50 Hz to 100 Hz above 500 Hz, `rigs/ic7300.toml`).
    // `quantizeFilterWidthToRule` snaps to a value the resolved per-mode
    // rule actually accepts BEFORE it reaches the wire — the "command-
    // emission point", not just a display-layer clamp — so this handler
    // never dispatches the alignment-error-triggering values the live
    // bench reported (1050/2150/3150 Hz).
    onFilterWidthChange: (() => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      return (width: number) => {
        if (knownActiveReceiver('filterWidth') === null) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          const receiver = knownActiveReceiver('filterWidth');
          if (receiver === null) return;
          const quantized = quantizeFilterWidthToRule(width, activeFilterRule(receiver));
          dispatchRadioIntent({ name: 'set_filter_width', params: { width: quantized, receiver } });
        }, 200);
      };
    })(),
    onFilterWidthCommit: (
      width: number,
      receiver: Receiver,
      expectedProviderGeneration: number,
    ): void => {
      const context = currentA03cContext();
      const stateGeneration = context?.state.providerGeneration;
      // MOR-1418: same single-receiver `active` bypass as knownActiveReceiver
      // — on a one-receiver radio, active is tautologically MAIN even when
      // structurally unobservable. Dual-RX still requires observation.
      const activeObserved = context !== null && observedAvailableField(context.state, 'active');
      const singleReceiver = context?.caps.receivers === 1;
      const active = context
        && (activeObserved ? context.state.active : singleReceiver ? 'MAIN' : undefined);
      if (!context || !Number.isSafeInteger(expectedProviderGeneration)
        || expectedProviderGeneration < 0 || !Number.isSafeInteger(stateGeneration)
        || expectedProviderGeneration !== stateGeneration
        || (active !== 'MAIN' && active !== 'SUB')
        || (active === 'MAIN' ? 0 : 1) !== receiver
        || knownA03cReceiver(context, active, 'mode') !== receiver
        || knownA03cReceiver(context, active, 'filterWidth') !== receiver
        || !context.caps.capabilities.includes('filter_width')) return;
      const key = receiver === 1 ? 'sub' : 'main';
      const observed = context.state[key];
      const mode = observed?.mode;
      const currentWidth = observed?.filterWidth;
      if (!observed || !observedAvailableField(context.state, `${key}.mode`)
        || !observedAvailableField(context.state, `${key}.filterWidth`)
        || typeof mode !== 'string' || mode.length === 0 || !positiveSafeInteger(currentWidth)) return;
      const supportsData = context.caps.capabilities.includes('data_mode');
      const dataMode = supportsData ? observed.dataMode : 0;
      if (supportsData && (!observedAvailableField(context.state, `${key}.dataMode`)
        || !Number.isSafeInteger(dataMode) || (dataMode as number) < 0)) return;
      const rule = resolveFilterModeConfig(context.caps, mode, dataMode as number);
      if (!validResolvedFilterWidth(width, rule)) return;
      dispatchRadioIntent({ name: 'set_filter_width', params: { width, receiver } });
    },
    onFilterShapeChange: (shape: number) => {
      const receiver = knownActiveReceiver('filterShape');
      if (receiver === null) return;
      dispatchRadioIntent({ name: 'set_filter_shape', params: { shape, receiver } });
    },
    // MOR-1518: `width` is a direct-entry value from the filter-settings
    // modal's per-preset slider (`FilterPanel.svelte`'s `handlePresetChange`)
    // — the same "any direct-entry path into the width command" the ticket
    // calls out. Quantized against the CURRENT receiver's resolved rule at
    // fire time (not the rule captured when the drag started), matching
    // `currentReceiver`/`currentActive` already being re-read fresh below.
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
          const quantized = quantizeFilterWidthToRule(width, activeFilterRule(currentReceiver));
          if (filter !== currentActive) {
            dispatchRadioIntent({ name: 'set_filter', params: { filter, receiver: currentReceiver } });
          }
          dispatchRadioIntent({ name: 'set_filter_width', params: { width: quantized, receiver: currentReceiver } });
          if (filter !== currentActive) {
            dispatchRadioIntent({ name: 'set_filter', params: { filter: currentActive as number, receiver: currentReceiver } });
          }
        }, 200);
      };
    })(),
    onFilterDefaults: (defaults: number[]) => {
      const receiver = knownActiveReceiver('filterWidth');
      const activeFilter = receiver === null ? null : getActiveReceiver()?.filter;
      if (receiver === null || !Number.isSafeInteger(activeFilter)) return;
      const rule = activeFilterRule(receiver);
      for (let i = 0; i < defaults.length; i++) {
        const filter = i + 1;
        if (filter !== activeFilter) {
          dispatchRadioIntent({ name: 'set_filter', params: { filter, receiver } });
        }
        dispatchRadioIntent({
          name: 'set_filter_width',
          params: { width: quantizeFilterWidthToRule(defaults[i], rule), receiver },
        });
      }
      if ((activeFilter as number) <= defaults.length) {
        dispatchRadioIntent({ name: 'set_filter', params: { filter: activeFilter as number, receiver } });
      }
    },
    onIfShiftChange: (value: number) => {
      const caps = getCapabilities();
      if (!caps) return;
      if (caps.capabilities.includes('if_shift')) {
        const receiver = knownActiveReceiver('ifShift');
        if (receiver !== null) dispatchRadioIntent({ name: 'set_if_shift', params: { offset: value, receiver } });
      } else if (caps.capabilities.includes('pbt')) {
        // MOR-1291: a radio can declare the `pbt` capability tag without
        // (yet, or ever) declaring a usable `controls.pbt_inner` range — no
        // fabricated IC-7610-shaped scale (rawCenter 128, ±1200 Hz) stands in
        // for a range this radio's OWN capabilities never provided. Missing
        // or malformed range ⇒ no command, same fail-closed shape every
        // other guard below already uses.
        const pbtRange = pbtRangeFromCaps(caps);
        if (!pbtRange) return;
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
        dispatchRadioIntent({ name: 'set_pbt_inner', params: { value: pbtHzToRaw(pbtInner, pbtRange), receiver } });
        dispatchRadioIntent({ name: 'set_pbt_outer', params: { value: pbtHzToRaw(pbtOuter, pbtRange), receiver } });
      }
    },
    onPbtInnerChange: (value: number) => {
      // MOR-1291: see `onIfShiftChange`'s pbt branch above — no command
      // without a caps-declared PBT range.
      const pbtRange = pbtRangeFromCaps(getCapabilities());
      if (!pbtRange) return;
      const receiver = knownActiveReceiver('pbtInner');
      if (receiver === null) return;
      dispatchRadioIntent({ name: 'set_pbt_inner', params: { value: pbtHzToRaw(value, pbtRange), receiver } });
    },
    onPbtOuterChange: (value: number) => {
      const pbtRange = pbtRangeFromCaps(getCapabilities());
      if (!pbtRange) return;
      const receiver = knownActiveReceiver('pbtOuter');
      if (receiver === null) return;
      dispatchRadioIntent({ name: 'set_pbt_outer', params: { value: pbtHzToRaw(value, pbtRange), receiver } });
    },
    onPbtReset: () => {
      const pbtRange = pbtRangeFromCaps(getCapabilities());
      if (!pbtRange) return;
      const receiver = knownActiveReceiver('pbtInner');
      const state = getRadioState();
      const prefix = receiver === 1 ? 'sub' : 'main';
      if (receiver === null || !state || !isFieldAvailable(state, `${prefix}.pbtOuter`)) return;
      const center = pbtHzToRaw(0, pbtRange);
      dispatchRadioIntent({ name: 'set_pbt_inner', params: { value: center, receiver } });
      dispatchRadioIntent({ name: 'set_pbt_outer', params: { value: center, receiver } });
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
  // MOR-1425: rapid-tuning-step accumulator, shared module-wide (review
  // B5) — NOT per-instance: `panel-adapters.ts` holds both a singleton
  // accessor and fresh per-composition-root calls, and two independently-
  // tracked accumulators for the same receiver would be blind to each
  // other's writes. `epoch`/`generation` are the same session/capabilities
  // reads `dispatchRadioIntent` itself uses (review B4).
  function tuningAccumulator(): ReturnType<typeof getSharedTuningAccumulator> {
    return getSharedTuningAccumulator({
      emit: (receiver, freq) =>
        dispatchRadioIntent({ name: 'set_freq', params: { freq, receiver: receiver as Receiver } }),
      epoch: currentControlSessionEpoch,
      generation: () => {
        const value = getCapabilities()?.providerGeneration;
        return typeof value === 'number' ? value : null;
      },
    });
  }

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
      // MOR-1423: same single-receiver `active` bypass as knownActiveReceiver
      // (MOR-1418) — on a one-receiver radio, active is tautologically MAIN
      // even when structurally unobservable. Dual-RX still requires observation.
      const activeObserved = context !== null && knownA03cTopLevelField(context, 'active');
      const singleReceiver = context?.caps.receivers === 1;
      const active = context
        && (activeObserved ? context.state.active : singleReceiver ? 'MAIN' : undefined);
      if (!context || (active !== 'MAIN' && active !== 'SUB')
        || knownA03cReceiver(context, receiver) === null
        || !supportsVfoSlot(context, slot)) return;
      if (active !== receiver && !activateReceiver(receiver, context)) return;
      if (slot !== null) dispatchRadioIntent({ name: 'set_vfo', params: { vfo: slot } });
    },
    onMainModeClick: () => focusModePanel('MAIN'),
    onSubModeClick: () => focusModePanel('SUB'),
    onMainFreqChange: (freq: number) => {
      const context = currentA03cContext();
      const main = context?.state.main;
      if (!context || knownA03cReceiver(context, 'MAIN', 'freqHz') !== 0
        || !Number.isSafeInteger(freq) || !main) return;
      tuningAccumulator().step(0, main.freqHz, freq);
    },
    onSubFreqChange: (freq: number) => {
      const context = currentA03cContext();
      const sub = context?.state.sub;
      if (!context || knownA03cReceiver(context, 'SUB', 'freqHz') !== 1
        || !Number.isSafeInteger(freq) || !sub) return;
      tuningAccumulator().step(1, sub.freqHz, freq);
    },
    // MOR-1425 review B1: callers mix ABSOLUTE targets (spectrum click/
    // drag, EiBi/QSY recall) and RELATIVE steps (spectrum scroll, media
    // keys, keyboard 'tune'). Defaults to 'jump' — absolute correctness by
    // default — so only true step sources opt in with 'step'.
    onFreqChange: (freq: number, receiver?: Receiver, kind: 'jump' | 'step' = 'jump') => {
      const context = currentA03cContext();
      const target = receiver === 0 ? 'MAIN' : receiver === 1 ? 'SUB' : null;
      if (!context || target === null || receiver === undefined
        || knownA03cReceiver(context, target, 'freqHz') !== receiver
        || !Number.isSafeInteger(freq)) return;
      if (kind === 'jump') { tuningAccumulator().jump(receiver, freq); return; }
      const confirmed = receiver === 1 ? context.state.sub : context.state.main;
      if (!confirmed) return;
      tuningAccumulator().step(receiver, confirmed.freqHz, freq);
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

/* ── System / scope handlers ─────────────────────────────────────── */

function currentScopeContext() {
  const context = currentA03cContext();
  return context && context.caps.scope === true && context.caps.capabilities.includes('scope')
    ? context : null;
}

function validScopeValue(value: unknown, kind: 'boolean' | 'integer', min = 0, max = 0): boolean {
  return kind === 'boolean' ? typeof value === 'boolean'
    : Number.isSafeInteger(value) && (value as number) >= min && (value as number) <= max;
}

function acceptsScopeValue(
  context: NonNullable<ReturnType<typeof currentScopeContext>>,
  field: string,
  proposed: unknown,
  kind: 'boolean' | 'integer',
  min = 0,
  max = 0,
): boolean {
  const current = (context.state.scopeControls as unknown as Record<string, unknown> | undefined)?.[field];
  return isFieldAvailable(context.state, `scopeControls.${field}`)
    && validScopeValue(current, kind, min, max) && validScopeValue(proposed, kind, min, max);
}

function hasPhysicalSub(context: NonNullable<ReturnType<typeof currentScopeContext>>): boolean {
  return context.caps.receivers === 2 && context.caps.capabilities.includes('dual_rx')
    && context.state.sub !== null && context.state.sub !== undefined;
}

export function makeSystemHandlers() {
  return {
    onDialLock: (on: boolean) => {
      const context = currentA03cContext();
      if (!context || !context.caps.capabilities.includes('dial_lock')
        || !knownA03cTopLevelField(context, 'dialLock')
        || typeof context.state.dialLock !== 'boolean' || typeof on !== 'boolean') return;
      dispatchRadioIntent({ name: 'set_dial_lock', params: { on } });
    },
    onPowerOff: () => {
      const context = currentA03cContext();
      if (!context || !context.caps.capabilities.includes('power_control')) return;
      dispatchRadioIntent({ name: 'set_powerstat', params: { on: false } });
    },
    onSpeak: () => dispatchRadioIntent({ name: 'speak', params: { mode: 0 } }),
  };
}

export function makeScopeControlsHandlers() {
  return {
    onModeChange: (mode: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'mode', mode, 'integer', 0, 3)) return;
      dispatchRadioIntent({ name: 'set_scope_mode', params: { mode } });
    },
    onEdgeChange: (edge: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'edge', edge, 'integer', 1, 4)) return;
      dispatchRadioIntent({ name: 'set_scope_edge', params: { edge } });
    },
    onSpanChange: (span: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'span', span, 'integer', 0, 7)) return;
      dispatchRadioIntent({ name: 'set_scope_span', params: { span } });
    },
    onSpeedChange: (speed: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'speed', speed, 'integer', 0, 2)) return;
      dispatchRadioIntent({ name: 'set_scope_speed', params: { speed } });
    },
    onHoldChange: (on: boolean) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'hold', on, 'boolean')) return;
      dispatchRadioIntent({ name: 'set_scope_hold', params: { on } });
    },
    onRefChange: (ref: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'refDb', ref, 'integer', -30, 10)) return;
      dispatchRadioIntent({ name: 'set_scope_ref', params: { ref } });
    },
    onDualChange: (dual: boolean) => {
      const context = currentScopeContext();
      if (!context || !hasPhysicalSub(context) || !acceptsScopeValue(context, 'dual', dual, 'boolean')) return;
      dispatchRadioIntent({ name: 'set_scope_dual', params: { dual } });
    },
    onReceiverChange: (receiver: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'receiver', receiver, 'integer', 0, 1)
        || (receiver === 1 && !hasPhysicalSub(context))) return;
      dispatchRadioIntent({ name: 'switch_scope_receiver', params: { receiver: receiver as Receiver } });
    },
    onDuringTxChange: (on: boolean) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'duringTx', on, 'boolean')) return;
      dispatchRadioIntent({ name: 'set_scope_during_tx', params: { on } });
    },
    onCenterTypeChange: (center_type: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'centerType', center_type, 'integer', 0, 2)) return;
      dispatchRadioIntent({ name: 'set_scope_center_type', params: { center_type } });
    },
    onVbwChange: (narrow: boolean) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'vbwNarrow', narrow, 'boolean')) return;
      dispatchRadioIntent({ name: 'set_scope_vbw', params: { narrow } });
    },
    onRbwChange: (rbw: number) => {
      const context = currentScopeContext();
      if (!context || !acceptsScopeValue(context, 'rbw', rbw, 'integer', 0, 2)) return;
      dispatchRadioIntent({ name: 'set_scope_rbw', params: { rbw } });
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
  'toggle_rit', 'toggle_xit', 'clear_rit_xit',
  'adjust_af_level', 'adjust_rf_gain', 'toggle_monitor',
  'toggle_split', 'vfo_swap', 'vfo_equalize', 'switch_active_vfo', 'set_active_vfo',
  'toggle_dial_lock', 'scope_span_step', 'scope_ref_step', 'scope_toggle_hold',
  'scope_toggle_dual', 'scope_toggle_fst',
]);

function currentKeyboardContext(): KeyboardContext | null {
  const context = currentA03cContext();
  // MOR-1423: same single-receiver `active` bypass as knownActiveReceiver
  // (MOR-1418) — on a one-receiver radio, active is tautologically MAIN
  // even when structurally unobservable. Dual-RX still requires observation.
  const activeObserved = context !== null && knownA03cTopLevelField(context, 'active');
  const singleReceiver = context?.caps.receivers === 1;
  const active = context
    && (activeObserved ? context.state.active : singleReceiver ? 'MAIN' : undefined);
  if (!context || (active !== 'MAIN' && active !== 'SUB')) return null;
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

function keyboardScopeField(context: KeyboardContext, field: string): unknown | null {
  const scope = context.state.scopeControls;
  const value = (scope as unknown as Record<string, unknown> | undefined)?.[field];
  return value !== undefined && value !== null && isFieldAvailable(context.state, `scopeControls.${field}`)
    ? value : null;
}

function keyboardParams(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object') return null;
  try {
    if (Array.isArray(value)) return null;
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) return null;
    const params: Record<PropertyKey, unknown> = Object.create(null);
    for (const key of Reflect.ownKeys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || !('value' in descriptor)) return null;
      Object.defineProperty(params, key, {
        value: descriptor.value,
        enumerable: descriptor.enumerable,
        configurable: true,
        writable: true,
      });
    }
    return params;
  } catch {
    return null;
  }
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
        && typeof target === 'number' && Number.isSafeInteger(target) && target > 0) makeVfoHandlers().onFreqChange(target, receiver, 'step');
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
    case 'toggle_rit':
      if (has('rit') && knownA03cTopLevelField(context, 'ritOn')
        && typeof context.state.ritOn === 'boolean') makeRitXitHandlers().onRitToggle();
      return true;
    case 'toggle_xit':
      if (has('xit') && knownA03cTopLevelField(context, 'ritTx')
        && typeof context.state.ritTx === 'boolean') makeRitXitHandlers().onXitToggle();
      return true;
    case 'clear_rit_xit':
      if ((has('rit') || has('xit')) && knownA03cTopLevelField(context, 'ritFreq')
        && Number.isSafeInteger(context.state.ritFreq)) makeRitXitHandlers().onClear();
      return true;
    case 'adjust_af_level': {
      const direction = keyboardDirection(safeParams.direction);
      const current = rx?.afLevel;
      if (direction && keyboardReceiverField(context, 'afLevel') && isNormalizedLevel(current)) {
        makeRxAudioHandlers().onAfLevelChange(Math.max(0, Math.min(1, current + (direction === 'down' ? -0.05 : 0.05))));
      }
      return true;
    }
    case 'adjust_rf_gain': {
      const direction = keyboardDirection(safeParams.direction);
      const current = rx?.rfGain;
      if (direction && keyboardReceiverField(context, 'rfGain') && isNormalizedLevel(current)) {
        const level = Math.max(0, Math.min(1, current + (direction === 'down' ? -0.05 : 0.05)));
        makeRfFrontEndHandlers().onRfGainChange(Math.round(level * 255));
      }
      return true;
    }
    case 'toggle_monitor':
      if (has('tx') && has('monitor') && knownA03cTopLevelField(context, 'monitorOn')
        && typeof context.state.monitorOn === 'boolean') makeTxHandlers().onMonToggle();
      return true;
    case 'toggle_split':
      if (has('split') && knownA03cTopLevelField(context, 'split')
        && typeof context.state.split === 'boolean') makeVfoHandlers().onSplitToggle();
      return true;
    case 'vfo_swap':
      makeVfoHandlers().onSwap();
      return true;
    case 'vfo_equalize':
      makeVfoHandlers().onEqual();
      return true;
    case 'switch_active_vfo': {
      const target = context.state.active === 'MAIN' ? 'SUB' : 'MAIN';
      if (target === 'MAIN') makeVfoHandlers().onMainVfoClick();
      else if (context.caps.receivers >= 2 && has('dual_rx') && context.state.sub) makeVfoHandlers().onSubVfoClick();
      return true;
    }
    case 'set_active_vfo':
      if (safeParams.vfo === 'MAIN') makeVfoHandlers().onMainVfoClick();
      else if (safeParams.vfo === 'SUB' && context.caps.receivers >= 2 && has('dual_rx') && context.state.sub) makeVfoHandlers().onSubVfoClick();
      return true;
    case 'toggle_dial_lock': {
      const dialLock = context.state.dialLock;
      if (has('dial_lock') && knownA03cTopLevelField(context, 'dialLock') && typeof dialLock === 'boolean') {
        makeSystemHandlers().onDialLock(!dialLock);
      }
      return true;
    }
    case 'scope_span_step': {
      const current = keyboardScopeField(context, 'span');
      const direction = keyboardDirection(safeParams.direction);
      if (context.caps.scope === true && has('scope') && direction && typeof current === 'number' && Number.isSafeInteger(current) && current >= 0 && current <= 7) {
        makeScopeControlsHandlers().onSpanChange(Math.max(0, Math.min(7, current + (direction === 'down' ? -1 : 1))));
      }
      return true;
    }
    case 'scope_ref_step': {
      const current = keyboardScopeField(context, 'refDb');
      const direction = keyboardDirection(safeParams.direction);
      if (context.caps.scope === true && has('scope') && direction && typeof current === 'number' && Number.isSafeInteger(current) && current >= -30 && current <= 10) {
        makeScopeControlsHandlers().onRefChange(Math.max(-30, Math.min(10, current + (direction === 'down' ? -5 : 5))));
      }
      return true;
    }
    case 'scope_toggle_hold': {
      const hold = keyboardScopeField(context, 'hold');
      if (context.caps.scope === true && has('scope') && typeof hold === 'boolean') {
        makeScopeControlsHandlers().onHoldChange(!hold);
      }
      return true;
    }
    case 'scope_toggle_dual': {
      const dual = keyboardScopeField(context, 'dual');
      if (context.caps.scope === true && has('scope') && context.caps.receivers === 2
        && has('dual_rx') && context.state.sub && typeof dual === 'boolean') {
        makeScopeControlsHandlers().onDualChange(!dual);
      }
      return true;
    }
    case 'scope_toggle_fst': {
      const speed = keyboardScopeField(context, 'speed');
      if (context.caps.scope === true && has('scope') && typeof speed === 'number' && Number.isSafeInteger(speed) && speed >= 0 && speed <= 2) {
        makeScopeControlsHandlers().onSpeedChange(speed === 0 ? 1 : 0);
      }
      return true;
    }
    default:
      return true;
  }
}

export function makeKeyboardHandlers() {
  return {
    dispatch<T extends KeyboardRadioAction>(action: T): void {
      if (dispatchKeyboardRadioAction(action)) return;
      switch (action.action) {
        case 'adjust_tuning_step': {
          adjustTuningStep(action.params?.direction === 'down' ? 'down' : 'up');
          return;
        }
        case 'open_filter_settings': {
          window.dispatchEvent(new CustomEvent('rigplane:open-filter-settings'));
          return;
        }
        case 'focus_target': {
          const target = action.params?.target;
          if (typeof target === 'string') {
            // MOR-1456: every selector below is verified against the ACTUAL
            // rendered desktop-v2 composition, not the pre-v3-rework legacy
            // panels (`LeftSidebar`'s `RfFrontEnd`/`ModePanel`/`FilterPanel`
            // are suppressed there — see `desktop-declarations.ts`'s
            // `filter`/`rfFrontEnd`/`rx-audio` zones). Each entry points at
            // the semantic surface that actually mounts, reusing its existing
            // `data-testid` hooks rather than inventing a parallel
            // `data-panel`/`data-control` vocabulary no component emits.
            const selectors: Record<string, string> = {
              af: '[data-testid="rx-audio-af"] input',
              rf: '[data-testid="rf-front-end-rf-sql"] input, [data-testid="rf-front-end-rfGain"] input',
              squelch: '[data-testid="rf-front-end-rf-sql"] input, [data-testid="rf-front-end-squelch"] input',
              filter: '[data-testid="filter-select"] button',
              mode: '[data-testid="filter-mode"] button',
              pbt: '[data-testid="filter-pbtInner"] input',
              waterfall: '[data-waterfall]',
              // The active receiver's tunable frequency tile: `data-vfo-freq`
              // marks the region (`VfoSurface.svelte`, MOR-1480), but the
              // actual focusable node is `FrequencyDisplayInteractive`'s
              // `tabindex="0"` root nested inside it (`vfoFreqHook={false}`
              // there deliberately keeps the attribute off that inner node —
              // see `keyboard-map.ts`'s `isFrequencyDisplayFocused` for the
              // same closest()-based hook this selector mirrors).
              vfo: '[data-vfo-tile][data-vfo-active="true"] [data-vfo-freq] [tabindex]',
            };
            const selector = selectors[target];
            const el = selector ? document.querySelector(selector) : null;
            if (el instanceof HTMLElement) {
              el.focus();
              el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
            // Honest failure (MOR-1456): a dead selector must never look like
            // a no-op keypress. `document.activeElement !== el` also catches
            // an element that resolved but couldn't actually take focus (e.g.
            // disabled or not currently rendered).
            if (!(el instanceof HTMLElement) || document.activeElement !== el) {
              console.warn(`[keyboard] focus_target "${target}" has no focusable anchor in the current layout`);
            }
          }
          return;
        }
        default:
          console.warn('[keyboard] unhandled action', action.action, action.params);
      }
    },
  };
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
