<script lang="ts">
  import '../controls/control-button.css';
  import { HardwareButton } from '$lib/Button';
  import { ValueControl, rawToPercentDisplay } from '../controls/value-control';
  import { getLocale } from '$lib/i18n';
  import {
    projectControlFeedbackPresentation,
    type PresentationPhase,
  } from '../../primitives/control-feedback/control-feedback-presentation';

  import { isApfActive } from './cw-panel-logic';
  import {
    deriveCwProps,
    getBreakInDelayControlFeedback,
    getCwHandlers,
  } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getCwHandlers();
  let p = $derived(deriveCwProps());

  let cwPitch = $derived(p.cwPitch ?? 600);
  let keySpeed = $derived(p.keySpeed ?? 12);
  let breakIn = $derived(p.breakIn ?? 0);
  let apfMode = $derived(p.apfMode ?? 0);
  let twinPeak = $derived(p.twinPeak ?? false);
  let currentMode = $derived(p.currentMode ?? 'CW');
  let apfDisabled = $derived(p.apfDisabled ?? false);
  let tpfDisabled = $derived(p.tpfDisabled ?? false);
  const onCwPitchChange = handlers.onCwPitchChange;
  const onKeySpeedChange = handlers.onKeySpeedChange;
  const onBreakInToggle = handlers.onBreakInToggle;
  const onBreakInModeChange = handlers.onBreakInModeChange;
  const onBreakInDelayChange = handlers.onBreakInDelayChange;
  const onApfChange = handlers.onApfChange;
  const onTwinPeakToggle = handlers.onTwinPeakToggle;
  const onAutoTune = handlers.onAutoTune;
  let showCw = $derived(p.hasCw);
  let showBreakIn = $derived(p.hasBreakIn);
  let showApf = $derived(p.hasApf);
  let showTwinPeak = $derived(p.hasTwinPeak);
  let showAutoTune = $derived(p.autoTuneAvailable);
  let apfActive = $derived(isApfActive(apfMode));
  let breakInDelayFeedback = $derived(getBreakInDelayControlFeedback());
  let breakInDelayAvailable = $derived(
    breakInDelayFeedback.availability === 'available'
      && breakInDelayFeedback.confirmed !== null,
  );
  let breakInDelayTruth = $derived(
    breakInDelayFeedback.busy && breakInDelayFeedback.target !== null
      ? breakInDelayFeedback.target
      : breakInDelayFeedback.confirmed,
  );
  let breakInDelayDraft = $state(0);
  let breakInDelayEditing = $state(false);
  let breakInDelayCancelled = $state(false);
  let breakInDelayAnnouncement = $state<string | null>(null);
  const announcedBreakInDelayTransitions = new Set<string>();
  const feedbackIntegratedRange = { 'feedback-policy': 'feedback-integrated' } as const;
  let breakInDelayPresentation = $derived(projectControlFeedbackPresentation(
    breakInDelayFeedback,
    { announcedTransitionIds: [] },
    rawToPercentDisplay,
  ));

  $effect(() => {
    const truth = breakInDelayTruth;
    if (!breakInDelayEditing && truth !== null) breakInDelayDraft = truth;
  });
  $effect(() => {
    const transition = breakInDelayPresentation.politeAnnouncement;
    if (transition && !announcedBreakInDelayTransitions.has(transition.transitionId)) {
      announcedBreakInDelayTransitions.add(transition.transitionId);
      breakInDelayAnnouncement = breakInDelayMessage(
        transition.phase,
        breakInDelayFeedback.requestedTarget,
        breakInDelayFeedback.confirmed,
      );
    }
  });

  const delayText = (value: number | null): string =>
    value === null ? '—' : rawToPercentDisplay(value);
  const delayLabel = (): string =>
    getLocale() === 'ru-RU' ? 'Задержка break-in' : 'Break-in Delay';
  function breakInDelayMessage(
    phase: PresentationPhase,
    target: number | null,
    confirmed: number | null,
  ): string {
    const ru = getLocale() === 'ru-RU';
    const requested = delayText(target);
    const canonical = delayText(confirmed);
    const copy: Record<PresentationPhase, string> = ru ? {
      unavailable: 'Управление недоступно', idle: `Подтверждено: ${canonical}`,
      submitted: `Запрошено ${requested}; подтверждено ${canonical}`,
      queued: `В очереди ${requested}; подтверждено ${canonical}`,
      dispatched: `Отправлено ${requested}; подтверждено ${canonical}`,
      'awaiting-confirmation': `Ожидание ${requested}; подтверждено ${canonical}`,
      confirmed: `Радио подтвердило ${canonical}`, failed: `Ошибка ${requested}; осталось ${canonical}`,
      'timed-out': `Время ожидания ${requested} истекло; осталось ${canonical}`,
      cancelled: `Запрос ${requested} отменён; осталось ${canonical}`,
      superseded: `Запрос ${requested} заменён; осталось ${canonical}`,
    } : {
      unavailable: 'Control unavailable', idle: `Confirmed: ${canonical}`,
      submitted: `Requested ${requested}; last confirmed ${canonical}`,
      queued: `Queued ${requested}; last confirmed ${canonical}`,
      dispatched: `Dispatched ${requested}; last confirmed ${canonical}`,
      'awaiting-confirmation': `Awaiting ${requested}; last confirmed ${canonical}`,
      confirmed: `Radio confirmed ${canonical}`, failed: `Failed ${requested}; confirmed remains ${canonical}`,
      'timed-out': `Timed out ${requested}; confirmed remains ${canonical}`,
      cancelled: `Cancelled ${requested}; confirmed remains ${canonical}`,
      superseded: `Superseded ${requested}; confirmed remains ${canonical}`,
    };
    return copy[phase];
  }
  let breakInDelayValueText = $derived(
    !breakInDelayAvailable
      ? breakInDelayMessage('unavailable', null, null)
      : breakInDelayEditing
        ? `${getLocale() === 'ru-RU' ? 'Черновик' : 'Draft'} ${delayText(breakInDelayDraft)}; ${getLocale() === 'ru-RU' ? 'подтверждено' : 'last confirmed'} ${delayText(breakInDelayFeedback.confirmed)}`
        : breakInDelayMessage(
          breakInDelayFeedback.phase,
          breakInDelayFeedback.target ?? breakInDelayFeedback.requestedTarget,
          breakInDelayFeedback.confirmed,
        ),
  );

  function restoreBreakInDelay(): void {
    breakInDelayEditing = false;
    breakInDelayDraft = breakInDelayTruth ?? 0;
  }
  function updateBreakInDelayDraft(event: Event): void {
    if (!breakInDelayAvailable) return;
    breakInDelayCancelled = false;
    breakInDelayEditing = true;
    breakInDelayDraft = Math.min(255, Math.max(0, Math.round(
      (event.currentTarget as HTMLInputElement).valueAsNumber,
    )));
  }
  function commitBreakInDelay(event: Event): void {
    if (breakInDelayCancelled) {
      breakInDelayCancelled = false;
      restoreBreakInDelay();
      (event.currentTarget as HTMLInputElement).value = String(breakInDelayDraft);
      return;
    }
    const candidate = (event.currentTarget as HTMLInputElement).valueAsNumber;
    if (breakInDelayAvailable && Number.isFinite(candidate)) {
      const bounded = Math.min(255, Math.max(0, Math.round(candidate)));
      breakInDelayDraft = bounded;
      onBreakInDelayChange(bounded);
    }
    breakInDelayEditing = false;
  }
  function cancelBreakInDelay(event?: Event): void {
    breakInDelayCancelled = true;
    restoreBreakInDelay();
    if (event?.currentTarget instanceof HTMLInputElement) {
      event.currentTarget.value = String(breakInDelayDraft);
    }
  }
  function handleBreakInDelayKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    cancelBreakInDelay(event);
  }

  // MOR-1409 A12 (coordinator adjudication, Core #2317, comment 5246487510):
  // `cwPitch`/`keySpeed` are `?? 600`/`?? 12` guarded above, but `??` does
  // not catch `NaN` (only `null`/`undefined`) — a connected receiver that
  // has never reported these optional fields still passes through as
  // `NaN`, and neither `ValueControl` call below has a `displayFn`, so the
  // default `${value}${unit}` renders the literal "NaN Hz"/"NaN WPM".
  // Guard locally, same shape as FilterPanel.svelte's `formatWidthDisplay`.
  // Preserves the exact prior finite-value format (the renderers' own
  // unguarded default is `${localValue}${unit ? ' ' + unit : ''}`,
  // a non-breaking space before the unit) — the guard only changes
  // behavior for the non-finite case, per the grant's "no behavior/logic
  // changes beyond the guards" restriction.
  function formatCwPitchDisplay(hz: number): string {
    return Number.isFinite(hz) ? `${hz} Hz` : '--- Hz';
  }
  function formatKeySpeedDisplay(wpm: number): string {
    return Number.isFinite(wpm) ? `${wpm} WPM` : '--- WPM';
  }
</script>

{#if showCw}
  <div class="panel-body">
    <div class="cw-mode-line">
      <span class="cw-mode-label">RX mode</span>
      <span class="cw-mode-value">{currentMode}</span>
    </div>

    <ValueControl
      label="CW Pitch"
      value={cwPitch}
      min={300}
      max={900}
      step={5}
      unit="Hz"
      renderer="hbar"
      accentColor="var(--v2-accent-cyan)"
      onChange={onCwPitchChange}
      variant="hardware-illuminated"
      displayFn={formatCwPitchDisplay}
    />

    <ValueControl
      label="Key Speed"
      value={keySpeed}
      min={6}
      max={48}
      step={1}
      unit="WPM"
      renderer="discrete"
      tickStyle="notch"
      accentColor="var(--v2-accent-orange)"
      onChange={onKeySpeedChange}
      variant="hardware-illuminated"
      displayFn={formatKeySpeedDisplay}
    />

    <div class="toggle-row">
      {#if showBreakIn}
        <HardwareButton indicator="edge-left" active={breakIn === 1} color="cyan" onclick={() => onBreakInModeChange(breakIn === 1 ? 0 : 1)}>
          SEMI
        </HardwareButton>
        <HardwareButton indicator="edge-left" active={breakIn === 2} color="orange" onclick={() => onBreakInModeChange(breakIn === 2 ? 0 : 2)}>
          FULL
        </HardwareButton>
      {/if}
      {#if showApf}
        <HardwareButton indicator="edge-left" active={apfActive} disabled={apfDisabled} title={apfDisabled ? 'APF only works in CW/CW-R' : null} color="cyan" onclick={() => onApfChange(apfMode > 0 ? 0 : 1)}>
          APF
        </HardwareButton>
      {/if}
      {#if showTwinPeak}
        <HardwareButton indicator="edge-left" active={twinPeak} disabled={tpfDisabled} title={tpfDisabled ? 'Twin Peak Filter only works in RTTY/RTTY-R' : null} color="cyan" onclick={() => onTwinPeakToggle()}>
          TPF
        </HardwareButton>
      {/if}
    </div>

    {#if showBreakIn && breakIn === 1}
      <label class="break-in-delay-control" data-testid="cw-break-in-delay-control">
        <span class="break-in-delay-header">
          <span>{delayLabel()}</span>
          <output data-testid="cw-break-in-delay-value">
            {breakInDelayAvailable ? delayText(breakInDelayDraft) : '—'}
          </output>
        </span>
        <input
          data-testid="cw-break-in-delay"
          type="range" min="0" max="255" step="1" value={breakInDelayDraft}
          {...feedbackIntegratedRange}
          disabled={!breakInDelayAvailable}
          aria-label={delayLabel()}
          aria-valuenow={breakInDelayAvailable ? breakInDelayDraft : undefined}
          aria-valuetext={breakInDelayValueText}
          aria-busy={breakInDelayPresentation.attributes['aria-busy']}
          data-command-phase={breakInDelayPresentation.attributes['data-command-phase']}
          oninput={updateBreakInDelayDraft}
          onchange={commitBreakInDelay}
          onpointercancel={cancelBreakInDelay}
          onkeydown={handleBreakInDelayKeydown}
        />
        <span class="break-in-delay-phase" aria-hidden="true">
          ● {breakInDelayFeedback.phase}
        </span>
        {#if breakInDelayAnnouncement}
          <span
            class="sr-only" role="status" aria-live="polite" aria-atomic="true"
            data-testid="cw-break-in-delay-live"
          >{breakInDelayAnnouncement}</span>
        {/if}
      </label>
    {/if}

    {#if showAutoTune}
      <div class="toggle-row">
        <HardwareButton indicator="edge-left" color="green" onclick={() => onAutoTune()}>
          AUTO TUNE
        </HardwareButton>
      </div>
    {/if}
  </div>
{/if}

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 8px;
  }

  .cw-mode-line {
    display: flex;
    flex-direction: row;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }

  .cw-mode-label {
    color: var(--v2-text-subdued);
    font-family: 'Roboto Mono', monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .cw-mode-value {
    color: var(--v2-text-header);
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    font-weight: 700;
  }

  .toggle-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .break-in-delay-control {
    display: grid;
    gap: 4px;
    color: var(--v2-text-label, var(--v2-text-dim));
    font: 700 10px/1.4 'Roboto Mono', monospace;
  }

  .break-in-delay-header {
    display: flex;
    justify-content: space-between;
  }

  .break-in-delay-control input { width: 100%; accent-color: var(--v2-accent-cyan); }
  .break-in-delay-phase { font-size: 9px; text-transform: uppercase; }
  .break-in-delay-phase { opacity: 0.8; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }

  @media (forced-colors: active) {
    .break-in-delay-control input { border: 1px solid CanvasText; }
    .break-in-delay-phase { forced-color-adjust: none; color: CanvasText; }
  }

  @media (prefers-reduced-motion: reduce) {
    .break-in-delay-control, .break-in-delay-control * { transition: none !important; }
  }

</style>
