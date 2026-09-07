import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte's reactive test proxy is intentionally internal.
import { proxy } from 'svelte/internal/client';
import { fromStore, writable } from 'svelte/store';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';
import { commitExternalPresentationBatch, getPresentationRecord, loadSkin,
  prepareExternalPresentationBatch, type ExternalPresentationRecord } from '../skins/registry';
import type {
  HostedFaceComponentV1, MeterAppearance, ScalarRendererLease, ScalarRendererSeat,
} from '../../component-kit-api/src/index';
import type { FiniteRendererLease } from '../primitives/control-instruments/control-instrument-renderer.svelte';
import type { FrequencyInstrumentBinding } from '../primitives/frequency/frequency-instrument.svelte';
import type { FrequencyInteractionLease } from '../primitives/frequency/frequency-interaction.svelte';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  session: { state: 'connected', epoch: 7 } as ControlSessionSnapshot,
  subscribers: new Set<(publication: never) => void>(),
  radioListeners: new Set<(state: never) => void>(),
  calls: new Map<string, ReturnType<typeof vi.fn>>(),
  frequencyOwners: [] as FrequencyInstrumentBinding[], frequencyLeases: [] as FrequencyInteractionLease[],
  meterOwners: [] as object[], barMeterOwners: [] as object[],
  meterAppearance: undefined as MeterAppearance | undefined,
  scalarOwners: [] as object[], finiteSeats: [] as object[],
  scalarSeats: [] as ScalarRendererSeat[], scalarLeases: [] as ScalarRendererLease[],
  finiteLeases: [] as FiniteRendererLease<unknown>[],
  subscribeCount: 0, unsubscribeCount: 0,
  omitSpeak: false,
}));
const call = (name: string) => {
  let fn = h.calls.get(name);
  if (!fn) { fn = vi.fn(); h.calls.set(name, fn); }
  return fn;
};

vi.mock('$lib/runtime', () => ({
  presentationResources: { acquire: vi.fn(), release: vi.fn() },
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; }, get caps() { return h.caps; },
    get controlSession() { return h.session; },
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; }, get radioPowerOn() { return null; },
    get scope() { return { hardwareScopeConnected: false }; },
    get defaultScopeStatus() { return { source: null, available: false, resourceSelected: false,
      demand: 0, lifecycle: 'inactive', transport: 'disconnected', frameSeen: false }; },
    subscribeControlSession: () => () => {},
    subscribeControlAuthority(handler: (publication: never) => void) {
      h.subscribeCount += 1; h.subscribers.add(handler); handler(authority() as never);
      return () => { h.unsubscribeCount += 1; h.subscribers.delete(handler); };
    },
  },
}));
vi.mock('$lib/runtime/frontend-runtime', async () => ({
  runtime: (await import('$lib/runtime')).runtime,
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => ({
    snapshot: () => ({ intent: 'receive', observedPtt: 'off', pending: false, blockedReason: null }),
    transmitOn: vi.fn(), forceOff: vi.fn(), requestTune: vi.fn(), recover: vi.fn(), subscribe: () => () => {},
  }),
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { get current() { return h.state; } }, getRadioState: () => h.state,
  subscribeRadioState(listener: (state: never) => void) {
    h.radioListeners.add(listener); listener(h.state as never);
    return () => h.radioListeners.delete(listener);
  },
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getScopeSource: () => null, getCapabilities: () => h.caps,
  capabilitiesMatchGeneration: () => true, getControlRange: () => null,
  getSmeterCalibration: () => (h.caps as Capabilities | null)?.meterCalibrations?.s_meter,
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/radio-view-model-adapter')>();
  return { ...actual, toRadioViewModel(...args: Parameters<typeof actual.toRadioViewModel>) {
    const view = actual.toRadioViewModel(...args);
    if (view === null) return null;
    return { ...view, radioWideIndicators: { ...view.radioWideIndicators,
      actions: { ...view.radioWideIndicators?.actions,
        quickSplit: { structural: true, operational: true },
        quickDualWatch: { structural: true, operational: true },
        ...(h.omitSpeak ? { speak: { structural: false, operational: false } } : {}) } } };
  } };
});
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
vi.mock('../primitives/frequency/frequency-instrument.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../primitives/frequency/frequency-instrument.svelte')>();
  return { ...actual, createFrequencyInstrumentBinding(
    ...args: Parameters<typeof actual.createFrequencyInstrumentBinding>
  ) {
    const owner = actual.createFrequencyInstrumentBinding(...args);
    const captured: FrequencyInstrumentBinding = { get context() { return owner.context; },
      get model() { return owner.model; }, attachRenderer(isCurrent) {
        const lease = owner.attachRenderer(isCurrent); h.frequencyLeases.push(lease); return lease;
      } };
    h.frequencyOwners.push(captured); return captured;
  } };
});
// Station meters take their appearance from the module-level selection, as
// `MetersSurface` does; only the receiver S meter gets a per-record override.
// This file builds records directly, bypassing `activateComponentKits`, which
// is what fills that selection in the app.
vi.mock('../component-kits/activation', async (importOriginal) => ({
  ...await importOriginal<typeof import('../component-kits/activation')>(),
  getSelectedMeterAppearance: () => h.meterAppearance,
}));
vi.mock('../components-v2/meters/signal-meter-motion.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components-v2/meters/signal-meter-motion.svelte')>();
  return { ...actual, createSignalMeterMotion(...args: Parameters<typeof actual.createSignalMeterMotion>) {
    const owner = actual.createSignalMeterMotion(...args); h.meterOwners.push(owner); return owner;
  } };
});
vi.mock('../components-v2/meters/bar-meter-motion.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components-v2/meters/bar-meter-motion.svelte')>();
  return { ...actual, createBarMeterMotion(...args: Parameters<typeof actual.createBarMeterMotion>) {
    const owner = actual.createBarMeterMotion(...args); h.barMeterOwners.push(owner); return owner;
  } };
});
vi.mock('../primitives/scalar/continuous-scalar.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../primitives/scalar/continuous-scalar.svelte')>();
  return { ...actual, createContinuousScalar(...args: Parameters<typeof actual.createContinuousScalar>) {
    const owner = actual.createContinuousScalar(...args); h.scalarOwners.push(owner); return owner;
  }, createContinuousScalarRendererSeat(
    ...args: Parameters<typeof actual.createContinuousScalarRendererSeat>
  ) {
    const seat = actual.createContinuousScalarRendererSeat(...args);
    const captured: ScalarRendererSeat = Object.freeze({
      get view() { return seat.view; }, cancel: (reason: 'authority') => seat.cancel(reason),
      attachRenderer() { const lease = seat.attachRenderer(); h.scalarLeases.push(lease); return lease; },
    });
    h.scalarSeats.push(captured); return captured;
  } };
});
vi.mock('../primitives/control-instruments/control-instrument-renderer.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../primitives/control-instruments/control-instrument-renderer.svelte')>();
  const capture = <T extends { attachRenderer(): unknown; destroy(): void }>(seat: T): T => {
    const captured = { attachRenderer() { const lease = seat.attachRenderer();
      h.finiteLeases.push(lease as FiniteRendererLease<unknown>); return lease; },
    destroy() { seat.destroy(); } } as T;
    h.finiteSeats.push(captured); return captured;
  };
  return { ...actual,
    createActionRendererSeat: (...args: Parameters<typeof actual.createActionRendererSeat>) =>
      capture(actual.createActionRendererSeat(...args)),
    createToggleRendererSeat: (...args: Parameters<typeof actual.createToggleRendererSeat>) =>
      capture(actual.createToggleRendererSeat(...args)),
    createAbsoluteChoiceRendererSeat: (...args: Parameters<typeof actual.createAbsoluteChoiceRendererSeat>) =>
      capture(actual.createAbsoluteChoiceRendererSeat(...args)),
  };
});
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const mockedMethods = (...names: string[]) => Object.fromEntries(names.map((name) => {
    let fn = h.calls.get(name);
    if (!fn) { fn = vi.fn(); h.calls.set(name, fn); }
    return [name, fn];
  }));
  return {
  ...(await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>()),
  makeVfoHandlers: () => mockedMethods('onVfoSelect', 'onMainFreqChange', 'onSubFreqChange', 'onSplitToggle', 'onDualWatchToggle',
    'onMainVfoClick', 'onSubVfoClick', 'onEqual', 'onSwap', 'onQuickSplit', 'onQuickDw'),
  makeSystemHandlers: () => mockedMethods('onSpeak'),
  makeVoxHandlers: () => mockedMethods('onVoxToggle', 'onVoxGainChange', 'onAntiVoxGainChange', 'onVoxDelayChange'),
  makeTxHandlers: () => mockedMethods('onRfPowerChange', 'onMicGainChange', 'onDriveGainChange',
    'onAtuToggle', 'onAtuTune', 'onCompToggle', 'onCompLevelChange', 'onMonToggle', 'onMonLevelChange'),
  makeModeHandlers: () => mockedMethods('onModInputChange', 'onModeChange', 'onDataModeChange'),
  makeFilterHandlers: () => mockedMethods('onFilterChange', 'onFilterWidthChange', 'onFilterShapeChange',
    'onIfShiftChange', 'onPbtInnerChange', 'onPbtOuterChange'),
  makeRxAudioHandlers: () => mockedMethods('onMonitorModeChange', 'onAfLevelChange'),
  makeAudioRoutingHandlers: () => mockedMethods('onFocusChange', 'onSplitStereoChange'),
  makeDspHandlers: () => mockedMethods('onNrModeChange', 'onNrLevelChange', 'onNbToggle', 'onNbLevelChange',
    'onNbDepthChange', 'onNbWidthChange', 'onNotchModeChange', 'onNotchFreqChange', 'onManualNotchWidthChange'),
  makeAgcHandlers: () => mockedMethods('onAgcModeChange'), makeRfFrontEndHandlers: () => mockedMethods('onAttChange',
    'onPreChange', 'onRfGainChange', 'onSquelchChange', 'onDigiSelToggle', 'onIpPlusToggle'),
  makeBandHandlers: () => mockedMethods('onBandSelect'),
  makeAntennaHandlers: () => mockedMethods('onSelectAnt1', 'onSelectAnt2', 'onToggleRxAnt'),
  makeRitXitHandlers: () => mockedMethods('onRitToggle', 'onXitToggle', 'onRitOffsetChange', 'onXitOffsetChange', 'onClear'),
  makeScanHandlers: () => mockedMethods('onScanStart', 'onScanStop', 'onDfSpanChange', 'onResumeChange'),
  makeCwPanelHandlers: () => mockedMethods('onKeySpeedChange', 'onCwPitchChange', 'onBreakInDelayChange',
    'onBreakInModeChange', 'onApfChange', 'onTwinPeakToggle', 'onReversePaddleToggle'),
  makeScopeControlsHandlers: () => mockedMethods('onModeChange', 'onEdgeChange', 'onSpanChange', 'onSpeedChange',
    'onHoldChange', 'onRefChange', 'onDualChange', 'onReceiverChange', 'onDuringTxChange',
    'onCenterTypeChange', 'onVbwChange', 'onRbwChange'),
  };
});

import SemanticRadioSurfaces, { type ExternalPresentation } from '../components-v2/wiring/SemanticRadioSurfaces.svelte';
import FaceA from '../../component-kit-api/fixtures/external-kit/src/FaceA.svelte';
import FaceB from '../../component-kit-api/fixtures/external-kit/src/FaceB.svelte';
import ScalarRenderer from '../../component-kit-api/fixtures/external-kit/src/FixtureScalarRenderer.svelte';
import FrequencyRenderer from '../../component-kit-api/fixtures/external-kit/src/FixtureFrequencyRenderer.svelte';
import SignalRenderer from '../../component-kit-api/fixtures/external-kit/src/FixtureSignalMeter.svelte';
import LevelRenderer from '../../component-kit-api/fixtures/external-kit/src/FixtureLevelMeter.svelte';
import ActionRenderer from '../../component-kit-api/fixtures/external-kit/src/FixtureActionRenderer.svelte';
import ToggleRenderer from '../../component-kit-api/fixtures/external-kit/src/FixtureToggleRenderer.svelte';
import ChoiceRenderer from '../../component-kit-api/fixtures/external-kit/src/FixtureChoiceRenderer.svelte';
import ExternalScalarRenderer from '../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import ProbeFace from './fixtures/ExternalHostedFaceProbe.svelte';
import { SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan } from '../presentation/workspace/resolution';

const fresh = { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1 };
const txFields = ['tunerStatus', 'voxOn', 'voxGain', 'antiVoxGain', 'voxDelay', 'compressorOn',
  'compressorLevel', 'monitorOn', 'monitorGain', 'powerLevel', 'micGain', 'driveGain'] as const;
function state(receivers = 2): ServerState {
  const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });
  const receiver = (freqHz: number, sMeter: number) => ({ ...slot(freqHz), filter: 1, activeSlot: 'A',
    vfoA: slot(freqHz), vfoB: slot(freqHz + 50_000), sMeter, afLevel: 100, rfGain: 200, squelch: 0,
    att: 0, preamp: 0, nb: false, nr: false });
  const paths = ['active', 'split', 'dualWatch', 'main', 'main.freqHz', 'main.mode', 'main.filter',
    'main.activeSlot', 'main.sMeter', ...txFields];
  for (const vfo of ['vfoA', 'vfoB']) paths.push(`main.${vfo}.freqHz`, `main.${vfo}.mode`, `main.${vfo}.filterNum`);
  if (receivers === 2) {
    paths.push('sub', 'sub.freqHz', 'sub.mode', 'sub.filter', 'sub.activeSlot', 'sub.sMeter');
    for (const vfo of ['vfoA', 'vfoB']) paths.push(`sub.${vfo}.freqHz`, `sub.${vfo}.mode`, `sub.${vfo}.filterNum`);
  }
  return { stateContractVersion: 1, providerGeneration: 1, revision: 1, stateRevision: 1,
    freshnessRevision: 1, observationSeq: 1, active: 'MAIN', ptt: false, split: false, dualWatch: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14_250_000 },
    main: receiver(14_250_000, 20), ...(receivers === 2 ? { sub: receiver(7_100_000, -12) } : {}),
    tunerStatus: 0, voxOn: false, voxGain: 50, antiVoxGain: 30, voxDelay: 10,
    compressorOn: false, compressorLevel: 40, monitorOn: false, monitorGain: 60,
    powerLevel: 0.8, micGain: 128, driveGain: 128,
    fieldStatus: Object.fromEntries(paths.map((path) => [path, fresh])), connection: {},
  } as unknown as ServerState;
}
function caps(receivers = 2): Capabilities {
  return { stateContractVersion: 1, providerGeneration: 1, model: 'fixture', scope: false, audio: false, tx: true,
    capabilities: ['tx', 'split', 'dual_watch', 'vfo_equalize', 'vfo_swap', 'speech', 'vox',
      'compressor', 'monitor', 'tuner', 'drive_gain', ...(receivers === 2 ? ['dual_rx'] : [])],
    receivers, vfoScheme: receivers === 2 ? 'main_sub' : 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    meterCalibrations: { s_meter: [{ raw: 0, actual: -54, label: 'S0' }, { raw: 130, actual: 0, label: 'S9' }] },
  } as unknown as Capabilities;
}
const authority = () => ({ state: h.state, caps: h.caps, session: h.session,
  rxAudioTarget: { muted: true, rxEnabled: false } });
const appearances = { scalar: { name: 'fixture-a', hbar: ScalarRenderer, knob: ScalarRenderer,
  bipolar: ScalarRenderer, discrete: ScalarRenderer }, frequency: FrequencyRenderer,
  finite: { action: ActionRenderer, toggle: ToggleRenderer, choice: ChoiceRenderer },
  meter: { signal: SignalRenderer, level: LevelRenderer } } as const;
let recordSequence = 0;
async function loadedRecord(
  id: string, component: HostedFaceComponentV1,
  recordAppearances: ExternalPresentationRecord['appearances'] = appearances,
  resources: ExternalPresentationRecord['resources'] = [],
): Promise<Pick<ExternalPresentation, 'record' | 'component'>> {
  const batch = prepareExternalPresentationBatch([{ id: `presentation-${id}-${recordSequence++}`,
    kind: 'external-instruments-v1', loader: async () => component,
    resources, layoutId: `layout-${id}`, appearances: recordAppearances } satisfies ExternalPresentationRecord]);
  commitExternalPresentationBatch(batch); const record = batch.records[0];
  expect(getPresentationRecord(record.id)).toBe(record);
  return { record, component: await loadSkin(record) };
}
function occurrence(
  loaded: Pick<ExternalPresentation, 'record' | 'component'>, current: { value: object },
): ExternalPresentation {
  let result!: ExternalPresentation;
  result = Object.freeze({ ...loaded, isCurrent: () => current.value === result });
  return result;
}
const fullPlan = () => new Map([['receiver-zone', ['vfo']], ['tx-zone', ['txAux']],
  ['meters-zone', ['meters']]]) as SurfacePlan;
const planWithoutMeters = () =>
  new Map([['receiver-zone', ['vfo']], ['tx-zone', ['txAux']]]) as SurfacePlan;
/** The six station LEVEL meters are structural only where `deriveMeters` sees
 *  their raw field, and `projectBarMeters` additionally drops `compression`
 *  while the compressor reads off. The signal meter needs none of these — it
 *  comes from `state()`'s own `main.sMeter`. */
function stationState(): ServerState {
  return { ...state(), compressorOn: true, powerMeter: 40, swrMeter: 30, alcMeter: 20,
    compMeter: 10, vdMeter: 60, idMeter: 50 } as unknown as ServerState;
}
/** Drops the active receiver's S meter, leaving SWR the only structural
 *  member of the host's signal-or-SWR composite. */
function stationStateWithoutSignal(): ServerState {
  const base = stationState();
  const { sMeter: _sMeter, ...main } = base.main as unknown as Record<string, unknown>;
  return { ...base, main } as unknown as ServerState;
}
const stationSection = () => target.querySelector('[data-family="stationMeters"]');
const stationKeys = () => Array.from(
  target.querySelectorAll<HTMLElement>('[data-family="stationMeters"] [data-fixture-level],'
    + ' [data-family="stationMeters"] [data-fixture-signal]'),
  (node) => node.dataset.fixtureLevel ?? 'signal',
);
const STATION_ORDER = ['signal', 'power', 'swr', 'alc', 'drainCurrent', 'drainVoltage', 'compression'];
let target: HTMLDivElement; let mounted: ReturnType<typeof mount> | null = null;
const clearCalls = () => h.calls.forEach((fn) => fn.mockClear());
const callCounts = () => Object.fromEntries([...h.calls].map(([name, fn]) => [name, fn.mock.calls.length]));
function expectOnly(name: string): void {
  expect(call(name), name).toHaveBeenCalledOnce();
  expect([...h.calls.values()].reduce((total, fn) => total + fn.mock.calls.length, 0)).toBe(1);
}

beforeEach(() => { h.state = proxy(state()); h.caps = proxy(caps()); h.session = { state: 'connected', epoch: 7 };
  h.calls.forEach((fn) => fn.mockClear()); h.subscribers.clear(); h.radioListeners.clear();
  h.subscribeCount = 0; h.unsubscribeCount = 0;
  h.omitSpeak = false; h.meterAppearance = appearances.meter;
  h.frequencyOwners.length = 0; h.frequencyLeases.length = 0; h.meterOwners.length = 0;
  h.barMeterOwners.length = 0;
  h.scalarOwners.length = 0; h.finiteSeats.length = 0;
  h.scalarSeats.length = 0; h.scalarLeases.length = 0; h.finiteLeases.length = 0; });
afterEach(() => { if (mounted) unmount(mounted); mounted = null; document.body.replaceChildren(); });

describe('external hosted face chain', () => {
  it('mounts the accepted faces through the exact 4+8+8 mapping and retires old occurrence commands', async () => {
    const current = { value: {} };
    const loadedA = await loadedRecord('a', FaceA);
    const appearancesB = { ...appearances, scalar: { name: 'fixture-b',
      hbar: ExternalScalarRenderer, knob: ExternalScalarRenderer } };
    const loadedB = await loadedRecord('b', FaceB, appearancesB, ['hardware-scope']);
    const a1 = occurrence(loadedA, current); current.value = a1;
    const props = proxy({ externalPresentation: a1 }); const plan = writable<SurfacePlan | null>(fullPlan());
    const livePlan = fromStore(plan); target = document.createElement('div'); document.body.appendChild(target);
    mounted = mount(SemanticRadioSurfaces, { target, props,
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => livePlan.current]]) }); flushSync();
    expect(target.querySelector('[data-external-face="a"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-fixture-frequency]')).toHaveLength(2);
    expect(target.querySelectorAll('[data-fixture-signal]')).toHaveLength(3);
    expect(target.querySelectorAll('[data-fixture-toggle],[data-fixture-choice],[data-fixture-action]')).toHaveLength(8);
    expect(target.querySelectorAll('[data-fixture-scalar]')).toHaveLength(8);
    expect(h.frequencyOwners).toHaveLength(2); expect(h.frequencyLeases).toHaveLength(2);
    expect(h.meterOwners.length).toBeGreaterThanOrEqual(2);
    const receiverOwners = [...h.frequencyOwners, ...h.meterOwners.slice(-2)];
    const finiteSeats = [...h.finiteSeats]; expect(finiteSeats.length).toBeGreaterThanOrEqual(8);
    const txOwners = h.scalarOwners.slice(-8); expect(txOwners).toHaveLength(8);
    expect(h.scalarSeats).toHaveLength(8); expect(h.scalarLeases).toHaveLength(8);
    const oldScalarSeat = h.scalarSeats[0]; const automaticScalarLease = h.scalarLeases[0];
    const cleanupScalarLease = h.scalarLeases[1];
    const oldFiniteLease = h.finiteLeases[3] as FiniteRendererLease<unknown> & { invoke(): void };
    const scalarPresentation = (field: string) => {
      const host = target.querySelector<HTMLElement>(`[data-testid="tx-aux-${field}"]`)!;
      const renderer = host.querySelector<HTMLElement>('[data-fixture-scalar]')!;
      return [host.dataset.scalarForm, renderer.dataset.compact,
        renderer.querySelector('span') !== null, renderer.querySelector('output') !== null];
    };
    expect(scalarPresentation('rfPower')).toEqual(['hbar', 'false', true, true]);
    expect(scalarPresentation('micGain')).toEqual(['knob', 'true', true, true]);
    expect(scalarPresentation('voxGain')).toEqual(['hbar', 'true', true, true]);
    const operate = (selector: string, names: string[]) =>
      Array.from(target.querySelectorAll<HTMLButtonElement>(selector)).forEach((button, index) => {
        clearCalls(); button.click(); expectOnly(names[index]);
      });
    operate('[data-fixture-toggle]', ['onSplitToggle', 'onDualWatchToggle']);
    operate('[data-fixture-action]', ['onEqual', 'onSwap', 'onQuickSplit', 'onQuickDw', 'onSpeak']);
    clearCalls(); target.querySelectorAll<HTMLButtonElement>('[data-fixture-choice] button')[1].click();
    expectOnly('onSubVfoClick');
    const scalarCalls = ['onRfPowerChange', 'onMicGainChange', 'onDriveGainChange', 'onVoxGainChange',
      'onAntiVoxGainChange', 'onVoxDelayChange', 'onCompLevelChange', 'onMonLevelChange'];
    target.querySelectorAll<HTMLInputElement>('[data-fixture-scalar] input').forEach((input, index) => {
      clearCalls(); input.value = String(index + 1);
      input.dispatchEvent(new InputEvent('input', { bubbles: true })); expectOnly(scalarCalls[index]);
    });
    const frequencyButtons = target.querySelectorAll<HTMLButtonElement>('[data-fixture-frequency] button');
    frequencyButtons[0].click();
    frequencyButtons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(call('onMainFreqChange')).toHaveBeenCalledOnce();
    const subFrequencyButton = frequencyButtons[frequencyButtons.length - 1];
    clearCalls(); subFrequencyButton.click();
    subFrequencyButton.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expectOnly('onSubFreqChange');
    clearCalls();
    const oldButton = target.querySelector<HTMLButtonElement>('[data-fixture-action]')!;
    const oldInput = target.querySelector<HTMLInputElement>('[data-fixture-scalar] input')!;
    const oldFrequency = frequencyButtons[0];
    const oldFrequencyLease = h.frequencyLeases[0];
    clearCalls(); oldFiniteLease.invoke(); expectOnly('onEqual');
    clearCalls(); automaticScalarLease.nativeInput(21); expectOnly('onRfPowerChange');
    clearCalls();
    const subscriptions = h.subscribers.size;

    const b = occurrence(loadedB, current); current.value = b; props.externalPresentation = b;
    oldButton.click(); oldInput.value = '17'; oldInput.dispatchEvent(new InputEvent('input', { bubbles: true }));
    oldFrequency.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    oldFrequencyLease.interaction.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowUp' }));
    oldFiniteLease.invoke();
    const lateScalarLease = oldScalarSeat.attachRenderer();
    lateScalarLease.nativeInput(22); lateScalarLease.cancel('authority'); lateScalarLease.dispose();
    expect([...h.calls.values()].every((fn) => fn.mock.calls.length === 0)).toBe(true);
    flushSync();
    expect(target.querySelector('[data-external-face="b"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(8);
    expect(h.subscribers.size).toBe(subscriptions);
    expect([...h.frequencyOwners, ...h.meterOwners.slice(-2)]).toEqual(receiverOwners);
    expect(h.finiteSeats).toEqual(finiteSeats);
    expect(h.scalarOwners.slice(-8)).toEqual(txOwners);
    automaticScalarLease.nativeInput(21); oldFiniteLease.invoke();
    oldButton.click(); oldInput.dispatchEvent(new InputEvent('input', { bubbles: true }));
    oldFrequency.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect([...h.calls.values()].every((fn) => fn.mock.calls.length === 0)).toBe(true);
    const bRenderer = target.querySelector<HTMLButtonElement & { rendererLease: ScalarRendererLease }>(
      '[data-testid="tx-aux-rfPower"] [data-external-scalar-renderer]',
    )!;
    const bLease = bRenderer.rendererLease; const token = bLease.beginPointer()!;
    bLease.pointer(token, 0.5); const bInteraction = bLease.view.interaction;
    cleanupScalarLease.cancel('authority'); cleanupScalarLease.dispose(); oldScalarSeat.cancel('authority');
    expect(bLease.view.interaction).toBe(bInteraction);
    clearCalls(); bLease.nativeInput(0.6); expectOnly('onRfPowerChange');
    clearCalls();
    target.querySelector<HTMLButtonElement>('[data-fixture-action]')!.click();
    expect(call('onEqual')).toHaveBeenCalledOnce();

    clearCalls();
    const a2 = occurrence(loadedA, current); current.value = a2; props.externalPresentation = a2; flushSync();
    expect(target.querySelector('[data-external-face="a"]')).not.toBeNull();
    target.querySelector<HTMLButtonElement>('[data-fixture-action]')!.click();
    expect(call('onEqual')).toHaveBeenCalledOnce();
    oldButton.click(); expect(call('onEqual')).toHaveBeenCalledOnce();
    const countsAtA2 = callCounts();
    automaticScalarLease.nativeInput(23); oldFiniteLease.invoke();
    oldFrequencyLease.interaction.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowUp' }));
    expect(callCounts()).toEqual(countsAtA2);
    expect([...h.frequencyOwners, ...h.meterOwners.slice(-2)]).toEqual(receiverOwners);
    expect(h.finiteSeats).toEqual(finiteSeats); expect(h.scalarOwners.slice(-8)).toEqual(txOwners);
    unmount(mounted!); mounted = null;
    expect(h.subscribers.size).toBe(0); expect(h.unsubscribeCount).toBe(h.subscribeCount);
  });

  it('derives family and SUB absence only from the live plan and structural owners', async () => {
    const current = { value: {} }; const a = occurrence(await loadedRecord('plan', FaceA), current);
    current.value = a;
    const props = proxy({ externalPresentation: a }); const plan = writable<SurfacePlan | null>(fullPlan());
    const livePlan = fromStore(plan); target = document.createElement('div'); document.body.appendChild(target);
    mounted = mount(SemanticRadioSurfaces, { target, props,
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => livePlan.current]]) }); flushSync();
    const face = target.querySelector('[data-external-face="a"]'); const subscriptions = h.subscribers.size;
    const owners = [...h.frequencyOwners, ...h.meterOwners, ...h.scalarOwners, ...h.finiteSeats];
    expect(Array.from(target.querySelectorAll<HTMLElement>('[data-fixture-signal]'),
      (meter) => meter.dataset.value)).toEqual(['20', '-12', '20']);
    plan.set(new Map([['tx-zone', ['txAux']]]) as SurfacePlan); flushSync();
    expect(target.querySelector('[data-family="receiver"]')).toBeNull();
    expect(target.querySelector('[data-family="vfoOperations"]')).toBeNull();
    expect(target.querySelector('[data-family="txAux"]')).not.toBeNull();
    plan.set(new Map([['receiver-zone', ['vfo']]]) as SurfacePlan); flushSync();
    expect(target.querySelector('[data-family="txAux"]')).toBeNull();
    expect(target.querySelector('[data-family="receiver"]')).not.toBeNull();
    expect(target.querySelector('[data-external-face="a"]')).toBe(face);
    expect(h.subscribers.size).toBe(subscriptions);
    expect([...h.frequencyOwners, ...h.meterOwners, ...h.scalarOwners, ...h.finiteSeats]).toEqual(owners);

    h.state = state(1); h.caps = caps(1); h.subscribers.forEach((handler) => handler(authority() as never)); flushSync();
    const singleReceiverOwners = [...h.frequencyOwners, ...h.meterOwners, ...h.scalarOwners, ...h.finiteSeats];
    expect(target.querySelectorAll('[data-fixture-frequency]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-fixture-signal]')).toHaveLength(1);
    h.session = { state: 'disconnected', epoch: 7 };
    h.subscribers.forEach((handler) => handler(authority() as never)); flushSync();
    expect(target.querySelector('[data-family="receiver"]')).not.toBeNull();
    expect(target.querySelector('[data-family="vfoOperations"]')).not.toBeNull();
    plan.set(null); flushSync();
    expect(target.querySelectorAll('[data-family]')).toHaveLength(0);
    expect([...h.frequencyOwners, ...h.meterOwners, ...h.scalarOwners, ...h.finiteSeats])
      .toEqual(singleReceiverOwners);

    plan.set(fullPlan()); h.state = proxy({ ...state(), fieldStatus: {} });
    h.caps = proxy({ ...caps(), capabilities: ['tx'] });
    h.subscribers.forEach((handler) => handler(authority() as never)); flushSync();
    expect(target.querySelector('[data-family="receiver"]')).not.toBeNull();
    expect(target.querySelector('[data-family="vfoOperations"]')).not.toBeNull();
    expect(target.querySelector('[data-family="txAux"]')).not.toBeNull();

    unmount(mounted!); mounted = null; h.omitSpeak = true; h.session = { state: 'connected', epoch: 7 };
    h.state = proxy(state()); h.caps = proxy({ ...caps(),
      capabilities: caps().capabilities.filter((tag) => tag !== 'speech' && tag !== 'monitor') });
    plan.set(fullPlan()); mounted = mount(SemanticRadioSurfaces, { target, props,
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => livePlan.current]]) }); flushSync();
    expect(target.querySelector('[data-family="vfoOperations"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-fixture-toggle],[data-fixture-choice],[data-fixture-action]')).toHaveLength(7);
    expect(target.querySelector('[data-family="txAux"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-fixture-scalar]')).toHaveLength(7);
    expect(h.subscribers.size).toBe(subscriptions);
  });

  it('forwards independent public options and exposes only the exact public keys', async () => {
    const current = { value: {} };
    const probeAppearances = { ...appearances, scalar: { name: 'probe',
      hbar: ScalarRenderer, knob: ExternalScalarRenderer } };
    const probe = occurrence(await loadedRecord('probe', ProbeFace, probeAppearances), current);
    current.value = probe; const plan = writable<SurfacePlan | null>(fullPlan()); const livePlan = fromStore(plan);
    target = document.createElement('div'); document.body.appendChild(target);
    mounted = mount(SemanticRadioSurfaces, { target, props: { externalPresentation: probe },
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => livePlan.current]]) }); flushSync();
    const face = target.querySelector<HTMLElement>('[data-external-face="probe"]')!;
    expect(face.dataset.topLevelKeys).toBe('receiver,stationMeters,txAux,vfoOperations');
    expect(face.dataset.receiverKeys).toBe('mainFrequency,mainSMeter,subFrequency,subSMeter');
    expect(face.dataset.vfoKeys).toBe('activeReceiver,dualWatch,equalize,quickDualWatch,quickSplit,speak,split,swap');
    expect(face.dataset.txKeys).toBe('antiVoxGain,compressorLevel,driveGain,micGain,monitorLevel,rfPower,voxDelay,voxGain');
    expect(target.querySelector('[data-probe="form-hbar"] [data-fixture-scalar]')).not.toBeNull();
    const knob = target.querySelector<HTMLElement>('[data-probe="form-knob"] [data-external-scalar-renderer]')!;
    expect(knob.dataset.externalScalarRenderer).toBe('knob');
    expect(target.querySelector<HTMLElement>('[data-probe="compact"] [data-fixture-scalar]')!.dataset.compact).toBe('true');
    expect(target.querySelector<HTMLElement>('[data-probe="label"] [data-external-scalar-renderer]')!.dataset)
      .toMatchObject({ showLabel: 'false', showValue: 'true', compact: 'false' });
    expect(target.querySelector<HTMLElement>('[data-probe="value"] [data-external-scalar-renderer]')!.dataset)
      .toMatchObject({ showLabel: 'true', showValue: 'false', compact: 'false' });
    const owners = [...h.scalarOwners];
    const invoke = (selector: string, name: string, value?: string) => {
      clearCalls(); const node = target.querySelector<HTMLInputElement | HTMLButtonElement>(selector)!;
      if (node instanceof HTMLInputElement) { node.value = value!; node.dispatchEvent(new InputEvent('input', { bubbles: true })); }
      else node.click();
      expectOnly(name);
    };
    invoke('[data-probe="form-hbar"] input', 'onRfPowerChange', '0.7');
    invoke('[data-probe="form-knob"] button', 'onMicGainChange');
    invoke('[data-probe="compact"] input', 'onDriveGainChange', '70');
    invoke('[data-probe="label"] button', 'onVoxGainChange');
    invoke('[data-probe="value"] button', 'onAntiVoxGainChange');
    expect(h.scalarOwners).toEqual(owners);
  });

  it('re-creates the face on an occurrence change with the same component and keeps its owners', async () => {
    const current = { value: {} };
    const loaded = await loadedRecord('keying', FaceA);
    const a1 = occurrence(loaded, current); current.value = a1;
    const props = proxy({ externalPresentation: a1 }); const plan = writable<SurfacePlan | null>(fullPlan());
    const livePlan = fromStore(plan); target = document.createElement('div'); document.body.appendChild(target);
    mounted = mount(SemanticRadioSurfaces, { target, props,
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => livePlan.current]]) }); flushSync();
    const faceA1 = target.querySelector('[data-external-face="a"]');
    const owners = [...h.frequencyOwners, ...h.meterOwners, ...h.scalarOwners, ...h.finiteSeats];
    const subscriptions = h.subscribers.size;
    const oldButton = target.querySelector<HTMLButtonElement>('[data-fixture-action]')!;
    const oldLease = h.finiteLeases[3] as FiniteRendererLease<unknown> & { invoke(): void };
    const a1b = occurrence(loaded, current); current.value = a1b; props.externalPresentation = a1b; flushSync();
    const faceA1b = target.querySelector('[data-external-face="a"]');
    expect(faceA1b).not.toBeNull(); expect(faceA1b).not.toBe(faceA1);
    expect([...h.frequencyOwners, ...h.meterOwners, ...h.scalarOwners, ...h.finiteSeats]).toEqual(owners);
    expect(h.subscribers.size).toBe(subscriptions);
    clearCalls(); oldLease.invoke(); oldButton.click();
    expect([...h.calls.values()].every((fn) => fn.mock.calls.length === 0)).toBe(true);
    clearCalls(); target.querySelector<HTMLButtonElement>('[data-fixture-action]')!.click();
    expectOnly('onEqual');
  });

  const mountStation = (
    presentation: ExternalPresentation, plan: ReturnType<typeof writable<SurfacePlan | null>>,
  ) => {
    const props = proxy({ externalPresentation: presentation }); const livePlan = fromStore(plan);
    mounted = mount(SemanticRadioSurfaces, { target, props,
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => livePlan.current]]) }); flushSync();
    return props;
  };

  it('renders the seven station meters in the order each face asks for', async () => {
    h.state = proxy(stationState());
    const current = { value: {} };
    const loadedA = await loadedRecord('station-a', FaceA);
    const loadedB = await loadedRecord('station-b', FaceB);
    const a = occurrence(loadedA, current); current.value = a;
    target = document.createElement('div'); document.body.appendChild(target);
    const props = mountStation(a, writable<SurfacePlan | null>(fullPlan()));
    expect(stationSection()).not.toBeNull();
    expect(stationKeys()).toEqual(STATION_ORDER);
    const b = occurrence(loadedB, current); current.value = b; props.externalPresentation = b; flushSync();
    expect(stationKeys()).toEqual([...STATION_ORDER].reverse());
  });

  it('renders station meters from the record appearance when the global selection is absent', async () => {
    h.state = proxy(stationState()); h.meterAppearance = undefined;
    const current = { value: {} }; const a = occurrence(await loadedRecord('station-global-absent', FaceA), current);
    current.value = a;
    target = document.createElement('div'); document.body.appendChild(target);
    mountStation(a, writable<SurfacePlan | null>(fullPlan()));
    expect(stationKeys()).toEqual(STATION_ORDER);
    expect(target.querySelectorAll('[data-family="receiver"] [data-fixture-signal]')).toHaveLength(2);
  });

  it('admits and withdraws the station family from the meters zone alone', async () => {
    h.state = proxy(stationState());
    const current = { value: {} }; const a = occurrence(await loadedRecord('station-zone', FaceA), current);
    current.value = a; const plan = writable<SurfacePlan | null>(fullPlan());
    target = document.createElement('div'); document.body.appendChild(target);
    mountStation(a, plan);
    expect(stationKeys()).toEqual(STATION_ORDER);
    const subscriptions = h.subscribers.size;
    const owners = [...h.meterOwners, ...h.barMeterOwners, ...h.frequencyOwners, ...h.scalarOwners];
    plan.set(planWithoutMeters()); flushSync();
    expect(stationSection()).toBeNull(); expect(stationKeys()).toEqual([]);
    expect(h.subscribers.size).toBe(subscriptions);
    plan.set(fullPlan()); flushSync();
    expect(stationKeys()).toEqual(STATION_ORDER);
    expect([...h.meterOwners, ...h.barMeterOwners, ...h.frequencyOwners, ...h.scalarOwners]).toEqual(owners);
    expect(h.subscribers.size).toBe(subscriptions);
  });

  it('keeps one station owner set across an A-B-A occurrence swap', async () => {
    h.state = proxy(stationState());
    const current = { value: {} };
    const loadedA = await loadedRecord('station-swap-a', FaceA);
    const loadedB = await loadedRecord('station-swap-b', FaceB);
    const a1 = occurrence(loadedA, current); current.value = a1;
    target = document.createElement('div'); document.body.appendChild(target);
    const props = mountStation(a1, writable<SurfacePlan | null>(fullPlan()));
    expect(h.barMeterOwners).toHaveLength(6);
    const stationOwners = [...h.barMeterOwners]; const signalOwners = [...h.meterOwners];
    const subscriptions = h.subscribers.size;
    const b = occurrence(loadedB, current); current.value = b; props.externalPresentation = b; flushSync();
    expect(stationKeys()).toEqual([...STATION_ORDER].reverse());
    const a2 = occurrence(loadedA, current); current.value = a2; props.externalPresentation = a2; flushSync();
    expect(stationKeys()).toEqual(STATION_ORDER);
    expect(h.barMeterOwners).toEqual(stationOwners); expect(h.meterOwners).toEqual(signalOwners);
    expect(h.subscribers.size).toBe(subscriptions);
  });

  it('renders no station signal meter while only SWR is structural', async () => {
    h.state = proxy(stationStateWithoutSignal());
    const current = { value: {} }; const a = occurrence(await loadedRecord('station-swr', FaceA), current);
    current.value = a;
    target = document.createElement('div'); document.body.appendChild(target);
    mountStation(a, writable<SurfacePlan | null>(fullPlan()));
    expect(stationSection()!.querySelectorAll('[data-fixture-signal]')).toHaveLength(0);
    expect(stationSection()!.querySelector('[data-fixture-level="swr"]')).not.toBeNull();
    expect(stationKeys()).toEqual(STATION_ORDER.filter((key) => key !== 'signal'));
    h.state = proxy(stationState());
    h.subscribers.forEach((handler) => handler(authority() as never)); flushSync();
    expect(stationKeys()).toEqual(STATION_ORDER);
  });

  it('disposes the station reset leases and its subscription on unmount', async () => {
    h.state = proxy(stationState());
    const current = { value: {} }; const loaded = await loadedRecord('station-dispose', FaceA);
    const a = occurrence(loaded, current); current.value = a;
    const plan = writable<SurfacePlan | null>(fullPlan());
    target = document.createElement('div'); document.body.appendChild(target);
    mountStation(a, plan);
    expect(stationKeys()).toEqual(STATION_ORDER);
    const withStation = h.finiteLeases.length;
    // FaceA renders the station section last, so the trailing renderer leases
    // are the reset-peak leases of the three meters `installResetSeat` seats a
    // reset control for. The second mount below measures that count.
    const disposals = h.finiteLeases.slice(-3).map((lease) => {
      const release = lease.dispose.bind(lease); const spy = vi.fn(release);
      (lease as { dispose: () => void }).dispose = spy; return spy;
    });
    unmount(mounted!); mounted = null;
    for (const spy of disposals) expect(spy).toHaveBeenCalledOnce();
    expect(h.subscribers.size).toBe(0); expect(h.unsubscribeCount).toBe(h.subscribeCount);
    h.finiteLeases.length = 0; plan.set(planWithoutMeters());
    const bare = occurrence(loaded, current); current.value = bare; mountStation(bare, plan);
    expect(stationSection()).toBeNull();
    expect(withStation - h.finiteLeases.length).toBe(3);
  });
});
