<!--
  MOR-2299 slice 1 — pure presentation for one receiver-addressed indicator
  entry. The component reads only the semantic contract handed to it. Shared
  ANT/TUNE/RIT/XIT facts and DUAL actions belong to slice 2.
-->
<script module lang="ts">
  // MOR-2644 correction 3 (owner rule 2026-09-21: the layout never moves):
  // every fact reserves the width of its widest lit text, the same way RFG
  // does (`min-inline-size` in `ch`, `box-sizing: content-box`). Unread,
  // known and changing values then all occupy the same box. Widest texts
  // come from what this component can render for that fact: the option
  // vocabulary the view model carries, ON/OFF for booleans, the offset range
  // for RIT/XIT. None of these vocabularies reach this component as props
  // (agcModes, attValues/preValues, antenna count live upstream), so the
  // table uses each vocabulary's widest possible rendered text instead of a
  // per-radio width:
  // - AGC: verbatim capability label, longest in the profiles is `TUNING`
  //   (verbatim and unbounded per-radio, so this covers the widest known
  //   one). The dict says which vocabulary is missing: AGC label set
  //   (agcModes/agcLabels).
  // - NOTCH: the widest rendered state text `NOTCH MANUAL` (the component
  //   prints the raw mode uppercased: OFF/AUTO/MANUAL).
  // - ATT: `ATT 45 dB` — the largest attenuator step across profiles
  //   (IC-7610 0..45 dB); attValues do not reach this component.
  // - P.AMP: `P.AMP 2` — the largest preamp level across profiles
  //   (0/1/2 everywhere shipped); preValues do not reach this component.
  // - ANT: `ANT 2` — the largest antenna index across profiles
  //   (IC-7610 has two TX ports); the port count does not reach this
  //   component.
  // - BW: `BW 9999 Hz` — the widest filter-width fallback the codebase uses
  //   when no profile bound is present (`FilterPanel`, `RitXitPanel`).
  // - RIT/XIT: `RIT OFF −9999 Hz` — minus sign, OFF, and the endpoint of the
  //   offset range this surface family renders (RitXitScanSurface
  //   OFFSET_MIN/MAX; the local RIT/XIT panel falls back to the same
  //   -9999..9999 bounds when no domain is present).
  export const FACT_SLOT_RESERVATIONS = {
    bandwidth: 'BW 9999 Hz'.length,
    agc: 'AGC TUNING'.length,
    nb: 'NB OFF'.length,
    nr: 'NR OFF'.length,
    notch: 'NOTCH MANUAL'.length,
    attenuator: 'ATT 45 dB'.length,
    preamp: 'P.AMP 2'.length,
    'ip-plus': 'IP+ OFF'.length,
    'digi-sel': 'DIGI-SEL OFF'.length,
    'rf-gain': 8,
    antenna: 'ANT 2'.length,
    atu: 'TUNE TUNING'.length,
    rit: 'RIT OFF −9999 Hz'.length,
    xit: 'XIT OFF −9999 Hz'.length,
  } as const;
</script>

<script lang="ts">
  import type { Snippet } from 'svelte';
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import type { MeterContinuitySession } from '../primitives/meters/meter-ballistics.svelte';
  import { formatKnownLevel, levelFormatsBelowMax } from './format-level';
  import { RF_FRONT_END_LEVELS } from './rf-front-end-instruments';
  import type {
    DisplayObservedField, RadioWideIndicatorsViewModel, ReceiverIndicatorField,
    ReceiverIndicatorViewModel, TxAuxField,
  } from './radio-view-model';

  interface Props {
    indicator?: ReceiverIndicatorViewModel;
    appearance?: 'semantic' | 'sdr' | 'standard';
    children?: Snippet;
    slotLabel?: string;
    radioWide?: RadioWideIndicatorsViewModel;
    continuitySession?: MeterContinuitySession | null;
    sMeter?: Snippet;
  }

  let {
    indicator, radioWide, appearance = 'semantic', children, slotLabel, continuitySession, sMeter,
  }: Props = $props();

  const rfLabel = (state: RadioWideIndicatorsViewModel['rfState']): string =>
    state === 'transmitting' ? 'TX'
      : state === 'uncertain' ? 'TX?'
        : '';

  // MOR-2644: an unread fact shows its label dimmed with no value text —
  // never a dash placeholder. A fact the radio does not have is not drawn
  // at all (the {#if} guards below). The subdued data-state paint stays.
  function numeric(field: ReceiverIndicatorField<number>): string {
    return field.reading.status === 'known' ? String(field.reading.value) : '';
  }

  /** The RF gain the display shows, independent of provenance: the observed
   *  display value when one exists, otherwise the strict reading. */
  function rfGainShown(field: DisplayObservedField<number>): number | null {
    if (field.display) {
      return field.display.state === 'current' || field.display.state === 'stale'
        ? field.display.value : null;
    }
    return field.reading.status === 'known' ? field.reading.value : null;
  }

  /**
   * Owner ruling 2026-09-23: RFG appears only while RF gain is REDUCED.
   * Coordinator decision, same day: "reduced" is what the operator reads —
   * the formatted percentage, the same rounding `formatKnownLevel` applies —
   * so a raw 254 of 255, strictly below 1 yet displaying as 100%, shows
   * nothing. At a displayed 100%, and while the reading is unknown, this
   * renders an empty string: the fact keeps its reserved slot
   * (min-inline-size below) but prints no label, number, or placeholder,
   * draws no frame, and is aria-hidden while empty.
   */
  function rfGainText(field: DisplayObservedField<number>): string {
    const shown = rfGainShown(field);
    return shown !== null
      && levelFormatsBelowMax(shown, RF_FRONT_END_LEVELS[0][2], RF_FRONT_END_LEVELS[0][3])
      ? formatKnownLevel(shown, RF_FRONT_END_LEVELS[0][2], RF_FRONT_END_LEVELS[0][3])
      : '';
  }

  function agc(field: ReceiverIndicatorViewModel['agcMode']): string {
    return field.reading.status === 'known' ? String(field.reading.value) : '';
  }

  function booleanState(field: ReceiverIndicatorField<boolean>): 'on' | 'off' | 'unknown' {
    return field.reading.status === 'known' ? (field.reading.value ? 'on' : 'off') : 'unknown';
  }

  function booleanLabel(field: ReceiverIndicatorField<boolean>): string {
    const state = booleanState(field);
    return state === 'unknown' ? '' : state.toUpperCase();
  }

  function sharedBoolean(field: TxAuxField<boolean>): string {
    return field.reading.status === 'known' ? (field.reading.value ? 'ON' : 'OFF') : '';
  }

  function sharedOffset(offset: TxAuxField<number>): string {
    return offset.reading.status === 'known' ? String(offset.reading.value) : '';
  }

  function sharedAggregate(
    label: 'RIT' | 'XIT', active: TxAuxField<boolean>, offset: TxAuxField<number>,
  ): string {
    // MOR-2644 correction 1: the Hz unit belongs to the offset number and
    // appears only right after a known offset. Both unread → exactly the
    // label; state known, offset unread → "RIT ON"/"RIT OFF"; both known →
    // the same text as main today.
    const state = sharedBoolean(active);
    const value = sharedOffset(offset);
    if (value === '') return state === '' ? label : `${label} ${state}`;
    return state === '' ? `${label} ${value} Hz` : `${label} ${state} ${value} Hz`;
  }

  function aggregateState(
    active: TxAuxField<boolean>, offset: TxAuxField<number>,
  ): 'known' | 'unknown' {
    return active.reading.status === 'known' && offset.reading.status === 'known'
      ? 'known' : 'unknown';
  }

</script>

{#if indicator}
<section
  class="indicator-row"
  data-indicator-appearance={appearance}
  data-testid="vfo-indicator-row"
  data-indicator-receiver={indicator.receiver}
  data-indicator-operational={indicator.availability.operational}
  aria-label={`${indicator.receiver} receiver indicators`}
>
  <header>
    <strong>{indicator.receiver}</strong>
    {#if indicator.bandwidthHz.availability.structural}
      <span
        class="fact"
        data-indicator-fact="bandwidth"
        data-state={indicator.bandwidthHz.reading.status}
      >BW {numeric(indicator.bandwidthHz)}{indicator.bandwidthHz.reading.status === 'known' ? ' Hz' : ''}</span>
    {/if}
    {#if appearance === 'standard'}
      <span class="header-badges"><span class="fact">BAR</span><span
        class="fact"
        data-empty={slotLabel == null ? 'true' : undefined}
      >{slotLabel ?? ''}</span></span>
    {/if}
  </header>

  <div class="s-meter" data-testid="receiver-s-meter" data-receiver={indicator.receiver}>
    {#if indicator.sMeter.reading.status === 'known' && Number.isFinite(indicator.sMeter.reading.value)}
      {#if sMeter}
        {@render sMeter()}
      {:else}
        <LinearSMeter value={indicator.sMeter.reading.value} compact label={appearance === 'standard' ? slotLabel : undefined} variant={appearance === 'sdr' ? 'sdr-screen' : 'vfo-wide'} source={indicator.sMeter.source} session={continuitySession} />
      {/if}
    {:else}
      <div
        class="s-meter-unknown"
        data-testid="receiver-s-meter-unknown"
        role="img"
        aria-label={`${indicator.receiver} S meter`}
      ></div>
    {/if}
  </div>

  {#if children}{@render children()}{/if}

  <div class="facts" aria-label={`${indicator.receiver} receiver facts`}>
    {#if indicator.agcMode.availability.structural}
      <span class="fact" data-indicator-fact="agc" data-state={indicator.agcMode.reading.status}
        >AGC{indicator.agcMode.reading.status === 'known' ? ` ${agc(indicator.agcMode)}` : ''}</span
      >
    {/if}
    {#if indicator.nbActive.availability.structural}
      <span class="fact" data-indicator-fact="nb" data-state={booleanState(indicator.nbActive)}
        >NB{indicator.nbActive.reading.status === 'known' ? ` ${booleanLabel(indicator.nbActive)}` : ''}</span
      >
    {/if}
    {#if indicator.nrActive.availability.structural}
      <span class="fact" data-indicator-fact="nr" data-state={booleanState(indicator.nrActive)}
        >NR{indicator.nrActive.reading.status === 'known' ? ` ${booleanLabel(indicator.nrActive)}` : ''}</span
      >
    {/if}
    {#if indicator.notchMode.availability.structural}
      <span class="fact" data-indicator-fact="notch" data-state={indicator.notchMode.reading.status}
        >NOTCH{indicator.notchMode.reading.status === 'known'
          ? ` ${indicator.notchMode.reading.value.toUpperCase()}`
          : ''}</span
      >
    {/if}
    {#if indicator.attenuator.availability.structural}
      <span class="fact" data-indicator-fact="attenuator" data-state={indicator.attenuator.reading.status}
        >ATT{indicator.attenuator.reading.status === 'known' ? ` ${numeric(indicator.attenuator)} dB` : ''}</span
      >
    {/if}
    {#if indicator.preamp.availability.structural}
      <span class="fact" data-indicator-fact="preamp" data-state={indicator.preamp.reading.status}
        >P.AMP{indicator.preamp.reading.status === 'known' ? ` ${numeric(indicator.preamp)}` : ''}</span
      >
    {/if}
    {#if indicator.ipPlus.availability.structural}
      <span class="fact" data-indicator-fact="ip-plus" data-state={booleanState(indicator.ipPlus)}
        >IP+{indicator.ipPlus.reading.status === 'known' ? ` ${booleanLabel(indicator.ipPlus)}` : ''}</span
      >
    {/if}
    {#if indicator.digiSel.availability.structural}
      <span class="fact" data-indicator-fact="digi-sel" data-state={booleanState(indicator.digiSel)}
        >DIGI-SEL{indicator.digiSel.reading.status === 'known' ? ` ${booleanLabel(indicator.digiSel)}` : ''}</span
      >
    {/if}
    {#if indicator.rfGain.availability.structural && indicator.rfGain.display?.state !== 'unsupported'}
      {@const gainText = rfGainText(indicator.rfGain)}
      <span
        class="fact"
        data-indicator-fact="rf-gain"
        role="img"
        data-state={indicator.rfGain.reading.status}
        data-display-state={indicator.rfGain.display?.state ?? (indicator.rfGain.reading.status === 'known' ? 'current' : 'unknown')}
        aria-label={gainText ? `RF gain ${gainText}` : undefined}
        aria-hidden={gainText ? undefined : 'true'}
        data-empty={gainText ? undefined : 'true'}
      >{gainText ? `RFG ${gainText}` : ''}</span>
    {/if}
  </div>
</section>
{:else if children}
  {@render children()}
{/if}

{#if radioWide}
  <section
    class="indicator-row shared-indicators"
    data-indicator-appearance={appearance}
    data-testid="vfo-shared-indicators"
    aria-label="Radio-wide indicators"
  >
    <div class="facts" aria-label="Radio-wide facts">
      <span
        class:tx={radioWide.rfState === 'transmitting'}
        class="rf-lamp"
        data-indicator-fact="rf-authority"
        data-indicator-rf={radioWide.rfState}
      >{rfLabel(radioWide.rfState)}</span>
      {#if radioWide.antenna.availability.structural}
        <span class="fact" data-indicator-fact="antenna" data-state={radioWide.antenna.reading.status}
          >ANT{radioWide.antenna.reading.status === 'known' ? ` ${sharedOffset(radioWide.antenna)}` : ''}</span
        >
      {/if}
      {#if radioWide.atu.availability.structural}
        <span class="fact" data-indicator-fact="atu" data-state={radioWide.atu.reading.status}
          >TUNE{radioWide.atu.reading.status === 'known'
            ? ` ${radioWide.atu.reading.value.toUpperCase()}`
            : ''}</span
        >
      {/if}
      {#if radioWide.ritActive.availability.structural || radioWide.ritOffset.availability.structural}
        <span class="fact" data-indicator-fact="rit" data-state={aggregateState(radioWide.ritActive, radioWide.ritOffset)}
          >{sharedAggregate('RIT', radioWide.ritActive, radioWide.ritOffset)}</span
        >
      {/if}
      {#if radioWide.xitActive.availability.structural || radioWide.xitOffset.availability.structural}
        <span class="fact" data-indicator-fact="xit" data-state={aggregateState(radioWide.xitActive, radioWide.xitOffset)}
          >{sharedAggregate('XIT', radioWide.xitActive, radioWide.xitOffset)}</span
        >
      {/if}
    </div>
  </section>
{/if}

<style>
  .indicator-row {
    display: grid;
    align-content: start;
    gap: 4px;
    min-width: 0;
    padding: 5px 7px;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    border-radius: 4px;
    background: var(--v2-bg-panel, rgba(255, 255, 255, 0.03));
  }
  .indicator-row[data-indicator-operational='false'] { opacity: 0.56; }
  header, .facts { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
  header strong { color: var(--v2-text-secondary, rgba(255, 255, 255, 0.8)); }
  .rf-lamp, .fact {
    padding: 1px 4px;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    border-radius: 3px;
    font-size: 10px;
    line-height: 1.4;
  }
  .rf-lamp { min-inline-size: 3ch; box-sizing: content-box; }
  .rf-lamp.tx { color: var(--v2-accent-red, #ff4545); border-color: currentColor; }
  .fact[data-state='on'], .fact[data-state='known'] { color: var(--v2-text-primary, #e8e8e8); }
  .fact[data-state='off'], .fact[data-state='unknown'] { color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55)); }
  /* Owner ruling 2026-09-23: the RFG fact keeps its slot whether or not it
     prints. The widest lit text is `RFG 99%` (7 characters in this mono
     face); the 8ch reservation stays deliberately wider than that, so the
     neighbouring facts never move when the gain changes between reduced,
     full and unknown. */
  .fact[data-indicator-fact='rf-gain'] { min-inline-size: 8ch; box-sizing: content-box; }
  /* MOR-2644 correction 3 (owner rule 2026-09-21: the layout never moves):
     every other fact reserves its widest lit text the same way, so an
     unread label and its first reading occupy the same box and neighbours
     never move. Widths mirror FACT_SLOT_RESERVATIONS in the module script
     (`ch` counts of each fact's widest rendered text); box-sizing matches
     RFG so the reservation covers text only, outside the shared padding
     and border. The pin test asserts each reservation against the length of
     its widest lit text, since jsdom has no layout. */
  .fact[data-indicator-fact='bandwidth'] { min-inline-size: 10ch; box-sizing: content-box; }
  .fact[data-indicator-fact='agc'] { min-inline-size: 10ch; box-sizing: content-box; }
  .fact[data-indicator-fact='nb'] { min-inline-size: 6ch; box-sizing: content-box; }
  .fact[data-indicator-fact='nr'] { min-inline-size: 6ch; box-sizing: content-box; }
  .fact[data-indicator-fact='notch'] { min-inline-size: 12ch; box-sizing: content-box; }
  .fact[data-indicator-fact='attenuator'] { min-inline-size: 9ch; box-sizing: content-box; }
  .fact[data-indicator-fact='preamp'] { min-inline-size: 7ch; box-sizing: content-box; }
  .fact[data-indicator-fact='ip-plus'] { min-inline-size: 8ch; box-sizing: content-box; }
  .fact[data-indicator-fact='digi-sel'] { min-inline-size: 12ch; box-sizing: content-box; }
  .fact[data-indicator-fact='antenna'] { min-inline-size: 5ch; box-sizing: content-box; }
  .fact[data-indicator-fact='atu'] { min-inline-size: 11ch; box-sizing: content-box; }
  .fact[data-indicator-fact='rit'] { min-inline-size: 15ch; box-sizing: content-box; }
  .fact[data-indicator-fact='xit'] { min-inline-size: 15ch; box-sizing: content-box; }
  /* Empty (a displayed 100%, or unread): the slot stays reserved at the same
     size and 1px geometry, but nothing is drawn — a transparent border and
     background instead of an empty frame. The unknown Standard slot badge
     uses the same treatment: it keeps its slot at the same size but draws
     nothing. */
  .fact[data-indicator-fact='rf-gain'][data-empty='true'] {
    border-color: transparent;
    background: transparent;
  }
  .header-badges .fact[data-empty='true'] {
    min-inline-size: 1ch;
    box-sizing: content-box;
    border-color: transparent;
    background: transparent;
    color: transparent;
  }
  .s-meter { min-width: 0; overflow: hidden; }
  .s-meter-unknown {
    display: grid;
    min-height: 30px;
    place-items: center;
    min-inline-size: 12ch;
    box-sizing: content-box;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55));
    font-size: 11px;
  }
  /* MOR-2644 correction 2: the unread S-meter box keeps its size (the same
     min-height and border as the shell above) and prints no text. */

  .indicator-row[data-indicator-appearance='sdr'], .indicator-row[data-indicator-appearance='standard'] {
    padding: 0; border: 0; background: transparent; border-radius: 0; gap: 6px;
  }
  [data-indicator-appearance='sdr'] header { justify-content: space-between; letter-spacing: .14em; }
  [data-indicator-appearance='sdr'] .fact { padding: 2px 6px; border-radius: 3px; letter-spacing: .06em; }
  [data-indicator-appearance='sdr'] .facts { gap: 5px; min-height: 24px; }
  .header-badges { display: inline-flex; gap: 4px; margin-left: auto; }
  /* The historical Standard face reserves a bounded 58px meter row. Without
     this face-owned height, the wide SVG's intrinsic ratio makes the whole
     receiver deck grow with viewport width. */
  [data-indicator-appearance='standard'] .s-meter {
    width: 100%; max-width: 600px; height: 58px;
  }
  [data-indicator-appearance='standard'] .s-meter :global(svg[data-variant='vfo-wide']) {
    height: 100%;
  }
  [data-indicator-appearance='standard'] .facts { gap: 4px; }
  .shared-indicators:not([data-indicator-appearance='semantic']) .facts { justify-content: center; }
  .shared-indicators:not([data-indicator-appearance='semantic']) .fact { font-size: 9px; }
</style>
