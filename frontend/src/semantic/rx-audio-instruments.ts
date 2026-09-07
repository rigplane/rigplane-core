import type { Snippet } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type {
  AudioFocus, ModInputReadiness, MonitorMode, RxAudioViewModel,
} from './radio-view-model';

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

/** The three monitor modes, in the shipped `RxAudioPanel` order. */
export const MONITOR_MODES: readonly MonitorMode[] = ['local', 'live', 'mute'];
/** Dual-RX focus choices, verbatim `AudioRoutingControl`'s own order. */
export const FOCUS_CHOICES: readonly AudioFocus[] = ['main', 'sub', 'both'];
/** Stereo split as two ABSOLUTE choices rather than one relative toggle: a
 *  toggle computed from an unknown reading would arm a guess (the RxAudio
 *  1B rule), and it would leave the control permanently dead while routing
 *  is unobserved. `[value, label]`. */
export const SPLIT_CHOICES = [[true, 'on'], [false, 'off']] as const;
/** The ONE rendering of "not measured". Never 0, never 'both', never 'off'. */
export const UNKNOWN_TEXT = '—';
/** MOR-1384 — audio-link loss without an inferred retry state. */
export const LINK_LOST_TEXT = 'live audio link lost';
/** Readiness words. `mismatch` names the consequence, not just the state. */
export const READINESS_LABEL: Record<ModInputReadiness['status'], string> = {
  'not-applicable': 'n/a', ready: 'LAN', unknown: UNKNOWN_TEXT,
  mismatch: 'not LAN — web voice TX would modulate from the wrong source',
};

/** The one widened choice type `RxAudioInstrumentHost`'s finite seats and its
 *  `FiniteControlAppearance` parameter share — the same "declare the seat at
 *  the appearance's own union" shape `DspFiniteChoiceValue` uses. */
export type RxAudioFiniteChoiceValue = MonitorMode | AudioFocus | boolean | number;

/**
 * The finite five (`monitorMode`/`routingFocus`/`routingSplit`/
 * `modInputSource`/`setModInputLan`) are optional, unlike every other shipped
 * handle record in this directory (`DspFiniteHandles`, `TxAuxFiniteHandles`),
 * because `RxAudioInstrumentHandles` is ALSO the exact type
 * `InstrumentComposition.rxAudioInstruments` (`components-v2/wiring/
 * instrument-composition.ts`) uses, and several existing literal
 * `InstrumentComposition` fixtures outside this MOR-2425 RX-B/RX-C cut
 * (`semantic-desktop-migration.component.test.ts`, `skins/__tests__/
 * entrypoints.test.ts`, `DualSdrFaceSkin.component.test.ts`,
 * `HostedRadioLayoutFixture.svelte`) still construct it with only `afLevel`.
 * Widening those five as REQUIRED would break every one of them, and none is
 * in this cut's scope — `SemanticRadioSurfaces.svelte`,
 * `instrument-composition.ts` and `HostedRadioLayoutFixture.svelte` are the
 * root/L1-owned integration seam this cut must not edit. Phase B's SRS wiring
 * is the natural place to either supply all five everywhere or tighten this
 * back to required once every consumer does.
 */
export interface RxAudioInstrumentHandles {
  readonly afLevel: Snippet;
  readonly monitorMode?: Snippet;
  readonly routingFocus?: Snippet;
  readonly routingSplit?: Snippet;
  readonly modInputSource?: Snippet;
  readonly setModInputLan?: Snippet;
}
