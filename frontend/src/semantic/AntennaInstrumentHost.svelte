<script module lang="ts">
  import type { Snippet } from 'svelte';
  import type { Capabilities } from '$lib/types/capabilities';
  import type { ServerState } from '$lib/types/state';
  import type { AntennaField, RadioViewModel } from './radio-view-model';
  import {
    keyBlockedReasons, type KeyBlockedReason, type TxAuthoritySnapshot,
  } from './rx-tx-surface';
  import { t } from '$lib/i18n';
  import { BLOCKED_REASON_KEY } from '$lib/i18n/blocked-reasons';

  export const ANTENNA_PORTS = [1, 2] as const;
  /** MOR-2652: an unread reading prints nothing — an unlit slot, never a dash.
   *  Kept exported because `AntennaSurface.svelte` re-exports the name. */
  export const UNKNOWN_TEXT = '';

  const RF_MUST_BE_IDLE: readonly KeyBlockedReason[] = [
    'tx-busy', 'radio-transmitting', 'rf-state-unknown',
  ];
  export type AntennaSwitchBlock = KeyBlockedReason | 'tuner-not-ready';

  /**
   * MOR-2691 — the blocked reason is no longer visible list text; it is the
   * disabled control's own `title`, resolved here to a plain catalog sentence
   * per block code (the same `Record<code, catalog key>` table pattern as
   * `$lib/i18n/blocked-reasons`). Every `KeyBlockedReason` member maps onto
   * the RX/TX gate's own sentence; the antenna gate only ever yields
   * `tx-busy`/`radio-transmitting`/`rf-state-unknown` (see `RF_MUST_BE_IDLE`),
   * and the two unconfirmed readings below get antenna-specific "waiting
   * for …" wording with no "unknown" and no "?". Exhaustiveness follows the
   * old `ANTENNA_BLOCKED_LABEL = { ...BLOCKED_LABEL, … }` spread ruling.
   */
  const ANTENNA_BLOCKED_KEY: Record<AntennaSwitchBlock, string> = {
    'tx-target-unknown': BLOCKED_REASON_KEY['tx-target-unknown'],
    'tx-permit-denied': BLOCKED_REASON_KEY['tx-permit-denied'],
    'tx-permit-unknown': BLOCKED_REASON_KEY['tx-permit-unknown'],
    'tx-fault': BLOCKED_REASON_KEY['tx-fault'],
    'tx-busy': BLOCKED_REASON_KEY['tx-busy'],
    'radio-transmitting': BLOCKED_REASON_KEY['radio-transmitting'],
    'rf-state-unknown': 'core.antenna.blocked.transmitterUnconfirmed',
    'tuner-not-ready': 'core.antenna.blocked.tunerNotReady',
  };
  /** The disabled-reason sentence for the closed gate; `undefined` while it opens. */
  export const antennaBlockedTitle = (blocks: readonly AntennaSwitchBlock[]): string | undefined =>
    blocks.length === 0 ? undefined : blocks.map((code) => t(ANTENNA_BLOCKED_KEY[code])).join('; ');

  export const usable = (f: AntennaField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  export const textOf = (f: AntennaField<unknown>): string =>
    f.reading.status !== 'known' ? UNKNOWN_TEXT
      : typeof f.reading.value === 'boolean' ? (f.reading.value ? 'on' : 'off')
        : String(f.reading.value);

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
    /** App-owned projection shared by every authority consumer. */
    readonly view?: RadioViewModel | null;
    readonly session: { readonly state: 'disconnected' | 'connecting' | 'connected' | 'reconnecting'; readonly epoch: number };
  }
  export type SubscribeAntennaAuthority = (handler: (value: AntennaAuthorityPublication) => void) => () => void;
  export interface AntennaInstrumentHandles {
    readonly txPort: Snippet<[compact?: boolean]>;
    readonly rxAnt: Snippet<[compact?: boolean]>;
  }
  export interface AntennaInstrumentLayout {
    /** Why the switch is blocked right now, resolved to a catalog sentence;
     *  `undefined` while the gate opens. */
    readonly blockedTitle: string | undefined;
  }
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
  const layout = {
    get blockedTitle() { return antennaBlockedTitle(currentInput().blockedReasons); },
  };
  const safe = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  function authority(source: AntennaAuthorityPublication): string | null {
    const generation = source.state?.providerGeneration;
    if (source.session.state !== 'connected' || !safe(source.session.epoch)
      || !safe(generation) || !safe(source.caps?.providerGeneration)
      || generation !== source.caps?.providerGeneration) return null;
    const current = source.view === undefined
      ? toRadioViewModel(source.state, source.caps) : source.view;
    return current?.antenna ? JSON.stringify([source.session.epoch, generation, current.topologyId,
      current.antenna.antennaCount, current.antenna.rxAnt.availability.structural]) : null;
  }
  function currentInput() {
    void tx;
    const currentTx = readTx();
    const current = published === null ? null
      : published.view === undefined
        ? toRadioViewModel(published.state, published.caps, currentTx) : published.view;
    const blockedReasons = current === null ? [] : antennaSwitchBlocks(current, currentTx);
    return { current, blocked: blockedReasons.length > 0, blockedReasons };
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

{#snippet txPort(compact = false)}
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
              aria-checked={lease.view?.selected === port} title={layout.blockedTitle}
              disabled={!lease.view?.available} onclick={() => lease.invoke(port)}>ANT {port}</button>
          {/each}
          <output class="antenna-value" class:sr-only={compact} data-testid="antenna-port-value">{textOf(ant.txAntenna)}</output>
        </div>
      {/if}
    {/key}{/key}
  {/if}
{/snippet}
{#snippet rxAnt(compact = false)}
  {#if ant?.rxAnt.availability.structural}
    {#key context}{#key finiteAppearance?.toggle}
      {#if finiteAppearance}
        <ControlInstrumentRendererHost seat={rxAntSeat} renderer={finiteAppearance.toggle} />
      {:else}
        {@const lease = untrack(() => rxAntSeat.attachRenderer())}
        <div class="antenna-row" data-testid="antenna-rx" data-observed={usable(ant.rxAnt)}
          {@attach () => () => lease.dispose()}>
          <button type="button" class="antenna-choice" data-testid="antenna-rx-toggle"
            aria-pressed={lease.view?.confirmed} title={layout.blockedTitle}
            disabled={!lease.view?.available} onclick={() => lease.invoke()}>
            {compact ? 'RX ANT' : `RX-ANT:${textOf(ant.rxAnt) ? ` ${textOf(ant.rxAnt)}` : ''}`}</button>
          {#if compact}<output class="sr-only" data-testid="antenna-rx-value">{textOf(ant.rxAnt)}</output>{/if}
        </div>
      {/if}
    {/key}{/key}
  {/if}
{/snippet}
{@render children(handles, layout)}

<style>
  .antenna-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .antenna-value { display: inline-block; min-width: 1ch; font-variant-numeric: tabular-nums; }
  .antenna-choice[aria-checked='true'], .antenna-choice[aria-pressed='true'] { font-weight: 700; }
  [data-observed='false'] { font-style: italic; }
  button:disabled { cursor: not-allowed; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
