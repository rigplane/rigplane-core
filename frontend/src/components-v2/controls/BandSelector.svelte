<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { getCapabilities } from '$lib/stores/capabilities.svelte';
  import { flattenBands, findActiveBand } from './band-utils';
  import { getShortcutHint } from '../layout/shortcut-hints';
  import { BROADCAST_SW_BANDS, BROADCAST_LW_MW_BANDS, findActiveBroadcastBand } from './broadcast-presets';

  import { deriveBandSelectorProps, getBandHandlers, getPresetHandlers } from '$lib/runtime/adapters/panel-adapters';

  /**
   * MOR-1367 (v3-rework S8) — the HAM/broadcast split, ruled by
   * `docs/plans/2026-08-06-settings-modal-boundary.md` §4a (row 6 vs row 10).
   *
   * This component is TWO independently-ruled things under one roof. The HAM
   * tab and its grid iterate `flattenBands(caps.freqRanges)` — the same source
   * `deriveBand` uses for `BandSurface`'s `band-choices` — so they are a plain
   * duplicate that retires under `band` zone ownership. The LW/MW and SWL tabs
   * iterate the sixteen curated `broadcast-presets.ts` entries (6 LW/MW + 10
   * SW; the S10 doc's "17" counted the interface's own `name:` field), which the
   * vocabulary excludes BY NAME (`semantic/radio-view-model.ts:494-496`: "UI convenience,
   * not radio facts, … deliberately absent") and which this component is the
   * only production consumer of. Suppressing the whole component would delete
   * that affordance with no remaining host.
   *
   * Hence a prop, not a mount gate: hosts pass `hamBands={!declared.has('band')}`
   * and keep mounting the component unconditionally. `true` by default, so
   * every caller that does not know about the split (LCD, mobile, tests)
   * renders exactly the pre-split three-tab component.
   */
  let { hamBands = true }: { hamBands?: boolean } = $props();

  const bandH = getBandHandlers();
  const presetH = getPresetHandlers();
  let bp = $derived(deriveBandSelectorProps());

  let currentFreq = $derived(bp.currentFreq);
  const onBandSelect = bandH.onBandSelect;
  const onPresetSelect = presetH.onPresetSelect;
  const onFreqPreset = presetH.onFreqPreset;

  let bandMode = $state<'ham' | 'broadcast' | 'lwmw'>('ham');

  // S10 §4a explicitly gates the DEFAULT too, not just the tab: with the HAM
  // tab gone there would be no control able to leave a `'ham'` selection, so
  // the component would open on an empty grid and stay there. Derived rather
  // than baked into `$state`'s initialiser so the fallback also holds if a host
  // flips the prop after mount.
  let shownMode = $derived(!hamBands && bandMode === 'ham' ? 'lwmw' : bandMode);

  let bands = $derived(flattenBands(getCapabilities()?.freqRanges ?? []));
  let activeBand = $derived(findActiveBand(currentFreq, getCapabilities()?.freqRanges ?? []));
  let activeBroadcast = $derived(findActiveBroadcastBand(currentFreq));

  function handleClick(name: string, defaultFreq: number, bsrCode?: number) {
    onBandSelect(name, defaultFreq, bsrCode);
  }

  function bandShortcut(bsrCode?: number): string | null {
    if (bsrCode === undefined) {
      return null;
    }
    return getShortcutHint('band_select', (binding) => Number(binding.params?.index) === bsrCode);
  }

</script>

<div class="band-tabs">
  {#if hamBands}
    <button
      class="band-tab"
      class:active={shownMode === 'ham'}
      onclick={(e: MouseEvent) => { bandMode = 'ham'; (e.currentTarget as HTMLElement)?.blur(); }}
    >HAM</button>
  {/if}
  <button
    class="band-tab"
    class:active={shownMode === 'lwmw'}
    onclick={(e: MouseEvent) => { bandMode = 'lwmw'; (e.currentTarget as HTMLElement)?.blur(); }}
  >LW/MW</button>
  <button
    class="band-tab"
    class:active={shownMode === 'broadcast'}
    onclick={(e: MouseEvent) => { bandMode = 'broadcast'; (e.currentTarget as HTMLElement)?.blur(); }}
  >SWL</button>
</div>

{#if shownMode === 'ham'}
  <div class="grid">
    {#each bands as band (band.name)}
      {@const isActive = activeBand === band.name}
      <HardwareButton
        active={isActive}
        indicator="edge-left"
        color="cyan"
        title={bandShortcut(band.bsrCode)}
        shortcutHint={bandShortcut(band.bsrCode)}
        onclick={() => handleClick(band.name, band.defaultFreq, band.bsrCode)}
      >
        {band.name}
      </HardwareButton>
    {/each}
  </div>
{:else if shownMode === 'lwmw'}
  <div class="grid">
    {#each BROADCAST_LW_MW_BANDS as preset (preset.name)}
      {@const isActive = activeBroadcast === preset.name}
      <HardwareButton
        active={isActive}
        indicator="edge-left"
        color="amber"
        onclick={() => onPresetSelect?.(preset.freq, preset.mode)}
      >
        {preset.name}
      </HardwareButton>
    {/each}
  </div>
{:else}
  <div class="grid">
    {#each BROADCAST_SW_BANDS as preset (preset.name)}
      {@const isActive = activeBroadcast === preset.name}
      <HardwareButton
        active={isActive}
        indicator="edge-left"
        color="amber"
        onclick={() => onPresetSelect?.(preset.freq, preset.mode)}
      >
        {preset.name}
      </HardwareButton>
    {/each}
  </div>
{/if}

<style>
  .band-tabs {
    display: flex;
    gap: 2px;
    padding: 6px 7px 0;
  }

  .band-tab {
    flex: 1;
    height: 24px;
    border: 1px solid var(--v2-border-darker);
    border-radius: 3px;
    background: var(--v2-bg-card);
    color: var(--v2-text-muted);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s;
    -webkit-tap-highlight-color: transparent;
    outline: none;
  }

  .band-tab:focus-visible {
    /* MOR-1232: colour only — the inset 1px shape is deliberately kept (a tight
     * tab strip). --v2-focus-ring-color is the contrast-checked ring colour;
     * the raw accent is under WCAG 1.4.11 3:1 on nord-light/solarized-light. */
    outline: 1px solid var(--v2-focus-ring-color);
    outline-offset: -1px;
  }

  .band-tab:hover {
    color: var(--v2-text-secondary);
  }

  .band-tab.active {
    color: var(--v2-accent-cyan);
    border-color: var(--v2-accent-cyan);
    background: var(--v2-bg-input);
  }

  .grid {
    padding: 6px 7px 7px;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 3px;
  }

  .grid > :global(button) {
    min-width: 0;
  }
</style>
