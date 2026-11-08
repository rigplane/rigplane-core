import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState } from '$lib/stores/radio.svelte';
import { getAudioState } from '$lib/stores/audio.svelte';
import { getConnectionStatus, getRadioPowerOn, isAudioConnected } from '$lib/stores/connection.svelte';
import { toRxAudioProps } from '$lib/runtime/props/panel-props';
import { RX_AF_LEVEL_MAX } from '$lib/audio/rx-player';
import { isFieldAvailable } from '$lib/state/field-status';
import { runtime } from '$lib/runtime/frontend-runtime';
import type { ListeningSnapshot } from './host-ui';

/** Read live inputs at request time as well as during reactive publication. */
export function readListeningSnapshot(): ListeningSnapshot {
  const state = getRadioState();
  const caps = getCapabilities();
  const props = toRxAudioProps(state, caps, getAudioState(), isAudioConnected());
  // Match makeRxAudioHandlers' routing even while mute/local teardown is pending.
  const live = runtime.rxEnabled;
  const level = live ? getAudioState().volume / 100 : props.afLevel;
  const receiver = state?.active === 'SUB' ? 'sub' : 'main';
  const observedAf = !!state && (caps?.receivers === 1 || isFieldAvailable(state, 'active'))
    && isFieldAvailable(state, `${receiver}.afLevel`);
  return { mode: props.monitorMode,
    level: Number.isFinite(level) && (live || (observedAf && caps?.capabilities.includes('af_level')))
      ? level : null,
    target: live ? 'playback' : 'radio',
    enabled: getConnectionStatus() === 'connected' && getRadioPowerOn() !== false
      && (props.hasAfLevel || props.hasLiveAudio),
    maxLevel: live ? RX_AF_LEVEL_MAX : 1 };
}
