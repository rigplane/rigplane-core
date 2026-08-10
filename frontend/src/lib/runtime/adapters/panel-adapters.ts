/**
 * Panel adapters — derive props and handlers for self-wiring panels.
 *
 * Each panel calls its derive function inside $derived() and its
 * handler function once at init. This replaces prop-passing from sidebars.
 *
 * Add new panel adapters here as panels are migrated to self-wiring.
 */

import { runtime } from '../frontend-runtime';
import {
  toAgcProps, toModeProps, toAntennaProps,
  toRfFrontEndProps, toRitXitProps, toScanProps,
  toCwProps, toDspProps, toTxProps,
  toFilterProps, toBandSelectorProps,
  toAudioSpectrumProps, toMemoryPanelProps,
  toAmberTelemetryProps, toVfoControlProps,
} from '../props/panel-props';
import {
  makeAgcHandlers, makeModeHandlers, makeAntennaHandlers,
  makeRfFrontEndHandlers, makeRitXitHandlers, makeScanHandlers,
  makeCwPanelHandlers, makeDspHandlers,
  makeTxHandlers, makeFilterHandlers, makeBandHandlers,
  makePresetHandlers, makeAudioRoutingHandlers, makeRxAudioHandlers,
  makeVfoHandlers, makeScopeControlsHandlers, makeVoxHandlers, makeMemoryHandlers,
} from '../commands/panel-commands';
import { toRadioViewModel } from './radio-view-model-adapter';
import { getAppTxController, type AppTxController } from '../tx-controller/app-host';
import {
  hasAudioFft, hasDualReceiver, hasCapability,
} from '$lib/stores/capabilities.svelte';
import { recordQsy } from './qsy-history-adapter';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

// Re-export types for panel imports
export type {
  AgcProps, ModeProps, AntennaProps,
  RfFrontEndProps, RitXitProps, ScanProps,
  CwProps, DspProps, TxProps,
  FilterProps, BandSelectorProps,
  AudioSpectrumProps, MemoryPanelProps,
  AmberTelemetryProps, VfoControlProps,
} from '../props/panel-props';

// ── AGC ──
export function deriveAgcProps() {
  return toAgcProps(runtime.state, runtime.caps);
}
const _agcHandlers = makeAgcHandlers();
export function getAgcHandlers() { return _agcHandlers; }

// ── Mode ──
export function deriveModeProps() {
  return toModeProps(runtime.state, runtime.caps);
}
const _modeHandlers = makeModeHandlers();
export function getModeHandlers() { return _modeHandlers; }

// ── Antenna ──
export function deriveAntennaProps() {
  return toAntennaProps(runtime.state, runtime.caps);
}
const _antennaHandlers = makeAntennaHandlers();
export function getAntennaHandlers() { return _antennaHandlers; }

// ── RF Front End ──
export function deriveRfFrontEndProps() {
  return toRfFrontEndProps(runtime.state, runtime.caps);
}
const _rfHandlers = makeRfFrontEndHandlers();
export function getRfFrontEndHandlers() { return _rfHandlers; }

// ── RIT/XIT ──
export function deriveRitXitProps() {
  return toRitXitProps(runtime.state, runtime.caps);
}
const _ritXitHandlers = makeRitXitHandlers();
export function getRitXitHandlers() { return _ritXitHandlers; }

// ── Scan ──
export function deriveScanProps() {
  return toScanProps(runtime.state);
}
const _scanHandlers = makeScanHandlers();
export function getScanHandlers() { return _scanHandlers; }

// ── CW ──
export function deriveCwProps() {
  return toCwProps(runtime.state, runtime.caps);
}
const _cwHandlers = makeCwPanelHandlers();
export function getCwHandlers() { return _cwHandlers; }

// ── DSP ──
export function deriveDspProps() {
  return toDspProps(runtime.state, runtime.caps);
}
const _dspHandlers = makeDspHandlers();
export function getDspHandlers() { return _dspHandlers; }

// ── TX ──
export function deriveTxProps() {
  return toTxProps(runtime.state, runtime.caps);
}
const _txHandlers = makeTxHandlers();
export function getTxHandlers() { return _txHandlers; }

// ── Filter ──
export function deriveFilterProps() {
  return toFilterProps(runtime.state, runtime.caps);
}
const _filterHandlers = makeFilterHandlers();
export function getFilterHandlers() { return _filterHandlers; }

// ── Band Selector ──
export function deriveBandSelectorProps() {
  return toBandSelectorProps(runtime.state);
}
const _bandHandlers = makeBandHandlers();
export function getBandHandlers() { return _bandHandlers; }
const _presetHandlers = makePresetHandlers();
export function getPresetHandlers() { return _presetHandlers; }

// ── A04 semantic composition bindings ──
// This binder deliberately creates fresh family objects for each mounted
// composition root: filter handlers retain per-instance debounce state.
export function bindSemanticSurfaceHandlers() {
  return Object.freeze({
    agc: makeAgcHandlers(),
    antenna: makeAntennaHandlers(),
    audioRouting: makeAudioRoutingHandlers(),
    band: makeBandHandlers(),
    cw: makeCwPanelHandlers(),
    dsp: makeDspHandlers(),
    filter: makeFilterHandlers(),
    mode: makeModeHandlers(),
    rfFrontEnd: makeRfFrontEndHandlers(),
    ritXit: makeRitXitHandlers(),
    rxAudio: makeRxAudioHandlers(),
    scan: makeScanHandlers(),
    scopeControls: makeScopeControlsHandlers(),
    tx: makeTxHandlers(),
    vfo: makeVfoHandlers(),
    vox: makeVoxHandlers(),
  });
}

const _audioRoutingHandlers = makeAudioRoutingHandlers();
export function getAudioRoutingHandlers() { return _audioRoutingHandlers; }
const _vfoHandlers = makeVfoHandlers();
export function getVfoHandlers() { return _vfoHandlers; }

export type VfoTunerRead = Readonly<{
  tx: ReturnType<AppTxController['snapshot']>;
  view: ReturnType<typeof toRadioViewModel>;
}>;
export type VfoTunerContext = Readonly<{ read(): VfoTunerRead }>;

/** Captures the App TX facade once, while read() projects only live read-only facts. */
export function bindVfoTunerContext(): VfoTunerContext {
  const tx = getAppTxController();
  return Object.freeze({
    read: () => {
      const snapshot = tx.snapshot();
      return Object.freeze({
        tx: snapshot,
        view: toRadioViewModel(runtime.state, runtime.caps, snapshot),
      });
    },
  });
}

// ── Audio Spectrum ──
export function deriveAudioSpectrumProps() {
  return toAudioSpectrumProps(runtime.state, runtime.caps);
}

// ── Memory Panel ──
export function deriveMemoryPanelProps() {
  return toMemoryPanelProps(runtime.state, runtime.caps);
}
const _memoryHandlers = makeMemoryHandlers();
export function getMemoryHandlers() { return _memoryHandlers; }

// ── Amber Telemetry ──
export function deriveAmberTelemetryProps() {
  return toAmberTelemetryProps(runtime.state);
}

// ── VFO Control Panel ──
export function deriveVfoControlProps() {
  return toVfoControlProps(runtime.state, runtime.caps);
}

// ── AmberScope (LCD skin) ──
// Bundles radio.current + capabilities reads for the AmberScope panel
// so it doesn't import `$lib/stores/*` directly. AmberScope has ~12
// `hasCapability(name)` checks; we expose the function on props rather
// than inflate the type with a dozen booleans. See audit Cluster B.
export interface AmberScopeProps {
  radioState: ServerState | null;
  caps: Capabilities | null;
  hasAudioFft: boolean;
  hasDualReceiver: boolean;
  hasCapability: (name: string) => boolean;
}

export function deriveAmberScopeProps(): AmberScopeProps {
  return {
    radioState: runtime.state,
    caps: runtime.caps,
    hasAudioFft: hasAudioFft(),
    hasDualReceiver: hasDualReceiver(),
    hasCapability,
  };
}

// ── AmberCockpit (LCD skin) ──
// Merge point for Cluster B (radio.current), Cluster A (capabilities), and
// Cluster D (qsy-history write) — see audit doc Batch 5. Same shape as
// AmberScope (~16 hasCapability(name) checks → expose the function rather
// than inflate the type). The qsy-history write happens via the handler
// bundle below (`onTuningChange`) so the panel never imports the qsy store.
export interface AmberCockpitProps {
  radioState: ServerState | null;
  caps: Capabilities | null;
  hasAudioFft: boolean;
  hasDualReceiver: boolean;
  hasCapability: (name: string) => boolean;
}

export function deriveAmberCockpitProps(): AmberCockpitProps {
  return {
    radioState: runtime.state,
    caps: runtime.caps,
    hasAudioFft: hasAudioFft(),
    hasDualReceiver: hasDualReceiver(),
    hasCapability,
  };
}

export interface AmberCockpitHandlers {
  /**
   * Record a tuning change into the QSY history ring buffer (#836). The
   * underlying store debounces internally — caller may invoke this from a
   * reactive `$effect` whenever active-receiver freq/mode changes.
   */
  onTuningChange: (freqHz: number, mode: string) => void;
}

const _amberCockpitHandlers: AmberCockpitHandlers = {
  onTuningChange: (freqHz: number, mode: string) => {
    if (freqHz > 0) recordQsy(freqHz, mode);
  },
};

export function getAmberCockpitHandlers(): AmberCockpitHandlers {
  return _amberCockpitHandlers;
}
