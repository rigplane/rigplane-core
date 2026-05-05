<script lang="ts">
  import { runtime } from '$lib/runtime';
  import { hasCapability } from '$lib/stores/capabilities.svelte';
  import RfFrontEnd from '../panels/RfFrontEnd.svelte';
  import ModePanel from '../panels/ModePanel.svelte';
  import FilterPanel from '../panels/FilterPanel.svelte';
  import AgcPanel from '../panels/AgcPanel.svelte';
  import RitXitPanel from '../panels/RitXitPanel.svelte';
  import AntennaPanel from '../panels/AntennaPanel.svelte';
  import ScanPanel from '../panels/ScanPanel.svelte';
  import BandSelector from '../controls/BandSelector.svelte';
  import RxAudioPanel from '../panels/RxAudioPanel.svelte';
  import DspPanel from '../panels/DspPanel.svelte';
  import TxPanel from '../panels/TxPanel.svelte';
  import CwPanel from '../panels/CwPanel.svelte';
  import MemoryPanel from '../panels/MemoryPanel.svelte';
  import CollapsiblePanel from '../controls/CollapsiblePanel.svelte';
  import { createDragReorder } from '$lib/drag-reorder.svelte';

  // Reactive state + capabilities — via runtime
  let caps = $derived(runtime.caps);

  // --- Panel reorder (shared logic) ---
  const drag = createDragReorder({
    storageKey: 'rigplane:panel-order',
    defaults: ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band', 'antenna', 'scan'],
    containerSelector: '.left-sidebar',
  });
</script>

<aside class="left-sidebar" class:cross-drop-target={drag.isDropTarget}>
  {#if drag.order.includes('rf-front-end')}
    <CollapsiblePanel title="RF FRONT END" panelId="rf-front-end" dataPanel="rf-frontend"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('rf-front-end')}>
      <RfFrontEnd />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('mode')}
    <CollapsiblePanel title="MODE" panelId="mode"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('mode')}>
      <ModePanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('filter')}
    <CollapsiblePanel title="FILTER" panelId="filter"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('filter')}>
      <FilterPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('agc')}
    <CollapsiblePanel title="AGC" panelId="agc"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('agc')}>
      <AgcPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('rit-xit')}
    <CollapsiblePanel title="RIT / XIT" panelId="rit-xit"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('rit-xit')}>
      <RitXitPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('band')}
    <CollapsiblePanel title="BAND" panelId="band"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('band')}>
      <BandSelector />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('antenna') && (caps?.antennas ?? 1) > 1}
    <CollapsiblePanel title="ANTENNA" panelId="antenna" dataPanel="antenna"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('antenna')}>
      <AntennaPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('scan')}
    <CollapsiblePanel title="SCAN" panelId="scan"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('scan')}>
      <ScanPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('rx-audio')}
    <CollapsiblePanel title="RX AUDIO" panelId="rx-audio" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('rx-audio')}>
      <RxAudioPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('dsp')}
    <CollapsiblePanel title="DSP" panelId="dsp" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('dsp')}>
      <DspPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('tx')}
    <CollapsiblePanel title="TX" panelId="tx" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('tx')}>
      <TxPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('cw') && hasCapability('cw')}
    <CollapsiblePanel title="CW" panelId="cw" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('cw')}>
      <CwPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('memory')}
    <CollapsiblePanel title="MEMORY" panelId="memory" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('memory')}>
      <MemoryPanel />
    </CollapsiblePanel>
  {/if}

  <div class="sidebar-footer" style="order:99">
    <button type="button" class="reset-order-btn" onclick={drag.resetAll}>
      Reset panel order
    </button>
  </div>
</aside>

<style>
  .left-sidebar {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
    padding: 6px 6px 16px;
    width: 100%;
    box-sizing: border-box;
  }

  .left-sidebar.cross-drop-target {
    outline: 2px solid var(--v2-accent, #4af);
    outline-offset: -2px;
  }

  .sidebar-footer {
    display: flex;
    justify-content: center;
    padding-top: 4px;
  }

  .reset-order-btn {
    background: none;
    border: 1px solid var(--v2-collapsible-border, #444);
    color: var(--v2-collapsible-chevron, #888);
    font-family: 'Roboto Mono', monospace;
    font-size: 9px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    padding: 3px 10px;
    border-radius: 3px;
    cursor: pointer;
    transition: color 0.15s ease, border-color 0.15s ease;
  }

  .reset-order-btn:hover {
    color: var(--v2-collapsible-header-text, #ccc);
    border-color: var(--v2-collapsible-header-text, #ccc);
  }
</style>
