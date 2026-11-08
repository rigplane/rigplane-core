/**
 * Audio adapter — derives RX audio view-model props from runtime state.
 *
 * Call deriveRxAudioProps() inside a $derived() block for reactivity.
 * getRxAudioHandlers() returns stable callbacks — safe to call once.
 */

import { runtime } from '../frontend-runtime';
import { toRxAudioProps, type RxAudioProps } from '../props/panel-props';
import { makeRxAudioHandlers } from '../commands/panel-commands';
import { isFieldAvailable } from '$lib/state/field-status';

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
  const { state, caps } = runtime;
  const receiver = state?.active === 'SUB' ? 'sub' : 'main';
  const observedAf = !!state && (caps?.receivers === 1 || isFieldAvailable(state, 'active'))
    && isFieldAvailable(state, `${receiver}.afLevel`);
  const level = state?.[receiver]?.afLevel;
  return observedAf && caps?.capabilities.includes('af_level') && Number.isFinite(level)
    ? level! : null;
}

// Stable handler object — delegates to existing command-bus logic.
// Created once; safe to call at module scope.
const _handlers = makeRxAudioHandlers();

export function getRxAudioHandlers() {
  return _handlers;
}
