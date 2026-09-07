<script lang="ts">
  import type { FiniteControlAppearance, FiniteRendererContext } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import DspInstrumentHost from '../../DspInstrumentHost.svelte';
  import DspSurface, { type DspLevelField } from '../../DspSurface.svelte';
  import type { DspFiniteChoiceValue, DspFiniteHandles,
    DspNotchMode, DspToggleField } from '../../dsp-instruments';
  import type { RadioViewModel } from '../../radio-view-model';

  interface Props {
    view: RadioViewModel;
    presentation?: 'grouped' | 'independent';
    agcLabels?: Record<string, string>;
    nbLevelMax?: number;
    nbLevelPercent?: boolean;
    pendingNb?: boolean | null;
    pendingNr?: boolean | null;
    finiteAppearance?: FiniteControlAppearance<DspFiniteChoiceValue>;
    rendererContext?: FiniteRendererContext | null;
    onToggle?: (field: DspToggleField, next: boolean) => void;
    onLevelChange?: (field: DspLevelField, value: number) => void;
    onNotchModeChange?: (mode: DspNotchMode) => void;
    onAgcModeChange?: (mode: number) => void;
  }
  let {
    view, presentation = 'grouped', agcLabels = {}, nbLevelMax = 255,
    nbLevelPercent = false, pendingNb = null, pendingNr = null,
    finiteAppearance, rendererContext = null,
    onToggle, onLevelChange, onNotchModeChange, onAgcModeChange,
  }: Props = $props();
  let selection = $derived(finiteAppearance === undefined
    ? {} : { finiteAppearance, rendererContext });
</script>

<DspInstrumentHost
  {view} {agcLabels} {pendingNb} {pendingNr} {onToggle}
  {onNotchModeChange} {onAgcModeChange} {...selection}
>
  {#snippet children(handles: DspFiniteHandles)}
    {#key presentation}
      {#if presentation === 'grouped'}
        <DspSurface {view} finiteHandles={handles} {nbLevelMax} {nbLevelPercent} {onLevelChange} />
      {:else}
        <section data-testid="independent-dsp-composition">
          <div data-slot="toggles">{@render handles.nrActive()}{@render handles.nbActive()}</div>
          <div data-slot="notch">{@render handles.notchMode()}</div>
          <div data-slot="agc">{@render handles.agcMode()}</div>
        </section>
      {/if}
    {/key}
  {/snippet}
</DspInstrumentHost>
