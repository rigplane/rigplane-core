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
  import type { SemanticSurfaceName } from '../../presentation/layouts/contract';

  /** MOR-1065: mirrors RightSidebar. The TX panel is not in this sidebar's
   *  defaults, but a cross-sidebar drag can move it here, so the semantic
   *  layout's TX suppression has to hold on both sides.
   *
   *  MOR-1364 (v3-rework S6-pre) — `declared` is the ONE legacy-twin
   *  suppression channel: the ACTIVE layout manifest's declared-surface set
   *  (`declaredSurfaces(getLayout(skinId))`, derived once in RadioLayout).
   *  Every panel below whose semantic twin is mounted by a declared zone
   *  retires; a surface no zone declares keeps its legacy panel untouched, so
   *  the default empty set is a full no-op. Deliberately the MANIFEST and
   *  never the resolved SurfacePlan (S5 ruling): a workspace SUBTRACTION must
   *  cost the operator the zone, never resurrect the legacy panel through the
   *  back door.
   *
   *  Safe ONLY because MOR-1336's `zoned()` degrades to a BARE render for an
   *  unzoned surface (S5-N3) — a future change making `zoned` withhold its
   *  body instead would turn each suppression below into a readout-losing bug.
   *
   *  `hideTxPanel` stays a SEPARATE boolean and is deliberately NOT folded
   *  into this set (R9, MOR-1313): it follows the semantic DECK, not
   *  `declared.has('rxTx')`. Do not "tidy" the two into one prop. */
  let {
    hideTxPanel = false,
    declared = new Set<SemanticSurfaceName>(),
  }: { hideTxPanel?: boolean; declared?: ReadonlySet<SemanticSurfaceName> } = $props();

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
  {#if drag.order.includes('rf-front-end') && !declared.has('rfFrontEnd')}
    <CollapsiblePanel title="RF FRONT END" panelId="rf-front-end" dataPanel="rf-frontend"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('rf-front-end')}>
      <RfFrontEnd />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('mode') && !declared.has('filter')}
    <CollapsiblePanel title="MODE" panelId="mode"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('mode')}>
      <ModePanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('filter') && !declared.has('filter')}
    <CollapsiblePanel title="FILTER" panelId="filter"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('filter')}>
      <FilterPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('agc') && !declared.has('dsp')}
    <CollapsiblePanel title="AGC" panelId="agc"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('agc')}>
      <AgcPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('rit-xit') && !declared.has('ritXitScan')}
    <CollapsiblePanel title="RIT / XIT" panelId="rit-xit"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('rit-xit')}>
      <RitXitPanel />
    </CollapsiblePanel>
  {/if}

  <!-- MOR-1364: the BAND twin is deliberately NOT on this channel. `BandSelector`
       hosts three tabs (HAM / LW-MW / SWL) and 17 broadcast presets; only the HAM
       half is duplicated by `BandSurface`, and the broadcast presets are
       deliberately NOT facts (`semantic/radio-view-model.ts:494-496`) and have no
       other production host. Gating on `declared.has('band')` would orphan them.
       Joins the channel in S8, after the component split (`hamBands` prop). -->
  {#if drag.order.includes('band')}
    <CollapsiblePanel title="BAND" panelId="band"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('band')}>
      <BandSelector />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('antenna') && (caps?.antennas ?? 1) > 1 && !declared.has('antenna')}
    <CollapsiblePanel title="ANTENNA" panelId="antenna" dataPanel="antenna"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('antenna')}>
      <AntennaPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('scan') && !declared.has('ritXitScan')}
    <CollapsiblePanel title="SCAN" panelId="scan"
      draggable={true} onDragStart={drag.handleDragStart}
      style={drag.dragStyle('scan')}>
      <ScanPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('rx-audio') && !declared.has('rxAudio')}
    <CollapsiblePanel title="RX AUDIO" panelId="rx-audio" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('rx-audio')}>
      <RxAudioPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('dsp') && !declared.has('dsp')}
    <CollapsiblePanel title="DSP" panelId="dsp" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('dsp')}>
      <DspPanel />
    </CollapsiblePanel>
  {/if}

  {#if !hideTxPanel && drag.order.includes('tx')}
    <CollapsiblePanel title="TX" panelId="tx" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('tx')}>
      <TxPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('cw') && hasCapability('cw') && !declared.has('cwKeyer')}
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
