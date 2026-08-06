<script lang="ts">
  import { splitFrequencyToDigits, groupDigitsForDisplay, adjustFreqByDigit, type DigitInfo } from './frequency-tuning';

  interface Props {
    freq: number;
    compact?: boolean;
    active?: boolean;
    receiver?: 'main' | 'sub';
    minFreq?: number;
    maxFreq?: number;
    onFreqChange?: (freq: number) => void;
  }

  let {
    freq,
    compact = false,
    active = true,
    receiver = 'main',
    minFreq = 0,
    maxFreq = 999_000_000,
    onFreqChange,
  }: Props = $props();
  
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

  let allDigits = $derived(splitFrequencyToDigits(freq));
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
<div class="freq" class:compact class:inactive={!active} style={Object.entries(cssVars).map(([k, v]) => `${k}:${v}`).join(';')} tabindex="0" role="group" aria-label="Frequency display" onkeydown={handleKeyDown}>
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
</style>
