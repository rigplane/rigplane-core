<script lang="ts">
  import type { FrequencyRendererProps } from '@rigplane/component-kit-api';

  let {
    model,
    interaction,
    presentation,
    compact,
    active,
    receiver,
  }: FrequencyRendererProps = $props();

  const label = $derived(`${receiver === 'main' ? 'Main' : 'Sub'} frequency`);
</script>

<div
  data-fixture-frequency={receiver}
  data-status={model.status}
  data-presentation={presentation}
  data-compact={compact}
  data-active={active}
  role="group"
  aria-label={label}
>
  {#if model.known}
    {#each model.digits as digit}
      <button
        type="button"
        disabled={interaction.inert}
        aria-pressed={interaction.isSelected(digit)}
        data-hovered={interaction.isHovered(digit)}
        onwheel={(event) => interaction.handleWheel(digit, event)}
        onclick={(event) => interaction.handleDigitClick(digit, event)}
        onkeydown={(event) => interaction.handleKeyDown(event)}
        onmouseenter={() => interaction.handleDigitEnter(digit)}
        onmouseleave={() => interaction.handleDigitLeave()}
      >{digit.char}</button>
    {/each}
  {:else}
    <span>unknown</span>
  {/if}
  <span aria-hidden="true">Hz</span>
</div>
