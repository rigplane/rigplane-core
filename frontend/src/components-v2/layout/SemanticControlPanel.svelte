<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { SemanticSurfaceName } from '../../presentation/layouts/contract';
  import CollapsiblePanel from '../controls/CollapsiblePanel.svelte';

  interface Props {
    surface: SemanticSurfaceName;
    children: Snippet;
    panelId?: string;
    draggable?: boolean;
    onDragStart?: (panelId: string, event: PointerEvent) => void;
    style?: string;
  }

  let {
    surface, children, panelId, draggable = false, onDragStart, style,
  }: Props = $props();
  const generatedPanelId = $props.id();
  let resolvedPanelId = $derived(panelId ?? generatedPanelId);
  const titles: Partial<Record<SemanticSurfaceName, string>> = {
    rfFrontEnd: 'RF FRONT END', filter: 'MODE / FILTER', band: 'BAND',
    antenna: 'ANTENNA', ritXitScan: 'RIT / XIT / SCAN', rxAudio: 'RX AUDIO',
    dsp: 'DSP', cwKeyer: 'CW', rxTx: 'TX', txAux: 'TX CONTROLS', meters: 'STATION METERS',
    memory: 'MEMORY',
  };
</script>

{#if surface === 'vfo'}
  {@render children()}
{:else}
  <div
    class="semantic-control-panel"
    class:desktop-scope-controls={surface === 'scopeControls'}
    class:desktop-scope-status={surface === 'scopeDisplay'}
    class:desktop-station-meters={surface === 'meters'}
    data-control-surface={surface}
    {style}
  >
    {#if titles[surface]}
      <CollapsiblePanel
        title={titles[surface]!} panelId={resolvedPanelId} collapsible={panelId !== undefined}
        {draggable} {onDragStart}
      >
        {@render children()}
      </CollapsiblePanel>
    {:else}
      {@render children()}
    {/if}
  </div>
{/if}
