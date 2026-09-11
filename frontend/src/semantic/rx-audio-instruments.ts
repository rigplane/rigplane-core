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

/** `SPLIT_CHOICES`'s own label type — used as the SPLIT seat's external-facing
 *  choice value (see `RxAudioFiniteChoiceValue` below). */
export type RxAudioSplitLabel = (typeof SPLIT_CHOICES)[number][1];

/** The one widened choice type `RxAudioInstrumentHost`'s finite seats and its
 *  `FiniteControlAppearance` parameter share — the same "declare the seat at
 *  the appearance's own union" shape `DspFiniteChoiceValue` uses. `boolean` is
 *  NOT a member: the public Component-Kit SDK's `FiniteChoiceValue` (every
 *  other production seat's bound) is `string | number` — `component-kit-api/
 *  src/index.ts`, checked directly — so the split seat reports its two
 *  choices by `SPLIT_CHOICES`'s own STRING label ('on'/'off'), not the raw
 *  boolean fact; `RxAudioInstrumentHost.svelte`'s `splitSeat` maps the label
 *  back to a boolean before calling `onSplitStereoChange`. The raw/bare
 *  (non-`finiteAppearance`) rendering is unaffected — it reads `SPLIT_CHOICES`
 *  and `rx.routingSplit`'s boolean fact directly, as before. */
export type RxAudioFiniteChoiceValue = MonitorMode | AudioFocus | RxAudioSplitLabel | number;

/** The finite five (`monitorMode`/`routingFocus`/`routingSplit`/
 *  `modInputSource`/`setModInputLan`), required exactly like every other
 *  shipped handle record in this directory (`DspFiniteHandles`,
 *  `TxAuxFiniteHandles`) — `RxAudioInstrumentHost.svelte` always constructs
 *  all five, so every `InstrumentComposition.rxAudioInstruments` consumer
 *  (`HostedRadioLayoutFixture.svelte` included) supplies them too
 *  (MOR-2425 RX-B/RX-C). */
export interface RxAudioInstrumentHandles {
  readonly afLevel: Snippet;
  readonly afLevelRow?: Snippet;
  readonly monitorMode: Snippet;
  readonly monitorStatus?: Snippet;
  readonly routingFocus: Snippet;
  readonly routingSplit: Snippet;
  readonly routingSplitToggle?: Snippet;
  readonly mainGain?: Snippet;
  readonly subGain?: Snippet;
  readonly modInputSource: Snippet;
  readonly setModInputLan: Snippet;
}

/** DSP/RF analogue (`dsp-instruments.ts`'s `DspFiniteLayout`,
 *  `rf-front-end-instruments.ts`'s `RfFrontEndFiniteLayout`): a layout
 *  snippet that places the finite five wherever the active face wants them,
 *  in place of `RxAudioSurface`'s own default grouping (MOR-2425 RX-B/RX-C).
 *  Takes the full handles record (`afLevel` included) — `RxAudioSurface.svelte`
 *  keeps rendering `afLevel` itself regardless of `finiteLayout`. */
export type RxAudioFiniteLayout = Snippet<[RxAudioInstrumentHandles]>;
