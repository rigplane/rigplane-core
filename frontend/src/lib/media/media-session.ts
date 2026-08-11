/**
 * MediaSession API integration for mobile radio control.
 *
 * - Volume keys (previoustrack/nexttrack) -> frequency tuning
 *
 * A silent audio loop keeps the MediaSession active on mobile browsers.
 */

import { runtime } from '../runtime/frontend-runtime';
import { toRadioViewModel } from '../runtime/adapters/radio-view-model-adapter';
import { getVfoHandlers } from '../runtime/adapters/panel-adapters';
import { getTuningStep, snapToStep } from '../stores/tuning.svelte';

const TAG = '[media-session]';

let audioCtx: AudioContext | null = null;
let oscillator: OscillatorNode | null = null;
let gainNode: GainNode | null = null;
let isInitialized = false;
let vfoHandlers: ReturnType<typeof getVfoHandlers> | null = null;

/** Tune the active receiver by `steps` increments and send to radio. */
function tuneStep(steps: number): void {
  if (!vfoHandlers) return;
  const view = toRadioViewModel(runtime.state, runtime.caps);
  if (!view || view.activeReceiver.status !== 'known') return;
  const receiver = view.activeReceiver.receiver;
  const active = view.vfos.filter((vfo) =>
    vfo.isActive && vfo.receiver === receiver);
  if (active.length !== 1) return;
  const current = active[0].frequencyHz;
  const step = getTuningStep();
  if (!Number.isSafeInteger(current) || current === null || current <= 0
    || !Number.isSafeInteger(step) || step <= 0) return;
  const candidate = current + steps * step;
  if (!Number.isSafeInteger(candidate)) return;
  const newFreq = snapToStep(candidate);
  if (!Number.isSafeInteger(newFreq) || newFreq <= 0 || newFreq === current) return;
  // MOR-1425 review B1: a fixed step-increment gesture — keep the
  // accumulate path, not the absolute-jump default.
  vfoHandlers.onFreqChange(newFreq, receiver === 'SUB' ? 1 : 0, 'step');
}

/** Start a silent audio loop so the browser keeps MediaSession alive. */
function startSilentAudio(): void {
  try {
    audioCtx = new AudioContext();
    oscillator = audioCtx.createOscillator();
    gainNode = audioCtx.createGain();
    gainNode.gain.value = 0; // silent
    oscillator.connect(gainNode);
    gainNode.connect(audioCtx.destination);
    oscillator.start();
    console.debug(TAG, 'silent audio loop started');
  } catch (e) {
    console.warn(TAG, 'failed to start silent audio:', e);
  }
}

/** Stop the silent audio loop and release resources. */
function stopSilentAudio(): void {
  try {
    oscillator?.stop();
  } catch {
    // already stopped
  }
  oscillator?.disconnect();
  gainNode?.disconnect();
  audioCtx?.close().catch(() => {});
  oscillator = null;
  gainNode = null;
  audioCtx = null;
}

/**
 * Initialize MediaSession handlers for volume-key tuning.
 * Call once on app startup. Safe to call in environments without MediaSession.
 */
export function initMediaSession(): void {
  if (!('mediaSession' in navigator)) {
    console.debug(TAG, 'MediaSession API not available');
    return;
  }
  if (isInitialized) {
    return;
  }

  vfoHandlers ??= getVfoHandlers();
  isInitialized = true;
  startSilentAudio();

  navigator.mediaSession.metadata = new MediaMetadata({
    title: 'RigPlane',
    artist: 'Radio Control',
  });

  // Volume keys -> tuning (previoustrack = down, nexttrack = up)
  navigator.mediaSession.setActionHandler('previoustrack', () => {
    console.debug(TAG, 'previoustrack -> tune down');
    tuneStep(-1);
  });

  navigator.mediaSession.setActionHandler('nexttrack', () => {
    console.debug(TAG, 'nexttrack -> tune up');
    tuneStep(1);
  });

  console.info(TAG, 'handlers registered (tuning only)');
}

/**
 * Remove MediaSession handlers and stop the silent audio loop.
 */
export function destroyMediaSession(): void {
  if (!('mediaSession' in navigator) || !isInitialized) return;

  stopSilentAudio();
  isInitialized = false;

  for (const action of ['previoustrack', 'nexttrack'] as MediaSessionAction[]) {
    try {
      navigator.mediaSession.setActionHandler(action, null);
    } catch {
      // some browsers don't support clearing handlers
    }
  }

  console.info(TAG, 'handlers removed');
}
