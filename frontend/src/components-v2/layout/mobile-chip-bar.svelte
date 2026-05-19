<script lang="ts">
  /**
   * Horizontal chip-scroll navigation bar for mobile portrait IA (#839).
   * One active chip at a time. Horizontal scroll (touch) for overflow.
   */
  import { t } from '$lib/i18n';

  type ChipItem = { id: string; label: string };

  interface Props {
    chips: ChipItem[];
    activeId: string;
    onSelect: (id: string) => void;
  }

  let { chips, activeId, onSelect }: Props = $props();
</script>

<div class="m-chip-bar" role="tablist" aria-label={t('core.mobile.chips.label')}>
  {#each chips as chip (chip.id)}
    <button
      type="button"
      role="tab"
      class="m-chip"
      class:m-chip-active={chip.id === activeId}
      aria-selected={chip.id === activeId}
      aria-controls="m-chip-panel-{chip.id}"
      onclick={() => onSelect(chip.id)}
    >
      {chip.label}
    </button>
  {/each}
</div>

<style>
  .m-chip-bar {
    display: flex;
    flex-shrink: 0;
    gap: 4px;
    padding: 6px 8px;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
    background: var(--v2-bg-darker, #0a0a14);
    border-bottom: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  .m-chip-bar::-webkit-scrollbar {
    display: none;
  }

  .m-chip {
    flex: 0 0 auto;
    min-width: 56px;
    min-height: 44px;
    padding: 6px 14px;
    border-radius: 22px;
    border: 1px solid var(--v2-border-darker, #333);
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-secondary, #aaa);
    font-family: 'Roboto Mono', monospace;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: background 0.15s, border-color 0.15s, color 0.15s;
    white-space: nowrap;
  }

  .m-chip:focus-visible {
    outline: 2px solid var(--v2-accent-cyan, #22d3ee);
    outline-offset: 2px;
  }

  .m-chip-active {
    background: var(--v2-accent-cyan, #22d3ee);
    border-color: var(--v2-accent-cyan, #22d3ee);
    color: var(--v2-bg-card, #111);
  }
</style>
