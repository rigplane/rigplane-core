/**
 * MOR-1310 — the semantic CW-keyer surface wired into `SemanticRadioSurfaces`.
 *
 * SAFETY-CRITICAL. `semantic/__tests__/CwKeyerSurface.test.ts` proves what the
 * surface does with a view model. This file proves the things only the composed
 * tree can prove, and it uses the REAL command bus, the REAL adapter and the
 * REAL surface — only the transport/runtime/authority SEAMS are spied:
 *
 *   (a) NO KEY PATH. With everything ARMED — break-in structurally available,
 *       `txPermit` `allowed`, every control enabled — exercising EVERY control
 *       on the surface must produce only the legal SETTING commands and ZERO
 *       commands of the key/unkey class (`ptt`, `cw_auto_tune`,
 *       `set_tuner_status`). Exactly one `<RxTxSurface>` remains the key/unkey
 *       authority (MOR-1262 decomposition R9), and it is untouched here.
 *   (b) The break-in gate survives the real adapter: a radio whose TX target is
 *       unobserved (permit `unknown`) reaches the surface with the control
 *       disabled and the recorded reason rendered — the deliberate fail-closed
 *       over-disable of MOR-1296 O2.
 *   (c) NO SECOND PERMIT: the whole composed tree resolves break-in from the
 *       ONE `txPermit`; moving the radio out of band flips the gate with no
 *       band-plan lookup anywhere in the CW path.
 *   (d) Receiver-scoped intents (APF, TPF) target the ACTIVE VFO — the facts
 *       and the commands must name the same receiver.
 *   (e) MOUNTING: the surface is control-bearing and no manifest declares a
 *       `cwKeyer` zone, so it renders in the SINGLE composition only. The dual
 *       composition must not grow it — asserted with a view model that DOES
 *       carry the group, because a fixture that cannot see the surface would
 *       reproduce the very hole the MOR-1304 ruling was written about.
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
  txStart: vi.fn(),
  txRelease: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
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
    start: h.txStart, setIntent: vi.fn(), release: h.txRelease, resetFault: vi.fn(),
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'LAN' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

/**
 * The command names that KEY or cause a carrier. Not one of these may leave
 * this surface, in any state, through any control. `set_tuner_status` is here
 * because value 2 starts an ATU tune cycle (the MOR-1244 carrier), and
 * `cw_auto_tune` is `CwPanel`'s own transmit-causing button.
 */
const KEY_CLASS_COMMANDS = ['ptt', 'cw_auto_tune', 'set_tuner_status'] as const;

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number, mode: string) => ({ freqHz, mode, filterNum: 1, dataMode: 0 });

/**
 * Every CW fact observed, on both receivers, with a TX target inside 20m.
 * `mode` is a parameter because the APF/TPF mutex consumes
 * `modeFilter.currentMode`: APF is live only in CW/CW-R, TPF only in
 * RTTY/RTTY-R, and the two can therefore never be enabled at once (MOR-1296
 * O1 — the reason this "CW" group is not uniformly CW).
 */
function liveState(over: Partial<ServerState> = {}, mode = 'CW'): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget', 'breakIn', 'breakInDelay', 'keySpeed',
    'cwPitch', 'dashRatio',
  ];
  for (const rx of ['main', 'sub']) {
    paths.push(
      `${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`,
      `${rx}.apfTypeLevel`, `${rx}.twinPeakFilter`,
    );
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number, apfTypeLevel: number) => ({
    ...slot(hz, mode), vfoA: slot(hz, mode), vfoB: slot(hz + 50000, mode), activeSlot: 'A',
    filter: 1, apfTypeLevel, twinPeakFilter: false,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    breakIn: 1, breakInDelay: 64, keySpeed: 24, cwPitch: 600, dashRatio: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000, 0), sub: receiver(14300000, 2),
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (tags: readonly string[]): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: tags, receivers: 2, vfoScheme: 'main_sub', freqRanges: [],
  // A radio that declares no modes is not a radio that exists, and the
  // APF/TPF mutex reads `modeFilter.currentMode` — an empty `modes` list makes
  // the mode fact absent and BOTH controls fail closed, hiding this slice's
  // behaviour behind a fixture hole (the MOR-1304 N2 lesson).
  modes: ['CW', 'CW-R', 'RTTY', 'USB'], filters: [1, 2, 3],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/** A full CW radio: keyer, break-in, APF and the twin-peak filter. */
const CW_TAGS = ['tx', 'cw', 'break_in', 'apf', 'twin_peak'] as const;
/** No `cw` tag ⇒ the MOR-1296 evidence gate declines and no group is emitted. */
const NO_CW_TAGS = ['tx'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const el = (id: string) => q<HTMLElement>(`[data-testid="cw-keyer-${id}"]`);
const press = (node: HTMLElement) => node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
const commands = () => vi.mocked(sendCommand).mock.calls.map(([name]) => name);

/**
 * The runtime seam and the radio STORE are two views of one state in
 * production (`FrontendRuntime` wraps the store the command bus reads). The
 * mocks split them, so this helper sets BOTH from one object — otherwise the
 * receiver-scoping pins below would compare a fact from one source against a
 * command param from the other and prove nothing about production.
 */
function useState(state: ServerState): void {
  h.state = state;
  // Reset first: `setRadioState` ignores a state whose revision has not
  // advanced, and these fixtures carry no revision counter at all.
  resetRadioState();
  setRadioState(state);
}

beforeEach(() => {
  useState(liveState());
  h.caps = liveCaps(CW_TAGS);
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  vi.mocked(sendCommand).mockClear();
  h.txStart.mockClear();
  h.txRelease.mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

/* ── (a) THE NO-KEY-PATH PIN ───────────────────────────────────── */

describe('the CW surface never becomes a second key path (decomposition R9)', () => {
  // MUTATION KILLED: any module in the closure keying at import time.
  it('mounts the armed tree and sends no command at all', () => {
    render();
    expect(el('surface')).not.toBeNull();
    expect(el('break-in-full')!.hasAttribute('disabled')).toBe(false);
    expect(sendCommand).not.toHaveBeenCalled();
    expect(h.txStart).not.toHaveBeenCalled();
  });

  /**
   * THE PIN. Everything armed (break-in structurally available, permit
   * `allowed`, every fact observed), EVERY control on the surface interacted
   * with — including the ones the APF/TPF mutex disables, driven past
   * `disabled` with a real bubbling click — and the bus must see only the
   * legal setting commands.
   *
   * Run in both mutex halves because APF (CW/CW-R) and TPF (RTTY/RTTY-R) can
   * never be live at once, so one render cannot exercise both live.
   *
   * MUTATION KILLED: wiring a key intent onto ANY control here (`cmd('ptt')`,
   * `tx.start(...)`, `onAutoTune`, an ATU tune). Sliders are driven to their
   * maximum, so even a "key at full scale" mutant is exercised.
   */
  it.each([
    ['CW', ['set_apf', 'set_apf'], 'cw-keyer-twin-peak-toggle'],
    ['RTTY', ['set_twin_peak'], 'cw-keyer-apf-off'],
  ] as const)('in %s mode sends only SETTING commands with every control exercised', (
    mode, filterCommands, mutexedTestId,
  ) => {
    useState(liveState({}, mode));
    render();
    const surface = el('surface')!;
    // The mutexed control really is disabled — otherwise the sweep below would
    // be exercising a live control and this pin would prove less than it says.
    expect(surface.querySelector(`[data-testid="${mutexedTestId}"]`)!.hasAttribute('disabled'))
      .toBe(true);

    const controls = [...surface.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')];
    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      if (control instanceof HTMLInputElement) {
        control.value = control.max;
        control.dispatchEvent(new Event('input', { bubbles: true }));
      } else press(control);
    }
    flushSync();

    expect(commands()).toEqual([
      'set_break_in', 'set_break_in', 'set_break_in',
      'set_key_speed', 'set_cw_pitch', 'set_break_in_delay',
      'set_dash_ratio', ...filterCommands,
    ]);
    for (const forbidden of KEY_CLASS_COMMANDS) expect(commands()).not.toContain(forbidden);
    // The App TX authority is never asked for a lease either — the ONE
    // `<RxTxSurface>` above is untouched by everything this surface does.
    expect(h.txStart).not.toHaveBeenCalled();
    expect(h.txRelease).not.toHaveBeenCalled();
  });

  // MUTATION KILLED: the surface reacting to the transmit bit or the App TX
  // authority at all. Nothing here is TX truth.
  it('never changes with the App TX authority or the raw transmit bit', () => {
    render();
    const before = el('surface')!.outerHTML;
    h.snapshot = { ...IDLE, phase: 'active', radioTx: 'on', mayOwnKey: true };
    for (const listener of h.listeners) listener(h.snapshot);
    h.state = liveState({ ptt: true } as Partial<ServerState>);
    flushSync();
    expect(el('surface')!.outerHTML).toBe(before);
  });
});

/* ── (b)(c) the break-in gate through the real adapter ─────────── */

describe('break-in is gated on the ONE txPermit, end to end', () => {
  it.each([
    ['an unobserved TX target', { txTarget: { status: 'unknown', reason: 'not-observed' } }, 'tx-target-unknown'],
    ['an out-of-band TX target', { txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 7100000 } }, 'out-of-band'],
  ] as const)('disables every break-in choice under %s', (_label, over, code) => {
    useState(liveState(over as Partial<ServerState>));
    render();
    for (const choice of ['off', 'semi', 'full']) {
      expect(el(`break-in-${choice}`)!.hasAttribute('disabled')).toBe(true);
    }
    expect(el('break-in')!.dataset.permitted).toBe('false');
    expect(el('break-in-blocked')!.dataset.reason).toBe(code);
    expect(el('break-in-blocked')!.textContent).toContain('TX not permitted');
  });

  /**
   * MUTATION KILLED: a second permit derivation, or the `unknown` case being
   * "fixed" to fail open. The handler is driven past `disabled` with a real
   * bubbling click, so this cannot pass on the attribute alone.
   */
  it.each([
    ['unknown', { txTarget: { status: 'unknown', reason: 'not-observed' } }],
    ['denied', { txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 7100000 } }],
  ] as const)('sends no set_break_in under a %s permit, even bypassing disabled', (_l, over) => {
    useState(liveState(over as Partial<ServerState>));
    render();
    for (const choice of ['off', 'semi', 'full']) press(el(`break-in-${choice}`)!);
    flushSync();
    expect(commands()).not.toContain('set_break_in');
  });

  // MUTATION KILLED: a permit read from the band plan / `defaultHz` rather than
  // the live TX target. Only the target frequency moves between these two
  // renders; the band table and every CW fact are identical.
  it('flips the gate on the LIVE TX-target frequency alone', () => {
    render();
    expect(el('break-in-full')!.hasAttribute('disabled')).toBe(false);
    unmount(component!);
    component = null;
    useState(liveState({
      txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14400000 },
    } as unknown as Partial<ServerState>));
    render();
    expect(el('break-in-full')!.hasAttribute('disabled')).toBe(true);
    expect(el('break-in-blocked')!.dataset.reason).toBe('out-of-band');
  });

  it('sends set_break_in with the absolute wire mode when permitted', () => {
    render();
    press(el('break-in-full')!);
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_break_in', { mode: 2 });
  });
});

/* ── (d) receiver-scoped intents follow the ACTIVE VFO ─────────── */

describe('APF and TPF are receiver-scoped and follow the active VFO', () => {
  // MUTATION KILLED: facts read from one receiver while the command targets
  // the other. SUB's apfTypeLevel is 2, MAIN's is 0, so the rendered ordinal
  // and the command's `receiver` param must move together.
  it('reads SUB facts and commands SUB when SUB is active', () => {
    useState(liveState({ active: 'SUB' } as Partial<ServerState>));
    render();
    expect(el('apf-value')!.textContent!.trim()).toBe('2');
    press(el('apf-off')!);
    flushSync();
    expect(sendCommand).toHaveBeenCalledWith('set_apf', { mode: 0, receiver: 1 });
  });

  it('reads MAIN facts and commands MAIN when MAIN is active', () => {
    render();
    expect(el('apf-value')!.textContent!.trim()).toBe('0');
    press(el('apf-on')!);
    flushSync();
    expect(sendCommand).toHaveBeenCalledWith('set_apf', { mode: 1, receiver: 0 });
  });
});

/* ── (e) MOUNTING — single composition only, dual absence pinned ── */

describe('the surface mounts only where a declared zone can hold it', () => {
  // MUTATION KILLED: mounting `CwKeyerSurface` unconditionally — a radio with
  // no CW keyer would gain a panel it never asked for.
  it('renders no CW surface for a radio with no keyer', () => {
    h.caps = liveCaps(NO_CW_TAGS);
    render();
    expect(el('surface')).toBeNull();
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render();
    expect(el('surface')).not.toBeNull();
    expect(el('surface')!.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * MUTATION KILLED: mounting this surface bare in the cockpit (the MOR-1304
   * ruling). It is control-bearing and no manifest declares a `cwKeyer` zone,
   * so MOR-1069's cockpit rule — every focusable control inside a declared
   * zone, tab order ending in rx-tx — would break on both clauses; folding it
   * into the rx-tx zone would put break-in choices between the operator and
   * the unkey button.
   *
   * The caps here DO emit the group (proved by the single-composition test
   * above, same `h.caps`), so this pin cannot pass vacuously the way a
   * cockpit fixture with `modes: [], filters: []` does.
   */
  it('renders NO CW-keyer surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('cw-keyer');
  });

  it('leaves the cockpit with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });
});
