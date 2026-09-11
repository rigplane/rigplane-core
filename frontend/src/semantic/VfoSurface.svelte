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
  import type { Snippet } from 'svelte';
  import { t } from '$lib/i18n';
  import FrequencyDisplayInteractive from '../primitives/frequency/FrequencyDisplayInteractive.svelte';
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import type { SignalMeterFrame } from '../components-v2/meters/signal-meter-motion.svelte';
  import VfoPanel from '../components-v2/vfo/VfoPanel.svelte';
  import VfoIndicatorRow from './VfoIndicatorRow.svelte';
  import VfoOperationGroup from './VfoOperationGroup.svelte';
  import { formatKnownLevel } from './format-level';
  import { RF_FRONT_END_LEVELS } from './rf-front-end-instruments';
  import {
    invokeVfoOperation,
    projectVfoOperations,
    type VfoOperationIntent,
    type VfoOperationProjectionInput,
  } from './vfo-operation-projection';
  import type { ReceiverInstrumentHandles } from './ReceiverInstrumentHost.svelte';
  import { splitFrequencyToDigits, groupDigitsForDisplay } from '../primitives/frequency/frequency-tuning';
  import { renderSlot } from './design-language-renderers';
  import type { MeterContinuitySession } from '../primitives/meters/meter-ballistics.svelte';
  import type { BooleanFact, DisplayObservation, RadioViewModel, ReceiverIndicatorViewModel, VfoViewModel } from './radio-view-model';

  interface Props {
    viewModel: RadioViewModel;
    appearance?: 'semantic' | 'sdr' | 'standard';
    onSelectVfo?: (target: VfoSelection) => void;
    /** Standard-face digit activation with exact VFO identity and focus return node. */
    onOpenFrequencyEntry?: (target: VfoSelection, trigger: HTMLElement) => void;
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
     * MOR-2425 / T207 — a STOPGAP. `true` withholds the "Select VFO A /
     * Select VFO B" pair. It resolves which of a one-receiver radio's two VFO
     * POSITIONS is A and which is B: on a deck that gives each position its
     * own column that is a relation BETWEEN the columns, and drawn inside one
     * column it reads as that column's own control. The slot-stripped deck
     * sets this until the relation has a surface of its own (T183). Defaults
     * to `false`: every other caller renders exactly as before.
     */
    suppressIdentitySelectors?: boolean;
    continuitySession?: MeterContinuitySession | null;
    frequencyLifetimeKey?: string;
    receiverInstruments?: ReceiverInstrumentHandles;
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
    operationInput?: VfoOperationProjectionInput;
    operationControls?: Snippet;
  }

  let {
    viewModel, appearance = 'semantic',
    onSelectVfo,
    onOpenFrequencyEntry,
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
    suppressIdentitySelectors = false,
    continuitySession,
    frequencyLifetimeKey,
    receiverInstruments,
    onEqualizeVfos,
    onSwapVfos,
    onQuickSplit,
    onQuickDualWatch,
    onSelectMainReceiver,
    onSelectSubReceiver,
    onSpeak,
    operationInput,
    operationControls,
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

  function localVfoOperationInput(): VfoOperationProjectionInput {
    return {
      hasVfoPair,
      hasDualReceiver,
      relativeIdentityUnknown,
      activeReceiver: viewModel.activeReceiver,
      split: viewModel.split,
      dualWatch: viewModel.dualWatch,
      actions: viewModel.radioWideIndicators?.actions,
      callbacks: {
        onToggleSplit,
        onToggleDualWatch,
        onSelectMainReceiver,
        onSelectSubReceiver,
        onEqualizeVfos,
        onSwapVfos,
        onQuickSplit,
        onQuickDualWatch,
        onSpeak,
      },
      reasons: {
        receiverUnavailable: t('core.vfo.select.receiverUnavailableReason'),
        identityUnknown: t('core.vfo.ops.identityUnknownReason'),
        splitUnknown: t('core.vfo.split.unknownReason'),
        dualWatchUnknown: t('core.vfo.dualWatch.unknownReason'),
      },
    };
  }

  const currentVfoOperationInput = (): VfoOperationProjectionInput =>
    operationInput ?? localVfoOperationInput();
  let vfoOperations = $derived(projectVfoOperations(currentVfoOperationInput()));

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
    if (receiverInstruments !== undefined) return vfo.isActiveSlot
      && (vfo.receiver === 'MAIN' || receiverInstruments.subFrequency !== undefined);
    return vfo.isActiveSlot && onTuneFrequency !== undefined
      && (vfo.frequencyHz !== null || vfo.display !== undefined);
  }

  function hasTunableFrequency(vfo: VfoViewModel): boolean {
    if (receiverInstruments !== undefined) return vfo.isActiveSlot
      && receiverInstruments.frequencyTunable(vfo.receiver)
      && (vfo.receiver === 'MAIN' || receiverInstruments.subFrequency !== undefined);
    return vfo.isActiveSlot && vfo.frequencyHz !== null && onTuneFrequency !== undefined;
  }

  function readoutDisabled(vfo: VfoViewModel): boolean {
    // MOR-2425/R29+R40: a HELD frequency stays tunable. Freshness alone no
    // longer locks the readout; a display carrying no value still does.
    return disabled || !hasTunableFrequency(vfo)
      || (vfo.display !== undefined
        && vfo.display.frequencyHz.state !== 'current'
        && vfo.display.frequencyHz.state !== 'stale');
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

  function handleOperationIntent(intent: VfoOperationIntent): void {
    invokeVfoOperation(currentVfoOperationInput, intent);
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
  let standardPair = $derived.by(() => {
    if (appearance !== 'standard' || instrumentReceivers.length !== 1) return null;
    const receiver = instrumentReceivers[0];
    const records = viewModel.vfos.filter((vfo) => vfo.receiver === receiver);
    const a = records.find((vfo) => vfo.slot.kind === 'slotted' && vfo.slot.id === 'A');
    const b = records.find((vfo) => vfo.slot.kind === 'slotted' && vfo.slot.id === 'B');
    if (records.length !== 2) return null;
    return a && b
      ? { receiver, left: a, right: b, absolute: true }
      : { receiver, left: records[0], right: records[1], absolute: false };
  });
  let receiverIndicators = $derived(
    (viewModel.receiverIndicators ?? []).filter(
      (indicator) => indicatorReceiver === undefined || indicator.receiver === indicatorReceiver,
    ),
  );

  function standardBadges(indicator: ReceiverIndicatorViewModel | undefined, vfo?: VfoViewModel) {
    const badges: { label: string; active: boolean; color: 'cyan' | 'orange' | 'muted' | 'red' | 'amber'; state: string }[] = [];
    const wide = viewModel.radioWideIndicators;
    const rfState = viewModel.radioWideIndicators?.rfState;
    if (vfo?.isActiveSlot) {
      const rxHz = displayValue(vfo.display?.frequencyHz, vfo.frequencyHz);
      badges.push({ label: `RX ${formatFrequency(rxHz)}`, active: rxHz !== null,
        color: rxHz === null ? 'muted' : 'cyan', state: vfo.display?.frequencyHz.state ?? (rxHz === null ? 'unknown' : 'current') });
      if (wide?.antenna.availability.structural) {
        const value = wide.antenna.reading.status === 'known' ? wide.antenna.reading.value : null;
        badges.push({ label: `ANT ${value ?? '—'}`, active: value !== null,
          color: value === null ? 'muted' : 'cyan', state: wide.antenna.reading.status });
      }
      if (wide?.atu.availability.structural) {
        const value = wide.atu.reading.status === 'known' ? wide.atu.reading.value.toUpperCase() : null;
        badges.push({ label: `TUNE ${value ?? '—'}`, active: value === 'ON' || value === 'TUNING',
          color: value === null ? 'muted' : value === 'OFF' ? 'cyan' : 'orange', state: wide.atu.reading.status });
      }
      if (wide && (wide.ritActive.availability.structural || wide.ritOffset.availability.structural)) {
        const active = wide.ritActive.reading.status === 'known' ? wide.ritActive.reading.value : null;
        const offset = wide.ritOffset.reading.status === 'known' ? wide.ritOffset.reading.value : null;
        badges.push({ label: `RIT ${active === null ? '—' : active ? 'ON' : 'OFF'} ${offset ?? '—'} Hz`,
          active: active === true, color: active === null || offset === null ? 'muted' : active ? 'orange' : 'cyan',
          state: active === null || offset === null ? 'unknown' : 'known' });
      }
      if (wide && (wide.xitActive.availability.structural || wide.xitOffset.availability.structural)) {
        const active = wide.xitActive.reading.status === 'known' ? wide.xitActive.reading.value : null;
        const offset = wide.xitOffset.reading.status === 'known' ? wide.xitOffset.reading.value : null;
        badges.push({ label: `XIT ${active === null ? '—' : active ? 'ON' : 'OFF'} ${offset ?? '—'} Hz`,
          active: active === true, color: active === null || offset === null ? 'muted' : active ? 'orange' : 'cyan',
          state: active === null || offset === null ? 'unknown' : 'known' });
      }
    }
    if (vfo?.isTxTarget && viewModel.txTarget.status === 'known') {
      const transmitting = rfState === 'transmitting';
      const uncertain = rfState === 'uncertain';
      badges.push({
        label: `${uncertain ? 'TX?' : 'TX'} ${formatFrequency(viewModel.txTarget.frequencyHz)}`,
        active: true,
        color: transmitting ? 'red' : uncertain ? 'amber' : 'orange',
        state: rfState ?? 'unknown',
      });
    }
    // A split TX target may be the inactive slot. It receives only RF truth;
    // receiver-local badges still belong exclusively to the active VFO.
    if (!indicator || (vfo !== undefined && !vfo.isActiveSlot)) return badges;
    const numeric = (name: string, field: ReceiverIndicatorViewModel['bandwidthHz'], unit = '') => {
      if (!field.availability.structural) return;
      const reading = field.reading;
      const value = reading.status === 'known' && Number.isFinite(reading.value) ? reading.value : null;
      badges.push({ label: `${name} ${value ?? '—'}${value !== null ? unit : ''}`, active: value !== null, color: value !== null ? 'cyan' : 'muted', state: reading.status });
    };
    const toggle = (name: string, field: ReceiverIndicatorViewModel['nbActive']) => {
      if (!field.availability.structural) return;
      const value = field.reading.status === 'known' ? field.reading.value : null;
      badges.push({ label: `${name} ${value === null ? '—' : value ? 'ON' : 'OFF'}`, active: value === true, color: value === null ? 'muted' : 'cyan', state: value === null ? 'unknown' : value ? 'on' : 'off' });
    };
    numeric('BW', indicator.bandwidthHz, ' Hz');
    if (indicator.agcMode.availability.structural) {
      const reading = indicator.agcMode.reading;
      const value = reading.status === 'known' ? reading.value : null;
      badges.push({ label: `AGC ${value ?? '—'}`, active: value !== null, color: value === null ? 'muted' : 'cyan', state: reading.status });
    }
    toggle('NB', indicator.nbActive); toggle('NR', indicator.nrActive);
    if (indicator.notchMode.availability.structural) {
      const reading = indicator.notchMode.reading;
      const value = reading.status === 'known' ? reading.value : null;
      badges.push({ label: `NOTCH ${value?.toUpperCase() ?? '—'}`, active: value !== null && value !== 'off', color: value === null ? 'muted' : 'orange', state: reading.status });
    }
    numeric('ATT', indicator.attenuator, ' dB'); numeric('P.AMP', indicator.preamp);
    toggle('IP+', indicator.ipPlus); toggle('DIGI-SEL', indicator.digiSel);
    if (indicator.rfGain.availability.structural && indicator.rfGain.display?.state !== 'unsupported') {
      const shown = displayValue(indicator.rfGain.display, indicator.rfGain.reading.status === 'known' ? indicator.rfGain.reading.value : null);
      const text = shown === null
        ? '—'
        : formatKnownLevel(shown, RF_FRONT_END_LEVELS[0][2], RF_FRONT_END_LEVELS[0][3]);
      badges.push({ label: `RFG ${text}`, active: shown !== null, color: shown === null ? 'muted' : 'cyan', state: indicator.rfGain.display?.state ?? indicator.rfGain.reading.status });
    }
    return badges;
  }

  function standardBand(vfo: VfoViewModel): string | null {
    const band = viewModel.band?.currentBand;
    return vfo.isActive && band?.availability.structural && band.availability.operational
      && band.reading.status === 'known' ? band.reading.value : null;
  }

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
      {#snippet hostedFrequency()}
        {#if vfo.receiver === 'MAIN'}
          {@render receiverInstruments!.mainFrequency({ compact: appearance === 'semantic', vfoFreqHook: false })}
        {:else if receiverInstruments!.subFrequency}
          {@render receiverInstruments!.subFrequency({ compact: appearance === 'semantic', vfoFreqHook: false })}
        {/if}
      {/snippet}
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
          class="vfo-freq" class:display-unknown={displayHz === null && pendingHz === null}
          {...(appearance === 'semantic' ? freq?.attributes ?? {} : {})}
          data-vfo-freq
          data-freq-tunable={!readoutDisabled(vfo)}
          data-display-state={vfo.display?.frequencyHz.state}
          aria-disabled={hasDigitReadout(vfo) && readoutDisabled(vfo) ? 'true' : undefined}
        >
          {#if receiverInstruments !== undefined && hasDigitReadout(vfo)}
            {@render hostedFrequency()}
          {:else if receiverInstruments === undefined && hasDigitReadout(vfo)}
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
              contextKey={`${frequencyLifetimeKey ?? 'unscoped'}:${viewModel.topologyId}:${vfo.receiver}:${slotKey(vfo.slot)}`}
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
  {#if !suppressIdentitySelectors && relativeIdentityUnknown && relativeReceiver !== null}
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
        {continuitySession}
      />
    {/if}
    <VfoOperationGroup
      {appearance}
      scheme={viewModel.vfoScheme}
      projection={vfoOperations}
      controls={operationControls}
      digest={hasVfoPair ? {
        rx: formatFrequency(rxFrequencyHz),
        tx: formatFrequency(txFrequencyHz),
        splitState: viewModel.split.status === 'known' ? String(viewModel.split.value) as 'true' | 'false' : 'mixed',
      } : undefined}
      onIntent={handleOperationIntent}
    />
  {/if}
  {/snippet}

  {#snippet standardOperationContent()}
    <VfoOperationGroup
      {appearance}
      scheme={viewModel.vfoScheme}
      projection={vfoOperations}
      controls={operationControls}
      onIntent={handleOperationIntent}
    />
  {/snippet}

  {#snippet standardPairSelectors(pair: { receiver: ReceiverId; left: VfoViewModel; right: VfoViewModel; absolute: boolean })}
    {#if pair.absolute}
    <div class="standard-vfo-selectors" aria-label="Select VFO">
      {#each [pair.left, pair.right] as vfo (slotKey(vfo.slot))}
        {@const slot = vfo.slot.kind === 'slotted' ? vfo.slot.id : '—'}
        <button type="button" class="vfo-select" data-standard-select-vfo={slot}
          data-active={vfo.isActiveSlot} disabled={disabled || vfo.isActiveSlot}
          onclick={() => selectVfo(vfo)}>SELECT {slot}</button>
      {/each}
    </div>
    {:else}
      {@render identitySelectors()}
    {/if}
  {/snippet}

  {#snippet receiverInstrument(receiver: ReceiverId)}
    {@const meterHandle = receiver === 'MAIN'
      ? receiverInstruments?.mainSMeter : receiverInstruments?.subSMeter}
    {#snippet hostedMeterFrame(frame: SignalMeterFrame)}
      <LinearSMeter {frame} compact
        variant={appearance === 'sdr' ? 'sdr-screen' : 'vfo-wide'} />
    {/snippet}
    {#snippet hostedMeter()}
      {#if meterHandle}{@render meterHandle(hostedMeterFrame)}{/if}
    {/snippet}
    <section class="receiver-instrument" data-receiver-instrument={receiver}
      class:instrument-active={viewModel.vfos.some((vfo) => vfo.receiver === receiver && vfo.isActive)}
      aria-label={`${receiver} instrument`}>
      <VfoIndicatorRow indicator={receiverIndicators.find((item) => item.receiver === receiver)} {appearance}
        slotLabel={instrumentSlot(receiver)} {continuitySession}
        sMeter={receiverInstruments === undefined ? undefined : hostedMeter}>
        <div class="freq-stack">
          {#each viewModel.vfos as vfo, i (vfo.receiver + ':' + i)}
            {#if vfo.receiver === receiver}{@render vfoTile(vfo, i)}{/if}
          {/each}
        </div>
      </VfoIndicatorRow>
    </section>
  {/snippet}

  {#snippet standardInstrument(receiver: ReceiverId, fixed: VfoViewModel | undefined)}
    {@const records = viewModel.vfos.filter((item) => item.receiver === receiver)}
    {@const activeSlot = records.find((item) => item.isActiveSlot)}
    {@const dominant = fixed ?? activeSlot ?? (records.length === 1 ? records[0] : undefined)}
    {@const indicator = receiverIndicators.find((item) => item.receiver === receiver)}
    {@const frequencyHandle = receiver === 'MAIN'
      ? receiverInstruments?.mainFrequency : receiverInstruments?.subFrequency}
    {@const meterHandle = receiver === 'MAIN'
      ? receiverInstruments?.mainSMeter : receiverInstruments?.subSMeter}
    {#snippet hostedFrequency()}
      {@render frequencyHandle!({ compact: false, vfoFreqHook: false })}
    {/snippet}
    {#snippet hostedMeterFrame(frame: SignalMeterFrame)}
      <LinearSMeter {frame} compact
        label={dominant ? (dominant.slot.kind === 'slotted' ? dominant.slot.id : roleLabel(dominant)) : '—'}
        variant="vfo" />
    {/snippet}
    {#snippet hostedMeter()}
      {#if meterHandle}{@render meterHandle(hostedMeterFrame)}{/if}
    {/snippet}
    {@const choices = fixed ? [] : viewModel.vfos.flatMap((choice, index) =>
        choice.receiver === receiver && choice !== dominant
          ? [{
              key: String(index), receiver: choice.receiver === 'SUB' ? 'sub' as const : 'main' as const,
              slot: slotKey(choice.slot), label: roleLabel(choice),
              frequencyText: formatFrequency(displayValue(choice.display?.frequencyHz, choice.frequencyHz)),
              active: choice.isActive, activeSlot: choice.isActiveSlot, txTarget: choice.isTxTarget,
              disabled: disabled || choice.slot.kind === 'unknown' || choice.slot.kind === 'relative',
              reason: choice.slot.kind === 'relative' ? identityOnlyReasonText() : selectReasonText(choice),
            }]
          : [])}
    <section class="receiver-instrument standard-receiver" data-receiver-instrument={receiver}
      data-standard-vfo-slot={fixed?.slot.kind === 'slotted' ? fixed.slot.id : undefined}
      data-testid="vfo-indicator-row" data-indicator-receiver={receiver}
      data-indicator-operational={indicator?.availability.operational}
      aria-label={`${receiver} receiver indicators`}>
      <div class="vfo-tile" class:is-active={dominant?.isActive}
        data-indicator-receiver={receiver} data-vfo-dominant={dominant ? 'known' : 'unknown'}
        data-vfo-tile={dominant ? '' : undefined} data-vfo-receiver={dominant ? receiver : undefined}
        data-vfo-slot={dominant ? slotKey(dominant.slot) : undefined}
        data-vfo-active={dominant?.isActive} data-vfo-active-slot={dominant?.isActiveSlot}
        data-vfo-tx-target={dominant?.isTxTarget}>
        <VfoPanel
          receiver={receiver === 'SUB' ? 'sub' : 'main'} receiverLabel={fixed ? roleLabel(fixed) : receiver}
          slotTag={dominant ? (dominant.slot.kind === 'slotted' ? dominant.slot.id : roleLabel(dominant)) : '—'}
          frequency={receiverInstruments !== undefined && dominant && frequencyHandle
            && (fixed === undefined || fixed.isActiveSlot)
            ? hostedFrequency : undefined}
          freq={receiverInstruments === undefined || (fixed !== undefined && !fixed.isActiveSlot)
            ? dominant?.frequencyHz ?? null : undefined}
          displayHz={(receiverInstruments === undefined || (fixed !== undefined && !fixed.isActiveSlot)) && dominant
            ? displayValue(dominant.display?.frequencyHz, dominant.frequencyHz) : undefined}
          pendingDisplayHz={receiverInstruments === undefined && dominant
            ? pendingFrequencyHz?.[receiver] ?? null : undefined}
          frequencyState={dominant?.display?.frequencyHz.state ?? (dominant?.frequencyHz == null ? 'unknown' : 'current')}
          contextKey={receiverInstruments === undefined
            ? `${frequencyLifetimeKey ?? 'unscoped'}:${viewModel.topologyId}:${receiver}:${dominant ? slotKey(dominant.slot) : 'unknown'}`
            : undefined}
          frequencyDisabled={!dominant || (fixed !== undefined && !fixed.isActiveSlot) || readoutDisabled(dominant)}
          controlsDisabled={fixed !== undefined && !fixed.isActiveSlot}
          mode={dominant ? displayValue(dominant.display?.mode, dominant.mode) : null}
          filter={dominant ? displayValue(dominant.display?.filter, dominant.filter) : null}
          sValue={indicator?.sMeter.availability.operational && indicator.sMeter.reading.status === 'known'
            && Number.isFinite(indicator.sMeter.reading.value) ? indicator.sMeter.reading.value : null}
          sMeter={receiverInstruments === undefined || (fixed && !fixed.isActiveSlot) ? undefined : hostedMeter}
          meterPresent={(fixed === undefined || fixed.isActiveSlot) && (indicator?.sMeter.availability.structural ?? false)}
          meterOperational={indicator?.sMeter.availability.operational ?? false}
          meterSource={indicator?.sMeter.source}
          {continuitySession}
          isActive={dominant?.isActive ?? false}
          badgeItems={standardBadges(indicator, dominant)}
          bandText={dominant ? standardBand(dominant) : null}
          slotChoices={choices}
          reserveMeterSpace={fixed !== undefined && !fixed.isActiveSlot}
          onFreqChange={receiverInstruments === undefined && dominant
            ? (hz) => tuneFrequency(dominant, hz) : undefined}
          onFrequencyClick={dominant && onOpenFrequencyEntry
            ? (trigger) => onOpenFrequencyEntry({ receiver: dominant.receiver, slot: dominant.slot }, trigger)
            : undefined}
          onSelectSlot={(key) => {
            const choice = viewModel.vfos[Number(key)];
            if (choice) selectVfo(choice);
          }}
          onSelectHeader={fixed?.slot.kind === 'slotted' && isSelectable(fixed) && !disabled
            ? () => selectVfo(fixed) : undefined}
          headerReason={fixed?.slot.kind === 'unknown'
            ? selectReasonText(fixed)
            : fixed?.slot.kind === 'relative' ? identityOnlyReasonText() : undefined}
        />
      </div>
    </section>
  {/snippet}

  {#if appearance !== 'semantic' && showVfoList}
    <div class="instrument-panel" data-testid="vfo-instrument-panel">
      {#if standardPair}
        {@render standardInstrument(standardPair.receiver, standardPair.left)}
        <div class="bridge standard-pair-bridge" data-instrument-bridge>
          {@render standardPairSelectors(standardPair)}
          {@render standardOperationContent()}
        </div>
        {@render standardInstrument(standardPair.receiver, standardPair.right)}
      {:else}
      {#if instrumentReceivers[0]}
        {#if appearance === 'standard'}{@render standardInstrument(instrumentReceivers[0], undefined)}
        {:else}{@render receiverInstrument(instrumentReceivers[0])}{/if}
      {/if}
      {#if showRadioWideFacts}
        <div class="bridge" data-instrument-bridge>
          {@render activeReceiverStatus()}
          {@render identitySelectors()}
          {#if appearance === 'standard'}{@render standardOperationContent()}
          {:else}{@render radioWideContent()}{/if}
        </div>
      {/if}
      {#each instrumentReceivers.slice(1) as receiver (receiver)}
        {#if appearance === 'standard'}{@render standardInstrument(receiver, undefined)}
        {:else}{@render receiverInstrument(receiver)}{/if}
      {/each}
      {/if}
    </div>
  {:else}
    {#if appearance !== 'semantic' && !showVfoList}{@render activeReceiverStatus()}{/if}
    {#if showVfoList}
      <div class="vfo-list" data-testid="vfo-list">
        {#each viewModel.vfos as vfo, i (vfo.receiver + ':' + i)}{@render vfoTile(vfo, i)}{/each}
      </div>
      {@render identitySelectors()}
      {#if receiverIndicators.length > 0}
        <div class="receiver-indicators" data-testid="vfo-receiver-indicators">
          {#each receiverIndicators as indicator (indicator.receiver)}
            {@const meterHandle = indicator.receiver === 'MAIN'
              ? receiverInstruments?.mainSMeter : receiverInstruments?.subSMeter}
            {#snippet hostedMeterFrame(frame: SignalMeterFrame)}
              <LinearSMeter {frame} compact variant="vfo-wide" />
            {/snippet}
            {#snippet hostedMeter()}
              {#if meterHandle}{@render meterHandle(hostedMeterFrame)}{/if}
            {/snippet}
            <VfoIndicatorRow {indicator} {continuitySession}
              sMeter={receiverInstruments === undefined ? undefined : hostedMeter} />
          {/each}
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
  [data-vfo-appearance='semantic'] .vfo-tile { position: relative; }
  .vfo-badge { padding: 1px 4px; border-radius: 3px; font-size: 10px; color: var(--v2-accent-red, #ff2020); border: 1px solid var(--v2-accent-red, #ff2020); }
  .vfo-select { border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12)); border-radius: 4px; background: transparent; color: inherit; cursor: pointer; padding: 3px 6px; }
  .vfo-select:disabled { color: var(--v2-text-disabled, rgba(255, 255, 255, 0.3)); cursor: not-allowed; }
  .vfo-identity-selectors { display: flex; gap: 6px; flex-wrap: wrap; }
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
  .receiver-instrument .vfo-role { grid-column: 1; grid-row: 1; font-size: 12px; letter-spacing: .1em; }
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
  /* A retained null primitive keeps the established instrument placeholder paint. */
  .receiver-instrument .vfo-freq.display-unknown :global(.freq) {
    font: inherit; letter-spacing: inherit; color: inherit; text-shadow: inherit;
  }
  .receiver-instrument .is-active .vfo-freq {
    color: var(--v2-vfo-main-freq-active, #7cfce5);
    text-shadow: 0 0 12px rgba(124,252,229,.5);
  }
  .receiver-instrument .secondary-slot .vfo-freq { font-size: 18px; margin: 2px 0; }
  .receiver-instrument .vfo-select { justify-self: end; grid-column: 2; grid-row: 1 / 3; }
  .bridge .vfo-identity-selectors { flex-direction: column; }
  [data-vfo-appearance='standard'] .receiver-instrument { padding: 8px; }
  [data-vfo-appearance='standard'] .instrument-active {
    border: 1px solid var(--v2-accent-cyan, #00d4ff); border-radius: 4px;
    box-shadow: 0 0 6px rgba(0,212,255,.3), inset 0 0 16px rgba(0,212,255,.06);
  }
  [data-vfo-appearance='standard'] .bridge { flex-basis: 136px; }
  [data-vfo-appearance='standard'] .standard-receiver[data-standard-vfo-slot] {
    flex: 1 1 0;
    padding: 6px;
    --btn-compact-min-height: 18px;
    --btn-compact-padding-block: 1px;
    --btn-compact-padding-inline: 4px;
    --btn-compact-font-size: 9px;
    --vfo-control-strip-gap: 2px;
    --vfo-panel-body-height: 100px;
    --vfo-control-strip-height: 54px;
  }
  [data-vfo-appearance='standard'] .standard-receiver[data-standard-vfo-slot] :global(.control-strip) {
    align-content: center;
    flex-wrap: wrap;
    overflow: visible;
    white-space: normal;
  }
  [data-vfo-appearance='standard'] .standard-pair-bridge {
    flex: 0 0 clamp(190px, 14vw, 220px);
    padding: 4px;
    gap: 3px;
    --vfo-ops-gap: 3px;
    --vfo-ops-badge-padding-x: 3px;
    --vfo-ops-badge-font-size: 9px;
  }
  [data-vfo-appearance='standard'] .standard-pair-bridge :global(.shared-indicators .facts) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 3px;
  }
  [data-vfo-appearance='standard'] .standard-pair-bridge :global(.shared-indicators .fact) {
    min-width: 0;
    text-align: center;
  }
  [data-vfo-appearance='standard'] .standard-pair-bridge :global(.shared-indicators .rf-lamp:empty) {
    display: none;
  }
  [data-vfo-appearance='standard'] .standard-pair-bridge :global(.vfo-ops) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  [data-vfo-appearance='standard'] .standard-pair-bridge :global(.split-digest) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 3px;
  }
  [data-vfo-appearance='standard'] .standard-pair-bridge :global(.split-digest > span) {
    min-width: 0;
    text-align: center;
  }
  .standard-vfo-selectors { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; }
  .standard-vfo-selectors .vfo-select {
    min-width: 0; padding: 4px 3px; font-size: 9px; font-weight: 700;
  }
  .standard-vfo-selectors .vfo-select[data-active='true'] {
    border-color: var(--v2-accent-cyan, #00d4ff); color: var(--v2-accent-cyan, #00d4ff);
  }
  .standard-tx-target {
    display: block; padding: 3px 5px; border: 1px solid var(--v2-accent-red, #ff2020);
    border-radius: 3px; color: var(--v2-accent-red, #ff2020); font-size: 9px;
    font-weight: 700; text-align: center;
  }
  [data-vfo-appearance='standard'] .receiver-instrument :where(.vfo-tile) {
    grid-template-columns: auto minmax(0, 1fr) auto;
    grid-template-rows: auto auto;
  }
  [data-vfo-appearance='standard'] .receiver-instrument > :where(.vfo-tile) {
    display: block;
    inline-size: 100%;
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
  @media (max-width: 950px) {
    [data-vfo-appearance='standard'] .standard-pair-bridge {
      padding: 2px;
      gap: 1px;
    }
    [data-vfo-appearance='standard'] .standard-receiver {
      --vfo-panel-body-height: 64px;
      --vfo-control-strip-height: 22px;
    }
    [data-vfo-appearance='standard'] .standard-receiver[data-standard-vfo-slot] {
      flex-basis: calc(100% - 192px);
    }
  }
  @media (max-width: 760px) {
    .instrument-panel { flex-direction: column; }
    .receiver-instrument, .bridge { flex-basis: auto; }
    .bridge { border-inline: 0; border-block: 1px solid var(--v2-border-panel, #18222d); }
  }
</style>
