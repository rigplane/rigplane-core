/**
 * MOR-1065 — the live adapter behind the semantic VFO / RX-TX surfaces.
 *
 * The failure mode this file exists to kill is FABRICATION: an adapter that
 * fills an unobserved receiver with 'MAIN', an unobserved A/B slot with 'A',
 * or an unobserved split with `false` looks perfectly healthy on screen and
 * lies about the radio. Every "unknown" assertion below names the mutation
 * it kills.
 *
 * The contract itself (`semantic/radio-view-model`) cannot be imported by
 * `lib/runtime/**` production code (eslint invariant 1) — test files are
 * exempt, so this is also where the emitted shape is proven to be a real,
 * validator-clean `RadioViewModel`.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import {
  validateRadioViewModel, type RadioViewModel,
} from '../../../../semantic/radio-view-model';
import { topologyFixtures } from '../../../../semantic/fixtures/topologies';
import { toMemoryPanelProps } from '../../props/panel-props';
import { toRadioViewModel } from '../radio-view-model-adapter';

const DUAL = ['scope', 'audio', 'tx', 'dual_rx'];
const SINGLE = ['scope', 'audio', 'tx'];

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: DUAL,
    receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: 'hardware', audioFftAvailable: true, ...overrides,
  } as Capabilities;
}

/** One representative capability set per canonical topology fixture. */
const TOPOLOGY_CAPS: Record<string, Capabilities> = {
  '1/single': caps({ vfoScheme: 'single', receivers: 1, capabilities: SINGLE }),
  '1/ab': caps({ vfoScheme: 'ab', receivers: 1, capabilities: SINGLE }),
  '2/ab_shared': caps({ vfoScheme: 'ab_shared', receivers: 2 }),
  '2/main_sub': caps({ vfoScheme: 'main_sub', receivers: 2 }),
};

const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
};
const stale: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'stale', availability: 'stale',
};

const slot = (freqHz: number, mode = 'USB') => ({
  freqHz, mode, filterNum: 1, dataMode: 0,
});
const receiver = (freqHz: number) => ({
  ...slot(freqHz), vfoA: slot(freqHz), vfoB: slot(freqHz + 50000), activeSlot: 'A',
  filter: 1, att: 0, preamp: 0, nb: false, nr: false, afLevel: 1, rfGain: 1,
  squelch: 0, sMeter: 0,
});

/** A fully observed state: every field this adapter reads is fresh+available. */
function observedState(overrides: Partial<ServerState> = {}): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter', 'main.activeSlot', 'sub.activeSlot',
  ];
  for (const key of ['main', 'sub'] as const) {
    for (const vfo of ['vfoA', 'vfoB']) {
      paths.push(`${key}.${vfo}.freqHz`, `${key}.${vfo}.mode`, `${key}.${vfo}.filterNum`);
    }
  }
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    scopeControls: { receiver: 0, dual: false } as ServerState['scopeControls'],
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
    ...overrides,
  } as ServerState;
}

/** Nothing has ever been observed: the payload exists, field status does not. */
const unobservedState = (): ServerState => ({
  ...observedState(), fieldStatus: {},
} as ServerState);

function model(state: ServerState | null, capabilities: Capabilities | null): RadioViewModel {
  const view = toRadioViewModel(state, capabilities);
  expect(view).not.toBeNull();
  // The single most important assertion in this file: the adapter's output is
  // a real view model by the contract's OWN validator, cross-field invariants
  // included (no 'allowed' permit under an unknown target, no orphan
  // isTxTarget). `lib/runtime` cannot import this type — the test can.
  return validateRadioViewModel(view);
}

describe('topology is derived from real capabilities', () => {
  it.each(Object.keys(TOPOLOGY_CAPS))('%s is reachable and validator-clean', (id) => {
    const view = model(observedState(), TOPOLOGY_CAPS[id]);
    expect(view.topologyId).toBe(id);
    expect(view.vfoScheme).toBe(topologyFixtures[id as keyof typeof topologyFixtures].vfoScheme);
  });

  it('emits the structural VFO positions each scheme implies', () => {
    const shape = (id: string) => model(observedState(), TOPOLOGY_CAPS[id]).vfos
      .map((v) => `${v.receiver}:${v.slot.kind === 'slotted' ? v.slot.id : v.slot.kind}`);
    expect(shape('1/single')).toEqual(['MAIN:unslotted']);
    expect(shape('1/ab')).toEqual(['MAIN:A', 'MAIN:B']);
    expect(shape('2/ab_shared')).toEqual(['MAIN:unslotted', 'SUB:unslotted']);
    expect(shape('2/main_sub')).toEqual(['MAIN:A', 'MAIN:B', 'SUB:A', 'SUB:B']);
  });

  it('renders nothing rather than guessing when capabilities are absent or contradictory', () => {
    expect(toRadioViewModel(observedState(), null)).toBeNull();
    // receivers=2 under a single-receiver scheme is `invalid-topology`; a
    // guessed topology here would silently mis-address every TX decision.
    expect(toRadioViewModel(observedState(), caps({ vfoScheme: 'ab', receivers: 2 }))).toBeNull();
  });
});

describe('unobserved facts survive as the explicit unknown branch', () => {
  it('projects fresh relative values without fabricating A/B identity', () => {
    const base = observedState();
    const fieldStatus = {
      ...base.fieldStatus,
      'main.freqHz': fresh,
      'main.mode': fresh,
      'main.filter': fresh,
      'main.unselectedVfo.freqHz': fresh,
      'main.unselectedVfo.mode': fresh,
      'main.unselectedVfo.filterNum': fresh,
    } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    const view = model({
      ...base,
      main: {
        ...base.main,
        unselectedVfo: slot(7_100_000, 'LSB'),
      },
      fieldStatus,
    } as ServerState, caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE,
      vfoReadback: 'selected_unselected',
    }));

    expect(view.vfos.map((vfo) => [vfo.slot, vfo.frequencyHz, vfo.label])).toEqual([
      [{ kind: 'relative', role: 'selected' }, 14_250_000, 'Selected VFO'],
      [{ kind: 'relative', role: 'unselected' }, 7_100_000, 'Unselected VFO'],
    ]);
    expect(view.vfos.filter((vfo) => vfo.isActive)).toHaveLength(1);
    expect(view.vfos.some((vfo) => vfo.slot.kind === 'slotted')).toBe(false);
  });

  it('keeps selected-only relative readback neutral while the unselected tuple is unavailable', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    const capabilities = caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE,
      vfoReadback: 'selected_unselected',
    });
    const state = {
      ...base,
      main: { ...base.main, unselectedVfo: undefined },
      fieldStatus,
    } as ServerState;

    const view = model(state, capabilities);
    expect(view.vfos.map((vfo) => [vfo.slot, vfo.frequencyHz])).toEqual([
      [{ kind: 'relative', role: 'selected' }, 14_250_000],
      [{ kind: 'relative', role: 'unselected' }, null],
    ]);
    expect(view.vfos.some((vfo) => vfo.slot.kind === 'slotted')).toBe(false);
    expect(toMemoryPanelProps(state, capabilities).vfoIdentityKnown).toBe(false);
  });

  it('keeps an older selected-only payload neutral without local persistence guesses', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    for (const slotKey of ['vfoA', 'vfoB']) {
      for (const leaf of ['freqHz', 'mode', 'filterNum', 'dataMode']) {
        delete fieldStatus[`main.${slotKey}.${leaf}`];
      }
    }
    const capabilities = caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE,
    });
    const state = {
      ...base,
      main: {
        ...base.main, vfoA: undefined, vfoB: undefined, unselectedVfo: undefined,
      },
      fieldStatus,
    } as ServerState;

    const view = model(state, capabilities);
    expect(view.vfos.map((vfo) => [vfo.slot, vfo.frequencyHz])).toEqual([
      [{ kind: 'relative', role: 'selected' }, 14_250_000],
      [{ kind: 'relative', role: 'unselected' }, null],
    ]);
    expect(toMemoryPanelProps(state, capabilities).vfoIdentityKnown).toBe(false);
  });

  it('leaves an explicitly absolute single-RX A/B contract literal', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    const view = model({ ...base, fieldStatus } as ServerState, caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE, vfoReadback: 'absolute',
    }));
    expect(view.vfos.map((vfo) => vfo.slot)).toEqual([
      { kind: 'slotted', id: 'A' }, { kind: 'slotted', id: 'B' },
    ]);
  });

  // MUTATION KILLED: `activeReceiver: { status: 'known', receiver: state.active ?? 'MAIN' }`
  // — the classic "default to MAIN" that MOR-988 §3.2 forbids.
  it('never fabricates an active receiver, and marks no VFO active', () => {
    const view = model(unobservedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.activeReceiver).toEqual({ status: 'unknown' });
    expect(view.vfos.some((v) => v.isActive)).toBe(false);
    expect(view.disabledReasons).toContainEqual({
      field: 'activeReceiver', code: 'field-not-observed',
    });
  });

  // MUTATION KILLED: `split: { status: 'known', value: state.split ?? false }`.
  it.each(['split', 'dualWatch'] as const)('never fabricates %s as off', (field) => {
    expect(model(unobservedState(), TOPOLOGY_CAPS['2/main_sub'])[field])
      .toEqual({ status: 'unknown' });
  });

  // MUTATION KILLED: enumerating `['A', 'B']` for a slotted scheme whose slot
  // view was never sent — one fabricated 'A' position and the operator reads a
  // slot identity the radio never reported.
  it('degrades an unobserved A/B slot view to slot.kind "unknown", not to "A"', () => {
    const view = model(observedState({
      main: { ...receiver(14250000), vfoA: undefined, vfoB: undefined },
    } as Partial<ServerState>), TOPOLOGY_CAPS['1/ab']);
    expect(view.vfos).toHaveLength(1);
    expect(view.vfos[0].slot).toEqual({ kind: 'unknown' });
    expect(view.vfos.some((v) => v.slot.kind === 'slotted')).toBe(false);
  });

  // MUTATION KILLED: reading `<rx>.activeSlot` ungated. The backend DEFAULTS
  // that field (`state_schema.py`: `activeSlot: str = "A"`), so an ungated
  // read highlights MAIN A as active on evidence the radio never provided —
  // a fabricated fact on an operator display, in a file whose header promises
  // every radio fact is field-status gated.
  it('marks no slot active when <rx>.activeSlot was never observed', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, unknown>;
    delete fieldStatus['main.activeSlot'];
    delete fieldStatus['sub.activeSlot'];
    const view = model(
      { ...base, fieldStatus } as unknown as ServerState, TOPOLOGY_CAPS['2/main_sub'],
    );

    expect(view.vfos.filter((v) => v.isActive)).toEqual([]);
    // The slot IDENTITIES stay structurally known (the slot view WAS observed);
    // only "which one is active" is unknown. The two must not collapse.
    expect(view.vfos.map((v) => v.slot.kind))
      .toEqual(['slotted', 'slotted', 'slotted', 'slotted']);
  });

  it('marks the observed slot active once activeSlot IS reported', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.vfos.filter((v) => v.isActive).map((v) => v.label)).toEqual(['MAIN A']);
  });

  // ── MOR-1335 (G4): the per-receiver active slot ─────────────────────────
  //
  // `isActive` answers "is this the ACTIVE RECEIVER's active VFO" and is
  // therefore globally unique — which is why the VFO surface, gating tuning on
  // it, left SUB untunable on `2/main_sub`. `isActiveSlot` answers the
  // per-receiver question, so each receiver names the VFO its own
  // receiver-scoped `set_freq` would write.

  // MUTATION KILLED: deriving `isActiveSlot` from the ACTIVE RECEIVER (i.e.
  // aliasing `isActive`) — SUB would report no active slot at all and the
  // parity gap this fact exists to close would silently persist.
  it('names each receiver\'s own active slot, including the receiver that is NOT active', () => {
    const view = model(observedState({
      sub: { ...receiver(14300000), activeSlot: 'B' },
    } as Partial<ServerState>), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.activeReceiver).toEqual({ status: 'known', receiver: 'MAIN' });
    expect(view.vfos.filter((v) => v.isActiveSlot).map((v) => v.label))
      .toEqual(['MAIN A', 'SUB B']);
    // ...and the radio-wide fact is unchanged by it: still exactly one.
    expect(view.vfos.filter((v) => v.isActive).map((v) => v.label)).toEqual(['MAIN A']);
  });

  // MUTATION KILLED: `slot.id === (rx.activeSlot ?? 'A')` — the backend
  // DEFAULTS activeSlot to "A", so an ungated read would hand the surface a
  // tunable MAIN A / SUB A on evidence the radio never provided.
  it('marks NO active slot for a receiver whose activeSlot was never observed', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, unknown>;
    delete fieldStatus['sub.activeSlot'];
    const view = model(
      { ...base, fieldStatus } as unknown as ServerState, TOPOLOGY_CAPS['2/main_sub'],
    );
    expect(view.vfos.filter((v) => v.receiver === 'SUB' && v.isActiveSlot)).toEqual([]);
    // Non-vacuous: MAIN's observed reading still names its slot.
    expect(view.vfos.filter((v) => v.isActiveSlot).map((v) => v.label)).toEqual(['MAIN A']);
  });

  // An unslotted position IS its receiver's active slot — there is no other
  // VFO on that receiver for `set_freq` to write. Kills a derivation keyed to
  // a slotted id, which would leave every `single`/`ab_shared` position
  // untunable.
  it.each(['1/single', '2/ab_shared'] as const)('%s: every unslotted position is its receiver\'s active slot', (id) => {
    const view = model(observedState(), TOPOLOGY_CAPS[id]);
    expect(view.vfos.every((v) => v.isActiveSlot)).toBe(true);
  });

  // The decomposition, stated once over the whole matrix: `isActive` is
  // exactly "this receiver is the active one AND this is its active slot".
  it.each(Object.keys(TOPOLOGY_CAPS))('%s: isActive === active receiver AND isActiveSlot', (id) => {
    const view = model(observedState(), TOPOLOGY_CAPS[id]);
    for (const vfo of view.vfos) {
      const receiverActive = view.activeReceiver.status === 'known'
        && view.activeReceiver.receiver === vfo.receiver;
      expect(vfo.isActive, vfo.label).toBe(receiverActive && vfo.isActiveSlot);
    }
  });

  // MUTATION KILLED: reading `state.txTarget` without the freshness gate — a
  // stale target keys the wrong VFO.
  it('reports a stale TX target as unknown/stale and blocks the permit', () => {
    const view = model(observedState({
      fieldStatus: { ...observedState().fieldStatus, txTarget: stale },
    }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.txTarget).toEqual({ status: 'unknown', reason: 'stale' });
    expect(view.txPermit.status).not.toBe('allowed');
    expect(view.vfos.some((v) => v.isTxTarget)).toBe(false);
  });

  // MUTATION KILLED: `frequencyHz: rx.freqHz ?? 14074000` (the legacy
  // `toVfoProps` default) — a plausible-looking frequency for an unread radio.
  it('nulls unobserved frequency / mode / filter instead of defaulting them', () => {
    const view = model(unobservedState(), TOPOLOGY_CAPS['1/single']);
    expect(view.vfos[0]).toMatchObject({ frequencyHz: null, mode: null, filter: null });
  });

  it('keeps observed readings when the field status backs them', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.vfos[0]).toMatchObject({
      receiver: 'MAIN', frequencyHz: 14250000, mode: 'USB', filter: 'FIL1',
      isActive: true, isTxTarget: true,
    });
  });
});

describe('TX identity and permit fail closed', () => {
  it('marks exactly the VFO a known target names', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.vfos.filter((v) => v.isTxTarget).map((v) => v.label)).toEqual(['MAIN A']);
    expect(view.txPermit).toEqual({ status: 'allowed', band: '20m' });
  });

  // MUTATION KILLED: keeping a target whose slot contradicts the scheme (a
  // slot-less target under `main_sub`) instead of collapsing it to unknown.
  it('collapses a target that contradicts the capability scheme', () => {
    const view = model(observedState({
      txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14250000 },
    }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.txTarget).toEqual({ status: 'unknown', reason: 'contradiction' });
    expect(view.txPermit.status).toBe('unknown');
  });

  it('denies an out-of-band target rather than leaving the permit open', () => {
    const view = model(observedState({
      txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 1000 },
    }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.txPermit).toEqual({ status: 'denied', reason: 'outside-configured-ranges' });
    expect(view.disabledReasons).toContainEqual({ field: 'txPermit', code: 'out-of-band' });
  });

  it('reports unconfigured TX ranges as unknown, never as allowed', () => {
    const view = model(observedState(), caps({ txBands: null }));
    expect(view.txPermit).toEqual({ status: 'unknown', reason: 'ranges-unconfigured' });
  });
});

describe('scope availability separates structural from operational', () => {
  it('holds the hardware scope structurally present but not operational without controls', () => {
    const view = model(observedState({ scopeControls: undefined }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.scope.hardwareScope).toEqual({ structural: true, operational: false });
    expect(view.disabledReasons).toContainEqual({
      field: 'scope.hardwareScope', code: 'field-not-observed',
    });
  });

  it('reports an absent audio FFT as capability-unavailable', () => {
    const view = model(observedState(), caps({ audioFftAvailable: false }));
    expect(view.scope.audioFftScope).toEqual({ structural: false, operational: false });
    expect(view.disabledReasons).toContainEqual({
      field: 'scope.audioFftScope', code: 'capability-unavailable',
    });
  });
});

// MOR-1256: `operationalReceivers` (presentation-capabilities.ts) had zero
// consumers — a `dual-rx-unavailable` radio (structurally dual, no `dual_rx`
// tag) kept SUB fully enabled. `vfos` correctly stays derived from
// `structuralReceivers` (MOR-977: the strip must still be PRESENT); the gap
// was that nothing fed `operationalReceivers` anywhere, so no CONSUMER ever
// disabled it. This closes the gap the same way `scope.hardwareScope` /
// `scope.audioFftScope` already report degraded-but-structural facts: one
// `disabledReasons` entry per structurally-present, operationally-absent
// receiver, read back by `dual-receiver-strips.ts`'s `isOperationalStrip`.
describe('operational receiver availability separates structural from operational (MOR-1256)', () => {
  const dualRxUnavailableCaps = caps({ capabilities: SINGLE });

  // MUTATION KILLED: dropping the `topology.operationalReceivers` loop
  // entirely — SUB stays structurally present (correct) but nothing ever
  // marks it disabled, reproducing the exact bug this ticket exists to fix.
  it('marks the structurally-present, operationally-unavailable SUB receiver disabled', () => {
    const view = model(observedState(), dualRxUnavailableCaps);
    expect(view.topologyId).toBe('2/main_sub');
    expect(view.vfos.some((v) => v.receiver === 'SUB')).toBe(true);
    expect(view.disabledReasons).toContainEqual({
      field: 'receiver.SUB', code: 'capability-unavailable',
    });
  });

  // MUTATION KILLED: pushing the reason for every structural receiver
  // instead of only the ones missing from `operationalReceivers` — MAIN
  // would falsely disable too.
  it('never marks MAIN unavailable — only the receiver that failed the capability check', () => {
    const view = model(observedState(), dualRxUnavailableCaps);
    expect(view.disabledReasons.some((r) => r.field === 'receiver.MAIN')).toBe(false);
  });

  it('emits no receiver disabledReason when the radio is fully dual-capable', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.disabledReasons.some((r) => r.field.startsWith('receiver.'))).toBe(false);
  });
});

describe('the emitted model carries only contract data', () => {
  it('survives a JSON round-trip unchanged — no functions, classes or live objects', () => {
    for (const id of Object.keys(TOPOLOGY_CAPS)) {
      const view = model(observedState(), TOPOLOGY_CAPS[id]);
      expect(JSON.parse(JSON.stringify(view))).toEqual(view);
    }
  });

  // MUTATION KILLED: passing the capability object (or a component/module
  // path) through onto the view model so a surface can "just read caps" —
  // exactly the manufacturer/runtime leak the contract exists to prevent.
  it('leaks no capability object and no module path', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    const strings: string[] = [];
    const walk = (value: unknown, path: string): void => {
      expect(typeof value).not.toBe('function');
      if (typeof value === 'string') strings.push(value);
      if (value && typeof value === 'object') {
        for (const [key, child] of Object.entries(value)) walk(child, `${path}.${key}`);
      }
    };
    walk(view, '$');
    for (const value of strings) {
      // `topologyId` is legitimately `<count>/<scheme>`, so bare '/' is not the
      // tell — module-ish segments and file extensions are.
      expect(value).not.toMatch(
        /\.svelte|\.ts$|\$lib|node_modules|(^|\/)(src|lib|skins|semantic|components-v2)(\/|$)/,
      );
    }
    expect(Object.keys(view)).not.toContain('capabilities');
    // The validator rejects extra keys, so this is belt-and-braces on shape.
    expect(Object.keys(view).sort()).toEqual([
      'activeReceiver', 'disabledReasons', 'dualWatch', 'scope', 'scopeControls', 'split',
      'topologyId', 'txPermit', 'txTarget', 'vfoScheme', 'vfos',
    ]);
  });
});
