<script module lang="ts">
  import type { Snippet } from 'svelte';
  import type { Capabilities } from '$lib/types/capabilities';
  import type { ServerState } from '$lib/types/state';
  import type { AntennaField, RadioViewModel } from './radio-view-model';
  import {
    BLOCKED_LABEL, keyBlockedReasons, type KeyBlockedReason, type TxAuthoritySnapshot,
  } from './rx-tx-surface';

  export const ANTENNA_PORTS = [1, 2] as const;
  export const UNKNOWN_TEXT = '—';

  const RF_MUST_BE_IDLE: readonly KeyBlockedReason[] = [
    'tx-busy', 'radio-transmitting', 'rf-state-unknown',
  ];
  export type AntennaSwitchBlock = KeyBlockedReason | 'tuner-not-ready';
  export const ANTENNA_BLOCKED_LABEL: Record<AntennaSwitchBlock, string> = {
    ...BLOCKED_LABEL,
    'tx-busy': 'a TX lease is in progress — an antenna must not switch under power',
    'radio-transmitting': 'the radio is transmitting — an antenna must not switch under power',
    'rf-state-unknown': 'RF state unknown — an unconfirmed transmitter is treated as keyed',
    'tuner-not-ready': 'ATU not confirmed idle — an unread tuner is treated as running',
  };

  export const usable = (f: AntennaField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  export const textOf = (f: AntennaField<unknown>): string =>
    f.reading.status !== 'known' ? UNKNOWN_TEXT
      : typeof f.reading.value === 'boolean' ? (f.reading.value ? 'on' : 'off')
        : String(f.reading.value);
  export const valueOf = <T>(f: AntennaField<T>): T | undefined =>
    f.reading.status === 'known' ? f.reading.value : undefined;

  export function tunerIdle(view: RadioViewModel): boolean {
    const atu = view.txAux?.atu;
    if (atu === undefined || !atu.availability.structural) return true;
    return usable(atu) && atu.reading.status === 'known' && atu.reading.value !== 'tuning';
  }

  export function antennaSwitchBlocks(
    view: RadioViewModel, tx: TxAuthoritySnapshot,
  ): readonly AntennaSwitchBlock[] {
    const blocks: AntennaSwitchBlock[] = keyBlockedReasons(view, tx)
      .filter((reason) => RF_MUST_BE_IDLE.includes(reason));
    if (!tunerIdle(view)) blocks.push('tuner-not-ready');
    return blocks;
  }


  export interface AntennaAuthorityPublication {
    readonly state: ServerState | null;
    readonly caps: Capabilities | null;
    readonly session: { readonly state: 'disconnected' | 'connecting' | 'connected' | 'reconnecting'; readonly epoch: number };
  }
  export type SubscribeAntennaAuthority = (handler: (value: AntennaAuthorityPublication) => void) => () => void;
  export interface AntennaInstrumentHandles { readonly txPort: Snippet; readonly rxAnt: Snippet }
  export interface AntennaInstrumentLayout { readonly blockedId: string }
</script>

<script lang="ts">
  import { onMount, onDestroy, untrack } from 'svelte';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import { createAbsoluteChoiceRendererSeat, createToggleRendererSeat, createFiniteRendererContext,
    type FiniteControlAppearance, type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  interface Props {
    view: RadioViewModel | null;
    tx: TxAuthoritySnapshot;
    readTx: () => TxAuthoritySnapshot;
    subscribeControlAuthority: SubscribeAntennaAuthority;
    onSelectPort?: (port: number) => void;
    onToggleRxAnt?: () => void;
    finiteAppearance?: FiniteControlAppearance<number>;
    children: Snippet<[AntennaInstrumentHandles, AntennaInstrumentLayout]>;
  }
  let { view, tx, readTx, subscribeControlAuthority, onSelectPort, onToggleRxAnt,
    finiteAppearance, children }: Props = $props();
  let published = $state.raw<AntennaAuthorityPublication | null>(null);
  let context = $state.raw<FiniteRendererContext | null>(null);
  let identity: string | null = null;
  let destroyed = false;
  let stop: (() => void) | undefined;
  const id = $props.id();
  const layout = { blockedId: `antenna-blocked-${id}` };
  const safe = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  function authority(source: AntennaAuthorityPublication): string | null {
    const generation = source.state?.providerGeneration;
    if (source.session.state !== 'connected' || !safe(source.session.epoch)
      || !safe(generation) || !safe(source.caps?.providerGeneration)
      || generation !== source.caps?.providerGeneration) return null;
    const current = toRadioViewModel(source.state, source.caps);
    return current?.antenna ? JSON.stringify([source.session.epoch, generation, current.topologyId,
      current.antenna.antennaCount, current.antenna.rxAnt.availability.structural]) : null;
  }
  function currentInput() {
    void tx;
    const currentTx = readTx();
    const current = published === null ? null : toRadioViewModel(published.state, published.caps, currentTx);
    return { current, blocked: current === null || antennaSwitchBlocks(current, currentTx).length > 0 };
  }
  const txPortSeat = createAbsoluteChoiceRendererSeat<number>(() => {
    const { current, blocked } = currentInput();
    return { context, label: 'Transmit antenna',
      reading: current?.antenna?.txAntenna.reading ?? { status: 'unknown' },
      available: current?.antenna !== undefined && onSelectPort !== undefined, blocked,
      options: ANTENNA_PORTS.map(value => ({ value, label: `ANT ${value}` })),
      invoke: (port) => onSelectPort?.(port) };
  });
  const rxAntSeat = createToggleRendererSeat(() => {
    const { current, blocked } = currentInput();
    return { context, label: 'RX-ANT', field: current?.antenna?.rxAnt,
      blocked: blocked || onToggleRxAnt === undefined,
      invoke: () => onToggleRxAnt?.() };
  });
  let ant = $derived(view?.antenna);
  const handles = { txPort, rxAnt };
  onMount(() => {
    stop = subscribeControlAuthority(next => {
      if (destroyed) return;
      published = next;
      const nextIdentity = authority(next);
      if (nextIdentity !== identity) {
        identity = nextIdentity;
        context = identity === null ? null : createFiniteRendererContext();
      }
    });
  });
  onDestroy(() => {
    destroyed = true;
    try { stop?.(); } finally { txPortSeat.destroy(); rxAntSeat.destroy(); }
  });
</script>

{#snippet txPort()}
  {#if ant}
    {#key context}{#key finiteAppearance?.choice}
      {#if finiteAppearance}
        <ControlInstrumentRendererHost seat={txPortSeat} renderer={finiteAppearance.choice} />
      {:else}
        {@const lease = untrack(() => txPortSeat.attachRenderer())}
        <div class="antenna-row" role="radiogroup" aria-label="Transmit antenna"
          data-testid="antenna-ports" data-observed={usable(ant.txAntenna)}
          {@attach () => () => lease.dispose()}>
          {#each ANTENNA_PORTS as port (port)}
            <button type="button" role="radio" class="antenna-choice"
              data-testid={`antenna-port-${port}`} data-port={port}
              aria-checked={lease.view?.selected === port} aria-describedby={layout.blockedId}
              disabled={!lease.view?.available} onclick={() => lease.invoke(port)}>ANT {port}</button>
          {/each}
          <output data-testid="antenna-port-value">{textOf(ant.txAntenna)}</output>
        </div>
      {/if}
    {/key}{/key}
  {/if}
{/snippet}
{#snippet rxAnt()}
  {#if ant?.rxAnt.availability.structural}
    {#key context}{#key finiteAppearance?.toggle}
      {#if finiteAppearance}
        <ControlInstrumentRendererHost seat={rxAntSeat} renderer={finiteAppearance.toggle} />
      {:else}
        {@const lease = untrack(() => rxAntSeat.attachRenderer())}
        <div class="antenna-row" data-testid="antenna-rx" data-observed={usable(ant.rxAnt)}
          {@attach () => () => lease.dispose()}>
          <button type="button" class="antenna-choice" data-testid="antenna-rx-toggle"
            aria-pressed={lease.view?.confirmed} aria-describedby={layout.blockedId}
            disabled={!lease.view?.available} onclick={() => lease.invoke()}>
            RX-ANT: {textOf(ant.rxAnt)}</button>
        </div>
      {/if}
    {/key}{/key}
  {/if}
{/snippet}
{@render children(handles, layout)}

<style>
  .antenna-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .antenna-choice[aria-checked='true'], .antenna-choice[aria-pressed='true'] { font-weight: 700; }
  [data-observed='false'] { font-style: italic; }
  button:disabled { cursor: not-allowed; }
</style>
