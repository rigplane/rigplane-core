<!--
  Semantic band + frequency-entry surface (MOR-1307, vocabulary slice 7B).

  Presentation only. It renders the MOR-1294 `band` fact group — the current
  band, the capability-derived band choice set, the live TX permit and the
  tuning envelope — and emits intents as callbacks. It holds no state beyond
  the operator's own unsubmitted keystrokes, consults no controller and owns
  no command path.

  SAFETY-ADJACENT. This surface renders TX-PERMISSION information: a fail-open
  bug here tells an operator "you may transmit" where transmission is not
  permitted. Five rules govern this file and nothing may relax them:

  (1) THE BAND-SCOPED TX ANSWER IS `band.currentBandTx`, AND NOTHING ELSE.
      That fact is the LIVE-frequency permit (MOR-1294 verify F1: the adapter
      evaluates `getFrequencyPermit(observedFreqHz, caps.txBands)`), already
      collapsed fail-closed. This file re-derives nothing: it holds no band
      plan, no `txBands`, no permit function, and its whole import list is the
      fact contract.

  (2) `BandChoice.defaultHzTxPermit` IS A POINT SAMPLE, AND IS LABELLED AS
      ONE. It answers "may I key at THIS band's default frequency", never "may
      I key anywhere in this band" — `txBands` segments are routinely narrower
      than a band-plan band, so presenting it band-wide is the exact fail-open
      misread the MOR-1294 rename exists to prevent (7B carry-forward F3).
      Every rendering of it names the frequency it was sampled at, and it is
      never used for the band-scoped answer in rule (1).

  (3) THE DENIAL SIGNAL IS THE FIELD VALUE ITSELF. By design the `band` group
      contributes NO `disabledReasons` entry (MOR-1294 build report §4) — a
      parallel entry would be a second representation of the same fact. So the
      denial is read off `currentBandTx` directly, and `disabledReasons` /
      top-level `txPermit` are consulted only for the richer EXPLANATION the
      binary collapse cannot carry (carry-forward 2). An empty
      `disabledReasons` never softens a denial.

  (4) A RECEIVER-SCOPED WRITE NEEDS A KNOWN ACTIVE RECEIVER. Band select and
      frequency entry both land on `set_freq`/`set_band`, which write the
      ACTIVE receiver's VFO (the MOR-1322 B1 wrong-VFO dispatch class). While
      `activeReceiver` is unobserved there is no honest target, so both
      controls are inert — disabled in the markup AND refused in the handler,
      two mechanisms on the same condition so neither can be the only one.

  (5) UNKNOWN TUNING BOUNDS FAIL CLOSED. `tuneMinHz`/`tuneMaxHz` are the
      frequency-entry constraint (MOR-1294 §5 ruling). `null` means the radio
      declared no range — entry is then DISABLED with a stated reason, never
      accepted unvalidated against v2's fabricated `0 … 999 MHz` default.

  Two-level availability (MOR-977/1256): `structural: false` renders NOTHING;
  `structural: true, operational: false` renders present-and-unobserved. An
  unread fact renders `UNKNOWN_TEXT`, never a fabricated 14.074 MHz.
-->
<script module lang="ts">
  import type {
    BandChoice, BandField, DisabledReasonCode, RadioViewModel,
  } from './radio-view-model';

  /** The ONE rendering of "not measured". Never a band name, never a number. */
  export const UNKNOWN_TEXT = '—';

  /** The `disabledReasons` codes that can EXPLAIN a TX denial, in the order
   *  they are preferred. Rule (3): explanation only — the denial itself is
   *  `currentBandTx`, so an empty list changes the words, never the verdict. */
  export const TX_REASON_CODES: readonly DisabledReasonCode[] = [
    'out-of-band', 'tx-target-unknown', 'capability-unavailable',
  ];
  export const REASON_LABEL: Record<string, string> = {
    'out-of-band': 'live frequency is outside the configured TX ranges',
    'tx-target-unknown': 'TX target frequency was never observed',
    'capability-unavailable': 'TX ranges are not configured',
  };
  /** The denial's words when no code explains it: `txPermit` says the TX
   *  TARGET may key, so what is missing is the band-scoped resolution itself
   *  (unobserved live frequency, or a frequency in no band of the plan). */
  export const UNRESOLVED_REASON = 'current band could not be resolved';
  /** MOR-1389: the denial's words when `deriveBand`'s MOR-1356
   *  `activeConfirmed` gate is what forced 'denied', with the current band
   *  ITSELF resolved and rendered. `UNRESOLVED_REASON` is false in that
   *  state — the band is not unresolved, the receiver serving it is
   *  unconfirmed — so it needs its own, equally honest, sentence. Named for
   *  exactly what `activeReceiver.status === 'unknown'` carries (rule (4)'s
   *  own gate): identity is unconfirmed, not "stale" or "never observed" —
   *  `seen()`'s three-part AND collapses those into one signal upstream, so
   *  claiming more than this would be a fact this file does not have. */
  export const ACTIVE_RECEIVER_UNCONFIRMED_REASON = 'active receiver identity was not confirmed';

  export const usable = (f: BandField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  export const textOf = (f: BandField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  export const isCurrent = (f: BandField<string>, name: string): boolean =>
    f.reading.status === 'known' && f.reading.value === name;

  /** Display only, never parsed back — the entry field works in whole Hz so a
   *  boundary comparison can never lose a digit to a decimal round-trip. */
  export const mhz = (hz: number): string => `${(hz / 1e6).toFixed(3)} MHz`;
  /** Rule (2): the permit is spelled WITH the frequency it was sampled at, so
   *  the label cannot be read as a claim about the whole band. */
  export const defaultPermitLabel = (choice: BandChoice): string =>
    `TX at ${mhz(choice.defaultHz)}: ${choice.defaultHzTxPermit.status}`;

  /** Rule (3): consulted ONLY once `currentBandTx` already said `denied` (or,
   *  from the fix-round F1 caveat, once the authoritative `txPermit` itself is
   *  not `allowed`) — and only over TX-SCOPED entries. `capability-unavailable`
   *  is ALSO emitted for `scope.hardwareScope`, `scope.audioFftScope` and each
   *  non-operational `receiver.<id>` (radio-view-model-adapter.ts:1171-1187),
   *  none of which explain a TX denial. The adapter records a `field:
   *  'txPermit'` entry for EVERY non-allowed permit
   *  (radio-view-model-adapter.ts:1164-1170), so filtering on that field is
   *  the whole fix — a code match alone would misattribute an unrelated
   *  capability gap as a TX-configuration fault (fix-round F2). This
   *  `field === 'txPermit'` match is NEVER weakened or bypassed below.
   *
   *  MOR-1389: a `field: 'txPermit'` reason is not the only way `denied` can
   *  arise. `deriveBand`'s MOR-1356 `activeConfirmed` gate can force it with
   *  no such entry at all — the true reason is `field: 'activeReceiver'`,
   *  which the match above correctly ignores (it is not a TX-scoped fault;
   *  rule (3) reserves `TX_REASON_CODES` for those). Once the TX-scoped
   *  search comes up empty, `view.activeReceiver.status` — the SAME top-level
   *  fact rule (4)'s `receiverKnown` reads, not a re-derivation — decides
   *  between the two remaining, mutually exclusive explanations: the
   *  receiver is unconfirmed (band resolved, rendered above), or the band
   *  itself could not be resolved (the pre-existing fallback, still true in
   *  that state). */
  export function txDeniedReason(view: RadioViewModel): string {
    const hit = TX_REASON_CODES.find(
      (c) => view.disabledReasons.some((r) => r.code === c && r.field === 'txPermit'),
    );
    if (hit !== undefined) return REASON_LABEL[hit];
    if (view.txPermit.status !== 'allowed') return UNKNOWN_TEXT;
    if (view.activeReceiver.status === 'unknown') return ACTIVE_RECEIVER_UNCONFIRMED_REASON;
    return UNRESOLVED_REASON;
  }
</script>

<script lang="ts">
  interface Props {
    view: RadioViewModel;
    onSelectBand?: (name: string, defaultHz: number, bsrCode: number | null) => void;
    onEnterFrequency?: (frequencyHz: number) => void;
  }
  let { view, onSelectBand, onEnterFrequency }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine). */
  let band = $derived(view.band);
  /** Rule (4). */
  let receiverKnown = $derived(view.activeReceiver.status === 'known');
  /** Rule (5). */
  let boundsKnown = $derived(
    band !== undefined && band.tuneMinHz !== null && band.tuneMaxHz !== null,
  );
  /** The operator's raw keystrokes, kept as typed: an empty or malformed entry
   *  must stay refusable rather than coerce to 0 Hz. */
  let entryText = $state('');
  let typedHz = $derived(entryText.trim() === '' ? Number.NaN : Number(entryText));
  let inRange = $derived(
    Number.isFinite(typedHz) && band?.tuneMinHz != null && band?.tuneMaxHz != null
      && typedHz >= band.tuneMinHz && typedHz <= band.tuneMaxHz,
  );
  let entryReady = $derived(receiverKnown && boundsKnown && inRange);

  function selectBand(choice: BandChoice): void {
    if (!receiverKnown) return;
    onSelectBand?.(choice.name, choice.defaultHz, choice.bsrCode);
  }
  function commitFrequency(): void {
    if (!entryReady) return;
    onEnterFrequency?.(typedHz);
  }
  /** MOR-1444: Escape cancels a typed entry — clears the keystrokes without
   *  dispatching, mirroring the "never coerce a malformed entry" rule above
   *  rather than adding a second dispatch guard. */
  function cancelEntry(): void {
    entryText = '';
  }
  /** MOR-1444: Enter commits through the same `commitFrequency` path (and
   *  therefore the same `entryReady` guard) the Set button already uses;
   *  Escape cancels. Template-only wiring — no new prop, import or hook. */
  function handleEntryKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitFrequency();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelEntry();
    }
  }
</script>

{#if band}
  <section
    class="band-surface" data-testid="band-surface" aria-label="Band and frequency entry"
    data-tx-permit-status={view.txPermit.status}
  >
    {#if band.currentBand.availability.structural}
      <p class="band-row" data-testid="band-current" data-observed={usable(band.currentBand)}>
        <span class="band-name">BAND</span>
        <output data-testid="band-current-value">{textOf(band.currentBand)}</output>
      </p>
    {/if}

    <!-- Rule (1). The live fact, verbatim; `defaultHzTxPermit` is not in scope
         anywhere in this block. -->
    <p class="band-row" data-testid="band-tx" data-tx={band.currentBandTx}>
      <span class="band-name">TX HERE</span>
      <output data-testid="band-tx-value">{band.currentBandTx}</output>
      {#if band.currentBandTx === 'denied'}
        <span data-testid="band-tx-reason">{txDeniedReason(view)}</span>
      {:else if view.txPermit.status !== 'allowed'}
        <!-- Fix-round F1: `band.currentBandTx` answers "may I key at the
             ACTIVE RECEIVER's frequency" — it can say `allowed` while the
             authoritative TX-TARGET permit (`view.txPermit`, the one 9A's
             break-in gate and `keyBlockedReasons` both key off) disagrees,
             e.g. under split, or while the TX target is simply unobserved.
             That disagreement must never live only in `data-tx-permit-status`
             — a data attribute is invisible to an operator. -->
        <span data-testid="band-tx-caveat">TX target {view.txPermit.status}: {txDeniedReason(view)}</span>
      {/if}
    </p>

    {#if band.bandChoices.length > 0}
      <div class="band-row" role="group" aria-label="Band select" data-testid="band-choices">
        {#each band.bandChoices as choice (choice.name)}
          <!-- NOT gated on the permit: picking a band is a TUNING action and a
               band with no TX allocation stays perfectly receivable. The permit
               is a label here (rule 2), never a tuning gate. -->
          <button
            type="button" class="band-choice" data-testid={`band-choice-${choice.name}`}
            data-default-permit={choice.defaultHzTxPermit.status}
            aria-pressed={isCurrent(band.currentBand, choice.name)}
            disabled={!receiverKnown}
            onclick={() => selectBand(choice)}
          >{choice.name}
            <small data-testid={`band-choice-permit-${choice.name}`}
            >{defaultPermitLabel(choice)}</small></button>
        {/each}
      </div>
    {/if}

    <label class="band-row" data-testid="band-entry" data-bounds={boundsKnown}>
      <span class="band-name">FREQ</span>
      <input
        type="number" data-testid="band-entry-input" data-freq-entry step="1"
        min={band.tuneMinHz ?? undefined} max={band.tuneMaxHz ?? undefined}
        value={entryText} disabled={!receiverKnown || !boundsKnown}
        oninput={(event) => { entryText = event.currentTarget.value; }}
        onkeydown={handleEntryKeydown}
      />
      <span data-testid="band-entry-range">{boundsKnown && band.tuneMinHz !== null
        && band.tuneMaxHz !== null
        ? `${mhz(band.tuneMinHz)} … ${mhz(band.tuneMaxHz)}`
        : UNKNOWN_TEXT}</span>
      <button
        type="button" data-testid="band-entry-set" disabled={!entryReady}
        onclick={commitFrequency}
      >Set</button>
      {#if !boundsKnown || !receiverKnown}
        <span data-testid="band-entry-reason">{boundsKnown
          ? 'active receiver not observed — no honest tuning target'
          : 'tuning limits unknown — entry cannot be validated'}</span>
      {/if}
    </label>
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .band-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .band-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .band-name { min-width: 7ch; }
  .band-choice[aria-pressed='true'] { font-weight: 700; }
  /* Second channel beside `data-observed`/`data-tx`, never the only one: the
     rendered word itself is the primary one and survives forced-colors. */
  [data-observed='false'] { font-style: italic; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
