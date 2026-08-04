<!--
  ActiveReceiverToggle — segmented [M|S] radiogroup for selecting the
  active receiver on dual-RX radios.  Replaces the old `activate-chip`
  affordance from DualVfoDisplay: a single primary control is the sole
  way to switch receivers from the UI (keyboard shortcuts handled
  elsewhere — see #827).

  ARIA: role="radiogroup" with two role="radio" segments.  Keyboard:
  - Left/Right arrows move the visual focus across segments and
    immediately select (typical radiogroup pattern).
  - Enter/Space activate the focused segment (redundant with click).

  The component is presentation-only — it emits the new selection via
  `onChange` and does not touch the store directly.
-->
<script lang="ts">
  type Receiver = 'MAIN' | 'SUB';

  interface Props {
    active: Receiver;
    onChange: (next: Receiver) => void;
    /** Optional label for screen readers. */
    label?: string;
  }

  let { active, onChange, label = 'Active receiver' }: Props = $props();

  const RECEIVERS: Receiver[] = ['MAIN', 'SUB'];

  function select(next: Receiver): void {
    if (next === active) return;
    onChange(next);
  }

  function move(current: Receiver, delta: 1 | -1): Receiver {
    const idx = RECEIVERS.indexOf(current);
    const nextIdx = (idx + delta + RECEIVERS.length) % RECEIVERS.length;
    return RECEIVERS[nextIdx];
  }

  function handleKeydown(event: KeyboardEvent, current: Receiver): void {
    const { key } = event;
    if (key === 'ArrowLeft' || key === 'ArrowUp') {
      event.preventDefault();
      const next = move(current, -1);
      select(next);
      focusSegment(next);
    } else if (key === 'ArrowRight' || key === 'ArrowDown') {
      event.preventDefault();
      const next = move(current, 1);
      select(next);
      focusSegment(next);
    } else if (key === 'Home') {
      event.preventDefault();
      const next = RECEIVERS[0];
      select(next);
      focusSegment(next);
    } else if (key === 'End') {
      event.preventDefault();
      const next = RECEIVERS[RECEIVERS.length - 1];
      select(next);
      focusSegment(next);
    } else if (key === 'Enter' || key === ' ') {
      event.preventDefault();
      select(current);
    }
  }

  function focusSegment(target: Receiver): void {
    // Defer to next tick so Svelte can update tabindex attrs first.
    queueMicrotask(() => {
      const el = document.querySelector<HTMLButtonElement>(
        `[data-active-receiver-segment="${target}"]`,
      );
      el?.focus();
    });
  }

  function shortLabel(receiver: Receiver): string {
    return receiver === 'MAIN' ? 'M' : 'S';
  }

  function longLabel(receiver: Receiver): string {
    return receiver === 'MAIN' ? 'MAIN receiver' : 'SUB receiver';
  }
</script>

<div
  class="active-receiver-toggle"
  role="radiogroup"
  aria-label={label}
  data-testid="active-receiver-toggle"
>
  {#each RECEIVERS as receiver (receiver)}
    {@const isActive = receiver === active}
    <button
      type="button"
      role="radio"
      class="segment"
      class:is-active={isActive}
      data-active-receiver-segment={receiver}
      aria-checked={isActive}
      aria-label={longLabel(receiver)}
      tabindex={isActive ? 0 : -1}
      onclick={() => select(receiver)}
      onkeydown={(e) => handleKeydown(e, receiver)}
    >
      {shortLabel(receiver)}
    </button>
  {/each}
</div>

<style>
  .active-receiver-toggle {
    display: inline-grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    width: 100%;
    min-height: var(--vfo-ops-badge-height, 18px);
    padding: 1px;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    border-radius: var(--vfo-ops-badge-radius, 4px);
    background: var(--v2-surface-muted, rgba(255, 255, 255, 0.04));
    box-sizing: border-box;
    font-family: 'Roboto Mono', monospace;
  }

  .segment {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2px var(--vfo-ops-badge-padding-x, 6px);
    border: 0;
    border-radius: calc(var(--vfo-ops-badge-radius, 4px) - 2px);
    background: transparent;
    color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55));
    font-family: inherit;
    font-size: var(--vfo-ops-badge-font-size, 10px);
    font-weight: 700;
    letter-spacing: 0.06em;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      color 120ms ease;
  }

  .segment:hover:not(.is-active) {
    color: var(--v2-text-secondary, rgba(255, 255, 255, 0.8));
    background: rgba(255, 255, 255, 0.05);
  }

  .segment:focus-visible {
    outline: none;
    box-shadow: var(--v2-focus-ring-shadow);
  }

  .segment.is-active {
    background: var(--v2-accent-cyan, #00d4ff);
    color: var(--v2-text-bright, #000);
  }
</style>
