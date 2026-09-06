<script module lang="ts">
  import type { Snippet } from 'svelte';

  export type ReceiverFrequencyMount = Readonly<{ compact?: boolean; vfoFreqHook?: boolean }>;
  export type ReceiverSMeterMount = Readonly<{
    compact?: boolean; label?: string; variant?: 'vfo' | 'vfo-wide' | 'sdr-screen';
  }>;

  export interface ReceiverInstrumentHandles {
    readonly mainFrequency: Snippet<[mount?: ReceiverFrequencyMount]>;
    readonly subFrequency?: Snippet<[mount?: ReceiverFrequencyMount]>;
    readonly mainSMeter: Snippet<[mount?: ReceiverSMeterMount]>;
    readonly subSMeter?: Snippet<[mount?: ReceiverSMeterMount]>;
    readonly vfoOperations: Snippet;
  }
</script>

<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import type { Capabilities } from '$lib/types/capabilities';
  import type { ServerState } from '$lib/types/state';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import { t } from '$lib/i18n';
  import FrequencyRendererSeat from '../primitives/frequency/FrequencyRendererSeat.svelte';
  import {
    createFrequencyInstrumentBinding,
    type FrequencyInstrumentBinding,
  } from '../primitives/frequency/frequency-instrument.svelte';
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import {
    createSignalMeterMotion,
    type SignalMeterMotionBinding,
    type SignalMeterMotionInput,
  } from '../components-v2/meters/signal-meter-motion.svelte';
  import { projectSignalMeter } from '../components-v2/meters/smeter-scale';
  import type { RadioViewModel, ReceiverId, VfoViewModel } from './radio-view-model';

  type ReceiverAuthorityPublication = Readonly<{
    state: ServerState | null; caps: Capabilities | null;
    session: Readonly<{
      state: 'disconnected' | 'connecting' | 'connected' | 'reconnecting'; epoch: number;
    }>;
  }>;

  export type SubscribeReceiverAuthority = (
    handler: (publication: ReceiverAuthorityPublication) => void,
  ) => () => void;

  interface Props {
    subscribeControlAuthority: SubscribeReceiverAuthority;
    pendingFrequencyHz?: Partial<Record<ReceiverId, number>>;
    onTuneFrequency?: (receiver: ReceiverId, frequencyHz: number) => void;
    vfoOperations: Snippet;
    children: Snippet<[ReceiverInstrumentHandles]>;
  }

  type ReceiverFrequencyAuthority = Readonly<{
    sessionState: 'connected'; sessionEpoch: number; matchedProviderGeneration: number;
    topologyId: string; activeReceiver: ReceiverId | 'unknown'; receiver: ReceiverId;
    activeSlotIdentity: string;
  }>;

  interface ReceiverOwner {
    readonly receiver: ReceiverId;
    readonly frequency: FrequencyInstrumentBinding;
    readonly motion: SignalMeterMotionBinding;
    readonly model: RadioViewModel | null;
    readonly context: object | null;
    readonly active: boolean;
    update(model: RadioViewModel | null, authority: ReceiverFrequencyAuthority | null): void;
    syncMeter(input: SignalMeterMotionInput): void;
  }

  let {
    subscribeControlAuthority,
    pendingFrequencyHz,
    onTuneFrequency,
    vfoOperations,
    children,
  }: Props = $props();

  let mainOwner = $state.raw<ReceiverOwner | null>(null);
  let subOwner = $state.raw<ReceiverOwner | null>(null);
  let mainOwnerRoot: (() => void) | null = null;
  let subOwnerRoot: (() => void) | null = null;
  let mounted = false;
  let destroyed = false;

  const safeGeneration = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

  function activeRecord(model: RadioViewModel | null, receiver: ReceiverId): VfoViewModel | null {
    const records = model?.vfos.filter((record) => record.receiver === receiver && record.isActiveSlot) ?? [];
    return records.length === 1 ? records[0] : null;
  }

  function displayFrequency(record: VfoViewModel | null): number | null {
    if (record === null) return null;
    const display = record.display?.frequencyHz;
    return display === undefined
      ? record.frequencyHz
      : display.state === 'current' || display.state === 'stale' ? display.value : null;
  }

  function slotIdentity(record: VfoViewModel): string {
    const slot = record.slot;
    if (slot.kind === 'slotted') return `slotted:${slot.id}`;
    if (slot.kind === 'relative') return `relative:${slot.role}`;
    return slot.kind;
  }

  function sameAuthority(
    left: ReceiverFrequencyAuthority | null,
    right: ReceiverFrequencyAuthority | null,
  ): boolean {
    if (left === null || right === null) return left === right;
    return left.sessionEpoch === right.sessionEpoch
      && left.matchedProviderGeneration === right.matchedProviderGeneration
      && left.topologyId === right.topologyId
      && left.activeReceiver === right.activeReceiver
      && left.receiver === right.receiver
      && left.activeSlotIdentity === right.activeSlotIdentity;
  }

  function frequencyAuthority(
    publication: ReceiverAuthorityPublication, model: RadioViewModel | null, receiver: ReceiverId,
  ): ReceiverFrequencyAuthority | null {
    const stateGeneration = publication.state?.providerGeneration;
    const capsGeneration = publication.caps?.providerGeneration;
    if (publication.session.state !== 'connected'
      || !safeGeneration(publication.session.epoch)
      || !safeGeneration(stateGeneration)
      || !safeGeneration(capsGeneration)
      || stateGeneration !== capsGeneration
      || model === null
      || !model.vfos.some((record) => record.receiver === receiver)) return null;
    const active = model.vfos.filter((record) => record.receiver === receiver && record.isActiveSlot);
    return {
      sessionState: 'connected',
      sessionEpoch: publication.session.epoch,
      matchedProviderGeneration: stateGeneration,
      topologyId: model.topologyId,
      activeReceiver: model.activeReceiver.status === 'known'
        ? model.activeReceiver.receiver : 'unknown',
      receiver,
      activeSlotIdentity: active.length === 1 ? slotIdentity(active[0]) : 'unknown',
    };
  }

  function createOwner(
    receiver: ReceiverId, initialModel: RadioViewModel,
    initialAuthority: ReceiverFrequencyAuthority,
  ): ReceiverOwner {
    let model = $state.raw<RadioViewModel | null>(initialModel);
    let authority = $state.raw<ReceiverFrequencyAuthority | null>(initialAuthority);
    let context = $state.raw<object | null>({});
    const receiverKey = receiver === 'SUB' ? 'sub' : 'main';
    const frequency = createFrequencyInstrumentBinding({
      get confirmedHz() { return activeRecord(model, receiver)?.frequencyHz ?? null; },
      get displayHz() { return displayFrequency(activeRecord(model, receiver)); },
      get pendingDisplayHz() { return pendingFrequencyHz?.[receiver]; },
      get pendingAnnouncement() {
        return pendingFrequencyHz?.[receiver] === undefined
          ? undefined : t('core.vfo.freq.pendingAnnouncement');
      },
      get disabled() {
        const record = activeRecord(model, receiver);
        const indicator = model?.receiverIndicators?.find((item) => item.receiver === receiver);
        return record === null
          || indicator?.availability.operational !== true
          || record.frequencyHz === null
          || !Number.isFinite(record.frequencyHz)
          || record.display?.frequencyHz.state !== 'current';
      },
      get context() { return context; },
      receiver: receiverKey,
      minFreq: 0,
      maxFreq: 999_000_000,
      get onFreqChange() {
        return onTuneFrequency === undefined
          ? undefined : (frequencyHz: number) => onTuneFrequency?.(receiver, frequencyHz);
      },
    });
    const indicator = initialModel.receiverIndicators?.find((item) => item.receiver === receiver);
    const motion = createSignalMeterMotion({
      projection: projectSignalMeter(
        indicator?.sMeter.availability.operational
          && indicator.sMeter.reading.status === 'known'
          && Number.isFinite(indicator.sMeter.reading.value)
          ? indicator.sMeter.reading.value : null,
      ),
      present: indicator?.sMeter.availability.structural ?? false,
      source: indicator?.sMeter.source,
      session: { controlSessionEpoch: initialAuthority.sessionEpoch },
    });
    return {
      receiver,
      frequency,
      motion,
      get model() { return model; },
      get context() { return context; },
      get active() { return activeRecord(model, receiver)?.isActive ?? false; },
      update(nextModel, nextAuthority) {
        if (nextAuthority !== null) model = nextModel;
        if (!sameAuthority(authority, nextAuthority)) context = nextAuthority === null ? null : {};
        authority = nextAuthority;
      },
      syncMeter: (input) => motion.sync(input),
    };
  }

  function stopOwner(receiver: ReceiverId): void {
    const owner = receiver === 'MAIN' ? mainOwner : subOwner;
    owner?.motion.stop();
    if (receiver === 'MAIN') {
      mainOwnerRoot?.(); mainOwnerRoot = null; mainOwner = null;
    } else {
      subOwnerRoot?.(); subOwnerRoot = null; subOwner = null;
    }
  }

  function installOwner(
    receiver: ReceiverId, model: RadioViewModel, authority: ReceiverFrequencyAuthority,
  ): ReceiverOwner {
    let created!: ReceiverOwner;
    const dispose = $effect.root(() => { created = createOwner(receiver, model, authority); });
    if (receiver === 'MAIN') {
      mainOwner = created; mainOwnerRoot = dispose;
    } else {
      subOwner = created; subOwnerRoot = dispose;
    }
    if (mounted) created.motion.start();
    return created;
  }

  function applyPublication(publication: ReceiverAuthorityPublication): void {
    if (destroyed) return;
    const model = toRadioViewModel(publication.state, publication.caps);
    const validModel = (['MAIN', 'SUB'] as const).some((receiver) =>
      frequencyAuthority(publication, model, receiver) !== null) ? model : null;

    if (validModel !== null) {
      for (const receiver of ['MAIN', 'SUB'] as const) {
        const structural = validModel.vfos.some((record) => record.receiver === receiver);
        const current = receiver === 'MAIN' ? mainOwner : subOwner;
        if (!structural) {
          if (current !== null) stopOwner(receiver);
          continue;
        }
        const authority = frequencyAuthority(publication, validModel, receiver)!;
        if (current === null) {
          installOwner(receiver, validModel, authority);
        }
      }
    }

    const session = validModel !== null
      ? { controlSessionEpoch: publication.session.epoch } : null;
    for (const owner of [mainOwner, subOwner]) {
      if (owner === null) continue;
      const authority = frequencyAuthority(publication, model, owner.receiver);
      owner.update(model, authority);
      const indicator = model?.receiverIndicators?.find((item) => item.receiver === owner.receiver);
      const value = indicator?.sMeter.availability.operational
        && indicator.sMeter.reading.status === 'known'
        && Number.isFinite(indicator.sMeter.reading.value)
        ? indicator.sMeter.reading.value : null;
      owner.syncMeter({
        projection: projectSignalMeter(value),
        present: indicator?.sMeter.availability.structural ?? false,
        source: indicator?.sMeter.source,
        session,
      });
    }
  }

  const initialSubscribe = untrack(() => subscribeControlAuthority);
  let unsubscribe: () => void = () => undefined;
  unsubscribe = initialSubscribe(applyPublication);

  onMount(() => {
    mounted = true;
    mainOwner?.motion.start();
    subOwner?.motion.start();
  });

  onDestroy(() => {
    destroyed = true;
    unsubscribe();
    mainOwner?.motion.stop();
    subOwner?.motion.stop();
    mainOwnerRoot?.();
    subOwnerRoot?.();
  });

</script>

{#snippet mainFrequency(mount?: ReceiverFrequencyMount)}
  {#if mainOwner !== null}
    <FrequencyRendererSeat binding={mainOwner.frequency} presentation="interactive"
      compact={mount?.compact ?? false} active={mainOwner.active} receiver="main"
      vfoFreqHook={mount?.vfoFreqHook ?? true} />
  {/if}
{/snippet}

{#snippet subFrequency(mount?: ReceiverFrequencyMount)}
  {#if subOwner !== null}
    <FrequencyRendererSeat binding={subOwner.frequency} presentation="interactive"
      compact={mount?.compact ?? false} active={subOwner.active} receiver="sub"
      vfoFreqHook={mount?.vfoFreqHook ?? true} />
  {/if}
{/snippet}

{#snippet mainSMeter(mount?: ReceiverSMeterMount)}
  {#if mainOwner !== null}
    <LinearSMeter frame={mainOwner.motion.frame} compact={mount?.compact ?? false}
      label={mount?.label} variant={mount?.variant} />
  {/if}
{/snippet}

{#snippet subSMeter(mount?: ReceiverSMeterMount)}
  {#if subOwner !== null}
    <LinearSMeter frame={subOwner.motion.frame} compact={mount?.compact ?? false}
      label={mount?.label} variant={mount?.variant} />
  {/if}
{/snippet}

{#if subOwner === null}
  {@render children({ mainFrequency, mainSMeter, vfoOperations })}
{:else}
  {@render children({ mainFrequency, subFrequency, mainSMeter, subSMeter, vfoOperations })}
{/if}
