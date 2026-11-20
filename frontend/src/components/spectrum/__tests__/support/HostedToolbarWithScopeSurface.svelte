<!--
  Test composition (MOR-4605): the HOSTED toolbar exactly as desktop-v2 mounts
  it — the real SpectrumToolbar rendering the real ScopeControlsSurface through
  the `scopeControls` snippet, forwarding the toolbar's 5th argument
  (`refOffset`) the way SemanticRadioSurfaces does. `onState` reports the two
  values the panel hands to the waterfall (AUTO flag and local REF offset).
-->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import SpectrumToolbar from '../../SpectrumToolbar.svelte';
  import ScopeControlsSurface from '../../../../semantic/ScopeControlsSurface.svelte';
  import type { RadioViewModel } from '../../../../semantic/radio-view-model';
  import type { ScopeRefOffset } from '../../spectrum-toolbar-logic';

  let { view, onRefChange, onState }: {
    view: RadioViewModel;
    onRefChange: (ref: number) => void;
    onState: (state: { autoFloor: boolean; autoFloorRef: number }) => void;
  } = $props();

  let autoFloor = $state(false);
  let autoFloorRef = $state(0);
  let brtLevel = $state(0);
  let colorScheme = $state<'classic'>('classic');
  $effect(() => { onState({ autoFloor, autoFloorRef }); });
</script>

{#snippet controls(
  _allowBare?: boolean, screenGroup?: Snippet, rowTail?: Snippet, _moreAsSheet?: boolean,
  refOffset?: ScopeRefOffset,
)}
  <ScopeControlsSurface {view} {onRefChange} {refOffset} moreScreen={screenGroup} {rowTail} />
{/snippet}

<SpectrumToolbar
  hideScopeControls={true}
  scopeControls={controls}
  bind:autoFloor
  bind:autoFloorRef
  bind:brtLevel
  bind:colorScheme
/>
