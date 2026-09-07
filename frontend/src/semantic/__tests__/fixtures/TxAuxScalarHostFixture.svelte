<script lang="ts">
  import TxAuxFiniteHost from '../../TxAuxFiniteHost.svelte';
  import TxAuxScalarHost from '../../TxAuxScalarHost.svelte';
  import TxAuxSurface, { type TxAuxToggleField } from '../../TxAuxSurface.svelte';
  import type { TxAuxFiniteHandles } from '../../tx-aux-finite';
  import type {
    TxAuxLevelFeedback,
    TxAuxLevelField,
    TxAuxScalarPresentation,
    TxAuxScalarHandles,
  } from '../../tx-aux-scalar';
  import type {
    FiniteControlAppearance, FiniteRendererContext,
  } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { RadioViewModel } from '../../radio-view-model';
  import type { TxAuthoritySnapshot } from '../../rx-tx-surface';

  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    presentation: 'grouped' | 'independent';
    scalarPresentation?: Readonly<TxAuxScalarPresentation>;
    levelFeedback?: TxAuxLevelFeedback;
    finiteAppearance?: FiniteControlAppearance;
    rendererContext?: FiniteRendererContext | null;
    onToggle?: (field: TxAuxToggleField) => void;
    onLevelChange?: (field: TxAuxLevelField, value: number) => void;
    onAtuTune?: () => void;
  }

  let {
    view, tx, presentation, scalarPresentation, levelFeedback, finiteAppearance, rendererContext,
    onToggle, onLevelChange, onAtuTune,
  }: Props = $props();
</script>

<TxAuxScalarHost {view} {levelFeedback} {onLevelChange}>
  {#snippet children(scalars: TxAuxScalarHandles)}
    {#snippet finiteComposition(finite: TxAuxFiniteHandles)}
      {#key presentation}
        {#if presentation === 'grouped'}
          <TxAuxSurface {view} {tx} scalarHandles={scalars} finiteHandles={finite} />
        {:else}
          <section data-testid="independent-tx-aux-composition" aria-label="Transmit auxiliary controls">
            <div class="tx-aux-row" data-testid="independent-tx-aux-finite">
              <div data-slot="monitor">{@render finite.monitor()}</div>
              <div data-slot="atu">{@render finite.atu()}</div>
              <div data-slot="tune">{@render finite.atuTune()}</div>
              <div data-slot="compressor">{@render finite.compressor()}</div>
              <div data-slot="vox">{@render finite.vox()}</div>
            </div>
            <div data-testid="independent-tx-aux-scalars">
              <div data-slot="vox">{@render scalars.voxGain(scalarPresentation)}</div>
              <div data-slot="power">{@render scalars.rfPower(scalarPresentation)}</div>
              <div data-slot="compressor">{@render scalars.compressorLevel(scalarPresentation)}</div>
              <div data-slot="microphone">{@render scalars.micGain(scalarPresentation)}</div>
              <div data-slot="drive">{@render scalars.driveGain(scalarPresentation)}</div>
              <div data-slot="anti-vox">{@render scalars.antiVoxGain(scalarPresentation)}</div>
              <div data-slot="delay">{@render scalars.voxDelay(scalarPresentation)}</div>
              <div data-slot="monitor">{@render scalars.monitorLevel(scalarPresentation)}</div>
            </div>
            <TxAuxSurface
              {view} {tx} scalarHandles={scalars} finiteHandles={finite}
              showScalars={false} showFinite={false}
            />
          </section>
        {/if}
      {/key}
    {/snippet}
    {#if finiteAppearance}
      <TxAuxFiniteHost
        {view} {tx} {onToggle} {onAtuTune}
        {finiteAppearance} rendererContext={rendererContext ?? null}
      >
        {#snippet children(finite: TxAuxFiniteHandles)}{@render finiteComposition(finite)}{/snippet}
      </TxAuxFiniteHost>
    {:else}
      <TxAuxFiniteHost {view} {tx} {onToggle} {onAtuTune}>
        {#snippet children(finite: TxAuxFiniteHandles)}{@render finiteComposition(finite)}{/snippet}
      </TxAuxFiniteHost>
    {/if}
  {/snippet}
</TxAuxScalarHost>
