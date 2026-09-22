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

  The component is presentation-only — it emits a selection intent via
  `onChange` and does not touch the store directly.
-->
<script module lang="ts">
  type Receiver = 'MAIN' | 'SUB';

  export interface ReceiverSegmentAvailability {
    structural: boolean;
    operational: boolean;
    reason?: string;
  }

  let sequence = 0;
</script>

<script lang="ts">
  import { ControlButton } from '$lib/Button';
  import { hasCommandModifier } from '../layout/keyboard-map';

  type Receiver = 'MAIN' | 'SUB';

  interface Props {
    active: Receiver | null;
    onChange: (next: Receiver) => void;
    availability?: Partial<Record<Receiver, ReceiverSegmentAvailability>>;
    segmentLabels?: Partial<Record<Receiver, string>>;
    /** Emit an explicit selection intent even when the segment is already active. */
    allowReselect?: boolean;
    /** Render only the segments when the caller already owns the operation group. */
    embedded?: boolean;
    /** MOR-2509 bridge only: render the segments through the shared Button
     *  family's fill look. Every other mount keeps the raw segment markup. */
    hardware?: boolean;
    /** Optional label for screen readers. */
    label?: string;
  }

  let {
    active,
    onChange,
    availability,
    segmentLabels,
    allowReselect = false,
    embedded = false,
    hardware = false,
    label = 'Active receiver',
  }: Props = $props();

  const RECEIVERS: readonly Receiver[] = ['MAIN', 'SUB'];
  const reasonIdPrefix = `active-receiver-reason-${++sequence}`;
  const segmentElements: Partial<Record<Receiver, HTMLButtonElement>> = {};

  function registerSegment(node: HTMLButtonElement, receiver: Receiver) {
    segmentElements[receiver] = node;
    return {
      destroy: () => {
        if (segmentElements[receiver] === node) delete segmentElements[receiver];
      },
    };
  }

  /** The hardware path's segments are family buttons an action cannot
   *  register, so they are found through the group element instead. */
  let groupElement: HTMLElement | null = null;

  function state(receiver: Receiver): ReceiverSegmentAvailability {
    return availability?.[receiver] ?? { structural: true, operational: true };
  }

  let focusableReceiver = $derived(
    active !== null && state(active).structural && state(active).operational
      ? active
      : RECEIVERS.find((receiver) => state(receiver).structural && state(receiver).operational) ?? null,
  );

  function select(next: Receiver): void {
    if (!state(next).structural || !state(next).operational) return;
    if (!allowReselect && next === active) return;
    onChange(next);
  }

  function availableReceivers(): Receiver[] {
    return RECEIVERS.filter((receiver) => state(receiver).structural && state(receiver).operational);
  }

  function move(current: Receiver, delta: 1 | -1): Receiver {
    const candidates = availableReceivers();
    if (candidates.length === 0) return current;
    const currentIndex = candidates.indexOf(current);
    const start = currentIndex >= 0 ? currentIndex : 0;
    return candidates[(start + delta + candidates.length) % candidates.length];
  }

  function handleKeydown(event: KeyboardEvent, current: Receiver): void {
    if (hasCommandModifier(event)) return;
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
      const next = availableReceivers()[0] ?? current;
      select(next);
      focusSegment(next);
    } else if (key === 'End') {
      event.preventDefault();
      const candidates = availableReceivers();
      const next = candidates[candidates.length - 1] ?? current;
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
      segmentElements[target]?.focus();
      if (segmentElements[target] === undefined) {
        groupElement?.querySelector<HTMLButtonElement>(
          `[data-active-receiver-segment="${target}"]`,
        )?.focus();
      }
    });
  }

  function shortLabel(receiver: Receiver): string {
    return receiver === 'MAIN' ? 'M' : 'S';
  }

  function longLabel(receiver: Receiver): string {
    return receiver === 'MAIN' ? 'MAIN receiver' : 'SUB receiver';
  }
</script>

{#snippet segments()}
  {#each RECEIVERS as receiver (receiver)}
    {@const availability = state(receiver)}
    {@const isActive = receiver === active}
    {#if availability.structural}
      {@const reasonId = availability.reason ? `${reasonIdPrefix}-${receiver.toLowerCase()}` : undefined}
      {#if embedded && hardware}
        <!--
          MOR-2509 bridge: the segment renders through the shared Button
          family's selected appearance — the filled key IS the "which receiver is
          active" announcement the old text caption carried. Radio
          semantics (roving tabindex, arrows) ride the family's
          passthrough props unchanged.
        -->
        <ControlButton
          appearance="selected"
          surface="hardware"
          indicatorColor="cyan"
          reserveIndicator
          role="radio"
          ariaChecked={isActive}
          active={isActive}
          ariaLabel={segmentLabels?.[receiver] ?? longLabel(receiver)}
          describedBy={reasonId}
          title={availability.reason}
          disabled={!availability.operational}
          tabindex={availability.operational ? receiver === focusableReceiver ? 0 : -1 : undefined}
          onkeydown={(event) => handleKeydown(event, receiver)}
          data={{
            'active-receiver-segment': receiver,
            'dual-action': receiver.toLowerCase(),
          }}
          onclick={() => select(receiver)}
        >{segmentLabels?.[receiver] ?? receiver}</ControlButton>
      {:else}
      <!-- The raw segment below is the pre-bridge markup, kept verbatim:
           every non-standard mount renders exactly what it rendered
           before the bridge keys existed. -->
      <button
        type="button"
        role="radio"
        class="segment"
        class:embedded
        class:is-active={isActive}
        data-active-receiver-segment={receiver}
        data-dual-action={receiver.toLowerCase()}
        aria-checked={isActive}
        aria-label={segmentLabels?.[receiver] ?? longLabel(receiver)}
        aria-describedby={reasonId}
        title={availability.reason}
        disabled={!availability.operational}
        tabindex={availability.operational ? receiver === focusableReceiver ? 0 : -1 : undefined}
        use:registerSegment={receiver}
        onclick={() => select(receiver)}
        onkeydown={(e) => handleKeydown(e, receiver)}
      >
        {shortLabel(receiver)}
      </button>
      {/if}
      {#if reasonId}
        <span id={reasonId} class="sr-only">{availability.reason}</span>
      {/if}
    {/if}
  {/each}
{/snippet}

<div class="active-receiver-toggle" class:embedded class:hardware role="radiogroup" data-owns-arrows="both" aria-label={label} bind:this={groupElement}>
  {@render segments()}
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

  .segment:disabled {
    color: var(--v2-text-disabled, rgba(255, 255, 255, 0.3));
    cursor: not-allowed;
  }

  .segment.embedded {
    min-height: max(24px, var(--vfo-ops-badge-height, 18px));
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    border-radius: var(--vfo-ops-badge-radius, 4px);
  }

  .active-receiver-toggle.embedded {
    grid-column: 1 / -1;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--vfo-ops-gap, 4px);
    width: auto;
    min-height: 0;
    padding: 0;
    border: 0;
    background: transparent;
  }

  /* MOR-2509 hardware bridge: the keys' family tokens — 28px key height
     and 12px labels, the same numbers `VfoOperationGroup`'s ops column
     scopes. Pinned by `semantic/__tests__/VfoSurface.panel-rows.test.ts`. */
  .active-receiver-toggle.embedded.hardware {
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 6px;
    --btn-min-height: 28px;
    --btn-font-size: 12px;
    --indicator-dot-offset: 4px;
    --indicator-dot-gap: 4px;
  }

  .active-receiver-toggle.embedded.hardware > :global(button) {
    grid-column: span 3;
    width: 100%;
    min-width: 0;
  }

  @media (pointer: coarse) {
    .segment.embedded {
      min-width: var(--tap-target, 44px);
      min-height: var(--tap-target, 44px);
    }
  }

  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
