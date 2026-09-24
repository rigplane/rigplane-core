<!--
  Semantic repeater surface (MOR-2111): OFF/TONE/TSQL, shift SIMP/−/+ (and
  ARS where `view.repeater.shiftChoices` has it) and the CTCSS tone stepper
  for ONE receiver. Presentation only: facts come from
  `view.repeater`, pending targets from `pending`, and every action leaves
  through a callback naming the receiver.
-->
<script module lang="ts">
  import type { RadioViewModel, ReceiverId, RepeaterReceiverViewModel } from './radio-view-model';

  const inBand = (rx: RepeaterReceiverViewModel): boolean =>
    rx.inRepeaterBand.reading.status === 'known' && rx.inRepeaterBand.reading.value;

  /** The receiver the panel controls (owner, 2026-09-24): the one receiver
   *  tuned to a repeater range; with both tuned to one, the selected receiver.
   *  `null` — no panel — when neither is, when both are and the selection is
   *  unread, or when that receiver draws none of the three controls. */
  export function repeaterReceiver(view: RadioViewModel | null): ReceiverId | null {
    const group = view?.repeater;
    if (!view || !group) return null;
    const main = inBand(group.main);
    const sub = inBand(group.sub);
    const receiver: ReceiverId | null = main && sub
      ? (view.activeReceiver.status === 'known' ? view.activeReceiver.receiver : null)
      : main ? 'MAIN' : sub ? 'SUB' : null;
    if (receiver === null) return null;
    const rx = receiver === 'MAIN' ? group.main : group.sub;
    return rx.toneMode.availability.structural || rx.toneFreq.availability.structural
      || rx.shift.availability.structural ? receiver : null;
  }
</script>

<script lang="ts">
  import { ControlButton } from '$lib/Button';
  import { t } from '$lib/i18n';
  import { formatToneHz, type RepeaterPending } from '$lib/radio/repeater-transitions';
  import { bindChoiceInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import type { RepeaterShift, RepeaterToneMode } from './radio-view-model';

  interface Props {
    view: RadioViewModel | null;
    pending?: Partial<Record<ReceiverId, RepeaterPending>>;
    onToneModeChange?: (receiver: ReceiverId, next: RepeaterToneMode) => void;
    onShiftChange?: (receiver: ReceiverId, shift: RepeaterShift) => void;
    onToneFreqStep?: (receiver: ReceiverId, direction: 1 | -1) => void;
  }

  let { view, pending = {}, onToneModeChange, onShiftChange, onToneFreqStep }: Props = $props();

  const TONE_MODES = [['off', 'OFF'], ['tone', 'TONE'], ['tsql', 'TSQL']] as const;
  const SHIFTS = [['simplex', 'SIMP'], ['minus', '−'], ['plus', '+'], ['ars', 'ARS']] as const;

  let receiver = $derived(repeaterReceiver(view));
  let rx = $derived(receiver === null ? undefined : view?.repeater?.[receiver === 'MAIN' ? 'main' : 'sub']);
  let rxPending = $derived(receiver === null ? undefined : pending[receiver]);
  let shifts = $derived(SHIFTS.filter(([shift]) => view?.repeater?.shiftChoices.includes(shift) === true));
  let stepAvailable = $derived(rx?.toneFreq.reading.status === 'known'
    && rx.toneMode.reading.status === 'known');
  const pendingId = $props.id();

  const toneBehavior = bindChoiceInstrument<RepeaterToneMode>(() => ({
    field: rx?.toneMode, choices: TONE_MODES.map(([mode]) => mode),
    invoke: (mode) => { if (receiver !== null) onToneModeChange?.(receiver, mode); },
  }));
  const shiftBehavior = bindChoiceInstrument<RepeaterShift>(() => ({
    field: rx?.shift, choices: shifts.map(([shift]) => shift),
    invoke: (shift) => { if (receiver !== null) onShiftChange?.(receiver, shift); },
  }));

  function step(direction: 1 | -1): void {
    if (receiver !== null && stepAvailable) onToneFreqStep?.(receiver, direction);
  }
</script>

{#if receiver !== null && rx}
  <section class="repeater-surface" data-testid="repeater-surface" data-receiver={receiver}>
    <span class="repeater-receiver" data-testid="repeater-receiver">{receiver}</span>
    {#if rx.toneMode.availability.structural}
      <div class="repeater-keys" role="radiogroup" aria-label="Tone mode" data-testid="repeater-tone-mode">
        {#each TONE_MODES as [mode, label] (mode)}
          {@const isPending = rxPending?.toneMode === mode}
          <div class="repeater-key">
            <ControlButton surface="hardware" indicatorStyle="edge-left" indicatorColor="cyan"
              active={toneBehavior.isSelected(mode)} disabled={!toneBehavior.available}
              data={{ testid: `repeater-tone-${mode}` }}
              role="radio" ariaChecked={toneBehavior.isSelected(mode)}
              armed={isPending} describedBy={isPending ? `${pendingId}-tone` : undefined}
              onclick={() => toneBehavior.invoke(mode)}>{label}</ControlButton>
          </div>
        {/each}
        {#if rxPending?.toneMode}<span id={`${pendingId}-tone`} class="sr-only">{t('core.repeater.pendingAnnouncement')}</span>{/if}
      </div>
    {/if}
    {#if rx.shift.availability.structural}
      <div class="repeater-keys" role="radiogroup" aria-label="Repeater shift" data-testid="repeater-shift">
        {#each shifts as [shift, label] (shift)}
          {@const isPending = rxPending?.shift === shift}
          <div class="repeater-key">
            <ControlButton surface="hardware" indicatorStyle="edge-left" indicatorColor="cyan"
              active={shiftBehavior.isSelected(shift)} disabled={!shiftBehavior.available}
              data={{ testid: `repeater-shift-${shift}` }}
              role="radio" ariaChecked={shiftBehavior.isSelected(shift)}
              armed={isPending} describedBy={isPending ? `${pendingId}-shift` : undefined}
              onclick={() => shiftBehavior.invoke(shift)}>{label}</ControlButton>
          </div>
        {/each}
        {#if rxPending?.shift}<span id={`${pendingId}-shift`} class="sr-only">{t('core.repeater.pendingAnnouncement')}</span>{/if}
      </div>
    {/if}
    {#if rx.toneFreq.availability.structural}
      <div class="repeater-stepper" data-testid="repeater-tone-freq">
        <span class="repeater-name">CTCSS</span>
        <button type="button" class="repeater-step-key scope-step-key" aria-label="Decrease CTCSS tone"
          data-testid="repeater-tone-freq-down" disabled={!stepAvailable} onclick={() => step(-1)}>&#8249;</button>
        <output class="repeater-step-value" data-testid="repeater-tone-freq-value"
          data-pending={rxPending?.toneFreq === true}
          aria-describedby={rxPending?.toneFreq ? `${pendingId}-freq` : undefined}
        >{rx.toneFreq.reading.status === 'known' ? formatToneHz(rx.toneFreq.reading.value) : ''}</output>
        <button type="button" class="repeater-step-key scope-step-key" aria-label="Increase CTCSS tone"
          data-testid="repeater-tone-freq-up" disabled={!stepAvailable} onclick={() => step(1)}>&#8250;</button>
        {#if rxPending?.toneFreq}<span id={`${pendingId}-freq`} class="sr-only">{t('core.repeater.pendingAnnouncement')}</span>{/if}
      </div>
    {/if}
  </section>
{/if}

<style>
  .repeater-surface {
    display: flex;
    flex-direction: column;
    gap: var(--dl-studioline-gap-micro, var(--dl-fieldline-gap, 6px));
    min-width: 0;
  }
  .repeater-receiver {
    color: var(--v2-text-dim);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
  }
  .repeater-keys {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: minmax(28px, 1fr);
    gap: var(--dl-studioline-gap-micro, var(--dl-fieldline-gap, 6px));
    width: 100%;
  }
  .repeater-key { display: flex; min-width: 0; }
  .repeater-key :global(button) { flex: 1 1 auto; min-height: 28px; }

  /* The stepper follows `ScopeControlsSurface.svelte`'s `.scope-stepper`. */
  .repeater-stepper {
    display: inline-flex;
    flex: none;
    align-items: center;
    gap: 1px;
    white-space: nowrap;
  }
  .repeater-name {
    flex: none;
    padding: 0 3px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    white-space: nowrap;
    color: var(--dl-vfo-unlit-text, var(--v2-text-muted, #5a6875));
  }
  .repeater-step-key {
    appearance: none;
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
    flex: none;
    width: 18px;
    height: 22px;
    font-family: inherit;
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    color: var(--dl-vfo-unlit-text, var(--v2-text-muted, #5a6875));
    cursor: pointer;
  }
  .repeater-step-key:disabled { cursor: not-allowed; }
  /* Reserved width in every state: an unread value is empty, never a
     placeholder. */
  .repeater-step-value {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    min-width: 7ch;
    height: 22px;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--vfo-lamp-color, var(--dl-vfo-red-text, var(--dl-vfo-red, #e2362c)));
  }
  .repeater-step-value[data-pending='true'] { font-style: italic; opacity: 0.75; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
