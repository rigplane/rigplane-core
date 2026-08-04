/**
 * MOR-1067 — the compiled dual-receiver-cockpit SHELL, mounted end to end
 * against the REAL adapter and REAL semantic surfaces (only the runtime
 * singleton and the TX authority are mocked, same pattern as
 * `components-v2/wiring/__tests__/semantic-rx-tx-wiring.component.test.ts`).
 *
 * Covers the ticket's discriminating requirements: per-receiver VFO strips
 * driven by the dual-receiver topologies, single-receiver exclusion (proved
 * separately at the registry in `presentation/layouts/__tests__/
 * dual-receiver-cockpit.test.ts`), exactly one TX authority surface, the
 * scope/controls/global zones staying inert, and no shell-level fabrication
 * of an active strip. Each test's doc line names the mutation it kills.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  setIntent: vi.fn(),
  resetFault: vi.fn(),
  selectVfo: vi.fn(),
  splitToggle: vi.fn(),
  dualWatchToggle: vi.fn(),
  modInputGuard: { visible: false, sourceLabel: null } as { visible: boolean; sourceLabel: string | null },
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
  },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => h.listeners.delete(listener);
    },
    start: h.start,
    setIntent: h.setIntent,
    release: h.release,
    resetFault: h.resetFault,
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => h.modInputGuard,
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
vi.mock('../../../components-v2/wiring/command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.selectVfo,
    onSplitToggle: h.splitToggle,
    onDualWatchToggle: h.dualWatchToggle,
  }),
}));

import DualReceiverCockpit from '../DualReceiverCockpit.svelte';
// MOR-1068 F6: the manifest's declared zone ids, read through the app-wide
// registration barrel (never '../../../presentation/layouts/
// dual-receiver-cockpit' directly), so the DOM assertions below are checked
// against what the app actually registers rather than a local copy.
import { dualReceiverCockpitLayout } from '../../../presentation/layouts/declarations';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};
const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

/** 2/main_sub: MAIN and SUB each carry A/B slots (4 vfo tiles total). */
function mainSubState(active: 'MAIN' | 'SUB' = 'MAIN'): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const slot = (hz: number) => ({ freqHz: hz, mode: 'USB', filterNum: 1 });
  const receiver = (hz: number) => ({ vfoA: slot(hz), vfoB: slot(hz + 30000), activeSlot: 'A' });
  return {
    active, split: true, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(21295000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
const mainSubCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'], receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/** 2/ab_shared: MAIN and SUB are each a single unslotted VFO (2 vfo tiles total). */
function abSharedState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter'];
  const receiver = (hz: number) => ({ freqHz: hz, mode: 'CW', filter: 1 });
  return {
    active: 'SUB', split: false, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'SUB', slot: null, frequencyHz: 3573000 },
    main: receiver(3573000), sub: receiver(3573000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
/** Same shape as `mainSubState`, minus the 'active' fieldStatus entry — activeReceiver stays unknown. */
function mainSubStateUnknownActive(): ServerState {
  const state = mainSubState();
  const { active: _dropped, ...fieldStatus } = state.fieldStatus as Record<string, unknown>;
  return { ...state, fieldStatus } as unknown as ServerState;
}
const abSharedCaps = (): Capabilities => ({
  ...mainSubCaps(), vfoScheme: 'ab_shared',
} as unknown as Capabilities);

/**
 * MOR-1068 degrade case: 1/single — ONE receiver, one unslotted VFO. The
 * manifest refuses this topology, so resolution sends it to sdr-test
 * (`presentation/layouts/__tests__/cockpit-topology-adaptation.test.ts`); the
 * shell must nonetheless degrade honestly if it is mounted over a
 * single-receiver view model — one strip, no fabricated SUB, and no
 * `secondary-vfo` zone standing empty.
 */
function singleReceiverState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget',
    'main.freqHz', 'main.mode', 'main.filter'];
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: { freqHz: 14195000, mode: 'USB', filter: 1 },
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
/** 1/single caps: no `dual_rx` tag, or the topology derivation contradicts itself. */
const singleReceiverCaps = (): Capabilities => ({
  ...mainSubCaps(), receivers: 1, vfoScheme: 'single', capabilities: ['audio', 'tx'],
} as unknown as Capabilities);

/** The ticket's operational audio-scope condition: scope=false + audioFft=true. */
const audioOnlyScopeCaps = (): Capabilities => ({
  ...mainSubCaps(), audioFftAvailable: true,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(DualReceiverCockpit, { target });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const qa = <T extends HTMLElement>(sel: string) => [...target.querySelectorAll<T>(sel)];

beforeEach(() => {
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.start.mockReset();
  h.release.mockReset();
  h.setIntent.mockReset();
  h.resetFault.mockReset();
  h.selectVfo = vi.fn();
  h.splitToggle = vi.fn();
  h.dualWatchToggle = vi.fn();
  h.modInputGuard = { visible: false, sourceLabel: null };
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('per-receiver VFO strips, driven by the dual-receiver topologies', () => {
  it('2/main_sub: renders one strip per receiver, each holding only that receiver\'s tiles', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    const mainStrip = q('[data-testid="channel-strip-MAIN"]')!;
    const subStrip = q('[data-testid="channel-strip-SUB"]')!;
    expect(mainStrip.querySelectorAll('[data-vfo-tile]')).toHaveLength(2);
    expect(subStrip.querySelectorAll('[data-vfo-tile]')).toHaveLength(2);
    // Kills: strips sharing one unfiltered vfo list instead of splitting.
    expect([...mainStrip.querySelectorAll('[data-vfo-tile]')].every(
      (el) => (el as HTMLElement).dataset.vfoReceiver === 'MAIN',
    )).toBe(true);
    expect([...subStrip.querySelectorAll('[data-vfo-tile]')].every(
      (el) => (el as HTMLElement).dataset.vfoReceiver === 'SUB',
    )).toBe(true);
  });

  it('2/ab_shared: renders one strip per receiver with exactly one tile each', () => {
    h.state = abSharedState();
    h.caps = abSharedCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    expect(q('[data-testid="channel-strip-MAIN"]')!.querySelectorAll('[data-vfo-tile]')).toHaveLength(1);
    expect(q('[data-testid="channel-strip-SUB"]')!.querySelectorAll('[data-vfo-tile]')).toHaveLength(1);
  });

  // Review cycle 1, F1. `2/ab_shared` gives each receiver exactly ONE
  // unslotted VFO, so a strip that gates selection on its own slice sees
  // `vfos.length === 1` and renders the control ABSENT — the operator loses
  // every way to change the active receiver, on a topology this layout
  // declares itself compatible with. The gate must read the whole radio.
  it('2/ab_shared: offers exactly one control, and it selects the NON-active receiver', () => {
    h.state = abSharedState();          // SUB is the active receiver
    h.caps = abSharedCaps();
    render();

    const selects = qa<HTMLButtonElement>('[data-vfo-select]');
    expect(selects).toHaveLength(1);
    // Present, enabled, and living in the strip the operator would switch TO.
    expect(selects[0].disabled).toBe(false);
    expect(q('[data-testid="channel-strip-MAIN"]')!.contains(selects[0])).toBe(true);

    selects[0].click();
    flushSync();
    expect(h.selectVfo).toHaveBeenCalledTimes(1);
    expect(h.selectVfo).toHaveBeenCalledWith('MAIN', null);
  });

  it('marks the positively-observed active receiver\'s strip, and only that one', () => {
    h.state = mainSubState('SUB');
    h.caps = mainSubCaps();
    render();

    expect(q('[data-testid="channel-strip-SUB"]')!.dataset.stripActive).toBe('true');
    expect(q('[data-testid="channel-strip-MAIN"]')!.dataset.stripActive).toBe('false');
  });

  // The mutation this ticket names explicitly: a shell that fabricates an
  // active strip when activeReceiver was never observed.
  it('fabricates NO active strip when activeReceiver is unknown', () => {
    h.state = mainSubStateUnknownActive();
    h.caps = mainSubCaps();
    render();

    expect(q('[data-testid="channel-strip-MAIN"]')!.dataset.stripActive).toBe('false');
    expect(q('[data-testid="channel-strip-SUB"]')!.dataset.stripActive).toBe('false');
  });
});

describe('radio-wide facts render once per cockpit, never once per strip', () => {
  // Review cycle 1, F2. split / dualWatch / activeReceiver are radio-WIDE
  // facts that `forReceiver` (correctly) passes through to every slice, so a
  // naive strip-per-receiver mount renders each of them TWICE: two
  // independent-looking `role="switch"` controls for one radio fact — a
  // duplicated aria-checked pair for assistive tech, and exactly the
  // "duplicated radio behavior" MOR-976 rules out. The shell got the TX half
  // of the one-control-per-radio-wide-authority rule right; this is the
  // split/dual-watch half.
  it.each([
    ['2/main_sub', () => mainSubState('MAIN'), mainSubCaps],
    ['2/ab_shared', abSharedState, abSharedCaps],
  ] as const)('%s: exactly one split switch, one dual-watch switch, one active-receiver line', (
    _topology, makeState, makeCaps,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    expect(qa('[data-vfo-split]')).toHaveLength(1);
    expect(qa('[data-vfo-dual-watch]')).toHaveLength(1);
    expect(qa('[data-testid="vfo-active-receiver"]')).toHaveLength(1);
  });

  // Rendering the radio-wide row once must not cost the operator the control:
  // the surviving switch still reaches the same radio-wide handler.
  it('the single surviving dual-watch switch still reaches the radio-wide handler', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const dualWatch = q<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    expect(dualWatch.disabled).toBe(false);
    dualWatch.click();
    flushSync();
    expect(h.dualWatchToggle).toHaveBeenCalledTimes(1);
  });
});

describe('exactly one authoritative TX action surface across both strips', () => {
  it('renders a single shared RX/TX surface and a single key button', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    expect(qa('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });

  it('unkey stays ungated and reaches the same App TX authority the sdr-test wiring uses', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();
    push({ phase: 'failed', fault: 'audio-failed', guard: { leaseId: 'x' }, mayOwnKey: true });

    const unkey = q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!;
    expect(unkey.disabled).toBe(false);
    unkey.click();
    flushSync();
    expect(h.release).toHaveBeenCalledTimes(1);
  });
});

describe('scope/controls zones — placed, never falsely active', () => {
  // MOR-1068: `global` left this list — it now carries the real radio-wide
  // row (see below), and MOR-1067's F7 note is explicit that `aria-disabled`
  // may only stand while a zone is honest-by-emptiness. A zone holding live
  // switches must gate at the widget, which the switches already do.
  it('both are present, structurally disabled, and hold nothing interactive', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    for (const zone of ['scope', 'controls']) {
      const el = q(`[data-testid="cockpit-zone-${zone}"]`)!;
      expect(el).not.toBeNull();
      expect(el.getAttribute('aria-disabled')).toBe('true');
      expect(el.dataset.zoneActive).toBe('false');
      // "Unsupported surfaces never appear enabled": empty, so there is no
      // widget for the marker to lie about.
      expect(el.querySelectorAll('button, [role="switch"], input')).toHaveLength(0);
    }
  });

  // Kills: keeping `aria-disabled` on the global zone after real content
  // landed in it — MOR-1067 F7. An `aria-disabled` container wrapping enabled
  // switches is precisely the MOR-557 bug class inverted: a live control
  // presenting as dead.
  it('the global zone is NOT marked disabled once it carries the live row', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const global = q('[data-zone-id="global"]')!;
    expect(global).not.toBeNull();
    expect(global.getAttribute('aria-disabled')).toBeNull();
    expect(global.dataset.zoneActive).not.toBe('false');
  });
});

// MOR-1067 verification F6: the manifest declared `primary-vfo` /
// `secondary-vfo` / `rx-tx` while the shell's DOM shared not one id with it,
// and nothing asserted the correspondence. These tests read the zone ids out
// of the REGISTERED manifest and require the rendered tree to expose exactly
// those, in declaration order — the two descriptions can no longer drift.
describe('F6 — manifest zone ids are bound to the rendered structure', () => {
  const declaredZoneIds = (): readonly string[] => dualReceiverCockpitLayout.zones.map((z) => z.id);

  it.each([
    ['2/main_sub', () => mainSubState('MAIN'), mainSubCaps],
    ['2/ab_shared', abSharedState, abSharedCaps],
  ] as const)('%s: renders every declared zone, once, in declaration order', (
    _topology, makeState, makeCaps,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    expect(qa('[data-zone-id]').map((el) => el.dataset.zoneId)).toEqual([...declaredZoneIds()]);
  });

  // Kills: a zone id invented in the DOM that no manifest zone declares (the
  // drift direction that made the shell's five ad-hoc ids invisible to the
  // registry). The inert placeholders are deliberately NOT manifest zones:
  // `LayoutZone` requires at least one semantic surface and MOR-1062/1065
  // ship none for scope/controls.
  it('no rendered zone id is undeclared, and the placeholders claim none', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    for (const el of qa('[data-zone-id]')) {
      expect(declaredZoneIds()).toContain(el.dataset.zoneId);
    }
    for (const zone of ['scope', 'controls']) {
      expect(q(`[data-testid="cockpit-zone-${zone}"]`)!.hasAttribute('data-zone-id')).toBe(false);
    }
  });

  // Kills: binding zone ids to a fixed strip count. `primary-vfo` is the
  // FIRST rendered strip whatever the topology; `secondary-vfo` exists only
  // when a second receiver was actually observed.
  it.each([
    ['2/main_sub', () => mainSubState('MAIN'), mainSubCaps, 'MAIN', 'SUB'],
    ['2/ab_shared', abSharedState, abSharedCaps, 'MAIN', 'SUB'],
  ] as const)('%s: primary-vfo is the first strip, secondary-vfo the second', (
    _topology, makeState, makeCaps, primary, secondary,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    expect(q('[data-zone-id="primary-vfo"]')!.dataset.stripReceiver).toBe(primary);
    expect(q('[data-zone-id="secondary-vfo"]')!.dataset.stripReceiver).toBe(secondary);
    // #5(b): strip ORDER, pinned. Kills a reversed/receiver-sorted render.
    expect(qa('[data-testid^="channel-strip-"]').map((el) => el.dataset.stripReceiver))
      .toEqual([primary, secondary]);
  });
});

describe('degrading to a single-receiver view model', () => {
  // "One compiled layout safely degrades across all pairs; no impossible
  // receiver/VFO labels." Kills: a shell that renders a second, empty strip
  // (or a fabricated SUB label) when only one receiver was observed.
  it('renders one strip, no SUB anywhere, and no secondary-vfo zone', () => {
    h.state = singleReceiverState();
    h.caps = singleReceiverCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(1);
    expect(q('[data-zone-id="primary-vfo"]')!.dataset.stripReceiver).toBe('MAIN');
    expect(q('[data-zone-id="secondary-vfo"]')).toBeNull();
    expect(q('[data-testid="channel-strip-SUB"]')).toBeNull();
    expect(target.textContent).not.toContain('SUB');
  });

  // Kills: degrading by dropping the radio-wide row or the TX authority with
  // the second strip. Single TX authority is non-negotiable in EVERY topology.
  it('keeps exactly one radio-wide row and exactly one TX action surface', () => {
    h.state = singleReceiverState();
    h.caps = singleReceiverCaps();
    render();

    expect(qa('[data-zone-id="global"]')).toHaveLength(1);
    expect(qa('[data-vfo-split]')).toHaveLength(1);
    expect(qa('[data-vfo-dual-watch]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });

  // The declared zone set is a superset here, and that is the honest reading:
  // a zone is rendered when its content exists, never as an empty promise.
  it('renders a strict, declared subset of the manifest zones', () => {
    h.state = singleReceiverState();
    h.caps = singleReceiverCaps();
    render();

    const rendered = qa('[data-zone-id]').map((el) => el.dataset.zoneId);
    expect(rendered).toEqual(['primary-vfo', 'global', 'rx-tx']);
    for (const id of rendered) {
      expect(dualReceiverCockpitLayout.zones.map((z) => z.id)).toContain(id);
    }
  });
});

describe('the radio-wide row lives in the cockpit\'s global zone', () => {
  // MOR-1067 verification #4: the row used to sit inside the FIRST strip, so
  // with SUB active "Active receiver: SUB" rendered in the column WITHOUT the
  // active border. It is radio-wide, so it belongs to no receiver's column.
  // #5(a): its PRESENCE is pinned here — deleting split/dual-watch fails.
  it.each([
    ['2/main_sub', () => mainSubState('SUB'), mainSubCaps],
    ['2/ab_shared', abSharedState, abSharedCaps],
  ] as const)('%s: split, dual-watch and active-receiver render once, outside every strip', (
    _topology, makeState, makeCaps,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    const global = q('[data-zone-id="global"]')!;
    for (const sel of ['[data-vfo-split]', '[data-vfo-dual-watch]', '[data-testid="vfo-active-receiver"]']) {
      expect(qa(sel)).toHaveLength(1);
      expect(global.contains(q(sel)!)).toBe(true);
      // Kills: reintroducing the row inside a strip (the MOR-1067 placement).
      expect(qa('[data-testid^="channel-strip-"]').some((s) => s.contains(q(sel)!))).toBe(false);
    }
    // The global row is facts only — the VFO tiles stay in the strips.
    expect(global.querySelectorAll('[data-vfo-tile]')).toHaveLength(0);
  });

  // Kills: moving the row at the cost of its wiring — the switches must still
  // reach the radio-wide handlers from their new home.
  it('the relocated switches still reach the radio-wide handlers', () => {
    h.state = mainSubState('SUB');
    h.caps = mainSubCaps();
    render();

    q<HTMLButtonElement>('[data-vfo-split]')!.click();
    q<HTMLButtonElement>('[data-vfo-dual-watch]')!.click();
    flushSync();
    expect(h.splitToggle).toHaveBeenCalledTimes(1);
    expect(h.dualWatchToggle).toHaveBeenCalledTimes(1);
  });

  // #5(b): strip accessible NAMING. Three groups mount at once (two strips +
  // the radio-wide row); with one shared generic name assistive tech cannot
  // tell MAIN from SUB, and the ticket's "no impossible receiver/VFO labels"
  // has no a11y half at all.
  it('names each mounted surface distinctly for assistive tech', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const names = qa('[data-testid="vfo-surface"]').map((el) => el.getAttribute('aria-label'));
    expect(names).toHaveLength(3);
    expect(new Set(names).size).toBe(3);
    expect(names.every((n) => (n?.length ?? 0) > 0)).toBe(true);
    expect(names.every((n) => !n?.includes('core.vfo.'))).toBe(true);
    const named = (receiver: string) =>
      q(`[data-testid="channel-strip-${receiver}"] [data-testid="vfo-surface"]`)!
        .getAttribute('aria-label');
    expect(named('MAIN')).toContain('MAIN');
    expect(named('SUB')).toContain('SUB');
  });
});

describe('operational audio-scope availability (scope=false + audioFft=true)', () => {
  // The ticket's fifth, orthogonal condition. The cockpit mounts no scope
  // surface, so the honest behaviour is to make NO claim either way: the
  // rendered tree must be identical with and without a live audio-FFT scope,
  // and the inert scope placeholder must not turn into an "unavailable"
  // verdict about a capability that IS available. Kills: gating any strip,
  // switch or TX control on scope state.
  /** The RX/TX surface's aria-describedby carries a per-instance counter. */
  const markup = (): string => target.innerHTML.replace(/rx-tx-\d+/g, 'rx-tx-N');

  it('changes nothing in the cockpit, and denies nothing about the radio', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();
    const withoutAudioFft = markup();

    if (component) unmount(component);
    document.body.innerHTML = '';
    h.state = mainSubState('MAIN');
    h.caps = audioOnlyScopeCaps();
    render();

    expect(markup()).toBe(withoutAudioFft);
    expect(qa('[data-testid="rx-tx-key"]')).toHaveLength(1);
    expect(q('[data-testid="cockpit-zone-scope"]')!.textContent).toBe('');
  });
});

function push(next: Partial<Snapshot>): void {
  h.snapshot = { ...(h.snapshot as Snapshot), ...next };
  for (const listener of h.listeners) listener(h.snapshot);
  flushSync();
}
