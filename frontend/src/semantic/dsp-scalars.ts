import type { Snippet } from 'svelte';
import type { CommandScalarFeedback } from '../primitives/scalar/continuous-scalar.svelte';

export const DSP_SCALAR_FIELDS = [
  'nbLevel', 'nbDepth', 'nbWidth', 'nrLevel',
  'notchFreq', 'manualNotchWidth', 'agcTimeConstant',
] as const;
export type DspScalarField = (typeof DSP_SCALAR_FIELDS)[number];
export type DspScalarFeedback = Readonly<Record<DspScalarField, Readonly<CommandScalarFeedback>>>;
export type DspScalarForm = 'hbar' | 'knob';

export interface DspScalarPresentation {
  readonly form?: DspScalarForm;
  readonly compact?: boolean;
  readonly showLabel?: boolean;
  readonly showValue?: boolean;
  readonly variant?: 'modern' | 'hardware' | 'hardware-illuminated';
}

export type DspScalarHandle = Snippet<[presentation?: Readonly<DspScalarPresentation>]>;
export type DspScalarHandles = Readonly<Record<DspScalarField, DspScalarHandle>>;
export type DspScalarLayout = Snippet<[DspScalarHandles]>;
