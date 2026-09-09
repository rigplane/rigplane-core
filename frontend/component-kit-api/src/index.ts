import type { Component, Snippet } from 'svelte';
import type { Skin as HostScalarAppearance } from '../../src/components-v2/controls/value-control/skin';
import type {
  ContinuousScalarRendererLease as HostScalarRendererLease,
  ContinuousScalarRendererSeat as HostScalarRendererSeat,
  ContinuousScalarView as HostScalarRendererView,
} from '../../src/primitives/scalar/continuous-scalar.svelte';
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
export type ScalarRendererLease = HostScalarRendererLease;
export type ScalarRendererSeat = HostScalarRendererSeat;
export type ScalarRendererView = HostScalarRendererView;
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

/** The two values a level bar's ends stand for: `min` at empty, `max` at full. */
export interface MeterScaleDomain {
  readonly min: number;
  readonly max: number;
}

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
  /**
   * What this bar's empty and full ends stand for, in the meter's own unit.
   * Null where the level is not positioned against such a scale.
   */
  readonly scale: MeterScaleDomain | null;
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

export interface ReceiverFrequencyPresentationV1 {
  readonly compact?: boolean;
}

export type ReceiverFrequencyHandleV1 = Snippet<[
  presentation?: Readonly<ReceiverFrequencyPresentationV1>,
]>;

export type ReceiverSignalMeterHandleV1 = Snippet<[]>;

export interface ReceiverInstrumentFamilyV1 {
  readonly mainFrequency: ReceiverFrequencyHandleV1;
  readonly subFrequency: ReceiverFrequencyHandleV1 | null;
  readonly mainSMeter: ReceiverSignalMeterHandleV1;
  readonly subSMeter: ReceiverSignalMeterHandleV1 | null;
}

export type VfoOperationHandleV1 = Snippet<[]>;

export interface VfoOperationInstrumentFamilyV1 {
  readonly split: VfoOperationHandleV1 | null;
  readonly dualWatch: VfoOperationHandleV1 | null;
  readonly activeReceiver: VfoOperationHandleV1 | null;
  readonly equalize: VfoOperationHandleV1 | null;
  readonly swap: VfoOperationHandleV1 | null;
  readonly quickSplit: VfoOperationHandleV1 | null;
  readonly quickDualWatch: VfoOperationHandleV1 | null;
  readonly speak: VfoOperationHandleV1 | null;
}

export type TxAuxContinuousFormV1 = 'hbar' | 'knob';

export interface TxAuxScalarPresentationV1 {
  readonly form?: TxAuxContinuousFormV1;
  readonly compact?: boolean;
  readonly showLabel?: boolean;
  readonly showValue?: boolean;
}

export type TxAuxScalarHandleV1 = Snippet<[
  presentation?: Readonly<TxAuxScalarPresentationV1>,
]>;

export interface TxAuxInstrumentFamilyV1 {
  readonly rfPower: TxAuxScalarHandleV1;
  readonly micGain: TxAuxScalarHandleV1;
  readonly driveGain: TxAuxScalarHandleV1;
  readonly voxGain: TxAuxScalarHandleV1;
  readonly antiVoxGain: TxAuxScalarHandleV1;
  readonly voxDelay: TxAuxScalarHandleV1;
  readonly compressorLevel: TxAuxScalarHandleV1;
  readonly monitorLevel: TxAuxScalarHandleV1;
}

export type StationMeterHandleV1 = Snippet<[]>;

export interface StationMeterInstrumentFamilyV1 {
  readonly signal: StationMeterHandleV1;
  readonly power: StationMeterHandleV1;
  readonly swr: StationMeterHandleV1;
  readonly alc: StationMeterHandleV1;
  readonly drainCurrent: StationMeterHandleV1;
  readonly drainVoltage: StationMeterHandleV1;
  readonly compression: StationMeterHandleV1;
}

export interface HostedInstrumentFamiliesV1 {
  readonly receiver: ReceiverInstrumentFamilyV1 | null;
  readonly vfoOperations: VfoOperationInstrumentFamilyV1 | null;
  readonly txAux: TxAuxInstrumentFamilyV1 | null;
  readonly stationMeters: StationMeterInstrumentFamilyV1 | null;
}

export interface HostedFacePropsV1 {
  readonly instruments: HostedInstrumentFamiliesV1;
}

export type HostedFaceComponentV1 = Component<HostedFacePropsV1>;

export interface HostedFaceAppearanceIdsV1 {
  readonly scalar: string;
  readonly frequency: string;
  readonly finite: string;
  readonly meter: string;
}

export interface HostedFacePresentationV1 {
  readonly hostMode: 'external-instruments-v1';
  readonly id: string;
  readonly layoutId: string;
  readonly loader: () => Promise<HostedFaceComponentV1>;
  readonly resources: PresentationResources;
  readonly appearances: HostedFaceAppearanceIdsV1;
}

export type ComponentKitPresentationDeclarationV1 =
  | PresentationDeclaration
  | HostedFacePresentationV1;

export interface ComponentKitDeclaration<
  P extends ComponentKitPresentationDeclarationV1 = PresentationDeclaration,
> {
  readonly apiVersion: ComponentKitApiVersion;
  readonly id: string;
  readonly scalarAppearances?: Readonly<Record<string, ScalarAppearance>>;
  readonly frequencyReadouts?: Readonly<Record<string, FrequencyRenderer>>;
  readonly finiteControlAppearances?: Readonly<Record<string, FiniteControlAppearance>>;
  readonly meterAppearances?: Readonly<Record<string, MeterAppearance>>;
  readonly designLanguages?: readonly DesignLanguageManifest[];
  readonly layouts?: readonly LayoutManifest[];
  readonly instrumentGroups?: readonly InstrumentGroup[];
  readonly presentations?: readonly P[];
}

export type HostedComponentKitDeclarationV1 =
  ComponentKitDeclaration<ComponentKitPresentationDeclarationV1>;

export function defineComponentKit<
  const T extends ComponentKitDeclaration<ComponentKitPresentationDeclarationV1>,
>(kit: T): T {
  return kit;
}
