<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import {
    buildAttControlModel,
    getAttOverflowLabel,
  } from '../panels/rf-frontend-utils';

  interface Props {
    values: number[];
    selected: number;
    onchange: (value: number) => void;
    labels?: Record<string, string>;
    accentColor?: string;
    shortcutHint?: string | null;
    title?: string | null;
  }

  let {
    values,
    selected,
    onchange,
    labels = {},
    accentColor = 'var(--v2-accent-cyan)',
    shortcutHint = null,
    title = null,
  }: Props = $props();

  let menuOpen = $state(false);
  let menuStyle = $state('');
  let moreButtonEl: HTMLElement | undefined = $state();
  let controlModel = $derived(buildAttControlModel(values, Object.keys(labels).length ? labels : undefined));
  let overflowSelected = $derived(controlModel.overflowOptions.some((option) => option.value === selected));
  let overflowLabel = $derived(getAttOverflowLabel(selected, controlModel.overflowOptions));

  function handleQuickChange(value: string | number): void {
    onchange(value as number);
  }

  function handleOverflowSelect(value: number): void {
    onchange(value);
    menuOpen = false;
  }

  function openMenu(): void {
    if (moreButtonEl) {
      const rect = moreButtonEl.getBoundingClientRect();
      const menuWidth = 220;
      let left = rect.right - menuWidth;
      if (left < 8) left = 8;
      const top = rect.bottom + 6;
      menuStyle = `top: ${top}px; left: ${left}px;`;
    }
    menuOpen = true;
  }
 </script>

<div class="att-control" style="--control-accent: {accentColor};" data-shortcut-hint={shortcutHint ?? undefined} title={title ?? shortcutHint ?? undefined}>
  <div class="button-grid">
    {#each controlModel.quickOptions as option}
      <HardwareButton
        active={selected === option.value}
        indicator="edge-left"
        color="cyan"
        onclick={() => handleQuickChange(option.value)}
      >
        {option.label}
      </HardwareButton>
    {/each}
  </div>

  {#if controlModel.overflowOptions.length > 0}
    <div bind:this={moreButtonEl}>
      <HardwareButton
        active={overflowSelected}
        indicator="edge-left"
        color="cyan"
        onclick={openMenu}
      >
        {overflowLabel}
      </HardwareButton>
    </div>
  {/if}

  {#if menuOpen}
    <button
      type="button"
      class="menu-backdrop"
      aria-label="Close attenuator menu"
      onclick={() => (menuOpen = false)}
    ></button>

    <div class="menu" role="dialog" aria-label="More attenuator values" style={menuStyle}>
      <div class="menu-title">ATT Values</div>
      <div class="menu-grid">
        {#each controlModel.overflowOptions as option}
          <HardwareButton
            active={option.value === selected}
            indicator="edge-left"
            color="cyan"
            onclick={() => handleOverflowSelect(option.value)}
          >
            {option.label}
          </HardwareButton>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .att-control {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 6px;
    min-width: 0;
    width: 100%;
  }

  .button-grid {
    display: flex;
    gap: 4px;
  }

  .button-grid > :global(button) {
    flex: 1 1 0;
    min-width: 0;
  }

  .menu-backdrop {
    position: fixed;
    inset: 0;
    z-index: 10000;
    background: var(--v2-attenuator-bg);
    border: 0;
    padding: 0;
    margin: 0;
  }

  .menu {
    position: fixed;
    z-index: 10001;
    min-width: min(220px, calc(100vw - 32px));
    padding: 8px;
    background: var(--v2-bg-darkest);
    border: 1px solid var(--v2-border-darker);
    border-radius: 4px;
    box-shadow: 0 10px 24px var(--v2-attenuator-shadow);
  }

  .menu-title {
    margin-bottom: 6px;
    color: var(--v2-text-subdued);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .menu-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;
  }

  .menu-grid > :global(button) {
    min-width: 0;
  }
</style>
