import type { Snippet } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandFeedbackContinuousPairInput } from '../primitives/scalar/continuous-pair.svelte';
import type { RadioViewModel } from './radio-view-model';

export const RF_FRONT_END_LEVELS = [
  ['rfGain', 'RF gain', 0, 1, 0.01],
  ['squelch', 'Squelch', 0, 1, 0.01],
] as const;

export type RfFrontEndLevelField = (typeof RF_FRONT_END_LEVELS)[number][0];
export type RfSqlControlModel = 'separate' | 'combined';
export type RfFrontEndLevelFeedback = Readonly<
  Pick<CommandFeedbackContinuousPairInput, 'rf' | 'sql'>
>;

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

export type RfFrontEndLevelHandles =
  | Readonly<{ kind: 'combined'; rfSql: Snippet }>
  | Readonly<{ kind: 'separate'; rfGain: Snippet; squelch: Snippet }>;
