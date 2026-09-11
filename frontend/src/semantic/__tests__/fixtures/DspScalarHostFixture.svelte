<script lang="ts">
  import type { ScalarAppearance } from '../../../../component-kit-api/src/index';
  import type { FiniteControlAppearance, FiniteRendererContext }
    from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { CommandScalarFeedback } from '../../../primitives/scalar/continuous-scalar.svelte';
  import DspInstrumentHost from '../../DspInstrumentHost.svelte';
  import DspScalarHost from '../../DspScalarHost.svelte';
  import DspSurface, { type DspLevelField } from '../../DspSurface.svelte';
  import type { DspFiniteChoiceValue, DspFiniteHandles, DspNotchMode,
    DspToggleField } from '../../dsp-instruments';
  import type { DspScalarFeedback, DspScalarField, DspScalarHandles,
    DspScalarPresentation } from '../../dsp-scalars';
  import type { RadioViewModel } from '../../radio-view-model';

  interface Props {
    view: RadioViewModel;
    feedback?: DspScalarFeedback;
    presentation?: 'grouped' | 'independent' | 'nr';
    scalarPresentation?: Readonly<DspScalarPresentation>;
    agcLabels?: Record<string, string>;
    nbLevelMax?: number;
    nbLevelPercent?: boolean;
    scalarAppearance?: ScalarAppearance;
    presentationIsCurrent?: () => boolean;
    pendingNb?: boolean | null;
    pendingNr?: boolean | null;
    finiteAppearance?: FiniteControlAppearance<DspFiniteChoiceValue>;
    rendererContext?: FiniteRendererContext | null;
    onToggle?: (field: DspToggleField, next: boolean) => void;
    onLevelChange?: (field: DspLevelField, value: number) => void;
    onNotchModeChange?: (mode: DspNotchMode) => void;
    onAgcModeChange?: (mode: number) => void;
  }
  let { view, feedback, presentation = 'grouped', scalarPresentation,
    agcLabels = {}, nbLevelMax = 255, nbLevelPercent = false,
    scalarAppearance, presentationIsCurrent,
    pendingNb = null, pendingNr = null, finiteAppearance, rendererContext = null,
    onToggle, onLevelChange, onNotchModeChange, onAgcModeChange }: Props = $props();
  let finiteSelection = $derived(finiteAppearance === undefined
    ? {} : { finiteAppearance, rendererContext });

  function defaultFeedback(field: DspScalarField): Readonly<CommandScalarFeedback> {
    const fact = view.dsp?.[field];
    const confirmed = fact?.reading.status === 'known' ? fact.reading.value : null;
    const available = fact?.availability.operational === true && confirmed !== null;
    return Object.freeze({
      confirmed: available ? confirmed : null,
      target: null, requestedTarget: null, phase: available ? 'idle' : 'unavailable',
      busy: false, availability: available ? 'available' : 'unavailable', outcome: null,
      lifecycleId: null, transitionId: null, providerGeneration: 1, sessionEpoch: 1,
      scope: Object.freeze({ control: field === 'nbLevel' ? 'nb-level' : 'nb-width', receiver: 0 }),
      repeatPolicy: 'latest-target-wins',
    });
  }
  let scalarFeedback = $derived(feedback ?? Object.freeze({
    nbLevel: defaultFeedback('nbLevel'), nbDepth: defaultFeedback('nbDepth'),
    nbWidth: defaultFeedback('nbWidth'), nrLevel: defaultFeedback('nrLevel'),
    notchFreq: defaultFeedback('notchFreq'), manualNotchWidth: defaultFeedback('manualNotchWidth'),
    agcTimeConstant: defaultFeedback('agcTimeConstant'),
  }));
</script>

<DspInstrumentHost {view} {agcLabels} {pendingNb} {pendingNr} {onToggle}
  {onNotchModeChange} {onAgcModeChange} {...finiteSelection}>
  {#snippet children(finiteHandles: DspFiniteHandles)}
    <DspScalarHost {view} feedback={scalarFeedback} {nbLevelMax} {nbLevelPercent}
      {scalarAppearance} {presentationIsCurrent}
      onLevelChange={(field, value) => onLevelChange?.(field, value)}>
      {#snippet children(scalarHandles: DspScalarHandles)}
        {#snippet independentFinite(handles: DspFiniteHandles)}
          <section data-testid="independent-dsp-finite">
            <div data-slot="toggles">{@render handles.nrActive()}{@render handles.nbActive()}</div>
            <div data-slot="notch">{@render handles.notchMode()}</div>
            <div data-slot="agc">{@render handles.agcMode()}</div>
          </section>
        {/snippet}
        {#snippet independentScalars(handles: DspScalarHandles)}
          <section data-testid="independent-dsp-scalars">
            <div data-slot="nb-level">{@render handles.nbLevel(scalarPresentation)}</div>
            <div data-slot="nb-width">{@render handles.nbWidth(scalarPresentation)}</div>
          </section>
        {/snippet}
        {#key presentation}
          {#if presentation === 'nr'}
            <section data-testid="nr-dsp-scalar">
              {@render scalarHandles.nrLevel(scalarPresentation)}
            </section>
          {:else}
            <DspSurface {view} {finiteHandles} scalarHandles={scalarHandles}
              finiteLayout={presentation === 'independent' ? independentFinite : undefined}
              scalarLayout={presentation === 'independent' ? independentScalars : undefined}
              onLevelChange={(field, value) => onLevelChange?.(field, value)} />
          {/if}
        {/key}
      {/snippet}
    </DspScalarHost>
  {/snippet}
</DspInstrumentHost>
