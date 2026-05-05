<script lang="ts">
  import { onMount } from 'svelte';

  interface Props {
    title: string;
    panelId: string;
    collapsible?: boolean;
    draggable?: boolean;
    /** Optional `data-panel` attribute for keyboard focus targets (e.g. rf-frontend). */
    dataPanel?: string;
    /** Style attribute for CSS order (used during drag reorder). */
    style?: string;
    /**
     * When true, the panel is force-collapsed unless the user has manually
     * expanded it during this session. Resets the sticky override each time
     * the condition flips back to false so re-entering a compatible mode
     * restores normal collapse behaviour.
     */
    autoCollapseWhen?: boolean;
    onDragStart?: (panelId: string, event: PointerEvent) => void;
    children?: any;
  }

  let { title, panelId, collapsible = true, draggable = false, dataPanel, style, autoCollapseWhen = false, onDragStart, children }: Props = $props();

  let collapsed = $state(false);
  // Session-only sticky flag: once the user expands while autoCollapseWhen is
  // true, keep the panel open until the condition flips back to false.
  let userExpanded = $state(false);

  $effect(() => {
    if (!autoCollapseWhen) {
      userExpanded = false;
    }
  });

  let effectiveCollapsed = $derived(collapsed || (autoCollapseWhen && !userExpanded));

  const STORAGE_KEY = 'rigplane:panel-collapsed';

  // Load collapsed state from localStorage
  onMount(() => {
    if (!collapsible) {
      return;
    }

    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const data = JSON.parse(stored);
        collapsed = data[panelId] === true;
      }
    } catch (e) {
      // Ignore parsing errors
    }
  });

  function toggle() {
    if (!collapsible) {
      return;
    }

    // When auto-collapse is active and the panel is currently showing as
    // collapsed because of it (not because of persisted state), a click
    // should expand it and mark it as user-expanded for this session.
    if (autoCollapseWhen && effectiveCollapsed && !collapsed) {
      userExpanded = true;
      return;
    }

    collapsed = !collapsed;
    // Collapsing while auto-collapse is active clears the sticky override so
    // the panel stays auto-collapsed until the user expands it again.
    if (collapsed && autoCollapseWhen) {
      userExpanded = false;
    }
    // Expanding (e.g. from a persisted-collapsed state) while auto-collapse
    // is active must also set the sticky override, otherwise the derived
    // ``effectiveCollapsed`` stays true and the panel wouldn't open until a
    // second click.
    if (!collapsed && autoCollapseWhen) {
      userExpanded = true;
    }

    // Save to localStorage
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      const data = stored ? JSON.parse(stored) : {};
      data[panelId] = collapsed;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch (e) {
      // Ignore storage errors
    }
  }

  // --- Swipe gesture tracking ---
  const SWIPE_THRESHOLD = 30;
  let swipeActive = false;
  let swipeStartY = 0;
  let swipeStartX = 0;
  let swipeHandled = false;

  function onHeaderPointerDown(e: PointerEvent) {
    swipeActive = true;
    swipeStartX = e.clientX;
    swipeStartY = e.clientY;
    swipeHandled = false;
  }

  function onHeaderPointerMove(e: PointerEvent) {
    // Only track swipe distance while a pointer is actively held down.
    // Without this gate, plain hover movement reads ``swipeStartY = 0`` and
    // the first frame computes ``dy = e.clientY`` (hundreds of pixels),
    // instantly tripping the swipe-to-collapse threshold on mouse-over.
    if (!swipeActive || !collapsible || swipeHandled) return;
    const dy = e.clientY - swipeStartY;
    const dx = e.clientX - swipeStartX;
    const absDy = Math.abs(dy);
    const absDx = Math.abs(dx);

    // If predominantly vertical movement exceeds threshold, mark as swipe
    if (absDy > SWIPE_THRESHOLD && absDy > absDx * 2) {
      swipeHandled = true;
      // Swipe down → collapse (only if expanded)
      if (dy > 0 && !effectiveCollapsed) {
        toggle();
      }
      // Swipe up → expand (only if collapsed)
      if (dy < 0 && effectiveCollapsed) {
        toggle();
      }
    }
  }

  function onHeaderPointerUp() {
    swipeActive = false;
  }

  function onHeaderPointerCancel() {
    swipeActive = false;
    swipeHandled = false;
  }

  function onHeaderClick(e: MouseEvent) {
    if (swipeHandled) {
      e.preventDefault();
      swipeHandled = false;
      return;
    }
    toggle();
  }
</script>

<div
  class="collapsible-panel"
  data-panel-id={panelId}
  data-panel={dataPanel}
  data-collapsed={effectiveCollapsed}
  {style}
>
  <div class="panel-header-row">
    <button
      type="button"
      class="panel-header"
      class:collapsible
      aria-expanded={!effectiveCollapsed}
      onclick={onHeaderClick}
      onpointerdown={onHeaderPointerDown}
      onpointermove={onHeaderPointerMove}
      onpointerup={onHeaderPointerUp}
      onpointercancel={onHeaderPointerCancel}
      onpointerleave={onHeaderPointerUp}
      disabled={!collapsible}
    >
      {#if collapsible}
        <span class="chevron" aria-hidden="true">{effectiveCollapsed ? '▸' : '▾'}</span>
      {/if}
      <span class="title">{title}</span>
    </button>
    {#if draggable}
      <button
        type="button"
        class="drag-handle"
        aria-label="Drag to reorder"
        onpointerdown={(e) => onDragStart?.(panelId, e)}
      >⠿</button>
    {/if}
  </div>

  <div
    class="panel-content"
    class:collapsed={effectiveCollapsed}
    style:max-height={effectiveCollapsed ? '0' : '2000px'}
  >
    <div class="panel-content-inner">
      {@render children?.()}
    </div>
  </div>
</div>

<style>
  .collapsible-panel {
    display: flex;
    flex-direction: column;
    background: var(--v2-collapsible-bg);
    border: 1px solid var(--v2-collapsible-border);
    border-radius: 4px;
    overflow: hidden;
    font-family: 'Roboto Mono', monospace;
  }

  .panel-header-row {
    display: flex;
    align-items: stretch;
    background: var(--v2-collapsible-header-bg);
    border-bottom: 1px solid var(--v2-collapsible-border);
  }

  .drag-handle {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    padding: 0;
    margin: 0;
    background: none;
    border: none;
    border-right: 1px solid var(--v2-collapsible-border);
    color: var(--v2-collapsible-chevron);
    font-size: 10px;
    cursor: grab;
    touch-action: none;
    user-select: none;
    transition: color 0.15s ease, background 0.15s ease;
  }

  .drag-handle:hover {
    color: var(--v2-collapsible-chevron-hover);
    background: var(--v2-collapsible-header-hover-bg);
  }

  .drag-handle:active {
    cursor: grabbing;
  }

  .panel-header {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
    padding: 5px 8px;
    background: none;
    border: none;
    color: var(--v2-collapsible-header-text);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    text-align: left;
    cursor: default;
    transition: background 0.15s ease;
  }

  .panel-header.collapsible {
    cursor: pointer;
  }

  .panel-header.collapsible:hover {
    background: var(--v2-collapsible-header-hover-bg);
  }

  .chevron {
    display: inline-block;
    color: var(--v2-collapsible-chevron);
    font-size: 10px;
    line-height: 1;
    transition: color 0.15s ease;
    user-select: none;
  }

  .panel-header.collapsible:hover .chevron {
    color: var(--v2-collapsible-chevron-hover);
  }

  .title {
    flex: 1;
  }

  .panel-content {
    overflow: hidden;
    transition: max-height 0.2s ease;
  }

  .panel-content.collapsed {
    max-height: 0 !important;
  }

  .panel-content-inner {
    display: block;
  }
</style>
