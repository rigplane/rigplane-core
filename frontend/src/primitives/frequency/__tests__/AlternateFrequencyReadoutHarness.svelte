<script module lang="ts">
  import type { FrequencyInteraction } from '../frequency-interaction.svelte';

  const retained: FrequencyInteraction[] = [];
  export const retainedInteractions = () => retained.slice();
  export const clearRetainedInteractions = () => { retained.length = 0; };
  function retain(interaction: FrequencyInteraction): void {
    if (retained.at(-1) !== interaction) retained.push(interaction);
  }
</script>

<script lang="ts">
  import type { FrequencyRendererProps } from '../../../../component-kit-api/src/index';

  let {
    model, interaction, presentation, compact, active, receiver, vfoFreqHook,
  }: FrequencyRendererProps = $props();
  $effect(() => retain(interaction));
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  role="group"
  tabindex="0"
  data-alternate-frequency-readout
  data-source={model.source}
  data-presentation={presentation}
  data-compact={compact}
  data-active={active}
  data-receiver={receiver}
  data-vfo-freq-hook={vfoFreqHook}
  aria-disabled={interaction.inert}
  onkeydown={interaction.handleKeyDown}
>
  {#each model.digits as digit}
    <button
      type="button"
      data-multiplier={digit.multiplier}
      aria-pressed={interaction.isSelected(digit)}
      onclick={(event) => interaction.handleDigitClick(digit, event)}
      onwheel={(event) => interaction.handleWheel(digit, event)}
    >{digit.char}</button>
  {/each}
</div>
