<script lang="ts">
  import type { HostedFacePropsV1 } from '@rigplane/component-kit-api';

  let { instruments }: HostedFacePropsV1 = $props();
</script>

<div class="face face-b" data-external-face="b">
  {#if instruments.txAux !== null}
    {@const tx = instruments.txAux}
    <section class="tx-strip" data-family="txAux" aria-label="Transmit controls">
      <div>{@render tx.monitorLevel({ form: 'knob', showLabel: true, showValue: true })}</div>
      <div>{@render tx.compressorLevel({ form: 'hbar', compact: true })}</div>
      <div>{@render tx.voxDelay({ form: 'hbar', showLabel: true })}</div>
      <div>{@render tx.antiVoxGain({ form: 'knob', compact: true })}</div>
      <div>{@render tx.voxGain({ form: 'knob', showValue: true })}</div>
      <div>{@render tx.driveGain({ form: 'hbar', compact: true })}</div>
      <div>{@render tx.micGain({ form: 'hbar', showLabel: true })}</div>
      <div>{@render tx.rfPower({ form: 'knob', compact: true, showValue: true })}</div>
    </section>
  {/if}

  {#if instruments.receiver !== null}
    {@const receiver = instruments.receiver}
    <section class="receiver-stack" data-family="receiver" aria-label="Receiver instruments">
      {#if receiver.subFrequency !== null && receiver.subSMeter !== null}
        <div class="receiver"><div>{@render receiver.subSMeter()}</div><div>{@render receiver.subFrequency({ compact: false })}</div></div>
      {/if}
      <div class="receiver"><div>{@render receiver.mainSMeter()}</div><div>{@render receiver.mainFrequency({ compact: true })}</div></div>
    </section>
  {/if}

  {#if instruments.vfoOperations !== null}
    {@const operations = instruments.vfoOperations}
    <section class="operations" data-family="vfoOperations" aria-label="Radio-wide VFO operations">
      {#if operations.activeReceiver}{@render operations.activeReceiver()}{/if}
      {#if operations.split}{@render operations.split()}{/if}
      {#if operations.equalize}{@render operations.equalize()}{/if}
      {#if operations.quickSplit}{@render operations.quickSplit()}{/if}
      {#if operations.dualWatch}{@render operations.dualWatch()}{/if}
      {#if operations.swap}{@render operations.swap()}{/if}
      {#if operations.quickDualWatch}{@render operations.quickDualWatch()}{/if}
      {#if operations.speak}{@render operations.speak()}{/if}
    </section>
  {/if}
</div>

<style>
  .face { box-sizing: border-box; display: grid; gap: 1.25rem; grid-template-columns: minmax(12rem, 1fr) minmax(18rem, 2fr); inline-size: 100%; min-inline-size: 0; }
  [data-family], .receiver { display: grid; gap: 0.5rem; min-inline-size: 0; }
  .tx-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .receiver { grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); }
  .operations { grid-column: 1 / -1; grid-template-columns: repeat(8, minmax(0, 1fr)); }
  @media (max-width: 44rem) { .face { grid-template-columns: minmax(0, 1fr); } .operations { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
