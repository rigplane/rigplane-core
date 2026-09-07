import type { Component } from 'svelte';
import type { Skin as HostScalarAppearance } from '../../src/components-v2/controls/value-control/skin';
import type { FrequencyInteraction as HostFrequencyInteraction } from '../../src/primitives/frequency/frequency-interaction.svelte';
import type { FrequencyReadoutModel as HostFrequencyReadoutModel } from '../../src/primitives/frequency/frequency-readout';
import type { InstrumentReading as HostInstrumentReading } from '../../src/primitives/control-instruments/control-instrument-behavior';
import type {
  ActionRendererLease as HostActionRendererLease,
  ActionRendererProps as HostActionRendererProps,
  ActionRendererView as HostActionRendererView,
  ChoiceRendererLease as HostChoiceRendererLease,
  ChoiceRendererProps as HostChoiceRendererProps,
  ChoiceRendererView as HostChoiceRendererView,
  ControlLabel as HostControlLabel,
  ControlOption as HostControlOption,
  FiniteControlAppearance as HostFiniteControlAppearance,
  RequestedTarget as HostRequestedTarget,
  ToggleRendererLease as HostToggleRendererLease,
  ToggleRendererProps as HostToggleRendererProps,
  ToggleRendererView as HostToggleRendererView,
} from '../../src/primitives/control-instruments/control-instrument-renderer.svelte';
import type { InstrumentGroup as HostInstrumentGroup } from '../../src/presentation/groups/contract';
import type { DesignLanguageManifest as HostDesignLanguageManifest } from '../../src/presentation/languages/contract';
import type { LayoutManifest as HostLayoutManifest } from '../../src/presentation/layouts/contract';
import type { AppResource } from '../../src/lib/runtime/resource-demand';

export const COMPONENT_KIT_API_VERSION = 1 as const;

export type ComponentKitApiVersion = typeof COMPONENT_KIT_API_VERSION;
export type ScalarAppearance = HostScalarAppearance;
export type FrequencyInteraction = HostFrequencyInteraction;
export type FrequencyReadoutModel = HostFrequencyReadoutModel;
export type DesignLanguageManifest = HostDesignLanguageManifest;
export type LayoutManifest = Omit<HostLayoutManifest, 'loader'>;
export type InstrumentGroup = HostInstrumentGroup;
export type PresentationComponent = Component;
export type PresentationResources = readonly AppResource[];
export type FiniteChoiceValue = string | number;
export type FiniteControlReading<T extends FiniteChoiceValue = FiniteChoiceValue> =
  HostInstrumentReading<T>;
export type ControlLabel = HostControlLabel;
export type ControlOption<T extends FiniteChoiceValue = FiniteChoiceValue> = HostControlOption<T>;
export type RequestedTarget<T extends FiniteChoiceValue = FiniteChoiceValue> =
  HostRequestedTarget<T>;
export type ActionRendererView<Feedback = unknown> = HostActionRendererView<Feedback>;
export type ToggleRendererView<Feedback = unknown> = HostToggleRendererView<Feedback>;
export type ChoiceRendererView<
  T extends FiniteChoiceValue = FiniteChoiceValue,
  Feedback = unknown,
> = HostChoiceRendererView<T, Feedback>;
export type ActionRendererLease<Feedback = unknown> = HostActionRendererLease<Feedback>;
export type ToggleRendererLease<Feedback = unknown> = HostToggleRendererLease<Feedback>;
export type ChoiceRendererLease<
  T extends FiniteChoiceValue = FiniteChoiceValue,
  Feedback = unknown,
> = HostChoiceRendererLease<T, Feedback>;
export type ActionRendererProps = HostActionRendererProps;
export type ToggleRendererProps = HostToggleRendererProps;
export type ChoiceRendererProps<T extends FiniteChoiceValue = FiniteChoiceValue> =
  HostChoiceRendererProps<T>;
export type FiniteControlAppearance = HostFiniteControlAppearance<FiniteChoiceValue>;

/**
 * Signal `engineering/db` values are dB relative to S9, never dBm.
 * `raw` and `unknown` do not authorize an engineering unit label.
 */
export type MeterDisplayDomain =
  | Readonly<{ kind: 'engineering'; unit: 'db' | 'normalized' | 'w' | 'ratio' | 'v' | 'a' }>
  | Readonly<{ kind: 'raw' }>
  | Readonly<{ kind: 'unknown' }>;

/** Stale evidence may retain its numeric value while live projected geometry is unavailable. */
export type MeterNumericEvidence =
  | Readonly<{
    state: 'current' | 'stale';
    value: number;
    domain: MeterDisplayDomain;
  }>
  | Readonly<{
    state: 'idle' | 'unknown' | 'unsupported';
    domain: MeterDisplayDomain;
  }>;
export type SignalMeterEvidence =
  | Readonly<{ state: 'current'; value: number; domain: MeterDisplayDomain }>
  | Readonly<{ state: 'unknown'; domain: MeterDisplayDomain }>;
export type SignalMeterScaleMode = 's' | 'raw' | 'none';
export type SignalMeterTickKind = 'major' | 'mid' | 'minor';
export interface SignalMeterMark {
  readonly actual: number;
  readonly fraction: number;
  readonly text: string;
}
export interface SignalMeterTick {
  readonly fraction: number;
  readonly kind: SignalMeterTickKind;
}
export interface SignalMeterRendererView {
  readonly kind: 'signal';
  readonly evidence: SignalMeterEvidence;
  readonly relevant?: boolean;
  readonly scaleMode: SignalMeterScaleMode;
  readonly displayedFraction: number | null;
  readonly peakFraction: number | null;
  readonly primaryText: string;
  readonly secondaryText: string;
  readonly accessibleDescription: string;
  readonly crossoverFraction: number | null;
  readonly marks: readonly SignalMeterMark[];
  readonly ticks: readonly SignalMeterTick[];
}
export type LevelMeterKey =
  | 'power' | 'swr' | 'alc' | 'drainCurrent' | 'drainVoltage' | 'compression';
interface LevelMeterRendererViewBase<Key extends LevelMeterKey> {
  readonly kind: 'level';
  readonly key: Key;
  readonly label: string;
  readonly evidence: MeterNumericEvidence;
  readonly relevant: boolean;
  readonly observed: boolean;
  readonly displayedFraction: number | null;
  readonly peakFraction: number | null;
  readonly displayText: string;
  readonly stateText: string;
  readonly accessibleDescription?: string;
  readonly gauge: boolean;
  readonly fault: boolean;
  readonly peakEnabled: boolean;
}
export type LevelMeterRendererView =
  | Readonly<LevelMeterRendererViewBase<Exclude<LevelMeterKey, 'swr'>>>
  | Readonly<LevelMeterRendererViewBase<'swr'> & { readonly ratioScale: boolean }>;
export interface SignalMeterRendererProps { readonly view: Readonly<SignalMeterRendererView> }
export interface LevelMeterRendererProps {
  readonly view: Readonly<LevelMeterRendererView>;
  readonly resetPeak?: ActionRendererLease;
}
export interface MeterAppearance {
  readonly signal: Component<SignalMeterRendererProps>;
  readonly level: Component<LevelMeterRendererProps>;
}

export interface FrequencyRendererProps {
  readonly model: Readonly<FrequencyReadoutModel>;
  readonly interaction: FrequencyInteraction;
  readonly presentation: 'interactive' | 'passive';
  readonly compact: boolean;
  readonly active: boolean;
  readonly receiver: 'main' | 'sub';
  readonly vfoFreqHook: boolean;
}

export type FrequencyRenderer = Component<FrequencyRendererProps>;

export interface PresentationDeclaration {
  readonly id: string;
  readonly loader: () => Promise<PresentationComponent>;
  readonly resources: PresentationResources;
}

export interface ComponentKitDeclaration {
  readonly apiVersion: ComponentKitApiVersion;
  readonly id: string;
  readonly scalarAppearances?: Readonly<Record<string, ScalarAppearance>>;
  readonly frequencyReadouts?: Readonly<Record<string, FrequencyRenderer>>;
  readonly finiteControlAppearances?: Readonly<Record<string, FiniteControlAppearance>>;
  readonly meterAppearances?: Readonly<Record<string, MeterAppearance>>;
  readonly designLanguages?: readonly DesignLanguageManifest[];
  readonly layouts?: readonly LayoutManifest[];
  readonly instrumentGroups?: readonly InstrumentGroup[];
  readonly presentations?: readonly PresentationDeclaration[];
}

export function defineComponentKit<const T extends ComponentKitDeclaration>(kit: T): T {
  return kit;
}
