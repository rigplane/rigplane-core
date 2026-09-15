import type { Snippet } from 'svelte';
import type { CommandScalarFeedback } from '../primitives/scalar/continuous-scalar.svelte';

export const TX_AUX_LEVELS = [
  ['rfPower', 'RF power', 0, 1, 0.01, undefined],
  ['micGain', 'Mic gain', 0, 255, 1, undefined],
  ['driveGain', 'Drive gain', 0, 255, 1, undefined],
  ['voxGain', 'VOX gain', 0, 255, 1, undefined],
  ['antiVoxGain', 'Anti-VOX', 0, 255, 1, undefined],
  ['voxDelay', 'VOX delay', 0, 20, 1, (value: number) => `${(value * 0.1).toFixed(1)}s`],
  ['compressorLevel', 'COMP level', 0, 255, 1, undefined],
  ['monitorLevel', 'MON level', 0, 255, 1, undefined],
] as const;

export type TxAuxLevelField = (typeof TX_AUX_LEVELS)[number][0];

export const TX_AUX_FEEDBACK_LEVELS = [
  'micGain', 'driveGain', 'voxGain', 'antiVoxGain', 'voxDelay',
  'compressorLevel', 'monitorLevel',
] as const;

export type TxAuxFeedbackLevelField = (typeof TX_AUX_FEEDBACK_LEVELS)[number];
export type TxAuxLevelFeedback = Readonly<Record<
  TxAuxFeedbackLevelField,
  Readonly<CommandScalarFeedback>
>>;

export type TxAuxContinuousForm = 'hbar' | 'knob';

export interface TxAuxScalarPresentation {
  readonly form?: TxAuxContinuousForm;
  readonly compact?: boolean;
  readonly showLabel?: boolean;
  readonly showValue?: boolean;
  readonly variant?: 'modern' | 'hardware' | 'hardware-illuminated';
}

export type TxAuxScalarHandle = Snippet<[
  presentation?: Readonly<TxAuxScalarPresentation>,
]>;

export type TxAuxScalarHandles = Readonly<Record<TxAuxLevelField, TxAuxScalarHandle>>;
