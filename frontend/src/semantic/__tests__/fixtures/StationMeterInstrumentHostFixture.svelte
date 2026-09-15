<script module lang="ts">
  import type { MeterAppearance } from '../../../../component-kit-api/src/index';
  import FixtureLevelMeter from '../../../../component-kit-api/fixtures/external-kit/src/FixtureLevelMeter.svelte';
  import FixtureSignalMeter from '../../../../component-kit-api/fixtures/external-kit/src/FixtureSignalMeter.svelte';
  import type { StationMeterAuthorityPublication,
    SubscribeStationMeterAuthority } from '../../StationMeterInstrumentHost.svelte';
  export const fixtureMeterAppearance = {
    signal: FixtureSignalMeter,
    level: FixtureLevelMeter,
  } satisfies MeterAppearance;
  export class StationMeterTestPublisher {
    readonly handlers = new Set<(publication: StationMeterAuthorityPublication) => void>();
    current: StationMeterAuthorityPublication;
    constructor(current: StationMeterAuthorityPublication) { this.current = current; }
    readonly subscribe: SubscribeStationMeterAuthority = (handler) => {
      this.handlers.add(handler); handler(this.current);
      return () => { this.handlers.delete(handler); };
    };
    emit(publication: StationMeterAuthorityPublication): void {
      this.current = publication;
      for (const handler of this.handlers) handler(publication);
    }
  }
  export const stationMeterProbe = {
    frames: new Map<string, object>(),
    seats: new Map<string, object | undefined>(),
    clear() { this.frames.clear(); this.seats.clear(); },
  };
</script>
<script lang="ts">
  import { untrack } from 'svelte';
  import StationMeterInstrumentHost, { type StationLevelMeterFrame, type StationMeterInstrumentHandles, type StationSignalMeterFrame } from '../../StationMeterInstrumentHost.svelte';
  import MetersSurface from '../../MetersSurface.svelte';
  import type { MeterContinuitySession } from '../../../primitives/meters/meter-ballistics.svelte';
  import type { RadioViewModel } from '../../radio-view-model';
  interface Props { subscribeStationMeterAuthority?: SubscribeStationMeterAuthority;
    view?: RadioViewModel; session?: MeterContinuitySession | null; presentation?: string;
    renderSurface?: boolean; probe?: boolean }
  let {
    subscribeStationMeterAuthority: externalSubscribe, view, session,
    presentation = 'native', renderSurface = true, probe = false,
  }: Props = $props();
  const local = new StationMeterTestPublisher(untrack(() => ({ view: view ?? null, session })));
  let last = untrack(() => view);
  $effect.pre(() => {
    const next = view;
    const nextSession = session;
    if (!Object.is(next, last)) {
      last = next;
      local.emit({ view: next ?? null, session: nextSession });
    }
  });
  const subscribeStationMeterAuthority: SubscribeStationMeterAuthority =
    untrack(() => externalSubscribe) ?? local.subscribe;
</script>
<StationMeterInstrumentHost {subscribeStationMeterAuthority}>
  {#snippet children(handles: StationMeterInstrumentHandles)}
    {#key presentation}
      <div data-presentation={presentation}>
        {#if renderSurface}<MetersSurface {handles} />{/if}
        {#if probe}
          {#snippet signal(frame: StationSignalMeterFrame | null)}{@const _ = stationMeterProbe.frames.set('signal', frame ?? {})}<i hidden>{_.size}</i>{/snippet}
          {#snippet power(frame: StationLevelMeterFrame<'power'>, seat?: object)}{@const _ = stationMeterProbe.frames.set('power', frame)}{@const s = stationMeterProbe.seats.set('power', seat)}<i hidden>{_.size + s.size}</i>{/snippet}
          {#snippet swr(frame: StationLevelMeterFrame<'swr'>)}{@const _ = stationMeterProbe.frames.set('swr', frame)}<i hidden>{_.size}</i>{/snippet}
          {#snippet alc(frame: StationLevelMeterFrame<'alc'>, seat?: object)}{@const _ = stationMeterProbe.frames.set('alc', frame)}{@const s = stationMeterProbe.seats.set('alc', seat)}<i hidden>{_.size + s.size}</i>{/snippet}
          {#snippet drainCurrent(frame: StationLevelMeterFrame<'drainCurrent'>, seat?: object)}{@const _ = stationMeterProbe.frames.set('drainCurrent', frame)}{@const s = stationMeterProbe.seats.set('drainCurrent', seat)}<i hidden>{_.size + s.size}</i>{/snippet}
          {#snippet drainVoltage(frame: StationLevelMeterFrame<'drainVoltage'>)}{@const _ = stationMeterProbe.frames.set('drainVoltage', frame)}<i hidden>{_.size}</i>{/snippet}
          {#snippet compression(frame: StationLevelMeterFrame<'compression'>)}{@const _ = stationMeterProbe.frames.set('compression', frame)}<i hidden>{_.size}</i>{/snippet}
          {@render handles.signal(signal)}{@render handles.power(power)}{@render handles.swr(swr)}
          {@render handles.alc(alc)}{@render handles.drainCurrent(drainCurrent)}
          {@render handles.drainVoltage(drainVoltage)}{@render handles.compression(compression)}
        {/if}
      </div>
    {/key}
  {/snippet}
</StationMeterInstrumentHost>
