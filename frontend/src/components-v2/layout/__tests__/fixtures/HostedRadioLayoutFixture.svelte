<script module lang="ts">
  import { createRawSnippet, type Snippet as FixtureSnippet } from 'svelte';
  import type {
    InstrumentComposition as FixtureInstrumentComposition,
    InstrumentVfoAppearance as FixtureVfoAppearance,
  } from '../../../wiring/instrument-composition';
  import type {
    ReceiverFrequencyMount, ReceiverInstrumentHandles, ReceiverVfoAppearance,
  } from '../../../../semantic/ReceiverInstrumentHost.svelte';

  const empty = createRawSnippet(() => ({ render: () => '' }));
  const vfo = createRawSnippet<[appearance: FixtureVfoAppearance, allowBare?: boolean]>(
    () => ({ render: () => '' }),
  );
  const txAux = createRawSnippet<[scalarLayout: FixtureSnippet, allowBare?: boolean]>(
    () => ({ render: () => '' }),
  );
  const frequency = createRawSnippet<[mount?: ReceiverFrequencyMount]>(() => ({ render: () => '' }));
  const operations = createRawSnippet<[appearance: ReceiverVfoAppearance]>(() => ({ render: () => '' }));
  const receiverInstruments = {
    mainFrequency: frequency, subFrequency: frequency,
    vfoOperations: operations,
  } satisfies ReceiverInstrumentHandles;
  const scalars = {
    rfPower: empty, micGain: empty, driveGain: empty, voxGain: empty,
    antiVoxGain: empty, voxDelay: empty, compressorLevel: empty, monitorLevel: empty,
  };

  export const TEST_INSTRUMENTS = {
    vfo, rxTx: empty, txAuxControls: txAux, txAuxScalars: scalars, receiverInstruments,
    meters: empty, rxAudio: empty, rfFrontEnd: empty, filter: empty, dsp: empty,
    band: empty, antenna: empty, ritXitScan: empty, cwKeyer: empty,
    scopeDisplay: empty, scopeControls: empty, txFaultRecovery: empty,
    modInputTxWarning: empty, managedScope: undefined,
  } satisfies FixtureInstrumentComposition;
</script>

<script lang="ts">
  import type { SkinId } from '../../../../skins/registry';
  import RadioLayout from '../../RadioLayout.svelte';
  import SemanticRadioSurfaces from '../../../wiring/SemanticRadioSurfaces.svelte';
  import type { InstrumentComposition } from '../../../wiring/instrument-composition';

  let { skinId = 'desktop-v2' }: { skinId?: SkinId } = $props();
</script>

<SemanticRadioSurfaces>
  {#snippet children(instruments: InstrumentComposition)}
    <RadioLayout {skinId} {instruments} />
  {/snippet}
</SemanticRadioSurfaces>
