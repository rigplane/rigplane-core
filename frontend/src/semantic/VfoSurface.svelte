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
  import FrequencyDisplayInteractive from '../primitives/frequency/FrequencyDisplayInteractive.svelte';
  import { renderSlot } from './design-language-renderers';
  import type { BooleanFact, RadioViewModel, VfoViewModel } from './radio-view-model';

  interface Props {
    viewModel: RadioViewModel;
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
     * MOR-1321 (v3-rework slice S3a) — the VFO-scoped ACTIONS the legacy
     * `VfoOps` bridge carried and the semantic deck lost at MOR-1313: equalize
     * (copy one VFO onto the other), swap, and the two composite "quick"
     * triggers the backend performs atomically as equalize-then-toggle-on
     * (`quick_split` / `quick_dualwatch`, epic #774).
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
  }

  let {
    viewModel,
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
    onEqualizeVfos,
    onSwapVfos,
    onQuickSplit,
    onQuickDualWatch,
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

  function formatFrequency(hz: number | null): string {
    return hz === null ? '—' : `${(hz / 1_000_000).toFixed(6)} MHz`;
  }

  /**
   * MOR-1275: the active design language's `frequencyDisplay` renderer, given
   * the ONE fact it is entitled to — this tile's own frequency, already a prop.
   * `null` whenever no language is active or none declares that renderer, in
   * which case `formatFrequency` above renders exactly as it always did. This
   * changes the READOUT only: which tiles exist, which controls are gated and
   * every accessible name are decided above and are not passed in.
   */
  function frequencyDisplay(vfo: VfoViewModel): ReturnType<typeof renderSlot> {
    return renderSlot('frequencyDisplay', { frequencyHz: vfo.frequencyHz });
  }

  /**
   * MOR-1322 (S3b) — per-digit tuning parity with the legacy VfoHeader, under
   * the owner's option (b): tuning OPTS OUT of the design language. The
   * MOR-1275 `frequencyDisplay` renderer stays display-only — it is never asked
   * to produce interactive markup — and the digit control self-renders.
   *
   * COMPOSITION RULE: one readout slot (`.vfo-freq`), two MUTUALLY EXCLUSIVE
   * fillings. Tunable → the self-rendered digit control; not tunable → the
   * language's text, or `formatFrequency` when no language is active (today's
   * behaviour, byte-identical). Never both: two elements painting the same
   * frequency is a double readout, and the operator must see exactly one number
   * per VFO. Because both fillings read the SAME single fact
   * (`vfo.frequencyHz`), they cannot show conflicting values by construction —
   * that is the property the tests pin, in both language states.
   *
   * The language keeps its claim on the REGION either way: `freq.attributes`
   * are spread onto the slot in both branches, so a language's tokens/hooks
   * still decorate the frequency area. What opts out is the rendering of the
   * VALUE, which is exactly what "the renderer stays display-only" means.
   *
   * Two-level gating (MOR-977), and the split matters — it is what makes the
   * guard REACHABLE and therefore independently testable:
   *   STRUCTURAL (`hasTunableFrequency`) — no observed frequency, no tune
   *     intent wired, or the tile is NOT ITS RECEIVER'S ACTIVE SLOT: there is
   *     nothing this tile can tune, so no control mounts and the slot shows the
   *     plain readout. ABSENT.
   *
   *     The active-slot term is load-bearing and was a real bug without it
   *     (MOR-1322 verification B1). The tune intent is RECEIVER-scoped —
   *     `set_freq {receiver}` writes that receiver's *active* VFO — so a
   *     control on a tile that is not that receiver's active slot would take
   *     its step from VFO B's digits and move VFO A: the operator scrolls one
   *     VFO and watches a different one move.
   *
   *     MOR-1335 (G4) qualifies the term PER RECEIVER. B1 first spelled it
   *     `isActive`, which is globally unique, so on `2/main_sub` (IC-7610) only
   *     one tile on the WHOLE radio was tunable and the SUB receiver lost the
   *     per-digit tuning the legacy `VfoPanel` had. That legacy shape is the
   *     specification: ONE widget per RECEIVER, over that receiver's current
   *     frequency. `isActiveSlot` is exactly that correspondence, and it does
   *     not widen the hazard — the hazard is INTRA-receiver (which VFO of this
   *     receiver `set_freq` lands on), and the intent carries `vfo.receiver`,
   *     so a SUB tile can only ever address SUB. Unobserved active-slot
   *     readings are `false` on both of that receiver's slots (the contract's
   *     fail-closed rule), so an unknown never becomes a tunable guess.
   *   OPERATIONAL (`disabled`, a MOR-1256 strip whose receiver is present but
   *     unavailable) — the control DOES mount, marked `aria-disabled`, and
   *     `tuneFrequency` refuses the dispatch. PRESENT BUT INERT, exactly as the
   *     select controls in this same surface behave.
   *
   * Folding `disabled` into the structural gate instead would make the guard
   * unreachable from the DOM — a mutant deleting it would survive, which is
   * precisely the MOR-1321 B2 finding. Here the markup gate and the handler
   * guard are two mechanisms on two different conditions, and each is pinned
   * on its own by bypassing the other.
   */
  function hasTunableFrequency(vfo: VfoViewModel): boolean {
    return vfo.isActiveSlot && vfo.frequencyHz !== null && onTuneFrequency !== undefined;
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
    if (relativeIdentityUnknown || viewModel.split.status !== 'known') return;
    onQuickSplit?.();
  }

  function quickDualWatch(): void {
    if (relativeIdentityUnknown || viewModel.dualWatch.status !== 'known') return;
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
   */
  let rxFrequencyHz = $derived(viewModel.vfos.find((vfo) => vfo.isActive)?.frequencyHz ?? null);
  let txFrequencyHz = $derived(
    viewModel.txTarget.status === 'known' ? viewModel.txTarget.frequencyHz : null,
  );
</script>

<div class="vfo-surface" role="group" aria-label={groupLabel ?? t('core.vfo.groupLabel')} data-testid="vfo-surface">
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

  {#if showVfoList}
  <div class="vfo-list" data-testid="vfo-list">
    {#each viewModel.vfos as vfo, i (vfo.receiver + ':' + i)}
      {@const selectable = isSelectable(vfo)}
      {@const selectDisabled = selectable && (vfo.slot.kind === 'unknown' || disabled)}
      {@const freq = frequencyDisplay(vfo)}
      {@const pendingHz = pendingFrequencyHz?.[vfo.receiver] ?? null}
      <div
        class="vfo-tile"
        class:is-active={vfo.isActive}
        data-vfo-tile
        data-vfo-receiver={vfo.receiver}
        data-vfo-slot={slotKey(vfo.slot)}
        data-vfo-active={vfo.isActive}
        data-vfo-active-slot={vfo.isActiveSlot}
        data-vfo-tx-target={vfo.isTxTarget}
      >
        <span class="vfo-role">{roleLabel(vfo)}</span>
        <!--
          MOR-1322 — ONE readout slot, two mutually exclusive fillings. See the
          `tuneFrequency` comment for the composition rule and why "alongside"
          cannot mean two visible numbers.
        -->
        <span
          class="vfo-freq"
          {...freq?.attributes ?? {}}
          data-vfo-freq
          data-freq-tunable={hasTunableFrequency(vfo) && !disabled}
          aria-disabled={hasTunableFrequency(vfo) && disabled ? 'true' : undefined}
        >
          {#if hasTunableFrequency(vfo)}
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
              freq={vfo.frequencyHz ?? 0}
              pendingDisplayHz={pendingHz}
              pendingAnnouncement={pendingHz !== null ? t('core.vfo.freq.pendingAnnouncement') : undefined}
              compact
              active={vfo.isActive}
              receiver={vfo.receiver === 'SUB' ? 'sub' : 'main'}
              onFreqChange={(hz) => tuneFrequency(vfo, hz)}
              vfoFreqHook={false}
            />
          {:else}
            {freq?.text ?? formatFrequency(vfo.frequencyHz)}
          {/if}
        </span>
        <span class="vfo-mode">{vfo.mode ?? '—'}{vfo.filter ? ` / ${vfo.filter}` : ''}</span>
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
          <span class="vfo-label" data-vfo-label>{vfo.label}</span>
        {/if}
      </div>
    {/each}
  </div>
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
  {/if}

  {#if showRadioWideFacts}
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
    {#if hasVfoPair}
      {@const equalizeReason = identityOnlyReasonText()}
      {@const swapReason = identityOnlyReasonText()}
      {@const quickSplitReason = quickSplitReasonText()}
      {@const quickDualWatchReason = quickDualWatchReasonText()}
      <div
        class="vfo-ops" data-testid="vfo-ops"
        data-disabled-reason={relativeIdentityUnknown ? 'vfo-identity-unknown' : undefined}
        title={identityOnlyReasonText()}
      >
        <button type="button" class="vfo-op" data-vfo-equalize
          title={equalizeReason} aria-describedby={reasonId('equalize', equalizeReason)}
          disabled={relativeIdentityUnknown} onclick={() => { if (!relativeIdentityUnknown) onEqualizeVfos?.(); }}>
          {t('core.vfo.ops.equalize')}
        </button>
        <button type="button" class="vfo-op" data-vfo-swap
          title={swapReason} aria-describedby={reasonId('swap', swapReason)}
          disabled={relativeIdentityUnknown} onclick={() => { if (!relativeIdentityUnknown) onSwapVfos?.(); }}>
          {t('core.vfo.ops.swap')}
        </button>
        <button
          type="button" class="vfo-op" data-vfo-quick-split
          title={quickSplitReason} aria-describedby={reasonId('quick-split', quickSplitReason)}
          disabled={relativeIdentityUnknown || viewModel.split.status === 'unknown'}
          onclick={quickSplit}
        >
          {t('core.vfo.ops.quickSplit')}
        </button>
        <button
          type="button" class="vfo-op" data-vfo-quick-dual-watch
          title={quickDualWatchReason} aria-describedby={reasonId('quick-dual-watch', quickDualWatchReason)}
          disabled={relativeIdentityUnknown || viewModel.dualWatch.status === 'unknown'}
          onclick={quickDualWatch}
        >
          {t('core.vfo.ops.quickDualWatch')}
        </button>
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
</div>

<style>
  /* Semantic-neutral layout only — existing --v2-* theme tokens, sensible fallbacks. */
  .vfo-surface { display: flex; flex-direction: column; gap: 8px; font-family: 'Roboto Mono', monospace; color: var(--v2-text-primary, #e8e8e8); }
  .active-receiver { margin: 0; font-size: 11px; color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55)); }
  .vfo-list { display: flex; flex-wrap: wrap; gap: 6px; }
  .vfo-tile { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12)); border-radius: 4px; background: var(--v2-bg-panel, rgba(255, 255, 255, 0.03)); }
  .vfo-tile.is-active { border-color: var(--v2-accent-cyan, #00d4ff); }
  .vfo-role { font-weight: 700; color: var(--v2-text-secondary, rgba(255, 255, 255, 0.8)); }
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
</style>
