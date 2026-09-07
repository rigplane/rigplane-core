<script module lang="ts">
  import type { Snippet } from 'svelte';
  import type {
    FiniteControlAppearance, FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { RadioViewModel } from './radio-view-model';
  import type {
    VfoOperationProjectionInput, VfoOperationReceiver,
  } from './vfo-operation-projection';

  export type VfoOperationHandle = Snippet<[]>;

  export type VfoOperationHandles = Readonly<{
    split: VfoOperationHandle | null;
    dualWatch: VfoOperationHandle | null;
    activeReceiver: VfoOperationHandle | null;
    equalize: VfoOperationHandle | null;
    swap: VfoOperationHandle | null;
    quickSplit: VfoOperationHandle | null;
    quickDualWatch: VfoOperationHandle | null;
    speak: VfoOperationHandle | null;
  }>;

  interface Props {
    input: VfoOperationProjectionInput;
    finiteAppearance: FiniteControlAppearance<string | number>;
    rendererContext: FiniteRendererContext | null;
    scheme: RadioViewModel['vfoScheme'];
    children: Snippet<[VfoOperationHandles]>;
  }
</script>

<script lang="ts">
  import { onDestroy } from 'svelte';
  import { t } from '$lib/i18n';
  import { vfoEqualLabel, vfoSwapLabel } from '../components-v2/vfo/vfo-ops-utils';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createAbsoluteChoiceRendererSeat, createActionRendererSeat, createToggleRendererSeat,
    type AbsoluteChoiceRendererInput, type AvailabilityActionRendererInput,
    type ToggleRendererInput,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import {
    invokeVfoOperation, projectVfoOperations, type VfoActionOperation,
    type VfoOperationIntent, type VfoOperationProjection, type VfoToggleOperation,
  } from './vfo-operation-projection';

  let { input, finiteAppearance, rendererContext, scheme, children }: Props = $props();

  const projection = (): VfoOperationProjection => projectVfoOperations(input);
  const invoke = (intent: VfoOperationIntent): void => invokeVfoOperation(() => input, intent);

  function toggleInput(
    operation: VfoToggleOperation,
    label: string,
    intent: VfoOperationIntent,
  ): ToggleRendererInput {
    return {
      context: rendererContext,
      field: { availability: operation.availability, reading: operation.reading },
      label,
      title: operation.availability.reason,
      invoke: () => invoke(intent),
    };
  }

  function actionInput(
    operation: VfoActionOperation,
    label: string,
    intent: VfoOperationIntent,
  ): AvailabilityActionRendererInput {
    return {
      context: rendererContext,
      availability: operation.availability,
      label,
      title: operation.availability.reason,
      invoke: () => invoke(intent),
    };
  }

  function receiverInput(): AbsoluteChoiceRendererInput<VfoOperationReceiver> {
    const operation = projection().activeReceiver;
    return {
      context: rendererContext,
      reading: operation.reading.status === 'known'
        ? { status: 'known', value: operation.reading.receiver }
        : { status: 'unknown' },
      available: operation.availability.operational,
      label: 'Receiver',
      accessibleLabel: 'Active receiver',
      options: operation.options
        .filter((option) => option.availability.structural)
        .map((option) => ({
          value: option.value,
          label: option.value,
          disabled: !option.availability.operational,
          ...(option.availability.reason === undefined
            ? {} : { disabledReason: option.availability.reason }),
        })),
      invoke: (receiver) => invoke({ kind: 'select-receiver', receiver }),
    };
  }

  const splitSeat = createToggleRendererSeat(() => toggleInput(
    projection().split, t('core.vfo.split.label'), { kind: 'toggle-split' },
  ));
  const dualWatchSeat = createToggleRendererSeat(() => toggleInput(
    projection().dualWatch, t('core.vfo.dualWatch.label'), { kind: 'toggle-dual-watch' },
  ));
  const receiverSeat = createAbsoluteChoiceRendererSeat(receiverInput);
  const equalizeSeat = createActionRendererSeat(() => actionInput(
    projection().equalize, vfoEqualLabel(scheme), { kind: 'equalize' },
  ));
  const swapSeat = createActionRendererSeat(() => actionInput(
    projection().swap, vfoSwapLabel(scheme), { kind: 'swap' },
  ));
  const quickSplitSeat = createActionRendererSeat(() => actionInput(
    projection().quickSplit, t('core.vfo.ops.quickSplit'), { kind: 'quick-split' },
  ));
  const quickDualWatchSeat = createActionRendererSeat(() => actionInput(
    projection().quickDualWatch, t('core.vfo.ops.quickDualWatch'), { kind: 'quick-dual-watch' },
  ));
  const speakSeat = createActionRendererSeat(() => actionInput(
    projection().speak, 'SPEAK', { kind: 'speak' },
  ));

  const seats = [
    splitSeat, dualWatchSeat, receiverSeat, equalizeSeat, swapSeat,
    quickSplitSeat, quickDualWatchSeat, speakSeat,
  ] as const;
  onDestroy(() => {
    for (const seat of seats) seat.destroy();
  });

  function handles(): VfoOperationHandles {
    const current = projection();
    return {
      split: current.split.availability.structural ? split : null,
      dualWatch: current.dualWatch.availability.structural ? dualWatch : null,
      activeReceiver: current.activeReceiver.availability.structural ? activeReceiver : null,
      equalize: current.equalize.availability.structural ? equalize : null,
      swap: current.swap.availability.structural ? swap : null,
      quickSplit: current.quickSplit.availability.structural ? quickSplit : null,
      quickDualWatch: current.quickDualWatch.availability.structural ? quickDualWatch : null,
      speak: current.speak.availability.structural ? speak : null,
    };
  }
</script>

{#snippet split()}
  {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
    seat={splitSeat} renderer={finiteAppearance.toggle}
  />{/key}{/key}
{/snippet}
{#snippet dualWatch()}
  {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
    seat={dualWatchSeat} renderer={finiteAppearance.toggle}
  />{/key}{/key}
{/snippet}
{#snippet activeReceiver()}
  {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
    seat={receiverSeat} renderer={finiteAppearance.choice}
  />{/key}{/key}
{/snippet}
{#snippet equalize()}
  {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
    seat={equalizeSeat} renderer={finiteAppearance.action}
  />{/key}{/key}
{/snippet}
{#snippet swap()}
  {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
    seat={swapSeat} renderer={finiteAppearance.action}
  />{/key}{/key}
{/snippet}
{#snippet quickSplit()}
  {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
    seat={quickSplitSeat} renderer={finiteAppearance.action}
  />{/key}{/key}
{/snippet}
{#snippet quickDualWatch()}
  {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
    seat={quickDualWatchSeat} renderer={finiteAppearance.action}
  />{/key}{/key}
{/snippet}
{#snippet speak()}
  {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
    seat={speakSeat} renderer={finiteAppearance.action}
  />{/key}{/key}
{/snippet}

{@render children(handles())}
