<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { FiniteControlAppearance, FiniteRendererContext } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import RitXitScanInstrumentHost, {
    type RitXitScanInstrumentHandles, type RitXitScanInstrumentLayout,
  } from '../../RitXitScanInstrumentHost.svelte';
  import RitXitScanSurface from '../../RitXitScanSurface.svelte';
  import type { ControlDomain } from '$lib/types/capabilities';
  import type { RadioViewModel } from '../../radio-view-model';

  interface Props {
    view: RadioViewModel;
    presentation?: 'grouped' | 'independent';
    finiteAppearance?: FiniteControlAppearance<string | number>;
    rendererContext?: FiniteRendererContext | null;
    ritDomain?: ControlDomain | null;
    onRitToggle?: () => void;
    onXitToggle?: () => void;
    onRitOffsetChange?: (hz: number) => void;
    onXitOffsetChange?: (hz: number) => void;
    onClear?: () => void;
    onScanStart?: (type: number) => void;
    onScanStop?: () => void;
    onResumeModeChange?: (mode: number) => void;
  }
  let {
    view, presentation = 'grouped', finiteAppearance, rendererContext = null, ritDomain,
    onRitToggle, onXitToggle, onRitOffsetChange, onXitOffsetChange, onClear,
    onScanStart, onScanStop, onResumeModeChange,
  }: Props = $props();
  let selection = $derived(finiteAppearance === undefined ? {} : { finiteAppearance, rendererContext });
</script>

{#snippet independent(handles: RitXitScanInstrumentHandles, offsetSlot: Snippet)}
  <div data-slot="rit">{@render handles.rit()}</div>
  <div data-slot="xit">{@render handles.xit()}</div>
  <div data-slot="offset">{@render offsetSlot()}</div>
  <div data-slot="clear">{@render handles.clear()}</div>
{/snippet}

<RitXitScanInstrumentHost {view} {onRitToggle} {onXitToggle} {onClear} {...selection}>
  {#snippet children(handles: RitXitScanInstrumentHandles)}
    <RitXitScanSurface
      {view} {handles}
      instrumentLayout={presentation === 'independent' ? independent as RitXitScanInstrumentLayout : undefined}
      {ritDomain} {onRitOffsetChange} {onXitOffsetChange}
      {onScanStart} {onScanStop} {onResumeModeChange}
    />
  {/snippet}
</RitXitScanInstrumentHost>
