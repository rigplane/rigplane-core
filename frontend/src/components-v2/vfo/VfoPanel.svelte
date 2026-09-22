<script module lang="ts">
  let sequence = 0;
</script>

<script lang="ts">
  import type { Snippet } from 'svelte';
  import LinearSMeter from '../meters/LinearSMeter.svelte';
  import FrequencyDisplayInteractive from '../../primitives/frequency/FrequencyDisplayInteractive.svelte';
  import { splitFrequencyToDigits, groupDigitsForDisplay } from '../../primitives/frequency/frequency-tuning';
  import type { VfoLayoutProfile } from '../layout/vfo-layout-tokens';
  import type {
    MeterContinuitySession, MeterSourceIdentity,
  } from '../../primitives/meters/meter-ballistics.svelte';

  /** One fixed slot: `text` is the lit label; unlit keeps the slot with its
   *  own label and width, so no sibling ever moves. */
  export interface VfoPanelSlot {
    key: string;
    text: string;
    lit: boolean;
    state?: string;
  }

  export interface VfoPanelAnnunciator extends VfoPanelSlot {
    family: 'amber' | 'red' | 'brown';
    group: 'agc' | 'front' | 'rfg';
    /** Optional legacy-path colour token name; overrides the family tone. */
    color?: string;
  }

  export interface VfoPanelDspChip extends VfoPanelSlot {
    key: 'nb' | 'nr' | 'notch';
  }

  /** Everything the panel screen draws besides the readout, the meter and
   *  the slot choices; built by `VfoSurface` (semantic) or the legacy
   *  adapter, never invented here. */
  export interface VfoPanelSections {
    tray: { band?: VfoPanelSlot; ant?: VfoPanelSlot; bw?: VfoPanelSlot };
    annunciators: VfoPanelAnnunciator[];
    dsp: VfoPanelDspChip[];
    under: { rit?: VfoPanelSlot; xit?: VfoPanelSlot; split?: VfoPanelSlot };
    tx: { lit: boolean; state: string };
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
    slotTag?: string;
    frequency?: Snippet;
    freq?: number | null;
    displayHz?: number | null;
    pendingDisplayHz?: number | null;
    frequencyState?: 'current' | 'stale' | 'unknown' | 'unsupported';
    contextKey?: string;
    frequencyDisabled?: boolean;
    controlsDisabled?: boolean;
    /** Tri-state: a string is known text, null is unread (dim label),
     *  undefined is structurally unsupported (slot kept, chip not drawn). */
    mode: string | null | undefined;
    filter: string | null | undefined;
    sMeter?: Snippet;
    sValue?: number | null;
    meterPresent?: boolean;
    meterOperational?: boolean;
    meterSource?: MeterSourceIdentity | null;
    continuitySession?: MeterContinuitySession | null;
    isActive: boolean;
    sections: VfoPanelSections;
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
    sections, slotChoices = [], reserveMeterSpace = false,
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
    if (hz === null || hz === undefined || !Number.isFinite(hz)) return '';
    const groups = groupDigitsForDisplay(splitFrequencyToDigits(hz));
    return [groups.mhz, groups.khz, groups.hz]
      .map((group) => group.map((digit) => digit.char).join('')).join('.');
  }

  /** A legacy badge colour is either a token NAME (mapped to the theme's
   *  badge text token) or an already-resolved colour literal from a custom
   *  theme (hex/rgb/hsl/var(...)), passed through unchanged. */
  function legacyLampColor(color: string): string {
    if (color === 'white') return 'var(--v2-text-primary, #ffffff)';
    if (color === 'muted') return 'var(--v2-badge-inactive-text)';
    if (/^(#[0-9a-f]{3,8}|rgba?\(|hsla?\(|var\(|oklch\(|lab\(|color-mix\(|currentcolor$)/i.test(color.trim())) {
      return color.trim();
    }
    return `var(--v2-badge-${color}-text)`;
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
  <div class="tray" data-vfo-row="tray">
    {#if sections.tray.band}
      <span class="tab" data-tray-tab="band" data-lit={sections.tray.band.lit}
        data-state={sections.tray.band.state}>{sections.tray.band.text}</span>
    {/if}
    {#if sections.tray.ant}
      <span class="tab tab-ant" data-tray-tab="ant" data-indicator-fact="ant"
        data-lit={sections.tray.ant.lit} data-state={sections.tray.ant.state}>{sections.tray.ant.text}</span>
    {/if}
    {#if sections.tray.bw}
      <span class="tab" data-tray-tab="bw" data-lit={sections.tray.bw.lit}
        data-state={sections.tray.bw.state}>{sections.tray.bw.text}</span>
    {/if}
  </div>

  <div class="receiver-row" data-vfo-row="receiver">
    <button
      type="button" class="panel-header" disabled={onSelectHeader === undefined}
      aria-label={onSelectHeader ? `Select ${receiverLabel}` : undefined}
      title={headerReason}
      onclick={onSelectHeader}
    >
      <span class="vfo-label">{receiverLabel}</span>
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
      {#if mode === undefined}
        <span class="chip-slot" aria-hidden="true"></span>
      {:else}
        <span class="chip-lg" data-family="primary-neon" data-chip="mode" data-lit={mode !== null}>{mode ?? 'MODE'}</span>
      {/if}
    </div>

    {#if filter === undefined}
      <span class="chip-slot" aria-hidden="true"></span>
    {:else}
      <span class="chip-lg" data-family="primary-neon" data-chip="filter" data-lit={filter !== null}>{filter ?? 'FIL'}</span>
    {/if}

    <div class="annunciators">
      {#each sections.annunciators as ann, i (ann.key)}
        {#if i > 0 && ann.group !== sections.annunciators[i - 1].group}<i class="ann-sep"></i>{/if}
        <span class="lamp" data-family={ann.family} data-chip={ann.key} data-lit={ann.lit}
          style={ann.color ? `--vfo-lamp-color: ${legacyLampColor(ann.color)}` : undefined}
          {...(ann.key === 'rfg' ? { 'data-indicator-fact': 'rfg' } : {})}>{ann.text}</span>
      {/each}
    </div>

    <span class="row-spacer"></span>

    <span class="chip-lg chip-tx" data-family="tx" data-chip="tx" data-indicator-fact="tx"
      data-lit={sections.tx.lit} data-state={sections.tx.state}>TX</span>

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

  <div class="display-row" data-vfo-row="main">
    <div class="freq-col">
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

      <div class="under-row" data-vfo-row="under">
        {#if sections.under.rit}
          <span class="chip-amber" data-family="amber" data-chip="rit" data-indicator-fact="rit"
            data-lit={sections.under.rit.lit} data-state={sections.under.rit.state}>{sections.under.rit.text}</span>
        {/if}
        {#if sections.under.xit}
          <span class="chip-amber" data-family="amber" data-chip="xit" data-indicator-fact="xit"
            data-lit={sections.under.xit.lit} data-state={sections.under.xit.state}>{sections.under.xit.text}</span>
        {/if}
        {#if sections.under.split}
          <span class="chip-amber" data-family="amber" data-chip="split"
            data-lit={sections.under.split.lit} data-state={sections.under.split.state}>{sections.under.split.text}</span>
        {/if}
      </div>
    </div>

    {#if sections.dsp.length > 0}
      <div class="dsp" data-vfo-row="dsp">
        {#each sections.dsp as chip (chip.key)}
          <span class="chip-dsp" data-family="dsp" data-chip={chip.key} data-lit={chip.lit}>{chip.text}</span>
        {/each}
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
</div>

<style>
  /* The screen's colours resolve through the --dl-vfo- design-language
     tokens, one root block per language in each language stylesheet; the
     literals below are the approved MOR-2509 palette, the fallback when no
     language defines a block. The dim variants swap per element, never
     through a whole-panel filter, and the meter reads none of them. */
  .panel {
    --vfo-deck-rhythm: var(--dl-vfo-rhythm, 1);
    --vfo-deck-rhythm-narrow: var(--dl-vfo-rhythm-narrow, calc(var(--vfo-deck-rhythm) * 0.78));
    --vfo-neon-fill: var(--dl-vfo-primary-neon, #1a63e0);
    --vfo-neon-frame: var(--dl-vfo-frame, #ffffff);
    --vfo-neon-text: var(--dl-vfo-frame, #ffffff);
    --vfo-ant-fill: var(--dl-vfo-red, #e2362c);
    --vfo-ant-frame: var(--dl-vfo-tab-frame, #ffffff);
    --vfo-ant-text: var(--dl-vfo-frame, #ffffff);
    --vfo-front-red: var(--dl-vfo-red-text, var(--dl-vfo-red, #e2362c));
    --vfo-tx-fill: var(--dl-vfo-red, #e2362c);
    --vfo-tx-frame: var(--dl-vfo-frame, #ffffff);
    --vfo-amber-text: var(--dl-vfo-amber-text, var(--dl-vfo-amber, #ffd47a));
    --vfo-amber-chip-text: var(--dl-vfo-amber-chip-text, var(--dl-vfo-amber, #ffd47a));
    --vfo-amber-chip-border: var(--dl-vfo-amber-border, #e0a030);
    --vfo-amber-chip-fill: var(--dl-vfo-amber-fill, #3a2a0c);
    --vfo-brown-text: var(--dl-vfo-brown-text, var(--dl-vfo-brown, #e09a4a));
    --vfo-slate-fill: var(--dl-vfo-slate, #5b6a79);
    --vfo-slate-frame: var(--dl-vfo-tab-frame, #ffffff);
    --vfo-slate-text: var(--dl-vfo-frame, #ffffff);
    --vfo-dsp-text: var(--dl-vfo-dsp, #dbe3ea);
    --vfo-dsp-border: var(--dl-vfo-dsp-border, #aab6c2);
    --vfo-dsp-fill: var(--dl-vfo-dsp-fill, #141a21);
    --vfo-unlit-text: var(--dl-vfo-unlit-text, var(--v2-text-muted, #5a6875));
    --vfo-unlit-border: var(--dl-vfo-unlit-border, #1f2832);
    --vfo-unlit-fill: var(--dl-vfo-unlit-fill, rgba(255, 255, 255, 0.012));
    --vfo-frequency-glow: var(--dl-vfo-frequency-glow, none);
    --vfo-primary-glow: var(--dl-vfo-primary-glow, none);
    --vfo-red-glow: var(--dl-vfo-red-glow, none);
    --vfo-amber-glow: var(--dl-vfo-amber-glow, none);
    --vfo-brown-glow: var(--dl-vfo-brown-glow, none);
    --vfo-dsp-glow: var(--dl-vfo-dsp-glow, none);
    --vfo-slate-glow: var(--dl-vfo-slate-glow, none);
    --v2-meter-lit-filter: var(--dl-vfo-meter-lit-filter, none);
    --vfo-large-chip-width: 70px;
    --vfo-panel-narrow-breakpoint: 480px;

    display: grid;
    grid-template-rows: auto auto auto;
    align-content: start;
    gap: var(--vfo-panel-body-gap, 4px);
    min-height: 100%;
    min-width: 0;
    container-type: inline-size;
    position: relative;
    background: var(--dl-vfo-panel-background, linear-gradient(180deg, #0b0f14 0%, #0a0e13 38%, #070a0e 100%));
    border: 1px solid var(--v2-border-darker);
    border-radius: var(--vfo-panel-radius, 10px);
    box-shadow: var(--dl-vfo-panel-shadow, inset 0 1px 0 rgba(255, 255, 255, 0.07), inset 0 -18px 30px rgba(0, 0, 0, 0.45), 0 6px 18px rgba(0, 0, 0, 0.5));
    padding-block-end: calc(19px * var(--vfo-deck-rhythm));
    font-family: 'Roboto Mono', monospace;
    transition: border-color 150ms ease, box-shadow 150ms ease;
  }

  .panel.active {
    border-color: var(--receiver-control-border);
    box-shadow:
      var(--dl-vfo-panel-shadow, inset 0 1px 0 rgba(255, 255, 255, 0.07), inset 0 -18px 30px rgba(0, 0, 0, 0.45), 0 6px 18px rgba(0, 0, 0, 0.5)),
      inset 0 0 0 1px var(--receiver-control-glow),
      0 0 12px 1px var(--receiver-panel-glow-outer);
  }

  .panel::before {
    content: '';
    position: absolute;
    inset: 0;
    z-index: -1;
    pointer-events: none;
    border-radius: inherit;
    clip-path: inset(0 round var(--vfo-panel-radius, 10px));
    background: var(--dl-vfo-panel-sheen, none);
    mix-blend-mode: var(--dl-vfo-panel-sheen-blend, normal);
  }

  /* The inactive receiver is quieter through explicit colour variants; a lit
     TX alarm keeps the full red, and the meter keeps its own paint. */
  .panel:not(.active) {
    --vfo-neon-fill: var(--dl-vfo-primary-neon-dim, #16396f);
    --vfo-neon-frame: var(--dl-vfo-frame-dim, #9fb0c4);
    --vfo-neon-text: var(--dl-vfo-dim-text, #e3eaf2);
    --vfo-ant-fill: var(--dl-vfo-red-dim, #8e2a24);
    --vfo-ant-frame: var(--dl-vfo-tab-frame-dim, #b9c4cf);
    --vfo-ant-text: var(--dl-vfo-dim-text, #e3eaf2);
    --vfo-front-red: var(--dl-vfo-red-text-dim, #c85e56);
    --vfo-amber-text: var(--dl-vfo-amber-dim, #b89a5e);
    --vfo-amber-chip-text: var(--dl-vfo-amber-chip-text-dim, var(--dl-vfo-amber-dim, #b89a5e));
    --vfo-amber-chip-border: var(--dl-vfo-amber-border-dim, #7a6232);
    --vfo-amber-chip-fill: var(--dl-vfo-amber-fill-dim, #241b0c);
    --vfo-brown-text: var(--dl-vfo-brown-dim, #a5763f);
    --vfo-slate-fill: var(--dl-vfo-slate-dim, #46525e);
    --vfo-slate-frame: var(--dl-vfo-tab-frame-dim, #b9c4cf);
    --vfo-slate-text: var(--dl-vfo-dim-text, #e3eaf2);
    --vfo-dsp-text: var(--dl-vfo-dsp-dim, #aab4bf);
    --vfo-dsp-border: var(--dl-vfo-dsp-border-dim, #6f7b87);
    --vfo-frequency-glow: none;
    --vfo-primary-glow: none;
    --vfo-red-glow: none;
    --vfo-amber-glow: none;
    --vfo-brown-glow: none;
    --vfo-dsp-glow: none;
    --vfo-slate-glow: none;
    --v2-meter-lit-filter: none;
  }

  /* ── Tray: tabs hanging from the panel's top edge ─────────────────── */
  .tray {
    display: flex;
    justify-content: center;
    gap: 3px;
    margin-block-end: calc(17px * var(--vfo-deck-rhythm));
    min-width: 0;
  }

  .tab {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    width: 64px;
    height: 20px;
    padding: 0 6px;
    border: 1px solid var(--vfo-slate-frame);
    border-top: 0;
    border-radius: 0 0 4px 4px;
    background: var(--vfo-slate-fill);
    color: var(--vfo-slate-text);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .tab[data-tray-tab='band'] { width: 56px; }
  .tab[data-tray-tab='bw'] { width: 84px; }

  .tab-ant {
    background: var(--vfo-ant-fill);
    border-color: var(--vfo-ant-frame);
    color: var(--vfo-ant-text);
  }

  .tab[data-lit='false'] {
    border-color: var(--vfo-unlit-border);
    color: var(--vfo-unlit-text);
    background: var(--vfo-unlit-fill);
    font-weight: 400;
  }

  .tab[data-lit='true'] { box-shadow: var(--vfo-slate-glow); }
  .tab-ant[data-lit='true'] { box-shadow: var(--vfo-red-glow); }

  /* ── Receiver row: name once, large mode/filter chips, lamps, TX ───── */
  .receiver-row {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-block-end: calc(16px * var(--vfo-deck-rhythm));
    padding: 0 var(--vfo-panel-body-pad-x, 10px);
    min-width: 0;
  }

  .panel-header {
    display: flex;
    align-items: center;
    border: 0;
    padding: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: inherit;
    cursor: pointer;
    flex: none;
  }

  .panel-header:disabled { cursor: default; }

  .vfo-label {
    color: var(--v2-text-secondary);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .mode-badge-wrapper {
    cursor: pointer;
    display: inline-flex;
  }

  .mode-badge-wrapper.mode-disabled { cursor: default; }

  .mode-badge-wrapper:hover :global(.chip-lg) {
    filter: brightness(1.15);
  }

  .chip-slot {
    display: inline-flex;
    flex: none;
    width: var(--vfo-large-chip-width);
    height: 30px;
  }

  .chip-lg {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    flex: none;
    width: var(--vfo-large-chip-width);
    height: 30px;
    border: 1px solid var(--vfo-neon-frame);
    border-radius: 4px;
    background: var(--vfo-neon-fill);
    color: var(--vfo-neon-text);
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.03em;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .chip-lg[data-lit='false'] {
    border-color: var(--vfo-unlit-border);
    color: var(--vfo-unlit-text);
    background: var(--vfo-unlit-fill);
  }

  .chip-lg[data-lit='true'] { box-shadow: var(--vfo-primary-glow); }

  .chip-tx { width: 50px; }

  .chip-tx[data-lit='true'] {
    background: var(--vfo-tx-fill);
    border-color: var(--vfo-tx-frame);
    color: var(--vfo-tx-frame);
    font-weight: 700;
    box-shadow: var(--vfo-red-glow);
  }

  .annunciators {
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
  }

  .lamp {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    flex: none;
    width: 72px;
    height: 22px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.03em;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    color: var(--vfo-lamp-color, var(--vfo-front-red));
  }

  .lamp[data-chip='agc'] { width: 76px; --vfo-lamp-color: var(--vfo-amber-text); }
  .lamp[data-chip='preamp'] { width: 78px; --vfo-lamp-color: var(--vfo-front-red); }
  .lamp[data-chip='att'] { width: 56px; --vfo-lamp-color: var(--vfo-front-red); }
  .lamp[data-chip='ip-plus'] { width: 44px; --vfo-lamp-color: var(--vfo-front-red); }
  .lamp[data-chip='digi-sel'] { width: 82px; --vfo-lamp-color: var(--vfo-front-red); }
  .lamp[data-chip='rfg'] { width: 78px; --vfo-lamp-color: var(--vfo-brown-text); }

  .lamp[data-lit='false'] {
    color: var(--vfo-unlit-text);
    font-weight: 400;
  }

  .lamp[data-lit='true'] { text-shadow: var(--vfo-red-glow); }
  .lamp[data-chip='agc'][data-lit='true'] { text-shadow: var(--vfo-amber-glow); }
  .lamp[data-chip='rfg'][data-lit='true'] { text-shadow: var(--vfo-brown-glow); }

  .ann-sep {
    flex: none;
    width: 1px;
    height: 16px;
    margin: 0 3px;
    background: var(--v2-border-panel, #26303a);
  }

  .row-spacer { flex: 1 1 auto; min-width: 0; }

  /* ── Main row: readout + under chips, DSP column, meter slot ──────── */
  .display-row {
    display: grid;
    grid-template-columns: auto 66px minmax(0, 1fr);
    gap: 12px;
    align-items: start;
    padding: 0 var(--vfo-panel-body-pad-x, 10px);
    min-width: 0;
  }

  .freq-col { display: flex; flex-direction: column; min-width: 0; }

  .freq-row {
    display: flex;
    align-items: center;
    flex: 0 0 auto;
    min-width: 0;
  }

  .vfo-freq {
    inline-size: 6.1em;
    max-inline-size: 100%;
    font-size: clamp(26px, 4.4cqw, 38px);
    line-height: 1.15;
    font-weight: var(--dl-vfo-frequency-weight, 800);
    font-variant-numeric: tabular-nums;
    letter-spacing: var(--vfo-frequency-letter-spacing, 0.02em);
  }

  .freq-row > .vfo-freq { text-shadow: var(--vfo-frequency-glow); }

  .frequency-readout-content { display: contents; }

  .vfo-freq.display-unknown { color: var(--vfo-unlit-text); }
  .vfo-freq.display-unknown :global(.freq) { text-shadow: none; }

  .vfo-freq[role='button'], .vfo-freq[role='button'] :global(.digit) { cursor: pointer; }
  /* No font-weight here on purpose: the interactive primitive resolves its
     own weight through --freq-font-weight -> --v2-vfo-font-weight ->
     --dl-vfo-frequency-weight, and an inherited shorthand would shadow that
     chain with the wrapper's value. */
  .vfo-freq :global(.freq.interactive) {
    font-family: inherit;
    font-size: inherit;
    line-height: inherit;
    font-variant-numeric: inherit;
    letter-spacing: inherit;
    color: inherit;
    text-shadow: inherit;
  }

  .freq-row :global(.sep) {
    opacity: 0.62;
    margin: 0 0.03em;
  }

  .under-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-block-start: calc(14px * var(--vfo-deck-rhythm));
    min-width: 0;
  }

  .chip-amber {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    flex: none;
    width: 76px;
    height: 22px;
    border: 1px solid var(--vfo-amber-chip-border);
    border-radius: 4px;
    background: var(--vfo-amber-chip-fill);
    color: var(--vfo-amber-chip-text);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.03em;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .chip-amber[data-chip='rit'] { width: 92px; }
  .chip-amber[data-chip='xit'] { width: 64px; }
  .chip-amber[data-chip='split'] { width: 76px; }

  .chip-amber[data-lit='false'] {
    border-color: var(--vfo-unlit-border);
    color: var(--vfo-unlit-text);
    background: var(--vfo-unlit-fill);
    font-weight: 400;
  }

  .chip-amber[data-lit='true'] { box-shadow: var(--vfo-amber-glow); }

  .dsp {
    display: grid;
    grid-template-columns: 1fr;
    gap: calc(9px * var(--vfo-deck-rhythm));
    align-self: start;
    min-width: 0;
  }

  .chip-dsp {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    width: 100%;
    height: 22px;
    border: 1px solid var(--vfo-dsp-border);
    border-radius: 4px;
    background: var(--vfo-dsp-fill);
    color: var(--vfo-dsp-text);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.03em;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .chip-dsp[data-lit='false'] {
    border-color: var(--vfo-dsp-border);
    background: var(--vfo-unlit-fill);
    color: var(--vfo-unlit-text);
    opacity: 1;
  }

  .chip-dsp[data-lit='true'] { box-shadow: var(--vfo-dsp-glow); }

  .panel-meter {
    grid-column: 3;
    flex: 1 1 auto;
    min-width: 0;
    padding: 0 var(--vfo-panel-meter-pad-x, 6px);
    border-radius: 4px;
    background: var(--dl-vfo-meter-well-background, #05070a);
    box-shadow: var(--dl-vfo-meter-well-shadow, inset 0 2px 6px rgba(0, 0, 0, 0.9), inset 0 0 0 1px #1a222b, 0 1px 0 rgba(255, 255, 255, 0.05));
  }

  .panel-meter > div { width: 100%; }

  /* ── Container-query modes on the panel's own inline size ─────────── */
  @container (min-width: 760px) {
    .display-row { grid-template-columns: auto 150px minmax(0, 1fr); }
    .dsp { grid-template-columns: 1fr 1fr; }
  }

  @container (max-width: 480px) {
    .chip-slot, .chip-lg { --vfo-large-chip-width: 62px; }
    .tray { margin-block-end: calc(17px * var(--vfo-deck-rhythm-narrow)); }
    .receiver-row {
      margin-block-end: calc(16px * var(--vfo-deck-rhythm-narrow));
      gap: 4px;
    }
    .under-row { margin-block-start: calc(14px * var(--vfo-deck-rhythm-narrow)); }
    .dsp { gap: calc(9px * var(--vfo-deck-rhythm-narrow)); }
    .display-row { row-gap: calc(10px * var(--vfo-deck-rhythm-narrow)); }
    .smeter-row { grid-column: 1 / -1; }
    .vfo-label { letter-spacing: 0.06em; }
    .lamp { width: 58px; }
    .lamp[data-chip='agc'] { width: 62px; }
    .lamp[data-chip='preamp'] { width: 62px; }
    .lamp[data-chip='digi-sel'] { width: 66px; }
    .chip-lg { font-size: 13px; }
    .chip-tx { width: 44px; }
  }

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
</style>
