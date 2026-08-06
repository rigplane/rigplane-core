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
</script>

<script lang="ts">
  import { t } from '$lib/i18n';
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

  function slotKey(slot: VfoSlot): string {
    return slot.kind === 'slotted' ? slot.id : slot.kind;
  }

  function roleLabel(vfo: VfoViewModel): string {
    const { slot } = vfo;
    if (slot.kind === 'slotted') return `${vfo.receiver} ${slot.id}`;
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

  function isSelectable(vfo: VfoViewModel): boolean {
    return hasVfoPair && !vfo.isActive;
  }

  function selectVfo(vfo: VfoViewModel): void {
    if (!isSelectable(vfo) || vfo.slot.kind === 'unknown' || disabled) return;
    onSelectVfo?.({ receiver: vfo.receiver, slot: vfo.slot });
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
    if (viewModel.split.status !== 'known') return;
    onQuickSplit?.();
  }

  function quickDualWatch(): void {
    if (viewModel.dualWatch.status !== 'known') return;
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
  {#if showRadioWideFacts}
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
      <div
        class="vfo-tile"
        class:is-active={vfo.isActive}
        data-vfo-tile
        data-vfo-receiver={vfo.receiver}
        data-vfo-slot={slotKey(vfo.slot)}
        data-vfo-active={vfo.isActive}
        data-vfo-tx-target={vfo.isTxTarget}
      >
        <span class="vfo-role">{roleLabel(vfo)}</span>
        <span class="vfo-freq" {...freq?.attributes ?? {}}>{freq?.text ?? formatFrequency(vfo.frequencyHz)}</span>
        <span class="vfo-mode">{vfo.mode ?? '—'}{vfo.filter ? ` / ${vfo.filter}` : ''}</span>
        {#if vfo.isTxTarget}
          <span class="vfo-badge" data-vfo-tx-badge>{t('core.vfo.txTarget.label')}</span>
        {/if}
        {#if selectable}
          <button
            type="button"
            class="vfo-select"
            data-vfo-select
            disabled={selectDisabled}
            aria-label={t('core.vfo.selectAction', { label: vfo.label })}
            onclick={() => selectVfo(vfo)}
          >
            {vfo.label}
          </button>
        {:else}
          <span class="vfo-label" data-vfo-label>{vfo.label}</span>
        {/if}
      </div>
    {/each}
  </div>
  {/if}

  {#if showRadioWideFacts}
    <div class="fact-toggles">
      <button
        type="button"
        class="fact-toggle"
        data-vfo-split
        role="switch"
        aria-checked={triState(viewModel.split)}
        aria-label={t('core.vfo.split.label')}
        disabled={viewModel.split.status === 'unknown'}
        onclick={toggleSplit}
      >
        {t('core.vfo.split.label')}: {stateWord(viewModel.split)}
      </button>
      <button
        type="button"
        class="fact-toggle"
        data-vfo-dual-watch
        role="switch"
        aria-checked={triState(viewModel.dualWatch)}
        aria-label={t('core.vfo.dualWatch.label')}
        disabled={viewModel.dualWatch.status === 'unknown'}
        onclick={toggleDualWatch}
      >
        {t('core.vfo.dualWatch.label')}: {stateWord(viewModel.dualWatch)}
      </button>
    </div>

    <!--
      MOR-1321. Actions, not facts — so no `role="switch"` and no `aria-checked`:
      each button DOES a thing once, it does not report a state. The two quick
      triggers carry the same `disabled` gate their fact toggles above carry.
      Button text is the accessible name; no `aria-label` duplicates it.
    -->
    {#if hasVfoPair}
      <div class="vfo-ops" data-testid="vfo-ops">
        <button type="button" class="vfo-op" data-vfo-equalize onclick={() => onEqualizeVfos?.()}>
          {t('core.vfo.ops.equalize')}
        </button>
        <button type="button" class="vfo-op" data-vfo-swap onclick={() => onSwapVfos?.()}>
          {t('core.vfo.ops.swap')}
        </button>
        <button
          type="button" class="vfo-op" data-vfo-quick-split
          disabled={viewModel.split.status === 'unknown'}
          onclick={quickSplit}
        >
          {t('core.vfo.ops.quickSplit')}
        </button>
        <button
          type="button" class="vfo-op" data-vfo-quick-dual-watch
          disabled={viewModel.dualWatch.status === 'unknown'}
          onclick={quickDualWatch}
        >
          {t('core.vfo.ops.quickDualWatch')}
        </button>
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
  .fact-toggles, .vfo-ops { display: flex; gap: 6px; flex-wrap: wrap; }
  .split-digest { display: flex; gap: 8px; margin: 0; font-size: 11px; color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55)); }
  .split-digest[data-split-active='false'] { opacity: 0.64; }
</style>
