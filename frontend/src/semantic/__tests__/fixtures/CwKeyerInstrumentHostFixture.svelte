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
    standard?: boolean;
    scalarPresentation?: Readonly<CwContinuousPresentation>;
    breakInDelayFeedback?: Readonly<BreakInDelayFeedback>;
    pitchFeedback?: Readonly<CommandScalarFeedback>;
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
    view, presentation = 'grouped', standard = false, scalarPresentation,
    breakInDelayFeedback, pitchFeedback, keySpeedFeedback, autoTuneAvailable = false,
    onBreakInMode, onLevelChange, onApfOn, onTwinPeakToggle, onReversePaddleToggle, onAutoTune,
  }: Props = $props();
</script>

<CwKeyerInstrumentHost {view} {keySpeedFeedback} {pitchFeedback} {onLevelChange}>
  {#snippet children(handles: CwKeyerInstrumentHandles)}
    {#key presentation}
      {#if presentation === 'grouped'}
        <CwKeyerSurface
          {view} continuousHandles={handles} {standard} {breakInDelayFeedback} {autoTuneAvailable}
          {onBreakInMode} {onLevelChange} {onApfOn} {onTwinPeakToggle}
          {onReversePaddleToggle} {onAutoTune}
        />
      {:else}
        <section data-testid="independent-cw-keyer-composition">
          <div data-slot="keyer-speed">{@render handles.keyerSpeed(scalarPresentation)}</div>
          <div data-slot="pitch-hz">{@render handles.pitchHz(scalarPresentation)}</div>
          <CwKeyerSurface
            {view} continuousHandles={handles} showKeyerSpeed={false} showPitchHz={false}
            {breakInDelayFeedback} {autoTuneAvailable}
            {onBreakInMode} {onLevelChange} {onApfOn} {onTwinPeakToggle}
            {onReversePaddleToggle} {onAutoTune}
          />
        </section>
      {/if}
    {/key}
  {/snippet}
</CwKeyerInstrumentHost>
