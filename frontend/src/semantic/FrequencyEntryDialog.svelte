<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    open: boolean;
    targetLabel?: string;
    /** The heading; defaults to the frequency-entry wording (MOR-4220: Memory reuses the dialog). */
    title?: string;
    closeLabel?: string;
    returnFocus?: HTMLElement | null;
    status?: string;
    onclose?: () => void;
    children: Snippet;
  }

  let {
    open, targetLabel = '', title, closeLabel = 'Close frequency entry', returnFocus = null, status,
    onclose, children,
  }: Props = $props();
  const titleId = $props.id();
  let panel: HTMLElement | undefined = $state();
  let wasOpen = false;
  let restoreTarget: HTMLElement | null = null;

  const focusable = () => panel?.querySelectorAll<HTMLElement>(
    'input:not([disabled]), button:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ) ?? [];
  const initialFocus = () => panel?.querySelector<HTMLElement>(
    '.content input:not([disabled]), .content select:not([disabled]), .content textarea:not([disabled]), .content button:not([disabled])',
  ) ?? focusable()[0] ?? panel;

  $effect(() => {
    if (open && !wasOpen) {
      restoreTarget = returnFocus ?? (document.activeElement instanceof HTMLElement
        ? document.activeElement : null);
      queueMicrotask(() => initialFocus()?.focus());
    } else if (!open && wasOpen) {
      const target = restoreTarget;
      queueMicrotask(() => target?.focus());
    }
    wasOpen = open;
  });

  function requestClose(event?: Event): void {
    event?.preventDefault();
    event?.stopPropagation();
    onclose?.();
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      requestClose(event);
      return;
    }
    if (event.key !== 'Tab') return;
    const nodes = [...focusable()];
    if (nodes.length === 0) {
      event.preventDefault();
      panel?.focus();
      return;
    }
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  }

  // Focus can leave the dialog without the user moving it: a control that
  // removes itself when pressed (the memory list's Store, MOR-4220) drops
  // focus to <body>. Escape still closes and Tab comes back inside. This
  // runs in the window's capture phase, ahead of the global shortcuts, so
  // the Escape that closes the dialog is not also a shortcut (Escape is
  // bound to "clear RIT/XIT" by default).
  function handleWindowKeydown(event: KeyboardEvent): void {
    if (!open || panel?.contains(event.target as Node)) return;
    if (event.key === 'Escape') {
      requestClose(event);
    } else if (event.key === 'Tab') {
      event.preventDefault();
      event.stopPropagation();
      initialFocus()?.focus();
    }
  }

  function handleBackdrop(event: MouseEvent): void {
    if (event.target === event.currentTarget) requestClose(event);
  }
</script>

<svelte:window onkeydowncapture={handleWindowKeydown} />

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="frequency-entry-backdrop" data-testid="frequency-entry-dialog-backdrop" role="presentation"
    onclick={handleBackdrop} onkeydowncapture={handleKeydown}>
    <div class="frequency-entry-dialog" data-testid="frequency-entry-dialog-panel"
      role="dialog" aria-modal="true" aria-labelledby={titleId} tabindex="-1" bind:this={panel}>
      <header>
        <h2 id={titleId}>{title ?? `Set frequency — ${targetLabel}`}</h2>
        <button type="button" class="close" aria-label={closeLabel}
          onclick={requestClose}>×</button>
      </header>
      <div class="content">{@render children()}</div>
      {#if status}<p class="status" role="status">{status}</p>{/if}
    </div>
  </div>
{/if}

<style>
  .frequency-entry-backdrop {
    position: fixed; inset: 0; z-index: 1200; display: grid; place-items: center;
    top: var(--rp-chrome-top, 0px); /* MOR-3845 */
    padding: 20px; background: rgba(0, 0, 0, 0.62); backdrop-filter: blur(3px);
  }
  .frequency-entry-dialog {
    width: min(480px, 100%); border: 1px solid var(--v2-border-cyan);
    border-radius: 12px; background: var(--v2-panel-bg, #12141b);
    color: var(--v2-text-primary, #f4f6fa); box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
    /* A long body (the memory list, MOR-4220) scrolls inside the viewport. */
    max-height: calc(100dvh - var(--rp-chrome-top, 0px) - 40px); overflow: auto;
  }
  header { display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 14px 16px; border-bottom: 1px solid var(--v2-border-panel, #333); }
  h2 { margin: 0; font: 700 15px/1.3 'IBM Plex Sans', sans-serif; }
  .close { border: 0; background: transparent; color: inherit; padding: 4px 8px;
    font-size: 22px; cursor: pointer; }
  .content { padding: 16px; }
  .status { margin: 0; padding: 0 16px 16px; color: var(--v2-text-muted, #aeb5c2); }
</style>
