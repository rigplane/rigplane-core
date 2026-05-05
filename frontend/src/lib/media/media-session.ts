/**
 * MediaSession API integration for mobile radio control.
 *
 * - Volume keys (previoustrack/nexttrack) -> frequency tuning
 * - Headphone play/pause button -> PTT toggle
 *
 * A silent audio loop keeps the MediaSession active on mobile browsers.
 */

import { tuneBy } from '../stores/tuning.svelte';
import { patchActiveReceiver } from '../stores/radio.svelte';
import { sendCommand } from '../transport/ws-client';

const TAG = '[media-session]';

let audioCtx: AudioContext | null = null;
let oscillator: OscillatorNode | null = null;
let gainNode: GainNode | null = null;

/** Tune the active receiver by `steps` increments and send to radio. */
function tuneStep(steps: number): void {
  const newFreq = tuneBy(steps);
  if (newFreq <= 0) return;
  patchActiveReceiver({ freqHz: newFreq }, true);
  sendCommand('set_freq', { freq: newFreq, receiver: 0 });
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
 * Initialize MediaSession handlers for volume-key tuning and headphone PTT.
 * Call once on app startup. Safe to call in environments without MediaSession.
 */
export function initMediaSession(): void {
  if (!('mediaSession' in navigator)) {
    console.debug(TAG, 'MediaSession API not available');
    return;
  }

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

  // Headphone play/pause -> PTT
  navigator.mediaSession.setActionHandler('play', () => {
    console.debug(TAG, 'play -> PTT on');
    sendCommand('ptt', { state: true });
  });

  navigator.mediaSession.setActionHandler('pause', () => {
    console.debug(TAG, 'pause -> PTT off');
    sendCommand('ptt', { state: false });
  });

  console.info(TAG, 'handlers registered (tuning + PTT)');
}

/**
 * Remove MediaSession handlers and stop the silent audio loop.
 */
export function destroyMediaSession(): void {
  if (!('mediaSession' in navigator)) return;

  stopSilentAudio();

  for (const action of ['previoustrack', 'nexttrack', 'play', 'pause'] as MediaSessionAction[]) {
    try {
      navigator.mediaSession.setActionHandler(action, null);
    } catch {
      // some browsers don't support clearing handlers
    }
  }

  console.info(TAG, 'handlers removed');
}
