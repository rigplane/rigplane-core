<script module lang="ts">
  let sequence = 0;
</script>

<script lang="ts">
  import type { Snippet } from 'svelte';
  import LinearSMeter from '../meters/LinearSMeter.svelte';
  import FrequencyDisplayInteractive from '../../primitives/frequency/FrequencyDisplayInteractive.svelte';
  import { StatusIndicator } from '$lib/Button';
  import { splitFrequencyToDigits, groupDigitsForDisplay } from '../../primitives/frequency/frequency-tuning';
  import { formatRitOffset } from './vfo-utils';
  import type { VfoLayoutProfile } from '../layout/vfo-layout-tokens';
  import type {
    MeterContinuitySession, MeterSourceIdentity,
  } from '../../primitives/meters/meter-ballistics.svelte';

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
    frequency?: Snippet;
    freq?: number | null;
    displayHz?: number | null;
    pendingDisplayHz?: number | null;
    frequencyState?: 'current' | 'stale' | 'unknown' | 'unsupported';
    contextKey?: string;
    frequencyDisabled?: boolean;
    controlsDisabled?: boolean;
    mode: string | null;
    filter: string | null;
    sMeter?: Snippet;
    sValue?: number | null;
    meterPresent?: boolean;
    meterOperational?: boolean;
    meterSource?: MeterSourceIdentity | null;
    continuitySession?: MeterContinuitySession | null;
    isActive: boolean;
    badgeItems: readonly VfoPanelBadge[];
    bandText?: string | null;
    rit?: { active: boolean; offset: number };
    slotChoices?: readonly VfoPanelSlotChoice[];
    /** Keep peer cards aligned while only the active VFO owns the real meter. */
    reserveMeterSpace?: boolean;
    layoutProfile?: VfoLayoutProfile;
    onModeClick?: () => void;
    onFreqChange?: (freq: number) => void;
    /** Opens the frequency-entry overlay for this card. */
    onFrequencyClick?: (trigger: HTMLElement) => void;
    onSelectSlot?: (key: string) => void;
    /** Selects this VFO from its header; frequency gestures remain independent. */
    onSelectHeader?: () => void;
    headerReason?: string;
  }

  let {
    receiver, receiverLabel, slotTag, frequency, freq, displayHz, pendingDisplayHz = null,
    frequencyState = 'current', contextKey, frequencyDisabled = false, controlsDisabled = false,
    mode, filter, sMeter, sValue, meterPresent = true, meterOperational, meterSource, continuitySession,
    isActive,
    badgeItems, bandText, rit, slotChoices = [], reserveMeterSpace = false,
    layoutProfile = 'baseline',
    onModeClick,
    onFreqChange,
    onFrequencyClick,
    onSelectSlot,
    onSelectHeader,
    headerReason,
  }: Props = $props();

  let meterVariant = $derived(layoutProfile === 'wide' ? 'vfo-wide' : 'vfo');
  let frequencyEntryButton = $derived(onFrequencyClick !== undefined
    && (frequencyState === 'current' || frequencyState === 'stale')
    && freq !== null && freq !== undefined && Number.isFinite(freq));
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

  function handleFrequencyClick(event: MouseEvent): void {
    if (onFrequencyClick === undefined || !(event.target instanceof Element)) return;
    const readout = event.target.closest('.freq');
    if (readout === null || !(event.currentTarget instanceof HTMLElement)
      || !event.currentTarget.contains(readout)) return;
    const trigger = frequencyEntryButton ? event.currentTarget : readout;
    if (trigger instanceof HTMLElement) {
      if (frequencyEntryButton) event.stopPropagation();
      onFrequencyClick(trigger);
    }
  }

  function handleFrequencyKeydown(event: KeyboardEvent): void {
    if (!frequencyEntryButton || onFrequencyClick === undefined
      || (event.key !== 'Enter' && event.key !== ' ')
      || !(event.currentTarget instanceof HTMLElement)) return;
    event.preventDefault();
    event.stopPropagation();
    onFrequencyClick(event.currentTarget);
  }
</script>

<div
  class="panel"
  class:active={isActive}
  data-layout-profile={layoutProfile}
  style={Object.entries(receiverChromeVars).map(([key, value]) => `${key}:${value}`).join(';')}
>
  <div class="panel-identity">
    <button
      type="button" class="panel-header" disabled={onSelectHeader === undefined}
      aria-label={onSelectHeader ? `Select ${receiverLabel}` : undefined}
      title={headerReason}
      onclick={onSelectHeader}
    >
      <div class="header-title-group">
        <span class="vfo-label">{receiverLabel}</span>
      </div>

      <div class="header-badges">
        {#if sMeter || meterPresent}<span class="header-tag meter-tag">BAR</span>{/if}
        <span class="header-tag slot-tag">{slotTag}</span>
      </div>
    </button>

    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="mode-badge-wrapper"
      class:mode-disabled={controlsDisabled || onModeClick === undefined}
      data-vfo-controls-disabled={controlsDisabled}
      aria-disabled={controlsDisabled || onModeClick === undefined}
      onclick={(e) => {
        e.stopPropagation();
        if (!controlsDisabled) onModeClick?.();
      }}
      title={!controlsDisabled && onModeClick ? `Change mode (current: ${mode})` : undefined}
    >
      <StatusIndicator
        label={mode ?? '—'}
        active={true}
        color="cyan"
        size="default"
      />
    </div>

    <StatusIndicator label={filter ?? '—'} active={filter !== null} color={filter === null ? 'muted' : 'cyan'} size="default" />
  </div>

  <div class="display-row">
    <div class="freq-row">
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <span class="vfo-freq" data-vfo-freq data-freq-tunable={!frequencyDisabled}
        data-display-state={frequencyState} class:display-unknown={displayHz === null}
        role={frequencyEntryButton ? 'button' : undefined}
        aria-label={frequencyEntryButton ? `Set frequency — ${receiverLabel}` : undefined}
        tabindex={frequencyEntryButton ? 0 : undefined}
        onclickcapture={handleFrequencyClick}
        onkeydowncapture={handleFrequencyKeydown}
        >
        <span class="frequency-readout-content" aria-hidden={frequencyEntryButton ? 'true' : undefined}>
          {#if frequency}
            {@render frequency()}
          {:else if freq !== null && freq !== undefined && Number.isFinite(freq)}
            <FrequencyDisplayInteractive
              {freq} {displayHz} {pendingDisplayHz} {contextKey}
              disabled={frequencyDisabled
                || (frequencyState !== 'current' && frequencyState !== 'stale')}
              active={isActive} {receiver} {onFreqChange} vfoFreqHook={false}
            />
          {:else}
            <span class="freq unknown-frequency">{formatFrequency(pendingDisplayHz ?? displayHz)}</span>
          {/if}
        </span>
      </span>
    </div>

    {#if rit?.active}
      <div class="rit-row">
        <span class="rit-label">RIT</span>
        <span class="rit-offset">{formatRitOffset(rit.offset)}</span>
      </div>
    {/if}

    <div class="smeter-row panel-meter"
      data-meter-space={!sMeter && !meterPresent && reserveMeterSpace ? 'reserved' : undefined}>
      {#if sMeter}
        <div data-testid="receiver-s-meter" data-receiver={receiver}>
          {@render sMeter()}
        </div>
      {:else if meterPresent}
        <div data-testid="receiver-s-meter" data-receiver={receiver}
          data-operational={meterOperational === undefined ? undefined : String(meterOperational)}
          aria-label={sValue === null ? `${receiverLabel} S meter unknown` : undefined}>
          <LinearSMeter value={typeof sValue === 'number' && Number.isFinite(sValue) ? sValue : null} compact label={slotTag} variant={meterVariant} source={meterSource} session={continuitySession} />
        </div>
      {/if}
    </div>
  </div>

    <div class="control-strip">
      <StatusIndicator label={slotTag} active={false} color="muted" size="default" />

      {#if bandText}
        <StatusIndicator label={bandText} active={true} color="cyan" size="default" />
      {/if}

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
          aria-describedby={choice.reason ? `${staleId}-choice-${choice.key}` : undefined}
          onclick={() => onSelectSlot?.(choice.key)}
        >
          <span class="vfo-role">{choice.label}</span>
          <span class="vfo-freq">{choice.frequencyText}</span>
        </button>
        {#if choice.reason}
          <span id={`${staleId}-choice-${choice.key}`} class="sr-only">{choice.reason}</span>
        {/if}
      {/each}
    </div>
</div>

<style>
  .panel {
    display: grid;
    grid-template-rows: auto auto auto;
    gap: var(--vfo-panel-body-gap, 4px);
    min-height: 100%;
    min-width: 0;
    background: linear-gradient(180deg, var(--v2-bg-gradient-start) 0%, var(--v2-bg-darkest) 100%);
    border: 1px solid var(--v2-border-darker);
    border-radius: 4px;
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

  /* MOR-2509: the identity row is one line — panel name plus the header's
     own tags plus the mode/filter chips the control strip used to carry.
     `VfoSurface.panel-rows.test.ts` pins membership and row order. */
  .panel-identity {
    display: flex;
    align-items: center;
    gap: var(--vfo-header-group-gap, 5px);
    padding: var(--vfo-badge-inset-y, 3px) var(--vfo-panel-pad-x, 10px) 0;
    min-width: 0;
  }

  .panel-header {
    display: flex;
    align-items: center;
    gap: var(--vfo-header-badge-gap, 3px);
    border: 0;
    padding: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: inherit;
    cursor: pointer;
  }

  .panel-header:disabled { cursor: default; }

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

  /* MOR-2509: the meter shares the display row with the readout — the
     readout keeps its fixed em-sized share, the meter takes the rest. */
  .panel-meter {
    flex: 1 1 auto;
    min-width: 0;
    padding: 0 var(--vfo-panel-meter-pad-x, 6px);
  }

  .panel-meter > div { width: 100%; }


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

  .slot-choice:first-of-type { margin-inline-start: auto; }

  .slot-choice:disabled { opacity: 0.55; cursor: not-allowed; }
  .slot-choice .vfo-role { font-size: 8px; }
  .control-strip .slot-choice .vfo-freq { font-size: 9px; }

  .mode-badge-wrapper {
    cursor: pointer;
    display: inline-flex;
  }

  .mode-badge-wrapper.mode-disabled { cursor: default; }

  .mode-badge-wrapper:hover :global(.v2-status-indicator) {
    filter: brightness(1.15);
  }



  .display-row {
    display: flex;
    align-items: center;
    gap: var(--vfo-display-row-gap, 12px);
    padding: 0 var(--vfo-panel-body-pad-x, 10px);
    min-width: 0;
  }

  .freq-row {
    display: flex;
    align-items: center;
    flex: 0 0 auto;
    min-width: 0;
  }

  .vfo-freq {
    inline-size: 6.1em;
    max-inline-size: 100%;
    font-size: var(--vfo-frequency-size, 24px);
    letter-spacing: var(--vfo-frequency-letter-spacing, 0.03em);
  }

  .frequency-readout-content { display: contents; }

  .vfo-freq[role='button'], .vfo-freq[role='button'] :global(.digit) { cursor: pointer; }
  .vfo-freq[role='button']:focus-visible {
    outline: 2px solid var(--v2-accent-cyan-bright);
    outline-offset: 3px;
    border-radius: 4px;
  }

  .vfo-freq :global(.freq.interactive) {
    font: inherit;
    font-variant-numeric: inherit;
    letter-spacing: inherit;
    color: inherit;
    text-shadow: inherit;
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

  /* MOR-2509: the indicator row wraps instead of being clipped — pinned by
     `VfoSurface.panel-rows.test.ts`'s control-strip source pin. */
  .control-strip {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--vfo-control-strip-gap, 4px);
    padding:
      0
      var(--vfo-panel-body-pad-x, 10px)
      var(--vfo-panel-body-pad-bottom, 0px);
    min-width: 0;
  }

  @media (max-width: 1280px) {
    .vfo-freq {
      font-size: 44px;
    }
  }

  @media (max-width: 1024px) {
    .display-row {
      flex-wrap: wrap;
      align-items: flex-start;
    }

    .vfo-freq {
      font-size: 32px;
    }
  }
</style>
