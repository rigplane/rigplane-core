<!--
  VfoSurface — pure semantic rendering of the VFO facts on `RadioViewModel`
  (MOR-1062 contract): frequency, receiver/slot identity, mode/filter,
  active state, per-VFO TX-target marker, and the orthogonal split/dual-watch
  facts. Emits selection/toggle INTENTS via callback props; no store,
  transport, capability, skin, or manufacturer knowledge (v3 ADR, MOR-1063).
  txTarget/txPermit (reasoned TX confirmation) and any TX action belong to
  the sibling RX/TX surface, not here.

  Two-level gating (MOR-977): a selection control is ABSENT when there is
  structurally nothing to choose (single-VFO topology, or already active)
  and DISABLED when the slot identity is unobserved (`slot.kind ===
  'unknown'`) — never fabricate an A/B id. Split/dual-watch toggles are
  always structurally present but DISABLED while unobserved, rendered as
  an explicit tri-state — never defaulted to "off".

  Two optional props exist only for callers that mount ONE SURFACE PER
  RECEIVER over a sliced view model (MOR-1067's dual-receiver cockpit);
  both default to the unsliced behaviour byte-for-byte. `selectionPoolSize`
  keeps the "structurally nothing to choose" gate reading the whole radio
  rather than the slice, and `showRadioWideFacts` lets such a caller render
  the radio-wide split/dual-watch/active-receiver facts exactly once.
-->
<script module lang="ts">
  import type { ReceiverId, VfoSlot } from './radio-view-model';

  export interface VfoSelection {
    receiver: ReceiverId;
    slot: VfoSlot;
  }

  /** MOR-1481: per-instance counter for disabled-reason `aria-describedby`
   *  targets, same convention as `TxAuxSurface`'s own `sequence` — several
   *  mounted surfaces (dual-receiver cockpit) must not collide. */
  let sequence = 0;
</script>

<script lang="ts">
  import { t } from '$lib/i18n';
  import { vfoEqualLabel, vfoSwapLabel } from '../components-v2/vfo/vfo-ops-utils';
  import FrequencyDisplayInteractive from '../primitives/frequency/FrequencyDisplayInteractive.svelte';
  import VfoIndicatorRow from './VfoIndicatorRow.svelte';
  import { splitFrequencyToDigits, groupDigitsForDisplay } from '../primitives/frequency/frequency-tuning';
  import { renderSlot } from './design-language-renderers';
  import type { BooleanFact, DisplayObservation, RadioViewModel, VfoViewModel } from './radio-view-model';

  interface Props {
    viewModel: RadioViewModel;
    appearance?: 'semantic' | 'sdr' | 'standard';
    onSelectVfo?: (target: VfoSelection) => void;
    onToggleSplit?: () => void;
    onToggleDualWatch?: () => void;
    /**
     * MOR-1067: how many VFOs the operator can choose BETWEEN across the WHOLE
     * radio. Defaults to `viewModel.vfos.length` — for an unsliced view model
     * those are the same number, so the default path is unchanged. A
     * dual-receiver channel strip passes the whole model's count: its slice
     * holds one VFO, but the radio still has something to choose, and the
     * MOR-977 structural gate must not read a per-receiver SLICE as "there is
     * structurally nothing to choose" and render the control ABSENT.
     */
    selectionPoolSize?: number;
    /**
     * MOR-1067: split / dual-watch / active-receiver are radio-WIDE facts, not
     * per-receiver ones. A cockpit that mounts one surface per receiver must
     * render them exactly once — two `role="switch"` controls for one radio
     * fact is duplicated radio behaviour in the presentation and a duplicated
     * `aria-checked` pair for assistive tech. Defaults to `true`: the single
     * unsliced surface renders them exactly as before.
     */
    showRadioWideFacts?: boolean;
    /**
     * MOR-1068: the mirror of `showRadioWideFacts`. `false` renders the
     * radio-wide half ALONE, so a layout can place that row outside the
     * per-receiver strips (the dual-receiver cockpit's global zone) without
     * repeating the VFO tiles the strips already show. Defaults to `true`:
     * every unsliced caller renders exactly as before.
     */
    showVfoList?: boolean;
    /**
     * MOR-1068: accessible name for this surface's group. A cockpit mounts
     * three of these at once (one per receiver strip + the radio-wide row);
     * with one shared generic name assistive tech sees three identical
     * groups and cannot tell which receiver it is in. Defaults to the
     * generic catalog label, so single-surface callers are unchanged.
     */
    groupLabel?: string;
    /**
     * MOR-1256: forces every select control in this surface's VFO list
     * disabled, regardless of slot/active state — the strip-level
     * counterpart to the per-VFO `slot.kind === 'unknown'` gate below (same
     * `disabled` attribute on the same button; not a parallel mechanism). A
     * dual-receiver-cockpit strip whose receiver is structurally present but
     * OPERATIONALLY unavailable (`dual-rx-unavailable`) sets this so its
     * controls go inert without pretending the receiver does not exist
     * (MOR-977: absent only when structural, disabled when only operational
     * fails). Defaults to `false`: every existing caller — single/unsliced,
     * and an operationally-fine dual strip — is unchanged.
     */
    disabled?: boolean;
    /**
     * MOR-1421 — a PLAIN boolean, not a capability lookup: this surface stays
     * capability-blind by ADR (v3, MOR-1063), so the caller (`SemanticRadioSurfaces`,
     * which already holds `runtime.caps` for the AGC/NB display-metadata
     * precedent) decides and hands over the answer. `false` hides the
     * radio-wide active-receiver readout and the dual-watch toggle: on a
     * single-receiver radio "which receiver is active" has only one possible
     * answer and "dual watch" has nothing to watch, so the operator
     * preference is to hide both rather than show a permanent "MAIN" readout
     * or a permanently-disabled toggle. The split toggle is unaffected — split
     * is meaningful with one receiver. Defaults to `true`: every existing
     * caller (all of them dual-receiver-capable radios today) renders exactly
     * as before.
     */
    hasDualReceiver?: boolean;
    /**
     * MOR-1322 (S3b) — a per-digit tuning intent for ONE receiver's frequency.
     * The same command-bus path the legacy VfoHeader used
     * (`onMainFreqChange`/`onSubFreqChange`), so this is not a new key path and
     * carries no TX semantics (R9): it sets a receive/transmit FREQUENCY, it
     * never keys the transmitter. Omit it and the surface renders the plain
     * readout, exactly as before this slice.
     */
    onTuneFrequency?: (receiver: ReceiverId, frequencyHz: number) => void;
    /**
     * MOR-1441 — the pending (not-yet-confirmed) tuning target for a
     * receiver, keyed by `ReceiverId`. The caller (`SemanticRadioSurfaces`,
     * which already holds the command-bus for the MOR-1421 `hasDualReceiver`
     * precedent) computes this from the in-flight `set_freq` intent; this
     * surface stays command-bus-blind and only renders the plain value it is
     * handed. Absent (or missing that receiver's key) renders the plain
     * confirmed readout, exactly as before this prop existed.
     */
    pendingFrequencyHz?: Partial<Record<ReceiverId, number>>;
    /**
     * A dual-receiver cockpit passes its strip receiver so the root-level
     * addressed collection is partitioned across the two strip mounts. The
     * unsliced/default surface omits this prop and renders the collection
     * once; the radio-wide `showVfoList={false}` mount renders no rows.
     */
    indicatorReceiver?: ReceiverId;
    /**
     * MOR-1321 (v3-rework slice S3a) — the VFO-scoped ACTIONS the legacy
     * `VfoOps` bridge carried and the semantic deck lost at MOR-1313: equalize
     * (copy one VFO onto the other), swap, and the two composite "quick"
     * frontend intents (`quick_split` / `quick_dualwatch`, epic #774).
     * A semantic availability declaration admits the callback; this surface
     * does not infer backend/provider consumption from primitive fact caps.
     *
     * INTENTS, like every other callback here: this surface names what the
     * operator asked for and knows nothing about how it is sent. None is a TX
     * path — the quick triggers move receive/transmit FREQUENCY assignment and
     * never key the transmitter (R9: the sole key/unkey authority is the
     * sibling RX/TX surface).
     */
    onEqualizeVfos?: () => void;
    onSwapVfos?: () => void;
    onQuickSplit?: () => void;
    onQuickDualWatch?: () => void;
    onSelectMainReceiver?: () => void;
    onSelectSubReceiver?: () => void;
    onSpeak?: () => void;
  }

  let {
    viewModel, appearance = 'semantic',
    onSelectVfo,
    onToggleSplit,
    onToggleDualWatch,
    selectionPoolSize,
    showRadioWideFacts = true,
    showVfoList = true,
    groupLabel,
    disabled = false,
    hasDualReceiver = true,
    onTuneFrequency,
    pendingFrequencyHz,
    indicatorReceiver,
    onEqualizeVfos,
    onSwapVfos,
    onQuickSplit,
    onQuickDualWatch,
    onSelectMainReceiver,
    onSelectSubReceiver,
    onSpeak,
  }: Props = $props();

  /**
   * MOR-1321 — the STRUCTURAL half of the MOR-977 gate for everything below,
   * and the same pool `isSelectable` reads: with one VFO there is nothing to
   * equalize onto, swap with, or split against, so the ops row and the RX/TX
   * digest are ABSENT rather than present-and-inert. `selectionPoolSize` keeps
   * a per-receiver slice reading the whole radio (MOR-1067), exactly as it does
   * for tile selection.
   */
  let vfoPool = $derived(selectionPoolSize ?? viewModel.vfos.length);
  let hasVfoPair = $derived(vfoPool > 1);
  let dualActions = $derived(viewModel.radioWideIndicators?.actions ?? {
    main: { structural: false, operational: false },
    sub: { structural: false, operational: false },
    equalize: { structural: hasVfoPair, operational: Boolean(onEqualizeVfos) },
    swap: { structural: hasVfoPair, operational: Boolean(onSwapVfos) },
    quickSplit: { structural: false, operational: false },
    quickDualWatch: { structural: false, operational: false },
    speak: { structural: false, operational: false },
  });
  let hasDualActions = $derived(Object.values(dualActions).some((action) => action.structural));
  let relativeIdentityUnknown = $derived(viewModel.vfos.some((vfo) => vfo.slot.kind === 'relative'));
  let relativeReceiver = $derived(
    viewModel.vfos.find((vfo) => vfo.slot.kind === 'relative')?.receiver ?? null,
  );
  let relativeSelectionPending = $state(false);
  const relativeSelectionHelp = 'Current A/B identity is unknown. Selecting this VFO will change the radio selection and establish identity.';

  /** MOR-1481: this instance's id prefix for disabled-reason
   *  `aria-describedby` targets (mirrors `TxAuxSurface`'s `reasonIdPrefix`). */
  const reasonIdPrefix = `vfo-reason-${++sequence}`;
  /** `aria-describedby` needs an id to point at; `undefined` omits the
   *  attribute exactly when there is no reason — same convention as
   *  `TxAuxSurface`'s `reasonIdOf`. */
  function reasonId(suffix: string, text: string | undefined): string | undefined {
    return text !== undefined ? `${reasonIdPrefix}-${suffix}` : undefined;
  }

  /**
   * MOR-1481 — the operator-facing reason a per-tile select control is
   * disabled. `selectDisabled` (computed per-tile below) combines two
   * independent gates, and this mirrors them in the same priority: THIS
   * VFO's own slot identity never resolved (the more specific claim — the
   * A/B resolver buttons only apply while every VFO reads `relative`, not
   * this case) beats the MOR-1256 strip-level `disabled` prop (the receiver
   * itself is operationally unavailable).
   */
  function selectReasonText(vfo: VfoViewModel): string | undefined {
    if (vfo.slot.kind === 'unknown') return t('core.vfo.select.unknownSlotReason');
    if (disabled) return t('core.vfo.select.receiverUnavailableReason');
    return undefined;
  }

  /** MOR-1481 rework (R2): this is the OPS ROW's own reason — no button here
   *  "selects a VFO" (that is what `relativeSelectionHelp` describes, and it
   *  is correct on the Select VFO A/B resolver buttons below, which DO). On
   *  equalize/swap/quick-split/quick-dual-watch the operator has not
   *  selected anything; the honest claim is that identity itself is
   *  unresolved, so this draws from its own catalog key instead of
   *  reusing the resolver buttons' English-only literal. */
  function identityOnlyReasonText(): string | undefined {
    return relativeIdentityUnknown ? t('core.vfo.ops.identityUnknownReason') : undefined;
  }
  /** MOR-1481: quick-split's own operational gate
   *  (`viewModel.split.status === 'unknown'`) is the SAME condition
   *  `toggleSplit`'s fact-toggle carries below, so the two share one
   *  catalog key rather than a parallel vocabulary. Identity wins when both
   *  apply — it is the more actionable claim (the A/B resolver exists for
   *  it), while nothing on this surface resolves an unread split state. */
  function quickSplitReasonText(): string | undefined {
    if (relativeIdentityUnknown) return t('core.vfo.ops.identityUnknownReason');
    return viewModel.split.status === 'unknown' ? t('core.vfo.split.unknownReason') : undefined;
  }
  /** Mirrors `quickSplitReasonText` for dual watch. */
  function quickDualWatchReasonText(): string | undefined {
    if (relativeIdentityUnknown) return t('core.vfo.ops.identityUnknownReason');
    return viewModel.dualWatch.status === 'unknown' ? t('core.vfo.dualWatch.unknownReason') : undefined;
  }

  function slotKey(slot: VfoSlot): string {
    if (slot.kind === 'slotted') return slot.id;
    if (slot.kind === 'relative') return slot.role;
    return slot.kind;
  }

  function roleLabel(vfo: VfoViewModel): string {
    const { slot } = vfo;
    if (slot.kind === 'slotted') return `${vfo.receiver} ${slot.id}`;
    if (slot.kind === 'relative') {
      return slot.role === 'selected' ? 'Selected VFO' : 'Unselected VFO';
    }
    if (slot.kind === 'unknown') return `${vfo.receiver} (${t('core.vfo.state.unknown')})`;
    return vfo.receiver;
  }

  /**
   * MOR-1482 — the ONE stable frequency format this surface's own fallback
   * uses: the same dot-grouped digit convention `FrequencyDisplayInteractive`
   * renders for the tunable/active tile, built from the SAME shared utility
   * (`frequency-tuning.ts`) so the two can never drift apart. Previously this
   * emitted a decimal-MHz string (`14.332000 MHz`) that matched neither the
   * active tile's dot convention nor (when a design language is active — the
   * WORKSPACE DEFAULT, see `DEFAULT_WORKSPACE.designLanguage`) the language's
   * own thin-space-grouped text: two mismatched formats on the same slot,
   * flipping between them as the design-language activation effect raced this
   * surface's own render. `null` still renders the honest placeholder, never
   * a differently-formatted number.
   */
  function formatFrequency(hz: number | null): string {
    if (hz === null) return '—';
    const { mhz, khz, hz: hzGroup } = groupDigitsForDisplay(splitFrequencyToDigits(hz));
    return [mhz, khz, hzGroup].map((group) => group.map((d) => d.char).join('')).join('.');
  }

  /**
   * MOR-1275: the active design language's `frequencyDisplay` renderer, given
   * the ONE fact it is entitled to — this tile's own frequency, already a prop.
   * `null` whenever no language is active or none declares that renderer.
   *
   * MOR-1482 (owner ruling, session 19) — only `.attributes` (the region
   * claim, spread onto `.vfo-freq` below) is read from this call's result
   * now; `.text` is deliberately NOT consumed anywhere in production (see the
   * doc note on `RendererDisplay.text` in `design-language-renderers.ts`). A
   * language's `frequencyDisplay` grammar is hero-scale — studioline's
   * thin-space-grouped ranked digits, fieldline's ungrouped run — and
   * flattened to a plain string at this tile's small font size it loses the
   * ranking/geometry that grammar depends on to read as anything but an
   * unformatted digit string, which is worse than the plain fallback it
   * displaced. The verifier caught this live: studioline is
   * `DEFAULT_WORKSPACE.designLanguage` and is declared compatible with the
   * shipped `desktop-v2` skin, so this was not a rare state — it was the
   * out-of-the-box tile text on the live bench.
   */
  function frequencyDisplay(vfo: VfoViewModel): ReturnType<typeof renderSlot> {
    return renderSlot('frequencyDisplay', { frequencyHz: displayValue(vfo.display?.frequencyHz, vfo.frequencyHz) });
  }

  function displayValue<T>(display: DisplayObservation<T> | undefined, strict: T | null): T | null {
    if (display === undefined) return strict;
    return display.state === 'current' || display.state === 'stale' ? display.value : null;
  }

  function hasDigitReadout(vfo: VfoViewModel): boolean {
    return vfo.isActiveSlot && onTuneFrequency !== undefined
      && (vfo.frequencyHz !== null || vfo.display !== undefined);
  }

  function hasTunableFrequency(vfo: VfoViewModel): boolean {
    return vfo.isActiveSlot && vfo.frequencyHz !== null && onTuneFrequency !== undefined;
  }

  function readoutDisabled(vfo: VfoViewModel): boolean {
    return disabled || !hasTunableFrequency(vfo)
      || (vfo.display !== undefined && vfo.display.frequencyHz.state !== 'current');
  }

  function tuneFrequency(vfo: VfoViewModel, frequencyHz: number): void {
    if (disabled || vfo.frequencyHz === null || !vfo.isActiveSlot) return;
    onTuneFrequency?.(vfo.receiver, frequencyHz);
  }

  function isSelectable(vfo: VfoViewModel): boolean {
    return hasVfoPair && vfo.slot.kind !== 'relative' && !vfo.isActive;
  }

  function selectVfo(vfo: VfoViewModel): void {
    if (!isSelectable(vfo) || vfo.slot.kind === 'unknown' || disabled) return;
    onSelectVfo?.({ receiver: vfo.receiver, slot: vfo.slot });
  }

  function selectAbsoluteSlot(id: 'A' | 'B'): void {
    if (disabled || relativeSelectionPending || relativeReceiver === null) return;
    relativeSelectionPending = true;
    onSelectVfo?.({ receiver: relativeReceiver, slot: { kind: 'slotted', id } });
    window.setTimeout(() => { relativeSelectionPending = false; }, 2500);
  }

  function triState(fact: BooleanFact): 'true' | 'false' | 'mixed' {
    if (fact.status === 'unknown') return 'mixed';
    return fact.value ? 'true' : 'false';
  }

  function stateWord(fact: BooleanFact): string {
    if (fact.status === 'unknown') return t('core.vfo.state.unknown');
    return t(fact.value ? 'core.vfo.state.on' : 'core.vfo.state.off');
  }

  function toggleSplit(): void {
    if (viewModel.split.status !== 'known') return;
    onToggleSplit?.();
  }

  function toggleDualWatch(): void {
    if (viewModel.dualWatch.status !== 'known') return;
    onToggleDualWatch?.();
  }

  /**
   * MOR-1652 — equalize and swap are caller-admitted primitive intents. They
   * do not need an observed A/B identity because this surface neither chooses
   * a direction nor derives source/target; an omitted callback remains inert.
   */
  function equalizeVfos(): void {
    if (!dualActions.equalize.operational) return;
    onEqualizeVfos?.();
  }

  function swapVfos(): void {
    if (!dualActions.swap.operational) return;
    onSwapVfos?.();
  }

  function selectMainReceiver(): void {
    if (!dualActions.main.operational) return;
    onSelectMainReceiver?.();
  }

  function selectSubReceiver(): void {
    if (!dualActions.sub.operational) return;
    onSelectSubReceiver?.();
  }

  function speak(): void {
    if (!dualActions.speak.operational) return;
    onSpeak?.();
  }

  /**
   * MOR-1321 — the OPERATIONAL half of the gate, and it applies to the quick
   * triggers only. `quick_split` / `quick_dualwatch` end with the corresponding
   * fact turned ON, so firing one while that fact is unobserved would ask the
   * radio to reach a state this surface cannot see it reach — the same reason
   * `toggleSplit`/`toggleDualWatch` above refuse, and the same `disabled`
   * attribute, not a parallel mechanism.
   *
   * Equalize and swap deliberately have NO such guard: neither reads a fact,
   * both are meaningful whatever split/dual-watch report, and gating them on an
   * unrelated unknown would invent a dependency the radio does not have.
   */
  function quickSplit(): void {
    if (relativeIdentityUnknown || !dualActions.quickSplit.operational
      || viewModel.split.status !== 'known') return;
    onQuickSplit?.();
  }

  function quickDualWatch(): void {
    if (relativeIdentityUnknown || !dualActions.quickDualWatch.operational
      || viewModel.dualWatch.status !== 'known') return;
    onQuickDualWatch?.();
  }

  /**
   * MOR-1321 row 20 — the legacy bridge's `RX <freq> TX <freq>` digest, restated
   * on facts. RX is the ACTIVE VFO (what the operator is listening to); TX comes
   * from the radio-wide `txTarget`, which carries its OWN frequency and is the
   * same derivation the App TX authority uses — so the digest can never disagree
   * with the per-tile TX-target badge. Either side reads `null` — rendered `—`
   * by `formatFrequency` — when its fact is unobserved; neither is ever defaulted
   * to the other's value, which is precisely what a split digest must not do.
   *
   * MOR-1482 disclosure: this digest has always called the SAME
   * `formatFrequency` the tile readout uses (no design-language involvement
   * here — this row is plain text, not a `.vfo-freq` region), so MOR-1482's
   * dot-grouped rewrite of that function changed this row's text too
   * (`RX 14.332000 MHz` → `RX 14.332.000`). Kept as-is rather than restoring
   * a unit suffix: the catalog string (`core.vfo.splitDigest.rx` =
   * `"RX {frequency}"`) never carried a unit itself — "MHz" was only ever a
   * side effect of the old `formatFrequency` — and re-introducing one here
   * would recreate the exact two-formats-on-one-radio inconsistency this
   * ticket exists to remove. The dot-grouped convention (like a ham-radio
   * frequency dial) carries no unit by design; this digest now reads
   * identically to every VFO tile on the same surface.
   */
  let rxFrequencyHz = $derived(viewModel.vfos.find((vfo) => vfo.isActive)?.frequencyHz ?? null);
  let txFrequencyHz = $derived(
    viewModel.txTarget.status === 'known' ? viewModel.txTarget.frequencyHz : null,
  );
  function instrumentSlot(receiver: ReceiverId): string {
    const slot = viewModel.vfos.find((vfo) => vfo.receiver === receiver && vfo.isActiveSlot)?.slot;
    return slot?.kind === 'slotted' ? slot.id : '—';
  }
  let instrumentReceivers = $derived([...new Set(viewModel.vfos.map((vfo) => vfo.receiver))]);
  let receiverIndicators = $derived(
    (viewModel.receiverIndicators ?? []).filter(
      (indicator) => indicatorReceiver === undefined || indicator.receiver === indicatorReceiver,
    ),
  );
</script>

<div class="vfo-surface" role="group" aria-label={groupLabel ?? t('core.vfo.groupLabel')} data-testid="vfo-surface" data-vfo-appearance={appearance}>
  {#snippet activeReceiverStatus()}
  {#if showRadioWideFacts && hasDualReceiver}
    <p
      class="active-receiver"
      data-testid="vfo-active-receiver"
      data-active-receiver={viewModel.activeReceiver.status === 'known'
        ? viewModel.activeReceiver.receiver
        : 'unknown'}
    >
      {viewModel.activeReceiver.status === 'known'
        ? t('core.vfo.activeReceiver.known', { receiver: viewModel.activeReceiver.receiver })
        : t('core.vfo.activeReceiver.unknown')}
    </p>
  {/if}
  {/snippet}
  {#if appearance === 'semantic'}{@render activeReceiverStatus()}{/if}

  {#snippet vfoTile(vfo: VfoViewModel, i: number)}
      {@const selectable = isSelectable(vfo)}
      {@const selectDisabled = selectable && (vfo.slot.kind === 'unknown' || disabled)}
      {@const freq = frequencyDisplay(vfo)}
      {@const pendingHz = pendingFrequencyHz?.[vfo.receiver] ?? null}
      {@const displayHz = displayValue(vfo.display?.frequencyHz, vfo.frequencyHz)}
      {@const displayMode = displayValue(vfo.display?.mode, vfo.mode)}
      {@const displayFilter = displayValue(vfo.display?.filter, vfo.filter)}
      {@const staleDisplay = Object.values(vfo.display ?? {}).some((field) => field.state === 'stale')}
      {@const staleId = `${reasonIdPrefix}-display-${i}`}
      <div
        class="vfo-tile"
        class:is-active={vfo.isActive}
        class:secondary-slot={appearance !== 'semantic' && !vfo.isActiveSlot
          && viewModel.vfos.filter((item) => item.receiver === vfo.receiver).length > 1}
        data-vfo-tile
        data-vfo-receiver={vfo.receiver}
        data-vfo-slot={slotKey(vfo.slot)}
        data-vfo-active={vfo.isActive}
        data-vfo-active-slot={vfo.isActiveSlot}
        data-vfo-tx-target={vfo.isTxTarget}
      >
        <span class="vfo-role">{roleLabel(vfo)}</span>
        <span
          class="vfo-freq"
          {...(appearance === 'semantic' ? freq?.attributes ?? {} : {})}
          data-vfo-freq
          data-freq-tunable={!readoutDisabled(vfo)}
          data-display-state={vfo.display?.frequencyHz.state}
          aria-disabled={hasDigitReadout(vfo) && readoutDisabled(vfo) ? 'true' : undefined}
          aria-describedby={staleDisplay ? staleId : undefined}
        >
          {#if hasDigitReadout(vfo)}
            <!--
              MOR-1441 REVIEW FIX (severe): `freq` is ALWAYS confirmed radio
              truth (`vfo.frequencyHz`), never `pendingHz` — the pending
              target is display-only, via `pendingDisplayHz`. Passing
              `pendingHz` as `freq` fed the growing pending value back into
              `FrequencyDisplayInteractive`'s own gesture arithmetic
              (`adjustFreqByDigit(freq, ...)`), which itself feeds the
              MOR-1425 tuning accumulator's delta — a positive-feedback
              runaway reproduced by the verifier (10 ticks of +10 Hz intent
              measured out to +1910 Hz actual; 30 ticks to +15.7 MHz).

              MOR-1480: this wrapper `<span>` is ALREADY the
              `[data-vfo-tile]`-wrapped `[data-vfo-freq]` hook
              `isFrequencyDisplayFocused()` reads (with `data-freq-tunable`
              on this SAME element, per the tests above) — `vfoFreqHook={false}`
              stops `FrequencyDisplayInteractive` from nesting a second,
              redundant `[data-vfo-freq]` inside it.
            -->
            <FrequencyDisplayInteractive
              freq={vfo.frequencyHz}
              {displayHz}
              disabled={readoutDisabled(vfo)}
              contextKey={`${viewModel.topologyId}:${vfo.receiver}:${slotKey(vfo.slot)}`}
              pendingDisplayHz={pendingHz}
              pendingAnnouncement={pendingHz !== null ? t('core.vfo.freq.pendingAnnouncement') : undefined}
              compact={appearance === 'semantic'}
              active={vfo.isActive}
              receiver={vfo.receiver === 'SUB' ? 'sub' : 'main'}
              onFreqChange={(hz) => tuneFrequency(vfo, hz)}
              vfoFreqHook={false}
            />
          {:else}
            <!--
              MOR-1482 (owner ruling, session 19) — the tile frequency
              self-renders unconditionally, the SAME MOR-1322 option-(b)
              precedent the tunable branch above already follows: the
              design-language grammar opts OUT of this slot's TEXT. A
              language's hero-ranked, grouped-digit grammar (studioline's
              `THIN_SPACE`-separated groups, fieldline's ungrouped run) is
              built for a hero-scale mount; flattened to a plain string at
              tile scale it loses its own ranking/geometry and reads as an
              unformatted digit run — worse than the plain fallback it was
              meant to improve on. `freq.attributes` (the region claim) is
              still spread on this `<span>` above, unconditionally — a
              language keeps its hooks on the tile even though it no longer
              supplies the tile's text. `frequencyDisplay`'s `.text` output
              is therefore unconsumed in production (see the doc note next
              to `RendererDisplay.text` in `design-language-renderers.ts`);
              a future hero-scale mount (not this tile) is the intended
              consumer.
            -->
            {formatFrequency(displayHz)}
          {/if}
        </span>
        <span class="vfo-mode">{displayMode ?? '—'}{displayFilter ? ` / ${displayFilter}` : ''}</span>
        <span id={staleId} class="vfo-stale-cue" class:stale={staleDisplay}
          data-vfo-stale-cue aria-hidden={!staleDisplay}
          title={staleDisplay ? t('core.rxTx.target.reason.stale') : undefined}>
          <span aria-hidden="true">†</span><span class="sr-only">{t('core.rxTx.target.reason.stale')}</span>
        </span>
        {#if vfo.isTxTarget}
          <span class="vfo-badge" data-vfo-tx-badge>{t('core.vfo.txTarget.label')}</span>
        {/if}
        {#if selectable}
          {@const selectReason = selectReasonText(vfo)}
          <button
            type="button"
            class="vfo-select"
            data-vfo-select
            disabled={selectDisabled}
            title={selectReason}
            aria-describedby={reasonId(`select-${i}`, selectReason)}
            aria-label={t('core.vfo.selectAction', { label: vfo.label })}
            onclick={() => selectVfo(vfo)}
          >
            {vfo.label}
          </button>
          {#if selectReason !== undefined}
            <span id={reasonId(`select-${i}`, selectReason)} class="sr-only">{selectReason}</span>
          {/if}
        {:else}
          <!--
            MOR-1482 — `.vfo-role` above already paints this tile's identity
            (byte-identical to `vfo.label` in every real case: 'relative' and
            'slotted' slots both carry the exact same string via the adapter,
            per `radio-view-model-adapter.ts`; only the rare 'unknown'-slot
            case differs). A second painted copy read as a duplicated label
            strip. `sr-only` keeps this span in the DOM (assistive tech and
            the `[data-vfo-label]` hook both still see it) without a second
            visible copy of the same words.
          -->
          <span class="vfo-label sr-only" data-vfo-label>{vfo.label}</span>
        {/if}
      </div>
  {/snippet}
  {#snippet identitySelectors()}
  {#if relativeIdentityUnknown && relativeReceiver !== null}
    {@const absoluteReason = disabled
      ? t('core.vfo.select.receiverUnavailableReason')
      : relativeSelectionPending
        ? t('core.vfo.select.pendingReason')
        : undefined}
    {@const absoluteReasonId = reasonId('select-absolute', absoluteReason)}
    <div class="vfo-identity-selectors" data-testid="vfo-identity-selectors">
      <button
        type="button" class="vfo-select" data-vfo-select-absolute="A"
        title={absoluteReason ?? relativeSelectionHelp} aria-label="Select VFO A"
        aria-describedby={absoluteReasonId}
        disabled={disabled || relativeSelectionPending}
        onclick={() => selectAbsoluteSlot('A')}
      >Select VFO A</button>
      <button
        type="button" class="vfo-select" data-vfo-select-absolute="B"
        title={absoluteReason ?? relativeSelectionHelp} aria-label="Select VFO B"
        aria-describedby={absoluteReasonId}
        disabled={disabled || relativeSelectionPending}
        onclick={() => selectAbsoluteSlot('B')}
      >Select VFO B</button>
      {#if absoluteReason !== undefined}
        <span id={absoluteReasonId} class="sr-only">{absoluteReason}</span>
      {/if}
    </div>
  {/if}
  {/snippet}

  {#snippet radioWideContent()}
  {#if showRadioWideFacts}
    {#if viewModel.radioWideIndicators}
      <VfoIndicatorRow
        radioWide={viewModel.radioWideIndicators}
        {appearance}
      />
    {/if}
    {@const splitReason = viewModel.split.status === 'unknown' ? t('core.vfo.split.unknownReason') : undefined}
    {@const dualWatchReason = viewModel.dualWatch.status === 'unknown' ? t('core.vfo.dualWatch.unknownReason') : undefined}
    <div class="fact-toggles">
      <button
        type="button"
        class="fact-toggle"
        data-vfo-split
        role="switch"
        aria-checked={triState(viewModel.split)}
        aria-label={t('core.vfo.split.label')}
        title={splitReason}
        aria-describedby={reasonId('split', splitReason)}
        disabled={viewModel.split.status === 'unknown'}
        onclick={toggleSplit}
      >
        {t('core.vfo.split.label')}: {stateWord(viewModel.split)}
      </button>
      {#if splitReason !== undefined}
        <span id={reasonId('split', splitReason)} class="sr-only">{splitReason}</span>
      {/if}
      {#if hasDualReceiver}
        <button
          type="button"
          class="fact-toggle"
          data-vfo-dual-watch
          role="switch"
          aria-checked={triState(viewModel.dualWatch)}
          aria-label={t('core.vfo.dualWatch.label')}
          title={dualWatchReason}
          aria-describedby={reasonId('dual-watch', dualWatchReason)}
          disabled={viewModel.dualWatch.status === 'unknown'}
          onclick={toggleDualWatch}
        >
          {t('core.vfo.dualWatch.label')}: {stateWord(viewModel.dualWatch)}
        </button>
        {#if dualWatchReason !== undefined}
          <span id={reasonId('dual-watch', dualWatchReason)} class="sr-only">{dualWatchReason}</span>
        {/if}
      {/if}
    </div>

    <!--
      MOR-1321. Actions, not facts — so no `role="switch"` and no `aria-checked`:
      each button DOES a thing once, it does not report a state. The two quick
      triggers carry the same `disabled` gate their fact toggles above carry.
      Button text is the accessible name; no `aria-label` duplicates it.
    -->
    {#if hasDualActions}
      {@const equalizeReason = undefined}
      {@const swapReason = undefined}
      {@const quickSplitReason = quickSplitReasonText()}
      {@const quickDualWatchReason = quickDualWatchReasonText()}
      <div
        class="vfo-ops" data-testid="vfo-ops" data-dual-action-block
        data-disabled-reason={relativeIdentityUnknown ? 'vfo-identity-unknown' : undefined}
        title={identityOnlyReasonText()}
      >
        {#if dualActions.main.structural}
          <button type="button" class="vfo-op" data-dual-action="main"
            aria-pressed={viewModel.activeReceiver.status === 'known' && viewModel.activeReceiver.receiver === 'MAIN'}
            disabled={!dualActions.main.operational || !onSelectMainReceiver}
            onclick={selectMainReceiver}>MAIN</button>
        {/if}
        {#if dualActions.sub.structural}
          <button type="button" class="vfo-op" data-dual-action="sub"
            aria-pressed={viewModel.activeReceiver.status === 'known' && viewModel.activeReceiver.receiver === 'SUB'}
            disabled={!dualActions.sub.operational || !onSelectSubReceiver}
            onclick={selectSubReceiver}>SUB</button>
        {/if}
        {#if dualActions.equalize.structural}
          <button type="button" class="vfo-op" data-vfo-equalize data-dual-action="equalize"
            aria-label={vfoEqualLabel(viewModel.vfoScheme)}
            title={equalizeReason} aria-describedby={reasonId('equalize', equalizeReason)}
            disabled={!dualActions.equalize.operational || !onEqualizeVfos} onclick={equalizeVfos}>
            {vfoEqualLabel(viewModel.vfoScheme)}
          </button>
        {/if}
        {#if dualActions.swap.structural}
          <button type="button" class="vfo-op" data-vfo-swap data-dual-action="swap"
            aria-label={vfoSwapLabel(viewModel.vfoScheme)}
            title={swapReason} aria-describedby={reasonId('swap', swapReason)}
            disabled={!dualActions.swap.operational || !onSwapVfos} onclick={swapVfos}>
            {vfoSwapLabel(viewModel.vfoScheme)}
          </button>
        {/if}
        {#if dualActions.quickSplit.structural}
          <button
            type="button" class="vfo-op" data-vfo-quick-split data-dual-action="quick-split"
            aria-label="Quick split"
            title={quickSplitReason} aria-describedby={reasonId('quick-split', quickSplitReason)}
            disabled={relativeIdentityUnknown || viewModel.split.status !== 'known'
              || !dualActions.quickSplit.operational || !onQuickSplit}
            onclick={quickSplit}
          >
            Quick split
          </button>
        {/if}
        {#if dualActions.quickDualWatch.structural}
          <button
            type="button" class="vfo-op" data-vfo-quick-dual-watch data-dual-action="quick-dual-watch"
            aria-label="Quick dual watch"
            title={quickDualWatchReason} aria-describedby={reasonId('quick-dual-watch', quickDualWatchReason)}
            disabled={relativeIdentityUnknown || viewModel.dualWatch.status !== 'known'
              || !dualActions.quickDualWatch.operational || !onQuickDualWatch}
            onclick={quickDualWatch}
          >
            Quick dual watch
          </button>
        {/if}
        {#if dualActions.speak.structural}
          <button type="button" class="vfo-op" data-dual-action="speak"
            title="Speak current frequency aloud"
            disabled={!dualActions.speak.operational || !onSpeak}
            onclick={speak}>SPEAK</button>
        {/if}
        {#if equalizeReason !== undefined}
          <span id={reasonId('equalize', equalizeReason)} class="sr-only">{equalizeReason}</span>
        {/if}
        {#if swapReason !== undefined}
          <span id={reasonId('swap', swapReason)} class="sr-only">{swapReason}</span>
        {/if}
        {#if quickSplitReason !== undefined}
          <span id={reasonId('quick-split', quickSplitReason)} class="sr-only">{quickSplitReason}</span>
        {/if}
        {#if quickDualWatchReason !== undefined}
          <span id={reasonId('quick-dual-watch', quickDualWatchReason)} class="sr-only">{quickDualWatchReason}</span>
        {/if}
      </div>
    {/if}

    {#if hasVfoPair}
      <!--
        `data-split-active` carries the split fact's TRI-STATE, so "unknown" is
        stated rather than collapsed into the legacy bridge's binary dimming.
      -->
      <p class="split-digest" data-testid="vfo-split-digest" data-split-active={triState(viewModel.split)}>
        <span data-split-rx>{t('core.vfo.splitDigest.rx', { frequency: formatFrequency(rxFrequencyHz) })}</span>
        <span data-split-tx>{t('core.vfo.splitDigest.tx', { frequency: formatFrequency(txFrequencyHz) })}</span>
      </p>
    {/if}
  {/if}
  {/snippet}

  {#snippet receiverInstrument(receiver: ReceiverId)}
    <section class="receiver-instrument" data-receiver-instrument={receiver}
      class:instrument-active={viewModel.vfos.some((vfo) => vfo.receiver === receiver && vfo.isActive)}
      aria-label={`${receiver} instrument`}>
      <VfoIndicatorRow indicator={receiverIndicators.find((item) => item.receiver === receiver)} {appearance}
        slotLabel={instrumentSlot(receiver)}>
        <div class="freq-stack">
          {#each viewModel.vfos as vfo, i (vfo.receiver + ':' + i)}
            {#if vfo.receiver === receiver}{@render vfoTile(vfo, i)}{/if}
          {/each}
        </div>
      </VfoIndicatorRow>
    </section>
  {/snippet}

  {#if appearance !== 'semantic' && showVfoList}
    <div class="instrument-panel" data-testid="vfo-instrument-panel">
      {#if instrumentReceivers[0]}{@render receiverInstrument(instrumentReceivers[0])}{/if}
      {#if showRadioWideFacts}
        <div class="bridge" data-instrument-bridge>
          {@render activeReceiverStatus()}
          {@render identitySelectors()}
          {@render radioWideContent()}
        </div>
      {/if}
      {#each instrumentReceivers.slice(1) as receiver (receiver)}
        {@render receiverInstrument(receiver)}
      {/each}
    </div>
  {:else}
    {#if showVfoList}
      <div class="vfo-list" data-testid="vfo-list">
        {#each viewModel.vfos as vfo, i (vfo.receiver + ':' + i)}{@render vfoTile(vfo, i)}{/each}
      </div>
      {@render identitySelectors()}
      {#if receiverIndicators.length > 0}
        <div class="receiver-indicators" data-testid="vfo-receiver-indicators">
          {#each receiverIndicators as indicator (indicator.receiver)}<VfoIndicatorRow {indicator} />{/each}
        </div>
      {/if}
    {/if}
    {@render radioWideContent()}
  {/if}
</div>

<style>
  /* Semantic-neutral layout only — existing --v2-* theme tokens, sensible fallbacks. */
  .vfo-surface { display: flex; flex-direction: column; gap: 8px; font-family: 'Roboto Mono', monospace; color: var(--v2-text-primary, #e8e8e8); }
  .active-receiver { margin: 0; font-size: 11px; color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55)); }
  .vfo-list { display: flex; flex-wrap: wrap; gap: 6px; }
  .receiver-indicators { display: grid; gap: 6px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
  .vfo-tile { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12)); border-radius: 4px; background: var(--v2-bg-panel, rgba(255, 255, 255, 0.03)); }
  .vfo-tile.is-active { border-color: var(--v2-accent-cyan, #00d4ff); }
  .vfo-role { font-weight: 700; color: var(--v2-text-secondary, rgba(255, 255, 255, 0.8)); }
  .vfo-stale-cue { visibility: hidden; font-size: 10px; inline-size: 1ch; flex: 0 0 1ch; }
  .vfo-stale-cue.stale { visibility: visible; }
  /* Reserve the semantic tile's upper trailing corner, not another flex gap.
     A flow slot can wrap a 375px phone row even while its cue is hidden. */
  [data-vfo-appearance='semantic'] .vfo-tile { position: relative; }
  [data-vfo-appearance='semantic'] .vfo-stale-cue {
    position: absolute; inset-inline-end: 1px; inset-block-start: 1px;
    line-height: 1; letter-spacing: normal;
  }
  .vfo-badge { padding: 1px 4px; border-radius: 3px; font-size: 10px; color: var(--v2-accent-red, #ff2020); border: 1px solid var(--v2-accent-red, #ff2020); }
  .vfo-select, .fact-toggle, .vfo-op { border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12)); border-radius: 4px; background: transparent; color: inherit; cursor: pointer; padding: 3px 6px; }
  .vfo-select:disabled, .fact-toggle:disabled, .vfo-op:disabled { color: var(--v2-text-disabled, rgba(255, 255, 255, 0.3)); cursor: not-allowed; }
  .fact-toggles, .vfo-ops, .vfo-identity-selectors { display: flex; gap: 6px; flex-wrap: wrap; }
  .split-digest { display: flex; gap: 8px; margin: 0; font-size: 11px; color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55)); }
  .split-digest[data-split-active='false'] { opacity: 0.64; }
  /* MOR-1481: the `aria-describedby` target for a disabled control's reason
     — present for screen readers, never painted (the `title` attribute
     already carries the sighted-hover channel). Mirrors `TxAuxSurface`. */
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }

  /* Receiver/bridge composition ported from v2.11.1 SdrVfoScreen and VfoHeader. */
  .instrument-panel {
    display: flex; align-items: stretch; width: 100%; min-width: 0;
    background: linear-gradient(180deg, var(--v2-bg-gradient-start, #0a0e14) 0%, var(--v2-bg-panel, #05080c) 100%);
    border: 1px solid var(--v2-border-panel, #18222d); border-radius: 4px;
  }
  .receiver-instrument { flex: 1 1 490px; min-width: 0; padding: 6px 12px; }
  .receiver-instrument + .receiver-instrument { border-left: 1px solid var(--v2-border-panel, #18222d); }
  .bridge {
    flex: 0 0 180px; min-width: 0; display: flex; flex-direction: column;
    justify-content: center; gap: 10px; padding: 10px;
    border-inline: 1px solid var(--v2-border-panel, #18222d);
  }
  .freq-stack { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .receiver-instrument :where(.vfo-tile) {
    display: grid; grid-template-columns: 1fr auto; gap: 6px;
    padding: 0; border: 0; background: transparent;
  }
  .receiver-instrument .vfo-role { font-size: 12px; letter-spacing: .1em; }
  .receiver-instrument .vfo-mode { font-size: 12px; grid-column: 1; }
  .receiver-instrument .vfo-freq {
    grid-column: 1 / -1; grid-row: 3; white-space: nowrap;
    font-size: clamp(26px, 3.6vw, 52px); line-height: 1.2; letter-spacing: .01em;
    color: var(--v2-text-dim, #6f8196); text-align: right; margin: 20px 0 6px;
  }
  /* The face owns geometry; the selected design language owns numeral weight
     and font family. The interactive primitive must inherit that typography. */
  .receiver-instrument :where(.vfo-freq) { font-weight: 700; }
  .receiver-instrument .vfo-freq :global(.freq) {
    font-size: inherit; line-height: inherit; font-weight: inherit; font-family: inherit;
  }
  .receiver-instrument .is-active .vfo-freq {
    color: var(--v2-vfo-main-freq-active, #7cfce5);
    text-shadow: 0 0 12px rgba(124,252,229,.5);
  }
  .receiver-instrument .secondary-slot .vfo-freq { font-size: 18px; margin: 2px 0; }
  .receiver-instrument .vfo-select { justify-self: end; grid-column: 2; grid-row: 1 / 3; }
  .bridge .fact-toggles, .bridge .vfo-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .bridge .fact-toggle, .bridge .vfo-op, .bridge .vfo-select {
    min-height: 28px; padding: 4px 6px; border-radius: 3px; font-size: 10px;
    font-family: inherit; font-weight: 700; letter-spacing: .04em;
    color: var(--v2-text-secondary, #a0b4c8);
    border: 1px solid rgba(72,96,122,.4);
    background: linear-gradient(180deg, var(--v2-control-button-gradient-top, #202a35) 0%, var(--v2-control-button-gradient-bottom, #0b1017) 100%);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.12);
  }
  .bridge .fact-toggle { grid-column: 1 / -1; }
  .bridge button:disabled { opacity: .5; }
  .bridge button[aria-pressed='true'], .bridge button[aria-checked='true'] {
    border-color: var(--v2-accent-cyan, #00d4ff); color: var(--v2-accent-cyan, #7cfce5);
  }
  .bridge .split-digest { flex-wrap: wrap; justify-content: center; font-size: 9px; }
  .bridge .vfo-identity-selectors { flex-direction: column; }
  [data-vfo-appearance='standard'] .receiver-instrument { padding: 8px; }
  [data-vfo-appearance='standard'] .instrument-active {
    border: 1px solid var(--v2-accent-cyan, #00d4ff); border-radius: 4px;
    box-shadow: 0 0 6px rgba(0,212,255,.3), inset 0 0 16px rgba(0,212,255,.06);
  }
  [data-vfo-appearance='standard'] .bridge { flex-basis: 136px; }
  [data-vfo-appearance='standard'] .receiver-instrument :where(.vfo-tile) {
    grid-template-columns: auto minmax(0, 1fr) auto;
    grid-template-rows: auto auto;
  }
  [data-vfo-appearance='standard'] .vfo-role { grid-column: 1; grid-row: 1; }
  [data-vfo-appearance='standard'] .vfo-mode {
    grid-column: 2; grid-row: 1; justify-self: start;
  }
  [data-vfo-appearance='standard'] .vfo-freq {
    grid-column: 1 / -1; grid-row: 2;
    font-size: clamp(34px, 3vw, 44px); margin: 4px 0; text-align: left;
  }
  [data-vfo-appearance='standard'] .receiver-instrument .vfo-select {
    grid-column: 3; grid-row: 1 / 3;
  }
  [data-vfo-appearance='standard'] .receiver-instrument .secondary-slot {
    grid-template-columns: auto auto minmax(0, 1fr) auto;
    grid-template-rows: auto;
  }
  [data-vfo-appearance='standard'] .secondary-slot .vfo-role {
    grid-column: 1; grid-row: 1;
  }
  [data-vfo-appearance='standard'] .secondary-slot .vfo-freq {
    grid-column: 2; grid-row: 1; margin: 0;
  }
  [data-vfo-appearance='standard'] .secondary-slot .vfo-mode {
    grid-column: 3; grid-row: 1;
  }
  [data-vfo-appearance='standard'] .receiver-instrument .secondary-slot .vfo-select {
    grid-column: 4; grid-row: 1;
  }
  @media (max-width: 1050px) {
    .instrument-panel { flex-wrap: wrap; }
    .receiver-instrument { flex-basis: calc(50% - 90px); }
    .bridge { flex-basis: 150px; }
    .receiver-instrument .vfo-freq { font-size: 26px; }
  }
  @media (max-width: 760px) {
    .instrument-panel { flex-direction: column; }
    .receiver-instrument, .bridge { flex-basis: auto; }
    .bridge { border-inline: 0; border-block: 1px solid var(--v2-border-panel, #18222d); }
  }
</style>
