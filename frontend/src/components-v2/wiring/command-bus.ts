/**
 * Command Bus — maps v2 UI callbacks → sendCommand() WebSocket calls.
 *
 * Each `makeXxxHandlers()` returns an object of callback functions
 * matching the corresponding v2 component's event props.
 *
 * Optimistic state updates happen inside ws-client.ts `_applyOptimistic()`.
 *
 * Epic #289, Phase 2.
 */

import { sendCommand } from '$lib/transport/ws-client';
import { getActiveReceiver, getRadioState, patchActiveReceiver, patchRadioState, patchReceiver } from '$lib/stores/radio.svelte';
import type { ReceiverState } from '$lib/types/state';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { adjustTuningStep, getTuningStep } from '$lib/stores/tuning.svelte';
import { audioManager } from '$lib/audio/audio-manager';
import { setMuted, setVolume } from '$lib/stores/audio.svelte';
import type { KeyboardActionConfig } from '../layout/keyboard-map';
import { mapIfShiftToPbt, pbtHzToRaw } from '../panels/filter-controls';
import { clampRef, clampSpan } from '../../components/spectrum/spectrum-toolbar-logic';
import { consumePendingFocus, setPendingFocus } from '$lib/radio/pending-focus';

/* ── Helpers ─────────────────────────────────────────────────── */

/** Get the receiver param (0 = MAIN/active, 1 = SUB). */
type Receiver = 0 | 1;

function cmd(name: string, params: Record<string, unknown> = {}): void {
  sendCommand(name, params);
}

function activeReceiverParam(): Receiver {
  return getRadioState()?.active === 'SUB' ? 1 : 0;
}

function focusModePanel(vfo: 'MAIN' | 'SUB'): void {
  setPendingFocus(vfo);
  patchRadioState({ active: vfo });
  cmd('set_vfo', { vfo });

  const modePanel = document.querySelector<HTMLElement>('[data-mode-panel="true"]');
  if (!modePanel) {
    return;
  }

  modePanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  modePanel.dataset.highlight = 'true';
  window.setTimeout(() => {
    if (modePanel.dataset.highlight === 'true') {
      delete modePanel.dataset.highlight;
    }
  }, 1200);
}

/* ── VFO Handlers ────────────────────────────────────────────── */

/** Fields that ``0x07 0xB1`` (equalize) and ``0x07 0xB0`` (exchange)
 *  propagate between MAIN and SUB on the radio.  Frequency + mode are
 *  the two observable ones the user cares about; backend poll refreshes
 *  the rest within one cycle.  Keep the list minimal. */
const _MAIN_SUB_EQUALIZE_FIELDS = ['freqHz', 'mode', 'filter'] as const;

function _activateReceiver(target: 'MAIN' | 'SUB'): void {
  // Optimistic UI + WS command to select the receiver.
  patchRadioState({ active: target });
  cmd('set_vfo', { vfo: target });
  // Couple audio focus to the selected receiver so operator hears the
  // band they're now tuning.  In Dual-Watch mode the radio broadcasts
  // both receivers' audio, and the web layer decides which channel to
  // render via the Phones L/R Mix (#752/#755).  Without this coupling,
  // clicking MAIN/SUB updated state + scope but left the audio focus
  // untouched, so the user heard MAIN while tuning SUB.
  audioManager.setAudioConfig({ focus: target === 'SUB' ? 'sub' : 'main' });
}

export function makeVfoHandlers() {
  return {
    onSwap: () => {
      // IC-7610 ``0x07 0xB0`` swaps freq+mode+params between MAIN and SUB.
      // Optimistically swap the two in the store so the UI reflects the
      // change within one tick rather than waiting for the next poll.
      const s = getRadioState();
      if (s?.main && s?.sub) {
        const mainSnap: Partial<ReceiverState> = {};
        const subSnap: Partial<ReceiverState> = {};
        for (const f of _MAIN_SUB_EQUALIZE_FIELDS) {
          Object.assign(mainSnap, { [f]: s.sub[f] });
          Object.assign(subSnap, { [f]: s.main[f] });
        }
        patchReceiver(0, mainSnap);
        patchReceiver(1, subSnap);
      }
      cmd('vfo_swap');
    },
    onEqual: () => {
      // IC-7610 ``0x07 0xB1`` copies MAIN state to SUB.  Optimistically
      // mirror that in the store so the SUB readouts snap to MAIN's
      // values immediately — previously users had to wait for the next
      // poll cycle (~250ms) to see the change.
      const s = getRadioState();
      if (s?.main) {
        const snap: Partial<ReceiverState> = {};
        for (const f of _MAIN_SUB_EQUALIZE_FIELDS) {
          Object.assign(snap, { [f]: s.main[f] });
        }
        patchReceiver(1, snap);
      }
      cmd('vfo_equalize');
    },
    onSplitToggle: () => {
      const next = !(getRadioState()?.split ?? false);
      patchRadioState({ split: next });
      cmd('set_split', { on: next });
    },
    onMainVfoClick: () => _activateReceiver('MAIN'),
    onSubVfoClick: () => _activateReceiver('SUB'),
    onMainModeClick: () => focusModePanel('MAIN'),
    onSubModeClick: () => focusModePanel('SUB'),
    onMainFreqChange: (freq: number) => {
      patchReceiver(0, { freqHz: freq }, true);
      cmd('set_freq', { freq, receiver: 0 });
    },
    onSubFreqChange: (freq: number) => {
      patchReceiver(1, { freqHz: freq }, true);
      cmd('set_freq', { freq, receiver: 1 });
    },
    onFreqChange: (freq: number, receiver: Receiver = 0) => {
      patchActiveReceiver({ freqHz: freq }, true);
      cmd('set_freq', { freq, receiver });
    },
    onModeChange: (mode: string, receiver: Receiver = 0) => {
      patchActiveReceiver({ mode }, true);
      cmd('set_mode', { mode, receiver });
    },
    onFilterChange: (filter: number, receiver: Receiver = 0) => {
      cmd('set_filter', { filter, receiver });
    },
    onDualWatchToggle: (on: boolean) => cmd('set_dual_watch', { on }),
    // Epic #774 — composite triggers on the backend.  Double-click on the
    // DW / SPLIT button fires these; backend emits equalize M→S then the
    // corresponding toggle-on atomically.
    onQuickDw: () => cmd('quick_dualwatch'),
    onQuickSplit: () => cmd('quick_split'),
    onTrackingToggle: (on: boolean) => cmd('set_main_sub_tracking', { on }),
  };
}

/* ── Mode Handlers ───────────────────────────────────────────── */

export function makeModeHandlers() {
  return {
    onModeChange: (mode: string) => {
      const pending = consumePendingFocus();
      const receiver: Receiver = pending
        ? (pending === 'SUB' ? 1 : 0)
        : activeReceiverParam();
      patchActiveReceiver({ mode }, true);
      cmd('set_mode', { mode, receiver });
    },
    onDataModeChange: (mode: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ dataMode: mode }, true);
      cmd('set_data_mode', { mode, receiver });
    },
  };
}

/* ── RF Front End Handlers ───────────────────────────────────── */

export function makeRfFrontEndHandlers() {
  return {
    onAttChange: (db: number) => {
      patchActiveReceiver({ att: db });
      cmd('set_attenuator', { db, receiver: activeReceiverParam() });
    },
    onPreChange: (level: number) => {
      patchActiveReceiver({ preamp: level });
      cmd('set_preamp', { level, receiver: activeReceiverParam() });
    },
    onRfGainChange: (level: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ rfGain: level }, true);
      cmd('set_rf_gain', { level, receiver });
    },
    onSquelchChange: (level: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ squelch: level }, true);
      cmd('set_squelch', { level, receiver });
    },
    onDigiSelToggle: (on: boolean) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ digisel: on });
      cmd('set_digisel', { on, receiver });
    },
    onIpPlusToggle: (on: boolean) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ ipplus: on });
      cmd('set_ip_plus', { on, receiver });
    },
  };
}

/* ── Filter Handlers ─────────────────────────────────────────── */

export function makeFilterHandlers() {
  return {
    onFilterChange: (filter: number) => {
      const rx = getActiveReceiver();
      const caps = getCapabilities();
      const mode = rx?.mode?.toUpperCase();
      const dataMode = rx?.dataMode ?? 0;
      // Resolve per-mode filter config for optimistic BW update
      let estimatedWidth: number | undefined;
      if (mode && caps?.filterConfig) {
        const candidates = [];
        if (dataMode > 0) candidates.push(`${mode}-D`);
        candidates.push(mode);
        if (mode === 'USB' || mode === 'LSB') {
          if (dataMode > 0) candidates.push('SSB-D');
          candidates.push('SSB');
        }
        for (const c of candidates) {
          const cfg = caps.filterConfig[c];
          if (cfg?.defaults?.[filter - 1] != null) {
            estimatedWidth = cfg.defaults[filter - 1];
            break;
          }
        }
      }
      const patch: Record<string, unknown> = { filter };
      if (estimatedWidth != null) {
        patch.filterWidth = estimatedWidth;
      }
      patchActiveReceiver(patch, true);
      cmd('set_filter', { filter, receiver: activeReceiverParam() });
    },
    onFilterWidthChange: (() => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      return (width: number) => {
        patchActiveReceiver({ filterWidth: width }, true);
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          cmd('set_filter_width', { width, receiver: activeReceiverParam() });
        }, 200);
      };
    })(),
    onFilterShapeChange: (shape: number) => {
      patchActiveReceiver({ filterShape: shape }, true);
      cmd('set_filter_shape', { shape, receiver: activeReceiverParam() });
    },
    onFilterPresetChange: (() => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      return (filter: number, width: number) => {
        // Optimistic UI update immediately
        const activeFilter = getActiveReceiver()?.filter ?? 1;
        if (filter === activeFilter) {
          patchActiveReceiver({ filterWidth: width }, true);
        }
        // Debounce CI-V commands to avoid flooding the radio
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          const receiver = activeReceiverParam();
          const currentActive = getActiveReceiver()?.filter ?? 1;
          if (filter !== currentActive) {
            cmd('set_filter', { filter, receiver });
          }
          cmd('set_filter_width', { width, receiver });
          if (filter !== currentActive) {
            cmd('set_filter', { filter: currentActive, receiver });
          }
        }, 200);
      };
    })(),
    onFilterDefaults: (defaults: number[]) => {
      const receiver = activeReceiverParam();
      const activeFilter = getActiveReceiver()?.filter ?? 1;
      // Send all at once, sequentially — not per-tick
      for (let i = 0; i < defaults.length; i++) {
        const filter = i + 1;
        if (filter !== activeFilter) {
          cmd('set_filter', { filter, receiver });
        }
        cmd('set_filter_width', { width: defaults[i], receiver });
      }
      if (activeFilter <= defaults.length) {
        cmd('set_filter', { filter: activeFilter, receiver });
        patchActiveReceiver({ filterWidth: defaults[activeFilter - 1] }, true);
      }
    },
    onIfShiftChange: (value: number) => {
      const receiver = activeReceiverParam();
      const caps = getCapabilities();
      if (caps?.capabilities?.includes('if_shift')) {
        // Native IF shift (Yaesu CAT)
        patchActiveReceiver({ ifShift: value }, true);
        cmd('set_if_shift', { offset: value, receiver });
      } else {
        // Emulate via PBT (Icom CI-V)
        const activeRx = getActiveReceiver();
        const { pbtInner, pbtOuter } = mapIfShiftToPbt(
          value,
          activeRx?.pbtInner ?? 0,
          activeRx?.pbtOuter ?? 0,
        );
        patchActiveReceiver({ pbtInner: pbtHzToRaw(pbtInner), pbtOuter: pbtHzToRaw(pbtOuter) }, true);
        cmd('set_pbt_inner', { value: pbtHzToRaw(pbtInner), receiver });
        cmd('set_pbt_outer', { value: pbtHzToRaw(pbtOuter), receiver });
      }
    },
    onPbtInnerChange: (value: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ pbtInner: pbtHzToRaw(value) }, true);
      cmd('set_pbt_inner', { value: pbtHzToRaw(value), receiver });
    },
    onPbtOuterChange: (value: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ pbtOuter: pbtHzToRaw(value) }, true);
      cmd('set_pbt_outer', { value: pbtHzToRaw(value), receiver });
    },
    onPbtReset: () => {
      const receiver = activeReceiverParam();
      const center = pbtHzToRaw(0);
      patchActiveReceiver({ pbtInner: center, pbtOuter: center }, true);
      cmd('set_pbt_inner', { value: center, receiver });
      cmd('set_pbt_outer', { value: center, receiver });
    },
  };
}

/* ── AGC Handlers ────────────────────────────────────────────── */

export function makeAgcHandlers() {
  return {
    onAgcModeChange: (mode: number) => {
      patchActiveReceiver({ agc: mode });
      cmd('set_agc', { mode, receiver: activeReceiverParam() });
    },
  };
}

/* ── RIT / XIT Handlers ──────────────────────────────────────── */

export function makeRitXitHandlers() {
  return {
    onRitToggle: () => {
      const next = !(getRadioState()?.ritOn ?? false);
      patchRadioState({ ritOn: next });
      cmd('set_rit_status', { on: next });
    },
    onXitToggle: () => {
      const next = !(getRadioState()?.ritTx ?? false);
      patchRadioState({ ritTx: next });
      cmd('set_rit_tx_status', { on: next });
    },
    onRitOffsetChange: (hz: number) => {
      patchRadioState({ ritFreq: hz });
      cmd('set_rit_frequency', { freq: hz });
    },
    onXitOffsetChange: (hz: number) => {
      // RIT and ∂TX share the same offset register
      patchRadioState({ ritFreq: hz });
      cmd('set_rit_frequency', { freq: hz });
    },
    onClear: () => {
      patchRadioState({ ritFreq: 0 });
      cmd('set_rit_frequency', { freq: 0 });
    },
  };
}

/* ── DSP Handlers ────────────────────────────────────────────── */

export function makeDspHandlers() {
  return {
    onNrModeChange: (mode: number) => {
      const on = mode > 0;
      const receiver = activeReceiverParam();
      patchActiveReceiver({ nr: on });
      cmd('set_nr', { on, receiver });
    },
    onNrLevelChange: (level: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ nrLevel: level }, true);
      cmd('set_nr_level', { level, receiver });
    },
    onNbToggle: (on: boolean) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ nb: on });
      cmd('set_nb', { on, receiver });
    },
    onNbLevelChange: (level: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ nbLevel: level }, true);
      cmd('set_nb_level', { level, receiver });
    },
    onNotchModeChange: (mode: string) => {
      const receiver = activeReceiverParam();
      if (mode === 'auto') {
        patchActiveReceiver({ autoNotch: true, manualNotch: false });
        cmd('set_auto_notch', { on: true, receiver });
      } else if (mode === 'manual') {
        patchActiveReceiver({ autoNotch: false, manualNotch: true });
        cmd('set_manual_notch', { on: true, receiver });
      } else {
        patchActiveReceiver({ autoNotch: false, manualNotch: false });
        cmd('set_auto_notch', { on: false, receiver });
        cmd('set_manual_notch', { on: false, receiver });
      }
    },
    onNotchFreqChange: (value: number) => {
      const receiver = activeReceiverParam();
      cmd('set_notch_filter', { value, receiver });
    },
    onNbDepthChange: (level: number) => {
      patchRadioState({ nbDepth: level });
      cmd('set_nb_depth', { level });
    },
    onNbWidthChange: (level: number) => {
      patchRadioState({ nbWidth: level });
      cmd('set_nb_width', { level });
    },
    onManualNotchWidthChange: (value: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ manualNotchWidth: value }, true);
      cmd('set_manual_notch_width', { value, receiver });
    },
    onAgcTimeChange: (value: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ agcTimeConstant: value }, true);
      cmd('set_agc_time_constant', { value, receiver });
    },
  };
}

/** CW panel — RightSidebar / CwPanel.svelte */
export function makeCwPanelHandlers() {
  return {
    onCwPitchChange: (value: number) => {
      patchRadioState({ cwPitch: value });
      cmd('set_cw_pitch', { value });
    },
    onKeySpeedChange: (speed: number) => {
      patchRadioState({ keySpeed: speed });
      cmd('set_key_speed', { speed });
    },
    onBreakInToggle: () => {
      const current = getRadioState()?.breakIn ?? 0;
      const next = current > 0 ? 0 : 1;
      patchRadioState({ breakIn: next });
      cmd('set_break_in', { mode: next });
    },
    onBreakInModeChange: (mode: number) => {
      patchRadioState({ breakIn: mode });
      cmd('set_break_in', { mode });
    },
    onApfChange: (mode: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ apfTypeLevel: mode }, true);
      cmd('set_apf', { mode, receiver });
    },
    onTwinPeakToggle: () => {
      const receiver = activeReceiverParam();
      const state = getRadioState();
      const rx = receiver === 0 ? state?.main : state?.sub;
      const current = rx?.twinPeakFilter ?? false;
      const next = !current;
      patchActiveReceiver({ twinPeakFilter: next }, true);
      cmd('set_twin_peak', { on: next, receiver });
    },
    onAutoTune: () => {
      cmd('cw_auto_tune', {});
    },
    onWpmChange: (speed: number) => {
      patchRadioState({ keySpeed: speed });
      cmd('set_key_speed', { speed });
    },
    onBreakInDelayChange: (level: number) => {
      patchRadioState({ breakInDelay: level });
      cmd('set_break_in_delay', { level });
    },
    onSidetonePitchChange: (value: number) => {
      patchRadioState({ cwPitch: value });
      cmd('set_cw_pitch', { value });
    },
    onSidetoneLevelChange: (level: number) => {
      patchRadioState({ monitorGain: level });
      cmd('set_monitor_gain', { level });
    },
    onReversePaddleToggle: () => {
      const current = getRadioState()?.dashRatio ?? 0;
      const next = current < 0 ? 0 : -1;
      patchRadioState({ dashRatio: next });
      cmd('set_dash_ratio', { ratio: next });
    },
    onKeyerTypeChange: (type: number) => {
      cmd('set_keyer_type', { type });
    },
  };
}

/* ── VOX Handlers ───────────────────────────────────────────── */

export function makeVoxHandlers() {
  return {
    onVoxToggle: () => {
      const next = !(getRadioState()?.voxOn ?? false);
      patchRadioState({ voxOn: next });
      cmd('set_vox', { on: next });
    },
    onVoxGainChange: (level: number) => {
      patchRadioState({ voxGain: level });
      cmd('set_vox_gain', { level });
    },
    onAntiVoxGainChange: (level: number) => {
      patchRadioState({ antiVoxGain: level });
      cmd('set_anti_vox_gain', { level });
    },
    onVoxDelayChange: (level: number) => {
      patchRadioState({ voxDelay: level });
      cmd('set_vox_delay', { level });
    },
  };
}

/* ── TX Handlers ─────────────────────────────────────────────── */

export function makeTxHandlers() {
  return {
    onRfPowerChange: (level: number) => {
      patchRadioState({ powerLevel: level });
      cmd('set_rf_power', { level });
    },
    onMicGainChange: (level: number) => {
      patchRadioState({ micGain: level });
      cmd('set_mic_gain', { level });
    },
    onAtuToggle: () => {
      const next = (getRadioState()?.tunerStatus ?? 0) > 0 ? 0 : 1;
      patchRadioState({ tunerStatus: next });
      cmd('set_tuner_status', { value: next });
    },
    onAtuTune: () => {
      cmd('set_tuner_status', { value: 2 }); // Start tuning
    },
    onVoxToggle: () => {
      const next = !(getRadioState()?.voxOn ?? false);
      patchRadioState({ voxOn: next });
      cmd('set_vox', { on: next });
    },
    onCompToggle: () => {
      const next = !(getRadioState()?.compressorOn ?? false);
      patchRadioState({ compressorOn: next });
      cmd('set_compressor', { on: next });
    },
    onCompLevelChange: (level: number) => {
      patchRadioState({ compressorLevel: level });
      cmd('set_compressor_level', { level });
    },
    onMonToggle: () => {
      const next = !(getRadioState()?.monitorOn ?? false);
      patchRadioState({ monitorOn: next });
      cmd('set_monitor', { on: next });
    },
    onMonLevelChange: (level: number) => {
      patchRadioState({ monitorGain: level });
      cmd('set_monitor_gain', { level });
    },
    onDriveGainChange: (level: number) => {
      patchRadioState({ driveGain: level });
      cmd('set_drive_gain', { level });
    },
    onPttOn: () => cmd('ptt_on'),
    onPttOff: () => cmd('ptt_off'),
  };
}

/* ── RX Audio Handlers ───────────────────────────────────────── */

let savedAfLevel: number | null = null;

export function makeRxAudioHandlers() {
  return {
    onMonitorModeChange: (mode: string) => {
      if (mode === 'live') {
        setMuted(false);
        // Restore radio AF if was muted
        if (savedAfLevel !== null) {
          cmd('set_af_level', { level: savedAfLevel, receiver: activeReceiverParam() });
          savedAfLevel = null;
        }
        audioManager.startRx();
        return;
      }

      audioManager.stopRx();

      if (mode === 'mute') {
        setMuted(true);
        // Save current AF level and mute radio
        const rx = getRadioState();
        const key = rx?.active === 'SUB' ? 'sub' : 'main';
        const currentAf = rx?.[key]?.afLevel ?? 128;
        if (savedAfLevel === null) savedAfLevel = currentAf;
        cmd('set_af_level', { level: 0, receiver: activeReceiverParam() });
      } else {
        // Radio mode — restore AF if was muted
        setMuted(false);
        if (savedAfLevel !== null) {
          cmd('set_af_level', { level: savedAfLevel, receiver: activeReceiverParam() });
          patchActiveReceiver({ afLevel: savedAfLevel }, true);
          savedAfLevel = null;
        }
      }
    },
    onAfLevelChange: (level: number) => {
      if (audioManager.rxEnabled) {
        // Live mode: browser volume only
        audioManager.setRxVolume(level / 255);
        setVolume(Math.round(level / 255 * 100));
      } else {
        // Radio mode: CI-V AF level
        const receiver = activeReceiverParam();
        patchActiveReceiver({ afLevel: level }, true);
        cmd('set_af_level', { level, receiver });
      }
    },
  };
}

/* ── Dual-RX audio routing (#756) ────────────────────────────────
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

/* ── Band Selector Handlers ──────────────────────────────────── */

export function makePresetHandlers() {
  return {
    onPresetSelect: (freq: number, mode: string, filter?: number) => {
      cmd('set_freq', { freq, receiver: 0 });
      cmd('set_mode', { mode, filter: filter ?? 1, receiver: 0 });
    },
    onFreqPreset: (freq: number, mode: string, filter?: number) => {
      cmd('set_freq', { freq, receiver: 0 });
      cmd('set_mode', { mode, filter: filter ?? 1, receiver: 0 });
    },
  };
}

export function makeBandHandlers() {
  return {
    onBandSelect: (_name: string, freq: number, bsrCode?: number) => {
      if (bsrCode !== undefined) {
        cmd('set_band', { band: bsrCode });
      } else {
        // Bands without BSR code (e.g. 60m) — fall back to direct freq set
        cmd('set_freq', { freq, receiver: 0 });
      }
    },
  };
}

/* ── Antenna Handlers ────────────────────────────────────────── */

export function makeAntennaHandlers() {
  return {
    onSelectAnt1: () => {
      // Preserve current RX-ANT state when switching TX antenna.
      const rxOn = getRadioState()?.rxAntenna1 ?? false;
      patchRadioState({ txAntenna: 1 });
      cmd('set_antenna_1', { on: rxOn });
    },
    onSelectAnt2: () => {
      const rxOn = getRadioState()?.rxAntenna2 ?? false;
      patchRadioState({ txAntenna: 2 });
      cmd('set_antenna_2', { on: rxOn });
    },
    onToggleRxAnt: () => {
      // RX-ANT is encoded as data byte of 0x12 0x00/0x01 and is tied to the current TX ANT.
      const s = getRadioState();
      const tx = s?.txAntenna ?? 1;
      const current = tx === 2 ? (s?.rxAntenna2 ?? false) : (s?.rxAntenna1 ?? false);
      const next = !current;
      if (tx === 2) {
        patchRadioState({ txAntenna: 2, rxAntenna2: next });
        cmd('set_rx_antenna_ant2', { on: next });
      } else {
        patchRadioState({ txAntenna: 1, rxAntenna1: next });
        cmd('set_rx_antenna_ant1', { on: next });
      }
    },
  };
}

/* ── Meter Handlers ──────────────────────────────────────────── */

export function makeMeterHandlers() {
  return {
    onMeterSourceChange: (source: string) => {
      patchRadioState({ meterSource: source as 'S' | 'SWR' | 'POWER' });
    },
  };
}

/* ── System Handlers ─────────────────────────────────────────── */

export function makeSystemHandlers() {
  return {
    onPttOn: () => cmd('ptt_on'),
    onPttOff: () => cmd('ptt_off'),
    onDialLock: (on: boolean) => cmd('set_dial_lock', { on }),
    onPowerOff: () => cmd('set_powerstat', { on: false }),
    onSpeak: () => cmd('speak', { mode: 0 }),
  };
}

/* ── Scan Handlers ──────────────────────────────────────────── */

export function makeScanHandlers() {
  return {
    onScanStart: (type: number) => {
      patchRadioState({ scanning: true, scanType: type });
      cmd('scan_start', { type });
    },
    onScanStop: () => {
      patchRadioState({ scanning: false, scanType: 0 });
      cmd('scan_stop');
    },
    onDfSpanChange: (span: number) => {
      cmd('scan_set_df_span', { span });
    },
    onResumeChange: (mode: number) => {
      patchRadioState({ scanResumeMode: mode & 0x0F });
      cmd('scan_set_resume', { mode });
    },
  };
}

function cycleValue(values: number[], current: number): number {
  if (values.length === 0) {
    return current;
  }
  const idx = values.indexOf(current);
  if (idx < 0 || idx === values.length - 1) {
    return values[0];
  }
  return values[idx + 1];
}

export function makeKeyboardHandlers() {
  return {
    dispatch(action: KeyboardActionConfig): void {
      switch (action.action) {
        case 'tune': {
          const rx = getActiveReceiver();
          const baseFreq = rx?.freqHz ?? 0;
          if (baseFreq <= 0) {
            return;
          }
          const deltaHz = typeof action.params?.deltaHz === 'number'
            ? action.params.deltaHz
            : (action.params?.direction === 'down' ? -1 : 1) * getTuningStep();
          const freq = baseFreq + deltaHz;
          patchActiveReceiver({ freqHz: freq }, true);
          cmd('set_freq', { freq, receiver: activeReceiverParam() });
          return;
        }
        case 'adjust_tuning_step': {
          adjustTuningStep(action.params?.direction === 'down' ? 'down' : 'up');
          return;
        }
        case 'band_select': {
          const index = Number(action.params?.index ?? 0);
          if (index > 0) {
            cmd('set_band', { band: index });
          }
          return;
        }
        case 'cycle_preamp': {
          const values = getCapabilities()?.preValues ?? [0, 1];
          const current = getActiveReceiver()?.preamp ?? values[0] ?? 0;
          const level = cycleValue(values, current);
          patchActiveReceiver({ preamp: level });
          cmd('set_preamp', { level, receiver: activeReceiverParam() });
          return;
        }
        case 'toggle_split': {
          const next = !(getRadioState()?.split ?? false);
          patchRadioState({ split: next });
          cmd('set_split', { on: next });
          return;
        }
        case 'cycle_data_mode': {
          const max = getCapabilities()?.dataModeCount ?? 0;
          const current = getActiveReceiver()?.dataMode ?? 0;
          const mode = current >= max ? 0 : current + 1;
          patchActiveReceiver({ dataMode: mode }, true);
          cmd('set_data_mode', { mode, receiver: activeReceiverParam() });
          return;
        }
        case 'open_filter_settings': {
          window.dispatchEvent(new CustomEvent('rigplane:open-filter-settings'));
          return;
        }
        case 'mode_select': {
          const mode = action.params?.mode;
          if (typeof mode === 'string') {
            patchActiveReceiver({ mode }, true);
            cmd('set_mode', { mode, receiver: activeReceiverParam() });
          }
          return;
        }
        case 'cycle_filter': {
          const current = getActiveReceiver()?.filter ?? 1;
          const direction = action.params?.direction;
          let next: number;
          if (direction === 'wider') {
            next = current <= 1 ? 3 : current - 1;
          } else if (direction === 'narrower') {
            next = current >= 3 ? 1 : current + 1;
          } else {
            next = current >= 3 ? 1 : current + 1;
          }
          patchActiveReceiver({ filter: next }, true);
          cmd('set_filter', { filter: next, receiver: activeReceiverParam() });
          return;
        }
        case 'toggle_nr': {
          const on = !(getActiveReceiver()?.nr ?? false);
          patchActiveReceiver({ nr: on }, true);
          cmd('set_nr', { on, receiver: activeReceiverParam() });
          return;
        }
        case 'toggle_nb': {
          const on = !(getActiveReceiver()?.nb ?? false);
          patchActiveReceiver({ nb: on }, true);
          cmd('set_nb', { on, receiver: activeReceiverParam() });
          return;
        }
        case 'cycle_agc': {
          const modes = getCapabilities()?.agcModes ?? [1, 2, 3];
          const current = getActiveReceiver()?.agc ?? modes[0] ?? 1;
          const mode = cycleValue(modes, current);
          patchActiveReceiver({ agc: mode }, true);
          cmd('set_agc', { mode, receiver: activeReceiverParam() });
          return;
        }
        case 'cycle_att': {
          const values = getCapabilities()?.attValues ?? [0];
          const current = getActiveReceiver()?.att ?? 0;
          const db = cycleValue(values, current);
          patchActiveReceiver({ att: db }, true);
          cmd('set_attenuator', { db, receiver: activeReceiverParam() });
          return;
        }
        case 'toggle_auto_notch': {
          const on = !(getActiveReceiver()?.autoNotch ?? false);
          patchActiveReceiver({ autoNotch: on }, true);
          cmd('set_auto_notch', { on, receiver: activeReceiverParam() });
          return;
        }
        case 'toggle_monitor': {
          const on = !(getRadioState()?.monitorOn ?? false);
          patchRadioState({ monitorOn: on });
          cmd('set_monitor', { on });
          return;
        }
        case 'toggle_ip_plus': {
          const on = !(getActiveReceiver()?.ipplus ?? false);
          patchActiveReceiver({ ipplus: on }, true);
          cmd('set_ip_plus', { on, receiver: activeReceiverParam() });
          return;
        }
        case 'toggle_dial_lock': {
          const on = !(getRadioState()?.dialLock ?? false);
          patchRadioState({ dialLock: on });
          cmd('set_dial_lock', { on });
          return;
        }
        case 'toggle_rit': {
          const on = !(getRadioState()?.ritOn ?? false);
          patchRadioState({ ritOn: on });
          cmd('set_rit_status', { on });
          return;
        }
        case 'toggle_xit': {
          const on = !(getRadioState()?.ritTx ?? false);
          patchRadioState({ ritTx: on });
          cmd('set_rit_tx_status', { on });
          return;
        }
        case 'clear_rit_xit': {
          patchRadioState({ ritFreq: 0 });
          cmd('set_rit_frequency', { freq: 0 });
          return;
        }
        case 'adjust_af_level': {
          const current = getActiveReceiver()?.afLevel ?? 128;
          const delta = (action.params?.direction === 'down' ? -5 : 5);
          const level = Math.max(0, Math.min(255, current + delta));
          patchActiveReceiver({ afLevel: level }, true);
          cmd('set_af_level', { level, receiver: activeReceiverParam() });
          return;
        }
        case 'adjust_rf_gain': {
          const current = getActiveReceiver()?.rfGain ?? 255;
          const delta = (action.params?.direction === 'down' ? -5 : 5);
          const level = Math.max(0, Math.min(255, current + delta));
          patchActiveReceiver({ rfGain: level }, true);
          cmd('set_rf_gain', { level, receiver: activeReceiverParam() });
          return;
        }
        case 'vfo_swap': {
          cmd('vfo_swap', {});
          return;
        }
        case 'vfo_equalize': {
          cmd('vfo_equalize', {});
          return;
        }
        case 'switch_active_vfo': {
          const state = getRadioState();
          const next = state?.active === 'SUB' ? 'MAIN' : 'SUB';
          patchRadioState({ active: next });
          cmd('set_vfo', { vfo: next });
          return;
        }
        case 'set_active_vfo': {
          const target = action.params?.vfo;
          if (target !== 'MAIN' && target !== 'SUB') {
            return;
          }
          // Route through the same helper the VFO-click path uses so the
          // audio focus follows the active receiver (#827 follow-up): a
          // `m`/Shift+M/Shift+S keypress must behave identically to
          // clicking MAIN/SUB, otherwise the operator tunes one side but
          // keeps hearing the other in Dual-Watch / browser-audio flows.
          _activateReceiver(target);
          return;
        }
        case 'focus_target': {
          const target = action.params?.target;
          if (typeof target === 'string') {
            const selectors: Record<string, string> = {
              af: '[data-panel="rf-frontend"] [data-control="af-gain"]',
              rf:
                '[data-panel="rf-frontend"] [data-control="rf-sql-dual"], [data-panel="rf-frontend"] [data-control="rf-gain"]',
              filter: '[data-panel="filter"]',
              squelch:
                '[data-panel="rf-frontend"] [data-control="rf-sql-dual"], [data-panel="rf-frontend"] [data-control="squelch"]',
              mode: '[data-panel="mode"]',
              pbt: '[data-panel="filter"] [data-control="pbt-inner"]',
              waterfall: '[data-waterfall]',
              vfo: '[data-vfo="main"] .freq-display',
            };
            const el = document.querySelector(selectors[target] ?? `[data-panel="${target}"]`);
            if (el instanceof HTMLElement) {
              el.focus();
              el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
          }
          return;
        }
        case 'scope_span_step': {
          const scope = getRadioState()?.scopeControls;
          const current = scope?.span ?? 3;
          const delta = action.params?.direction === 'down' ? -1 : 1;
          const span = clampSpan(current, delta);
          cmd('set_scope_span', { span });
          return;
        }
        case 'scope_ref_step': {
          const scope = getRadioState()?.scopeControls;
          const current = scope?.refDb ?? 0;
          const delta = action.params?.direction === 'down' ? -5 : 5;
          const ref = clampRef(current, delta);
          cmd('set_scope_ref', { ref });
          return;
        }
        case 'scope_toggle_hold': {
          const scope = getRadioState()?.scopeControls;
          const on = !(scope?.hold ?? false);
          cmd('set_scope_hold', { on });
          return;
        }
        case 'scope_toggle_dual': {
          const scope = getRadioState()?.scopeControls;
          const dual = !(scope?.dual ?? false);
          cmd('set_scope_dual', { dual });
          return;
        }
        case 'scope_toggle_fst': {
          const scope = getRadioState()?.scopeControls;
          const currentSpeed = scope?.speed ?? 1;
          // Toggle FST (speed=0) vs MID (speed=1).
          const speed = currentSpeed === 0 ? 1 : 0;
          cmd('set_scope_speed', { speed });
          return;
        }
        default:
          console.warn('[keyboard] unhandled action', action.action, action.params);
      }
    },
  };
}
