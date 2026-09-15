<script module lang="ts">
  import type { DisplayIndicator } from '../../semantic/radio-display-model';
  import type { LcdStatusIconName } from './LcdStatusIcon.svelte';

  export interface LcdFlagRailItem {
    readonly label: string;
    readonly icon?: LcdStatusIconName | null;
    readonly field: DisplayIndicator;
    readonly value?: string;
    readonly dataState?: string;
    readonly statusLabel?: string;
  }
</script>

<script lang="ts">
  import LcdStatusIcon from './LcdStatusIcon.svelte';

  interface Props {
    label: string;
    items: readonly LcdFlagRailItem[];
    end?: boolean;
  }

  let { label, items, end = false }: Props = $props();
</script>

<div class="flag-zone" class:end>
  <span class="zone-label">{label}</span>
  {#each items as item}
    <span
      class="status-flag {item.field.state}"
      data-state={item.dataState ?? item.field.state}
      data-status-label={item.statusLabel}
    >{#if item.icon}<LcdStatusIcon name={item.icon} />{/if}{item.label}{item.value ? ` ${item.value}` : ''}</span>
  {/each}
</div>

<style>
  .flag-zone { display: flex; align-items: center; gap: 4px; min-width: 0; }
  .flag-zone.end { justify-content: flex-end; }
  .zone-label { margin-right: 4px; color: var(--ink-mid); font-size: 11px; font-weight: 700; letter-spacing: 0.2em; }
  .status-flag {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    min-height: 23px;
    box-sizing: border-box;
    padding: 2px 8px;
    border: 1.25px solid currentColor;
    border-radius: 2px;
    color: var(--ink-ghost);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.1em;
    line-height: 1.2;
    white-space: nowrap;
  }
  .status-flag.active { color: var(--ink-strong); }
  .status-flag.inactive { color: var(--ink-ghost); }
  .status-flag.unknown { color: var(--ink-soft); }
  .status-flag.unsupported { visibility: hidden; }
  .status-flag[data-state='active'][data-state='active'] { background: rgba(26, 16, 0, 0.06); }
  :global(.peer-display[data-rf-state='transmitting']) .status-flag.active { color: #7a1a0a; }
</style>
