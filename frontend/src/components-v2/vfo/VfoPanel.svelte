<script lang="ts">
  import LinearSMeter from '../meters/LinearSMeter.svelte';
  import FrequencyDisplayInteractive from '../../primitives/frequency/FrequencyDisplayInteractive.svelte';
  import { StatusIndicator } from '$lib/Button';
  import { getCapabilities, receiverLabel, vfoSlotLabel } from '$lib/stores/capabilities.svelte';
  import { findActiveBand } from '../controls/band-utils';
  import { formatBadges, formatRitOffset } from './vfo-utils';
  import type { VfoLayoutProfile } from '../layout/vfo-layout-tokens';

  interface Props {
    receiver: 'main' | 'sub';
    freq: number;
    mode: string;
    filter: string;
    sValue: number;
    isActive: boolean;
    badges: Record<string, boolean | string>;
    rit?: { active: boolean; offset: number };
    layoutProfile?: VfoLayoutProfile;
    onModeClick?: () => void;
    onVfoClick?: () => void;
    onFreqChange?: (freq: number) => void;
  }

  let {
    receiver,
    freq,
    mode,
    filter,
    sValue,
    isActive,
    badges,
    rit,
    layoutProfile = 'baseline',
    onModeClick,
    onVfoClick,
    onFreqChange,
  }: Props = $props();

  let slot = $derived<'A' | 'B'>(receiver === 'main' ? 'A' : 'B');
  let label = $derived(receiverLabel(receiver === 'main' ? 'MAIN' : 'SUB'));
  let slotTag = $derived(vfoSlotLabel(slot).replace(/^VFO /, ''));
  let activeBand = $derived(findActiveBand(freq, getCapabilities()?.freqRanges ?? []));
  let badgeItems = $derived(formatBadges(badges, receiver));
  let meterVariant = $derived(layoutProfile === 'wide' ? 'vfo-wide' : 'vfo');
  let receiverChromeVars = $derived({
    '--receiver-accent': `var(--v2-receiver-${receiver}-accent)`,
    '--receiver-control-border': `var(--v2-vfo-${receiver}-control-border)`,
    '--receiver-control-glow': `var(--v2-vfo-${receiver}-control-glow)`,
    '--receiver-panel-glow-outer': `var(--v2-vfo-${receiver}-panel-glow-outer)`,
  });
</script>

<div
  class="panel"
  class:active={isActive}
  data-layout-profile={layoutProfile}
  style={Object.entries(receiverChromeVars).map(([key, value]) => `${key}:${value}`).join(';')}
>
  <div class="panel-header">
    <div class="header-title-group">
      <span class="vfo-label">{label}</span>
    </div>

    <div class="header-badges">
      <span class="header-tag meter-tag">BAR</span>
      <span class="header-tag slot-tag">{slotTag}</span>
    </div>
  </div>

  <div class="smeter-row panel-meter">
    <LinearSMeter value={sValue} compact label={slotTag} variant={meterVariant} />
  </div>

  <div class="panel-body">
    <div class="display-row">
      <div class="freq-row">
        <FrequencyDisplayInteractive {freq} active={isActive} {receiver} {onFreqChange} />
      </div>

      {#if rit?.active}
        <div class="rit-row">
          <span class="rit-label">RIT</span>
          <span class="rit-offset">{formatRitOffset(rit.offset)}</span>
        </div>
      {/if}
    </div>

    <div class="control-strip">
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div
        class="mode-badge-wrapper"
        onclick={(e) => { e.stopPropagation(); onModeClick?.(); }}
        title={`Change mode (current: ${mode})`}
      >
        <StatusIndicator
          label={mode}
          active={true}
          color="cyan"
          size="default"
        />
      </div>

      <StatusIndicator label={slotTag} active={false} color="muted" size="default" />

      {#if activeBand}
        <StatusIndicator label={activeBand} active={true} color="cyan" size="default" />
      {/if}

      <StatusIndicator label={filter} active={true} color="cyan" size="default" />

      {#each badgeItems as item (item.label)}
        <StatusIndicator
          label={item.label}
          active={item.active}
          color={item.color as 'cyan' | 'green' | 'amber' | 'orange' | 'red' | 'muted'}
          size="default"
        />
      {/each}
    </div>
  </div>
</div>

<style>
  .panel {
    display: grid;
    grid-template-rows:
      var(--vfo-panel-header-height, 18px)
      var(--vfo-panel-meter-height, 58px)
      var(--vfo-panel-body-height, 64px);
    min-height: 100%;
    background: linear-gradient(180deg, var(--v2-bg-gradient-start) 0%, var(--v2-bg-darkest) 100%);
    border: 1px solid var(--v2-border-darker);
    border-radius: 4px;
    overflow: hidden;
    font-family: 'Roboto Mono', monospace;
    transition: border-color 150ms ease, box-shadow 150ms ease;
  }

  .panel.active {
    border-color: var(--receiver-control-border);
    box-shadow:
      inset 0 0 0 1px var(--receiver-control-glow),
      0 0 12px 1px var(--receiver-panel-glow-outer);
  }

  .panel-meter,
  .control-strip {
    transition: filter 150ms ease;
  }

  .panel:not(.active) .panel-meter,
  .panel:not(.active) .control-strip {
    filter: saturate(0.4) brightness(0.85);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: var(--vfo-panel-header-height, 18px);
    padding:
      var(--vfo-badge-inset-y, 3px)
      var(--vfo-panel-pad-x, 10px)
      0;
    border-bottom: none;
  }

  .header-title-group {
    display: flex;
    align-items: center;
    gap: var(--vfo-header-group-gap, 5px);
  }

  .vfo-label {
    color: var(--v2-text-secondary);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  .header-badges {
    display: flex;
    align-items: center;
    gap: var(--vfo-header-badge-gap, 3px);
  }

  .header-tag {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: var(--vfo-header-badge-height, 12px);
    padding: 0 var(--vfo-header-badge-padding-x, 5px);
    border-radius: var(--vfo-panel-badge-radius, 3px);
    font-size: var(--vfo-control-badge-font-size, 7px);
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .meter-tag {
    border: 1px solid var(--receiver-control-border);
    background: var(--v2-vfo-meter-tag-bg);
    color: var(--receiver-accent);
  }

  .slot-tag {
    border: 1px solid var(--v2-border-soft);
    background: var(--v2-vfo-slot-tag-bg);
    color: var(--v2-text-muted);
  }

  .panel-meter {
    padding: 0 var(--vfo-panel-meter-pad-x, 6px);
  }

  .mode-badge-wrapper {
    cursor: pointer;
    display: inline-flex;
  }

  .mode-badge-wrapper:hover :global(.v2-status-indicator) {
    filter: brightness(1.15);
  }



  .panel-body {
    display: grid;
    grid-template-rows:
      var(--vfo-display-row-height, 38px)
      var(--vfo-control-strip-height, 22px);
    gap: var(--vfo-panel-body-gap, 4px);
    padding:
      0
      var(--vfo-panel-body-pad-x, 10px)
      var(--vfo-panel-body-pad-bottom, 0px);
    min-height: 0;
  }

  .display-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--vfo-display-row-gap, 12px);
    min-height: var(--vfo-display-row-height, 38px);
  }

  .freq-row {
    display: flex;
    align-items: center;
    min-width: 0;
  }

  .freq-row :global(.freq) {
    font-size: var(--vfo-frequency-size, 24px);
    letter-spacing: var(--vfo-frequency-letter-spacing, 0.03em);
  }

  .freq-row :global(.sep) {
    opacity: 0.62;
    margin: 0 0.03em;
  }

  .rit-row {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .rit-label {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 18px;
    padding: 0 7px;
    border: 1px solid var(--receiver-control-border);
    border-radius: 4px;
    background: var(--v2-vfo-rit-label-bg);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--receiver-accent);
  }

  .rit-offset {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 18px;
    padding: 0 9px;
    border: 1px solid var(--v2-vfo-rit-offset-border);
    border-radius: 4px;
    background: var(--v2-vfo-rit-offset-bg);
    font-size: 10px;
    font-weight: 700;
    color: var(--v2-accent-yellow);
  }

  .control-strip {
    display: flex;
    align-items: center;
    gap: var(--vfo-control-strip-gap, 4px);
    min-height: var(--vfo-control-strip-height, 22px);
    overflow: hidden;
    white-space: nowrap;
  }



  @media (max-width: 1280px) {
    .freq-row :global(.freq) {
      font-size: 44px;
    }
  }

  @media (max-width: 1024px) {
    .display-row {
      flex-wrap: wrap;
      align-items: flex-start;
    }

    .freq-row :global(.freq) {
      font-size: 32px;
    }

    .panel-body {
      grid-template-rows: auto auto;
    }

    .control-strip {
      white-space: normal;
      overflow: visible;
      flex-wrap: wrap;
    }
  }
</style>
