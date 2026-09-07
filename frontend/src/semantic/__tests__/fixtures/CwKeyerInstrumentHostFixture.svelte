<script lang="ts">
  import type { CommandScalarFeedback } from '../../../primitives/scalar/continuous-scalar.svelte';
  import type { ControlFeedbackPresentationInput } from '../../../primitives/control-feedback/control-feedback-presentation';
  import CwKeyerInstrumentHost, {
    type CwContinuousPresentation,
    type CwKeyerInstrumentHandles,
  } from '../../CwKeyerInstrumentHost.svelte';
  import CwKeyerSurface, { type CwLevelField } from '../../CwKeyerSurface.svelte';
  import type { RadioViewModel } from '../../radio-view-model';

  type BreakInDelayFeedback = ControlFeedbackPresentationInput<number> & {
    readonly sessionEpoch?: number;
    readonly scope?: Readonly<{ control: string; receiver: number; slot?: string }>;
  };

  interface Props {
    view: RadioViewModel;
    presentation?: 'grouped' | 'independent';
    scalarPresentation?: Readonly<CwContinuousPresentation>;
    breakInDelayFeedback?: Readonly<BreakInDelayFeedback>;
    cwPitchFeedback?: Readonly<CommandScalarFeedback>;
    keySpeedFeedback?: Readonly<CommandScalarFeedback>;
    autoTuneAvailable?: boolean;
    onBreakInMode?: (mode: number) => void;
    onLevelChange?: (field: CwLevelField, value: number) => void;
    onApfOn?: (on: boolean) => void;
    onTwinPeakToggle?: () => void;
    onReversePaddleToggle?: () => void;
    onAutoTune?: () => void;
  }

  let {
    view, presentation = 'grouped', scalarPresentation,
    breakInDelayFeedback, cwPitchFeedback, keySpeedFeedback, autoTuneAvailable = false,
    onBreakInMode, onLevelChange, onApfOn, onTwinPeakToggle, onReversePaddleToggle, onAutoTune,
  }: Props = $props();
</script>

<CwKeyerInstrumentHost {view} {keySpeedFeedback} {onLevelChange}>
  {#snippet children(handles: CwKeyerInstrumentHandles)}
    {#key presentation}
      {#if presentation === 'grouped'}
        <CwKeyerSurface
          {view} continuousHandles={handles} {breakInDelayFeedback} {autoTuneAvailable}
          {cwPitchFeedback}
          {onBreakInMode} {onLevelChange} {onApfOn} {onTwinPeakToggle}
          {onReversePaddleToggle} {onAutoTune}
        />
      {:else}
        <section data-testid="independent-cw-keyer-composition">
          <div data-slot="keyer-speed">{@render handles.keyerSpeed(scalarPresentation)}</div>
          <CwKeyerSurface
            {view} continuousHandles={handles} showKeyerSpeed={false}
            {breakInDelayFeedback} {cwPitchFeedback} {autoTuneAvailable}
            {onBreakInMode} {onLevelChange} {onApfOn} {onTwinPeakToggle}
            {onReversePaddleToggle} {onAutoTune}
          />
        </section>
      {/if}
    {/key}
  {/snippet}
</CwKeyerInstrumentHost>
