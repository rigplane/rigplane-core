<script lang="ts">
  import TxAuxScalarHost from '../../TxAuxScalarHost.svelte';
  import TxAuxSurface, {
    type TxAuxToggleField,
  } from '../../TxAuxSurface.svelte';
  import type {
    TxAuxLevelFeedback,
    TxAuxLevelField,
    TxAuxScalarHandles,
  } from '../../tx-aux-scalar';
  import type { RadioViewModel } from '../../radio-view-model';
  import type { TxAuthoritySnapshot } from '../../rx-tx-surface';

  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    presentation: 'grouped' | 'independent';
    levelFeedback?: TxAuxLevelFeedback;
    onToggle?: (field: TxAuxToggleField) => void;
    onLevelChange?: (field: TxAuxLevelField, value: number) => void;
    onAtuTune?: () => void;
  }

  let {
    view, tx, presentation, levelFeedback,
    onToggle, onLevelChange, onAtuTune,
  }: Props = $props();
</script>

<TxAuxScalarHost {view} {levelFeedback} {onLevelChange}>
  {#snippet children(scalars: TxAuxScalarHandles)}
    {#key presentation}
      {#if presentation === 'grouped'}
        <TxAuxSurface {view} {tx} {onToggle} {onAtuTune} scalarHandles={scalars} />
      {:else}
        <div data-testid="independent-tx-aux-composition">
          <TxAuxSurface
            {view} {tx} {onToggle} {onAtuTune} scalarHandles={scalars} showScalars={false}
          />
          <section data-testid="independent-tx-aux-scalars">
            <div data-slot="vox">{@render scalars.voxGain()}</div>
            <div data-slot="power">{@render scalars.rfPower()}</div>
            <div data-slot="compressor">{@render scalars.compressorLevel()}</div>
            <div data-slot="microphone">{@render scalars.micGain()}</div>
            <div data-slot="drive">{@render scalars.driveGain()}</div>
            <div data-slot="anti-vox">{@render scalars.antiVoxGain()}</div>
            <div data-slot="delay">{@render scalars.voxDelay()}</div>
            <div data-slot="monitor">{@render scalars.monitorLevel()}</div>
          </section>
        </div>
      {/if}
    {/key}
  {/snippet}
</TxAuxScalarHost>
