<script lang="ts">
  import type { SemanticSurfaceName } from '../../../presentation/layouts/contract';
  import type { RadioViewModel } from '../../../semantic/radio-view-model';
  import SemanticControlPanel from '../SemanticControlPanel.svelte';
  import RfFrontEndSurface from '../../../semantic/RfFrontEndSurface.svelte';
  import ScopeControlsSurface from '../../../semantic/ScopeControlsSurface.svelte';
  import ScopeDisplaySurface from '../../../semantic/ScopeDisplaySurface.svelte';
  import '../../../skins/desktop-v2/semantic-controls.css';

  let { surface, view, pendingPreamp = null, onPreampChange, onLevelChange }: {
    surface: SemanticSurfaceName;
    view: RadioViewModel;
    pendingPreamp?: number | null;
    onPreampChange?: (value: number) => void;
    onLevelChange?: (field: 'rfGain' | 'squelch', value: number) => void;
  } = $props();
</script>

<div class="desktop-control-face">
  <SemanticControlPanel {surface}>
    {#if surface === 'rfFrontEnd'}
      <RfFrontEndSurface {view} {pendingPreamp} {onPreampChange} {onLevelChange} />
    {:else if surface === 'scopeControls'}
      <ScopeControlsSurface {view} />
    {:else if surface === 'scopeDisplay'}
      <ScopeDisplaySurface {view} />
    {:else}
      <button type="button" data-testid="child">Child</button>
    {/if}
  </SemanticControlPanel>
</div>
