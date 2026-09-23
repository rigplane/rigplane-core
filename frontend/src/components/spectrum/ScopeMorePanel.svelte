<!--
  "More" panel for the scope row (MOR-2545 PR1) — an anchored group panel
  that opens under the ⋯ key, over the panorama.

  Close semantics, per the ticket:
  - Outside click: a fixed transparent backdrop (z-index 999, the existing
    popover scheme — `ScopeSettingsPopover.svelte`) swallows the click and
    closes, so a dismiss never retunes the panorama underneath.
  - Escape: a WINDOW-level keydown handler that exists only while the panel
    is MOUNTED (registered in onMount, removed in its cleanup) — idempotent,
    and it never steals Esc from the MOR-2514 frequency-digit release while
    closed, because closed means no handler at all.
  - Focus moves INTO the panel on open (the panel root takes it) and back to
    `returnFocusTo` (the ⋯ key) on close.

  Two groups are planned; PR1 mounts only the RADIO-HELD group. The panel
  already takes the screen-only group as a second snippet/slot so PR2 is a
  content-only change at the call site.
-->
<script lang="ts">
  import { onMount, type Snippet } from 'svelte';

  interface Props {
    onClose: () => void;
    /** Element focus returns to on close — the ⋯ key that opened the panel. */
    returnFocusTo?: HTMLElement | null;
    /** Radio-held controls group (PR1). */
    radioHeld?: Snippet;
    /** Screen-only controls group (PR2 — slot reserved, not yet rendered). */
    screenOnly?: Snippet;
  }
  let { onClose, returnFocusTo, radioHeld, screenOnly }: Props = $props();

  let panel: HTMLElement | undefined = $state();

  onMount(() => {
    panel?.focus();
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeydown);
    return () => {
      window.removeEventListener('keydown', onKeydown);
      returnFocusTo?.focus();
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
  {#if screenOnly}
    <div class="scope-more-group" data-testid="scope-more-screen-only">
      {@render screenOnly()}
    </div>
  {/if}
</div>

<style>
  .scope-more-backdrop {
    position: fixed;
    inset: 0;
    z-index: 999;
  }

  .scope-more-panel {
    position: absolute;
    top: 100%;
    right: 0;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 240px;
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
