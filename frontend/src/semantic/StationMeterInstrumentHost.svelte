<script module lang="ts">
  import type { Snippet } from 'svelte';
  import type { BarMeterFrame } from '../components-v2/meters/bar-meter-motion.svelte';
  import type { SignalMeterFrame } from '../components-v2/meters/signal-meter-motion.svelte';
  import type {
    MeterContinuitySession,
    MeterSourceIdentity,
  } from '../primitives/meters/meter-ballistics.svelte';
  import type { ActionRendererSeat } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type {
    LevelMeterKey,
    LevelMeterProjection,
    SwrMeterProjection,
  } from './bar-meter-projector';
  import type { MeterField, MeterRfState, RadioViewModel } from './radio-view-model';

  export type StationSignalFacts = Readonly<
    Pick<MeterField, 'reading' | 'availability' | 'relevant' | 'domain'>
  >;
  export interface StationSignalMeterFrame {
    readonly field: StationSignalFacts;
    readonly motion: SignalMeterFrame;
  }
  export type RenderLevelProjection<K extends LevelMeterKey> = Readonly<
    Omit<K extends 'swr' ? SwrMeterProjection : LevelMeterProjection<K>, 'source'>
  >;
  export interface StationLevelMeterFrame<K extends LevelMeterKey = LevelMeterKey> {
    readonly projection: RenderLevelProjection<K>;
    readonly motion: BarMeterFrame;
  }
  export type StationLevelMeterRenderer<K extends LevelMeterKey> = Snippet<[
    frame: StationLevelMeterFrame<K>,
    resetPeakSeat?: ActionRendererSeat,
  ]>;
  export type StationSignalMeterRenderer = Snippet<[
    frame: StationSignalMeterFrame | null,
    projection: SignalMeterFrame['projection'] | null,
    swr: StationLevelMeterFrame<'swr'> | null,
    rfState: MeterRfState,
    present: boolean,
  ]>;
  export interface StationMeterInstrumentHandles {
    readonly signal: Snippet<[renderer: StationSignalMeterRenderer]>;
    readonly power: Snippet<[renderer: StationLevelMeterRenderer<'power'>]>;
    readonly swr: Snippet<[renderer: StationLevelMeterRenderer<'swr'>]>;
    readonly alc: Snippet<[renderer: StationLevelMeterRenderer<'alc'>]>;
    readonly drainCurrent: Snippet<[renderer: StationLevelMeterRenderer<'drainCurrent'>]>;
    readonly drainVoltage: Snippet<[renderer: StationLevelMeterRenderer<'drainVoltage'>]>;
    readonly compression: Snippet<[renderer: StationLevelMeterRenderer<'compression'>]>;
  }
  export interface StationMeterAuthorityPublication {
    readonly view: RadioViewModel | null;
    readonly session?: MeterContinuitySession | null;
  }
  export type SubscribeStationMeterAuthority = (
    handler: (publication: StationMeterAuthorityPublication) => void,
  ) => () => void;
</script>
<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import {
    createBarMeterMotion,
    type BarMeterMotionBinding,
  } from '../components-v2/meters/bar-meter-motion.svelte';
  import {
    createSignalMeterMotion,
    type SignalMeterMotionBinding,
  } from '../components-v2/meters/signal-meter-motion.svelte';
  import {
    createActionRendererSeat,
    createFiniteRendererContext,
    type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import { projectBarMeters, projectSwrMeter } from './bar-meter-projector';
  import { projectSignalMeter } from '../components-v2/meters/smeter-scale';
  interface Props {
    subscribeStationMeterAuthority: SubscribeStationMeterAuthority;
    children: Snippet<[StationMeterInstrumentHandles]>;
  }
  interface SignalOwner {
    readonly binding: SignalMeterMotionBinding;
    readonly frame: StationSignalMeterFrame;
    field: StationSignalFacts;
  }
  interface LevelOwner<K extends LevelMeterKey = LevelMeterKey> {
    readonly key: K;
    readonly binding: BarMeterMotionBinding;
    readonly frame: StationLevelMeterFrame<K>;
    projection: RenderLevelProjection<K>;
    source: MeterSourceIdentity | null | undefined;
    session: MeterContinuitySession | null | undefined;
    resetContext: FiniteRendererContext | null;
    resetSeat: ActionRendererSeat | undefined;
    disposeRoot: () => void;
  }
  let { subscribeStationMeterAuthority, children }: Props = $props();
  let signalOwner = $state.raw<SignalOwner | null>(null);
  let signalProjection = $state.raw<SignalMeterFrame['projection'] | null>(null);
  let signalRoot: (() => void) | null = null;
  let owners = $state.raw(new Map<LevelMeterKey, LevelOwner>());
  let rfState = $state<MeterRfState>('unknown');
  let groupPresent = $state(false);
  let mounted = false;
  let destroyed = false;
  const factsOf = (field: MeterField): StationSignalFacts => ({
    reading: field.reading,
    availability: field.availability,
    relevant: field.relevant,
    domain: field.domain,
  });
  const observed = (field: MeterField): boolean =>
    field.availability.operational && field.reading.status === 'known';
  const renderProjection = <K extends LevelMeterKey>(
    projection: LevelMeterProjection<K>,
  ): RenderLevelProjection<K> => {
    const { source: _source, ...render } = projection;
    return render as unknown as RenderLevelProjection<K>;
  };
  const sameSource = (a: MeterSourceIdentity, b: MeterSourceIdentity): boolean =>
    a.providerGeneration === b.providerGeneration
    && a.scope === b.scope
    && a.receiver === b.receiver
    && a.path === b.path;
  const sameAuthority = (
    aSource: MeterSourceIdentity | null | undefined,
    aSession: MeterContinuitySession | null | undefined,
    bSource: MeterSourceIdentity | null | undefined,
    bSession: MeterContinuitySession | null | undefined,
  ): boolean => {
    const mode = (source: typeof aSource, session: typeof aSession) => {
      if (source === null || session === null) return 'null';
      return source === undefined || session === undefined ? 'unqualified' : 'qualified';
    };
    const aMode = mode(aSource, aSession);
    const bMode = mode(bSource, bSession);
    if (aMode !== bMode) return false;
    if (aMode !== 'qualified') return true;
    return sameSource(aSource!, bSource!)
      && aSession!.controlSessionEpoch === bSession!.controlSessionEpoch;
  };
  const currentOwner = <K extends LevelMeterKey>(owner: LevelOwner<K>): boolean =>
    owners.get(owner.key) === owner;
  function clean(cleanups: readonly (() => void)[]): void {
    let failed = false;
    let failure: unknown;
    for (const cleanup of cleanups) {
      try {
        cleanup();
      } catch (error) {
        if (!failed) {
          failed = true;
          failure = error;
        }
      }
    }
    if (failed) throw failure;
  }
  function installResetSeat<K extends LevelMeterKey>(owner: LevelOwner<K>): void {
    if (!['power', 'alc', 'drainCurrent'].includes(owner.key)) return;
    owner.resetContext = createFiniteRendererContext();
    owner.resetSeat = createActionRendererSeat(() => ({
      context: owner.resetContext,
      availability: {
        structural: currentOwner(owner),
        operational: currentOwner(owner) && owner.projection.showPeak,
      },
      label: 'Reset peak',
      invoke: () => {
        if (currentOwner(owner)) owner.binding.resetPeak();
      },
    }));
  }
  function installLevel<K extends LevelMeterKey>(
    projection: LevelMeterProjection<K>,
    session: MeterContinuitySession | null | undefined,
  ): LevelOwner<K> {
    let created!: LevelOwner<K>;
    const disposeRoot = $effect.root(() => {
      let render = $state.raw(renderProjection(projection));
      let resetSeat = $state.raw<ActionRendererSeat | undefined>();
      const binding = createBarMeterMotion({
        value: projection.motionFraction,
        peakEnabled: projection.showPeak,
        source: projection.source,
        session,
      });
      created = {
        key: projection.key,
        binding,
        frame: {
          get projection() { return created.projection; },
          motion: binding.frame,
        },
        get projection() { return render; },
        set projection(value) { render = value; },
        source: projection.source,
        session,
        resetContext: null,
        get resetSeat() { return resetSeat; },
        set resetSeat(value) { resetSeat = value; },
        disposeRoot: () => undefined,
      };
    });
    created.disposeRoot = disposeRoot;
    owners.set(projection.key, created);
    installResetSeat(created);
    if (mounted) created.binding.start();
    return created;
  }
  function removeLevel(key: LevelMeterKey): void {
    const owner = owners.get(key);
    if (!owner) return;
    const seat = owner.resetSeat;
    const root = owner.disposeRoot;
    owners.delete(key);
    owner.resetContext = null;
    owner.resetSeat = undefined;
    owner.disposeRoot = () => undefined;
    clean([() => seat?.destroy(), () => owner.binding.stop(), root]);
  }
  function removeSignal(): void {
    const signal = signalOwner;
    const root = signalRoot;
    signalOwner = null;
    signalRoot = null;
    clean([() => signal?.binding.stop(), () => root?.()]);
  }
  function syncLevel<K extends LevelMeterKey>(
    owner: LevelOwner<K>,
    projection: LevelMeterProjection<K>,
    session: MeterContinuitySession | null | undefined,
  ): void {
    if (!sameAuthority(owner.source, owner.session, projection.source, session)) {
      owner.resetSeat?.destroy();
      owner.resetContext = null;
      owner.resetSeat = undefined;
      owner.source = projection.source;
      owner.session = session;
      installResetSeat(owner);
    }
    owner.projection = renderProjection(projection);
    owner.binding.sync({
      value: projection.motionFraction,
      peakEnabled: projection.showPeak,
      source: projection.source,
      session,
    });
  }
  function applyPublication({ view, session }: StationMeterAuthorityPublication): void {
    if (destroyed) return;
    const meters = view?.meters;
    groupPresent = meters !== undefined;
    rfState = meters?.rfState ?? 'unknown';
    const projection = meters ? projectSignalMeter(
      observed(meters.signal) && meters.signal.reading.status === 'known'
        ? meters.signal.reading.value : null,
      meters.signal.domain,
    ) : null;
    signalProjection = projection;
    const composite = !!meters
      && (meters.signal.availability.structural || meters.swr.availability.structural);
    if (!composite && signalOwner) removeSignal();
    else if (composite && meters && projection) {
      const field = factsOf(meters.signal);
      if (!signalOwner) {
        let created!: SignalOwner;
        signalRoot = $effect.root(() => {
          let currentField = $state.raw(field);
          const binding = createSignalMeterMotion({
            projection, present: meters.signal.availability.structural,
            source: meters.signal.source, session,
          });
          created = { binding,
            get field() { return currentField; },
            set field(value) { currentField = value; },
            frame: { get field() { return created.field; }, motion: binding.frame } };
        });
        signalOwner = created;
        if (mounted) created.binding.start();
      } else {
        signalOwner.field = field;
        signalOwner.binding.sync({
          projection, present: meters.signal.availability.structural,
          source: meters.signal.source, session,
        });
      }
    }
    const swr = view ? projectSwrMeter(view) : null;
    const projected: LevelMeterProjection[] = view
      ? [...projectBarMeters(view), ...(swr === null ? [] : [swr])]
      : [];
    const admitted = new Set(projected.map(({ key }) => key));
    for (const key of [...owners.keys()]) if (!admitted.has(key)) removeLevel(key);
    for (const projection of projected) {
      const owner = owners.get(projection.key);
      if (owner) syncLevel(owner, projection, session);
      else installLevel(projection, session);
    }
    owners = new Map(owners);
  }
  function disposeOwned(): void {
    clean([removeSignal, ...[...owners.keys()].map((key) => () => removeLevel(key))]);
  }
  const subscribe = untrack(() => subscribeStationMeterAuthority);
  let unsubscribe: () => void = () => undefined;
  try {
    unsubscribe = subscribe(applyPublication);
  } catch (error) {
    destroyed = true;
    try { disposeOwned(); } catch {}
    throw error;
  }
  onMount(() => {
    mounted = true;
    signalOwner?.binding.start();
    for (const owner of owners.values()) owner.binding.start();
  });
  onDestroy(() => {
    destroyed = true;
    clean([unsubscribe, disposeOwned]);
  });
</script>
{#snippet signal(renderer: StationSignalMeterRenderer)}
  {@render renderer(signalOwner?.frame ?? null, signalProjection, owners.get('swr')?.frame as StationLevelMeterFrame<'swr'> ?? null, rfState, groupPresent)}
{/snippet}
{#snippet power(renderer: StationLevelMeterRenderer<'power'>)}{@const owner = owners.get('power')}{#if owner}{@render renderer(owner.frame as StationLevelMeterFrame<'power'>, owner.resetSeat)}{/if}{/snippet}
{#snippet swr(renderer: StationLevelMeterRenderer<'swr'>)}{@const owner = owners.get('swr')}{#if owner}{@render renderer(owner.frame as StationLevelMeterFrame<'swr'>)}{/if}{/snippet}
{#snippet alc(renderer: StationLevelMeterRenderer<'alc'>)}{@const owner = owners.get('alc')}{#if owner}{@render renderer(owner.frame as StationLevelMeterFrame<'alc'>, owner.resetSeat)}{/if}{/snippet}
{#snippet drainCurrent(renderer: StationLevelMeterRenderer<'drainCurrent'>)}{@const owner = owners.get('drainCurrent')}{#if owner}{@render renderer(owner.frame as StationLevelMeterFrame<'drainCurrent'>, owner.resetSeat)}{/if}{/snippet}
{#snippet drainVoltage(renderer: StationLevelMeterRenderer<'drainVoltage'>)}{@const owner = owners.get('drainVoltage')}{#if owner}{@render renderer(owner.frame as StationLevelMeterFrame<'drainVoltage'>)}{/if}{/snippet}
{#snippet compression(renderer: StationLevelMeterRenderer<'compression'>)}{@const owner = owners.get('compression')}{#if owner}{@render renderer(owner.frame as StationLevelMeterFrame<'compression'>)}{/if}{/snippet}
{@render children({ signal, power, swr, alc, drainCurrent, drainVoltage, compression })}
