<!--
  Semantic antenna surface (MOR-1309, vocabulary slice 8C). SAFETY-ADJACENT.

  Presentation only. It renders the MOR-1295 `antenna` fact group — the
  selected TX port, the port-dependent RX-antenna override and the declared
  port count — and emits control intents as callbacks. It holds no state,
  consults no controller and takes no TX lease (v3 ADR invariant 11).

  SAFETY. Four rules govern this file and nothing may relax them:

  (1) AN ANTENNA MUST NOT SWITCH UNDER POWER. Throwing a TX antenna relay
      while RF is present arcs the contacts and can destroy the PA, the relay
      or whatever is on the other end of the feedline. The gate is
      `antennaSwitchBlocks` below, enforced TWICE — on every control
      (`disabled`) and again inside every handler, because a design language
      may restyle these controls and a programmatic click must not switch a
      relay the disabled attribute would have refused.

  (2) UNKNOWN TX STATE IS TREATED AS KEYED. The RF half of the gate is the
      SHARED `keyBlockedReasons` predicate, applied to the SAME App-owned TX
      authority snapshot that gates `RxTxSurface`'s key button and
      `TxAuxSurface`'s TUNE, filtered to the three reasons that mean "the
      transmitter is not provably idle" (`tx-busy`, `radio-transmitting`,
      `rf-state-unknown`). A local re-derivation could drift and disagree; the
      shared import cannot, and this surface never computes TX truth itself
      (R9). `radioTx: 'unknown'` therefore blocks exactly as hard as
      `radioTx: 'on'`.

  (3) AN UNREAD TUNER IS TREATED AS RUNNING (MOR-1295 §3 / MOR-1293
      precedent). ATU state is family 1's `txAux.atu` — it is deliberately NOT
      duplicated into the `antenna` group — and an ATU mid-cycle is emitting a
      carrier. `tunerIdle` therefore requires a POSITIVELY observed, non-
      `tuning` reading; an unobserved or stale `tunerStatus` is not-ready, and
      this surface never infers "tuner idle, safe to switch".
      A radio that declares NO tuner (`atu.availability.structural === false`,
      or no `txAux` group at all — `deriveTxAux` only omits the group when the
      radio declares no `tx` capability or reported nothing at all) is a
      DIFFERENT claim from an unread one: there is no tuner that could be
      running, so it is not a block. Conflating the two would permanently
      disable antenna switching on every ATU-less radio — broken, not safe.

  (4) NO FABRICATED PORT 1. The shipped v2 `toAntennaProps` defaults
      `txAntenna = state?.txAntenna ?? 1`, so v2 silently claims port 1 and
      then reports port 1's RX-ANT; slice 8A refused that and this surface
      preserves the divergence (the same deliberate, documented pattern
      6B/PRE and 9B/break-in carry). An unread port renders `UNKNOWN_TEXT`
      with NO port marked selected — never ANT 1.

  Two-level availability (MOR-977/1256): `structural: false` renders NOTHING —
  "this radio has no RX-ANT" is a different claim from "RX-ANT is unreadable
  right now", which renders present-and-disabled with a reason.
-->
<script module lang="ts">
  import type { AntennaField, RadioViewModel } from './radio-view-model';
  import {
    BLOCKED_LABEL, keyBlockedReasons, type KeyBlockedReason, type TxAuthoritySnapshot,
  } from './rx-tx-surface';

  /** The TX ports the shipped command vocabulary can actually reach
   *  (`set_antenna_1` / `set_antenna_2`) — and exactly what the shipped
   *  `AntennaPanel` renders. `antennaCount` is this group's EVIDENCE GATE
   *  (`> 1`), not a port enumerator: inventing a button for a port no command
   *  can address would be a dead control (the "no speculative keys" rule), so
   *  the count is published as `data-antenna-count` instead. Every shipped
   *  profile declares 1 or 2. */
  export const ANTENNA_PORTS = [1, 2] as const;
  /** The ONE rendering of "not measured". Never `1`, never `off`. */
  export const UNKNOWN_TEXT = '—';

  /** The `keyBlockedReasons` subset that means "the transmitter is not
   *  provably idle". The permit/target/fault reasons are deliberately NOT
   *  here: an out-of-band frequency makes keying illegal, not antenna
   *  switching dangerous, and gating on it would be theatre rather than
   *  safety. */
  const RF_MUST_BE_IDLE: readonly KeyBlockedReason[] = [
    'tx-busy', 'radio-transmitting', 'rf-state-unknown',
  ];
  export type AntennaSwitchBlock = KeyBlockedReason | 'tuner-not-ready';
  /** Each label names the CONSEQUENCE, not just the state — an operator who
   *  sees a disabled antenna button must be told why it is disabled. */
  export const ANTENNA_BLOCKED_LABEL: Record<AntennaSwitchBlock, string> = {
    ...BLOCKED_LABEL,
    'tx-busy': 'a TX lease is in progress — an antenna must not switch under power',
    'radio-transmitting': 'the radio is transmitting — an antenna must not switch under power',
    'rf-state-unknown': 'RF state unknown — an unconfirmed transmitter is treated as keyed',
    'tuner-not-ready': 'ATU not confirmed idle — an unread tuner is treated as running',
  };

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was actually read. */
  export const usable = (f: AntennaField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** Honest text: an unread fact reads as unknown, never as a default. */
  export const textOf = (f: AntennaField<unknown>): string =>
    f.reading.status !== 'known' ? UNKNOWN_TEXT
      : typeof f.reading.value === 'boolean' ? (f.reading.value ? 'on' : 'off')
        : String(f.reading.value);
  /** Rule (4): `undefined` for an unread fact. There is no `?? 1` here and
   *  there must never be one. */
  export const valueOf = <T>(f: AntennaField<T>): T | undefined =>
    f.reading.status === 'known' ? f.reading.value : undefined;

  /** Rule (3). `true` only for a radio with no ATU at all, or a positively
   *  observed ATU that is not mid-cycle. */
  export function tunerIdle(view: RadioViewModel): boolean {
    const atu = view.txAux?.atu;
    if (atu === undefined || !atu.availability.structural) return true;
    return usable(atu) && atu.reading.status === 'known' && atu.reading.value !== 'tuning';
  }

  /** Rules (1)–(3) as one ordered list of reasons; empty ⇔ switching is safe. */
  export function antennaSwitchBlocks(
    view: RadioViewModel, tx: TxAuthoritySnapshot,
  ): readonly AntennaSwitchBlock[] {
    const blocks: AntennaSwitchBlock[] = keyBlockedReasons(view, tx)
      .filter((reason) => RF_MUST_BE_IDLE.includes(reason));
    if (!tunerIdle(view)) blocks.push('tuner-not-ready');
    return blocks;
  }

  /** Per-instance DOM id, so several mounted surfaces keep distinct aria targets. */
  let sequence = 0;
</script>

<script lang="ts">
  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    onSelectPort?: (port: number) => void;
    onToggleRxAnt?: () => void;
  }
  let { view, tx, onSelectPort, onToggleRxAnt }: Props = $props();

  const blockedId = `antenna-blocked-${++sequence}`;
  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine):
   *  a single-port radio gets no empty panel and no zone had to learn about it. */
  let ant = $derived(view.antenna);
  let blocked = $derived(antennaSwitchBlocks(view, tx));
  /** Rule (4). */
  let currentPort = $derived(ant ? valueOf(ant.txAntenna) : undefined);

  /** The handler half of rule (1). The port choice is ABSOLUTE, so it does not
   *  need the current reading and is deliberately NOT gated on it — a radio
   *  that never reported its port must still be able to select one; that is a
   *  readability gap, not a hazard. The hazard gate is `blocked`. */
  function selectPort(port: number): void {
    if (ant && blocked.length === 0) onSelectPort?.(port);
  }
  /** RX-ANT is a RELATIVE toggle computed from the current value, so it needs
   *  an observed reading as well — and `rxAnt`'s own `operational` flag
   *  already carries "the TX port it belongs to was observed" (MOR-1295). */
  function toggleRxAnt(): void {
    if (ant && blocked.length === 0 && usable(ant.rxAnt)) onToggleRxAnt?.();
  }
</script>

{#if ant}
  <section
    class="antenna-surface" data-testid="antenna-surface" aria-label="Antenna selection"
    data-antenna-count={ant.antennaCount} data-switch-blocked={blocked.length > 0}
  >
    <div
      class="antenna-row" role="radiogroup" aria-label="Transmit antenna"
      data-testid="antenna-ports" data-observed={usable(ant.txAntenna)}
    >
      {#each ANTENNA_PORTS as port (port)}
        <button
          type="button" role="radio" class="antenna-choice"
          data-testid={`antenna-port-${port}`} data-port={port}
          aria-checked={currentPort === port} aria-describedby={blockedId}
          disabled={blocked.length > 0}
          onclick={() => selectPort(port)}
        >ANT {port}</button>
      {/each}
      <output data-testid="antenna-port-value">{textOf(ant.txAntenna)}</output>
    </div>

    {#if ant.rxAnt.availability.structural}
      <div class="antenna-row" data-testid="antenna-rx" data-observed={usable(ant.rxAnt)}>
        <button
          type="button" class="antenna-choice" data-testid="antenna-rx-toggle"
          aria-pressed={valueOf(ant.rxAnt)} aria-describedby={blockedId}
          disabled={blocked.length > 0 || !usable(ant.rxAnt)}
          onclick={toggleRxAnt}
        >RX-ANT: {textOf(ant.rxAnt)}</button>
      </div>
    {/if}

    <ul class="antenna-blocked" id={blockedId} data-testid="antenna-blocked">
      {#each blocked as code (code)}<li data-reason={code}>{ANTENNA_BLOCKED_LABEL[code]}</li>{/each}
    </ul>
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .antenna-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .antenna-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .antenna-choice[aria-checked='true'], .antenna-choice[aria-pressed='true'] { font-weight: 700; }
  .antenna-blocked { margin: 0; padding-inline-start: 1.2em; }
  .antenna-blocked:empty { display: none; }
  /* Second channel beside `data-observed`, never the only one: the unknown
     text itself is the primary one and survives forced-colors. */
  [data-observed='false'] { font-style: italic; }
  button:disabled { cursor: not-allowed; }
</style>
