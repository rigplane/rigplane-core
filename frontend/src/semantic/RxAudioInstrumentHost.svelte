<script lang="ts">
  import { onDestroy, onMount, type Snippet } from 'svelte';
  import { t } from '$lib/i18n';
  import { LAN_MOD_INPUT_SOURCE } from '$lib/radio/mod-input';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import { ValueControl } from '../components-v2/controls/value-control';
  import {
    bindAbsoluteChoiceInstrument, bindActionInstrument, bindChoiceInstrument,
  } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createAbsoluteChoiceRendererSeat, createActionRendererSeat, createChoiceRendererSeat,
    createToggleRendererSeat,
    type AvailabilityActionRendererInput,
    type FiniteControlAppearance, type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
    type CommandScalarFeedback,
    type ContinuousScalarInput,
    type ContinuousScalarPolicy,
    type ScalarDomain,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import type { AudioFocus, MonitorMode, RxAudioField } from './radio-view-model';
  import {
    FOCUS_CHOICES, LINK_LOST_TEXT, MONITOR_MODES, READINESS_LABEL, SPLIT_CHOICES,
  } from './rx-audio-instruments';
  import type {
    RxAudioAuthorityPublication,
    RxAudioFiniteChoiceValue,
    RxAudioInstrumentHandles,
    RxAudioInstrumentPresentation,
    RxAudioSplitLabel,
    SubscribeRxAudioAuthority,
  } from './rx-audio-instruments';

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was actually read.
   *  Deliberately re-declared rather than imported from `RxAudioSurface.svelte`
   *  — the same small-predicate duplication `DspInstrumentHost`/`DspSurface`
   *  already carry between a host and its paired surface (both declare their
   *  own `usable`). */
  const usable = (f: RxAudioField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** MOR-2527 (owner rule 2026-09-21): a routing VALUE renders no text at
   *  all while the reading is unknown — an unlit slot, never a `—`
   *  placeholder. The `<output>` stays mounted with a reserved min-width so
   *  the row does not shift when the reading arrives. The split row's slot
   *  NEVER carries text, known or not: its lit key already states on/off,
   *  and the boolean would only echo as raw `true`/`false` (round-2
   *  coordinator ruling). */
  const unlitTextOf = (f: RxAudioField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : '';
  const afPercent = (f: RxAudioField<number>): string =>
    f.reading.status === 'known' ? `${Math.round(f.reading.value * 100)}%` : '';

  interface ExistingProps {
    presentation: RxAudioInstrumentPresentation;
    subscribeControlAuthority: SubscribeRxAudioAuthority;
    onAfLevelChange?: (value: number, unit?: 'raw') => void;
    afLevelFeedback?: Readonly<CommandScalarFeedback>;
    /** MOR-2579: the per-receiver AF knobs (`rxAudio.receiverAfLevels`). */
    onReceiverAfLevelChange?: (receiver: AfReceiverKey, value: number, unit?: 'raw') => void;
    receiverAfLevelFeedback?: Readonly<Record<AfReceiverKey, Readonly<CommandScalarFeedback>>>;
    onMonitorModeChange?: (mode: MonitorMode) => void;
    onFocusChange?: (focus: AudioFocus) => void;
    onSplitStereoChange?: (split: boolean) => void;
    routingGains?: Readonly<{ main: number; sub: number }> | null;
    onChannelGainChange?: (channel: 'main' | 'sub', value: number) => void;
    onModInputChange?: (source: number) => void;
    onSetModInputLan?: () => void;
    children: Snippet<[RxAudioInstrumentHandles]>;
  }
  type RendererSelection =
    | { finiteAppearance?: undefined; rendererContext?: undefined }
    | {
        finiteAppearance: FiniteControlAppearance<RxAudioFiniteChoiceValue>;
        rendererContext: FiniteRendererContext | null;
      };
  type Props = ExistingProps & RendererSelection;

  type Receiver = 'MAIN' | 'SUB' | 'unknown';
  type AfReceiverKey = 'main' | 'sub';
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
    presentation, subscribeControlAuthority, onAfLevelChange, afLevelFeedback,
    onReceiverAfLevelChange, receiverAfLevelFeedback,
    onMonitorModeChange, onFocusChange, onSplitStereoChange, routingGains = null,
    onChannelGainChange, onModInputChange, onSetModInputLan,
    finiteAppearance, rendererContext, children,
  }: Props = $props();
  let published = $state.raw<RxAudioAuthorityPublication | null>(null);
  let lastAuthority: AfAuthority | null | undefined;
  let stop: (() => void) | null = null;
  let rx = $derived(presentation.rxAudio);
  let modInputChoices = $derived(rx?.modInputChoices ?? []);
  let modInputReadingValue = $derived(
    rx?.modInputSource.reading.status === 'known' ? rx.modInputSource.reading.value : undefined,
  );
  /** MOR-1279: the FACT, never a capability re-derivation. */
  let liveOffered = $derived(rx?.liveAudio.structural === true);
  /** MOR-1384 — the v2 `RxAudioPanel` link-lost readout, restored from the SAME
   *  underlying fact (`liveAudio.operational` is `runtime.connectionAudio`, the
   *  identical audio-WS health the retired panel read as `isAudioConnected`).
   *  `monitorMode === 'live'` is the EPISTEMIC leg: while `local`/`mute` is
   *  selected the audio WS was never requested, so a down link there is not a
   *  loss. Structurally absent live audio has no link to lose at all. */
  let linkLost = $derived(
    liveOffered && rx?.monitorMode === 'live' && rx.liveAudio.operational === false,
  );
  const monitorLabel: Record<MonitorMode, string> = {
    local: 'RADIO', live: 'LIVE', mute: 'MUTE',
  };
  const monitorStatusText: Record<MonitorMode, string> = {
    local: 'Radio speaker output', live: 'Browser audio stream', mute: 'Audio muted',
  };
  let modInputRecognized = $derived(
    modInputReadingValue !== undefined
      && modInputChoices.some(option => option.value === modInputReadingValue),
  );
  /** MOR-2527 owner rule: the MOD readout label is the constant `MOD`; the
   *  source value renders in its OWN span, empty while unread. The span's
   *  reserved width is computed from the labels the select offers — never a
   *  hardcoded model-specific width — so neither it nor the readiness span
   *  after it moves when the reading arrives. */
  let modSourceValue = $derived(modInputReadingValue === undefined ? ''
    : modInputChoices.find(option => option.value === modInputReadingValue)?.label
      ?? String(modInputReadingValue));
  let modSourceWidthCh = $derived(
    Math.max(0, ...modInputChoices.map((option) => option.label.length)),
  );

  const monitorBehavior = bindAbsoluteChoiceInstrument<MonitorMode>(() => ({
    choices: MONITOR_MODES.filter((mode) => mode !== 'live' || liveOffered),
    selected: rx?.monitorMode,
    available: rx !== undefined,
    invoke: (mode) => onMonitorModeChange?.(mode),
  }));
  const focusBehavior = bindAbsoluteChoiceInstrument<AudioFocus>(() => ({
    choices: FOCUS_CHOICES,
    selected: rx?.routingFocus.reading.status === 'known'
      ? rx.routingFocus.reading.value : undefined,
    available: rx?.routingFocus.availability.structural === true,
    invoke: (focus) => onFocusChange?.(focus),
  }));
  const splitBehavior = bindAbsoluteChoiceInstrument<boolean>(() => ({
    choices: SPLIT_CHOICES.map(([value]) => value),
    selected: rx?.routingSplit.reading.status === 'known'
      ? rx.routingSplit.reading.value : undefined,
    available: rx?.routingSplit.availability.structural === true,
    invoke: (split) => onSplitStereoChange?.(split),
  }));
  const modInputBehavior = bindChoiceInstrument<number>(() => ({
    field: rx?.modInputSource,
    choices: modInputChoices.map((option) => option.value),
    blocked: !modInputRecognized,
    invoke: (source) => onModInputChange?.(source),
  }));
  let modInputValue = $derived(
    modInputBehavior.selected === undefined ? '' : String(modInputBehavior.selected),
  );
  function changeModInput(select: HTMLSelectElement): void {
    const value = select.value;
    const source = modInputChoices.find((option) => String(option.value) === value);
    select.value = modInputValue;
    if (source) modInputBehavior.invoke(source.value);
  }
  /** The split seat's external-facing choice VALUE is `SPLIT_CHOICES`'s own
   *  string LABEL, not the raw boolean fact — the public Component-Kit SDK's
   *  `FiniteChoiceValue` (every other production seat's bound, and
   *  `RxAudioFiniteChoiceValue`'s own bound) is `string | number`, and never
   *  `boolean` (`component-kit-api/src/index.ts`). The raw/bare rendering
   *  below is unaffected — it reads `SPLIT_CHOICES`/`rx.routingSplit` directly. */
  const splitLabelOf = (value: boolean): RxAudioSplitLabel =>
    SPLIT_CHOICES.find(([choice]) => choice === value)![1];
  const splitValueOf = (label: RxAudioSplitLabel): boolean =>
    SPLIT_CHOICES.find(([, choice]) => choice === label)![0];
  const monitorSeat = createAbsoluteChoiceRendererSeat<RxAudioFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, label: 'Monitor mode',
    reading: rx === undefined ? { status: 'unknown' } : { status: 'known', value: rx.monitorMode },
    available: rx !== undefined,
    options: MONITOR_MODES.filter((mode) => mode !== 'live' || liveOffered)
      .map((mode) => ({ value: mode, label: monitorLabel[mode] })),
    invoke: (mode) => onMonitorModeChange?.(mode as MonitorMode),
  }));
  const focusSeat = createAbsoluteChoiceRendererSeat<RxAudioFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, label: 'Audio focus',
    reading: rx?.routingFocus.reading ?? { status: 'unknown' },
    available: rx?.routingFocus.availability.structural === true,
    options: FOCUS_CHOICES.map((focus) => ({
      value: focus, label: focus === 'both' ? 'Both' : focus.toUpperCase(),
    })),
    invoke: (focus) => onFocusChange?.(focus as AudioFocus),
  }));
  const splitSeat = createAbsoluteChoiceRendererSeat<RxAudioFiniteChoiceValue>(() => {
    const reading = rx?.routingSplit.reading;
    return {
      context: rendererContext ?? null, label: 'Stereo split',
      reading: reading?.status === 'known'
        ? { status: 'known' as const, value: splitLabelOf(reading.value) }
        : { status: 'unknown' as const },
      available: rx?.routingSplit.availability.structural === true,
      options: SPLIT_CHOICES.map(([, label]) => ({ value: label, label })),
      invoke: (label) => onSplitStereoChange?.(splitValueOf(label as RxAudioSplitLabel)),
    };
  });
  const splitToggleSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: rx?.routingSplit, label: 'Stereo split',
    invoke: (next) => onSplitStereoChange?.(next),
  }));
  const modInputSeat = createChoiceRendererSeat<RxAudioFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, label: 'MOD input',
    field: rx?.modInputSource, blocked: !modInputRecognized,
    options: modInputChoices.map((option) => ({ value: option.value, label: option.label })),
    invoke: (source) => onModInputChange?.(source as number),
  }));
  function setLanInput(): AvailabilityActionRendererInput {
    return {
      context: rendererContext ?? null, label: 'Set LAN',
      availability: rx === undefined || !modInputChoices.some(option => option.value === LAN_MOD_INPUT_SOURCE)
        ? undefined : { structural: true, operational: true },
      blocked: rx?.modInputReadiness.status !== 'mismatch'
        || !modInputChoices.some(option => option.value === LAN_MOD_INPUT_SOURCE),
      invoke: () => onSetModInputLan?.(),
    };
  }
  const setLanSeat = createActionRendererSeat(() => setLanInput());
  const setLanBehavior = bindActionInstrument(() => setLanInput());

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
    const model = source.view === undefined
      ? toRadioViewModel(source.state, source.caps) : source.view;
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
  const key = (value: AfAuthority | null, owner = 'rx-af'): string => value === null
    ? `${owner}:inactive`
    : JSON.stringify([
      owner, value.epoch, value.generation, value.topologyId, value.receiver,
      value.muted, value.target,
    ]);
  /** Shared by the three AF bindings. */
  let presentedAfAuthority = $derived(authority(presentation));
  let publishedAfAuthority = $derived(published === null ? null : authority(published));
  /** MOR-1676 part A (AF): the browser-volume branch of the AF slider keeps
   *  the normalized 0..1 lattice it has always used — browser volume is a
   *  different meaning of the same slider, not a radio raw value. */
  const AF_DOMAIN = { min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 1 } as const;
  /** MOR-1676 part A (AF): the radio-AF slider moves on the radio's raw
   *  integer lattice (one step = one raw unit, step 1), read from the
   *  authority publication's capabilities (`controls.af_level`) — the same
   *  capability fact other controls read their ranges from (`ControlRange`
   *  `raw_min`/`raw_max`), never a hard-coded 255 in this surface. A radio
   *  that publishes no `controls.af_level` keeps the normalized lattice. */
  function radioAfDomain(source: RxAudioAuthorityPublication | null): {
    min: number; max: number; step: 1; defaultValue: null; fineStepDivisor: 1;
  } | null {
    // PREFER the render-time presentation caps: the publisher delivers them
    // with the view, while the subscription snapshot may lag one publication
    // behind. FALL BACK to the published snapshot so the domain and the
    // authority gate stay on one snapshot when they disagree.
    const control = presentation.caps?.controls?.af_level
      ?? source?.caps?.controls?.af_level;
    if (control === undefined || 'mapping' in control) return null;
    const { raw_min: rawMin, raw_max: rawMax } = control;
    if (!Number.isSafeInteger(rawMin) || !Number.isSafeInteger(rawMax) || rawMax <= rawMin) return null;
    return { min: rawMin, max: rawMax, step: 1, defaultValue: null, fineStepDivisor: 1 };
  }

  function input(): Readonly<ContinuousScalarInput> {
    const field = presentation.rxAudio?.afLevel;
    const currentAuthority = publishedAfAuthority;
    const presentedAuthority = presentedAfAuthority;
    const reading = field?.reading.status === 'known'
      ? { status: 'known' as const, value: field.reading.value }
      : { status: 'unknown' as const };
    const targetKnown = currentAuthority?.target === 'browser-volume'
      || currentAuthority?.target === 'radio-af:MAIN'
      || currentAuthority?.target === 'radio-af:SUB';
    const readingMatchesTarget = currentAuthority?.target === 'browser-volume'
      ? presentation.rxAudio?.monitorMode === 'live'
      : presentation.rxAudio?.monitorMode !== 'live';
    const enabled = same(currentAuthority, presentedAuthority)
      && targetKnown
      && currentAuthority?.muted === false
      && readingMatchesTarget
      && field?.availability.structural === true
      && field.availability.operational
      && reading.status === 'known'
      && Number.isFinite(reading.value)
      && onAfLevelChange !== undefined;
    // MOR-1676 part A (AF): on the radio target the slider moves on the
    // radio's raw integer lattice (the value in IS the raw value, dispatched
    // with the explicit `'raw'` unit), while the browser-volume target keeps
    // the normalized 0..1 lattice. The normalized readback (`raw/255`)
    // converts to the exact raw value (`Math.round(normalized * raw_max)`)
    // — exact for every raw value, so a step plus its reverse restore the
    // raw value. The domain follows the PUBLISHED authority: `published` is
    // the snapshot the authority gate above compares, while the render-time
    // `presentation` prop may carry caps the publisher has not delivered
    // yet.
    const isRadioTarget = currentAuthority?.target !== 'browser-volume';
    const rawDomain = isRadioTarget ? radioAfDomain(published) : null;
    const domain = rawDomain ?? AF_DOMAIN;
    const rawReading = rawDomain !== null && reading.status === 'known'
      ? {
        status: 'known' as const,
        value: Math.round(rawDomain.min + reading.value * (rawDomain.max - rawDomain.min)),
      }
      : reading;
    // MOR-1676 part A (AF): the value out carries its meaning explicitly —
    // the raw integer lattice sends the `'raw'` unit (the intent strips it
    // before `sendCommand`), the normalized lattice (browser volume, or a
    // radio that publishes no `controls.af_level`) sends the legacy
    // unit-less normalized float. The unit is explicit because JS cannot
    // dispatch on JSON type (`1.0 === 1`): the handler must not guess raw
    // from `Number.isInteger`.
    const base = {
      domain,
      enabled,
      request: (value: number) => rawDomain !== null
        ? onAfLevelChange?.(value, 'raw') : onAfLevelChange?.(value),
    } as const;
    if (afLevelFeedback !== undefined) return {
      ...base,
      evidence: 'command-feedback',
      feedback: afLevelFeedback,
      command: 'set_af_level',
    };
    return {
      ...base,
      evidence: 'reading',
      reading: rawReading,
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

  /** MOR-2579: a per-receiver knob's authority names its OWN receiver, so a
   *  MAIN/SUB selection change neither retargets nor cancels it. */
  function receiverAuthority(base: AfAuthority | null, receiver: AfReceiverKey): AfAuthority | null {
    if (base === null) return null;
    const fixed = receiver === 'main' ? 'MAIN' : 'SUB';
    return {
      ...base, receiver: fixed,
      target: base.target === 'browser-volume' ? base.target : `radio-af:${fixed}`,
    };
  }
  function receiverAfInput(receiver: AfReceiverKey): Readonly<ContinuousScalarInput> {
    const field = presentation.rxAudio?.receiverAfLevels?.[receiver];
    const currentAuthority = receiverAuthority(publishedAfAuthority, receiver);
    const reading = field?.reading.status === 'known'
      ? { status: 'known' as const, value: field.reading.value }
      : { status: 'unknown' as const };
    const enabled = same(currentAuthority, receiverAuthority(presentedAfAuthority, receiver))
      && currentAuthority?.target !== 'browser-volume'
      && currentAuthority?.muted === false
      && presentation.rxAudio?.monitorMode !== 'live'
      && field?.availability.structural === true
      && field.availability.operational
      && reading.status === 'known'
      && Number.isFinite(reading.value)
      && onReceiverAfLevelChange !== undefined;
    // MOR-1676 part A (AF): the named-receiver knob moves on the radio's raw
    // integer lattice, like the active-receiver slider above — the value in
    // IS the raw value, dispatched with the explicit `'raw'` unit. The
    // domain follows the PUBLISHED authority (`published`), the same
    // snapshot the gate above compares — never the render-time
    // `presentation` prop, whose caps the publisher may not have delivered
    // yet.
    const rawDomain = radioAfDomain(published);
    const domain = rawDomain ?? AF_DOMAIN;
    const rawReading = rawDomain !== null && reading.status === 'known'
      ? {
        status: 'known' as const,
        value: Math.round(rawDomain.min + reading.value * (rawDomain.max - rawDomain.min)),
      }
      : reading;
    const base = {
      domain,
      enabled,
      request: (value: number) => rawDomain !== null
        ? onReceiverAfLevelChange?.(receiver, value, 'raw') : onReceiverAfLevelChange?.(receiver, value),
    } as const;
    const feedback = receiverAfLevelFeedback?.[receiver];
    if (feedback !== undefined) return {
      ...base, evidence: 'command-feedback', feedback, command: 'set_af_level',
    };
    return {
      ...base, evidence: 'reading', reading: rawReading, ownerKey: key(currentAuthority, 'rx-receiver-af'),
    };
  }
  const receiverAfBindings = {
    main: createContinuousScalar(() => receiverAfInput('main'), policy),
    sub: createContinuousScalar(() => receiverAfInput('sub'), policy),
  } as const;

  const CHANNEL_GAIN_DOMAIN = {
    min: -60, max: 12, step: 1, defaultValue: null, fineStepDivisor: 1,
  } as const;
  function channelGainInput(channel: 'main' | 'sub'): Readonly<ContinuousScalarInput> {
    const value = channel === 'main' ? routingGains?.main : routingGains?.sub;
    const known = value !== undefined && Number.isFinite(value);
    return {
      domain: CHANNEL_GAIN_DOMAIN,
      enabled: known && rx?.routingFocus.availability.operational === true
        && onChannelGainChange !== undefined,
      request: (next: number) => onChannelGainChange?.(channel, next),
      evidence: 'reading',
      reading: known ? { status: 'known', value } : { status: 'unknown' },
      ownerKey: `rx-audio-gain:${channel}`,
    };
  }
  const channelGainBindings = {
    main: createContinuousScalar(() => channelGainInput('main'), policy),
    sub: createContinuousScalar(() => channelGainInput('sub'), policy),
  } as const;

  onMount(() => {
    stop = subscribeControlAuthority((next) => {
      const nextAuthority = authority(next);
      if (lastAuthority !== undefined && !same(lastAuthority, nextAuthority)) {
        afLevelBinding.cancel('authority');
      }
      for (const receiver of ['main', 'sub'] as const) {
        if (lastAuthority !== undefined && !same(
          receiverAuthority(lastAuthority, receiver), receiverAuthority(nextAuthority, receiver),
        )) receiverAfBindings[receiver].cancel('authority');
      }
      lastAuthority = nextAuthority;
      published = next;
    });
    // `onMount` runs after the first render: `published` is still null
    // while the bindings above already read it. Seed it from the
    // render-time `presentation` — the same publication the publisher will
    // deliver — so the first frame (domain included) already sees it.
    // A caps-only difference never cancels a gesture: `published` is not
    // part of the binding's authority identity.
    published = { ...presentation };
  });
  const finiteSeats = [
    monitorSeat, focusSeat, splitSeat, splitToggleSeat, modInputSeat, setLanSeat,
  ] as const;
  onDestroy(() => {
    try { stop?.(); } finally {
      afLevelBinding.destroy();
      receiverAfBindings.main.destroy();
      receiverAfBindings.sub.destroy();
      channelGainBindings.main.destroy();
      channelGainBindings.sub.destroy();
      for (const seat of finiteSeats) seat.destroy();
    }
  });
</script>

{#snippet afLevelControl(hardware: boolean, receiver?: AfReceiverKey)}
  <ValueControl
    {...feedbackIntegratedControl}
    binding={receiver === undefined ? afLevelBinding : receiverAfBindings[receiver]}
    label={receiver === undefined ? 'AF' : `AF ${receiver.toUpperCase()}`} renderer="hbar"
    showLabel={false} showValue={false} compact={true}
    variant={hardware ? 'hardware-illuminated' : 'modern'}
    accentColor={hardware ? 'var(--v2-accent-cyan-alt)' : 'var(--v2-accent-cyan)'}
  />
{/snippet}
{#snippet afLevel()}{@render afLevelControl(false)}{/snippet}
{#snippet receiverAfLevel(receiver: AfReceiverKey)}{@render afLevelControl(false, receiver)}{/snippet}

{#snippet routingSplitToggle()}
  {#if rx?.routingSplit.availability.structural}
    <div class="rx-audio-row" data-testid="rx-audio-split"
      data-observed={usable(rx.routingSplit)}>
      {#if finiteAppearance}
        {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
          seat={splitToggleSeat} renderer={finiteAppearance.toggle}
        />{/key}{/key}
      {:else}
        <button type="button" class="rx-audio-choice" data-testid="rx-audio-split-toggle"
          aria-pressed={splitBehavior.selected}
          disabled={!splitBehavior.available || splitBehavior.selected === undefined}
          onclick={() => splitBehavior.selected !== undefined
            && splitBehavior.invoke(!splitBehavior.selected)}>Stereo split</button>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet receiverAfRow(receiver: AfReceiverKey, field: RxAudioField<number>)}
  <label class="rx-audio-level" data-testid={`rx-audio-af-${receiver}`}
    data-observed={usable(field)}>
    <span class="rx-audio-name">AF {receiver.toUpperCase()}</span>
    {@render afLevelControl(true, receiver)}
    <output data-testid={`rx-audio-af-${receiver}-value`}>{afPercent(field)}</output>
  </label>
{/snippet}

{#snippet afLevelRow()}
  {#if rx?.receiverAfLevels}
    {@render receiverAfRow('main', rx.receiverAfLevels.main)}
    {@render receiverAfRow('sub', rx.receiverAfLevels.sub)}
  {:else if rx?.afLevel.availability.structural}
    <label class="rx-audio-level" data-testid="rx-audio-af"
      data-observed={usable(rx.afLevel)}>
      <span class="rx-audio-name">AF LEVEL</span>
      {@render afLevelControl(true)}
      <!-- MOR-2527: the unread AF level renders NO value text — an unlit
           slot, never a `—`; the reserved min-width keeps the row from
           shifting when the reading arrives. -->
      <output data-testid="rx-audio-af-value">{afPercent(rx.afLevel)}</output>
    </label>
  {/if}
{/snippet}

{#snippet monitorMode()}
  {#if rx}
    <div
      class="rx-audio-row" role="radiogroup" aria-label="Monitor mode"
      data-testid="rx-audio-monitor" data-monitor-mode={rx.monitorMode}
    >
      {#if finiteAppearance}
        {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
          seat={monitorSeat} renderer={finiteAppearance.choice}
        />{/key}{/key}
      {:else}
        {#each MONITOR_MODES as mode (mode)}
          {#if mode !== 'live' || liveOffered}
            <button
              type="button" role="radio" class="rx-audio-choice"
              data-testid={`rx-audio-monitor-${mode}`} data-mode={mode}
              data-live-link={mode === 'live' ? rx.liveAudio.operational : undefined}
              aria-checked={monitorBehavior.isSelected(mode)}
              disabled={!monitorBehavior.available}
              onclick={() => monitorBehavior.invoke(mode)}
            >{monitorLabel[mode]}</button>
          {/if}
        {/each}
      {/if}
    </div>

    <!-- Beside the monitor row, next to the `live` choice whose
         `data-live-link` already carries this fact machine-readably: the
         operator gets it in WORDS. Zero focusable elements, so the rx-audio
         zone's tab order is unchanged (MOR-1069/MOR-1304 mounting canon). -->
    {#if linkLost}
      <p class="rx-audio-row" data-testid="rx-audio-link">{LINK_LOST_TEXT}</p>
    {/if}
  {/if}
{/snippet}

{#snippet monitorStatus()}
  {#if rx}
    <p class="rx-audio-status" data-testid="rx-audio-monitor-status">
      {linkLost ? LINK_LOST_TEXT : monitorStatusText[rx.monitorMode]}
    </p>
  {/if}
{/snippet}

{#snippet routingFocus()}
  {#if rx?.routingFocus.availability.structural}
    <div
      class="rx-audio-row" role="radiogroup" aria-label="Audio focus"
      data-testid="rx-audio-focus" data-observed={usable(rx.routingFocus)}
    >
      {#if finiteAppearance}
        {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
          seat={focusSeat} renderer={finiteAppearance.choice}
        />{/key}{/key}
      {:else}
        {#each FOCUS_CHOICES as focus (focus)}
          <button
            type="button" role="radio" class="rx-audio-choice"
            data-testid={`rx-audio-focus-${focus}`}
            aria-checked={focusBehavior.isSelected(focus)}
            disabled={!focusBehavior.available}
            onclick={() => focusBehavior.invoke(focus)}
          >{focus}</button>
        {/each}
        <output data-testid="rx-audio-focus-value">{unlitTextOf(rx.routingFocus)}</output>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet channelGain(channel: 'main' | 'sub', hardware: boolean)}
  {#if rx?.routingFocus.availability.structural}
    {@const value = channel === 'main' ? routingGains?.main : routingGains?.sub}
    <label class="rx-audio-level rx-audio-gain" data-testid={`rx-audio-${channel}-gain`}
      data-observed={rx.routingFocus.availability.operational && value !== undefined}>
      <span class="rx-audio-name">{channel.toUpperCase()}</span>
      <ValueControl
        {...feedbackIntegratedControl}
        binding={channelGainBindings[channel]}
        label={`${channel.toUpperCase()} gain in decibels`}
        renderer="hbar" showLabel={false} showValue={false} compact={true}
        variant={hardware ? 'hardware-illuminated' : 'modern'}
        accentColor={hardware ? 'var(--v2-accent-cyan-alt)' : 'var(--v2-accent-cyan)'}
      />
      <output data-testid={`rx-audio-${channel}-gain-value`}
        >{value === undefined ? '' : `${value} dB`}</output>
    </label>
  {/if}
{/snippet}
{#snippet mainGain(hardware = true)}{@render channelGain('main', hardware)}{/snippet}
{#snippet subGain(hardware = true)}{@render channelGain('sub', hardware)}{/snippet}

{#snippet routingSplit()}
  {#if rx?.routingSplit.availability.structural}
    <div
      class="rx-audio-row" role="radiogroup" aria-label="Stereo split"
      data-testid="rx-audio-split" data-observed={usable(rx.routingSplit)}
    >
      {#if finiteAppearance}
        {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
          seat={splitSeat} renderer={finiteAppearance.choice}
        />{/key}{/key}
      {:else}
        {#each SPLIT_CHOICES as [value, label] (label)}
          <button
            type="button" role="radio" class="rx-audio-choice"
            data-testid={`rx-audio-split-${label}`}
            aria-checked={splitBehavior.isSelected(value)}
            disabled={!splitBehavior.available}
            onclick={() => splitBehavior.invoke(value)}
          >split {label}</button>
        {/each}
        <!-- Round-2 ruling: the split slot NEVER carries text — the lit key
             above already states on/off, and the boolean would only echo as
             raw `true`/`false`. Kept mounted to reserve the row's shape. -->
        <output data-testid="rx-audio-split-value"></output>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet modInputSource()}
  {#if rx?.modInputSource.availability.structural}
    <p
      class="rx-audio-row" data-testid="rx-audio-mod-input"
      data-readiness={rx.modInputReadiness.status}
      data-observed={usable(rx.modInputSource)}
    >
      {#if finiteAppearance}
        {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
          seat={modInputSeat} renderer={finiteAppearance.choice}
        />{/key}{/key}
      {:else}
        <label class="rx-audio-mod-selector">
          <span>{t('core.modePanel.modInputLabel')}</span>
          <select
            data-testid="rx-audio-mod-select"
            aria-label={t('core.modePanel.modInputAria')}
            value={modInputValue}
            disabled={!modInputBehavior.available}
            onchange={(event) => changeModInput(event.currentTarget)}
          >
            {#each modInputChoices as option (option.value)}
              <option value={String(option.value)}>{option.label}</option>
            {/each}
          </select>
        </label>
        <!-- MOR-2527 owner rule: the readout label stays the constant `MOD`;
             the source value lives in its own span, empty while unread —
             its width reserved for the widest label the select offers, so
             neither it nor the readiness span after it moves when the
             reading arrives. A known-but-unrecognized source shows its true
             raw code in that span, never a fabricated choice name. -->
        <span data-testid="rx-audio-mod-source">MOD</span>
        <span
          data-testid="rx-audio-mod-source-value" class="rx-audio-mod-source-value"
          style={`--rx-audio-mod-source-width: ${modSourceWidthCh}ch`}
        >{modSourceValue}</span>
        <span data-testid="rx-audio-mod-readiness"
        >{READINESS_LABEL[rx.modInputReadiness.status]}</span>
      {/if}
    </p>
  {/if}
{/snippet}

{#snippet setModInputLan()}
  {#if rx?.modInputReadiness.status === 'mismatch'
    && modInputChoices.some(option => option.value === LAN_MOD_INPUT_SOURCE)}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
        seat={setLanSeat} renderer={finiteAppearance.action}
      />{/key}{/key}
    {:else}
      <!-- The one-click remedy, same command path as ModInputTxWarning's
           "Set LAN" (rule: a mismatch must never be a dead end). -->
      <button
        type="button" data-testid="rx-audio-mod-set-lan"
        disabled={!setLanBehavior.available} onclick={() => setLanBehavior.invoke()}
      >Set LAN</button>
    {/if}
  {/if}
{/snippet}

{@render children({
  afLevel, receiverAfLevel, afLevelRow, monitorMode, monitorStatus, routingFocus, routingSplit,
  routingSplitToggle, mainGain, subGain, modInputSource, setModInputLan,
})}

<style>
  .rx-audio-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  /* MOR-2527: the focus and split value slots keep their width while the
     reading is unknown — the row must not shift when the value arrives.
     4ch covers every value the focus slot renders: main/sub/both (all
     ≤ 4 characters); the split slot never carries text at all. */
  .rx-audio-row > output { min-width: 4ch; }
  .rx-audio-level { display: flex; align-items: baseline; gap: 0.5rem; }
  /* Same reservation for the AF readout ("42%".."100%" — 4ch at most). */
  .rx-audio-level > output { min-width: 4ch; }
  .rx-audio-level :global(.vc-hbar) { flex: 1 1 auto; min-width: 0; }
  .rx-audio-gain > output {
    min-width: 7ch;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .rx-audio-name { min-width: 7ch; }
  .rx-audio-status { margin: 0; color: var(--v2-text-dim, #8ca0b8); font-size: 10px; }
  .rx-audio-mod-selector { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; max-width: 100%; }
  .rx-audio-mod-selector select { min-width: 0; max-width: 100%; }
  /* MOR-2527: the MOD value span is a flex item of `.rx-audio-row`, so its
     min-width applies directly — the custom property is computed from the
     widest label the select offers, and the reservation keeps both this span
     and the readiness span after it from moving when the reading arrives. */
  .rx-audio-mod-source-value { min-width: var(--rx-audio-mod-source-width, 0ch); }
  .rx-audio-choice[aria-checked='true'] { font-weight: 700; }
  /* Second channel beside `data-observed`, never the only one. MOR-2527: an
     unobserved slot carries NO value text, so italics mark it without
     inventing a placeholder; both survive forced-colors. */
  [data-observed='false'] { font-style: italic; }
  button:disabled, select:disabled { cursor: not-allowed; }
</style>
