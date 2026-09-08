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
 *       commands of the key/unkey class (`ptt`, `set_tuner_status`). The
 *       RX-assisted frequency correction is intentionally distinct: it
 *       delegates to the existing fail-closed `cw_auto_tune` intent and never
 *       emits a managed TX intent. Exactly one `<RxTxSurface>` remains the key/unkey
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
 *   (e) MOUNTING: the surface is control-bearing and the dual composition's
 *       only layout (`dual-receiver-cockpit.ts`) declares no `cwKeyer` zone,
 *       so it renders in the SINGLE composition only. The dual
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
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: ControlSessionTransition;
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: false, rxEnabled: true, volume: 42 },
  audioConnected: true,
  rxEnabled: true,
  txController: null as ManagedAppTxController | null,
  session: { state: 'connected' as ControlSessionTransition['state'], epoch: 1 },
  sessionSubscriber: undefined as ((event: ControlSessionTransition) => void) | undefined,
  delivery: undefined as ((event: CommandDeliveryEvent) => void) | undefined,
  transition: undefined as ((event: ControlSessionTransition) => void) | undefined,
}));

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
  getControlSession: () => h.session,
  onCommandDelivery: vi.fn((handler: (event: CommandDeliveryEvent) => void) => {
    h.delivery = handler;
    return () => { if (h.delivery === handler) h.delivery = undefined; };
  }),
  onControlSessionTransition: vi.fn((handler: (event: ControlSessionTransition) => void) => {
    h.transition = handler;
    return () => { if (h.transition === handler) h.transition = undefined; };
  }),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return h.rxEnabled; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
    subscribeControlSession(handler: (event: ControlSessionTransition) => void) {
      h.sessionSubscriber = handler;
      return () => { if (h.sessionSubscriber === handler) h.sessionSubscriber = undefined; };
    },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: h.session,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    get rxEnabled() { return h.rxEnabled; },
    setVolume: vi.fn(), setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(),
    // MOR-1312 slice 12B (rebase fix): the wiring now also hands the adapter
    // a scope-display snapshot (the FIFTH argument). This file tests
    // cwKeyer, so this stays on its pre-1312 path regardless of these values.
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
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (!h.txController) throw new Error('managed TX harness is not installed');
    return h.txController;
  },
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'LAN' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

/** MOR-2425 — the `createContinuousScalar` capture wrapper
 *  `semantic-dsp-wiring.component.test.ts` establishes, so the persistence
 *  witness below can name the binding OBJECT, not just its DOM projection. */
const scalars = vi.hoisted(() => ({
  bindings: [] as Array<{ command: string | null; binding: unknown }>,
  leases: [] as Array<{ binding: unknown; lease: unknown }>,
}));
vi.mock('../../../primitives/scalar/continuous-scalar.svelte', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../primitives/scalar/continuous-scalar.svelte')>();
  return {
    ...actual,
    createContinuousScalar: (...args: Parameters<typeof actual.createContinuousScalar>) => {
      const binding = actual.createContinuousScalar(...args);
      const attachRenderer = binding.attachRenderer.bind(binding);
      binding.attachRenderer = (...attachArgs) => {
        const lease = attachRenderer(...attachArgs);
        scalars.leases.push({ binding, lease });
        return lease;
      };
      const source = args[0]();
      scalars.bindings.push({
        command: source.evidence === 'command-feedback' ? source.command : null, binding,
      });
      return binding;
    },
  };
});

// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import { sendCommand } from '$lib/transport/ws-client';
import { beginCommand, getCommandLifecycle, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { CW_CONTINUOUS_LEVELS } from '../../../semantic/CwKeyerInstrumentHost.svelte';
import HostedRadioLayoutFixture from '../../layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import type {
  ContinuousScalarBinding, ContinuousScalarRendererLease, ContinuousScalarView,
} from '../../../primitives/scalar/continuous-scalar.svelte';
import { desktopV2Layout, sdrTestLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY } from '../../../presentation/workspace/resolution';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

/**
 * The command names that KEY or cause a carrier. Not one of these may leave
 * this surface, in any state, through any control. `set_tuner_status` is here
 * because value 2 starts an ATU tune cycle (the MOR-1244 carrier).
 */
const KEY_CLASS_COMMANDS = ['ptt', 'set_tuner_status'] as const;

const fresh = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 10,
};
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
    sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 0, rfGain: 0, squelch: 0,
  });
  return {
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-08-08T00:00:00Z', tunerStatus: 0,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    stateContractVersion: 1, providerGeneration: 0,
    breakIn: 1, breakInDelay: 64, keySpeed: 24, cwPitch: 600, dashRatio: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000, 0), sub: receiver(14300000, 2),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (tags: readonly string[], audioFftAvailable = false): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: tags, receivers: 2, vfoScheme: 'main_sub', freqRanges: [],
  // A radio that declares no modes is not a radio that exists, and the
  // APF/TPF mutex reads `modeFilter.currentMode` — an empty `modes` list makes
  // the mode fact absent and BOTH controls fail closed, hiding this slice's
  // behaviour behind a fixture hole (the MOR-1304 N2 lesson).
  modes: ['CW', 'CW-R', 'RTTY', 'USB'], filters: ['FIL1', 'FIL2', 'FIL3'],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable,
  stateContractVersion: 1, providerGeneration: 0,
} as unknown as Capabilities);

/** A full CW radio: keyer, break-in, APF and the twin-peak filter. */
const CW_TAGS = ['tx', 'cw', 'break_in', 'apf', 'twin_peak'] as const;
/** No `cw` tag ⇒ the MOR-1296 evidence gate declines and no group is emitted. */
const NO_CW_TAGS = ['tx'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

/** Mounts the REAL `RadioLayout` behind a reactive `skinId`, for the
 *  Standard↔SDR persistence witness below — `props.skinId` can be reassigned
 *  and `flushSync()`'d without remounting the fixture. */
function renderHosted() {
  target = document.createElement('div');
  document.body.appendChild(target);
  const props = proxy({ skinId: 'desktop-v2' as 'desktop-v2' | 'sdr-test' });
  const context = new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () =>
    resolveSurfacePlan(props.skinId === 'desktop-v2' ? desktopV2Layout : sdrTestLayout,
      readWorkspace({ version: 1 }).workspace)]]);
  component = mount(HostedRadioLayoutFixture, { target, props, context });
  flushSync();
  return props;
}

/** The pitch scalar binding, as an OBJECT (there is exactly one). */
const cwBinding = (command: string): ContinuousScalarBinding => {
  const found = scalars.bindings.filter((entry) => entry.command === command);
  expect(found).toHaveLength(1);
  return found[0]!.binding as ContinuousScalarBinding;
};
const latestCwLease = (binding: unknown): ContinuousScalarRendererLease => {
  for (let index = scalars.leases.length - 1; index >= 0; index -= 1) {
    if (scalars.leases[index]!.binding === binding) {
      return scalars.leases[index]!.lease as ContinuousScalarRendererLease;
    }
  }
  throw new Error('renderer lease not captured');
};
/** The command-feedback evidence a persistent binding must carry across a switch. */
const cwLifecycle = (binding: ContinuousScalarBinding) => {
  const view = binding.view as Extract<ContinuousScalarView, { evidence: 'command-feedback' }>;
  return {
    requested: view.requested, confirmed: view.confirmed, phase: view.phase,
    error: view.error, transitionId: view.feedback.transitionId,
  };
};

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

function publishAuthority(): void {
  for (const subscriber of h.authoritySubscribers) subscriber({
    state: h.state, caps: h.caps, session: h.session,
    rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
  });
}

function delayState(
  value: number | undefined, marker: number,
  status: Partial<(typeof fresh)> = {},
): ServerState {
  const state = liveState({
    breakInDelay: value, revision: marker, stateRevision: marker,
    freshnessRevision: marker, observationSeq: marker,
  });
  return {
    ...state,
    fieldStatus: {
      ...state.fieldStatus,
      breakInDelay: { ...fresh, lastObservedMonotonic: marker, ...status },
    },
  } as unknown as ServerState;
}

function advanceDelay(value: number, marker: number): void {
  const state = delayState(value, marker);
  h.state = state;
  setRadioState(state);
  publishAuthority();
  flushSync();
}

function advanceCw(cwPitch: number, keySpeed: number, marker: number): void {
  const state = liveState({
    cwPitch, keySpeed, revision: marker, stateRevision: marker,
    freshnessRevision: marker, observationSeq: marker,
  });
  state.fieldStatus = {
    ...state.fieldStatus,
    cwPitch: { ...fresh, freshness: 'fresh' as const, availability: 'available' as const,
      lastObservedMonotonic: marker },
    keySpeed: { ...fresh, freshness: 'fresh' as const, availability: 'available' as const,
      lastObservedMonotonic: marker },
  };
  h.state = state;
  setRadioState(state);
  publishAuthority();
  flushSync();
}

function delayInput(): HTMLInputElement {
  return el('breakInDelay')!.querySelector('input') as HTMLInputElement;
}

function submitDelay(value: number): string {
  const input = delayInput();
  input.value = String(value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  flushSync();
  return vi.mocked(sendCommand).mock.calls.at(-1)![2] as string;
}

function cwInput(field: 'pitchHz' | 'keyerSpeed'): HTMLElement {
  return el(field)!.querySelector('[role="slider"]') as HTMLElement;
}

function cwValue(field: 'pitchHz' | 'keyerSpeed'): string | null {
  const input = cwInput(field);
  return input instanceof HTMLInputElement ? input.value : input.getAttribute('aria-valuenow');
}

function cwDisabled(field: 'pitchHz' | 'keyerSpeed'): boolean {
  const input = cwInput(field);
  return input instanceof HTMLInputElement
    ? input.disabled : input.getAttribute('aria-disabled') === 'true';
}

const HBAR_WIDTH = 84;

function cwDomain(field: 'pitchHz' | 'keyerSpeed'): { min: number; max: number } {
  const [, , min, max] = CW_CONTINUOUS_LEVELS.find(([f]) => f === field)!;
  return { min, max };
}

function submitCw(field: 'pitchHz' | 'keyerSpeed', value: number): string {
  const input = cwInput(field);
  const frame = input.closest<HTMLElement>('.vc-hbar')!;
  vi.spyOn(frame, 'getBoundingClientRect').mockReturnValue({
    left: 0, width: HBAR_WIDTH,
  } as DOMRect);
  Object.assign(input, {
    setPointerCapture: () => undefined,
    hasPointerCapture: () => false,
    releasePointerCapture: () => undefined,
  });
  const { min, max } = cwDomain(field);
  const clientX = (value - min) * HBAR_WIDTH / (max - min);
  input.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX, pointerId: 1 }));
  input.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
  flushSync();
  return vi.mocked(sendCommand).mock.calls.at(-1)![2] as string;
}

// The Host's announcement lane is emitted at the Host's own root (see
// `CwKeyerInstrumentHost.svelte`'s `{#each CW_CONTINUOUS_LEVELS}` block after
// `{@render children(...)}`), wherever the consumer places each field's seat, not
// nested inside it — so it is located by `data-feedback-lane`, not by
// querying inside `el(field)`.
const feedbackStatus = (field: 'pitchHz' | 'keyerSpeed') =>
  q<HTMLElement>(`[data-feedback-lane="${field}"]`);
const feedbackStatuses = (field: 'pitchHz' | 'keyerSpeed') =>
  target.querySelectorAll<HTMLElement>(`[data-feedback-lane="${field}"]`);

function deliver(commandId: string, kind: CommandDeliveryEvent['kind'], error?: string): void {
  expect(h.delivery).toBeTypeOf('function');
  h.delivery!({ commandId, kind, originalEpoch: 1, eventEpoch: 1, error });
  flushSync();
}

beforeEach(() => {
  scalars.bindings = [];
  scalars.leases = [];
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  setCapabilities(liveCaps(CW_TAGS));
  useState(liveState());
  h.caps = liveCaps(CW_TAGS);
  h.session = { state: 'connected', epoch: 1 };
  h.sessionSubscriber = undefined;
  vi.mocked(sendCommand).mockClear();
  resetCommandLifecycle();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  resetCommandLifecycle();
  vi.useRealTimers();
  expect(h.sessionSubscriber).toBeUndefined();
  expect(h.authoritySubscribers.size).toBe(0);
});

/* ── (a) THE NO-KEY-PATH PIN ───────────────────────────────────── */

describe('the CW surface never becomes a second key path (decomposition R9)', () => {
  // MUTATION KILLED: any module in the closure keying at import time.
  it('mounts the armed tree and sends no command at all', () => {
    render();
    expect(el('surface')).not.toBeNull();
    expect(el('break-in-full')!.hasAttribute('disabled')).toBe(false);
    expect(sendCommand).not.toHaveBeenCalled();
    expect(txHarness.trace()).toEqual([]);
  });

  it('projects independent native CW feedback from intent through confirmation and failure', () => {
    render();
    expect(cwInput('pitchHz').dataset.commandPhase).toBe('idle');
    expect(cwInput('keyerSpeed').dataset.commandPhase).toBe('idle');

    const pitchId = submitCw('pitchHz', 725);
    expect(sendCommand).toHaveBeenLastCalledWith(
      'set_cw_pitch', { value: 725 }, pitchId,
    );
    const speedId = submitCw('keyerSpeed', 31);
    expect(sendCommand).toHaveBeenLastCalledWith(
      'set_key_speed', { speed: 31 }, speedId,
    );
    expect(cwInput('pitchHz').dataset.commandPhase).toBe('submitted');
    expect(cwInput('keyerSpeed').dataset.commandPhase).toBe('submitted');
    unmount(component!); component = null; target.remove();
    render();
    expect(cwValue('pitchHz')).toBe('600');
    expect(cwValue('keyerSpeed')).toBe('24');
    expect(cwInput('pitchHz').getAttribute('aria-valuetext')).toContain('requested 725 Hz');
    expect(cwInput('keyerSpeed').getAttribute('aria-valuetext')).toContain('requested 31 WPM');
    expect(cwInput('pitchHz').dataset.commandPhase).toBe('submitted');
    expect(cwInput('keyerSpeed').dataset.commandPhase).toBe('submitted');
    expect(sendCommand).toHaveBeenCalledTimes(2);

    deliver(pitchId, 'ack');
    deliver(speedId, 'ack');
    expect(cwInput('pitchHz').dataset.commandPhase).toBe('awaiting-confirmation');
    expect(cwInput('keyerSpeed').dataset.commandPhase).toBe('awaiting-confirmation');

    advanceCw(725, 24, 11);
    expect(cwInput('pitchHz').dataset.commandPhase).toBe('confirmed');
    expect(cwValue('pitchHz')).toBe('725');
    expect(cwInput('keyerSpeed').dataset.commandPhase).toBe('awaiting-confirmation');
    deliver(speedId, 'response-error', 'speed rejected');
    expect(cwInput('keyerSpeed').dataset.commandPhase).toBe('failed');
    expect(cwValue('keyerSpeed')).toBe('24');
    expect(el('keyerSpeed')!.textContent).toContain('speed rejected');
    expect(txHarness.trace()).toEqual([]);
  });

  it('keeps the newest CW target and invalidates both lanes on session replacement', () => {
    render();
    const oldId = submitCw('pitchHz', 650);
    const latestId = submitCw('pitchHz', 700);
    submitCw('keyerSpeed', 31);
    deliver(oldId, 'response-error', 'late superseded failure');
    expect(cwInput('pitchHz').dataset.commandPhase).toBe('submitted');
    expect(cwInput('pitchHz').getAttribute('aria-valuetext')).toContain('requested 700 Hz');
    expect(getCommandLifecycle(latestId, 1)?.status).toBe('pending');
    const oldPitchStatus = feedbackStatus('pitchHz')!;
    const oldPitchText = oldPitchStatus.textContent;
    expect(target.querySelectorAll('[data-cw-feedback-status]')).toHaveLength(2);

    h.session = { state: 'disconnected', epoch: 2 };
    h.transition!({ state: 'disconnected', epoch: 2 });
    h.sessionSubscriber!({ state: 'disconnected', epoch: 2 });
    publishAuthority();
    flushSync();
    for (const field of ['pitchHz', 'keyerSpeed'] as const) {
      expect(cwDisabled(field)).toBe(true);
      expect(cwInput(field).dataset.commandPhase).toBe('unavailable');
      expect(el(field)!.dataset.observed).toBe('false');
    }
    expect(target.querySelectorAll('[data-cw-feedback-status]')).toHaveLength(0);
    expect(sendCommand).toHaveBeenCalledTimes(3);

    h.session = { state: 'connected', epoch: 3 };
    h.transition!({ state: 'connected', epoch: 3 });
    h.sessionSubscriber!({ state: 'connected', epoch: 3 });
    publishAuthority();
    flushSync();
    submitCw('pitchHz', 700);
    const newPitchStatus = feedbackStatus('pitchHz')!;
    expect(newPitchStatus.textContent).toBe(oldPitchText);
    expect(newPitchStatus).not.toBe(oldPitchStatus);
    expect(feedbackStatuses('pitchHz')).toHaveLength(1);
    expect(sendCommand).toHaveBeenCalledTimes(4);
    expect(txHarness.trace()).toEqual([]);
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
   * MUTATION KILLED: wiring a key intent onto ANY setting control here (`cmd('ptt')`,
   * `tx.start(...)`, an ATU tune). Sliders are driven to their
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
        control.dispatchEvent(new Event('change', { bubbles: true }));
      } else if (control.getAttribute('role') === 'slider') {
        control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      } else press(control);
    }
    flushSync();

    expect(commands()).toEqual([
      'set_break_in', 'set_break_in', 'set_break_in',
      'set_key_speed', 'set_cw_pitch', 'set_break_in_delay',
      'set_dash_ratio', ...filterCommands,
    ]);
    for (const forbidden of KEY_CLASS_COMMANDS) expect(commands()).not.toContain(forbidden);
    // The App TX facade receives no intent either — the ONE
    // `<RxTxSurface>` above is untouched by everything this surface does.
    expect(txHarness.trace()).toEqual([]);
  });

  it('commits one IC-7300-shaped Break-in Delay intent only on release', () => {
    const ic7300Caps = {
      ...liveCaps(CW_TAGS), model: 'IC-7300', receivers: 1, vfoScheme: 'ab',
    } as Capabilities;
    h.caps = ic7300Caps;
    setCapabilities(ic7300Caps);
    useState(liveState());
    render();
    const input = el('breakInDelay')!.querySelector('input') as HTMLInputElement;
    expect(input.dataset.commandPhase).toBe('idle');
    expect(input.getAttribute('aria-busy')).toBe('false');
    expect(el('breakInDelay-value')!.textContent).toContain('64 idle');
    for (const value of [80, 96, 111]) {
      input.value = String(value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
    input.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith(
      'set_break_in_delay', { level: 111 }, expect.any(String),
    );
    const commandId = vi.mocked(sendCommand).mock.calls[0]![2] as string;
    expect(getCommandLifecycle(commandId, 1)).toMatchObject({
      id: commandId, name: 'set_break_in_delay', params: { level: 111 }, status: 'pending',
    });
    expect(input.value).toBe('111');
    expect(input.dataset.commandPhase).toBe('submitted');
    expect(input.getAttribute('aria-busy')).toBe('true');
    expect(input.getAttribute('aria-valuetext')).toBe('Requested 111; last confirmed 64');
    expect(el('breakInDelay-value')!.textContent).toContain('111 submitted');
    expect(txHarness.trace()).toEqual([]);
  });

  it('projects acknowledgement and only newer matching radio truth as confirmed', () => {
    render();
    const commandId = submitDelay(111);
    deliver(commandId, 'ack');
    expect(delayInput().dataset.commandPhase).toBe('awaiting-confirmation');
    expect(delayInput().value).toBe('111');

    advanceDelay(64, 11);
    expect(delayInput().dataset.commandPhase).toBe('awaiting-confirmation');
    advanceDelay(111, 12);
    expect(delayInput().dataset.commandPhase).toBe('confirmed');
    expect(delayInput().value).toBe('111');
    expect(delayInput().getAttribute('aria-busy')).toBe('false');
    const live = q<HTMLElement>('[data-control-feedback-status]');
    expect(live?.getAttribute('aria-live')).toBe('polite');
    expect(live?.textContent).toContain('111');
  });

  it('restores canonical truth after transport failure and timeout', () => {
    render();
    const failedId = submitDelay(111);
    deliver(failedId, 'response-error', 'radio rejected write');
    expect(delayInput().dataset.commandPhase).toBe('failed');
    expect(delayInput().value).toBe('64');
    expect(el('breakInDelay-value')!.textContent).toContain('64 failed');

    unmount(component!); component = null;
    resetCommandLifecycle(); vi.mocked(sendCommand).mockClear();
    vi.useFakeTimers();
    render();
    submitDelay(99);
    vi.advanceTimersByTime(5_000); flushSync();
    expect(delayInput().dataset.commandPhase).toBe('timed-out');
    expect(delayInput().value).toBe('64');
    expect(delayInput().getAttribute('aria-busy')).toBe('false');
  });

  it('keeps only the newest target and invalidates its draft on provider cancellation', () => {
    render();
    const oldId = submitDelay(80);
    const currentId = submitDelay(111);
    expect(sendCommand).toHaveBeenCalledTimes(2);
    expect(delayInput().value).toBe('111');
    deliver(oldId, 'response-error', 'late superseded failure');
    expect(delayInput().dataset.commandPhase).toBe('submitted');
    expect(getCommandLifecycle(currentId, 1)?.status).toBe('pending');

    delayInput().value = '99';
    delayInput().dispatchEvent(new Event('input', { bubbles: true }));
    h.session = { state: 'disconnected', epoch: 1 };
    h.transition!({ state: 'disconnected', epoch: 1 }); publishAuthority(); flushSync();
    expect(delayInput().dataset.commandPhase).toBe('cancelled');
    expect(delayInput().value).toBe('64');
    delayInput().dispatchEvent(new Event('change', { bubbles: true })); flushSync();
    expect(sendCommand).toHaveBeenCalledTimes(2);
  });

  it('presents out-of-band truth, keeps a stale reading honest, and fails closed for malformed truth', () => {
    render();
    unmount(component!); component = null;
    useState(delayState(72, 11)); render();
    expect(delayInput().dataset.commandPhase).toBe('idle');
    expect(delayInput().value).toBe('72');
    expect(sendCommand).not.toHaveBeenCalled();

    // R29: a stale-but-observed field no longer fails the control closed —
    // `projectControlFeedback` presents the last value with phase 'idle',
    // never a blanked 'unavailable' placeholder. `disabled` now flips to
    // `false` too: MOR-2425's follow-up PR fixes `field-status.ts:
    // getFieldAvailability`, the second doctrine site #3357 (the commit that
    // wrote this test) named and deliberately deferred — `CwKeyerSurface`'s
    // `usable(cw.breakInDelay)` gate reads THAT primitive, confirmed by the
    // flipped `STALE_FIELDS` row for `breakInDelay` in
    // `cw-keyer-adapter.test.ts`.
    unmount(component!); component = null;
    useState(delayState(72, 12, { freshness: 'stale' })); render();
    expect(delayInput().disabled).toBe(false);
    expect(delayInput().dataset.commandPhase).toBe('idle');
    expect(delayInput().value).toBe('72');
    expect(delayInput().hasAttribute('aria-valuenow')).toBe(true);
    expect(delayInput().getAttribute('aria-valuenow')).toBe('72');
    expect(delayInput().getAttribute('aria-valuetext')).toBe('Confirmed 72');
    expect(el('breakInDelay-value')!.textContent).toContain('72');

    // A REAL disconnect (`state === null`, `projectControlFeedback`'s own
    // gate, orthogonal to freshness) still greys the control out from this
    // stale-but-presented starting point.
    unmount(component!); component = null;
    h.state = null;
    h.session = { state: 'disconnected', epoch: 2 };
    render();
    expect(delayInput().disabled).toBe(true);
    expect(delayInput().dataset.commandPhase).toBe('unavailable');
    h.session = { state: 'connected', epoch: 1 };

    unmount(component!); component = null;
    useState(delayState(256, 13)); render();
    expect(delayInput().disabled).toBe(true);
    delayInput().value = '99';
    delayInput().dispatchEvent(new Event('change', { bubbles: true })); flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it.each(['pointer release', 'keyboard release'])('commits exactly once on %s', (gesture) => {
    render();
    const input = delayInput();
    input.value = '111';
    if (gesture === 'keyboard release') {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    if (gesture === 'keyboard release') {
      input.dispatchEvent(new KeyboardEvent('keyup', { key: 'ArrowRight', bubbles: true }));
    } else input.dispatchEvent(new Event('pointerup', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true })); flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith(
      'set_break_in_delay', { level: 111 }, expect.any(String),
    );
  });

  it.each(['Escape', 'pointercancel'])('routes %s cancellation without a commit', (event) => {
    render();
    const input = delayInput();
    input.value = '111';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    if (event === 'Escape') {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    } else input.dispatchEvent(new Event('pointercancel', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true })); flushSync();
    expect(input.value).toBe('64');
    expect(sendCommand).not.toHaveBeenCalled();
  });

  // MUTATION KILLED: the surface reacting to the transmit bit or the App TX
  // authority at all. Nothing here is TX truth.
  it('never changes with the App TX authority or the raw transmit bit', () => {
    render();
    const before = el('surface')!.outerHTML;
    txHarness.emitServerSnapshot({ intent: 'ptt', observedPtt: 'on' });
    h.state = liveState({ ptt: true } as Partial<ServerState>);
    publishAuthority();
    flushSync();
    expect(el('surface')!.outerHTML).toBe(before);
  });
});

/* ── (a2) RX-ASSISTED FREQUENCY CORRECTION — EXISTING SAFE INTENT ─────── */

describe('RX-assisted frequency correction wires only the existing safe capability facts', () => {
  /**
   * RED-FIRST: on the parent revision this control is absent because the
   * semantic wiring deliberately omits both props. This exact composed-tree
   * witness therefore fails before the wiring change, instead of merely
   * proving CwKeyerSurface can render a prop supplied by a unit-test fixture.
   */
  it('renders only with CW + audio + audio FFT and delegates 1:1 without TX authority', () => {
    const caps = liveCaps([...CW_TAGS, 'audio'], true);
    h.caps = caps;
    setCapabilities(caps);
    render();

    const correction = el('auto-tune');
    expect(correction).not.toBeNull();
    expect(correction!.textContent).toContain('RX frequency correction');
    press(correction!);
    flushSync();

    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('cw_auto_tune', {}, expect.any(String));
    expect(txHarness.trace()).toEqual([]);
  });

  it.each([
    ['audio FFT is absent', [...CW_TAGS, 'audio'], false],
    ['audio capability is absent', CW_TAGS, true],
    ['CW capability is absent', ['tx', 'audio'], true],
  ] as const)('renders no correction control when %s', (_reason, tags, audioFftAvailable) => {
    const caps = liveCaps(tags, audioFftAvailable);
    h.caps = caps;
    setCapabilities(caps);
    render();

    expect(el('auto-tune')).toBeNull();
    expect(sendCommand).not.toHaveBeenCalled();
    expect(txHarness.trace()).toEqual([]);
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
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_break_in', { mode: 2 }, expect.any(String));
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
    expect(sendCommand).toHaveBeenCalledWith('set_apf', { mode: 0, receiver: 1 }, expect.any(String));
  });

  it('reads MAIN facts and commands MAIN when MAIN is active', () => {
    render();
    expect(el('apf-value')!.textContent!.trim()).toBe('0');
    press(el('apf-on')!);
    flushSync();
    expect(sendCommand).toHaveBeenCalledWith('set_apf', { mode: 1, receiver: 0 }, expect.any(String));
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
   * ruling). It is control-bearing and `dual-receiver-cockpit.ts` — the only
   * layout with a dual composition — declares no `cwKeyer` zone, so
   * MOR-1069's cockpit rule — every focusable control inside a declared
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
      .filter((node) => !node.matches(':disabled') && node.tabIndex >= 0
        && node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });
});

/**
 * `desktop-v2` seats `pitchHz` outside `<CwKeyerSurface>` (like `keyerSpeed`)
 * and suppresses the surface's own copy; every other layout still renders it
 * via the grouped handle. MUTATION KILLED (b1): keeping the seat while the
 * surface's copy is also shown duplicates it. (b2): dropping the seat without
 * restoring the surface's copy leaves zero.
 */
describe('pitch mounts exactly once regardless of the hosting layout', () => {
  it.each(['desktop-v2', 'sdr-test'] as const)(
    'renders exactly one pitchHz and one keyerSpeed control under %s',
    (skinId) => {
      target = document.createElement('div');
      document.body.appendChild(target);
      component = mount(HostedRadioLayoutFixture, { target, props: { skinId } });
      flushSync();
      for (const field of ['pitchHz', 'keyerSpeed'] as const) {
        expect(target.querySelectorAll(
          `[data-testid="cw-keyer-${field}"] [role="slider"][aria-label="${
            field === 'pitchHz' ? 'CW pitch' : 'Keyer speed'}"]`,
        )).toHaveLength(1);
      }
    },
  );
});

/**
 * MOR-2425 — the witness the persistent pitch binding exists for, in the
 * order its parts must be read (mirrors the `nbWidth` witness in
 * `semantic-dsp-wiring.component.test.ts`): identity first, then proof the
 * CURRENT lease still commands, then surviving evidence — only then inertness.
 */
describe('the pitch binding survives a Standard→SDR switch (MOR-2425)', () => {
  it('keeps the pitch binding, its pending evidence and its live lease across the switch', () => {
    const props = renderHosted();
    beginCommand({
      id: 'pending-cw-pitch', name: 'set_cw_pitch', params: { value: 725 }, originalEpoch: 1,
    });
    flushSync();
    const before = cwBinding('set_cw_pitch');
    const beforeLifecycle = cwLifecycle(before);
    expect(beforeLifecycle).toMatchObject({ phase: 'submitted', requested: 725, confirmed: 600 });
    const staleLease = latestCwLease(before);

    props.skinId = 'sdr-test';
    flushSync();
    const afterLifecycle = cwLifecycle(before);

    // (i) Identity, positively: the SAME binding object, not a rebuilt one.
    const after = cwBinding('set_cw_pitch');
    expect(after).toBe(before);

    // (ii) Admission proven open: the CURRENT lease emits exactly one command.
    expect(latestCwLease(after)).not.toBe(staleLease);
    expect(submitCw('pitchHz', 800)).toEqual(expect.any(String));
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith(
      'set_cw_pitch', { value: 800 }, expect.any(String),
    );

    // (iii) Evidence survival, read at the moment after the switch — BEFORE
    // the (ii) submission moved the target on to 800.
    expect(afterLifecycle).toEqual(beforeLifecycle);
    expect(feedbackStatuses('pitchHz')).toHaveLength(1);

    // Only now: the retained Standard lease is inert, and adds no command.
    expect(staleLease.beginPointer()).toBeNull();
    expect(staleLease.key({ key: 'ArrowRight', fine: false })).toBe(false);
    staleLease.nativeInput(900);
    staleLease.wheel({ direction: 1, fine: false });
    flushSync();
    expect(sendCommand).toHaveBeenCalledOnce();
  });
});
