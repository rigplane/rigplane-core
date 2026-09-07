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

import ExternalScalarRenderer from '../../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import type { Skin } from '../../components-v2/controls/value-control/skin';
import type {
  ContinuousScalarBinding,
  ContinuousScalarRendererLease,
} from '../../primitives/scalar/continuous-scalar.svelte';
import Fixture from './fixtures/RxAudioInstrumentHostFixture.svelte';
import type { RxAudioViewModel } from '../radio-view-model';
import type {
  RxAudioAuthorityPublication as Publication,
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
