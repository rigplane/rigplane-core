<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import { HardwareButton } from '$lib/Button';
  import { t } from '$lib/i18n';
  import { buildAgcOptions } from '../components-v2/panels/agc-utils';
  import { bindChoiceInstrument, bindToggleInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import { createChoiceRendererSeat, createToggleRendererSeat,
    type FiniteControlAppearance, type FiniteRendererContext } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import { DSP_NOTCH_MODES, DSP_TOGGLES, type DspFiniteChoiceValue,
    type DspFiniteHandles, type DspNotchMode, type DspSettingsPanel,
    type DspToggleField } from './dsp-instruments';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel | null;
    agcLabels?: Record<string, string>;
    pendingNb?: boolean | null;
    pendingNr?: boolean | null;
    onToggle?: (field: DspToggleField, next: boolean) => void;
    onNotchModeChange?: (mode: DspNotchMode) => void;
    onAgcModeChange?: (mode: number) => void;
    settingsPanel?: DspSettingsPanel | null;
    onOpenSettings?: (panel: DspSettingsPanel | null) => void;
    children: Snippet<[DspFiniteHandles]>;
  }
  type RendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<DspFiniteChoiceValue>; rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & RendererSelection;

  let {
    view, agcLabels = {}, pendingNb = null, pendingNr = null,
    onToggle, onNotchModeChange, onAgcModeChange, settingsPanel = null, onOpenSettings,
    finiteAppearance, rendererContext, children,
  }: Props = $props();

  let dsp = $derived(view?.dsp);
  let agcOptions = $derived(dsp ? buildAgcOptions([...dsp.agcModes], agcLabels) : []);
  const pendingId = $props.id();
  const pendingOf = (field: DspToggleField): boolean | null =>
    field === 'nrActive' ? pendingNr : pendingNb;
  const usable = (field: DspToggleField): boolean => {
    const current = dsp?.[field];
    return current !== undefined && current.availability.structural
      && current.availability.operational && current.reading.status === 'known';
  };
  const toggleBehavior = (field: DspToggleField) => bindToggleInstrument(() => ({
    field: dsp?.[field], invoke: (next) => onToggle?.(field, next),
  }));
  const nrBehavior = toggleBehavior('nrActive');
  const nbBehavior = toggleBehavior('nbActive');
  const notchBehavior = bindChoiceInstrument(() => ({
    field: dsp?.notchMode, choices: DSP_NOTCH_MODES,
    invoke: (mode) => onNotchModeChange?.(mode),
  }));
  const agcBehavior = bindChoiceInstrument<number>(() => ({
    field: dsp?.agcMode, choices: agcOptions.map(option => option.value),
    invoke: (mode) => onAgcModeChange?.(mode),
  }));

  const requested = (field: DspToggleField) => {
    const target = pendingOf(field);
    return target === null ? undefined : { kind: 'requested-target' as const, target };
  };
  const nrSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: dsp?.nrActive, label: 'NR',
    requested: requested('nrActive'), invoke: (next) => onToggle?.('nrActive', next),
  }));
  const nbSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: dsp?.nbActive, label: 'NB',
    requested: requested('nbActive'), invoke: (next) => onToggle?.('nbActive', next),
  }));
  const notchSeat = createChoiceRendererSeat<DspFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, field: dsp?.notchMode, label: 'Notch mode',
    options: DSP_NOTCH_MODES.map(value => ({ value, label: value })),
    invoke: (mode) => onNotchModeChange?.(mode as DspNotchMode),
  }));
  const notchToggleField = (mode: 'manual' | 'auto') => {
    const field = dsp?.notchMode;
    return field === undefined ? undefined : {
      availability: field.availability,
      reading: field.reading.status === 'known'
        ? { status: 'known' as const, value: field.reading.value === mode }
        : { status: 'unknown' as const },
    };
  };
  const manualNotchSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: notchToggleField('manual'), label: 'NOTCH',
    invoke: (next) => onNotchModeChange?.(next ? 'manual' : 'off'),
  }));
  const autoNotchSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: notchToggleField('auto'), label: 'A-NOTCH',
    invoke: (next) => onNotchModeChange?.(next ? 'auto' : 'off'),
  }));
  let holdTimer: ReturnType<typeof setTimeout> | null = null;
  let heldPanel: DspSettingsPanel | null = null;
  const toggleSettings = (panel: DspSettingsPanel): void =>
    onOpenSettings?.(settingsPanel === panel ? null : panel);
  function startSettingsHold(panel: DspSettingsPanel): void {
    if (holdTimer !== null) clearTimeout(holdTimer);
    heldPanel = null;
    holdTimer = setTimeout(() => {
      holdTimer = null; heldPanel = panel; toggleSettings(panel);
    }, 500);
  }
  function endSettingsHold(): void {
    if (holdTimer !== null) clearTimeout(holdTimer);
    holdTimer = null;
  }
  function compactToggle(field: DspToggleField): void {
    if (heldPanel !== null) { heldPanel = null; return; }
    const behavior = field === 'nrActive' ? nrBehavior : nbBehavior;
    behavior.invoke();
  }
  function suppressHeldClick(event: MouseEvent): void {
    if (heldPanel === null) return;
    event.preventDefault(); event.stopPropagation(); heldPanel = null;
  }
  function settingsHold(node: HTMLElement, panel: DspSettingsPanel) {
    const pointerDown = () => startSettingsHold(panel);
    const keyDown = (event: KeyboardEvent) => {
      if (event.key === ' ' && !event.repeat && holdTimer === null) startSettingsHold(panel);
    };
    const keyUp = (event: KeyboardEvent) => { if (event.key === ' ') endSettingsHold(); };
    node.addEventListener('pointerdown', pointerDown);
    node.addEventListener('pointerup', endSettingsHold);
    node.addEventListener('pointercancel', endSettingsHold);
    node.addEventListener('pointerleave', endSettingsHold);
    node.addEventListener('keydown', keyDown);
    node.addEventListener('keyup', keyUp);
    node.addEventListener('click', suppressHeldClick, { capture: true });
    return {
      destroy() {
        node.removeEventListener('pointerdown', pointerDown);
        node.removeEventListener('pointerup', endSettingsHold);
        node.removeEventListener('pointercancel', endSettingsHold);
        node.removeEventListener('pointerleave', endSettingsHold);
        node.removeEventListener('keydown', keyDown);
        node.removeEventListener('keyup', keyUp);
        node.removeEventListener('click', suppressHeldClick, { capture: true });
      },
    };
  }
  function compactNotch(mode: 'manual' | 'auto'): void {
    if (heldPanel !== null) { heldPanel = null; return; }
    notchBehavior.invoke(notchBehavior.isSelected(mode) ? 'off' : mode);
  }
  const agcSeat = createChoiceRendererSeat<DspFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, field: dsp?.agcMode, label: 'AGC mode',
    options: agcOptions.map(option => ({ value: option.value, label: option.label })),
    invoke: (mode) => onAgcModeChange?.(mode as number),
  }));
  onDestroy(() => {
    endSettingsHold();
    nrSeat.destroy(); nbSeat.destroy(); notchSeat.destroy(); manualNotchSeat.destroy();
    autoNotchSeat.destroy(); agcSeat.destroy();
  });
</script>

{#snippet toggle(field: DspToggleField, label: string)}
  {@const current = dsp?.[field]}
  {#if current?.availability.structural}
    {@const pending = pendingOf(field)}
    {@const behavior = field === 'nrActive' ? nrBehavior : nbBehavior}
    {@const id = `${pendingId}-${field}`}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
        seat={field === 'nrActive' ? nrSeat : nbSeat} renderer={finiteAppearance.toggle}
      />{/key}{/key}
    {:else}
      <button
        type="button" class="dsp-toggle" data-testid={`dsp-${field}`} data-field={field}
        data-disabled-reason={usable(field) ? undefined : 'field-not-observed'}
        aria-pressed={behavior.confirmed}
        data-pending-status={pending !== null ? 'pending' : 'confirmed'}
        aria-describedby={pending !== null ? id : undefined}
        disabled={!behavior.available} onclick={() => behavior.invoke()}
      >{label}: {current.reading.status === 'known' ? (current.reading.value ? 'on' : 'off') : '?'}</button>
      {#if pending !== null}<span {id} class="sr-only">{t('core.dsp.pendingAnnouncement')}</span>{/if}
    {/if}
  {/if}
{/snippet}
{#snippet compactDspButton(kind: 'nb' | 'nr' | 'notch', label: string)}
  {@const field = kind === 'nb' ? 'nbActive' : 'nrActive'}
  {@const current = kind === 'notch' ? dsp?.notchMode : dsp?.[field]}
  {#if current?.availability.structural}
    {@const available = kind === 'notch' ? notchBehavior.available
      : (kind === 'nr' ? nrBehavior.available : nbBehavior.available)}
    {@const active = kind === 'notch'
      ? notchBehavior.isSelected('manual')
      : current.reading.status === 'known' && current.reading.value === true}
    <div class="compact-dsp-button" role="group" aria-label={`${label} control`}
      data-expanded={settingsPanel === kind}
      use:settingsHold={kind}>
      {#if finiteAppearance}
        {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
          seat={kind === 'notch' ? manualNotchSeat : (kind === 'nr' ? nrSeat : nbSeat)}
          renderer={finiteAppearance.toggle}
        />{/key}{/key}
      {:else}
        <button type="button" class="dsp-toggle" aria-pressed={active} disabled={!available}
          title={`${label} — click to toggle; hold for settings`}
          onclick={() => kind === 'notch' ? compactNotch('manual') : compactToggle(field)}>{label}</button>
      {/if}
      <span class="dsp-settings-chevron">
        <HardwareButton compact={true} indicator="edge-left" active={settingsPanel === kind}
          color="cyan" title={`${settingsPanel === kind ? 'Close' : 'Open'} ${label} settings`}
          ariaLabel={`${label} settings`} ariaExpanded={settingsPanel === kind}
          ariaControls={`dsp-${kind}-settings`}
          onclick={() => toggleSettings(kind)}>{settingsPanel === kind ? '▴' : '▾'}</HardwareButton>
      </span>
    </div>
  {/if}
{/snippet}
{#snippet compactNb()}{@render compactDspButton('nb', 'NB')}{/snippet}
{#snippet compactNr()}{@render compactDspButton('nr', 'NR')}{/snippet}
{#snippet compactManualNotch()}{@render compactDspButton('notch', 'NOTCH')}{/snippet}
{#snippet compactAutoNotch()}
  {#if dsp?.notchMode.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
        seat={autoNotchSeat} renderer={finiteAppearance.toggle}
      />{/key}{/key}
    {:else}
      <button type="button" class="dsp-toggle"
        aria-pressed={notchBehavior.isSelected('auto')} disabled={!notchBehavior.available}
        onclick={() => compactNotch('auto')}>A-NOTCH</button>
    {/if}
  {/if}
{/snippet}
{#snippet notchToggle(mode: 'manual' | 'auto', label: string)}
  {#if dsp?.notchMode.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
        seat={mode === 'manual' ? manualNotchSeat : autoNotchSeat}
        renderer={finiteAppearance.toggle}
      />{/key}{/key}
    {:else}
      <button type="button" class="dsp-toggle" data-testid={`dsp-${mode}Notch`}
        aria-pressed={notchBehavior.selected === undefined ? undefined : notchBehavior.isSelected(mode)}
        disabled={!notchBehavior.available}
        onclick={() => notchBehavior.invoke(notchBehavior.isSelected(mode) ? 'off' : mode)}>{label}</button>
    {/if}
  {/if}
{/snippet}
{#snippet manualNotch()}{@render notchToggle('manual', 'NOTCH')}{/snippet}
{#snippet autoNotch()}{@render notchToggle('auto', 'A-NOTCH')}{/snippet}

{#snippet nrActive()}{@render toggle('nrActive', DSP_TOGGLES[0][1])}{/snippet}
{#snippet nbActive()}{@render toggle('nbActive', DSP_TOGGLES[1][1])}{/snippet}
{#snippet notchMode()}
  {#if dsp?.notchMode.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
        seat={notchSeat} renderer={finiteAppearance.choice}
      />{/key}{/key}
    {:else}
      <div class="dsp-row" data-testid="dsp-notchMode"
        data-disabled-reason={notchBehavior.available ? undefined : 'field-not-observed'}>
        {#each DSP_NOTCH_MODES as mode (mode)}
          <button type="button" class="dsp-choice" data-testid={`dsp-notchMode-${mode}`}
            aria-pressed={notchBehavior.selected === undefined ? undefined : notchBehavior.isSelected(mode)}
            disabled={!notchBehavior.available} onclick={() => notchBehavior.invoke(mode)}>{mode}</button>
        {/each}
      </div>
    {/if}
  {/if}
{/snippet}
{#snippet agcMode()}
  {#if dsp?.agcMode.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
        seat={agcSeat} renderer={finiteAppearance.choice}
      />{/key}{/key}
    {:else}
      <div class="dsp-row" data-testid="dsp-agcMode"
        data-disabled-reason={agcBehavior.available ? undefined : 'field-not-observed'}>
        {#each agcOptions as option (option.value)}
          <button type="button" class="dsp-choice" data-testid={`dsp-agcMode-${option.value}`}
            aria-pressed={agcBehavior.selected === undefined ? undefined : agcBehavior.isSelected(option.value)}
            disabled={!agcBehavior.available} onclick={() => agcBehavior.invoke(option.value)}>{option.label}</button>
        {/each}
      </div>
    {/if}
  {/if}
{/snippet}

{@render children({
  nrActive, nbActive, notchMode, manualNotch, autoNotch,
  compactNb, compactNr, compactManualNotch, compactAutoNotch, agcMode,
})}

<style>
  .dsp-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .compact-dsp-button {
    display: grid; grid-template-columns: minmax(0, 1fr) 24px; gap: 2px; min-width: 0;
  }
  .compact-dsp-button > :global(button) { min-width: 0; }
  .dsp-settings-chevron { display: flex; min-width: 0; }
  .dsp-settings-chevron :global(button) { flex: 1 1 auto; min-width: 0; padding-inline: 2px; }
  .dsp-toggle[aria-pressed='true'], .dsp-choice[aria-pressed='true'] { font-weight: 700; }
  .dsp-toggle:disabled, .dsp-choice:disabled { cursor: not-allowed; }
  .dsp-toggle[data-pending-status='pending'] { font-style: italic; opacity: 0.75; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
