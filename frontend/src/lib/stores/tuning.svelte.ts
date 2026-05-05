// Tuning step store — controls frequency step for all tuning methods

import { radio } from './radio.svelte';

/** Available tuning steps in Hz */
export const TUNING_STEPS = [10, 50, 100, 250, 500, 1_000, 5_000, 10_000, 25_000, 100_000] as const;

const STORAGE_STEP_KEY = 'rigplane.tuning-step-hz';
const STORAGE_AUTO_KEY = 'rigplane.tuning-step-auto';

/** Mode-based default steps */
const MODE_DEFAULTS: Record<string, number> = {
  'CW':     10,
  'CW-R':   10,
  'RTTY':   100,
  'RTTY-R': 100,
  'AM':     1_000,
  'FM':     25_000,
  // SSB and everything else → 1kHz
};

const DEFAULT_STEP = 1_000;

function _storage(): Storage | null {
  return typeof globalThis.localStorage?.getItem === 'function' ? globalThis.localStorage : null;
}

function _readStoredStep(): number {
  const raw = _storage()?.getItem(STORAGE_STEP_KEY);
  if (!raw) {
    return DEFAULT_STEP;
  }
  const parsed = Number(raw);
  return (TUNING_STEPS as readonly number[]).includes(parsed) ? parsed : DEFAULT_STEP;
}

function _readStoredAutoStep(): boolean {
  const raw = _storage()?.getItem(STORAGE_AUTO_KEY);
  if (raw == null) {
    return true;
  }
  return raw !== 'false';
}

function _persistState(): void {
  const storage = _storage();
  if (!storage) {
    return;
  }
  storage.setItem(STORAGE_STEP_KEY, String(_step));
  storage.setItem(STORAGE_AUTO_KEY, String(_autoStep));
  _syncToCompanion(_step);
}

/** Notify the companion (if present) about the tuning step change. */
function _syncToCompanion(hz: number): void {
  fetch('/api/local/v1/rc28/tuning-step', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tuning_step_hz: hz }),
  }).catch(() => {
    // Companion not running or endpoint not available — ignore.
  });
}

let _step = $state(_readStoredStep());
let _autoStep = $state(_readStoredAutoStep()); // auto-select step based on mode

// Sync initial step to companion on load.
_syncToCompanion(_readStoredStep());

export function getTuningStep(): number {
  return _step;
}

export function setTuningStep(hz: number): void {
  if (!(TUNING_STEPS as readonly number[]).includes(hz)) {
    return;
  }
  _step = hz;
  _autoStep = false; // manual override disables auto
  _persistState();
}

/** Update step from companion (RC-28) without affecting auto-step preference. */
export function setTuningStepFromCompanion(hz: number): void {
  if (!(TUNING_STEPS as readonly number[]).includes(hz)) {
    return;
  }
  if (_step === hz) {
    return; // no change — avoid redundant PUT back
  }
  _step = hz;
  _persistState();
}

export function setAutoStep(on: boolean): void {
  _autoStep = on;
  if (on) {
    const rx = radio.current?.active === 'SUB' ? radio.current?.sub : radio.current?.main;
    if (rx?.mode) {
      _step = MODE_DEFAULTS[rx.mode?.toUpperCase()] ?? DEFAULT_STEP;
    }
  }
  _persistState();
}

export function isAutoStep(): boolean {
  return _autoStep;
}

/** Apply mode-based default step (called when mode changes) */
export function applyModeDefault(mode: string): void {
  if (!_autoStep) return;
  _step = MODE_DEFAULTS[mode?.toUpperCase()] ?? DEFAULT_STEP;
  _persistState();
}

export function adjustTuningStep(direction: 'up' | 'down'): number {
  const idx = (TUNING_STEPS as readonly number[]).indexOf(_step);
  if (idx < 0) {
    _step = DEFAULT_STEP;
    _persistState();
    return _step;
  }
  const nextIdx = direction === 'down'
    ? Math.max(0, idx - 1)
    : Math.min(TUNING_STEPS.length - 1, idx + 1);
  setTuningStep(TUNING_STEPS[nextIdx]);
  return _step;
}

/** Snap frequency to nearest step boundary 
 * NOTE: Only used for display/tuning UI. Server always returns precise Hz.
 */
export function snapToStep(freqHz: number): number {
  if (_step <= 0) return freqHz;
  return Math.round(freqHz / _step) * _step;
}

/** Tune up/down by N steps (positive = up, negative = down) */
export function tuneBy(steps: number): number {
  const rx = radio.current?.active === 'SUB' ? radio.current?.sub : radio.current?.main;
  const freq = rx?.freqHz ?? 0;
  if (freq <= 0) return 0;
  return snapToStep(freq + steps * _step);
}

/** Format step for display */
export function formatStep(hz: number): string {
  if (hz >= 1_000_000) return `${hz / 1_000_000}MHz`;
  if (hz >= 1_000) return `${hz / 1_000}kHz`;
  return `${hz}Hz`;
}
