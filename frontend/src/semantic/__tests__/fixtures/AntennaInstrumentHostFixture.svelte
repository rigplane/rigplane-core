<script lang="ts">
  import type { ComponentProps } from 'svelte';
  import type { Capabilities } from '$lib/types/capabilities';
  import type { ServerState } from '$lib/types/state';
  import AntennaInstrumentHost, { type AntennaAuthorityPublication } from '../../AntennaInstrumentHost.svelte';
  import AntennaSurface from '../../AntennaSurface.svelte';
  import type { RadioViewModel } from '../../radio-view-model';
  let { view, tx, onSelectPort = () => {}, onToggleRxAnt = () => {},
    subscribeControlAuthority, readTx, finiteAppearance, arrangement = 'grouped', body = true,
    capture = () => {},
  }: Omit<ComponentProps<typeof AntennaInstrumentHost>, 'children' | 'subscribeControlAuthority' | 'readTx'> & {
    view: RadioViewModel;
    subscribeControlAuthority?: ComponentProps<typeof AntennaInstrumentHost>['subscribeControlAuthority'];
    readTx?: ComponentProps<typeof AntennaInstrumentHost>['readTx'];
    arrangement?: 'grouped' | 'independent'; body?: boolean;
    capture?: (handles: import('../../AntennaInstrumentHost.svelte').AntennaInstrumentHandles) => void;
  } = $props();
  function subscribe(handler: (value: AntennaAuthorityPublication) => void) {
    if (subscribeControlAuthority) return subscribeControlAuthority(handler);
    const ant = view.antenna;
    const fields = { txAntenna: ant?.txAntenna, rxAntenna1: ant?.rxAnt,
      rxAntenna2: ant?.rxAnt, tunerStatus: view.txAux?.atu };
    const state = Object.fromEntries(Object.entries(fields).map(([key, field]) => [key,
      field?.reading.status === 'known' ? (key === 'tunerStatus'
        ? { off: 0, on: 1, tuning: 2 }[field.reading.value as 'off'] : field.reading.value) : null]));
    handler({ session: { state: 'connected', epoch: 1 },
      state: { ...state, providerGeneration: 1, active: 'MAIN',
        fieldStatus: Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, {
          observed: field?.reading.status === 'known', freshness: 'fresh',
          availability: field?.availability.operational ? 'available' : 'unavailable',
        }])) } as unknown as ServerState,
      caps: { model: 'fixture', providerGeneration: 1, receivers: 1, vfoScheme: 'single',
        scope: false, audio: false, tx: true, freqRanges: [], modes: [], filters: [], txBands: null,
        audioConfig: { sampleRate: 48000, channels: 1, codecs: [] },
        webrtc: { available: false, enabled: false },
        antennas: ant?.antennaCount ?? 1, capabilities: ['tx',
          ...(ant?.rxAnt.availability.structural ? ['rx_antenna'] : []),
          ...(view.txAux?.atu.availability.structural ? ['tuner'] : [])],
      } as Capabilities });
    return () => {};
  }
</script>

<AntennaInstrumentHost {view} {tx} {onSelectPort} {onToggleRxAnt} {finiteAppearance}
  subscribeControlAuthority={subscribe} readTx={readTx ?? (() => tx)}>
  {#snippet children(handles, layout)}
    {@const captured = capture(handles)}
    {#if body}
      {#if arrangement === 'grouped'}<AntennaSurface {view} {tx} {handles} {layout} />
      {:else}<div data-testid="independent-tx">{@render handles.txPort()}</div>
        <aside data-testid="independent-rx">{@render handles.rxAnt()}</aside>{/if}
    {/if}
  {/snippet}
</AntennaInstrumentHost>
