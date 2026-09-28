<!--
  MOR-2299 slice 1 — pure presentation for one receiver-addressed indicator
  entry. The component reads only the semantic contract handed to it. Shared
  ANT/TUNE/RIT/XIT facts and DUAL actions belong to slice 2.
-->
<script module lang="ts">
  /** MOR-2852: the phone meta row's fact vocabulary — the subset the 'chips'
   *  appearance can draw. Exported for the ReceiverInstrumentHost handle
   *  handles, whose `mainFacts`/`subFacts` snippets re-expose this type. */
  export type VfoFactKind = 'bandwidth' | 'agc' | 'nb' | 'nr';

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
  // - AGC: verbatim capability label. The AGC label vocabulary
  //   (agcModes/agcLabels) does not reach this component; labels are
  //   verbatim and unbounded per-radio. The widest shipped label is 4
  //   characters (FAST/SLOW/AUTO/OFF); the reservation covers a 6-character
  //   label shape (`TUNING`, representative of the widest plausible label,
  //   e.g. a future profile label), not any per-radio width.
  // - NOTCH: the widest rendered state text `NOTCH MANUAL` (the component
  //   prints the raw mode uppercased: OFF/AUTO/MANUAL).
  // - ATT: `ATT 45 dB` — the largest attenuator step across profiles
  //   (IC-7610 0..45 dB); attValues do not reach this component.
  // - P.AMP: `P.AMP 2` — the largest preamp level across profiles
  //   (0/1/2 everywhere shipped); preValues do not reach this component.
  // - ANT: `ANT 2` — the largest antenna index across profiles
  //   (IC-7610 has two TX ports); the port count does not reach this
  //   component.
  // - BW: `BW 9999 Hz` — the codebase-wide filter-width fallback
  //   (`FilterPanel`, `RitXitPanel`). MOR-2706: this constant is the FLOOR;
  //   the rendered reservation derives from the mounted profile's widest
  //   filter-width value (`bandwidthMaxHz`), so an Icom AM width max of
  //   10000 reserves `BW 10000 Hz` (11ch) and an FTX-1 FM table of 16000
  //   reserves `BW 16000 Hz` (11ch).
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
  import { t } from '$lib/i18n';
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import type { MeterContinuitySession } from '../primitives/meters/meter-ballistics.svelte';
  import { formatKnownLevel, levelFormatsBelowMax } from './format-level';
  import { RF_FRONT_END_LEVELS } from './rf-front-end-instruments';
  import { rfUnconfirmedLabel } from './rx-tx-surface';
  import { finiteValue, observationValue, readingText, readingValue } from '../primitives/reading-text';
  import type {
    DisplayObservedField, RadioWideIndicatorsViewModel, ReceiverIndicatorField,
    ReceiverIndicatorViewModel, TxAuxField,
  } from './radio-view-model';

  interface Props {
    indicator?: ReceiverIndicatorViewModel;
    appearance?: 'semantic' | 'sdr' | 'standard' | 'chips';
    /** MOR-2852: with `appearance="chips"` — the receiver facts to draw, in
     *  that order. Other appearances ignore it and render exactly as before. */
    facts?: readonly VfoFactKind[];
    children?: Snippet;
    slotLabel?: string;
    radioWide?: RadioWideIndicatorsViewModel;
    continuitySession?: MeterContinuitySession | null;
    sMeter?: Snippet;
  }

  let {
    indicator, radioWide, appearance = 'semantic', facts, children, slotLabel, continuitySession, sMeter,
  }: Props = $props();

  /** MOR-2671: uncertain reads `TX` as well — the distinction from confirmed TX is the
   *  hollow dashed outline (`[data-indicator-rf='uncertain']` below) plus the unconfirmed
   *  accessible sentence, never a `?` in the text. */
  const rfLabel = (state: RadioWideIndicatorsViewModel['rfState']): string =>
    state === 'transmitting' || state === 'uncertain' ? 'TX' : '';

  // MOR-2644: an unread fact shows its label dimmed with no value text —
  // never a dash placeholder. A fact the radio does not have is not drawn
  // at all (the {#if} guards below). The subdued data-state paint stays.
  function booleanState(field: ReceiverIndicatorField<boolean>): 'on' | 'off' | 'unknown' {
    return field.reading.status === 'known' ? (field.reading.value ? 'on' : 'off') : 'unknown';
  }

  // MOR-2688: the empty-display rule is `readingText`'s predicate,
  // `reading.status === 'known'`; boolean sites pass their own ON/OFF
  // formatter so the text on screen stays identical.
  /** Leading-space ON/OFF text for the per-receiver boolean facts (NB, NR,
   *  IP+, DIGI-SEL). */
  function booleanLabel(field: ReceiverIndicatorField<boolean>): string {
    return readingText(field, (v) => ` ${v ? 'ON' : 'OFF'}`);
  }

  /** ON/OFF text for the radio-wide boolean facts the RIT/XIT aggregate
   *  composes. */
  function sharedBoolean(field: TxAuxField<boolean>): string {
    return readingText(field, (v) => (v ? 'ON' : 'OFF'));
  }

  /** The RF gain the display shows, independent of provenance: the observed
   *  display value when one exists, otherwise the strict reading. MOR-2688
   *  S4c: the absence fallback is the strict reading, passed INTO
   *  `observationValue` (`primitives/reading-text.ts`); the predicate is
   *  unchanged. */
  function rfGainShown(field: DisplayObservedField<number>): number | null {
    return observationValue(field.display, readingValue(field));
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

  /** MOR-2706: the BW fact's reserved width derives from the mounted
   *  profile's widest filter-width value (`bandwidthMaxHz`, a structural
   *  fact fixed when the capabilities load — never a reading), with the
   *  9999-fallback constant as the floor. A reading never changes it. */
  let bandwidthReservationCh = $derived.by(() => {
    const widest = indicator?.bandwidthMaxHz;
    return widest === undefined
      ? FACT_SLOT_RESERVATIONS.bandwidth
      : Math.max(FACT_SLOT_RESERVATIONS.bandwidth, `BW ${widest} Hz`.length);
  });

  function sharedAggregate(
    label: 'RIT' | 'XIT', active: TxAuxField<boolean>, offset: TxAuxField<number>,
  ): string {
    // MOR-2644 correction 1: the Hz unit belongs to the offset number and
    // appears only right after a known offset. Both unread → exactly the
    // label; state known, offset unread → "RIT ON"/"RIT OFF"; both known →
    // the same text as main today.
    const state = sharedBoolean(active);
    const value = readingText(offset);
    if (value === '') return state === '' ? label : `${label} ${state}`;
    // MOR-2905: the hertz unit comes from the i18n catalog («Гц» in ru).
    const unit = t('core.filter.unit.hz');
    return state === '' ? `${label} ${value} ${unit}` : `${label} ${state} ${value} ${unit}`;
  }

  function aggregateState(
    active: TxAuxField<boolean>, offset: TxAuxField<number>,
  ): 'known' | 'unknown' {
    return active.reading.status === 'known' && offset.reading.status === 'known'
      ? 'known' : 'unknown';
  }

</script>

{#snippet factSpan(fact: VfoFactKind)}
  {#if indicator}
    {#if fact === 'bandwidth' && indicator.bandwidthHz.availability.structural}
      <span class="fact" data-indicator-fact="bandwidth" data-state={indicator.bandwidthHz.reading.status}
        style:min-inline-size={`${bandwidthReservationCh}ch`}>BW {readingText(indicator.bandwidthHz, (v) => `${String(v)} ${t('core.filter.unit.hz')}`)}</span>
    {:else if fact === 'agc' && indicator.agcMode.availability.structural}
      <span class="fact" data-indicator-fact="agc" data-state={indicator.agcMode.reading.status}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS.agc}ch`}>AGC{readingText(indicator.agcMode, (v) => ` ${String(v)}`)}</span>
    {:else if fact === 'nb' && indicator.nbActive.availability.structural}
      <!-- MOR-2852: NB/NR chips drop ON/OFF — the label; the state lives in data-state. -->
      <span class="fact" data-indicator-fact="nb" data-state={booleanState(indicator.nbActive)}
        style:min-inline-size="2ch">NB</span>
    {:else if fact === 'nr' && indicator.nrActive.availability.structural}
      <span class="fact" data-indicator-fact="nr" data-state={booleanState(indicator.nrActive)}
        style:min-inline-size="2ch">NR</span>
    {/if}
  {/if}
{/snippet}

{#if indicator}
  <!-- MOR-2852: the chips appearance carries ONLY the listed facts — no
       receiver header, no S-meter, no radio-wide section. -->
  {#if appearance === 'chips'}
    <section
      class="indicator-row"
      data-indicator-appearance="chips"
      data-testid="vfo-indicator-row"
      data-indicator-receiver={indicator.receiver}
      data-indicator-operational={indicator.availability.operational}
      aria-label={`${indicator.receiver} receiver indicators`}
    >
      <div class="facts chips" aria-label={`${indicator.receiver} receiver facts`}>
        {#each facts ?? [] as fact (fact)}
          {@render factSpan(fact)}
        {/each}
      </div>
    </section>
  {:else}
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
            style:min-inline-size={`${bandwidthReservationCh}ch`}
          >BW {readingText(indicator.bandwidthHz, (v) => `${String(v)} ${t('core.filter.unit.hz')}`)}</span>
        {/if}
        {#if appearance === 'standard'}
          <span class="header-badges"><span class="fact">BAR</span><span
            class="fact"
            data-empty={slotLabel == null ? 'true' : undefined}
          >{slotLabel ?? ''}</span></span>
        {/if}
      </header>

  <div class="s-meter" data-testid="receiver-s-meter" data-receiver={indicator.receiver}>
    {#if finiteValue(readingValue(indicator.sMeter)) !== null}
      {@const sMeterValue = finiteValue(readingValue(indicator.sMeter))}
      {#if sMeter}
        {@render sMeter()}
      {:else}
        <LinearSMeter value={sMeterValue} compact label={appearance === 'standard' ? slotLabel : undefined} variant={appearance === 'sdr' ? 'sdr-screen' : 'vfo-wide'} source={indicator.sMeter.source} session={continuitySession} />
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
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS.agc}ch`}
        >AGC{readingText(indicator.agcMode, (v) => ` ${String(v)}`)}</span
      >
    {/if}
    {#if indicator.nbActive.availability.structural}
      <span class="fact" data-indicator-fact="nb" data-state={booleanState(indicator.nbActive)}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS.nb}ch`}
        >NB{booleanLabel(indicator.nbActive)}</span
      >
    {/if}
    {#if indicator.nrActive.availability.structural}
      <span class="fact" data-indicator-fact="nr" data-state={booleanState(indicator.nrActive)}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS.nr}ch`}
        >NR{booleanLabel(indicator.nrActive)}</span
      >
    {/if}
    {#if indicator.notchMode.availability.structural}
      <span class="fact" data-indicator-fact="notch" data-state={indicator.notchMode.reading.status}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS.notch}ch`}
        >NOTCH{readingText(indicator.notchMode, (v) => ` ${String(v).toUpperCase()}`)}</span
      >
    {/if}
    {#if indicator.attenuator.availability.structural}
      <span class="fact" data-indicator-fact="attenuator" data-state={indicator.attenuator.reading.status}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS.attenuator}ch`}
        >ATT{readingText(indicator.attenuator, (v) => ` ${String(v)} dB`)}</span
      >
    {/if}
    {#if indicator.preamp.availability.structural}
      <span class="fact" data-indicator-fact="preamp" data-state={indicator.preamp.reading.status}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS.preamp}ch`}
        >P.AMP{readingText(indicator.preamp, (v) => ` ${String(v)}`)}</span
      >
    {/if}
    {#if indicator.ipPlus.availability.structural}
      <span class="fact" data-indicator-fact="ip-plus" data-state={booleanState(indicator.ipPlus)}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS['ip-plus']}ch`}
        >IP+{booleanLabel(indicator.ipPlus)}</span
      >
    {/if}
    {#if indicator.digiSel.availability.structural}
      <span class="fact" data-indicator-fact="digi-sel" data-state={booleanState(indicator.digiSel)}
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS['digi-sel']}ch`}
        >DIGI-SEL{booleanLabel(indicator.digiSel)}</span
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
        style:min-inline-size={`${FACT_SLOT_RESERVATIONS['rf-gain']}ch`}
      >{gainText ? `RFG ${gainText}` : ''}</span>
    {/if}
  </div>
</section>
{/if}
{:else if children}
  {@render children()}
{/if}

{#if radioWide && appearance !== 'chips'}
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
        aria-label={rfUnconfirmedLabel(radioWide.rfState) ?? undefined}
      >{rfLabel(radioWide.rfState)}</span>
      {#if radioWide.antenna.availability.structural}
        <span class="fact" data-indicator-fact="antenna" data-state={radioWide.antenna.reading.status}
          style:min-inline-size={`${FACT_SLOT_RESERVATIONS.antenna}ch`}
          >ANT{readingText(radioWide.antenna, (v) => ` ${String(v)}`)}</span
        >
      {/if}
      {#if radioWide.atu.availability.structural}
        <span class="fact" data-indicator-fact="atu" data-state={radioWide.atu.reading.status}
          style:min-inline-size={`${FACT_SLOT_RESERVATIONS.atu}ch`}
          >TUNE{readingText(radioWide.atu, (v) => ` ${String(v).toUpperCase()}`)}</span
        >
      {/if}
      {#if radioWide.ritActive.availability.structural || radioWide.ritOffset.availability.structural}
        <span class="fact" data-indicator-fact="rit" data-state={aggregateState(radioWide.ritActive, radioWide.ritOffset)}
          style:min-inline-size={`${FACT_SLOT_RESERVATIONS.rit}ch`}
          >{sharedAggregate('RIT', radioWide.ritActive, radioWide.ritOffset)}</span
        >
      {/if}
      {#if radioWide.xitActive.availability.structural || radioWide.xitOffset.availability.structural}
        <span class="fact" data-indicator-fact="xit" data-state={aggregateState(radioWide.xitActive, radioWide.xitOffset)}
          style:min-inline-size={`${FACT_SLOT_RESERVATIONS.xit}ch`}
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
  /* MOR-2671: the unconfirmed lamp is hollow — a dashed outline where confirmed
     TX is solid. Geometry, not colour, so the distinction survives forced-colors. */
  .rf-lamp[data-indicator-rf='uncertain'] { border-style: dashed; }
  .fact[data-state='on'], .fact[data-state='known'] { color: var(--v2-text-primary, #e8e8e8); }
  .fact[data-state='off'], .fact[data-state='unknown'] { color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55)); }
  /* MOR-2852: the chips appearance lights ON with the phone's lit-key
     vocabulary — the cyan edge bar on the left, never colour alone. The bar
     is pseudo-element geometry, so it survives forced-colors and never
     shifts the reserved slot. */
  .indicator-row[data-indicator-appearance='chips'] .fact { position: relative; }
  [data-indicator-appearance='chips'] .fact[data-state='on']::before {
    content: '';
    position: absolute;
    left: 0;
    top: 2px;
    bottom: 2px;
    width: 2px;
    border-radius: 0 2px 2px 0;
    background: var(--v2-accent-cyan, #22d3ee);
  }
  /* Owner ruling 2026-09-23: the RFG fact keeps its slot whether or not it
     prints. The widest lit text is `RFG 99%` (7 characters in this mono
     face); the reservation (8, in FACT_SLOT_RESERVATIONS) stays
     deliberately wider than that, so the neighbouring facts never move when
     the gain changes between reduced, full and unknown. */
  /* MOR-2644 correction 3 (owner rule 2026-09-21: the layout never moves):
     every fact reserves the width of its widest lit text, so an unread
     label and its first reading occupy the same box and neighbours never
     move. FACT_SLOT_RESERVATIONS in the module script is the ONLY source of
     the reserved width: each fact span sets min-inline-size inline from
     the constant, and the stylesheet carries no width of its own, so the
     two cannot drift apart. box-sizing matches RFG so the reservation
     covers text only, outside the shared padding and border. jsdom has no
     layout, so the pin test asserts each rendered fact's inline
     reservation against the constant, and each constant against the length
     of its widest lit text. */
  .fact[data-indicator-fact='bandwidth'], .fact[data-indicator-fact='agc'],
  .fact[data-indicator-fact='nb'], .fact[data-indicator-fact='nr'],
  .fact[data-indicator-fact='notch'], .fact[data-indicator-fact='attenuator'],
  .fact[data-indicator-fact='preamp'], .fact[data-indicator-fact='ip-plus'],
  .fact[data-indicator-fact='digi-sel'], .fact[data-indicator-fact='rf-gain'],
  .fact[data-indicator-fact='antenna'], .fact[data-indicator-fact='atu'],
  .fact[data-indicator-fact='rit'], .fact[data-indicator-fact='xit'] {
    box-sizing: content-box;
  }
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
    box-sizing: border-box;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55));
    font-size: 11px;
  }
  /* MOR-2644 correction 2: the unread S-meter box keeps its main outer
     height (30px including the 1px border) and prints no text. The width
     reservation (min-inline-size) holds the inline size only; the global
     border-box model holds the block size, so the 1px top and bottom
     borders stay inside the 30px min-height. */

  /* MOR-2852: the chips appearance is a bare inline strip — no panel
     chrome — so the phone meta row owns the one-line, unclipped box. The
     sdr/standard faces de-panel the same way below for their own cases. */
  .indicator-row[data-indicator-appearance='chips'] {
    padding: 0; border: 0; background: transparent; border-radius: 0; gap: 0;
  }
  .indicator-row[data-indicator-appearance='chips'] .facts { flex-wrap: nowrap; gap: 5px; }

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
