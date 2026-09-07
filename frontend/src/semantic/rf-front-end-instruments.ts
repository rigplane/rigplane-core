import type { Snippet } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandFeedbackContinuousPairInput } from '../primitives/scalar/continuous-pair.svelte';
import type { DisabledReasonCode, RadioViewModel } from './radio-view-model';

export const RF_FRONT_END_LEVELS = [
  ['rfGain', 'RF gain', 0, 1, 0.01],
  ['squelch', 'Squelch', 0, 1, 0.01],
] as const;

export type RfFrontEndLevelField = (typeof RF_FRONT_END_LEVELS)[number][0];
export type RfSqlControlModel = 'separate' | 'combined';
export type RfFrontEndLevelFeedback = Readonly<
  Pick<CommandFeedbackContinuousPairInput, 'rf' | 'sql'>
>;

/** `[field, label]` on/off finite controls (MOR-1293, relocated here from
 *  `RfFrontEndSurface.svelte`'s module script by MOR-2425 RF-B alongside the
 *  finite handles below — same "group-owned" home `DSP_TOGGLES` has in
 *  `dsp-instruments.ts`). */
export const RF_FRONT_END_TOGGLES = [
  ['digiSel', 'DIGI-SEL'], ['ipPlus', 'IP+'],
] as const;
export type RfFrontEndToggleField = (typeof RF_FRONT_END_TOGGLES)[number][0];
export type RfFrontEndFiniteChoiceValue = number;

/** Carry-forward 4 (MOR-1293): keyed by the generic CODE, never by a peer-
 *  control name — the mutex label must read the same whichever control
 *  triggers it. */
export const DISABLED_REASON_LABEL: Partial<Record<DisabledReasonCode, string>> = {
  'mutually-exclusive-control': 'disabled: another control is active',
};

export interface RfFrontEndControlSession {
  readonly state: 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
  readonly epoch: number;
}

export interface RfFrontEndAuthorityPublication {
  readonly state: ServerState | null;
  readonly caps: Capabilities | null;
  readonly session: RfFrontEndControlSession;
}

export type SubscribeRfFrontEndAuthority = (
  handler: (publication: RfFrontEndAuthorityPublication) => void,
) => () => void;

export type RfFrontEndInstrumentPresentation = Readonly<
  RfFrontEndAuthorityPublication & {
    readonly view: RadioViewModel | null;
    readonly controlModel: RfSqlControlModel;
    readonly rfSqlFeedback?: RfFrontEndLevelFeedback | null;
  }
>;

/** Preamp/attenuator/DIGI-SEL/IP+ finite handles (MOR-2425 RF-B) — always
 *  present in `RfFrontEndLevelHandles` below alongside whichever RF/SQL
 *  level shape is active. */
export interface RfFrontEndFiniteHandles {
  readonly preamp: Snippet;
  readonly attenuator: Snippet;
  readonly digiSel: Snippet;
  readonly ipPlus: Snippet;
}

export type RfFrontEndLevelHandles =
  | Readonly<{ kind: 'combined'; rfSql: Snippet } & RfFrontEndFiniteHandles>
  | Readonly<{ kind: 'separate'; rfGain: Snippet; squelch: Snippet } & RfFrontEndFiniteHandles>;

/** DSP analogue (`dsp-instruments.ts`'s `DspFiniteLayout`): a layout snippet
 *  that places the four finite handles wherever the active face wants them,
 *  in place of `RfFrontEndSurface`'s own default grouping (MOR-2425 RF-B). */
export type RfFrontEndFiniteLayout = Snippet<[RfFrontEndFiniteHandles]>;
