<script lang="ts">
  import type { FrequencyInteraction } from './frequency-interaction.svelte';
  import type { FrequencyReadoutModel } from './frequency-readout';

  export type FrequencyReadoutPresentation = 'interactive' | 'passive';

  interface Props {
    model: FrequencyReadoutModel;
    presentation: FrequencyReadoutPresentation;
    interaction?: FrequencyInteraction;
    compact?: boolean;
    active?: boolean;
    receiver?: 'main' | 'sub';
    vfoFreqHook?: boolean;
  }

  let {
    model,
    presentation,
    interaction,
    compact = false,
    active = true,
    receiver = 'main',
    vfoFreqHook = true,
  }: Props = $props();

  const pendingId = $props.id();
  let interactive = $derived(presentation === 'interactive');
  let cssVars = $derived(interactive ? {
    '--freq-active-color': `var(--v2-vfo-${receiver}-freq-active)`,
    '--freq-inactive-color': `var(--v2-vfo-${receiver}-freq-inactive)`,
    '--freq-hover-color': `var(--v2-vfo-${receiver}-freq-hover)`,
    '--freq-selected-bg': `var(--v2-vfo-${receiver}-freq-selected-bg)`,
    '--freq-selected-text': `var(--v2-vfo-${receiver}-freq-selected-text)`,
    '--freq-glow': `var(--v2-vfo-${receiver}-freq-glow)`,
    '--freq-font-family': 'var(--v2-vfo-font-family)',
    '--freq-font-weight': 'var(--v2-vfo-font-weight)',
  } : undefined);
  let style = $derived(cssVars
    ? Object.entries(cssVars).map(([key, value]) => `${key}:${value}`).join(';')
    : undefined);
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  class="freq" class:compact class:inactive={!active} class:interactive
  data-freq-status={interactive ? model.status : undefined}
  data-vfo-freq={interactive && vfoFreqHook ? '' : undefined}
  data-vfo-active={interactive && vfoFreqHook ? active : undefined}
  aria-describedby={interactive && model.status === 'pending' && model.pendingAnnouncement ? pendingId : undefined}
  aria-disabled={interactive ? interaction?.inert ?? true : undefined}
  {style}
  tabindex={interactive ? interaction?.inert === false ? 0 : -1 : undefined}
  role={interactive ? 'group' : undefined}
  aria-label={interactive ? 'Frequency display' : undefined}
  onkeydown={interactive ? interaction?.handleKeyDown : undefined}
>
  {#if !model.known}
    {#if interactive}
      <span>—</span>
    {:else}
      <span class="digits">{model.textGroups.mhz}</span><span class="sep">.</span><span
        class="digits">{model.textGroups.khz}</span><span class="sep">.</span><span
        class="digits">{model.textGroups.hz}</span>
    {/if}
  {:else if interactive}
    {#each model.groups.mhz as digit}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <span
        class="digit"
        class:selected={interaction?.isSelected(digit)}
        class:hovered={interaction?.isHovered(digit)}
        onwheel={(event) => interaction?.handleWheel(digit, event)}
        onclick={(event) => interaction?.handleDigitClick(digit, event)}
        onmouseenter={() => interaction?.handleDigitEnter(digit)}
        onmouseleave={() => interaction?.handleDigitLeave()}
      >{digit.char}</span>
    {/each}
    <span class="sep">.</span>
    {#each model.groups.khz as digit}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <span
        class="digit"
        class:selected={interaction?.isSelected(digit)}
        class:hovered={interaction?.isHovered(digit)}
        onwheel={(event) => interaction?.handleWheel(digit, event)}
        onclick={(event) => interaction?.handleDigitClick(digit, event)}
        onmouseenter={() => interaction?.handleDigitEnter(digit)}
        onmouseleave={() => interaction?.handleDigitLeave()}
      >{digit.char}</span>
    {/each}
    <span class="sep">.</span>
    {#each model.groups.hz as digit}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <span
        class="digit"
        class:selected={interaction?.isSelected(digit)}
        class:hovered={interaction?.isHovered(digit)}
        onwheel={(event) => interaction?.handleWheel(digit, event)}
        onclick={(event) => interaction?.handleDigitClick(digit, event)}
        onmouseenter={() => interaction?.handleDigitEnter(digit)}
        onmouseleave={() => interaction?.handleDigitLeave()}
      >{digit.char}</span>
    {/each}
  {:else}
    <span class="digits">{model.textGroups.mhz}</span><span class="sep">.</span><span
      class="digits">{model.textGroups.khz}</span><span class="sep">.</span><span
      class="digits">{model.textGroups.hz}</span>
  {/if}
  {#if interactive && model.status === 'pending' && model.pendingAnnouncement}
    <span id={pendingId} class="sr-only">{model.pendingAnnouncement}</span>
  {/if}
</div>

<style>
  .freq {
    display: inline-flex;
    align-items: baseline;
    font-family: 'Roboto Mono', monospace;
    font-weight: 700;
    font-size: 24px;
    line-height: 1;
    letter-spacing: 0.035em;
    color: var(--v2-accent-cyan-bright);
    white-space: nowrap;
    user-select: none;
  }

  .freq.interactive {
    font-family: var(--freq-font-family, 'Roboto Mono', monospace);
    font-weight: var(--freq-font-weight, 700);
    color: var(--freq-active-color, var(--v2-accent-cyan-bright));
    text-shadow: var(--freq-glow, none);
  }

  .freq.compact { font-size: 14px; }

  .freq.inactive { color: var(--v2-text-muted); }

  .freq.interactive.inactive { color: var(--freq-inactive-color, var(--v2-text-muted)); }

  .freq[data-freq-status='pending'] {
    font-style: italic;
    opacity: 0.75;
  }

  .digit {
    cursor: ns-resize;
    position: relative;
    transition: color 0.1s ease, background 0.1s ease;
  }

  .digit:hover { color: var(--freq-hover-color, var(--v2-text-white, #ffffff)); }

  .freq[aria-disabled='true'] .digit { cursor: default; }

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
  }

  .freq.interactive .sep { pointer-events: none; }

  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
