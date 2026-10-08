<!--
  Scope source selector (MOR-3158) — display-only in this issue: it names
  every scope source the info payload advertises (RIG / AUDIO / SDR) and
  marks the active one, so an operator can see the panadapter is one of
  several sources. It renders nothing unless more than one source is
  available — a single-source radio needs no selector.

  Selecting a source is a backend control that does not exist yet; when
  MOR-3157's follow-up defines it, this readout becomes the control's
  seat. Until then it stays inert and announces nothing as focusable
  (zero focusable elements by construction, like the badge beside it).
-->
<script lang="ts">
  import { scopeSourceLabel } from './ScopeSourceBadge.svelte';
  import type { ScopeSourceId } from './sdr-contract';

  interface Props {
    sources: readonly ScopeSourceId[];
    active: ScopeSourceId | null;
  }
  let { sources, active }: Props = $props();

  let shown = $derived(sources.length > 1 ? sources : []);
</script>

{#if shown.length > 0}
  <span
    class="scope-source-selector"
    data-testid="scope-source-selector"
    role="status"
    aria-label="Scope sources: {shown.map((source) =>
      `${scopeSourceLabel(source)}${source === active ? ' (active)' : ''}`).join(', ')}"
    title="Scope sources ({shown.map((source) =>
      `${scopeSourceLabel(source)}${source === active ? ' (active)' : ''}`).join(', ')})"
  >
    {#each shown as source (source)}
      <span
        class="scope-source-key"
        data-source={source}
        data-active={source === active}
        aria-current={source === active ? 'true' : undefined}
      >{scopeSourceLabel(source)}</span>
    {/each}
  </span>
{/if}

<style>
  .scope-source-selector {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    white-space: nowrap;
  }

  .scope-source-key {
    display: inline-flex;
    padding: 1px 5px;
    border: 1px solid transparent;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    color: var(--v2-text-dim, var(--text-muted, #888));
  }

  .scope-source-key[data-active='true'] {
    color: var(--text);
    border-color: var(--panel-border);
    background: rgba(255, 255, 255, 0.06);
  }
</style>
