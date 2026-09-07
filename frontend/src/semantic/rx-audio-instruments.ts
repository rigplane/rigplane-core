import type { Snippet } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { RxAudioViewModel } from './radio-view-model';

export interface RxAudioTargetAuthority {
  readonly muted: boolean;
  readonly rxEnabled: boolean;
}

export interface RxAudioControlSession {
  readonly state: 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
  readonly epoch: number;
}

export interface RxAudioAuthorityPublication {
  readonly state: ServerState | null;
  readonly caps: Capabilities | null;
  readonly session: RxAudioControlSession;
  readonly rxAudioTarget: RxAudioTargetAuthority;
}

export type SubscribeRxAudioAuthority = (
  handler: (publication: RxAudioAuthorityPublication) => void,
) => () => void;

export type RxAudioInstrumentPresentation = Readonly<RxAudioAuthorityPublication & {
  readonly rxAudio: RxAudioViewModel | undefined;
}>;

export interface RxAudioInstrumentHandles {
  readonly afLevel: Snippet;
}
