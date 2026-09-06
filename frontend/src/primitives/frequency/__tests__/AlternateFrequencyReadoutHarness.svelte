<script lang="ts">
  import { projectFrequencyReadout } from '../frequency-readout';
  import { createFrequencyInteraction } from '../frequency-interaction.svelte';

  interface Props {
    confirmedHz: number | null;
    displayHz?: number | null;
    pendingDisplayHz?: number | null;
    disabled?: boolean;
    contextKey?: string;
    onFreqChange?: (frequencyHz: number) => void;
  }

  let {
    confirmedHz,
    displayHz,
    pendingDisplayHz = null,
    disabled = false,
    contextKey,
    onFreqChange,
  }: Props = $props();

  let readout = $derived(projectFrequencyReadout({
    confirmedHz,
    displayHz,
    pendingDisplayHz,
  }));
  const interaction = createFrequencyInteraction({
    get confirmedHz() { return confirmedHz; },
    get digits() { return readout.digits; },
    get disabled() { return disabled; },
    get contextKey() { return contextKey; },
    minFreq: 0,
    maxFreq: 999_000_000,
    get onFreqChange() { return onFreqChange; },
  });
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  role="group"
  tabindex="0"
  data-alternate-frequency-readout
  data-source={readout.source}
  aria-disabled={interaction.inert}
  onkeydown={interaction.handleKeyDown}
>
  {#each readout.digits as digit}
    <button
      type="button"
      data-multiplier={digit.multiplier}
      aria-pressed={interaction.isSelected(digit)}
      onclick={(event) => interaction.handleDigitClick(digit, event)}
      onwheel={(event) => interaction.handleWheel(digit, event)}
    >{digit.char}</button>
  {/each}
</div>
