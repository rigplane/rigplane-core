<script lang="ts">
  import { splitFrequencyToDigits, groupDigitsForDisplay, adjustFreqByDigit, type DigitInfo } from './frequency-tuning';

  interface Props {
    /**
     * CONFIRMED radio truth — and, deliberately, the SOLE arithmetic base
     * for every gesture below. MOR-1441 regression (verifier-reproduced):
     * an earlier revision let `freq` carry the PENDING target while a hot
     * burst was in flight, so `adjustFreqByDigit` computed off a value that
     * already included the burst's own not-yet-confirmed delta — each tick
     * added `(pending − confirmed) + step` instead of `step`, a positive
     * feedback loop that compounded roughly every pacing window (10 ticks
     * of +10 Hz intent measured out to +1910 Hz actual; 30 ticks to
     * +15.7 MHz — a TX-out-of-band hazard). `freq` must never be sourced
     * from `pendingDisplayHz` — that is precisely the bug.
     */
    freq: number;
    compact?: boolean;
    active?: boolean;
    receiver?: 'main' | 'sub';
    minFreq?: number;
    maxFreq?: number;
    onFreqChange?: (freq: number) => void;
    /**
     * MOR-1441 — the pending (not-yet-confirmed) tuning target, DISPLAY
     * ONLY: when non-null the digit readout shows THIS value instead of
     * `freq` (so the operator sees where a hot burst is heading) and marks
     * `data-freq-status="pending"` — but every gesture still computes its
     * next target from `freq` alone. Never plumb this into `adjustFreqByDigit`
     * or any arithmetic path; see the `freq` doc above for why.
     */
    pendingDisplayHz?: number | null;
    /**
     * MOR-1441 (B2) — already-localized text announcing the pending state
     * to assistive tech. The `data-freq-status`/italic marker is a VISUAL
     * channel only; without a rendered word (screen-reader convention, see
     * `TxAuxSurface.svelte`'s `.sr-only`/`aria-describedby` pair) an AT user
     * hears the pending frequency read as though it were confirmed. Passed
     * in rather than resolved here — this primitive stays i18n-blind, same
     * as every other `primitives/` component.
     */
    pendingAnnouncement?: string;
    /**
     * MOR-1480 — when true (the default), this primitive emits its own
     * `data-vfo-freq` + `data-vfo-active` (mirroring `active`) on its
     * focusable root, so the MOR-1444 keyboard routing guard
     * (`isFrequencyDisplayFocused` in `keyboard-map.ts`) recognizes ANY
     * mount without a bespoke wrapper.
     * DEFENSE-IN-DEPTH ONLY (verifier F1): no current mount depends on this
     * — the `VfoPanel`/`VfoHeader` header path it originally targeted renders
     * on no shipping skin, and `VfoSurface.svelte` (the one live mount) opts
     * out with `vfoFreqHook={false}`. Kept so a future non-semantic mount of
     * this primitive self-qualifies without bespoke wrapper markup.
     * `VfoSurface.svelte` already supplies its own equivalent hook —
     * on the SAME wrapper element its own tests key `data-freq-tunable` off
     * of — so it opts out here with `vfoFreqHook={false}` to avoid a second,
     * nested `[data-vfo-freq]` match for every tunable tile.
     */
    vfoFreqHook?: boolean;
  }

  let {
    freq,
    compact = false,
    active = true,
    receiver = 'main',
    minFreq = 0,
    maxFreq = 999_000_000,
    onFreqChange,
    pendingDisplayHz = null,
    pendingAnnouncement,
    vfoFreqHook = true,
  }: Props = $props();

  const pendingId = $props.id();

  // Receiver-aware CSS custom properties
  let cssVars = $derived({
    '--freq-active-color': `var(--v2-vfo-${receiver}-freq-active)`,
    '--freq-inactive-color': `var(--v2-vfo-${receiver}-freq-inactive)`,
    '--freq-hover-color': `var(--v2-vfo-${receiver}-freq-hover)`,
    '--freq-selected-bg': `var(--v2-vfo-${receiver}-freq-selected-bg)`,
    '--freq-selected-text': `var(--v2-vfo-${receiver}-freq-selected-text)`,
    '--freq-glow': `var(--v2-vfo-${receiver}-freq-glow)`,
    '--freq-font-family': `var(--v2-vfo-font-family)`,
    '--freq-font-weight': `var(--v2-vfo-font-weight)`,
  });

  let selectedDigitIndex = $state<number | null>(null);
  let hoveredDigitIndex = $state<number | null>(null);

  let pending = $derived(pendingDisplayHz !== null);
  // RENDER off the pending target when present — `freq` (confirmed) stays
  // untouched by this and is read ONLY inside the gesture handlers below.
  let allDigits = $derived(splitFrequencyToDigits(pendingDisplayHz ?? freq));
  let groups = $derived(groupDigitsForDisplay(allDigits));

  function handleWheel(digit: DigitInfo, event: WheelEvent) {
    event.preventDefault();
    const direction = event.deltaY > 0 ? -1 : 1;
    const newFreq = adjustFreqByDigit(freq, digit.multiplier, direction, minFreq, maxFreq);
    if (newFreq !== freq && onFreqChange) {
      onFreqChange(newFreq);
    }
  }

  function handleDigitClick(digit: DigitInfo, event: MouseEvent) {
    event.stopPropagation();
    selectedDigitIndex = digit.digitIndex;
  }

  function handleKeyDown(event: KeyboardEvent) {
    if (selectedDigitIndex === null) return;
    const digit = allDigits.find(d => d.digitIndex === selectedDigitIndex);
    if (!digit) return;
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      const newFreq = adjustFreqByDigit(freq, digit.multiplier, 1, minFreq, maxFreq);
      if (newFreq !== freq && onFreqChange) onFreqChange(newFreq);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      const newFreq = adjustFreqByDigit(freq, digit.multiplier, -1, minFreq, maxFreq);
      if (newFreq !== freq && onFreqChange) onFreqChange(newFreq);
    }
  }

  function handleDigitEnter(digit: DigitInfo) {
    hoveredDigitIndex = digit.digitIndex;
  }

  function handleDigitLeave() {
    hoveredDigitIndex = null;
  }

  function isSelected(digit: DigitInfo): boolean {
    return selectedDigitIndex === digit.digitIndex;
  }

  function isHovered(digit: DigitInfo): boolean {
    return hoveredDigitIndex === digit.digitIndex;
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  class="freq" class:compact class:inactive={!active}
  data-freq-status={pending ? 'pending' : 'confirmed'}
  data-vfo-freq={vfoFreqHook ? '' : undefined}
  data-vfo-active={vfoFreqHook ? active : undefined}
  aria-describedby={pending && pendingAnnouncement ? pendingId : undefined}
  style={Object.entries(cssVars).map(([k, v]) => `${k}:${v}`).join(';')}
  tabindex="0" role="group" aria-label="Frequency display" onkeydown={handleKeyDown}
>
  {#each groups.mhz as digit}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <span
      class="digit"
      class:selected={isSelected(digit)}
      class:hovered={isHovered(digit)}
      onwheel={(e) => handleWheel(digit, e)}
      onclick={(e) => handleDigitClick(digit, e)}
      onmouseenter={() => handleDigitEnter(digit)}
      onmouseleave={handleDigitLeave}
    >{digit.char}</span>
  {/each}
  <span class="sep">.</span>
  {#each groups.khz as digit}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <span
      class="digit"
      class:selected={isSelected(digit)}
      class:hovered={isHovered(digit)}
      onwheel={(e) => handleWheel(digit, e)}
      onclick={(e) => handleDigitClick(digit, e)}
      onmouseenter={() => handleDigitEnter(digit)}
      onmouseleave={handleDigitLeave}
    >{digit.char}</span>
  {/each}
  <span class="sep">.</span>
  {#each groups.hz as digit}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <span
      class="digit"
      class:selected={isSelected(digit)}
      class:hovered={isHovered(digit)}
      onwheel={(e) => handleWheel(digit, e)}
      onclick={(e) => handleDigitClick(digit, e)}
      onmouseenter={() => handleDigitEnter(digit)}
      onmouseleave={handleDigitLeave}
    >{digit.char}</span>
  {/each}
  {#if pending && pendingAnnouncement}
    <span id={pendingId} class="sr-only">{pendingAnnouncement}</span>
  {/if}
</div>

<style>
  .freq {
    display: inline-flex;
    align-items: baseline;
    font-family: var(--freq-font-family, 'Roboto Mono', monospace);
    font-weight: var(--freq-font-weight, 700);
    font-size: 24px;
    line-height: 1;
    letter-spacing: 0.035em;
    color: var(--freq-active-color, var(--v2-accent-cyan-bright));
    text-shadow: var(--freq-glow, none);
    white-space: nowrap;
    user-select: none;
  }

  .freq.compact {
    font-size: 14px;
  }

  .freq.inactive {
    color: var(--freq-inactive-color, var(--v2-text-muted));
  }

  /* MOR-1441 — a pending (unconfirmed) target never renders identically to
     confirmed radio truth. Structural (italic + reduced opacity), never a
     color-only tell, same doctrine as the `data-observed` convention. */
  .freq[data-freq-status='pending'] {
    font-style: italic;
    opacity: 0.75;
  }

  .digit {
    cursor: ns-resize;
    position: relative;
    transition: color 0.1s ease, background 0.1s ease;
  }

  .digit:hover {
    color: var(--freq-hover-color, var(--v2-text-white, #ffffff));
  }

  .digit.hovered::after {
    content: '';
    position: absolute;
    bottom: -2px;
    left: 0;
    right: 0;
    height: 2px;
    background: var(--freq-active-color, var(--v2-accent-cyan-bright));
    opacity: 0.5;
  }

  .digit.selected {
    color: var(--freq-selected-text, var(--v2-text-white, #ffffff));
    background: var(--freq-selected-bg, var(--v2-accent-cyan, #00b4d8));
    border-radius: 2px;
    padding: 0 1px;
  }

  .sep {
    opacity: 0.5;
    margin: 0 0.02em;
    pointer-events: none;
  }

  /* MOR-1441 (B2) — the visual pending marker's assistive-tech twin: a
     rendered word, not just italic/opacity (which convey nothing to a
     screen reader). Same convention as `TxAuxSurface.svelte`'s `.sr-only`. */
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
