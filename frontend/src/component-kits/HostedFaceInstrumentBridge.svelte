<script lang="ts">
  import type {
    HostedFaceComponentV1,
    HostedInstrumentFamiliesV1,
    ReceiverFrequencyPresentationV1,
    TxAuxScalarPresentationV1,
  } from '../../component-kit-api/src/index';
  import type { ReceiverInstrumentHandles } from '../semantic/ReceiverInstrumentHost.svelte';
  import type { TxAuxScalarHandles } from '../semantic/tx-aux-scalar';
  import type { VfoOperationHandles } from '../semantic/VfoOperationSeatHost.svelte';

  let {
    component: Face,
    receiverInstruments,
    vfoOperations,
    txAuxScalars,
    receiverAdmitted,
    vfoOperationsAdmitted,
    txAuxAdmitted,
  }: {
    component: HostedFaceComponentV1;
    receiverInstruments: ReceiverInstrumentHandles;
    vfoOperations: VfoOperationHandles;
    txAuxScalars: TxAuxScalarHandles;
    receiverAdmitted: boolean;
    vfoOperationsAdmitted: boolean;
    txAuxAdmitted: boolean;
  } = $props();
  let subReceiverAdmitted = $derived(
    receiverInstruments.subFrequency !== undefined && receiverInstruments.subSMeter !== undefined,
  );
</script>

{#snippet mainFrequency(presentation?: Readonly<ReceiverFrequencyPresentationV1>)}
  {@render receiverInstruments.mainFrequency(presentation)}
{/snippet}
{#snippet subFrequency(presentation?: Readonly<ReceiverFrequencyPresentationV1>)}
  {#if receiverInstruments.subFrequency}
    {@render receiverInstruments.subFrequency(presentation)}
  {/if}
{/snippet}
{#snippet mainSMeter()}
  {@render receiverInstruments.mainSMeter()}
{/snippet}
{#snippet subSMeter()}
  {#if receiverInstruments.subSMeter}
    {@render receiverInstruments.subSMeter()}
  {/if}
{/snippet}

{#snippet rfPower(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.rfPower(presentation)}
{/snippet}
{#snippet micGain(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.micGain(presentation)}
{/snippet}
{#snippet driveGain(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.driveGain(presentation)}
{/snippet}
{#snippet voxGain(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.voxGain(presentation)}
{/snippet}
{#snippet antiVoxGain(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.antiVoxGain(presentation)}
{/snippet}
{#snippet voxDelay(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.voxDelay(presentation)}
{/snippet}
{#snippet compressorLevel(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.compressorLevel(presentation)}
{/snippet}
{#snippet monitorLevel(presentation?: Readonly<TxAuxScalarPresentationV1>)}
  {@render txAuxScalars.monitorLevel(presentation)}
{/snippet}

<Face instruments={{
  receiver: receiverAdmitted ? {
    mainFrequency,
    subFrequency: subReceiverAdmitted ? subFrequency : null,
    mainSMeter,
    subSMeter: subReceiverAdmitted ? subSMeter : null,
  } : null,
  vfoOperations: vfoOperationsAdmitted ? vfoOperations : null,
  txAux: txAuxAdmitted ? {
    rfPower, micGain, driveGain, voxGain, antiVoxGain, voxDelay, compressorLevel, monitorLevel,
  } : null,
} satisfies HostedInstrumentFamiliesV1} />
