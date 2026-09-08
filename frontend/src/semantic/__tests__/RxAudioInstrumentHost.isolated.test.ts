import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { Capabilities, VfoScheme } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const activation = vi.hoisted(() => ({ selected: undefined as unknown }));
const scalarCapture = vi.hoisted(() => ({ bindings: [] as unknown[] }));
vi.mock('../../component-kits/activation', () => ({
  getSelectedScalarAppearance: () => activation.selected,
}));
vi.mock('../../primitives/scalar/continuous-scalar.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../primitives/scalar/continuous-scalar.svelte')>();
  return {
    ...actual,
    createContinuousScalar: (...args: Parameters<typeof actual.createContinuousScalar>) => {
      const binding = actual.createContinuousScalar(...args);
      scalarCapture.bindings.push(binding);
      return binding;
    },
  };
});

import { MOD_INPUT_SOURCES } from '$lib/radio/mod-input';
import ExternalScalarRenderer from '../../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import type { Skin } from '../../components-v2/controls/value-control/skin';
import {
  createFiniteRendererContext, type FiniteControlAppearance,
} from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type {
  ContinuousScalarBinding,
  ContinuousScalarRendererLease,
} from '../../primitives/scalar/continuous-scalar.svelte';
import Fixture from './fixtures/RxAudioInstrumentHostFixture.svelte';
import { topologyFixtures, withRxAudio } from '../fixtures/topologies';
import type { AudioFocus, RadioViewModel, RxAudioViewModel } from '../radio-view-model';
import {
  FOCUS_CHOICES, MONITOR_MODES, SPLIT_CHOICES,
} from '../rx-audio-instruments';
import type {
  RxAudioAuthorityPublication as Publication,
  RxAudioFiniteChoiceValue,
  SubscribeRxAudioAuthority,
} from '../rx-audio-instruments';

type RendererNode = HTMLButtonElement & { readonly rendererLease: ContinuousScalarRendererLease };
type Props = {
  publication: Publication;
  rxAudio: RxAudioViewModel | undefined;
  subscribeControlAuthority: SubscribeRxAudioAuthority;
  layout: 'grouped' | 'independent';
  onAfLevelChange?: (value: number) => void;
};

function capabilities(receivers = 2, generation = 1, scheme?: VfoScheme): Capabilities {
  return {
    model: 'TEST', scope: false, audio: true, tx: false,
    capabilities: receivers === 2 ? ['dual_rx', 'audio', 'af_level'] : ['audio', 'af_level'],
    receivers, vfoScheme: scheme ?? (receivers === 2 ? 'main_sub' : 'single'),
    freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: generation,
  } as Capabilities;
}

const observed = () => ({
  observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1,
});
function state(options: {
  receivers?: number; generation?: number; active?: 'MAIN' | 'SUB'; activeKnown?: boolean;
} = {}): ServerState {
  const { receivers = 2, generation = 1, active = 'MAIN', activeKnown = true } = options;
  const receiver = (afLevel: number) => ({
    freqHz: 14_250_000, mode: 'USB', filter: 1, dataMode: 0, afLevel,
    sMeter: 0, att: 0, preamp: 0, nb: false, nr: false, rfGain: 255, squelch: 0,
  });
  const paths = ['main', 'main.afLevel'];
  if (activeKnown) paths.push('active');
  if (receivers === 2) paths.push('sub', 'sub.afLevel');
  return {
    stateContractVersion: 1, providerGeneration: generation, revision: 1, stateRevision: 1,
    freshnessRevision: 1, observationSeq: 1, updatedAt: '2026-09-06T00:00:00Z',
    active, ptt: false, split: false, dualWatch: false, tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: receiver(0.2), ...(receivers === 2 ? { sub: receiver(0.6) } : {}),
    connection: {} as ServerState['connection'],
    fieldStatus: Object.fromEntries(paths.map((path) => [path, observed()])),
  } as ServerState;
}

function publication(options: {
  receivers?: number; generation?: number; capsGeneration?: number;
  active?: 'MAIN' | 'SUB'; activeKnown?: boolean; scheme?: VfoScheme;
  session?: Publication['session']['state']; epoch?: number; muted?: boolean; rxEnabled?: boolean;
  nullState?: boolean;
} = {}): Publication {
  const receivers = options.receivers ?? 2;
  const generation = options.generation ?? 1;
  return {
    state: options.nullState ? null : state({
      receivers, generation, active: options.active, activeKnown: options.activeKnown,
    }),
    caps: capabilities(receivers, options.capsGeneration ?? generation, options.scheme),
    session: { state: options.session ?? 'connected', epoch: options.epoch ?? 1 },
    rxAudioTarget: { muted: options.muted ?? false, rxEnabled: options.rxEnabled ?? false },
  };
}

const unknownField = {
  reading: { status: 'unknown' as const },
  availability: { structural: false, operational: false },
};
function audio(
  value: number | null,
  operational = true,
  monitorMode: RxAudioViewModel['monitorMode'] = 'local',
): RxAudioViewModel {
  return {
    monitorMode, liveAudio: { structural: true, operational: true },
    afLevel: {
      reading: value === null ? { status: 'unknown' } : { status: 'known', value },
      availability: { structural: true, operational },
    },
    routingFocus: unknownField, routingSplit: unknownField,
    modInputSource: unknownField, modInputReadiness: { status: 'not-applicable' },
  } as RxAudioViewModel;
}

class Publisher {
  handlers = new Set<(next: Publication) => void>();
  constructor(public current: Publication) {}
  subscribe: SubscribeRxAudioAuthority = (handler) => {
    this.handlers.add(handler); handler(this.current);
    return () => { this.handlers.delete(handler); };
  };
  emit(next: Publication): void {
    this.current = next;
    this.handlers.forEach((handler) => handler(next));
  }
}

let target: HTMLDivElement;
let components: ReturnType<typeof mount>[] = [];
beforeEach(() => {
  target = document.createElement('div'); document.body.appendChild(target);
  activation.selected = { name: 'RX AF test', hbar: ExternalScalarRenderer } satisfies Skin;
  scalarCapture.bindings = [];
});
afterEach(() => {
  components.forEach((component) => unmount(component)); components = [];
  activation.selected = undefined; target.remove();
});

function render(initial = publication(), initialAudio = audio(0.2)) {
  const publisher = new Publisher(initial);
  const onAfLevelChange = vi.fn<(value: number) => void>();
  const props = proxy<Props>({
    publication: initial, rxAudio: initialAudio,
    subscribeControlAuthority: publisher.subscribe,
    layout: 'grouped', onAfLevelChange,
  });
  const component = mount(Fixture, { target, props }); components.push(component); flushSync();
  const slider = () => target.querySelector<RendererNode>('[data-external-scalar-renderer]')!;
  const transition = (next: Publication, nextAudio: RxAudioViewModel) => {
    props.publication = next; props.rxAudio = nextAudio; publisher.emit(next);
  };
  return { publisher, props, onAfLevelChange, slider, transition, component };
}

describe('RxAudioInstrumentHost', () => {
  const changes = [
    ['session', () => publication({ epoch: 2 })],
    ['provider', () => publication({ generation: 2 })],
    ['topology', () => publication({ scheme: 'ab_shared' })],
    ['receiver', () => publication({ active: 'SUB' })],
    ['receiver knowledge', () => publication({ activeKnown: false })],
    ['mute', () => publication({ muted: true })],
    ['live route', () => publication({ rxEnabled: true })],
  ] as const;

  it.each(changes)('cancels an old pointer across same-task %s A-B-A', (_name, middle) => {
    const r = render(); const lease = r.slider().rendererLease;
    const token = lease.beginPointer(); expect(token).not.toBeNull();
    r.transition(middle(), audio(0.7));
    r.transition(publication(), audio(0.35));
    lease.pointer(token!, 0.9); lease.endPointer(token!);
    expect(r.onAfLevelChange).not.toHaveBeenCalled();
    expect(lease.view.canonical).toBe(0.35);
    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(r.onAfLevelChange).toHaveBeenCalledExactlyOnceWith(0.36);
  });

  it.each([
    ['pointer', 0.73, (lease: ContinuousScalarRendererLease) => {
      const token = lease.beginPointer()!; lease.pointer(token, 0.73); lease.endPointer(token);
    }],
    ['native', 0.73, (lease: ContinuousScalarRendererLease) => lease.nativeInput(0.73)],
    ['key', 0.71, (lease: ContinuousScalarRendererLease) => lease.key({ key: 'ArrowRight', fine: false })],
    ['wheel', 0.76, (lease: ContinuousScalarRendererLease) => lease.wheel({ direction: 1, fine: false })],
  ] as const)('uses the fresh same-task presentation for %s input', (_name, expected, act) => {
    const r = render(); const lease = r.slider().rendererLease;
    r.transition(publication({ rxEnabled: true }), audio(0.7, true, 'live'));
    act(lease);
    expect(r.onAfLevelChange).toHaveBeenCalledExactlyOnceWith(expected);
  });

  it('keeps a known reading but rejects it when its presentation source disagrees with the raw route', () => {
    const r = render(); const lease = r.slider().rendererLease;
    r.transition(publication({ rxEnabled: true }), audio(0.7));
    expect(lease.view.canonical).toBe(0.7); expect(lease.view.editable).toBe(false);
    r.props.rxAudio = audio(0.7, true, 'live');
    lease.nativeInput(0.72);
    expect(r.onAfLevelChange).toHaveBeenCalledExactlyOnceWith(0.72);
  });

  it('preserves readings while authority or availability makes the control inert, then recovers', () => {
    const r = render(publication({ nullState: true }), audio(0.41));
    const lease = r.slider().rendererLease;
    expect(lease.view.canonical).toBe(0.41); expect(lease.view.editable).toBe(false);
    r.transition(publication({ generation: 1, capsGeneration: 2 }), audio(0.42));
    expect(lease.view.canonical).toBe(0.42); expect(lease.view.editable).toBe(false);
    r.transition(publication({ session: 'disconnected', epoch: -1 }), audio(0.43));
    expect(lease.view.canonical).toBe(0.43); expect(lease.view.editable).toBe(false);
    r.transition(publication(), audio(0.44, false));
    expect(lease.view.canonical).toBe(0.44); expect(lease.view.editable).toBe(false);
    r.transition(publication(), audio(null));
    expect(lease.view.canonical).toBeNull(); expect(lease.view.editable).toBe(false);
    r.transition(publication({ generation: 2 }), audio(0.5));
    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(r.onAfLevelChange).toHaveBeenCalledExactlyOnceWith(0.51);
  });

  it('updates value evidence without canceling an unchanged command target', () => {
    const r = render(); const lease = r.slider().rendererLease;
    const token = lease.beginPointer()!;
    r.props.rxAudio = audio(0.3);
    expect(lease.view.canonical).toBe(0.3);
    r.transition(publication(), audio(0.31));
    lease.pointer(token, 0.4); lease.endPointer(token);
    expect(r.onAfLevelChange).toHaveBeenCalledExactlyOnceWith(0.4);
  });

  it('uses the adapter single-receiver MAIN fact and rejects unknown dual-RX routing', () => {
    const single = publication({ receivers: 1, activeKnown: false });
    const r = render(single);
    expect(r.slider().rendererLease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    r.transition(publication({ activeKnown: false }), audio(0.3));
    expect(r.slider().rendererLease.view.canonical).toBe(0.3);
    expect(r.slider().rendererLease.view.editable).toBe(false);
  });

  it('retains one binding across layout replacement and retires the detached renderer lease', () => {
    const r = render(); const binding = scalarCapture.bindings[0] as ContinuousScalarBinding;
    const oldLease = r.slider().rendererLease;
    r.props.layout = 'independent'; flushSync();
    expect(scalarCapture.bindings).toEqual([binding]);
    expect(oldLease.key({ key: 'ArrowRight', fine: false })).toBe(false);
    expect(r.slider().rendererLease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(r.onAfLevelChange).toHaveBeenCalledExactlyOnceWith(0.21);
  });

  it('has no reset default and tears down the subscriber, binding, and active gesture', () => {
    const r = render(); const lease = r.slider().rendererLease;
    lease.reset(); expect(r.onAfLevelChange).not.toHaveBeenCalled();
    const token = lease.beginPointer()!;
    unmount(r.component); components = components.filter((item) => item !== r.component);
    expect(r.publisher.handlers.size).toBe(0);
    lease.pointer(token, 0.8); lease.endPointer(token);
    expect(r.onAfLevelChange).not.toHaveBeenCalled();
  });
});

/**
 * RX-B/RX-C — the five finite handles (`monitorMode`, `routingFocus`,
 * `routingSplit`, `modInputSource`, `setModInputLan`). Per the RX-B/RX-C
 * census, these are discrete choice/action instruments recomputed reactively
 * every render, not scalars with drag-gesture state — so the correctness
 * burden here is the finite seat/lease discipline (an inactive renderer's
 * lease must not invoke after a context swap), exercised below through the
 * shared `FiniteControlRendererFixture` test double, the same one
 * `DspInstrumentHost.isolated.test.ts` uses for its own finite seats.
 */
describe('RxAudioInstrumentHost finite handles (RX-B/RX-C)', () => {
  const appearance = {
    action: FiniteControlRendererFixture as FiniteControlAppearance<RxAudioFiniteChoiceValue>['action'],
    toggle: FiniteControlRendererFixture as FiniteControlAppearance<RxAudioFiniteChoiceValue>['toggle'],
    choice: FiniteControlRendererFixture as FiniteControlAppearance<RxAudioFiniteChoiceValue>['choice'],
  } satisfies FiniteControlAppearance<RxAudioFiniteChoiceValue>;

  const rxBase = (): RadioViewModel => withRxAudio(topologyFixtures['1/single']);
  const withRx = (over: Partial<RxAudioViewModel>): RadioViewModel => {
    const view = rxBase();
    return { ...view, rxAudio: { ...view.rxAudio!, ...over } };
  };
  const AVAILABLE = { structural: true, operational: true };
  const UNAVAILABLE = { structural: false, operational: false };
  type Handlers = {
    onMonitorMode?: (mode: RxAudioViewModel['monitorMode']) => void;
    onRoutingFocus?: (focus: AudioFocus) => void;
    onRoutingSplit?: (split: boolean) => void;
    onModInputChange?: (source: number) => void;
    onSetModInputLan?: () => void;
  };

  function renderFinite(view: RadioViewModel, handlers: Handlers = {}) {
    const publication: Publication = {
      state: null, caps: null, session: { state: 'connected', epoch: 1 },
      rxAudioTarget: { muted: false, rxEnabled: false },
    };
    const component = mount(Fixture, { target, props: {
      view, publication, ...handlers,
      subscribeControlAuthority: (handler) => { handler(publication); return () => undefined; },
      finiteAppearance: appearance, rendererContext: createFiniteRendererContext(),
    } });
    components.push(component);
    flushSync();
    return { component };
  }

  beforeEach(() => resetRetainedInvocations());

  it('routes each finite handle to its own callback with its own value, never crossed', () => {
    const onMonitorMode = vi.fn();
    const onRoutingFocus = vi.fn();
    const onRoutingSplit = vi.fn();
    const onModInputChange = vi.fn();
    const onSetModInputLan = vi.fn();
    const view = withRx({ modInputReadiness: { status: 'mismatch', source: 0 } });
    renderFinite(view, {
      onMonitorMode, onRoutingFocus, onRoutingSplit, onModInputChange, onSetModInputLan,
    });

    for (const mode of MONITOR_MODES) {
      target.querySelector<HTMLButtonElement>(`[data-testid="external-Monitor mode-${mode}"]`)!.click();
    }
    expect(onMonitorMode.mock.calls).toEqual(MONITOR_MODES.map((mode) => [mode]));

    for (const focus of FOCUS_CHOICES) {
      target.querySelector<HTMLButtonElement>(`[data-testid="external-Audio focus-${focus}"]`)!.click();
    }
    expect(onRoutingFocus.mock.calls).toEqual(FOCUS_CHOICES.map((focus) => [focus]));

    // The split seat's external choice VALUE is the STRING label ('on'/'off'),
    // not the raw boolean — the Component-Kit SDK's `FiniteChoiceValue` bound
    // is `string | number`, never `boolean` (`rx-audio-instruments.ts`'s own
    // `RxAudioSplitLabel` doc comment). The callback still receives the
    // mapped boolean.
    for (const [, label] of SPLIT_CHOICES) {
      target.querySelector<HTMLButtonElement>(`[data-testid="external-Stereo split-${label}"]`)!.click();
    }
    expect(onRoutingSplit.mock.calls).toEqual(SPLIT_CHOICES.map(([value]) => [value]));

    for (const option of MOD_INPUT_SOURCES) {
      target.querySelector<HTMLButtonElement>(`[data-testid="external-MOD input-${option.value}"]`)!.click();
    }
    expect(onModInputChange.mock.calls).toEqual(MOD_INPUT_SOURCES.map((option) => [option.value]));

    target.querySelector<HTMLButtonElement>('[data-testid="external-Set LAN"]')!.click();
    expect(onSetModInputLan).toHaveBeenCalledTimes(1);

    // Cross-check: none of the five leaked into a sibling's callback.
    expect(onMonitorMode).toHaveBeenCalledTimes(MONITOR_MODES.length);
    expect(onRoutingFocus).toHaveBeenCalledTimes(FOCUS_CHOICES.length);
    expect(onRoutingSplit).toHaveBeenCalledTimes(SPLIT_CHOICES.length);
    expect(onModInputChange).toHaveBeenCalledTimes(MOD_INPUT_SOURCES.length);
  });

  // The split seat's reading is the LABEL ('on'/'off') mapped from the raw
  // `routingSplit` boolean fact by `splitLabelOf`; these two rows are literal
  // (not derived via `SPLIT_CHOICES.find`) so a `splitLabelOf` that maps the
  // fact to the wrong label is caught rather than agreed with.
  it("reports the split seat's data-reading and aria-checked from a known-true routingSplit", () => {
    renderFinite(withRx({
      routingSplit: { reading: { status: 'known', value: true }, availability: AVAILABLE },
    }));
    expect(
      target.querySelector('[data-testid="external-Stereo split"]')?.getAttribute('data-reading'),
    ).toBe('on');
    expect(
      target.querySelector('[data-testid="external-Stereo split-on"]')?.getAttribute('aria-checked'),
    ).toBe('true');
    expect(
      target.querySelector('[data-testid="external-Stereo split-off"]')?.getAttribute('aria-checked'),
    ).toBe('false');
  });

  it("reports the split seat's data-reading and aria-checked from a known-false routingSplit", () => {
    renderFinite(withRx({
      routingSplit: { reading: { status: 'known', value: false }, availability: AVAILABLE },
    }));
    expect(
      target.querySelector('[data-testid="external-Stereo split"]')?.getAttribute('data-reading'),
    ).toBe('off');
    expect(
      target.querySelector('[data-testid="external-Stereo split-off"]')?.getAttribute('aria-checked'),
    ).toBe('true');
    expect(
      target.querySelector('[data-testid="external-Stereo split-on"]')?.getAttribute('aria-checked'),
    ).toBe('false');
  });

  it('offers `live` only while liveAudio is structural, never re-derived from anything else', () => {
    const { component: absent } = renderFinite(withRx({ liveAudio: UNAVAILABLE }));
    expect(target.querySelector('[data-testid="external-Monitor mode-live"]')).toBeNull();
    expect(target.querySelector('[data-testid="external-Monitor mode-local"]')).not.toBeNull();
    unmount(absent);
    components = components.filter((item) => item !== absent);

    renderFinite(withRx({ liveAudio: AVAILABLE }));
    expect(target.querySelectorAll('[data-testid="external-Monitor mode-live"]')).toHaveLength(1);
  });

  it('renders no external composition for a structurally-absent focus, split or MOD-input group', () => {
    renderFinite(withRx({
      routingFocus: { reading: { status: 'unknown' }, availability: UNAVAILABLE },
      routingSplit: { reading: { status: 'unknown' }, availability: UNAVAILABLE },
      modInputSource: { reading: { status: 'unknown' }, availability: UNAVAILABLE },
      modInputReadiness: { status: 'unknown' },
    }));
    expect(target.querySelector('[data-testid="external-Audio focus"]')).toBeNull();
    expect(target.querySelector('[data-testid="external-Stereo split"]')).toBeNull();
    expect(target.querySelector('[data-testid="external-MOD input"]')).toBeNull();
  });

  it('blocks an unrecognized MOD-input reading and offers no LAN remedy outside a mismatch', () => {
    const onModInputChange = vi.fn();
    const onSetModInputLan = vi.fn();
    renderFinite(
      withRx({
        modInputSource: { reading: { status: 'known', value: 99 }, availability: AVAILABLE },
        modInputReadiness: { status: 'unknown' },
      }),
      { onModInputChange, onSetModInputLan },
    );
    const modExternal = target.querySelector('[data-testid="external-MOD input"]')!;
    expect(modExternal).not.toBeNull();
    target.querySelector<HTMLButtonElement>('[data-testid="external-MOD input-0"]')!.click();
    expect(onModInputChange).not.toHaveBeenCalled();
    expect(target.querySelector('[data-testid="external-Set LAN"]')).toBeNull();
    expect(onSetModInputLan).not.toHaveBeenCalled();
  });

  it(
    'keeps the monitor-mode (absolute choice) and Set-LAN (action) seats owner-correct '
    + 'across an A-B-A context swap: a detached lease stays inert, the active one still emits once',
    () => {
      const onMonitorMode = vi.fn();
      const onSetModInputLan = vi.fn();
      const a = createFiniteRendererContext();
      const b = createFiniteRendererContext();
      const view = withRx({ modInputReadiness: { status: 'mismatch', source: 0 } });
      const publication: Publication = {
        state: null, caps: null, session: { state: 'connected', epoch: 1 },
        rxAudioTarget: { muted: false, rxEnabled: false },
      };
      const props = proxy({
        view, publication,
        subscribeControlAuthority: ((handler) => {
          handler(publication); return () => undefined;
        }) as SubscribeRxAudioAuthority,
        finiteAppearance: appearance, rendererContext: a, onMonitorMode, onSetModInputLan,
      });
      const component = mount(Fixture, { target, props });
      flushSync();
      const staleMonitor = retainedInvocations.get('Monitor mode')!;
      const staleLan = retainedInvocations.get('Set LAN')!;
      expect(staleMonitor).toBeDefined();
      expect(staleLan).toBeDefined();

      props.rendererContext = b;
      flushSync();
      const activeMonitor = retainedInvocations.get('Monitor mode')!;
      const activeLan = retainedInvocations.get('Set LAN')!;

      staleMonitor('mute');
      staleLan();
      expect(onMonitorMode).not.toHaveBeenCalled();
      expect(onSetModInputLan).not.toHaveBeenCalled();

      activeMonitor('mute');
      activeLan();
      expect(onMonitorMode).toHaveBeenCalledExactlyOnceWith('mute');
      expect(onSetModInputLan).toHaveBeenCalledTimes(1);

      // Swap A back: the (new) A lease is live, the old B lease is now stale.
      props.rendererContext = a;
      flushSync();
      const finalMonitor = retainedInvocations.get('Monitor mode')!;
      const finalLan = retainedInvocations.get('Set LAN')!;
      activeLan();
      expect(onSetModInputLan).toHaveBeenCalledTimes(1);

      // Host teardown (`onDestroy`'s seat.destroy() loop) is the LAST fence:
      // a lease retained past `unmount` must stay inert too, independent of
      // any later context swap ever happening.
      unmount(component);
      finalMonitor('local');
      finalLan();
      expect(onMonitorMode).toHaveBeenCalledTimes(1);
      expect(onSetModInputLan).toHaveBeenCalledTimes(1);
    },
  );
});
