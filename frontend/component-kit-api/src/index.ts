import type { Component } from 'svelte';
import type { Skin as HostScalarAppearance } from '../../src/components-v2/controls/value-control/skin';
import type { FrequencyInteraction as HostFrequencyInteraction } from '../../src/primitives/frequency/frequency-interaction.svelte';
import type { FrequencyReadoutModel as HostFrequencyReadoutModel } from '../../src/primitives/frequency/frequency-readout';
import type { InstrumentGroup as HostInstrumentGroup } from '../../src/presentation/groups/contract';
import type { DesignLanguageManifest as HostDesignLanguageManifest } from '../../src/presentation/languages/contract';
import type { LayoutManifest as HostLayoutManifest } from '../../src/presentation/layouts/contract';
import type { loadSkin, presentationResourcePlan } from '../../src/skins/registry';

export const COMPONENT_KIT_API_VERSION = 1 as const;

export type ComponentKitApiVersion = typeof COMPONENT_KIT_API_VERSION;
export type ScalarAppearance = HostScalarAppearance;
export type FrequencyInteraction = HostFrequencyInteraction;
export type FrequencyReadoutModel = HostFrequencyReadoutModel;
export type DesignLanguageManifest = HostDesignLanguageManifest;
export type LayoutManifest = Omit<HostLayoutManifest, 'loader'>;
export type InstrumentGroup = HostInstrumentGroup;
export type PresentationComponent = Awaited<ReturnType<typeof loadSkin>>;
export type PresentationResources = ReturnType<typeof presentationResourcePlan>;

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
  readonly loader: () => ReturnType<typeof loadSkin>;
  readonly resources: PresentationResources;
}

export interface ComponentKitDeclaration {
  readonly apiVersion: ComponentKitApiVersion;
  readonly id: string;
  readonly scalarAppearances?: Readonly<Record<string, ScalarAppearance>>;
  readonly frequencyReadouts?: Readonly<Record<string, FrequencyRenderer>>;
  readonly designLanguages?: readonly DesignLanguageManifest[];
  readonly layouts?: readonly LayoutManifest[];
  readonly instrumentGroups?: readonly InstrumentGroup[];
  readonly presentations?: readonly PresentationDeclaration[];
}

export function defineComponentKit<const T extends ComponentKitDeclaration>(kit: T): T {
  return kit;
}
