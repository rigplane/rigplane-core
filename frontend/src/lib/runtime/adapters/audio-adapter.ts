/**
 * Audio adapter — derives RX audio view-model props from runtime state.
 *
 * Call deriveRxAudioProps() inside a $derived() block for reactivity.
 * getRxAudioHandlers() returns stable callbacks — safe to call once.
 */

import { runtime } from '../frontend-runtime';
import { toRxAudioProps, type RxAudioProps } from '../props/panel-props';
import { makeRxAudioHandlers } from '../commands/panel-commands';
import { getAfLevelControlFeedback } from './panel-adapters';

export function deriveRxAudioProps(): RxAudioProps {
  const state = runtime.state;
  const caps = runtime.caps;
  // Track RX teardown even when monitorMode short-circuits on mute.
  const audio = { ...runtime.audio };
  // Audio-WS connection health — surfaced as a prop so RxAudioPanel
  // doesn't have to import `$lib/stores/connection.svelte` directly.
  const audioConnected = runtime.connectionAudio;
  return toRxAudioProps(state, caps, audio, audioConnected);
}

/** Radio AF readback for listening snapshots, independent of panel defaults. */
export function deriveObservedRxAfLevel(): number | null {
  const feedback = getAfLevelControlFeedback();
  return feedback.availability === 'available' ? feedback.confirmed : null;
}

// Stable handler object — delegates to existing command-bus logic.
// Created once; safe to call at module scope.
const _handlers = makeRxAudioHandlers();

export function getRxAudioHandlers() {
  return _handlers;
}
