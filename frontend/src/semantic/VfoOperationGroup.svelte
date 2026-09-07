<script module lang="ts">
  import type { Snippet } from 'svelte';
  import type {
    BooleanFact,
    RadioViewModel,
  } from './radio-view-model';
  import type {
    VfoOperationIntent,
    VfoOperationProjection,
  } from './vfo-operation-projection';

  interface Props {
    appearance: 'semantic' | 'sdr' | 'standard';
    scheme: RadioViewModel['vfoScheme'];
    projection: VfoOperationProjection;
    digest?: { rx: string; tx: string; splitState: 'true' | 'false' | 'mixed' };
    onIntent: (intent: VfoOperationIntent) => void;
    controls?: Snippet;
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
    projection,
    digest,
    onIntent,
    controls,
  }: Props = $props();

  const reasonIdPrefix = `vfo-operation-reason-${++sequence}`;
  let active = $derived(projection.activeReceiver.reading.status === 'known'
    ? projection.activeReceiver.reading.receiver : null);
  let hasReceiverSelector = $derived(projection.activeReceiver.availability.structural);
  let hasActions = $derived([
    projection.activeReceiver.availability,
    projection.equalize.availability,
    projection.swap.availability,
    projection.quickSplit.availability,
    projection.quickDualWatch.availability,
    projection.speak.availability,
  ].some((operation) => operation.structural));
  let receiverAvailability = $derived({
    MAIN: projection.activeReceiver.options[0].availability,
    SUB: projection.activeReceiver.options[1].availability,
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

  function emit(intent: VfoOperationIntent): void {
    onIntent(intent);
  }
</script>

  {#if controls}
    {@render controls()}
  {:else}
  <div class="fact-toggles" data-vfo-operation-appearance={appearance}>
    {#if projection.split.availability.structural}
      {@const splitReasonId = reasonId('split', projection.split.availability.reason)}
      <button
        type="button"
        class="fact-toggle"
        class:v2-control-button={appearance !== 'semantic'}
        data-vfo-split
        data-op="split"
        data-active={triState(projection.split.reading)}
        data-color="cyan"
        role="switch"
        aria-checked={triState(projection.split.reading)}
        aria-label={`${t('core.vfo.split.label')}: ${stateWord(projection.split.reading)}`}
        aria-describedby={splitReasonId}
        title={projection.split.availability.reason}
        disabled={!projection.split.availability.operational}
        onclick={() => emit({ kind: 'toggle-split' })}
      >SPLIT</button>
      {#if splitReasonId}<span id={splitReasonId} class="sr-only">{projection.split.availability.reason}</span>{/if}
    {/if}

    {#if projection.dualWatch.availability.structural}
      {@const dualWatchReasonId = reasonId('dual-watch', projection.dualWatch.availability.reason)}
      <button
        type="button"
        class="fact-toggle"
        class:v2-control-button={appearance !== 'semantic'}
        data-vfo-dual-watch
        data-op="dw"
        data-active={triState(projection.dualWatch.reading)}
        data-color="green"
        role="switch"
        aria-checked={triState(projection.dualWatch.reading)}
        aria-label={`${t('core.vfo.dualWatch.label')}: ${stateWord(projection.dualWatch.reading)}`}
        aria-describedby={dualWatchReasonId}
        title={projection.dualWatch.availability.reason}
        disabled={!projection.dualWatch.availability.operational}
        onclick={() => emit({ kind: 'toggle-dual-watch' })}
      >DW</button>
      {#if dualWatchReasonId}<span id={dualWatchReasonId} class="sr-only">{projection.dualWatch.availability.reason}</span>{/if}
    {/if}
  </div>

  {#if hasActions}
    <div
      class="vfo-ops"
      class:dual={hasReceiverSelector}
      data-vfo-operation-appearance={appearance}
      data-testid="vfo-ops"
      data-dual-action-block
      data-disabled-reason={projection.groupReason ? 'vfo-identity-unknown' : undefined}
      title={projection.groupReason}
    >
      {#if hasReceiverSelector}
        <ActiveReceiverToggle
          {active}
          availability={receiverAvailability}
          segmentLabels={{ MAIN: 'MAIN', SUB: 'SUB' }}
          allowReselect
          embedded
          onChange={(receiver) => emit({ kind: 'select-receiver', receiver })}
        />
      {/if}

      {#if projection.equalize.availability.structural}
        {@const id = reasonId('equalize', projection.equalize.availability.reason)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-op="copy" data-color="muted" data-active="false"
          data-vfo-equalize data-dual-action="equalize" aria-label={vfoEqualLabel(scheme)}
          aria-describedby={id} title={projection.equalize.availability.reason} disabled={!projection.equalize.availability.operational}
          onclick={() => emit({ kind: 'equalize' })}>{vfoEqualLabel(scheme)}</button>
        {#if id}<span {id} class="sr-only">{projection.equalize.availability.reason}</span>{/if}
      {/if}

      {#if projection.swap.availability.structural}
        {@const id = reasonId('swap', projection.swap.availability.reason)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-op="swap" data-color="muted" data-active="false"
          data-vfo-swap data-dual-action="swap" aria-label={vfoSwapLabel(scheme)}
          aria-describedby={id} title={projection.swap.availability.reason} disabled={!projection.swap.availability.operational}
          onclick={() => emit({ kind: 'swap' })}>{vfoSwapLabel(scheme)}</button>
        {#if id}<span {id} class="sr-only">{projection.swap.availability.reason}</span>{/if}
      {/if}

      {#if projection.quickSplit.availability.structural}
        {@const id = reasonId('quick-split', projection.quickSplit.availability.reason)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-vfo-quick-split data-dual-action="quick-split" aria-label={t('core.vfo.ops.quickSplit')}
          aria-describedby={id} title={projection.quickSplit.availability.reason} disabled={!projection.quickSplit.availability.operational}
          onclick={() => emit({ kind: 'quick-split' })}>{t('core.vfo.ops.quickSplit')}</button>
        {#if id}<span {id} class="sr-only">{projection.quickSplit.availability.reason}</span>{/if}
      {/if}

      {#if projection.quickDualWatch.availability.structural}
        {@const id = reasonId('quick-dual-watch', projection.quickDualWatch.availability.reason)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-vfo-quick-dual-watch data-dual-action="quick-dual-watch" aria-label={t('core.vfo.ops.quickDualWatch')}
          aria-describedby={id} title={projection.quickDualWatch.availability.reason} disabled={!projection.quickDualWatch.availability.operational}
          onclick={() => emit({ kind: 'quick-dual-watch' })}>{t('core.vfo.ops.quickDualWatch')}</button>
        {#if id}<span {id} class="sr-only">{projection.quickDualWatch.availability.reason}</span>{/if}
      {/if}

      {#if projection.speak.availability.structural}
        {@const id = reasonId('speak', projection.speak.availability.reason)}
        <button type="button" class="vfo-op" class:v2-control-button={appearance !== 'semantic'}
          data-dual-action="speak" aria-describedby={id} title={projection.speak.availability.reason ?? 'Speak current frequency aloud'}
          disabled={!projection.speak.availability.operational} onclick={() => emit({ kind: 'speak' })}>SPEAK</button>
        {#if id}<span {id} class="sr-only">{projection.speak.availability.reason}</span>{/if}
      {/if}
    </div>
  {/if}
  {/if}

  {#if digest}
    <p
      class="split-digest"
      data-vfo-operation-appearance={appearance}
      data-testid="vfo-split-digest"
      data-split-active={digest.splitState}
    >
      <span data-split-rx>{t('core.vfo.splitDigest.rx', { frequency: digest.rx })}</span>
      <span data-split-tx>{t('core.vfo.splitDigest.tx', { frequency: digest.tx })}</span>
    </p>
  {/if}

<style>
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
  .fact-toggles[data-vfo-operation-appearance='sdr'],
  .vfo-ops[data-vfo-operation-appearance='sdr'],
  .fact-toggles[data-vfo-operation-appearance='standard'],
  .vfo-ops[data-vfo-operation-appearance='standard'] {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--vfo-ops-gap, 4px);
  }
  .fact-toggles[data-vfo-operation-appearance='sdr'] .fact-toggle,
  .vfo-ops[data-vfo-operation-appearance='sdr'] .vfo-op,
  .fact-toggles[data-vfo-operation-appearance='standard'] .fact-toggle,
  .vfo-ops[data-vfo-operation-appearance='standard'] .vfo-op {
    width: 100%; min-width: 0; min-height: var(--vfo-ops-badge-height, 18px);
    padding: 4px var(--vfo-ops-badge-padding-x, 6px);
    border-radius: var(--vfo-ops-badge-radius, 4px);
    font-size: var(--vfo-ops-badge-font-size, 10px); box-sizing: border-box;
  }
  .split-digest[data-vfo-operation-appearance='sdr'],
  .split-digest[data-vfo-operation-appearance='standard'] {
    flex-wrap: wrap; justify-content: center; font-size: 9px;
  }
</style>
