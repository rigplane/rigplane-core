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
    type ContinuousScalarInput,
    type ContinuousScalarPolicy,
    type ScalarDomain,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import type { AudioFocus, MonitorMode, RxAudioField } from './radio-view-model';
  import {
    FOCUS_CHOICES, LINK_LOST_TEXT, MONITOR_MODES, READINESS_LABEL, SPLIT_CHOICES, UNKNOWN_TEXT,
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
  /** Honest text: an unread fact reads as unknown, never as a default. */
  const textOf = (f: RxAudioField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;

  interface ExistingProps {
    presentation: RxAudioInstrumentPresentation;
    subscribeControlAuthority: SubscribeRxAudioAuthority;
    onAfLevelChange?: (value: number) => void;
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
    presentation, subscribeControlAuthority, onAfLevelChange,
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
        && currentAuthority?.muted === false
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
  const finiteSeats = [
    monitorSeat, focusSeat, splitSeat, splitToggleSeat, modInputSeat, setLanSeat,
  ] as const;
  onDestroy(() => {
    try { stop?.(); } finally {
      afLevelBinding.destroy();
      for (const seat of finiteSeats) seat.destroy();
    }
  });
</script>

{#snippet afLevel()}
  <ValueControl
    {...feedbackIntegratedControl}
    binding={afLevelBinding} label="AF" renderer="hbar"
    showLabel={false} showValue={false} compact={true}
  />
{/snippet}

{#snippet routingSplitToggle()}
  {#if rx?.routingSplit.availability.structural}
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
  {/if}
{/snippet}

{#snippet afLevelRow()}
  {#if rx?.afLevel.availability.structural}
    <label class="rx-audio-level" data-testid="rx-audio-af"
      data-observed={usable(rx.afLevel)}>
      <span class="rx-audio-name">AF LEVEL</span>
      {@render afLevel()}
      <output data-testid="rx-audio-af-value">{rx.afLevel.reading.status === 'known'
        ? `${Math.round(rx.afLevel.reading.value * 100)}%` : UNKNOWN_TEXT}</output>
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
        <output data-testid="rx-audio-focus-value">{textOf(rx.routingFocus)}</output>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet channelGain(channel: 'main' | 'sub')}
  {#if rx?.routingFocus.availability.structural}
    {@const value = channel === 'main' ? routingGains?.main : routingGains?.sub}
    <label class="rx-audio-gain" data-testid={`rx-audio-${channel}-gain`}>
      <span>{channel.toUpperCase()}</span>
      <input type="range" min="-60" max="12" step="1" value={value ?? 0}
        aria-label={`${channel.toUpperCase()} gain in decibels`}
        disabled={value === undefined || !rx.routingFocus.availability.operational
          || onChannelGainChange === undefined}
        oninput={(event) => onChannelGainChange?.(channel, event.currentTarget.valueAsNumber)} />
      <output>{value === undefined ? UNKNOWN_TEXT : `${value} dB`}</output>
    </label>
  {/if}
{/snippet}
{#snippet mainGain()}{@render channelGain('main')}{/snippet}
{#snippet subGain()}{@render channelGain('sub')}{/snippet}

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
        <output data-testid="rx-audio-split-value">{textOf(rx.routingSplit)}</output>
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
            {#if modInputValue === ''}
              <option value="" disabled>{UNKNOWN_TEXT}</option>
            {/if}
            {#each modInputChoices as option (option.value)}
              <option value={String(option.value)}>{option.label}</option>
            {/each}
          </select>
        </label>
        <span data-testid="rx-audio-mod-source">MOD: {rx.modInputSource.reading.status === 'known'
          ? modInputChoices.find(option => option.value === modInputReadingValue)?.label ?? UNKNOWN_TEXT
          : UNKNOWN_TEXT}</span>
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
  afLevel, afLevelRow, monitorMode, monitorStatus, routingFocus, routingSplit,
  routingSplitToggle, mainGain, subGain, modInputSource, setModInputLan,
})}

<style>
  .rx-audio-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .rx-audio-level, .rx-audio-gain { display: flex; align-items: baseline; gap: 0.5rem; }
  .rx-audio-level :global(.value-control), .rx-audio-gain input { flex: 1 1 auto; min-width: 0; }
  .rx-audio-name { min-width: 7ch; }
  .rx-audio-status { margin: 0; color: var(--v2-text-dim, #8ca0b8); font-size: 10px; }
  .rx-audio-mod-selector { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; max-width: 100%; }
  .rx-audio-mod-selector select { min-width: 0; max-width: 100%; }
  .rx-audio-choice[aria-checked='true'] { font-weight: 700; }
  /* Second channel beside `data-observed`, never the only one: the unknown
     text itself is the primary one and survives forced-colors. */
  [data-observed='false'] { font-style: italic; }
  button:disabled, select:disabled { cursor: not-allowed; }
</style>
