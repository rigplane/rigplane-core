import type { Snippet } from 'svelte';
import type { ManagedScopeRegion } from '$lib/runtime/adapters/scope-display-projection';
import type { TxAuxLevelField, TxAuxScalarHandles } from '../../semantic/tx-aux-scalar';
import type { TxAuxFiniteHandles } from '../../semantic/tx-aux-finite';
import type { ReceiverInstrumentHandles } from '../../semantic/ReceiverInstrumentHost.svelte';
import type {
  RxAudioFiniteLayout, RxAudioInstrumentHandles,
} from '../../semantic/rx-audio-instruments';
import type {
  RfFrontEndFiniteLayout, RfFrontEndLevelHandles,
} from '../../semantic/rf-front-end-instruments';
import type { DspFiniteLayout } from '../../semantic/dsp-instruments';
import type { DspScalarLayout } from '../../semantic/dsp-scalars';
import type { DspSurfacePart } from '../../semantic/DspSurface.svelte';
import type { VfoOperationHandles } from '../../semantic/VfoOperationSeatHost.svelte';
import type { FilterFiniteLayout } from '../../semantic/filter-instruments';
import type { BandControlLayout, BandInstrumentHandles } from '../../semantic/band-instruments';
import type { CwKeyerInstrumentHandles } from '../../semantic/CwKeyerInstrumentHost.svelte';
import type {
  AntennaInstrumentHandles, AntennaInstrumentLayout,
} from '../../semantic/AntennaInstrumentHost.svelte';
import type { RitXitScanInstrumentHandles } from '../../semantic/RitXitScanInstrumentHost.svelte';
import type { RitXitScanSurfacePart } from '../../semantic/RitXitScanSurface.svelte';

export type InstrumentVfoAppearance = 'semantic' | 'sdr' | 'standard';
export type StandardTxLevelAvailability = Readonly<Record<TxAuxLevelField, boolean>>;
export type StandardTxLayout = Snippet<[
  TxAuxFiniteHandles, TxAuxScalarHandles, StandardTxLevelAvailability,
]>;

export interface PanelChrome {
  readonly panelId: string;
  readonly title?: string;
  readonly draggable: boolean;
  readonly onDragStart: (panelId: string, event: PointerEvent) => void;
  readonly style: string;
}

export interface PanelDragOwner {
  readonly order: string[];
  readonly isDropTarget: boolean;
  dragStyle(panelId: string): string;
  handleDragStart(panelId: string, event: PointerEvent): void;
  resetAll(): void;
}

export interface InstrumentComposition {
  readonly vfo: Snippet<[
    appearance: InstrumentVfoAppearance,
    allowBare?: boolean,
    operationControls?: Snippet,
  ]>;
  readonly vfoOperations: VfoOperationHandles;
  readonly rxTx: Snippet<[
    allowBare?: boolean, chrome?: PanelChrome, standardTxLayout?: StandardTxLayout,
  ]>;
  readonly txAuxControls: Snippet<[
    scalarLayout: Snippet, allowBare?: boolean, chrome?: PanelChrome,
  ]>;
  readonly txAuxScalars: TxAuxScalarHandles;
  readonly txAuxInstruments: TxAuxFiniteHandles;
  readonly receiverInstruments: ReceiverInstrumentHandles;
  readonly rxAudioInstruments: RxAudioInstrumentHandles;
  readonly rfFrontEndInstruments: RfFrontEndLevelHandles;
  readonly meters: Snippet<[allowBare?: boolean, chrome?: PanelChrome]>;
  readonly rxAudio: Snippet<[
    allowBare?: boolean, finiteLayout?: RxAudioFiniteLayout, chrome?: PanelChrome,
  ]>;
  readonly rfFrontEnd: Snippet<[
    allowBare?: boolean, finiteLayout?: RfFrontEndFiniteLayout, chrome?: PanelChrome,
  ]>;
  readonly filter: Snippet<[
    allowBare?: boolean, finiteLayout?: FilterFiniteLayout, chrome?: PanelChrome,
    filterLayout?: FilterFiniteLayout, filterChrome?: PanelChrome,
  ]>;
  readonly dsp: Snippet<[
    allowBare?: boolean, finiteLayout?: DspFiniteLayout, scalarLayout?: DspScalarLayout,
    chrome?: PanelChrome, part?: DspSurfacePart, compactAgcTime?: boolean,
  ]>;
  readonly band: Snippet<[
    allowBare?: boolean, controlLayout?: BandControlLayout, chrome?: PanelChrome,
  ]>;
  readonly bandInstruments?: BandInstrumentHandles;
  readonly antenna: Snippet<[
    allowBare?: boolean, controlLayout?: Snippet, chrome?: PanelChrome,
  ]>;
  readonly antennaInstruments: AntennaInstrumentHandles;
  readonly antennaLayout: AntennaInstrumentLayout;
  readonly ritXitScan: Snippet<[
    allowBare?: boolean, chrome?: PanelChrome, part?: RitXitScanSurfacePart,
  ]>;
  readonly ritXitInstruments: RitXitScanInstrumentHandles;
  readonly cwKeyerInstruments: CwKeyerInstrumentHandles;
  readonly cwKeyer: Snippet<[
    allowBare?: boolean, showKeyerSpeed?: boolean, chrome?: PanelChrome,
    instrumentLayout?: Snippet, standard?: boolean,
  ]>;
  readonly memory: Snippet<[allowBare?: boolean, chrome?: PanelChrome]>;
  readonly scopeDisplay: Snippet<[allowBare?: boolean]>;
  readonly scopeControls: Snippet<[allowBare?: boolean]>;
  readonly txFaultRecovery: Snippet;
  readonly modInputTxWarning: Snippet;
  readonly managedScope: ManagedScopeRegion | undefined;
}
