<script module lang="ts">
  import type {
    ActiveRx,
    BooleanFact,
    DualActionBlockViewModel,
    RadioViewModel,
  } from './radio-view-model';

  export type VfoOperationIntent =
    | { kind: 'select-receiver'; receiver: 'MAIN' | 'SUB' }
    | { kind: 'toggle-split' | 'toggle-dual-watch' }
    | { kind: 'equalize' | 'swap' | 'quick-split' | 'quick-dual-watch' | 'speak' };

  export interface OperationAvailability {
    structural: boolean;
    operational: boolean;
    reason?: string;
  }

  export interface OperationReasons {
    main?: string;
    sub?: string;
    equalize?: string;
    swap?: string;
    quickSplit?: string;
    quickDualWatch?: string;
    speak?: string;
  }

  interface Props {
    appearance: 'semantic' | 'sdr' | 'standard';
    scheme: RadioViewModel['vfoScheme'];
    activeReceiver: ActiveRx;
    split: BooleanFact;
    dualWatch: BooleanFact;
    splitAvailability: OperationAvailability;
    dualWatchAvailability: OperationAvailability;
    actions: DualActionBlockViewModel;
    actionReasons?: OperationReasons;
    digest?: { rx: string; tx: string; splitState: 'true' | 'false' | 'mixed' };
    groupReason?: string;
    onIntent: (intent: VfoOperationIntent) => void;
  }

  let sequence = 0;
</script>

<script lang="ts">
  import '../components-v2/controls/control-button.css';
  import { t } from '$lib/i18n';
  import ActiveReceiverToggle from '../components-v2/vfo/ActiveReceiverToggle.svelte';
  import { vfoEqualLabel, vfoSwapLabel } from '../components-v2/vfo/vfo-ops-utils';

  let {
    appearance,
    scheme,
    activeReceiver,
    split,
    dualWatch,
    splitAvailability,
    dualWatchAvailability,
    actions,
    actionReasons = {},
    digest,
    groupReason,
    onIntent,
  }: Props = $props();

  const reasonIdPrefix = `vfo-operation-reason-${++sequence}`;
  let active = $derived(activeReceiver.status === 'known' ? activeReceiver.receiver : null);
  let hasReceiverSelector = $derived(actions.main.structural || actions.sub.structural);
  let hasActions = $derived(Object.values(actions).some((action) => action.structural));
  let receiverAvailability = $derived({
    MAIN: { ...actions.main, reason: actionReasons.main },
    SUB: { ...actions.sub, reason: actionReasons.sub },
  });

  function triState(fact: BooleanFact): 'true' | 'false' | 'mixed' {
    return fact.status === 'known' ? String(fact.value) as 'true' | 'false' : 'mixed';
  }

  function stateWord(fact: BooleanFact): string {
    if (fact.status === 'unknown') return t('core.vfo.state.unknown');
    return t(fact.value ? 'core.vfo.state.on' : 'core.vfo.state.off');
  }

  function reasonId(name: string, reason: string | undefined): string | undefined {
    return reason ? `${reasonIdPrefix}-${name}` : undefined;
  }

  function emit(intent: VfoOperationIntent, availability: OperationAvailability): void {
    if (!availability.structural || !availability.operational) return;
    onIntent(intent);
  }
</script>

<div
  class="vfo-operation-group"
  data-vfo-operation-appearance={appearance}
  data-disabled-reason={groupReason ? 'vfo-identity-unknown' : undefined}
>
  <div class="fact-toggles">
    {#if splitAvailability.structural}
      {@const splitReasonId = reasonId('split', splitAvailability.reason)}
      <button
        type="button"
        class="fact-toggle"
        class:v2-control-button={appearance !== 'semantic'}
        data-vfo-split
        data-op="split"
        data-active={triState(split)}
        data-color="cyan"
        role="switch"
        aria-checked={triState(split)}
        aria-label={t('core.vfo.split.label')}
        aria-describedby={splitReasonId}
        title={splitAvailability.reason}
        disabled={!splitAvailability.operational}
        onclick={() => emit({ kind: 'toggle-split' }, splitAvailability)}
      >{t('core.vfo.split.label')}: {stateWord(split)}</button>
      {#if splitReasonId}<span id={splitReasonId} class="sr-only">{splitAvailability.reason}</span>{/if}
    {/if}

    {#if dualWatchAvailability.structural}
      {@const dualWatchReasonId = reasonId('dual-watch', dualWatchAvailability.reason)}
      <button
        type="button"
        class="fact-toggle"
        class:v2-control-button={appearance !== 'semantic'}
        data-vfo-dual-watch
        data-op="dw"
        data-active={triState(dualWatch)}
        data-color="green"
        role="switch"
        aria-checked={triState(dualWatch)}
        aria-label={t('core.vfo.dualWatch.label')}
        aria-describedby={dualWatchReasonId}
        title={dualWatchAvailability.reason}
        disabled={!dualWatchAvailability.operational}
        onclick={() => emit({ kind: 'toggle-dual-watch' }, dualWatchAvailability)}
      >{t('core.vfo.dualWatch.label')}: {stateWord(dualWatch)}</button>
      {#if dualWatchReasonId}<span id={dualWatchReasonId} class="sr-only">{dualWatchAvailability.reason}</span>{/if}
    {/if}
  </div>

  {#if hasActions}
    <div
      class="vfo-ops"
      class:dual={hasReceiverSelector}
      data-testid="vfo-ops"
      data-dual-action-block
      data-disabled-reason={groupReason ? 'vfo-identity-unknown' : undefined}
      title={groupReason}
    >
      {#if hasReceiverSelector}
        <div class="active-receiver-slot">
          <ActiveReceiverToggle
            {active}
            availability={receiverAvailability}
            segmentLabels={{ MAIN: 'MAIN', SUB: 'SUB' }}
            allowReselect
            onChange={(receiver) => emit(
              { kind: 'select-receiver', receiver },
              receiver === 'MAIN' ? actions.main : actions.sub,
            )}
          />
        </div>
      {/if}

      {#if actions.equalize.structural}
        {@const id = reasonId('equalize', actionReasons.equalize)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-op="copy" data-color="muted" data-active="false"
          data-vfo-equalize data-dual-action="equalize" aria-label={vfoEqualLabel(scheme)}
          aria-describedby={id} title={actionReasons.equalize} disabled={!actions.equalize.operational}
          onclick={() => emit({ kind: 'equalize' }, actions.equalize)}>{vfoEqualLabel(scheme)}</button>
        {#if id}<span {id} class="sr-only">{actionReasons.equalize}</span>{/if}
      {/if}

      {#if actions.swap.structural}
        {@const id = reasonId('swap', actionReasons.swap)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-op="swap" data-color="muted" data-active="false"
          data-vfo-swap data-dual-action="swap" aria-label={vfoSwapLabel(scheme)}
          aria-describedby={id} title={actionReasons.swap} disabled={!actions.swap.operational}
          onclick={() => emit({ kind: 'swap' }, actions.swap)}>{vfoSwapLabel(scheme)}</button>
        {#if id}<span {id} class="sr-only">{actionReasons.swap}</span>{/if}
      {/if}

      {#if actions.quickSplit.structural}
        {@const id = reasonId('quick-split', actionReasons.quickSplit)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-vfo-quick-split data-dual-action="quick-split" aria-label={t('core.vfo.ops.quickSplit')}
          aria-describedby={id} title={actionReasons.quickSplit} disabled={!actions.quickSplit.operational}
          onclick={() => emit({ kind: 'quick-split' }, actions.quickSplit)}>{t('core.vfo.ops.quickSplit')}</button>
        {#if id}<span {id} class="sr-only">{actionReasons.quickSplit}</span>{/if}
      {/if}

      {#if actions.quickDualWatch.structural}
        {@const id = reasonId('quick-dual-watch', actionReasons.quickDualWatch)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-vfo-quick-dual-watch data-dual-action="quick-dual-watch" aria-label={t('core.vfo.ops.quickDualWatch')}
          aria-describedby={id} title={actionReasons.quickDualWatch} disabled={!actions.quickDualWatch.operational}
          onclick={() => emit({ kind: 'quick-dual-watch' }, actions.quickDualWatch)}>{t('core.vfo.ops.quickDualWatch')}</button>
        {#if id}<span {id} class="sr-only">{actionReasons.quickDualWatch}</span>{/if}
      {/if}

      {#if actions.speak.structural}
        {@const id = reasonId('speak', actionReasons.speak)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-dual-action="speak" aria-describedby={id} title={actionReasons.speak ?? 'Speak current frequency aloud'}
          disabled={!actions.speak.operational} onclick={() => emit({ kind: 'speak' }, actions.speak)}>SPEAK</button>
        {#if id}<span {id} class="sr-only">{actionReasons.speak}</span>{/if}
      {/if}
    </div>
  {/if}

  {#if digest}
    <p class="split-digest" data-testid="vfo-split-digest" data-split-active={digest.splitState}>
      <span data-split-rx>{t('core.vfo.splitDigest.rx', { frequency: digest.rx })}</span>
      <span data-split-tx>{t('core.vfo.splitDigest.tx', { frequency: digest.tx })}</span>
    </p>
  {/if}
</div>

<style>
  .vfo-operation-group { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .fact-toggles, .vfo-ops { display: flex; gap: 6px; flex-wrap: wrap; }
  .fact-toggle, .vfo-op {
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    border-radius: 4px; background: transparent; color: inherit; cursor: pointer; padding: 3px 6px;
  }
  .fact-toggle:disabled, .vfo-op:disabled {
    color: var(--v2-text-disabled, rgba(255, 255, 255, 0.3)); cursor: not-allowed;
  }
  .split-digest {
    display: flex; gap: 8px; margin: 0; font-size: 11px;
    color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55));
  }
  .split-digest[data-split-active='false'] { opacity: 0.64; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
  [data-vfo-operation-appearance='sdr'] .fact-toggles,
  [data-vfo-operation-appearance='sdr'] .vfo-ops,
  [data-vfo-operation-appearance='standard'] .fact-toggles,
  [data-vfo-operation-appearance='standard'] .vfo-ops {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--vfo-ops-gap, 4px);
  }
  [data-vfo-operation-appearance='sdr'] .fact-toggle,
  [data-vfo-operation-appearance='sdr'] .vfo-op,
  [data-vfo-operation-appearance='standard'] .fact-toggle,
  [data-vfo-operation-appearance='standard'] .vfo-op {
    width: 100%; min-width: 0; min-height: var(--vfo-ops-badge-height, 18px);
    padding: 4px var(--vfo-ops-badge-padding-x, 6px);
    border-radius: var(--vfo-ops-badge-radius, 4px);
    font-size: var(--vfo-ops-badge-font-size, 10px); box-sizing: border-box;
  }
  .active-receiver-slot { grid-column: 1 / -1; }
  [data-vfo-operation-appearance='sdr'] .split-digest,
  [data-vfo-operation-appearance='standard'] .split-digest {
    flex-wrap: wrap; justify-content: center; font-size: 9px;
  }
</style>
