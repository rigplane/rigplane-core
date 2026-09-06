<script module lang="ts">
  let sequence = 0;
</script>

<script lang="ts">
  import LinearSMeter from '../meters/LinearSMeter.svelte';
  import FrequencyDisplayInteractive from '../../primitives/frequency/FrequencyDisplayInteractive.svelte';
  import { StatusIndicator } from '$lib/Button';
  import { splitFrequencyToDigits, groupDigitsForDisplay } from '../../primitives/frequency/frequency-tuning';
  import { formatRitOffset } from './vfo-utils';
  import type { VfoLayoutProfile } from '../layout/vfo-layout-tokens';

  export interface VfoPanelBadge {
    label: string;
    active: boolean;
    color?: string;
    state?: string;
  }

  export interface VfoPanelSlotChoice {
    key: string;
    receiver: 'main' | 'sub';
    slot: string;
    label: string;
    frequencyText: string;
    active: boolean;
    activeSlot: boolean;
    txTarget: boolean;
    disabled: boolean;
    reason?: string;
  }

  interface Props {
    receiver: 'main' | 'sub';
    receiverLabel: string;
    slotTag: string;
    freq: number | null;
    displayHz?: number | null;
    pendingDisplayHz?: number | null;
    frequencyState?: 'current' | 'stale' | 'unknown' | 'unsupported';
    staleReason?: string;
    contextKey?: string;
    frequencyDisabled?: boolean;
    mode: string | null;
    filter: string | null;
    sValue: number | null;
    meterPresent?: boolean;
    meterOperational?: boolean;
    isActive: boolean;
    badgeItems: readonly VfoPanelBadge[];
    bandText?: string | null;
    rit?: { active: boolean; offset: number };
    slotChoices?: readonly VfoPanelSlotChoice[];
    layoutProfile?: VfoLayoutProfile;
    onModeClick?: () => void;
    onFreqChange?: (freq: number) => void;
    onSelectSlot?: (key: string) => void;
  }

  let {
    receiver, receiverLabel, slotTag, freq, displayHz, pendingDisplayHz = null,
    frequencyState = 'current', staleReason, contextKey, frequencyDisabled = false,
    mode, filter, sValue, meterPresent = true, meterOperational,
    isActive,
    badgeItems, bandText, rit, slotChoices = [],
    layoutProfile = 'baseline',
    onModeClick,
    onFreqChange,
    onSelectSlot,
  }: Props = $props();

  let meterVariant = $derived(layoutProfile === 'wide' ? 'vfo-wide' : 'vfo');
  let staleDisplay = $derived(frequencyState === 'stale');
  const staleId = `vfo-panel-stale-${++sequence}`;
  let receiverChromeVars = $derived({
    '--receiver-accent': `var(--v2-receiver-${receiver}-accent)`,
    '--receiver-control-border': `var(--v2-vfo-${receiver}-control-border)`,
    '--receiver-control-glow': `var(--v2-vfo-${receiver}-control-glow)`,
    '--receiver-panel-glow-outer': `var(--v2-vfo-${receiver}-panel-glow-outer)`,
  });

  function formatFrequency(hz: number | null | undefined): string {
    if (hz === null || hz === undefined || !Number.isFinite(hz)) return '—';
    const groups = groupDigitsForDisplay(splitFrequencyToDigits(hz));
    return [groups.mhz, groups.khz, groups.hz]
      .map((group) => group.map((digit) => digit.char).join('')).join('.');
  }
</script>

<div
  class="panel"
  class:active={isActive}
  data-layout-profile={layoutProfile}
  style={Object.entries(receiverChromeVars).map(([key, value]) => `${key}:${value}`).join(';')}
>
  <div class="panel-header">
    <div class="header-title-group">
      <span class="vfo-label">{receiverLabel}</span>
    </div>

    <div class="header-badges">
      <span class="header-tag meter-tag">BAR</span>
      <span class="header-tag slot-tag">{slotTag}</span>
    </div>
  </div>

  <div class="smeter-row panel-meter">
    {#if meterPresent}
      <div data-testid="receiver-s-meter" data-receiver={receiver}
        data-operational={meterOperational === undefined ? undefined : String(meterOperational)}
        aria-label={sValue === null ? `${receiverLabel} S meter unknown` : undefined}>
        <LinearSMeter value={Number.isFinite(sValue) ? sValue : null} compact label={slotTag} variant={meterVariant} />
      </div>
    {/if}
  </div>

  <div class="panel-body">
    <div class="display-row">
      <div class="freq-row">
        <span data-vfo-freq data-display-state={frequencyState} class:display-unknown={displayHz === null}
          aria-describedby={staleDisplay ? staleId : undefined}>
          {#if freq !== null && Number.isFinite(freq)}
            <FrequencyDisplayInteractive
              {freq} {displayHz} {pendingDisplayHz} {contextKey}
              disabled={frequencyDisabled || frequencyState !== 'current'}
              active={isActive} {receiver} {onFreqChange} vfoFreqHook={false}
            />
          {:else}
            <span class="freq unknown-frequency">{formatFrequency(pendingDisplayHz ?? displayHz)}</span>
          {/if}
        </span>
        <span id={staleId} data-vfo-stale-cue class="stale-cue" aria-hidden={!staleDisplay}
          class:stale={staleDisplay} title={staleDisplay ? staleReason : undefined}>
          <span aria-hidden="true">†</span><span class="sr-only">{staleReason}</span>
        </span>
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
          label={mode ?? '—'}
          active={true}
          color="cyan"
          size="default"
        />
      </div>

      <StatusIndicator label={slotTag} active={false} color="muted" size="default" />

      {#if bandText}
        <StatusIndicator label={bandText} active={true} color="cyan" size="default" />
      {/if}

      <StatusIndicator label={filter ?? '—'} active={filter !== null} color={filter === null ? 'muted' : 'cyan'} size="default" />

      {#each badgeItems as item (item.label)}
        <span data-indicator-fact={item.label.split(' ')[0]?.toLowerCase()} data-state={item.state}>
          <StatusIndicator
            label={item.label}
            active={item.active}
            color={(item.color ?? 'cyan') as 'cyan' | 'green' | 'amber' | 'orange' | 'red' | 'muted'}
            size="default"
          />
        </span>
      {/each}

      {#each slotChoices as choice (choice.key)}
        <button
          type="button" class="slot-choice" data-vfo-tile data-vfo-select
          data-vfo-receiver={choice.receiver === 'sub' ? 'SUB' : 'MAIN'}
          data-vfo-slot={choice.slot} data-vfo-active={choice.active}
          data-vfo-active-slot={choice.activeSlot} data-vfo-tx-target={choice.txTarget}
          disabled={choice.disabled} title={choice.reason}
          onclick={() => onSelectSlot?.(choice.key)}
        >
          <span class="vfo-role">{choice.label}</span>
          <span class="vfo-freq">{choice.frequencyText}</span>
        </button>
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

  .panel-meter > div { width: 100%; height: 100%; }

  .stale-cue { visibility: hidden; inline-size: 1ch; font-size: 10px; }
  .stale-cue.stale { visibility: visible; }

  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }

  .slot-choice {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
    padding: 1px 4px;
    border: 1px solid var(--v2-border-soft);
    border-radius: 3px;
    background: transparent;
    color: var(--v2-text-muted);
    font: inherit;
    cursor: pointer;
  }

  .slot-choice:disabled { opacity: 0.55; cursor: not-allowed; }
  .slot-choice .vfo-role { font-size: 8px; }
  .slot-choice .vfo-freq { font-size: 9px; }

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
