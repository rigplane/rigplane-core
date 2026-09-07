<script lang="ts">
  import type { HostedFacePropsV1 } from '@rigplane/component-kit-api';

  let { instruments }: HostedFacePropsV1 = $props();
</script>

<div class="face face-a" data-external-face="a">
  {#if instruments.receiver !== null}
    {@const receiver = instruments.receiver}
    <section data-family="receiver" aria-label="Receiver instruments">
      <div class="receiver"><div>{@render receiver.mainFrequency({ compact: false })}</div><div>{@render receiver.mainSMeter()}</div></div>
      {#if receiver.subFrequency !== null && receiver.subSMeter !== null}
        <div class="receiver"><div>{@render receiver.subFrequency({ compact: true })}</div><div>{@render receiver.subSMeter()}</div></div>
      {/if}
    </section>
  {/if}

  {#if instruments.vfoOperations !== null}
    {@const operations = instruments.vfoOperations}
    <section class="operations" data-family="vfoOperations" aria-label="Radio-wide VFO operations">
      {#if operations.split}{@render operations.split()}{/if}
      {#if operations.dualWatch}{@render operations.dualWatch()}{/if}
      {#if operations.activeReceiver}{@render operations.activeReceiver()}{/if}
      {#if operations.equalize}{@render operations.equalize()}{/if}
      {#if operations.swap}{@render operations.swap()}{/if}
      {#if operations.quickSplit}{@render operations.quickSplit()}{/if}
      {#if operations.quickDualWatch}{@render operations.quickDualWatch()}{/if}
      {#if operations.speak}{@render operations.speak()}{/if}
    </section>
  {/if}

  {#if instruments.txAux !== null}
    {@const tx = instruments.txAux}
    <section class="tx-grid" data-family="txAux" aria-label="Transmit controls">
      <div class="wide">{@render tx.rfPower({ form: 'hbar', showLabel: true, showValue: true })}</div>
      <div>{@render tx.micGain({ form: 'knob', compact: true, showLabel: true })}</div>
      <div>{@render tx.driveGain({ form: 'knob', showValue: true })}</div>
      <div>{@render tx.voxGain({ form: 'hbar', compact: true })}</div>
      <div>{@render tx.antiVoxGain({ form: 'hbar', showLabel: true })}</div>
      <div>{@render tx.voxDelay({ form: 'knob', compact: true })}</div>
      <div>{@render tx.compressorLevel({ form: 'knob', showLabel: true })}</div>
      <div class="wide">{@render tx.monitorLevel({ form: 'hbar', showValue: true })}</div>
    </section>
  {/if}
</div>

<style>
  .face { box-sizing: border-box; display: grid; gap: 1rem; inline-size: 100%; min-inline-size: 0; }
  [data-family], .receiver, .tx-grid { display: grid; gap: 0.5rem; min-inline-size: 0; }
  .receiver { grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); }
  .operations { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .tx-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .wide { grid-column: span 2; }
  @media (max-width: 44rem) { .operations, .tx-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
