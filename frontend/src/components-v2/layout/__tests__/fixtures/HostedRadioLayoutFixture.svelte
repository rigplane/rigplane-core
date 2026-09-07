<script module lang="ts">
  import { createRawSnippet, type Snippet as FixtureSnippet } from 'svelte';
  import type {
    InstrumentComposition as FixtureInstrumentComposition,
    InstrumentVfoAppearance as FixtureVfoAppearance,
  } from '../../../wiring/instrument-composition';
  import type {
    ReceiverFrequencyMount, ReceiverInstrumentHandles, ReceiverSMeterRenderer,
    ReceiverVfoAppearance,
  } from '../../../../semantic/ReceiverInstrumentHost.svelte';
  import type { TxAuxFiniteHandles } from '../../../../semantic/tx-aux-finite';
  import type { VfoOperationHandles } from '../../../../semantic/VfoOperationSeatHost.svelte';
  import type { BandControlLayout } from '../../../../semantic/band-instruments';
  import type { CwKeyerInstrumentHandles } from '../../../../semantic/CwKeyerInstrumentHost.svelte';
  import type {
    AntennaInstrumentHandles, AntennaInstrumentLayout,
  } from '../../../../semantic/AntennaInstrumentHost.svelte';
  import type { RitXitScanInstrumentHandles } from '../../../../semantic/RitXitScanInstrumentHost.svelte';

  const empty = createRawSnippet(() => ({ render: () => '' }));
  const vfo = createRawSnippet<[
    appearance: FixtureVfoAppearance, allowBare?: boolean, operationControls?: FixtureSnippet,
  ]>(
    () => ({ render: () => '' }),
  );
  const txAux = createRawSnippet<[scalarLayout: FixtureSnippet, allowBare?: boolean]>(
    () => ({ render: () => '' }),
  );
  const band = createRawSnippet<[allowBare?: boolean, controlLayout?: BandControlLayout]>(
    () => ({ render: () => '' }),
  );
  const cwKeyer = createRawSnippet<[allowBare?: boolean, showKeyerSpeed?: boolean]>(
    () => ({ render: () => '' }),
  );
  const cwKeyerInstruments = { keyerSpeed: empty, pitchHz: empty } satisfies CwKeyerInstrumentHandles;
  const antenna = createRawSnippet<[allowBare?: boolean, controlLayout?: FixtureSnippet]>(
    () => ({ render: () => '' }),
  );
  const antennaInstruments = {
    txPort: empty, rxAnt: empty,
  } satisfies AntennaInstrumentHandles;
  const antennaLayout = {
    blockedId: 'fixture-antenna-blocked', blocked: [],
  } satisfies AntennaInstrumentLayout;
  const ritXitInstruments = {
    rit: empty, xit: empty, clear: empty,
  } satisfies RitXitScanInstrumentHandles;
  const frequency = createRawSnippet<[mount?: ReceiverFrequencyMount]>(() => ({ render: () => '' }));
  const meter = createRawSnippet<[renderer?: ReceiverSMeterRenderer]>(() => ({ render: () => '' }));
  const operations = createRawSnippet<[appearance: ReceiverVfoAppearance]>(() => ({ render: () => '' }));
  const receiverInstruments = {
    mainFrequency: frequency, subFrequency: frequency,
    mainSMeter: meter, subSMeter: meter,
    frequencyTunable: () => true,
    vfoOperations: operations,
  } satisfies ReceiverInstrumentHandles;
  const rxAudioInstruments = { afLevel: empty };
  const txAuxInstruments = {
    atu: empty, vox: empty, compressor: empty, monitor: empty, atuTune: empty,
  } satisfies TxAuxFiniteHandles;
  const vfoOperations = {
    split: null, dualWatch: null, activeReceiver: null, equalize: null,
    swap: null, quickSplit: null, quickDualWatch: null, speak: null,
  } satisfies VfoOperationHandles;
  const rfFrontEndInstruments = {
    kind: 'separate', rfGain: empty, squelch: empty,
    preamp: empty, attenuator: empty, digiSel: empty, ipPlus: empty,
  } as const;
  const scalars = {
    rfPower: empty, micGain: empty, driveGain: empty, voxGain: empty,
    antiVoxGain: empty, voxDelay: empty, compressorLevel: empty, monitorLevel: empty,
  };

  export const TEST_INSTRUMENTS = {
    vfo, vfoOperations, rxTx: empty, txAuxControls: txAux,
    txAuxScalars: scalars, txAuxInstruments,
    receiverInstruments,
    rxAudioInstruments, rfFrontEndInstruments,
    meters: empty, rxAudio: empty, rfFrontEnd: empty, filter: empty, dsp: empty,
    band, antenna, antennaInstruments, antennaLayout,
    ritXitScan: empty, ritXitInstruments, cwKeyerInstruments, cwKeyer, memory: empty,
    scopeDisplay: empty, scopeControls: empty, txFaultRecovery: empty,
    modInputTxWarning: empty, managedScope: undefined,
  } satisfies FixtureInstrumentComposition;
</script>

<script lang="ts">
  import type { SkinId } from '../../../../skins/registry';
  import RadioLayout from '../../RadioLayout.svelte';
  import SemanticRadioSurfaces from '../../../wiring/SemanticRadioSurfaces.svelte';
  import type { InstrumentComposition } from '../../../wiring/instrument-composition';

  let {
    skinId = 'desktop-v2', rxAudioLayout = 'production', rfFrontEndLayout = 'production',
  }: {
    skinId?: SkinId; rxAudioLayout?: 'production' | 'grouped' | 'independent';
    rfFrontEndLayout?: 'production' | 'grouped' | 'independent';
  } = $props();
</script>

<SemanticRadioSurfaces>
  {#snippet children(instruments: InstrumentComposition)}
    {#if rfFrontEndLayout === 'grouped'}
      {@render instruments.rfFrontEnd()}
    {:else if rfFrontEndLayout === 'independent'}
      <div data-rf-layout="independent" data-handle-kind={instruments.rfFrontEndInstruments.kind}>
        {#if instruments.rfFrontEndInstruments.kind === 'combined'}
          {@render instruments.rfFrontEndInstruments.rfSql()}
        {:else}
          {@render instruments.rfFrontEndInstruments.rfGain()}
          {@render instruments.rfFrontEndInstruments.squelch()}
        {/if}
      </div>
    {:else if rxAudioLayout === 'production'}
      <RadioLayout {skinId} {instruments} />
    {:else if rxAudioLayout === 'grouped'}
      {@render instruments.rxAudio()}
    {:else}
      <div data-af-layout="independent">{@render instruments.rxAudioInstruments.afLevel()}</div>
    {/if}
  {/snippet}
</SemanticRadioSurfaces>
