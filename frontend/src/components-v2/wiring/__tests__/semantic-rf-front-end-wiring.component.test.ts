/**
 * MOR-1306 — the semantic RF-front-end surface wired into
 * `SemanticRadioSurfaces`.
 *
 * `semantic/__tests__/RfFrontEndSurface.test.ts` proves what the surface does
 * with a view model. This file proves what only the composed tree can prove:
 *
 *   (a) every intent reaches its OWN mapped `makeRfFrontEndHandlers` spy,
 *       none cross-wired to a neighbor — mirrors
 *       `semantic-tx-aux-wiring.component.test.ts`'s own "every intent
 *       reaches its own command-bus handler" section, and `../command-bus` is
 *       mocked wholesale for the same reason that file mocks it: the real
 *       `makeRfFrontEndHandlers` reads/writes the LEGACY `$lib/stores/
 *       radio.svelte` singleton (`getRadioState`/`patchActiveReceiver`), a
 *       different seam than `runtime.state` — agreement between the real
 *       module and this file's names is a name/arity fact, already covered
 *       by `stub-export-parity.test.ts` and TypeScript itself (the real
 *       factory is imported for its type in the toggle-flip test below);
 *   (b) THE MOUNTING CANON (MOR-1304 ruling): the surface mounts through
 *       `zoned(...)` in the SINGLE composition only, and is ABSENT — zoned or
 *       unzoned — from the DUAL composition, with a view model that actually
 *       carries the group (a fixture that cannot see the surface is the bug
 *       being fixed, not a pass). Mirrors
 *       `semantic-rx-audio-wiring.component.test.ts`'s own pin exactly, per
 *       the ticket brief's option (i);
 *   (c) the default path stays byte-identical for a radio with no RF-front-end
 *       capability at all;
 *   (d) the FLIPPED-value contract between the surface and the toggle wiring
 *       (`RfFrontEndSurface.svelte`'s `onToggle`) reaches the real
 *       `onDigiSelToggle`/`onIpPlusToggle` `(on: boolean)` signature, not the
 *       argument-less vox/comp/mon shape `TxAuxSurface` composes.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine —
 * no `vite.config.ts` edit was needed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  noop: vi.fn(),
  att: vi.fn(),
  pre: vi.fn(),
  rfGain: vi.fn(),
  squelch: vi.fn(),
  digiSel: vi.fn(),
  ipPlus: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
  },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => { h.listeners.delete(listener); };
    },
    start: vi.fn(), setIntent: vi.fn(), release: vi.fn(), resetFault: vi.fn(),
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
// The real module's names/arities are covered by `stub-export-parity.test.ts`
// and by TypeScript; this file only proves ROUTING, mirroring
// `semantic-tx-aux-wiring.component.test.ts`'s own wholesale mock.
vi.mock('../command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.noop, onSplitToggle: h.noop, onDualWatchToggle: h.noop,
  }),
  makeVoxHandlers: () => ({
    onVoxToggle: h.noop, onVoxGainChange: h.noop, onAntiVoxGainChange: h.noop, onVoxDelayChange: h.noop,
  }),
  makeTxHandlers: () => ({
    onRfPowerChange: h.noop, onMicGainChange: h.noop, onAtuToggle: h.noop, onAtuTune: h.noop,
    onVoxToggle: h.noop, onCompToggle: h.noop, onCompLevelChange: h.noop, onMonToggle: h.noop,
    onMonLevelChange: h.noop, onDriveGainChange: h.noop,
  }),
  makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
  makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
  // MOR-1304/MOR-1305 — the wiring's module scope also composes the
  // filter/dsp intent vocabularies unconditionally; without stubs here the
  // wiring's `makeFilterHandlers()`/`makeDspHandlers()`/`makeAgcHandlers()`
  // calls throw before this file's own RF-front-end assertions ever run.
  makeModeHandlers: () => ({
    onModInputChange: h.noop, onModeChange: h.noop, onDataModeChange: h.noop,
  }),
  makeFilterHandlers: () => ({
    onFilterChange: h.noop, onFilterWidthChange: h.noop, onFilterShapeChange: h.noop,
    onIfShiftChange: h.noop, onPbtInnerChange: h.noop, onPbtOuterChange: h.noop,
  }),
  makeDspHandlers: () => ({
    onNrModeChange: h.noop, onNrLevelChange: h.noop, onNbToggle: h.noop,
    onNbLevelChange: h.noop, onNbDepthChange: h.noop, onNbWidthChange: h.noop,
    onNotchModeChange: h.noop, onNotchFreqChange: h.noop,
    onManualNotchWidthChange: h.noop, onAgcTimeChange: h.noop,
  }),
  makeAgcHandlers: () => ({ onAgcModeChange: h.noop }),
  makeRfFrontEndHandlers: () => ({
    onAttChange: h.att, onPreChange: h.pre, onRfGainChange: h.rfGain,
    onSquelchChange: h.squelch, onDigiSelToggle: h.digiSel, onIpPlusToggle: h.ipPlus,
  }),
  // MOR-1307 slice 7B: the band-select intent the band surface composes.
  // This fixture declares no band capability, so it is never reachable —
  // same stand-in role as the noop handlers above.
  makeBandHandlers: () => ({ onBandSelect: h.noop }),
  // MOR-1309 slice 8C: the wiring's module scope also composes the antenna
  // intent vocabulary unconditionally; without a stub here the wiring's
  // `makeAntennaHandlers()` call throws before this file's own RF-front-end
  // assertions ever run. This fixture declares no antenna capability, so
  // none of these is reachable — same stand-in role as `makeBandHandlers`.
  makeAntennaHandlers: () => ({ onSelectAnt1: h.noop, onSelectAnt2: h.noop, onToggleRxAnt: h.noop }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** Every rfFrontEnd raw field the MOR-1292/1293 adapter reads, all observed fresh. */
const RF_FRONT_END_STATE = { preamp: 1, att: 6, rfGain: 0.8, squelch: 0.1, digisel: false, ipplus: false };
const RF_FRONT_END_PATHS = ['preamp', 'att', 'rfGain', 'squelch', 'digisel', 'ipplus'];

function liveState(withRfFrontEnd: boolean): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    if (withRfFrontEnd) paths.push(...RF_FRONT_END_PATHS.map((p) => `${rx}.${p}`));
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
    ...(withRfFrontEnd ? RF_FRONT_END_STATE : {}),
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (withRfFrontEnd: boolean): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: withRfFrontEnd
    ? ['audio', 'tx', 'dual_rx', 'preamp', 'attenuator', 'rf_gain', 'squelch', 'digisel', 'ip_plus']
    : ['audio', 'tx', 'dual_rx'],
  preValues: [0, 1, 2], attValues: [0, 6, 12, 18],
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const el = (id: string) => q<HTMLElement>(`[data-testid="rf-front-end-${id}"]`);

beforeEach(() => {
  h.state = liveState(true);
  h.caps = liveCaps(true);
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  for (const value of Object.values(h)) {
    if (typeof value === 'function' && 'mockReset' in value) (value as ReturnType<typeof vi.fn>).mockReset();
  }
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

/* ── (a)+(d) each intent reaches its own mapped handler ─────────── */

describe('every rfFrontEnd intent reaches its own command-bus handler, none cross-wired', () => {
  const ALL = [h.att, h.pre, h.rfGain, h.squelch, h.digiSel, h.ipPlus];

  it('routes the preamp choice to onPreChange, verbatim', () => {
    render();
    el('preamp-2')!.click();
    flushSync();
    expect(h.pre).toHaveBeenCalledExactlyOnceWith(2);
    for (const other of ALL.filter((s) => s !== h.pre)) expect(other).not.toHaveBeenCalled();
  });

  it('routes the attenuator choice to onAttChange, verbatim', () => {
    render();
    el('attenuator-18')!.click();
    flushSync();
    expect(h.att).toHaveBeenCalledExactlyOnceWith(18);
    for (const other of ALL.filter((s) => s !== h.att)) expect(other).not.toHaveBeenCalled();
  });

  it('routes the RF-gain slider to onRfGainChange, verbatim', () => {
    render();
    const input = el('rfGain')!.querySelector('input')!;
    input.value = '0.55';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(0.55);
    for (const other of ALL.filter((s) => s !== h.rfGain)) expect(other).not.toHaveBeenCalled();
  });

  it('routes the squelch slider to onSquelchChange, verbatim', () => {
    render();
    const input = el('squelch')!.querySelector('input')!;
    input.value = '0.2';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(0.2);
    for (const other of ALL.filter((s) => s !== h.squelch)) expect(other).not.toHaveBeenCalled();
  });

  // (d): `onDigiSelToggle`/`onIpPlusToggle` take an explicit `on: boolean` —
  // the wiring must pass the FLIPPED value the surface computed, not call
  // with no argument (the vox/comp/mon toggle shape) and not the CURRENT value.
  it('routes DIGI-SEL to onDigiSelToggle with the FLIPPED boolean', () => {
    render();
    el('digiSel')!.click();
    flushSync();
    expect(h.digiSel).toHaveBeenCalledExactlyOnceWith(true);
    for (const other of ALL.filter((s) => s !== h.digiSel)) expect(other).not.toHaveBeenCalled();
  });

  it('routes IP+ to onIpPlusToggle with the FLIPPED boolean', () => {
    render();
    el('ipPlus')!.click();
    flushSync();
    expect(h.ipPlus).toHaveBeenCalledExactlyOnceWith(true);
    for (const other of ALL.filter((s) => s !== h.ipPlus)) expect(other).not.toHaveBeenCalled();
  });
});

/* ── (b) THE MOUNTING CANON ──────────────────────────────────────── */

describe('the surface mounts only when the view model carries the group', () => {
  it('renders no rf-front-end surface for a radio with no RF-front-end capability', () => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render();
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('rf-front-end');
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render();
    const surface = el('surface')!;
    expect(surface).not.toBeNull();
    expect(surface.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * MOR-1304 mounting canon, applied per the ticket brief's option (i): a
   * control-bearing surface with no declared cockpit zone must be ABSENT from
   * the dual composition, not mounted bare. Caps here positively declare
   * every rfFrontEnd sub-capability, so this is not the `mainSubCaps()` blind
   * spot the MOR-1304 verify round documented for 4B — the group genuinely IS
   * present, and the surface must still not appear.
   */
  it('renders NO rf-front-end surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('rf-front-end');
  });

  it('leaves the cockpit with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });

  it('never changes with the App TX authority or the raw transmit bit', () => {
    render();
    const before = el('surface')!.outerHTML;
    h.snapshot = { ...IDLE, phase: 'transmitting', radioTx: 'on', mayOwnKey: true };
    for (const listener of h.listeners) listener(h.snapshot);
    h.state = liveState(true);
    (h.state as unknown as { ptt: boolean }).ptt = true;
    flushSync();
    expect(el('surface')!.outerHTML).toBe(before);
  });
});
