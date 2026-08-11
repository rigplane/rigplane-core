/**
 * MOR-1307 — the semantic band surface wired into `SemanticRadioSurfaces`.
 *
 * `semantic/__tests__/BandSurface.test.ts` proves what the surface does with a
 * view model. This file proves what only the composed tree can prove, using the
 * REAL command bus, the REAL adapter and the REAL surface — only the transport
 * and runtime SEAMS are spied:
 *
 *   (a) THE MOUNTING CANON. The surface is control-bearing (band buttons, a
 *       frequency field, a Set button) and no manifest declares a `band` zone,
 *       so it must NOT appear in the dual composition — and the pin renders the
 *       dual composition with caps that DO emit the group, because a fixture
 *       that cannot see the surface is the bug, not the proof (MOR-1304 §1).
 *   (b) THE WRONG-VFO CLASS (MOR-1322 B1). Every intent this surface can fire
 *       lands on the ACTIVE receiver. The no-BSR band fallback in particular
 *       must NOT take `makeBandHandlers`' own `receiver: 0` path while SUB is
 *       active.
 *   (c) The intents are the SHIPPED command vocabulary, not a v3 fork.
 *   (d) The wiring's own active-receiver guard is independent of the surface's.
 *   (e) The structural gate: a radio with no declared frequency range renders
 *       the pre-1307 element shape exactly.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
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
  audio: { muted: false, rxEnabled: true, volume: 42 },
  audioConnected: true,
  rxEnabled: true,
  listeners: new Set<(next: unknown) => void>(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return {
    dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params),
    currentControlSessionEpoch: () => 0,
  };
});
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: vi.fn(() => h.state),
  getActiveReceiver: vi.fn(() => {
    const state = h.state as ServerState | null;
    return state?.active === 'SUB' ? state.sub ?? null : state?.main ?? null;
  }),
  patchActiveReceiver: vi.fn(), patchRadioState: vi.fn(), patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  capabilitiesMatchGeneration: vi.fn(() => true),
  getCapabilities: vi.fn(() => h.caps),
  getControlRange: vi.fn(() => null),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return h.rxEnabled; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    get rxEnabled() { return h.rxEnabled; },
    setVolume: vi.fn(), setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(),
    // MOR-1312 slice 12B (rebase fix): the wiring now also hands the adapter
    // a scope-display snapshot (the FIFTH argument). This file tests band, so
    // this stays on its pre-1312 path regardless of these values.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime', async () => ({
  runtime: (await import('$lib/runtime/frontend-runtime')).runtime,
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
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { makeBandHandlers, makeVfoHandlers } from '$lib/runtime/commands/panel-commands';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** MAIN sits at 14.250 (inside the 20m TX segment), SUB at 7.100 (inside 40m). */
function liveState(over: Partial<ServerState> = {}): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget', 'dataOffModInput'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false, dataOffModInput: 5,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(7100000),
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

/** `freqRanges` is the band group's WHOLE evidence gate — the cockpit
 *  fixture's `freqRanges: []` is exactly the blindness MOR-1304 §1 recorded,
 *  so this fixture declares a real plan and the dual-absence pin is real. */
const BAND_PLAN = [{
  start: 30000, end: 60000000,
  bands: [
    { name: '40m', start: 7000000, end: 7300000, default: 7100000, bsrCode: 2 },
    { name: '20m', start: 14000000, end: 14350000, default: 14195000, bsrCode: 5 },
    // No BSR — the `set_freq` fallback path, i.e. the wrong-VFO hazard.
    { name: 'MW', start: 520000, end: 1710000, default: 1000000 },
  ],
}];

const liveCaps = (freqRanges: unknown[]): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'], audioTxRequiredModInputSource: 5,
  receivers: 2, vfoScheme: 'main_sub', freqRanges, modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [
    { start: 7000000, end: 7200000, name: '40m' },
    { start: 14000000, end: 14350000, name: '20m' },
  ],
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
const el = (id: string) => q<HTMLElement>(`[data-testid="band-${id}"]`);
const btn = (id: string) => q<HTMLButtonElement>(`[data-testid="band-${id}"]`);
const setFreqCalls = () => vi.mocked(sendCommand).mock.calls.filter(([n]) => n === 'set_freq');

function typeFrequency(value: string): void {
  const input = q<HTMLInputElement>('[data-testid="band-entry-input"]')!;
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  flushSync();
}

beforeEach(() => {
  h.state = liveState();
  h.caps = liveCaps(BAND_PLAN);
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  vi.mocked(sendCommand).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

/* ── (a) THE MOUNTING CANON ─────────────────────────────────────── */

describe('the band surface obeys the zone-mount canon (MOR-1069 / MOR-1304)', () => {
  it('renders it bare in the single composition, outside every zone', () => {
    render();
    expect(el('surface')).not.toBeNull();
    expect(el('surface')!.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * THE DUAL-ABSENCE PIN. MUTATION KILLED: mounting this surface in the dual
   * composition — bare, or through `zoned()`, which renders bare anyway while
   * no manifest declares a `band` zone (`zoneOwning()` answers `null`). The
   * view model here DOES carry the band group (asserted below via the single
   * composition), so this is not the vacuous green MOR-1304 §1 warned about.
   */
  it('renders NO band surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('band-surface');
    expect(target.innerHTML).not.toContain('band-entry');
  });

  it('proves the same view model DOES carry the band group, so the pin is not vacuous', () => {
    render();
    expect(el('surface')).not.toBeNull();
    expect(el('choice-20m')).not.toBeNull();
  });

  it('leaves the cockpit with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });
});

/* ── (e) the structural gate ────────────────────────────────────── */

describe('the surface mounts only when the view model carries the group', () => {
  it('renders no band surface for a radio that declares no frequency range', () => {
    h.caps = liveCaps([]);
    render();
    expect(el('surface')).toBeNull();
  });
});

/* ── (b) the wrong-VFO dispatch class ───────────────────────────── */

describe('every band intent lands on the ACTIVE receiver (MOR-1322 B1 class)', () => {
  // MUTATION KILLED: the no-BSR fallback left to `makeBandHandlers`, whose own
  // `else` branch sends `set_freq {receiver: 0}` — MAIN — regardless of which
  // receiver is active. With SUB active that tunes the wrong VFO.
  it('routes a no-BSR band pick to the SUB receiver while SUB is active', () => {
    h.state = liveState({ active: 'SUB' } as Partial<ServerState>);
    render();
    btn('choice-MW')!.click();
    flushSync();
    expect(setFreqCalls()).toEqual([['set_freq', { freq: 1000000, receiver: 1 }]]);
  });

  it('routes the same pick to MAIN while MAIN is active', () => {
    render();
    btn('choice-MW')!.click();
    flushSync();
    expect(setFreqCalls()).toEqual([['set_freq', { freq: 1000000, receiver: 0 }]]);
  });

  // MUTATION KILLED: forking the BSR path instead of using the shipped handler.
  it('routes a band WITH a stacking register to the shipped set_band command', () => {
    render();
    btn('choice-20m')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_band', { band: 5 });
  });

  it('routes a typed frequency to the active receiver', () => {
    h.state = liveState({ active: 'SUB' } as Partial<ServerState>);
    render();
    typeFrequency('7150000');
    btn('entry-set')!.click();
    flushSync();
    expect(setFreqCalls()).toEqual([['set_freq', { freq: 7150000, receiver: 1 }]]);
  });
});

/*
 * ── MOR-1425 review round 2 (B1 residual) ──────────────────────────────
 *
 * `tuneFrequency` (the shared per-receiver dispatcher behind BOTH the
 * digit-tuning path and this surface's two absolute sources) used to funnel
 * everything through the unconditional `step()` accumulate path. A hot
 * digit-tuning burst on the active receiver (VfoSurface's wheel/arrow
 * tuning, elsewhere in the composed tree) left a live pending-target window
 * open; a typed frequency entry or a bandless band pick landing inside that
 * window was treated as ANOTHER relative step and accumulated onto the
 * stale pending target instead of landing at the exact value the operator
 * asked for — reproduced live as "5 wheel ticks then typing 7_100_000 →
 * emitted 7_105_000" and "3 ticks then a bandless band select → 3 kHz off".
 * `enterFrequency`/`selectBand`'s bandless fallback now route through
 * `tuneFrequency(..., 'jump')`, which clears the burst and emits the exact
 * target immediately, unpaced (`tuning-accumulator.ts`'s `jump()`).
 *
 * Fake timers: the accumulator's own pacing (`quietWindowMs`/`paceMs`)
 * needs a controllable clock so the burst stays provably HOT for the whole
 * test and a stray late flush can be proven absent, same pattern as
 * `panel-commands.intent.isolated.test.ts`'s MOR-1425 burst tests.
 */
describe('absolute band intents land exactly, unpaced, even mid a hot digit-tuning burst (MOR-1425 review round 2, B1 residual)', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('a typed frequency entry (BandSurface) lands EXACTLY, immediately, with no accumulated offset', () => {
    render();
    const vfo = makeVfoHandlers();
    const confirmed = 14250000; // MAIN's confirmed freq (liveState default; MAIN is active)
    // Prime a HOT digit-tuning burst on MAIN: the first gesture is a cold
    // start (emits immediately), the second lands inside the quiet window
    // and is held, paced — exactly the "wheel toward a signal" shape.
    vfo.onMainFreqChange(confirmed + 1_000);
    vfo.onMainFreqChange(confirmed + 1_000);
    expect(setFreqCalls()).toEqual([['set_freq', { freq: confirmed + 1_000, receiver: 0 }]]);

    typeFrequency('7100000');
    btn('entry-set')!.click();
    flushSync();

    // The typed ABSOLUTE target must land EXACTLY and IMMEDIATELY — not
    // folded into the still-hot burst's accumulated target (the
    // 7_105_000-style offset), and not held for the burst's own pace timer.
    expect(setFreqCalls().at(-1)).toEqual(['set_freq', { freq: 7100000, receiver: 0 }]);

    // The jump clears the burst's pending flush — advancing past its pace
    // window must never emit a stray third call.
    vi.advanceTimersByTime(60);
    expect(setFreqCalls()).toHaveLength(2);
  });

  it('a bandless band select lands EXACTLY, immediately, with no accumulated offset', () => {
    render();
    const vfo = makeVfoHandlers();
    const confirmed = 14250000;
    vfo.onMainFreqChange(confirmed + 1_000);
    vfo.onMainFreqChange(confirmed + 1_000);
    expect(setFreqCalls()).toEqual([['set_freq', { freq: confirmed + 1_000, receiver: 0 }]]);

    // 'MW' carries no BSR code (see BAND_PLAN above) — the bandless
    // `set_freq` fallback, the same absolute-default gesture reproduced
    // live as "3 ticks then bandless band select → 3 kHz off".
    btn('choice-MW')!.click();
    flushSync();

    expect(setFreqCalls().at(-1)).toEqual(['set_freq', { freq: 1000000, receiver: 0 }]);

    vi.advanceTimersByTime(60);
    expect(setFreqCalls()).toHaveLength(2);
  });
});

/* ── (d) the wiring's own guard, independent of the surface's ───── */

describe('the wiring refuses a receiver-scoped write with no known active receiver', () => {
  /** `active` unobserved ⇒ `activeReceiver` is `{status:'unknown'}`. The band
   *  group still derives (its gate is `freqRanges`), so the surface renders. */
  const unobservedActive = () => {
    const state = liveState() as unknown as Record<string, unknown>;
    const status = { ...(state.fieldStatus as Record<string, unknown>) };
    delete status.active;
    h.state = { ...state, active: undefined, fieldStatus: status } as unknown as ServerState;
  };

  // MUTATION KILLED: dropping the wiring-side guard. The surface's `disabled`
  // is bypassed here, so only the wiring guard (and the surface's own handler
  // guard, pinned separately in the surface test) can stop the dispatch.
  it('sends nothing when a band pick is fired past the disabled attribute', () => {
    unobservedActive();
    render();
    expect(el('surface')).not.toBeNull();
    expect(btn('choice-20m')!.disabled).toBe(true);
    btn('choice-20m')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('sends nothing when a typed frequency is fired past the disabled attribute', () => {
    unobservedActive();
    render();
    btn('entry-set')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
  });
});

/* ── (c) the shipped vocabulary, and no TX authority ────────────── */

describe('the band surface composes the shipped command vocabulary', () => {
  it('composes the shipped band and VFO factories', () => {
    for (const [factory, names] of [
      [makeBandHandlers, ['onBandSelect']],
      [makeVfoHandlers, ['onMainFreqChange', 'onSubFreqChange']],
    ] as const) {
      const handlers = factory() as Record<string, unknown>;
      for (const name of names) expect(typeof handlers[name]).toBe('function');
    }
  });

  // MUTATION KILLED: the surface taking a TX-authority snapshot or growing a
  // key path. The permit it renders is a FACT, already decided by the adapter.
  it('never changes with the App TX authority or the raw transmit bit', () => {
    render();
    const before = el('surface')!.outerHTML;
    h.snapshot = { ...IDLE, phase: 'transmitting', radioTx: 'on', mayOwnKey: true };
    for (const listener of h.listeners) listener(h.snapshot);
    h.state = liveState({ ptt: true } as Partial<ServerState>);
    flushSync();
    expect(el('surface')!.outerHTML).toBe(before);
  });

  it('mounts and renders the surface without sending a single command', () => {
    render();
    expect(el('surface')).not.toBeNull();
    expect(sendCommand).not.toHaveBeenCalled();
  });
});

/* ── the live permit, end to end through the real adapter ───────── */

describe('the rendered TX permit is the LIVE-frequency answer (7B carry-forward 1/F3)', () => {
  /**
   * THE FAIL-OPEN REGRESSION PIN, end to end. MAIN sits at 7.250 MHz: inside
   * the 40m band-plan band (7.000–7.300) but OUTSIDE the 40m TX segment
   * (7.000–7.200). The band's DEFAULT frequency (7.100) is inside that segment,
   * so a surface reading `defaultHzTxPermit` would tell the operator "allowed"
   * at a frequency where transmission is not permitted.
   */
  it('reads denied at a live frequency outside a narrower TX segment', () => {
    h.state = liveState({ main: { ...slot(7250000), vfoA: slot(7250000), activeSlot: 'A' } } as
      unknown as Partial<ServerState>);
    render();
    expect(el('current-value')!.textContent!.trim()).toBe('40m');
    expect(el('choice-permit-40m')!.textContent).toContain('allowed');
    expect(el('tx-value')!.textContent!.trim()).toBe('denied');
  });

  it('reads allowed once the live frequency is inside the segment', () => {
    render();
    expect(el('current-value')!.textContent!.trim()).toBe('20m');
    expect(el('tx-value')!.textContent!.trim()).toBe('allowed');
  });
});

/* ── the caveat, end to end through the real adapter's two-permit split
   (fix-round F1) ────────────────────────────────────────────────────── */

describe('a split TX target surfaces the caveat, end to end (fix-round F1)', () => {
  /**
   * THE F1 REGRESSION PIN, end to end. MAIN (the ACTIVE receiver) sits at
   * 14.250 — inside the 20m TX segment, so `band.currentBandTx` (scoped to
   * the active receiver) reads `allowed`. But the TX TARGET is SUB, split to
   * 7.250 — inside the 40m band-plan band but OUTSIDE the 40m TX segment
   * (7.000-7.200) — so the authoritative `view.txPermit` (`tx-capabilities.ts`,
   * keyed off `txTarget.frequencyHz`) reads `denied`. A surface that shows
   * only `band.currentBandTx` renders an unqualified "TX HERE: allowed"
   * while the radio would refuse to key. The caveat must be visible.
   */
  it('shows TX HERE: allowed but a denied TX-target caveat under split', () => {
    h.state = liveState({
      split: true,
      sub: { ...slot(7250000), vfoA: slot(7250000), activeSlot: 'A', filter: 1 },
      txTarget: { status: 'known', receiver: 'SUB', slot: 'A', frequencyHz: 7250000 },
    } as unknown as Partial<ServerState>);
    render();
    expect(el('tx-value')!.textContent!.trim()).toBe('allowed');
    expect(el('tx-caveat')).not.toBeNull();
    expect(el('tx-caveat')!.textContent).toContain('denied');
  });
});
