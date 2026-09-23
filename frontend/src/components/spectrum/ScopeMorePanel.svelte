<!--
  "More" panel for the scope row (MOR-2545 PR1) — an anchored group panel
  that opens under the ⋯ key, over the panorama.

  Positioning (review rounds 3–4): plain `position: fixed` from the ⋯ key's
  rect, recomputed on window `resize` while open and clamped to the viewport
  with an 8 px margin; any scroll while open closes it (one capture-phase
  listener). Round 4, measured in Chromium: inside the container-type surface
  and the LCD column, `fixed` places in viewport coordinates — no top layer.

  Close semantics, per the ticket: outside click lands on a fixed transparent
  backdrop (z-index 999, the existing popover scheme —
  `ScopeSettingsPopover.svelte`) that swallows it, so a dismiss never retunes
  the panorama underneath; Escape is a WINDOW-level keydown handler that
  exists only while MOUNTED (idempotent — closed means no handler at all, so
  it never steals Esc from the MOR-2514 frequency-digit release); focus moves
  INTO the panel on open and back to `anchor` (the ⋯ key) on close. PR1
  mounts only the RADIO-HELD group.
-->
<script lang="ts">
  import { onMount, type Snippet } from 'svelte';

  interface Props {
    onClose: () => void;
    /** The ⋯ key that opened the panel: position derives from its rect; focus returns to it on close. */
    anchor?: HTMLElement | null;
    /** Radio-held controls group (PR1). */
    radioHeld?: Snippet;
  }
  let { onClose, anchor, radioHeld }: Props = $props();

  let panel: HTMLElement | undefined = $state();

  /** Leftward from the key's right edge, shifted right at the left margin, never past the right one. */
  function place() {
    if (!panel || !anchor) return;
    const rect = anchor.getBoundingClientRect();
    const { offsetWidth: w, offsetHeight: h } = panel;
    const left = Math.max(8, Math.min(rect.right - w, window.innerWidth - 8 - w));
    const top = Math.max(8, Math.min(rect.bottom, window.innerHeight - 8 - h));
    Object.assign(panel.style, { left: `${left}px`, top: `${top}px` });
  }

  onMount(() => {
    place();
    panel?.focus();
    const onKeydown = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKeydown);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', onClose, true);
    return () => {
      window.removeEventListener('keydown', onKeydown);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', onClose, true);
      anchor?.focus();
    };
  });
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="scope-more-backdrop" data-testid="scope-more-backdrop" role="presentation" onclick={onClose}></div>
<div
  class="scope-more-panel"
  role="dialog"
  aria-label="More scope controls"
  tabindex="-1"
  bind:this={panel}
  data-testid="scope-more-panel"
>
  <div class="scope-more-group" data-testid="scope-more-radio-held">
    {@render radioHeld?.()}
  </div>
</div>

<style>
  .scope-more-backdrop {
    position: fixed;
    inset: 0;
    z-index: 999;
  }

  .scope-more-panel {
    position: fixed;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 240px;
    max-width: calc(100vw - 16px);
    padding: 8px;
    background: var(--v2-bg-darkest, #0a0a0f);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 6px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
    font-size: 12px;
  }

  .scope-more-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
</style>
