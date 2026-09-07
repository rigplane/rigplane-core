<script lang="ts">
  import type {
    HostedFaceComponentV1,
    HostedInstrumentFamiliesV1,
    MeterAppearance,
    ReceiverFrequencyPresentationV1,
    TxAuxScalarPresentationV1,
  } from '../../component-kit-api/src/index';
  import type { SignalMeterProjection } from '../components-v2/meters/smeter-scale';
  import type { ActionRendererSeat } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { MeterRfState } from '../semantic/radio-view-model';
  import type { ReceiverInstrumentHandles } from '../semantic/ReceiverInstrumentHost.svelte';
  import type {
    StationLevelMeterFrame,
    StationMeterInstrumentHandles,
    StationSignalFacts,
    StationSignalMeterFrame,
  } from '../semantic/StationMeterInstrumentHost.svelte';
  import type { TxAuxScalarHandles } from '../semantic/tx-aux-scalar';
  import type { VfoOperationHandles } from '../semantic/VfoOperationSeatHost.svelte';
  import MeterRendererSeat from './MeterRendererSeat.svelte';

  let {
    component: Face,
    receiverInstruments,
    vfoOperations,
    txAuxScalars,
    stationMeters,
    meterAppearance,
    receiverAdmitted,
    vfoOperationsAdmitted,
    txAuxAdmitted,
    stationMetersAdmitted,
  }: {
    component: HostedFaceComponentV1;
    receiverInstruments: ReceiverInstrumentHandles;
    vfoOperations: VfoOperationHandles;
    txAuxScalars: TxAuxScalarHandles;
    stationMeters: StationMeterInstrumentHandles;
    meterAppearance: MeterAppearance;
    receiverAdmitted: boolean;
    vfoOperationsAdmitted: boolean;
    txAuxAdmitted: boolean;
    stationMetersAdmitted: boolean;
  } = $props();
  let subReceiverAdmitted = $derived(
    receiverInstruments.subFrequency !== undefined && receiverInstruments.subSMeter !== undefined,
  );
  /** Readable now AND actually read — same two-part test `MetersSurface` applies. */
  const observed = (facts: StationSignalFacts): boolean =>
    facts.availability.operational && facts.reading.status === 'known';
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

<!-- An external Face gets no native DOM meter: absent an installed meter
     appearance these seats render nothing, the way `mainSMeter` does. -->
{#snippet noNativeLevel(_frame: StationLevelMeterFrame, _resetPeakSeat?: ActionRendererSeat)}{/snippet}
{#snippet stationLevel(frame: StationLevelMeterFrame, resetPeakSeat?: ActionRendererSeat)}
  {#key resetPeakSeat}
    <MeterRendererSeat kind="level" {frame} {resetPeakSeat}
      levelRenderer={meterAppearance.level} fallback={noNativeLevel} />
  {/key}
{/snippet}
{#snippet stationSignalMeter(
  signalFrame: StationSignalMeterFrame | null,
  _signalProjection: SignalMeterProjection | null,
  _swrFrame: StationLevelMeterFrame<'swr'> | null,
  _rfState: MeterRfState,
  _presentGroup: boolean,
)}
  {#if signalFrame !== null}
    {@const facts = signalFrame.field}
    {@const reading = observed(facts) && facts.reading.status === 'known'
      && Number.isFinite(facts.reading.value) ? facts.reading : { status: 'unknown' } as const}
    <!-- The host's signal continuation runs whenever signal OR swr is
         structural, so `signalFrame !== null` does not mean this radio has an
         S meter. `selectedPresent` carries that structural fact, and
         `MeterRendererSeat` renders no signal appearance when it is false. -->
    <MeterRendererSeat frame={signalFrame.motion} {reading} domain={facts.domain}
      relevant={facts.relevant} selectedPresent={facts.availability.structural}
      signalRenderer={meterAppearance.signal} />
  {/if}
{/snippet}
{#snippet stationSignal()}{@render stationMeters.signal(stationSignalMeter)}{/snippet}
{#snippet stationPower()}{@render stationMeters.power(stationLevel)}{/snippet}
{#snippet stationSwr()}{@render stationMeters.swr(stationLevel)}{/snippet}
{#snippet stationAlc()}{@render stationMeters.alc(stationLevel)}{/snippet}
{#snippet stationDrainCurrent()}{@render stationMeters.drainCurrent(stationLevel)}{/snippet}
{#snippet stationDrainVoltage()}{@render stationMeters.drainVoltage(stationLevel)}{/snippet}
{#snippet stationCompression()}{@render stationMeters.compression(stationLevel)}{/snippet}

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
  stationMeters: stationMetersAdmitted ? {
    signal: stationSignal,
    power: stationPower,
    swr: stationSwr,
    alc: stationAlc,
    drainCurrent: stationDrainCurrent,
    drainVoltage: stationDrainVoltage,
    compression: stationCompression,
  } : null,
} satisfies HostedInstrumentFamiliesV1} />
