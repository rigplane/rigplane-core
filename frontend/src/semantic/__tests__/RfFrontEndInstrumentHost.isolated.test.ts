import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { Capabilities, VfoScheme } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';

const activation = vi.hoisted(() => ({ selected: undefined as unknown }));
const captures = vi.hoisted(() => ({
  pairs: [] as unknown[], scalars: [] as unknown[], lifecycle: [] as string[],
}));
vi.mock('../../component-kits/activation', () => ({
  getSelectedScalarAppearance: () => activation.selected,
}));
vi.mock('../../primitives/scalar/continuous-pair.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../primitives/scalar/continuous-pair.svelte')>();
  return {
    ...actual,
    createContinuousPair: (...args: Parameters<typeof actual.createContinuousPair>) => {
      const binding = actual.createContinuousPair(...args);
      const destroy = binding.destroy.bind(binding);
      vi.spyOn(binding, 'cancel');
      vi.spyOn(binding, 'destroy').mockImplementation(() => {
        captures.lifecycle.push('pair-destroy'); destroy();
      });
      captures.pairs.push(binding);
      return binding;
    },
  };
});
vi.mock('../../primitives/scalar/continuous-scalar.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../primitives/scalar/continuous-scalar.svelte')>();
  return {
    ...actual,
    createContinuousScalar: (...args: Parameters<typeof actual.createContinuousScalar>) => {
      const binding = actual.createContinuousScalar(...args);
      const destroy = binding.destroy.bind(binding);
      vi.spyOn(binding, 'cancel');
      vi.spyOn(binding, 'destroy').mockImplementation(() => {
        captures.lifecycle.push('scalar-destroy'); destroy();
      });
      captures.scalars.push(binding);
      return binding;
    },
  };
});

import ExternalScalarRenderer from '../../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import type { Skin } from '../../components-v2/controls/value-control/skin';
import type { ContinuousPairBinding } from '../../primitives/scalar/continuous-pair.svelte';
import type { ContinuousScalarBinding } from '../../primitives/scalar/continuous-scalar.svelte';
import Fixture from './fixtures/RfFrontEndInstrumentHostFixture.svelte';
import type {
  RfFrontEndAuthorityPublication as Publication,
  RfFrontEndLevelFeedback,
  RfSqlControlModel,
  SubscribeRfFrontEndAuthority,
} from '../rf-front-end-instruments';

const fresh = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 1,
};
const observed = () => ({ ...fresh });
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

function capabilities(options: {
  receivers?: number; generation?: number; scheme?: VfoScheme;
  controlModel?: RfSqlControlModel; omit?: 'rf_gain' | 'squelch';
} = {}): Capabilities {
  const receivers = options.receivers ?? 2;
  const tags = ['audio', 'dual_rx', 'rf_gain', 'squelch']
    .filter((tag) => tag !== options.omit);
  return {
    stateContractVersion: 1, providerGeneration: options.generation ?? 1,
    model: 'RF-HOST-TEST', scope: false, audio: true, tx: false, capabilities: tags,
    receivers, vfoScheme: options.scheme ?? (receivers === 2 ? 'main_sub' : 'single'),
    freqRanges: [], modes: [], filters: [], preValues: [], attValues: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    rfSqlControlModel: options.controlModel ?? 'combined',
  } as Capabilities;
}

function state(options: {
  receivers?: number; generation?: number; active?: 'MAIN' | 'SUB'; activeKnown?: boolean;
  rfGain?: number; squelch?: number;
} = {}): ServerState {
  const receivers = options.receivers ?? 2;
  const receiver = (freqHz: number) => ({
    ...slot(freqHz), vfoA: slot(freqHz), vfoB: slot(freqHz + 50_000), activeSlot: 'A',
    filter: 1, afLevel: 0.5, rfGain: options.rfGain ?? 1, squelch: options.squelch ?? 0,
    sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
  });
  const paths = ['split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter',
    'main.activeSlot', 'main.rfGain', 'main.squelch'];
  if (options.activeKnown !== false) paths.push('active');
  if (receivers === 2) paths.push('sub.freqHz', 'sub.mode', 'sub.filter', 'sub.activeSlot',
    'sub.rfGain', 'sub.squelch');
  return {
    stateContractVersion: 1, providerGeneration: options.generation ?? 1,
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-09-07T00:00:00Z', active: options.active ?? 'MAIN',
    ptt: false, split: false, dualWatch: false, tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: receiver(14_250_000), ...(receivers === 2 ? { sub: receiver(14_300_000) } : {}),
    connection: {} as ServerState['connection'],
    fieldStatus: Object.fromEntries(paths.map((path) => [path, observed()])),
  } as ServerState;
}

function publication(options: {
  epoch?: number; generation?: number; capsGeneration?: number; receivers?: number;
  scheme?: VfoScheme; active?: 'MAIN' | 'SUB'; activeKnown?: boolean;
  controlModel?: RfSqlControlModel; omit?: 'rf_gain' | 'squelch'; rfGain?: number; squelch?: number;
  sessionState?: Publication['session']['state'];
} = {}): Publication {
  const generation = options.generation ?? 1;
  const receivers = options.receivers ?? 2;
  return {
    state: state({
      generation, receivers, active: options.active, activeKnown: options.activeKnown,
      rfGain: options.rfGain, squelch: options.squelch,
    }),
    caps: capabilities({
      generation: options.capsGeneration ?? generation, receivers, scheme: options.scheme,
      controlModel: options.controlModel, omit: options.omit,
    }),
    session: { state: options.sessionState ?? 'connected', epoch: options.epoch ?? 1 },
  };
}

type LaneFeedback = RfFrontEndLevelFeedback['rf'];
function commandFeedback(
  over: Partial<Record<'rf' | 'sql', Partial<LaneFeedback['feedback']>>> = {},
): RfFrontEndLevelFeedback {
  const lane = (name: 'rf' | 'sql', confirmed: number): LaneFeedback => ({
    command: name === 'rf' ? 'set_rf_gain' : 'set_squelch',
    feedback: {
      confirmed, target: null, requestedTarget: null, phase: 'idle', busy: false,
      availability: 'available', lifecycleId: null, transitionId: null, outcome: null,
      providerGeneration: 1, sessionEpoch: 1,
      scope: { control: name === 'rf' ? 'rf-gain' : 'squelch', receiver: 0 },
      repeatPolicy: 'latest-target-wins', ...over[name],
    },
  });
  return { rf: lane('rf', 0.5), sql: lane('sql', 0.2) };
}

class Publisher {
  handlers = new Set<(next: Publication) => void>();
  constructor(public current: Publication) {}
  subscribe: SubscribeRfFrontEndAuthority = (handler) => {
    this.handlers.add(handler); handler(this.current);
    return () => { captures.lifecycle.push('unsubscribe'); this.handlers.delete(handler); };
  };
  emit(next: Publication): void {
    this.current = next;
    this.handlers.forEach((handler) => handler(next));
  }
}

type Props = {
  publication: Publication;
  view: ReturnType<typeof toRadioViewModel>;
  subscribeControlAuthority: SubscribeRfFrontEndAuthority;
  controlModel: RfSqlControlModel;
  rfSqlFeedback?: RfFrontEndLevelFeedback | null;
  layout: 'grouped' | 'independent';
  onLevelChange: (field: 'rfGain' | 'squelch', value: number) => void;
};

let target: HTMLDivElement;
let mounted: ReturnType<typeof mount>[] = [];
beforeEach(() => {
  target = document.createElement('div'); document.body.appendChild(target);
  activation.selected = { name: 'RF host test', hbar: ExternalScalarRenderer } satisfies Skin;
  captures.pairs = []; captures.scalars = []; captures.lifecycle = [];
});
afterEach(() => {
  mounted.forEach((component) => unmount(component)); mounted = [];
  activation.selected = undefined; target.remove();
});

function render(initial = publication(), rfSqlFeedback?: RfFrontEndLevelFeedback | null) {
  const publisher = new Publisher(initial);
  const onLevelChange = vi.fn<Props['onLevelChange']>();
  const props = proxy<Props>({
    publication: initial, view: toRadioViewModel(initial.state, initial.caps),
    subscribeControlAuthority: publisher.subscribe,
    controlModel: initial.caps?.rfSqlControlModel ?? 'separate',
    rfSqlFeedback, layout: 'grouped', onLevelChange,
  });
  const component = mount(Fixture, { target, props }); mounted.push(component); flushSync();
  const transition = (next: Publication) => {
    props.publication = next;
    props.view = toRadioViewModel(next.state, next.caps);
    props.controlModel = next.caps?.rfSqlControlModel ?? 'separate';
    publisher.emit(next);
  };
  const setFeedback = (next: RfFrontEndLevelFeedback | null | undefined) => {
    props.rfSqlFeedback = next;
  };
  return { publisher, props, component, onLevelChange, transition, setFeedback };
}

describe('RfFrontEndInstrumentHost', () => {
  it('creates and destroys one pair plus its three scalar primitives exactly once', () => {
    const r = render();
    const pair = captures.pairs[0] as ContinuousPairBinding;
    const scalars = [...captures.scalars] as ContinuousScalarBinding[];
    expect(captures.pairs).toHaveLength(1);
    expect(captures.scalars).toHaveLength(3);
    expect(r.publisher.handlers).toHaveLength(1);
    expect(target.querySelector('[data-handle-kind="combined"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-slot]')).toHaveLength(1);
    unmount(r.component); mounted = [];
    expect(r.publisher.handlers).toHaveLength(0);
    expect(captures.lifecycle[0]).toBe('unsubscribe');
    expect(pair.destroy).toHaveBeenCalledOnce();
    for (const scalar of scalars) expect(scalar.destroy).toHaveBeenCalledOnce();
  });

  it.each([
    ['session', () => publication({ epoch: 2 })],
    ['session state', () => publication({ sessionState: 'reconnecting' })],
    ['provider', () => publication({ generation: 2 })],
    ['provider mismatch', () => publication({ capsGeneration: 2 })],
    ['topology', () => publication({ scheme: 'ab_shared' })],
    ['receiver', () => publication({ active: 'SUB' })],
    ['receiver knowledge', () => publication({ activeKnown: false })],
    ['effective form', () => publication({ controlModel: 'separate' })],
  ] as const)('cancels a pointer across synchronous %s A-B-A', (_name, middle) => {
    const r = render();
    const binding = captures.pairs[0] as ContinuousPairBinding;
    const lease = binding.attachRenderer();
    const token = lease.beginPointer(); expect(token).not.toBeNull();

    r.transition(middle());
    r.transition(publication());
    lease.pointer(token!, 1); lease.endPointer(token!);
    expect(r.onLevelChange).not.toHaveBeenCalled();

    const fresh = binding.attachRenderer();
    expect(lease.key({ key: 'End', fine: false })).toBe(false);
    expect(fresh.key({ key: 'End', fine: false })).toBe(true);
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('squelch', 1);
  });

  it('replaces combined with exactly two separate handles without recreating owners', () => {
    const r = render();
    const pair = captures.pairs[0] as ContinuousPairBinding;
    const scalars = [...captures.scalars] as ContinuousScalarBinding[];
    r.transition(publication({ controlModel: 'separate' })); flushSync();

    expect(target.querySelector('[data-handle-kind="separate"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-slot]')).toHaveLength(2);
    expect(captures.pairs).toEqual([pair]);
    expect(captures.scalars).toEqual(scalars);
  });

  it('falls back to separate handles and renders only the structurally present field', () => {
    render(publication({ omit: 'squelch' }));
    expect(target.querySelector('[data-handle-kind="separate"]')).not.toBeNull();
    expect(target.querySelectorAll('[role="slider"]')).toHaveLength(1);
    expect(target.querySelector('[data-testid="rf-front-end-rfGain"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="rf-front-end-squelch"]')).toBeNull();
  });

  it('keeps undefined compatibility, explicit null, and authoritative feedback distinct', () => {
    const r = render();
    const group = () => target.querySelector<HTMLElement>('[data-testid="rf-front-end-rf-sql"]')!;
    const slider = () => group().querySelector<HTMLElement>('[role="slider"]')!;
    expect(group().dataset.feedbackIntegration).toBe('compatibility-reading');
    expect(group().textContent).toContain('100%');
    expect(slider().getAttribute('aria-disabled')).toBe('false');

    r.setFeedback(null); flushSync();
    expect(group().dataset.feedbackIntegration).toBe('authority-unresolved');
    expect(group().textContent).toContain('? / ?');
    expect(slider().getAttribute('aria-disabled')).toBe('true');

    r.setFeedback(commandFeedback()); flushSync();
    expect(group().dataset.feedbackIntegration).toBe('command-feedback');
    expect(group().textContent).toContain('50% / 20%');
    expect(slider().getAttribute('aria-disabled')).toBe('false');

    r.setFeedback(commandFeedback({ rf: { availability: 'unavailable' } })); flushSync();
    expect(group().dataset.observed).toBe('false');
    expect(slider().getAttribute('aria-disabled')).toBe('true');
  });

  it('keeps one pair announcement outside a keyed renderer replacement', () => {
    const r = render(publication(), commandFeedback());
    r.setFeedback(commandFeedback({ rf: {
      target: 0.75, requestedTarget: 0.75, phase: 'awaiting-confirmation', busy: true,
      lifecycleId: 'rf-75', transitionId: 'rf-awaiting-75',
    } }));
    flushSync();
    const statuses = () => target.querySelectorAll<HTMLElement>('[data-control-feedback-status]');
    expect(statuses()).toHaveLength(1);
    expect(statuses()[0].textContent).toContain('Awaiting confirmation: 0.75');
    expect(target.querySelector('[data-testid="rf-front-end-rf-sql-rf-status"]')?.textContent)
      .toContain('requested 75%; confirmed 50%');

    r.props.layout = 'independent'; flushSync();
    expect(statuses()).toHaveLength(1);
    expect(statuses()[0].textContent).toContain('Awaiting confirmation: 0.75');
  });

  it('keeps one failed scalar announcement and its error across renderer replacement', () => {
    activation.selected = undefined;
    const initial = publication({ controlModel: 'separate' });
    const r = render(initial, commandFeedback());
    r.setFeedback(commandFeedback({ rf: {
      requestedTarget: 0.4, phase: 'failed', lifecycleId: 'rf-40',
      transitionId: 'rf-failed-40', outcome: { phase: 'failed', error: 'denied' },
    } }));
    flushSync();
    const statuses = () => target.querySelectorAll<HTMLElement>('[data-control-feedback-status]');
    expect(statuses()).toHaveLength(1);
    expect(statuses()[0].textContent).toContain('Failed: 0.4: denied');
    expect(target.querySelector('[data-testid="rf-front-end-rfGain-status"]')?.textContent)
      .toContain('requested 40%; confirmed 50%; denied');

    r.props.layout = 'independent'; flushSync();
    expect(statuses()).toHaveLength(1);
    expect(statuses()[0].textContent).toContain('Failed: 0.4: denied');
  });

  it('retires issued status when authority changes', () => {
    const r = render(publication(), commandFeedback());
    r.setFeedback(commandFeedback({ rf: {
      requestedTarget: 0.4, phase: 'failed', lifecycleId: 'rf-40',
      transitionId: 'rf-failed-40', outcome: { phase: 'failed', error: 'denied' },
    } }));
    flushSync();
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(1);
    r.transition(publication({ generation: 2 })); flushSync();
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(0);
  });

  it.each(['native', 'key', 'wheel'] as const)('makes stale %s input inert after same-task A-B-A', (kind) => {
    const r = render();
    const binding = captures.pairs[0] as ContinuousPairBinding;
    const stale = binding.attachRenderer();
    r.transition(publication({ generation: 2 }));
    r.transition(publication());
    if (kind === 'native') stale.nativeInput(1);
    else if (kind === 'key') expect(stale.key({ key: 'End', fine: false })).toBe(false);
    else stale.wheel({ direction: 1, fine: false });
    expect(r.onLevelChange).not.toHaveBeenCalled();

    const fresh = binding.attachRenderer();
    expect(fresh.key({ key: 'End', fine: false })).toBe(true);
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('squelch', 1);
  });

  it.each(['pointer', 'native', 'key', 'wheel'] as const)(
    'makes a stale standalone scalar %s inert while a fresh lease dispatches', (kind) => {
      const initial = publication({ controlModel: 'separate' });
      const r = render(initial);
      const binding = captures.scalars.at(-1) as ContinuousScalarBinding;
      const stale = binding.attachRenderer();
      const token = kind === 'pointer' ? stale.beginPointer() : null;
      r.transition(publication({ controlModel: 'separate', generation: 2 }));
      r.transition(initial);
      if (kind === 'pointer') { stale.pointer(token!, 1); stale.endPointer(token!); }
      else if (kind === 'native') stale.nativeInput(1);
      else if (kind === 'key') expect(stale.key({ key: 'End', fine: false })).toBe(false);
      else stale.wheel({ direction: 1, fine: false });
      expect(r.onLevelChange).not.toHaveBeenCalled();

      expect(binding.attachRenderer().key({ key: 'End', fine: false })).toBe(true);
      expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('squelch', 1);
    },
  );

  it.each([
    ['combined reading', 'combined', 'disconnected', undefined],
    ['separate feedback', 'separate', 'reconnecting', commandFeedback()],
  ] as const)('keeps fresh %s input inert without current authority', (
    _name, controlModel, sessionState, feedback,
  ) => {
    const initial = publication({ controlModel });
    const r = render(initial, feedback);
    const binding = controlModel === 'combined'
      ? captures.pairs[0] as ContinuousPairBinding
      : captures.scalars.at(-1) as ContinuousScalarBinding;

    r.transition(publication({ controlModel, sessionState })); flushSync();
    const inactive = [...target.querySelectorAll<HTMLElement>('[role="slider"]')].at(-1)!;
    expect(inactive.getAttribute('aria-disabled')).toBe('true');
    expect(binding.attachRenderer().key({ key: 'End', fine: false })).toBe(false);
    if (controlModel === 'combined') {
      inactive.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    } else {
      const node = inactive as HTMLElement & {
        rendererLease: ReturnType<ContinuousScalarBinding['attachRenderer']>;
      };
      expect(node.rendererLease.key({ key: 'End', fine: false })).toBe(false);
    }
    expect(r.onLevelChange).not.toHaveBeenCalled();

    r.transition(initial); flushSync();
    const recovered = [...target.querySelectorAll<HTMLElement>('[role="slider"]')].at(-1)!;
    expect(recovered.getAttribute('aria-disabled')).toBe('false');
    if (controlModel === 'combined') {
      recovered.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    } else {
      const node = recovered as typeof inactive & {
        rendererLease: ReturnType<ContinuousScalarBinding['attachRenderer']>;
      };
      expect(node.rendererLease.key({ key: 'End', fine: false })).toBe(true);
    }
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('squelch', 1);
  });

  it('does not cancel a healthy gesture for ordinary value and feedback updates', () => {
    const r = render(publication(), commandFeedback());
    const pair = captures.pairs[0] as ContinuousPairBinding;
    const scalars = captures.scalars.slice(-2) as ContinuousScalarBinding[];
    const lease = pair.attachRenderer();
    const token = lease.beginPointer(); expect(token).not.toBeNull();

    r.transition(publication({ rfGain: 0.8, squelch: 0.1 }));
    r.setFeedback(commandFeedback({ rf: { confirmed: 0.8 }, sql: { confirmed: 0.1 } }));
    expect(pair.cancel).not.toHaveBeenCalled();
    for (const scalar of scalars) expect(scalar.cancel).not.toHaveBeenCalled();

    lease.pointer(token!, 1); lease.endPointer(token!);
    expect(r.onLevelChange.mock.calls).toEqual([
      ['rfGain', 1], ['squelch', 1],
    ]);
  });
});
