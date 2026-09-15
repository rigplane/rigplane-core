<script lang="ts">
  import type { HostedFacePropsV1 } from '../../../component-kit-api/src/index';

  let { instruments }: HostedFacePropsV1 = $props();
  const keys = (value: object | null) => value === null ? '' : Object.keys(value).sort().join(',');
</script>

<div
  data-external-face="probe"
  data-top-level-keys={keys(instruments)}
  data-receiver-keys={keys(instruments.receiver)}
  data-vfo-keys={keys(instruments.vfoOperations)}
  data-tx-keys={keys(instruments.txAux)}
>
  {#if instruments.txAux}
    {@const tx = instruments.txAux}
    <div data-probe="form-hbar">{@render tx.rfPower({ form: 'hbar' })}</div>
    <div data-probe="form-knob">{@render tx.micGain({ form: 'knob' })}</div>
    <div data-probe="compact">{@render tx.driveGain({ form: 'hbar', compact: true })}</div>
    <div data-probe="label">{@render tx.voxGain({ form: 'knob', showLabel: false })}</div>
    <div data-probe="value">{@render tx.antiVoxGain({ form: 'knob', showValue: false })}</div>
  {/if}
</div>
