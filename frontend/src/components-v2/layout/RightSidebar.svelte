<script lang="ts">
  import { hasCapability, hasAudioFft } from '$lib/stores/capabilities.svelte';
  import RxAudioPanel from '../panels/RxAudioPanel.svelte';
  import DspPanel from '../panels/DspPanel.svelte';
  import TxPanel from '../panels/TxPanel.svelte';
  import CwPanel from '../panels/CwPanel.svelte';
  import MemoryPanel from '../panels/MemoryPanel.svelte';
  import AudioSpectrumPanel from '../panels/audio-scope/AudioSpectrumPanel.svelte';
  import CollapsiblePanel from '../controls/CollapsiblePanel.svelte';
  import { createDragReorder } from '$lib/drag-reorder.svelte';

  type RightSidebarMode = 'all' | 'rx' | 'tx';

  interface Props {
    mode?: RightSidebarMode;
  }

  let { mode = 'all' }: Props = $props();

  let showRx = $derived(mode === 'all' || mode === 'rx');
  let showTx = $derived(mode === 'all' || mode === 'tx');

  // --- Panel reorder (shared logic) ---
  const drag = createDragReorder({
    storageKey: 'rigplane:right-panel-order',
    defaults: ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory'],
    containerSelector: '.right-sidebar',
  });
</script>

<aside class="right-sidebar" class:cross-drop-target={drag.isDropTarget}>
  {#if showRx && drag.order.includes('rx-audio')}
    <CollapsiblePanel title="RX AUDIO" panelId="rx-audio" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('rx-audio')}>
      <RxAudioPanel />
    </CollapsiblePanel>
  {/if}

  {#if showRx && drag.order.includes('audio-scope') && hasAudioFft()}
    <CollapsiblePanel title="AUDIO SCOPE" panelId="audio-scope" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('audio-scope')}>
      <AudioSpectrumPanel />
    </CollapsiblePanel>
  {/if}

  {#if showRx && drag.order.includes('dsp')}
    <CollapsiblePanel title="DSP" panelId="dsp" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('dsp')}>
      <DspPanel />
    </CollapsiblePanel>
  {/if}

  {#if showTx && drag.order.includes('tx')}
    <CollapsiblePanel title="TX" panelId="tx" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('tx')}>
      <TxPanel />
    </CollapsiblePanel>
  {/if}

  {#if showTx && drag.order.includes('cw') && hasCapability('cw')}
    <CollapsiblePanel title="CW" panelId="cw" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('cw')}>
      <CwPanel />
    </CollapsiblePanel>
  {/if}

  {#if drag.order.includes('memory')}
    <CollapsiblePanel title="MEMORY" panelId="memory" draggable onDragStart={drag.handleDragStart} style={drag.dragStyle('memory')}>
      <MemoryPanel />
    </CollapsiblePanel>
  {/if}

</aside>

<style>
  .right-sidebar {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
    padding: 6px 6px 16px;
    width: 100%;
    box-sizing: border-box;
  }

  .right-sidebar.cross-drop-target {
    outline: 2px solid var(--v2-accent, #4af);
    outline-offset: -2px;
  }
</style>
