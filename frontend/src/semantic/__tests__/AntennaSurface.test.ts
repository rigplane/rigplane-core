/**
 * MOR-1309 — the semantic antenna surface (vocabulary slice 8C).
 *
 * SAFETY-ADJACENT. Every test below names the mutation it kills, because the
 * failure modes are physical:
 *   (a) an antenna relay thrown under power — the transmitter is keyed, may be
 *       keyed, or its state was never observed;
 *   (b) an ATU mid-cycle treated as idle because its reading was never taken
 *       (MOR-1295 §3 / the MOR-1293 DIGI-SEL precedent: unknown ⇒ not-ready);
 *   (c) the shipped v2 `?? 1` port fabrication reintroduced at this layer,
 *       which makes the surface claim ANT 1 for a port nobody read;
 *   (d) a gate enforced only on `disabled` — a design language may restyle the
 *       control, and a programmatic click must not switch a relay.
 *
 * Every gate is pinned TWICE and INDEPENDENTLY: once on the attribute, once by
 * invoking the handler directly (`dispatchEvent`, not `.click()` — the latter
 * is a no-op on a disabled button and would make the handler pin vacuous).
 *
 * Fast-pool-safe by construction (MOR-1272): no `vi.mock`, no `vi.stubGlobal`,
 * no global spy. The composed-tree pins live in the isolated-pool wiring file
 * `components-v2/wiring/__tests__/semantic-antenna-wiring.component.test.ts`.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AntennaSurface, {
  ANTENNA_BLOCKED_LABEL, ANTENNA_PORTS, UNKNOWN_TEXT, antennaSwitchBlocks, tunerIdle,
} from '../AntennaSurface.svelte';
import { topologyFixtures, withAntenna, withTxAux } from '../fixtures/topologies';
import type { TxAuthoritySnapshot } from '../rx-tx-surface';
import type {
  AntennaField, AntennaViewModel, Availability, AtuStatus, RadioViewModel, TxAuxField,
} from '../radio-view-model';

const SOURCE = readFileSync('src/semantic/AntennaSurface.svelte', 'utf8');
/** Comments stripped, so the file's own doctrine prose can never be what a
 *  source-scanning test matches. */
const CODE = SOURCE
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const ON: Availability = { structural: true, operational: true };
const OFF: Availability = { structural: false, operational: false };
const DEGRADED: Availability = { structural: true, operational: false };

/** The ONLY snapshot in which switching is permitted: the transmitter is
 *  positively observed OFF, no lease, no risk. */
const RECEIVING: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null,
};
const TRANSMITTING: TxAuthoritySnapshot = {
  ...RECEIVING, phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on',
  mayOwnKey: true,
};
/** The fail-closed case: nobody keyed anything here, the RF state simply was
 *  never confirmed. It must gate exactly as hard as TRANSMITTING. */
const RF_UNKNOWN: TxAuthoritySnapshot = { ...RECEIVING, radioTx: 'unknown' };

/** A fully-observed antenna group on a radio whose ATU is observed and idle. */
const base = (): RadioViewModel => withTxAux(withAntenna(topologyFixtures['1/single']));
const withAnt = (over: Partial<AntennaViewModel>): RadioViewModel => {
  const view = base();
  return { ...view, antenna: { ...view.antenna!, ...over } };
};
/** Re-shape ONLY the ATU fact — the carry-forward-1 axis. */
const withAtu = (atu: TxAuxField<AtuStatus>): RadioViewModel => {
  const view = base();
  return { ...view, txAux: { ...view.txAux!, atu } };
};
const unread = <T>(availability: Availability = ON): AntennaField<T> =>
  ({ reading: { status: 'unknown' }, availability });
const known = <T>(value: T, availability: Availability = ON): AntennaField<T> =>
  ({ reading: { status: 'known', value }, availability });

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = { onSelectPort?: (port: number) => void; onToggleRxAnt?: () => void };

function render(view: RadioViewModel, tx: TxAuthoritySnapshot, handlers: Handlers = {}) {
  const component = mount(AntennaSurface, { target, props: { view, tx, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="antenna-surface"]'),
    el: (id: string) => q<HTMLElement>(`[data-testid="antenna-${id}"]`),
    btn: (id: string) => q<HTMLButtonElement>(`[data-testid="antenna-${id}"]`),
    text: (id: string) => q<HTMLElement>(`[data-testid="antenna-${id}"]`)?.textContent?.trim(),
    reasons: () => [...target.querySelectorAll('[data-testid="antenna-blocked"] li')]
      .map((li) => li.getAttribute('data-reason')),
  };
}

/** The bypass pattern: a REAL click event, dispatched past the `disabled`
 *  attribute, so the handler guard is proven on its own. `.click()` is a
 *  documented no-op on a disabled button and would pass with no guard at all. */
function forceClick(node: HTMLElement): void {
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  flushSync();
}

/* ── the surface stays presentation-only ───────────────────────── */

describe('the antenna surface owns no state and no TX authority (R9)', () => {
  // Kills: importing the runtime, the command bus, transport or the capability
  // store — the layering the semantic vertical exists to remove.
  it('imports nothing but the fact contract and the shared RX/TX vocabulary', () => {
    const specifiers = [...CODE.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]);
    expect(specifiers.length).toBeGreaterThan(0);
    expect([...new Set(specifiers)].sort()).toEqual(['./radio-view-model', './rx-tx-surface']);
  });

  // Kills: a lifecycle hook, an effect, or a dynamic import that could reach a
  // command path from a presentation file.
  it('declares no lifecycle hook and no effect', () => {
    for (const forbidden of ['onMount', 'onDestroy', '$effect', 'import(']) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  // Kills: this surface becoming a second key path or a second TX authority.
  it('never keys, leases or re-derives TX truth', () => {
    // `lease` is deliberately absent from this list: it appears in a REASON
    // STRING ("a TX lease is in progress"), which is the surface reporting the
    // authority's conclusion — the opposite of taking one.
    for (const forbidden of [
      'requestKey', 'tx.start', 'tx.release', 'resetFault', 'sendCommand',
      'capabilities', 'hasCap', '$lib/transport', '$lib/stores',
    ]) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  // Kills: the surface growing a live-state prop beyond the view model and the
  // App-owned authority snapshot.
  it('takes exactly two state props — the view model and the TX snapshot', () => {
    const props = CODE.slice(CODE.indexOf('interface Props'), CODE.indexOf('}: Props'));
    expect([...props.matchAll(/^\s{4}(\w+)[?]?:/gm)].map((m) => m[1]))
      .toEqual(['view', 'tx', 'onSelectPort', 'onToggleRxAnt']);
  });
});

/* ── carry-forward 4: the evidence gate ────────────────────────── */

describe('the group-level evidence gate is the whole mount condition (CF4)', () => {
  // Kills: mounting the surface unconditionally — a single-port radio would
  // gain an antenna panel it has no antennas to fill.
  it('renders nothing at all when the view model carries no antenna group', () => {
    const view = topologyFixtures['1/single'];
    expect(view.antenna).toBeUndefined();
    const r = render(view, RECEIVING);
    expect(r.root()).toBeNull();
    expect(target.innerHTML).not.toContain('antenna');
    r.dispose();
  });

  // Kills: gating RX-ANT on the group gate instead of on its own structural
  // flag — CF4's "independent, finer-grained field gate".
  it('omits RX-ANT entirely when the radio does not declare it, keeping the ports', () => {
    const r = render(withAnt({ rxAnt: unread<boolean>(OFF) }), RECEIVING);
    expect(r.el('rx')).toBeNull();
    expect(r.btn('port-1')).not.toBeNull();
    r.dispose();
  });

  it('renders RX-ANT present-and-disabled when it is declared but unread', () => {
    const r = render(withAnt({ rxAnt: unread<boolean>(DEGRADED) }), RECEIVING);
    expect(r.el('rx')).not.toBeNull();
    expect(r.el('rx')!.dataset.observed).toBe('false');
    expect(r.btn('rx-toggle')!.disabled).toBe(true);
    r.dispose();
  });

  it('publishes the declared port count rather than inventing a button per port', () => {
    const r = render(withAnt({ antennaCount: 4 }), RECEIVING);
    expect(r.root()!.dataset.antennaCount).toBe('4');
    expect(target.querySelectorAll('[role="radio"]').length).toBe(ANTENNA_PORTS.length);
    r.dispose();
  });
});

/* ── carry-forward 3: no fabricated port 1 ─────────────────────── */

describe('an unread TX port renders as unknown, never as ANT 1 (CF3)', () => {
  // MUTATION KILLED: restoring v2's `state?.txAntenna ?? 1` at this layer.
  // `toAntennaProps` does exactly that today, which makes v2 claim port 1 —
  // and then report port 1's RX-ANT — for a radio that reported neither.
  it('marks NO port selected while the port itself is unobserved', () => {
    const r = render(withAnt({ txAntenna: unread<number>(DEGRADED) }), RECEIVING);
    for (const port of ANTENNA_PORTS) {
      expect(r.btn(`port-${port}`)!.getAttribute('aria-checked')).toBe('false');
    }
    r.dispose();
  });

  // MUTATION KILLED: the same fabrication in the readout instead of the
  // pressed state — "ANT 1" printed for a port nobody read.
  it('prints the unknown marker for the port readout, not a port number', () => {
    const r = render(withAnt({ txAntenna: unread<number>(DEGRADED) }), RECEIVING);
    expect(r.text('port-value')).toBe(UNKNOWN_TEXT);
    expect(r.el('ports')!.dataset.observed).toBe('false');
    r.dispose();
  });

  it('marks exactly the observed port selected when the port IS read', () => {
    const r = render(withAnt({ txAntenna: known(2) }), RECEIVING);
    expect(r.btn('port-2')!.getAttribute('aria-checked')).toBe('true');
    expect(r.btn('port-1')!.getAttribute('aria-checked')).toBe('false');
    expect(r.text('port-value')).toBe('2');
    r.dispose();
  });

  // MUTATION KILLED: rendering an unread RX-ANT as `off` — the second half of
  // the same v2 fabrication (v2 reports port 1's RX-ANT for an unread port).
  it('renders an unread RX-ANT as unknown rather than off', () => {
    const r = render(withAnt({ rxAnt: unread<boolean>(DEGRADED) }), RECEIVING);
    expect(r.text('rx-toggle')).toContain(UNKNOWN_TEXT);
    expect(r.btn('rx-toggle')!.getAttribute('aria-pressed')).toBeNull();
    r.dispose();
  });
});

/* ── the under-power gate: attribute AND handler, independently ── */

describe('antenna switching is gated while the transmitter is not provably idle', () => {
  // MUTATION KILLED: inverting or dropping the under-power gate condition on
  // the widget — the operator can throw a TX relay mid-transmission.
  it.each([['transmitting', TRANSMITTING], ['RF-state unknown', RF_UNKNOWN]] as const)(
    'disables every port button while %s', (_label, tx) => {
      const r = render(base(), tx);
      for (const port of ANTENNA_PORTS) expect(r.btn(`port-${port}`)!.disabled).toBe(true);
      expect(r.btn('rx-toggle')!.disabled).toBe(true);
      expect(r.root()!.dataset.switchBlocked).toBe('true');
      r.dispose();
    },
  );

  // MUTATION KILLED (INDEPENDENTLY of `disabled`): dropping the guard inside
  // `selectPort`. Dispatched as a real event past the disabled attribute, so
  // the assertion cannot be satisfied by the attribute alone.
  it.each([['transmitting', TRANSMITTING], ['RF-state unknown', RF_UNKNOWN]] as const)(
    'refuses a forced port click while %s, in the handler', (_label, tx) => {
      const onSelectPort = vi.fn();
      const r = render(base(), tx, { onSelectPort });
      forceClick(r.btn('port-2')!);
      expect(onSelectPort).not.toHaveBeenCalled();
      r.dispose();
    },
  );

  it.each([['transmitting', TRANSMITTING], ['RF-state unknown', RF_UNKNOWN]] as const)(
    'refuses a forced RX-ANT click while %s, in the handler', (_label, tx) => {
      const onToggleRxAnt = vi.fn();
      const r = render(base(), tx, { onToggleRxAnt });
      forceClick(r.btn('rx-toggle')!);
      expect(onToggleRxAnt).not.toHaveBeenCalled();
      r.dispose();
    },
  );

  // MUTATION KILLED: a gate that blocks silently. An operator facing a dead
  // control with no explanation reaches for the radio's front panel instead.
  it('states a reason naming the consequence for every blocked state', () => {
    for (const [tx, code] of [
      [TRANSMITTING, 'radio-transmitting'], [RF_UNKNOWN, 'rf-state-unknown'],
    ] as const) {
      const r = render(base(), tx);
      expect(r.reasons()).toContain(code);
      expect(r.el('blocked')!.textContent).toContain(ANTENNA_BLOCKED_LABEL[code]);
      expect(r.btn('port-1')!.getAttribute('aria-describedby'))
        .toBe(r.el('blocked')!.getAttribute('id'));
      r.dispose();
    }
  });

  // MUTATION KILLED: a gate that never opens. Switching MUST work on a radio
  // that is positively receiving, or the surface is useless and the operator
  // routes around it.
  it('permits switching, in the widget and the handler, while positively receiving', () => {
    const onSelectPort = vi.fn();
    const onToggleRxAnt = vi.fn();
    const r = render(base(), RECEIVING, { onSelectPort, onToggleRxAnt });
    expect(r.btn('port-2')!.disabled).toBe(false);
    expect(r.reasons()).toEqual([]);
    expect(r.root()!.dataset.switchBlocked).toBe('false');
    r.btn('port-2')!.click();
    flushSync();
    r.btn('rx-toggle')!.click();
    flushSync();
    expect(onSelectPort).toHaveBeenCalledExactlyOnceWith(2);
    expect(onToggleRxAnt).toHaveBeenCalledOnce();
    r.dispose();
  });

  // The gate is the SHARED predicate on the SHARED snapshot, filtered to the
  // three reasons that mean "the transmitter is not provably idle". Kills:
  // widening it to the whole `keyBlockedReasons` list — an out-of-band
  // frequency makes KEYING illegal, it does not make a relay hot, and blocking
  // on it would be theatre that trains operators to distrust the gate.
  it('does not block on a permit or target reason alone', () => {
    const view = withTxAux(withAntenna(topologyFixtures['1/ab']));
    expect(view.txPermit.status).toBe('unknown');
    expect(antennaSwitchBlocks(view, RECEIVING)).toEqual([]);
  });

  // Kills: a local re-derivation of TX truth drifting from the shared one.
  it('shares the transmitter-busy vocabulary with the key gate', () => {
    expect(CODE).toContain('keyBlockedReasons');
    expect(antennaSwitchBlocks(base(), TRANSMITTING)).toContain('radio-transmitting');
    expect(antennaSwitchBlocks(base(), { ...RECEIVING, phase: 'key-confirm-pending' }))
      .toContain('tx-busy');
  });
});

/* ── carry-forwards 1 + 2: the ATU lives in txAux, and unknown is busy ── */

describe('ATU readiness comes from txAux.atu and fails closed (CF1, CF2)', () => {
  // CF2, confirmed as an acceptance criterion: `txAux.atu` (slice 1A) is the
  // ONLY tuner signal and was deliberately NOT duplicated into `antenna`.
  // Kills: a second ATU fact appearing on the antenna group and this surface
  // quietly preferring it.
  it('reads no tuner fact from the antenna group, which carries none', () => {
    expect(Object.keys(base().antenna!).sort()).toEqual(['antennaCount', 'rxAnt', 'txAntenna']);
    expect(CODE).toContain('view.txAux?.atu');
    expect(CODE).not.toMatch(/antenna[^\n]*\.(atu|tuner)/);
  });

  // MUTATION KILLED: letting an unknown tuner fall through to "idle". This is
  // the MOR-1295 §3 ruling verbatim — 8C must not infer "tuner idle, safe to
  // switch" from an unobserved `tunerStatus`.
  it.each([
    ['unobserved', { reading: { status: 'unknown' }, availability: ON }],
    ['stale', { reading: { status: 'unknown' }, availability: DEGRADED }],
  ] as const)('treats a %s tuner reading as not-ready', (_label, atu) => {
    const view = withAtu(atu as TxAuxField<AtuStatus>);
    expect(tunerIdle(view)).toBe(false);
    const r = render(view, RECEIVING);
    expect(r.btn('port-1')!.disabled).toBe(true);
    expect(r.reasons()).toContain('tuner-not-ready');
    r.dispose();
  });

  // MUTATION KILLED (INDEPENDENTLY of `disabled`): dropping the guard inside
  // the handler while the tuner is unread.
  it('refuses a forced port click while the tuner reading is unknown', () => {
    const onSelectPort = vi.fn();
    const r = render(withAtu({ reading: { status: 'unknown' }, availability: ON }), RECEIVING,
      { onSelectPort });
    forceClick(r.btn('port-1')!);
    expect(onSelectPort).not.toHaveBeenCalled();
    r.dispose();
  });

  // MUTATION KILLED: treating a running ATU as idle. An ATU mid-cycle IS
  // emitting a carrier — this is the under-power case the ticket names.
  it('treats a tuner that is actively tuning as not-ready', () => {
    const view = withAtu(known<AtuStatus>('tuning') as TxAuxField<AtuStatus>);
    expect(tunerIdle(view)).toBe(false);
    const r = render(view, RECEIVING);
    expect(r.btn('port-1')!.disabled).toBe(true);
    r.dispose();
  });

  it.each(['off', 'on'] as const)('treats an observed, idle ATU (%s) as ready', (value) => {
    const view = withAtu(known<AtuStatus>(value) as TxAuxField<AtuStatus>);
    expect(tunerIdle(view)).toBe(true);
    const r = render(view, RECEIVING);
    expect(r.btn('port-1')!.disabled).toBe(false);
    r.dispose();
  });

  /**
   * The distinction the 6B/PRE precedent turns on: "this radio has no ATU" is
   * a STRUCTURAL claim, not an unread reading. Collapsing the two would
   * permanently disable antenna switching on every ATU-less multi-port radio —
   * broken, not fail-closed. Kills: a `tunerIdle` that returns false for a
   * structurally-absent ATU, and one that returns true for an unread one.
   */
  it('does not block on a radio that declares no ATU at all', () => {
    const noTuner = withAtu({ reading: { status: 'unknown' }, availability: OFF });
    expect(tunerIdle(noTuner)).toBe(true);
    const { txAux: _dropped, ...noTxAux } = base();
    expect(tunerIdle(noTxAux as RadioViewModel)).toBe(true);
    const r = render(noTxAux as RadioViewModel, RECEIVING);
    expect(r.btn('port-1')!.disabled).toBe(false);
    r.dispose();
  });
});
