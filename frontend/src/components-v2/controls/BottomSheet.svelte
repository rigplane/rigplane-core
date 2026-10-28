<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    open: boolean;
    title: string;
    compact?: boolean;
    contentStyle?: string;
    children: Snippet;
    onclose?: () => void;
  }

  let {
    open = $bindable(),
    title,
    compact = false,
    contentStyle = '',
    children,
    onclose,
  }: Props = $props();

  let sheetEl: HTMLDivElement | undefined = $state();
  let dragOffsetY = $state(0);
  let isDragging = $state(false);
  let isAnimatingOut = $state(false);

  // Pointer tracking state (not reactive — raw tracking)
  let startY = 0;
  let startTime = 0;
  let startedOnHandle = false;
  let dragActive = false;

  const DISMISS_THRESHOLD = 0.3; // 30% of sheet height
  const VELOCITY_THRESHOLD = 0.5; // px/ms

  function dismiss() {
    isAnimatingOut = true;
    // Animate off-screen
    if (sheetEl) {
      dragOffsetY = sheetEl.offsetHeight;
    }
    setTimeout(() => {
      open = false;
      isAnimatingOut = false;
      dragOffsetY = 0;
      isDragging = false;
      dragActive = false;
      onclose?.();
    }, 250);
  }

  function onBackdropClick() {
    dismiss();
  }

  function onSheetClick(e: MouseEvent) {
    e.stopPropagation();
  }

  function onPointerDown(e: PointerEvent) {
    if (!sheetEl) return;

    const target = e.target as HTMLElement;
    startedOnHandle = target.closest('.m-sheet-handle') !== null;

    // Only track if started on handle or content is scrolled to top
    const contentEl = sheetEl.querySelector('.m-sheet-content');
    const scrolledToTop = !contentEl || contentEl.scrollTop <= 0;

    if (!startedOnHandle && !scrolledToTop) return;

    startY = e.clientY;
    startTime = e.timeStamp;
    dragActive = false;
    isDragging = false;

    sheetEl.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: PointerEvent) {
    if (!sheetEl || startY === 0) return;

    const dy = e.clientY - startY;

    // Only start dragging downward
    if (!dragActive) {
      if (dy < 5) return; // ignore upward or tiny movements

      // If not started on handle, re-check scroll position
      if (!startedOnHandle) {
        const contentEl = sheetEl.querySelector('.m-sheet-content');
        if (contentEl && contentEl.scrollTop > 0) {
          startY = 0;
          return;
        }
      }

      dragActive = true;
      isDragging = true;
    }

    // Apply resistance: full 1:1 tracking
    dragOffsetY = Math.max(0, dy);
  }

  function onPointerUp(e: PointerEvent) {
    if (!sheetEl || !dragActive) {
      startY = 0;
      return;
    }

    const dy = e.clientY - startY;
    const elapsed = e.timeStamp - startTime;
    const velocity = elapsed > 0 ? dy / elapsed : 0;
    const sheetHeight = sheetEl.offsetHeight;
    const ratio = dy / sheetHeight;

    if (ratio > DISMISS_THRESHOLD || velocity > VELOCITY_THRESHOLD) {
      dismiss();
    } else {
      // Snap back
      isDragging = false;
      dragActive = false;
      dragOffsetY = 0;
    }

    startY = 0;
  }

  function onPointerCancel(_e: PointerEvent) {
    isDragging = false;
    dragActive = false;
    dragOffsetY = 0;
    startY = 0;
  }

  // Reset state when sheet closes externally
  $effect(() => {
    if (!open) {
      dragOffsetY = 0;
      isDragging = false;
      dragActive = false;
      isAnimatingOut = false;
      startY = 0;
    }
  });
</script>

{#if open || isAnimatingOut}
  <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events a11y_interactive_supports_focus -->
  <div
    class="m-sheet-backdrop"
    onclick={onBackdropClick}
    onkeydown={(e) => { if (e.key === 'Escape') dismiss(); }}
    role="dialog"
    aria-modal="true"
    aria-label={title}
    tabindex="-1"
  >
    <div
      bind:this={sheetEl}
      class="m-sheet"
      class:m-sheet--compact={compact}
      class:dragging={isDragging}
      class:animating={isAnimatingOut || (!isDragging && dragOffsetY === 0)}
      style:transform="translateY({dragOffsetY}px)"
      role="presentation"
      onclick={onSheetClick}
      onpointerdown={onPointerDown}
      onpointermove={onPointerMove}
      onpointerup={onPointerUp}
      onpointercancel={onPointerCancel}
    >
      <div class="m-sheet-handle"></div>
      <div class="m-sheet-title">{title}</div>
      <div class="m-sheet-content" style={contentStyle}>
        {@render children()}
      </div>
    </div>
  </div>
{/if}

<style>
  .m-sheet-backdrop {
    position: fixed;
    inset: 0;
    z-index: 200;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(2px);
  }

  .m-sheet {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    max-height: 80vh;
    background: var(--v2-bg-primary, #0f0f1a);
    border-top: 1px solid var(--v2-border-panel, #333);
    border-radius: 16px 16px 0 0;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    z-index: 201;
    padding-bottom: env(safe-area-inset-bottom, 0px);
    touch-action: none;
    will-change: transform;
  }

  .m-sheet.animating {
    transition: transform 0.25s ease-out;
  }

  .m-sheet.dragging {
    transition: none;
    overflow-y: hidden;
  }

  .m-sheet--compact {
    max-height: 50vh;
  }

  .m-sheet-handle {
    width: 36px;
    height: 4px;
    background: var(--v2-text-dim, #444);
    border-radius: 2px;
    margin: 10px auto 6px;
    cursor: grab;
  }

  .m-sheet-title {
    text-align: center;
    font-family: 'Roboto Mono', monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: var(--v2-text-secondary, #aaa);
    padding: 0 0 8px;
    border-bottom: 1px solid var(--v2-border-darker, #222);
  }

  .m-sheet-content {
    display: flex;
    flex-direction: column;
    gap: 0;
    padding: 4px 0;
  }
</style>
