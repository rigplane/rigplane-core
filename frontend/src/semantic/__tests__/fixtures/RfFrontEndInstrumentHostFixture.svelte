<script module lang="ts">
  import type { Capabilities } from '$lib/types/capabilities';
  import type { ServerState } from '$lib/types/state';
  import type {
    RfFrontEndAuthorityPublication as ModulePublication,
    RfSqlControlModel as ModuleControlModel,
  } from '../../rf-front-end-instruments';

  const observed = {
    storePath: 'fixture', observed: true, freshness: 'fresh', availability: 'available',
    lastObservedMonotonic: 1,
  };
  const authorityState = {
    stateContractVersion: 1, providerGeneration: 3,
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-09-07T00:00:00Z', active: 'MAIN', ptt: false,
    split: false, dualWatch: false, tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: {
      freqHz: 14_250_000, mode: 'USB', filterNum: 1, filter: 1, dataMode: 0,
      vfoA: { freqHz: 14_250_000, mode: 'USB', filterNum: 1, dataMode: 0 },
      vfoB: { freqHz: 14_300_000, mode: 'USB', filterNum: 1, dataMode: 0 },
      activeSlot: 'A', afLevel: 0.5, rfGain: 1, squelch: 0,
      sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    },
    connection: {},
    fieldStatus: Object.fromEntries([
      'active', 'split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter',
      'main.activeSlot', 'main.rfGain', 'main.squelch',
    ].map((path) => [path, { ...observed }])),
  } as unknown as ServerState;

  export function rfTestAuthorityPublication(
    controlModel: ModuleControlModel, generation = 3,
  ): ModulePublication {
    const caps = {
      stateContractVersion: 1, providerGeneration: generation, model: 'RF-SURFACE-TEST',
      scope: false, audio: true, tx: false,
      capabilities: ['audio', 'rf_gain', 'squelch', 'preamp', 'attenuator', 'digisel', 'ip_plus'],
      receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
      preValues: [0, 1, 2], attValues: [0, 6, 12, 18],
      audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
      webrtc: { available: false, enabled: false }, txBands: null,
      rfSqlControlModel: controlModel,
    } as Capabilities;
    return {
      state: { ...authorityState, providerGeneration: generation },
      caps, session: { state: 'connected', epoch: 7 },
    };
  }
</script>

<script lang="ts">
  import type {
    FiniteControlAppearance, FiniteRendererContext,
  } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import RfFrontEndInstrumentHost from '../../RfFrontEndInstrumentHost.svelte';
  import RfFrontEndSurface from '../../RfFrontEndSurface.svelte';
  import type { RadioViewModel } from '../../radio-view-model';
  import type {
    RfFrontEndAuthorityPublication,
    RfFrontEndFiniteChoiceValue,
    RfFrontEndLevelFeedback,
    RfFrontEndLevelField,
    RfFrontEndLevelHandles,
    RfFrontEndToggleField,
    RfSqlControlModel,
    SubscribeRfFrontEndAuthority,
  } from '../../rf-front-end-instruments';

  interface Props {
    publication: RfFrontEndAuthorityPublication;
    view: RadioViewModel | null;
    subscribeControlAuthority: SubscribeRfFrontEndAuthority;
    controlModel: RfSqlControlModel;
    rfSqlFeedback?: RfFrontEndLevelFeedback | null;
    layout?: 'grouped' | 'independent';
    renderSurface?: boolean;
    pendingPreamp?: number | null;
    finiteAppearance?: FiniteControlAppearance<RfFrontEndFiniteChoiceValue>;
    rendererContext?: FiniteRendererContext | null;
    onPreampChange?: (level: number) => void;
    onAttenuatorChange?: (db: number) => void;
    onLevelChange?: (field: RfFrontEndLevelField, value: number) => void;
    onToggle?: (field: RfFrontEndToggleField, next: boolean) => void;
  }

  let {
    publication, view, subscribeControlAuthority, controlModel, rfSqlFeedback,
    layout = 'grouped', renderSurface = false, pendingPreamp = null,
    finiteAppearance, rendererContext = null,
    onPreampChange, onAttenuatorChange, onLevelChange, onToggle,
  }: Props = $props();
  let presentation = $derived({
    ...publication, view, controlModel, rfSqlFeedback,
  });
  let selection = $derived(finiteAppearance === undefined ? {} : { finiteAppearance, rendererContext });
</script>

<RfFrontEndInstrumentHost
  {presentation} {subscribeControlAuthority} {onLevelChange} {pendingPreamp}
  onPreChange={onPreampChange} onAttChange={onAttenuatorChange}
  onDigiSelToggle={(next) => onToggle?.('digiSel', next)}
  onIpPlusToggle={(next) => onToggle?.('ipPlus', next)}
  {...selection}
>
  {#snippet children(handles: RfFrontEndLevelHandles)}
    {#if renderSurface && view !== null}
      <RfFrontEndSurface {view} levelHandles={handles} />
    {:else}
      {#key layout}
        <section data-layout={layout} data-handle-kind={handles.kind}>
          {#if handles.kind === 'combined'}
            <div data-slot="rf-sql">{@render handles.rfSql()}</div>
          {:else}
            <div data-slot="rf-gain">{@render handles.rfGain()}</div>
            <div data-slot="squelch">{@render handles.squelch()}</div>
          {/if}
          <div data-finite-slot="preamp">{@render handles.preamp()}</div>
          <div data-finite-slot="attenuator">{@render handles.attenuator()}</div>
          <div data-finite-slot="digiSel">{@render handles.digiSel()}</div>
          <div data-finite-slot="ipPlus">{@render handles.ipPlus()}</div>
        </section>
      {/key}
    {/if}
  {/snippet}
</RfFrontEndInstrumentHost>
