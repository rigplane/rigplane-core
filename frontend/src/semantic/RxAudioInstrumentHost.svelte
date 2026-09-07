<script lang="ts">
  import { onDestroy, onMount, type Snippet } from 'svelte';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import { ValueControl } from '../components-v2/controls/value-control';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
    type ContinuousScalarInput,
    type ContinuousScalarPolicy,
    type ScalarDomain,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import type {
    RxAudioAuthorityPublication,
    RxAudioInstrumentHandles,
    RxAudioInstrumentPresentation,
    SubscribeRxAudioAuthority,
  } from './rx-audio-instruments';

  interface Props {
    presentation: RxAudioInstrumentPresentation;
    subscribeControlAuthority: SubscribeRxAudioAuthority;
    onAfLevelChange?: (value: number) => void;
    children: Snippet<[RxAudioInstrumentHandles]>;
  }

  type Receiver = 'MAIN' | 'SUB' | 'unknown';
  type AfTarget = 'browser-volume' | `radio-af:${Receiver}`;
  interface AfAuthority {
    readonly epoch: number;
    readonly generation: number;
    readonly topologyId: string;
    readonly receiver: Receiver;
    readonly muted: boolean;
    readonly target: AfTarget;
  }

  let {
    presentation, subscribeControlAuthority, onAfLevelChange, children,
  }: Props = $props();
  let published = $state.raw<RxAudioAuthorityPublication | null>(null);
  let lastAuthority: AfAuthority | null | undefined;
  let stop: (() => void) | null = null;

  const safeGeneration = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  function authority(source: RxAudioAuthorityPublication): AfAuthority | null {
    const stateGeneration = source.state?.providerGeneration;
    const capsGeneration = source.caps?.providerGeneration;
    if (source.session.state !== 'connected'
      || !safeGeneration(source.session.epoch)
      || !safeGeneration(stateGeneration)
      || !safeGeneration(capsGeneration)
      || stateGeneration !== capsGeneration) return null;
    const model = toRadioViewModel(source.state, source.caps);
    if (model === null) return null;
    const receiver = model.activeReceiver.status === 'known'
      ? model.activeReceiver.receiver : 'unknown';
    return {
      epoch: source.session.epoch,
      generation: stateGeneration,
      topologyId: model.topologyId,
      receiver,
      muted: source.rxAudioTarget.muted,
      target: source.rxAudioTarget.rxEnabled ? 'browser-volume' : `radio-af:${receiver}`,
    };
  }
  function same(left: AfAuthority | null, right: AfAuthority | null): boolean {
    if (left === null || right === null) return left === right;
    return left.epoch === right.epoch
      && left.generation === right.generation
      && left.topologyId === right.topologyId
      && left.receiver === right.receiver
      && left.muted === right.muted
      && left.target === right.target;
  }
  const key = (value: AfAuthority | null): string => value === null
    ? 'rx-af:inactive'
    : JSON.stringify([
      'rx-af', value.epoch, value.generation, value.topologyId, value.receiver,
      value.muted, value.target,
    ]);

  function input(): Readonly<ContinuousScalarInput> {
    const field = presentation.rxAudio?.afLevel;
    const currentAuthority = published === null ? null : authority(published);
    const presentedAuthority = authority(presentation);
    const reading = field?.reading.status === 'known'
      ? { status: 'known' as const, value: field.reading.value }
      : { status: 'unknown' as const };
    const targetKnown = currentAuthority?.target === 'browser-volume'
      || currentAuthority?.target === 'radio-af:MAIN'
      || currentAuthority?.target === 'radio-af:SUB';
    const readingMatchesTarget = currentAuthority?.target === 'browser-volume'
      ? presentation.rxAudio?.monitorMode === 'live'
      : presentation.rxAudio?.monitorMode !== 'live';
    return {
      evidence: 'reading',
      domain: { min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 1 },
      enabled: same(currentAuthority, presentedAuthority)
        && targetKnown
        && readingMatchesTarget
        && field?.availability.structural === true
        && field.availability.operational
        && reading.status === 'known'
        && Number.isFinite(reading.value)
        && onAfLevelChange !== undefined,
      request: (value) => onAfLevelChange?.(value),
      reading,
      ownerKey: key(currentAuthority),
    };
  }

  const basePolicy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 });
  const policy: Readonly<ContinuousScalarPolicy> = Object.freeze({
    ...basePolicy,
    reset: (domain: ScalarDomain) => domain.defaultValue,
  });
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const afLevelBinding = createContinuousScalar(input, policy);

  onMount(() => {
    stop = subscribeControlAuthority((next) => {
      const nextAuthority = authority(next);
      if (lastAuthority !== undefined && !same(lastAuthority, nextAuthority)) {
        afLevelBinding.cancel('authority');
      }
      lastAuthority = nextAuthority;
      published = next;
    });
  });
  onDestroy(() => {
    try { stop?.(); } finally { afLevelBinding.destroy(); }
  });
</script>

{#snippet afLevel()}
  <ValueControl
    {...feedbackIntegratedControl}
    binding={afLevelBinding} label="AF" renderer="hbar"
    showLabel={false} showValue={false} compact={true}
  />
{/snippet}

{@render children({ afLevel })}
