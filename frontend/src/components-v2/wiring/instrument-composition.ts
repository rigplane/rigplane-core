import type { Snippet } from 'svelte';
import type { ManagedScopeRegion } from '$lib/runtime/adapters/scope-display-projection';
import type { TxAuxScalarHandles } from '../../semantic/tx-aux-scalar';
import type { TxAuxFiniteHandles } from '../../semantic/tx-aux-finite';
import type { ReceiverInstrumentHandles } from '../../semantic/ReceiverInstrumentHost.svelte';
import type { RxAudioInstrumentHandles } from '../../semantic/rx-audio-instruments';

export type InstrumentVfoAppearance = 'semantic' | 'sdr' | 'standard';

export interface InstrumentComposition {
  readonly vfo: Snippet<[appearance: InstrumentVfoAppearance, allowBare?: boolean]>;
  readonly rxTx: Snippet<[allowBare?: boolean]>;
  readonly txAuxControls: Snippet<[scalarLayout: Snippet, allowBare?: boolean]>;
  readonly txAuxScalars: TxAuxScalarHandles;
  readonly txAuxInstruments: TxAuxFiniteHandles;
  readonly receiverInstruments: ReceiverInstrumentHandles;
  readonly rxAudioInstruments: RxAudioInstrumentHandles;
  readonly meters: Snippet<[allowBare?: boolean]>;
  readonly rxAudio: Snippet<[allowBare?: boolean]>;
  readonly rfFrontEnd: Snippet<[allowBare?: boolean]>;
  readonly filter: Snippet<[allowBare?: boolean]>;
  readonly dsp: Snippet<[allowBare?: boolean]>;
  readonly band: Snippet<[allowBare?: boolean]>;
  readonly antenna: Snippet<[allowBare?: boolean]>;
  readonly ritXitScan: Snippet<[allowBare?: boolean]>;
  readonly cwKeyer: Snippet<[allowBare?: boolean]>;
  readonly scopeDisplay: Snippet<[allowBare?: boolean]>;
  readonly scopeControls: Snippet<[allowBare?: boolean]>;
  readonly txFaultRecovery: Snippet;
  readonly modInputTxWarning: Snippet;
  readonly managedScope: ManagedScopeRegion | undefined;
}
