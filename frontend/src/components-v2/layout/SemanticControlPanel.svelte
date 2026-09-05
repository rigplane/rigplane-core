<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { SemanticSurfaceName } from '../../presentation/layouts/contract';
  import CollapsiblePanel from '../controls/CollapsiblePanel.svelte';

  let { surface, children }: { surface: SemanticSurfaceName; children: Snippet } = $props();
  const panelId = $props.id();
  const titles: Partial<Record<SemanticSurfaceName, string>> = {
    rfFrontEnd: 'RF FRONT END', filter: 'MODE / FILTER', band: 'BAND',
    antenna: 'ANTENNA', ritXitScan: 'RIT / XIT / SCAN', rxAudio: 'RX AUDIO',
    dsp: 'DSP', cwKeyer: 'CW', rxTx: 'TX', txAux: 'TX CONTROLS', meters: 'STATION METERS',
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
  >
    {#if titles[surface]}
      <CollapsiblePanel title={titles[surface]!} {panelId} collapsible={false}>
        {@render children()}
      </CollapsiblePanel>
    {:else}
      {@render children()}
    {/if}
  </div>
{/if}
