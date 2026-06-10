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
  patchActiveReceiver,
  patchRadioState,
} from '$lib/stores/radio.svelte';
import { getCapabilities, getControlRange } from '$lib/stores/capabilities.svelte';
import { audioManager } from '$lib/audio/audio-manager';
import { setMuted, setVolume } from '$lib/stores/audio.svelte';
import { consumePendingFocus } from '$lib/radio/pending-focus';
import { getModeFilter } from '$lib/radio/mode-filter-memory';
import { modInputCommand, modInputStateKey } from '$lib/radio/mod-input';
import { nbDepthDisplayToRaw, nrDisplayToRaw } from '$lib/radio/filter-controls';
import type { ServerState } from '$lib/types/state';

/* ── Shared helpers ──────────────────────────────────────────────── */

type Receiver = 0 | 1;

function cmd(name: string, params: Record<string, unknown> = {}): void {
  sendCommand(name, params);
}

function activeReceiverParam(): Receiver {
  return getRadioState()?.active === 'SUB' ? 1 : 0;
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
      patchActiveReceiver({ agc: mode });
      cmd('set_agc', { mode, receiver: activeReceiverParam() });
    },
  };
}

/* ── Mode Handlers ───────────────────────────────────────────────── */

export function makeModeHandlers() {
  return {
    onModeChange: (mode: string) => {
      const pending = consumePendingFocus();
      const receiver: Receiver = pending ? (pending === 'SUB' ? 1 : 0) : activeReceiverParam();
      // MOR-495: recall the destination mode's remembered filter so the web
      // mirrors the front panel (mode-only 0x06 would force the radio's
      // mode-default filter, e.g. USB → FIL2).  Unseen mode → mode-only.
      const filter = getModeFilter(mode);
      patchActiveReceiver({ mode }, true);
      if (filter !== undefined) {
        cmd('set_mode', { mode, filter, receiver });
      } else {
        cmd('set_mode', { mode, receiver });
      }
    },
    onDataModeChange: (mode: number) => {
      const receiver = activeReceiverParam();
      patchActiveReceiver({ dataMode: mode }, true);
      cmd('set_data_mode', { mode, receiver });
    },
    onModInputChange: (source: number) => {
      // MOR-616: route the new source to the active receiver's DATA group
      // (DATA OFF/1/2/3 MOD, CI-V 0x1A 05 00 0x91-0x94). Optimistic
      // top-level patch; the backend confirms via write-through readback
      // (MOR-615), which also reverts the patch if the radio rejects it.
      const dataMode = getActiveReceiver()?.dataMode ?? 0;
      patchRadioState({ [modInputStateKey(dataMode)]: source } as Partial<ServerState>);
      cmd(modInputCommand(dataMode), { source });
    },
  };
}

/* ── Antenna Handlers ────────────────────────────────────────────── */

export function makeAntennaHandlers() {
  return {
    onSelectAnt1: () => {
      const rxOn = (getRadioState() as any)?.rxAntenna1 ?? false;
      patchRadioState({ txAntenna: 1 });
      cmd('set_antenna_1', { on: rxOn });
    },
    onSelectAnt2: () => {
      const rxOn = (getRadioState() as any)?.rxAntenna2 ?? false;
      patchRadioState({ txAntenna: 2 });
      cmd('set_antenna_2', { on: rxOn });
    },
    onToggleRxAnt: () => {
      const s = getRadioState() as any;
      const tx = (s?.txAntenna ?? 1) as number;
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

/* ── RF Front End Handlers ───────────────────────────────────────── */

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

/* ── RIT / XIT Handlers ──────────────────────────────────────────── */

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
      // RIT and XIT share the same offset register
      patchRadioState({ ritFreq: hz });
      cmd('set_rit_frequency', { freq: hz });
    },
    onClear: () => {
      patchRadioState({ ritFreq: 0 });
      cmd('set_rit_frequency', { freq: 0 });
    },
  };
}

/* ── Scan Handlers ───────────────────────────────────────────────── */

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
      patchRadioState({ scanResumeMode: mode & 0x0f });
      cmd('scan_set_resume', { mode });
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

/* ── DSP Handlers ────────────────────────────────────────────────── */

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
      // MOR-490: slider is 0-15 (front-panel scale); wire is 0-255 BCD.
      // Store the raw wire value optimistically so it matches the polled
      // readback (which the adapter scales raw -> display).
      const raw = nrDisplayToRaw(level);
      patchActiveReceiver({ nrLevel: raw }, true);
      cmd('set_nr_level', { level: raw, receiver });
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
      // MOR-498: slider is 1-10 (front-panel scale); wire is 0-9.  Store the
      // wire value optimistically so it matches the polled/NB-B readback
      // (which the adapter offsets wire -> display).
      const wire = nbDepthDisplayToRaw(level);
      patchRadioState({ nbDepth: wire });
      cmd('set_nb_depth', { level: wire });
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

/* ── TX Handlers ─────────────────────────────────────────────────── */

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

/* ── Filter Handlers ─────────────────────────────────────────────── */

export function makeFilterHandlers() {
  return {
    onFilterChange: (filter: number) => {
      const rx = getActiveReceiver();
      const caps = getCapabilities();
      const mode = rx?.mode?.toUpperCase();
      const dataMode = rx?.dataMode ?? 0;
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
        const activeFilter = getActiveReceiver()?.filter ?? 1;
        if (filter === activeFilter) {
          patchActiveReceiver({ filterWidth: width }, true);
        }
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
        patchActiveReceiver({ ifShift: value }, true);
        cmd('set_if_shift', { offset: value, receiver });
      } else {
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

/* ── Band Handlers ───────────────────────────────────────────────── */

export function makeBandHandlers() {
  return {
    onBandSelect: (_name: string, freq: number, bsrCode?: number) => {
      if (bsrCode !== undefined) {
        cmd('set_band', { band: bsrCode });
      } else {
        cmd('set_freq', { freq, receiver: 0 });
      }
    },
  };
}

/* ── Preset Handlers ─────────────────────────────────────────────── */

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

/* ── RX Audio Handlers ───────────────────────────────────────────── */

let savedAfLevel: number | null = null;

export function makeRxAudioHandlers() {
  return {
    onMonitorModeChange: (mode: string) => {
      if (mode === 'live') {
        setMuted(false);
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
        const rx = getRadioState();
        const key = rx?.active === 'SUB' ? 'sub' : 'main';
        const currentAf = rx?.[key]?.afLevel ?? 128;
        if (savedAfLevel === null) savedAfLevel = currentAf;
        cmd('set_af_level', { level: 0, receiver: activeReceiverParam() });
      } else {
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
        audioManager.setRxVolume(level / 255);
        setVolume(Math.round((level / 255) * 100));
      } else {
        const receiver = activeReceiverParam();
        patchActiveReceiver({ afLevel: level }, true);
        cmd('set_af_level', { level, receiver });
      }
    },
  };
}
