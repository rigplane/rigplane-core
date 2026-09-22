<script module lang="ts">
  import type { Snippet } from 'svelte';
  import type {
    BooleanFact,
    RadioViewModel,
  } from './radio-view-model';
  import type {
    VfoOperationIntent,
    VfoOperationProjection,
    VfoRadioFunctionOperation,
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
  import { ControlButton } from '$lib/Button';
  import { t } from '$lib/i18n';
  import ActiveReceiverToggle from '../components-v2/vfo/ActiveReceiverToggle.svelte';
  import { vfoEqualLabel, vfoSwapLabel } from '../components-v2/vfo/vfo-ops-utils';
  import { disabledReasonText } from './disabled-reason';

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
  let hasLatching = $derived(
    projection.split.availability.structural || projection.dualWatch.availability.structural,
  );
  let functions = $derived(projection.radioFunctions);
  let hasFunctions = $derived([
    functions.tuner.availability,
    functions.vox.availability,
    functions.dialLock.availability,
  ].some((operation) => operation.structural));
  let hasStandardBridge = $derived(hasActions || hasLatching || hasFunctions);

  function triState(fact: BooleanFact): 'true' | 'false' | 'mixed' {
    return fact.status === 'known' ? String(fact.value) as 'true' | 'false' : 'mixed';
  }

  function stateWord(fact: BooleanFact): string {
    if (fact.status === 'unknown') return t('core.vfo.state.unknown');
    return t(fact.value ? 'core.vfo.state.on' : 'core.vfo.state.off');
  }

  function boolFact(operation: VfoRadioFunctionOperation): BooleanFact {
    const reading = operation.reading;
    return reading.status === 'known' && typeof reading.value === 'boolean'
      ? { status: 'known', value: reading.value } : { status: 'unknown' };
  }

  function atuValue(operation: VfoRadioFunctionOperation): 'on' | 'off' | 'tuning' | null {
    const reading = operation.reading;
    return reading.status === 'known'
      && (reading.value === 'on' || reading.value === 'off' || reading.value === 'tuning')
      ? reading.value : null;
  }

  /** The ATU word 'tuning' is the enum value the panel badges already print
   *  (VfoSurface's TUNE badge); on/off reuse the catalog state words. */
  function tunerStateWord(operation: VfoRadioFunctionOperation): string {
    const value = atuValue(operation);
    if (value === null) return t('core.vfo.state.unknown');
    return value === 'tuning' ? value : t(value === 'on' ? 'core.vfo.state.on' : 'core.vfo.state.off');
  }

  /** Present but unoperational is unobserved — the same wording family the
   *  TX aux toggles draw from (`TxAuxFiniteHost`'s `reasonTextOf`). */
  function functionReason(operation: VfoRadioFunctionOperation): string | undefined {
    if (operation.availability.operational || !operation.availability.structural) return undefined;
    return disabledReasonText({ structural: true, operational: false });
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
  {:else if appearance === 'standard'}
    <!--
      MOR-2509 package C — the bridge between the receiver panels is the
      radio's front-panel HARDWARE, rendered through the shared Button
      family: dot-lamp latching keys, reserved-slot momentary keys, and the
      flat fill selector keys. The semantic/sdr branch below keeps the raw
      controls those appearances always rendered.
    -->
    {#if hasStandardBridge}
      <div
        class="vfo-ops"
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

        {#if projection.swap.availability.structural || projection.equalize.availability.structural
          || projection.quickSplit.availability.structural
          || projection.quickDualWatch.availability.structural
          || projection.speak.availability.structural}
          <div class="ops-row">
            {#if projection.swap.availability.structural}
              {@const id = reasonId('swap', projection.swap.availability.reason)}
              <ControlButton
                surface="hardware"
                reserveIndicator
                ariaLabel={vfoSwapLabel(scheme)}
                describedBy={id}
                title={projection.swap.availability.reason}
                disabled={!projection.swap.availability.operational}
                data={{ 'vfo-swap': true, 'dual-action': 'swap', op: 'swap', color: 'muted' }}
                onclick={() => emit({ kind: 'swap' })}
              >{vfoSwapLabel(scheme)}</ControlButton>
              {#if id}<span {id} class="sr-only">{projection.swap.availability.reason}</span>{/if}
            {/if}

            {#if projection.equalize.availability.structural}
              {@const id = reasonId('equalize', projection.equalize.availability.reason)}
              <ControlButton
                surface="hardware"
                reserveIndicator
                ariaLabel={vfoEqualLabel(scheme)}
                describedBy={id}
                title={projection.equalize.availability.reason}
                disabled={!projection.equalize.availability.operational}
                data={{ 'vfo-equalize': true, 'dual-action': 'equalize', op: 'copy', color: 'muted' }}
                onclick={() => emit({ kind: 'equalize' })}
              >{vfoEqualLabel(scheme)}</ControlButton>
              {#if id}<span {id} class="sr-only">{projection.equalize.availability.reason}</span>{/if}
            {/if}

            {#if projection.quickSplit.availability.structural}
              {@const id = reasonId('quick-split', projection.quickSplit.availability.reason)}
              <ControlButton
                surface="hardware"
                reserveIndicator
                ariaLabel={t('core.vfo.ops.quickSplit')}
                describedBy={id}
                title={projection.quickSplit.availability.reason}
                disabled={!projection.quickSplit.availability.operational}
                data={{ 'vfo-quick-split': true, 'dual-action': 'quick-split', 'ops-wide': true, color: 'muted' }}
                onclick={() => emit({ kind: 'quick-split' })}
              >{t('core.vfo.ops.quickSplit')}</ControlButton>
              {#if id}<span {id} class="sr-only">{projection.quickSplit.availability.reason}</span>{/if}
            {/if}

            {#if projection.quickDualWatch.availability.structural}
              {@const id = reasonId('quick-dual-watch', projection.quickDualWatch.availability.reason)}
              <ControlButton
                surface="hardware"
                reserveIndicator
                ariaLabel={t('core.vfo.ops.quickDualWatch')}
                describedBy={id}
                title={projection.quickDualWatch.availability.reason}
                disabled={!projection.quickDualWatch.availability.operational}
                data={{ 'vfo-quick-dual-watch': true, 'dual-action': 'quick-dual-watch', 'ops-wide': true, color: 'muted' }}
                onclick={() => emit({ kind: 'quick-dual-watch' })}
              >{t('core.vfo.ops.quickDualWatch')}</ControlButton>
              {#if id}<span {id} class="sr-only">{projection.quickDualWatch.availability.reason}</span>{/if}
            {/if}

            {#if projection.speak.availability.structural}
              {@const id = reasonId('speak', projection.speak.availability.reason)}
              <ControlButton
                surface="hardware"
                reserveIndicator
                describedBy={id}
                title={projection.speak.availability.reason ?? 'Speak current frequency aloud'}
                disabled={!projection.speak.availability.operational}
                data={{ 'dual-action': 'speak' }}
                onclick={() => emit({ kind: 'speak' })}
              >SPEAK</ControlButton>
              {#if id}<span {id} class="sr-only">{projection.speak.availability.reason}</span>{/if}
            {/if}
          </div>
        {/if}

        {#if hasLatching}
          <div class="ops-row">
            {#if projection.split.availability.structural}
              {@const splitReasonId = reasonId('split', projection.split.availability.reason)}
              <ControlButton
                surface="hardware"
                indicatorStyle="dot"
                indicatorColor="cyan"
                role="switch"
                ariaChecked={triState(projection.split.reading)}
                active={projection.split.reading.status === 'known' && projection.split.reading.value}
                ariaLabel={`${t('core.vfo.split.label')}: ${stateWord(projection.split.reading)}`}
                describedBy={splitReasonId}
                title={projection.split.availability.reason}
                disabled={!projection.split.availability.operational}
                data={{ 'vfo-split': true, op: 'split', color: 'cyan' }}
                onclick={() => emit({ kind: 'toggle-split' })}
              >SPLIT</ControlButton>
              {#if splitReasonId}<span id={splitReasonId} class="sr-only">{projection.split.availability.reason}</span>{/if}
            {/if}

            {#if projection.dualWatch.availability.structural}
              {@const dualWatchReasonId = reasonId('dual-watch', projection.dualWatch.availability.reason)}
              <ControlButton
                surface="hardware"
                indicatorStyle="dot"
                indicatorColor="green"
                role="switch"
                ariaChecked={triState(projection.dualWatch.reading)}
                active={projection.dualWatch.reading.status === 'known' && projection.dualWatch.reading.value}
                ariaLabel={`${t('core.vfo.dualWatch.label')}: ${stateWord(projection.dualWatch.reading)}`}
                describedBy={dualWatchReasonId}
                title={projection.dualWatch.availability.reason}
                disabled={!projection.dualWatch.availability.operational}
                data={{ 'vfo-dual-watch': true, op: 'dw', color: 'green' }}
                onclick={() => emit({ kind: 'toggle-dual-watch' })}
              >DW</ControlButton>
              {#if dualWatchReasonId}<span id={dualWatchReasonId} class="sr-only">{projection.dualWatch.availability.reason}</span>{/if}
            {/if}
          </div>
        {/if}

        {#if hasFunctions}
          <div class="bridge-divider" aria-hidden="true"></div>
          <div class="ops-row">
            {#if functions.tuner.availability.structural}
              {@const id = reasonId('tuner', functionReason(functions.tuner))}
              {@const lamp = atuValue(functions.tuner)}
              <ControlButton
                surface="hardware"
                indicatorStyle="dot"
                indicatorColor={lamp === 'tuning' ? 'orange' : 'red'}
                role="switch"
                ariaChecked={lamp === null || lamp === 'tuning' ? 'mixed' : lamp === 'on' ? 'true' : 'false'}
                active={lamp === 'on' || lamp === 'tuning'}
                ariaLabel={`Tuner: ${tunerStateWord(functions.tuner)}`}
                describedBy={id}
                title={functionReason(functions.tuner)}
                disabled={!functions.tuner.availability.operational}
                data={{ 'vfo-tuner': true }}
                onclick={() => emit({ kind: 'toggle-tuner' })}
              >TUNER</ControlButton>
              {#if id}<span {id} class="sr-only">{functionReason(functions.tuner)}</span>{/if}
            {/if}

            {#if functions.vox.availability.structural}
              {@const id = reasonId('vox', functionReason(functions.vox))}
              {@const vox = boolFact(functions.vox)}
              <ControlButton
                surface="hardware"
                indicatorStyle="dot"
                indicatorColor="amber"
                role="switch"
                ariaChecked={triState(vox)}
                active={vox.status === 'known' && vox.value}
                ariaLabel={`VOX: ${stateWord(vox)}`}
                describedBy={id}
                title={functionReason(functions.vox)}
                disabled={!functions.vox.availability.operational}
                data={{ 'vfo-vox': true }}
                onclick={() => emit({ kind: 'toggle-vox' })}
              >VOX</ControlButton>
              {#if id}<span {id} class="sr-only">{functionReason(functions.vox)}</span>{/if}
            {/if}

            {#if functions.dialLock.availability.structural}
              {@const id = reasonId('dial-lock', functionReason(functions.dialLock))}
              {@const lock = boolFact(functions.dialLock)}
              <ControlButton
                surface="hardware"
                indicatorStyle="dot"
                indicatorColor="cyan"
                role="switch"
                ariaChecked={triState(lock)}
                active={lock.status === 'known' && lock.value}
                ariaLabel={`Lock: ${stateWord(lock)}`}
                describedBy={id}
                title={functionReason(functions.dialLock)}
                disabled={!functions.dialLock.availability.operational}
                data={{ 'vfo-lock': true }}
                onclick={() => emit({ kind: 'toggle-dial-lock' })}
              >LOCK</ControlButton>
              {#if id}<span {id} class="sr-only">{functionReason(functions.dialLock)}</span>{/if}
            {/if}
          </div>
        {/if}
      </div>
    {/if}
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
  .vfo-ops[data-vfo-operation-appearance='sdr'] {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--vfo-ops-gap, 4px);
  }
  .fact-toggles[data-vfo-operation-appearance='sdr'] .fact-toggle,
  .vfo-ops[data-vfo-operation-appearance='sdr'] .vfo-op {
    width: 100%; min-width: 0; min-height: max(24px, var(--vfo-ops-badge-height, 18px));
    padding: 4px var(--vfo-ops-badge-padding-x, 6px);
    border-radius: var(--vfo-ops-badge-radius, 4px);
    font-size: var(--vfo-ops-badge-font-size, 10px); box-sizing: border-box;
  }
  .split-digest[data-vfo-operation-appearance='sdr'] {
    flex-wrap: wrap; justify-content: center; font-size: 9px;
  }
  /* MOR-2509 bridge: one fixed column of group rows; every key renders
   * through the shared Button family, so height and text size read the
   * family tokens scoped here (28px floor, 12px labels). The dot sits
   * tighter than the family default because a bridge key is narrower than
   * a panel key. */
  .vfo-ops[data-vfo-operation-appearance='standard'] {
    display: flex; flex-direction: column; flex-wrap: nowrap;
    gap: var(--vfo-ops-gap, 4px);
    --btn-min-height: 28px;
    --btn-font-size: 12px;
    --indicator-dot-offset: 4px;
    --indicator-dot-gap: 4px;
  }
  .ops-row {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--vfo-ops-gap, 4px);
  }
  .ops-row > :global(*) { min-width: 0; width: 100%; }
  .ops-row > :global([data-ops-wide]) { grid-column: 1 / -1; }
  .bridge-divider {
    height: 1px; margin-block: calc(var(--vfo-ops-gap, 4px) / 2);
    background: var(--v2-border-panel, rgba(255, 255, 255, 0.12));
  }
</style>
