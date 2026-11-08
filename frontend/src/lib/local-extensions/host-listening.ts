import { getAudioState } from '$lib/stores/audio.svelte';
import { getConnectionStatus, getRadioPowerOn } from '$lib/stores/connection.svelte';
import { deriveObservedRxAfLevel, deriveRxAudioProps } from '$lib/runtime/adapters/audio-adapter';
import { RX_AF_LEVEL_MAX } from '$lib/audio/rx-player';
import { runtime } from '$lib/runtime/frontend-runtime';
import type { ListeningSnapshot } from './host-ui';

/** Read live inputs at request time as well as during reactive publication. */
export function readListeningSnapshot(): ListeningSnapshot {
  const props = deriveRxAudioProps();
  // Match makeRxAudioHandlers' routing even while mute/local teardown is pending.
  const live = runtime.rxEnabled;
  const level = live ? getAudioState().volume / 100 : deriveObservedRxAfLevel();
  return { mode: props.monitorMode,
    level: Number.isFinite(level) ? level : null,
    target: live ? 'playback' : 'radio',
    enabled: getConnectionStatus() === 'connected' && getRadioPowerOn() !== false
      && (props.hasAfLevel || props.hasLiveAudio),
    maxLevel: live ? RX_AF_LEVEL_MAX : 1 };
}
