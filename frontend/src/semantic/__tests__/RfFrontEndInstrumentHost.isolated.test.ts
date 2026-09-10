import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { Capabilities, VfoScheme } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import { getRfSqlControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';

const activation = vi.hoisted(() => ({ selected: undefined as unknown }));
const captures = vi.hoisted(() => ({
  pairs: [] as unknown[], scalars: [] as unknown[], lifecycle: [] as string[],
  /** MOR-2425 review fix — the `createChoiceRendererSeat` `readCurrent`
   *  closures, so a test can read `requested` directly: unlike
   *  `option.disabledReason` (rendered as `title`/`aria-describedby`/
   *  `.sr-only` by `FiniteControlRendererFixture`), `requested` is not
   *  surfaced anywhere in that fixture's DOM. */
  choiceReads: [] as Array<() => { label: string; requested?: unknown;
    options: readonly { value: unknown; disabledReason?: string }[] }>,
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
vi.mock('../../primitives/control-instruments/control-instrument-renderer.svelte', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../primitives/control-instruments/control-instrument-renderer.svelte')>();
  return {
    ...actual,
    createChoiceRendererSeat: (...args: Parameters<typeof actual.createChoiceRendererSeat>) => {
      captures.choiceReads.push(args[0] as (typeof captures.choiceReads)[number]);
      return actual.createChoiceRendererSeat(...args);
    },
  };
});

import ExternalScalarRenderer from '../../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import type { Skin } from '../../components-v2/controls/value-control/skin';
import {
  createFiniteRendererContext,
  type FiniteControlAppearance, type FiniteRendererContext,
} from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { ContinuousPairBinding } from '../../primitives/scalar/continuous-pair.svelte';
import type { ContinuousScalarBinding } from '../../primitives/scalar/continuous-scalar.svelte';
import Fixture from './fixtures/RfFrontEndInstrumentHostFixture.svelte';
import { topologyFixtures, withRfFrontEnd } from '../fixtures/topologies';
import type { Availability, DisabledReason, RadioViewModel, RfFrontEndViewModel } from '../radio-view-model';
import { DISABLED_REASON_LABEL } from '../rf-front-end-instruments';
import type {
  RfFrontEndAuthorityPublication as Publication,
  RfFrontEndFiniteChoiceValue,
  RfFrontEndLevelFeedback,
  RfFrontEndToggleField,
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
  captures.pairs = []; captures.scalars = []; captures.lifecycle = []; captures.choiceReads = [];
  resetRetainedInvocations();
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

/* ── MOR-2425 RF-B: finite handles (preamp/attenuator/DIGI-SEL/IP+) ──── */

const rfAppearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance<RfFrontEndFiniteChoiceValue>['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance<RfFrontEndFiniteChoiceValue>['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance<RfFrontEndFiniteChoiceValue>['choice'],
} satisfies FiniteControlAppearance<RfFrontEndFiniteChoiceValue>;

const finiteBase = (): RadioViewModel => withRfFrontEnd(topologyFixtures['1/single']);

/** Re-shapes one `rfFrontEnd` field's reading, keeping its availability. */
function rfReading(
  view: RadioViewModel, name: keyof RfFrontEndViewModel, reading: unknown,
): RadioViewModel {
  return {
    ...view,
    rfFrontEnd: { ...view.rfFrontEnd!, [name]: { ...view.rfFrontEnd![name], reading } } as RfFrontEndViewModel,
  };
}

/** Re-shapes one `rfFrontEnd` field's availability, keeping its reading. */
function rfAvailability(
  view: RadioViewModel, name: keyof RfFrontEndViewModel, availability: Availability,
): RadioViewModel {
  return {
    ...view,
    rfFrontEnd: { ...view.rfFrontEnd!, [name]: { ...view.rfFrontEnd![name], availability } } as RfFrontEndViewModel,
  };
}

type FiniteOverrides = Partial<{
  onPreampChange: (level: number) => void;
  onAttenuatorChange: (db: number) => void;
  onToggle: (field: RfFrontEndToggleField, next: boolean) => void;
  pendingPreamp: number | null;
  finiteAppearance: FiniteControlAppearance<RfFrontEndFiniteChoiceValue>;
  rendererContext: FiniteRendererContext | null;
  renderSurface: boolean;
}>;

/** A minimal mount dedicated to the four finite handles: they read directly
 *  off `presentation.view.rfFrontEnd`, so — unlike the scalar RF/SQL pair
 *  above — none of this needs the authority-epoch/receiver A-B-A machinery
 *  `render()` exists for. */
function renderFinite(view: RadioViewModel, overrides: FiniteOverrides = {}) {
  const pub = publication();
  const subscribeOnce: SubscribeRfFrontEndAuthority = (handler) => {
    handler(pub); return () => undefined;
  };
  const props = proxy({
    publication: pub, view, subscribeControlAuthority: subscribeOnce,
    controlModel: 'separate' as RfSqlControlModel, renderSurface: false, layout: 'independent' as const,
    ...overrides,
  });
  const component = mount(Fixture, { target, props });
  mounted.push(component);
  flushSync();
  return {
    props,
    el: (id: string) => target.querySelector<HTMLElement>(`[data-testid="rf-front-end-${id}"]`),
    slot: (name: string) => target.querySelector<HTMLElement>(`[data-finite-slot="${name}"]`)!,
  };
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
    expect(slider().closest('.rf-front-end-slider')).not.toBeNull();
    expect(slider().getAttribute('aria-disabled')).toBe('false');

    r.setFeedback(null); flushSync();
    expect(group().dataset.feedbackIntegration).toBe('authority-unresolved');
    expect(group().textContent).toContain('RF ?');
    expect(group().textContent).toContain('SQL ?');
    expect(slider().getAttribute('aria-disabled')).toBe('true');

    r.setFeedback(commandFeedback()); flushSync();
    expect(group().dataset.feedbackIntegration).toBe('command-feedback');
    expect(group().textContent).toContain('RF 50%');
    expect(group().textContent).toContain('SQL 20%');
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

describe('RfFrontEndInstrumentHost finite handles (MOR-2425 RF-B)', () => {
  it.each([0, 1, 2] as const)(
    'sends preamp level %s exactly once, from a fresh mount, independent of the other buttons',
    (value) => {
      const onPreampChange = vi.fn();
      const r = renderFinite(finiteBase(), { onPreampChange });
      r.el(`preamp-${value}`)!.click();
      flushSync();
      expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(value);
    },
  );

  it('presents capability-derived preamp and attenuator choices with operator labels', () => {
    const r = renderFinite(finiteBase());
    expect(r.el('preamp-0')?.textContent).toBe('OFF');
    expect(r.el('preamp-1')?.textContent).toBe('P1');
    expect(r.el('preamp-2')?.textContent).toBe('P2');
    expect(r.el('attenuator-0')?.textContent).toBe('OFF');
    expect(r.el('attenuator-6')?.textContent).toBe('6 dB');
  });

  it.each([0, 6, 12, 18] as const)(
    'sends attenuator step %s dB exactly once, from a fresh mount, independent of the other buttons',
    (value) => {
      const onAttenuatorChange = vi.fn();
      const r = renderFinite(finiteBase(), { onAttenuatorChange });
      r.el(`attenuator-${value}`)!.click();
      flushSync();
      expect(onAttenuatorChange).toHaveBeenCalledExactlyOnceWith(value);
    },
  );

  it.each(['digiSel', 'ipPlus'] as const)(
    'flips %s exactly once, computed from the observed reading',
    (field) => {
      const onToggle = vi.fn();
      const view = rfReading(finiteBase(), field, { status: 'known', value: false });
      const r = renderFinite(view, { onToggle });
      r.el(field)!.click();
      flushSync();
      expect(onToggle).toHaveBeenCalledExactlyOnceWith(field, true);
    },
  );

  it.each(['digiSel', 'ipPlus'] as const)(
    'refuses to emit %s while its own reading is unread, independent of `disabled`',
    (field) => {
      const onToggle = vi.fn();
      const view = rfReading(finiteBase(), field, { status: 'unknown' });
      const r = renderFinite(view, { onToggle });
      r.el(field)!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onToggle).not.toHaveBeenCalled();
    },
  );

  it.each(['preamp', 'attenuator', 'digiSel', 'ipPlus'] as const)(
    'renders NOTHING for %s when structurally absent — never merely disabled',
    (field) => {
      const view = rfAvailability(finiteBase(), field, { structural: false, operational: false });
      const r = renderFinite(view);
      expect(r.slot(field).children).toHaveLength(0);
      expect(r.el(field)).toBeNull();
    },
  );

  it.each(['preamp', 'attenuator', 'digiSel', 'ipPlus'] as const)(
    'renders %s present-and-disabled when structurally present but operationally unread',
    (field) => {
      const view = rfAvailability(finiteBase(), field, { structural: true, operational: false });
      const r = renderFinite(view);
      const control = r.el(field);
      expect(control).not.toBeNull();
      if (field === 'preamp' || field === 'attenuator') {
        expect(control!.dataset.observed).toBe('false');
      } else {
        expect(control!.hasAttribute('disabled')).toBe(true);
      }
    },
  );

  it('keeps the preamp mutex from the host directly: disabled, with the explanation, on top of a usable field', () => {
    const view: RadioViewModel = {
      ...finiteBase(),
      disabledReasons: [{ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' }] as DisabledReason[],
    };
    const r = renderFinite(view);
    for (const value of [0, 1, 2]) {
      expect(r.el(`preamp-${value}`)!.hasAttribute('disabled')).toBe(true);
    }
    expect(r.el('preamp-mutex-reason')).not.toBeNull();
  });

  it('keeps the attenuator seat live across an A-B-A finite-appearance swap, with a stale lease inert', () => {
    const onAttenuatorChange = vi.fn();
    const a = createFiniteRendererContext(), b = createFiniteRendererContext();
    const r = renderFinite(finiteBase(), {
      finiteAppearance: rfAppearance, rendererContext: a, onAttenuatorChange,
    });
    const staleA = retainedInvocations.get('Attenuator')!;
    expect(target.querySelector('[data-testid="external-Attenuator"]')?.getAttribute('data-reading')).toBe('6');

    r.props.rendererContext = b;
    flushSync();
    staleA();
    expect(onAttenuatorChange).not.toHaveBeenCalled();

    r.props.view = rfReading(r.props.view, 'attenuator', { status: 'known', value: 18 });
    flushSync();
    expect(target.querySelector('[data-testid="external-Attenuator"]')?.getAttribute('data-reading')).toBe('18');
    const activeB = retainedInvocations.get('Attenuator')!;
    activeB(0);
    expect(onAttenuatorChange).toHaveBeenCalledExactlyOnceWith(0);

    r.props.rendererContext = a;
    flushSync();
    activeB(0);
    expect(onAttenuatorChange).toHaveBeenCalledTimes(1);
  });

  /**
   * MOR-2425 review fix (BLOCKING 2) — on `origin/main` preamp had no
   * external-renderer path at all; once it renders through
   * `finiteAppearance.choice`, the mutex explanation and the pending target
   * must reach it too, or the file header's "disabled control WITH AN
   * EXPLANATION"/"keyed off `DisabledReasonCode`" claims are false on this
   * path. `FiniteControlRendererFixture` never surfaces `requested` in the
   * DOM, so this reads the seat's own `readCurrent()` input directly.
   */
  it('forwards the preamp mutex reason and the pending target to the external choice renderer', () => {
    const view: RadioViewModel = {
      ...finiteBase(),
      disabledReasons: [{ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' }] as DisabledReason[],
    };
    renderFinite(view, {
      finiteAppearance: rfAppearance, pendingPreamp: 2, rendererContext: createFiniteRendererContext(),
    });
    expect(target.querySelector('[data-testid="external-Preamp"]')).not.toBeNull();
    const preamp = captures.choiceReads.map((read) => read()).find((input) => input.label === 'Preamp')!;
    expect(preamp.requested).toEqual({ kind: 'requested-target', target: 2 });
    const reason = DISABLED_REASON_LABEL['mutually-exclusive-control'];
    const option = target.querySelector<HTMLElement>('[data-testid="external-Preamp-0"]')!;
    expect(option.title).toBe(reason);
    const describedBy = option.getAttribute('aria-describedby')!;
    expect(target.querySelector(`#${describedBy}`)?.textContent).toBe(reason);
  });

  /**
   * MOR-2425 review fix (non-blocking order note) — `RfFrontEndSurface
   * .svelte`'s grouped (non-`finiteLayout`) placement must keep the
   * Standard panel order: the continuous RF/SQL controls first, followed by
   * ATT, PRE, then the remaining capability-derived finite controls. Exercises the REAL surface
   * component via the fixture's `renderSurface` flag, not a bare mock.
   */
  it('renders the grouped surface controls in the documented order', () => {
    renderFinite(finiteBase(), { renderSurface: true });
    const documented = [
      'rf-front-end-rfGain', 'rf-front-end-squelch', 'rf-front-end-attenuator',
      'rf-front-end-preamp', 'rf-front-end-digiSel', 'rf-front-end-ipPlus',
    ];
    const order = [...target.querySelectorAll<HTMLElement>('[data-testid]')]
      .map((node) => node.dataset.testid)
      .filter((id): id is string => documented.includes(id ?? ''));
    expect(order).toEqual(documented);
  });
});

/**
 * MOR-2425/R29 weakest witness (census §E): unlike every other test in this
 * file, `view` here is built by the REAL `toRadioViewModel(state, caps)` —
 * a genuine consumer of the fixed `field-status.ts` primitive, not a
 * hand-shaped `RfFrontEndViewModel` (`finiteBase()`/`rfReading`/
 * `rfAvailability` above construct the reading/availability directly and so
 * never touch `field-status.ts` at all).
 *
 * The "real disconnect" half is modelled as a field that has NEVER been
 * observed (`observed: false`, `availability: 'missing'`), not a live
 * `ManagedAppTxController` session transition: verified before writing this
 * (against `semantic-rf-front-end-wiring.component.test.ts`) that publishing
 * a `disconnected` session, or even a `null` state, through that file's own
 * authority mock leaves every preamp button's `disabled` unchanged, because
 * that wiring's `canonicalView` re-reads `runtime.state`/`runtime.caps` with
 * no reactive subscription of its own — unlike the separate, session-aware
 * continuous-scalar level bindings its rfGain/squelch disconnect tests
 * exercise. `missing`/`observed: false` is the shape `field-status.ts`'s own
 * fix actually distinguishes from stale-but-observed (its own-entry branch:
 * `if (status.observed) return 'available'; return status.availability;`),
 * so it is the honest way to exercise "a real disconnect or structural
 * absence" (the fix's own docstring) for this fact.
 */
describe('MOR-2425/R29 weakest witness: preamp stays enabled while stale-but-observed', () => {
  const r29Caps = (): Capabilities => ({
    stateContractVersion: 1, providerGeneration: 1,
    model: 'R29-WITNESS', scope: false, audio: true, tx: false,
    capabilities: ['audio', 'preamp'],
    receivers: 1, vfoScheme: 'single',
    freqRanges: [], modes: [], filters: [], preValues: [0, 1, 2], attValues: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
  } as Capabilities);

  const r29Fresh = { storePath: 'main.preamp', observed: true, freshness: 'fresh', availability: 'available' };

  function r29State(preampStatus: Record<string, unknown>): ServerState {
    return {
      stateContractVersion: 1, providerGeneration: 1,
      revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
      updatedAt: '2026-09-07T00:00:00Z', active: 'MAIN',
      ptt: false, split: false, dualWatch: false, tunerStatus: 0,
      txTarget: { status: 'unknown', reason: 'not-observed' },
      main: {
        freqHz: 14_250_000, mode: 'USB', filter: 1, dataMode: 0,
        att: 0, preamp: 1, nb: false, nr: false, afLevel: 0.5, rfGain: 1, squelch: 0, sMeter: 0,
      },
      connection: {} as ServerState['connection'],
      fieldStatus: {
        active: r29Fresh, split: r29Fresh, dualWatch: r29Fresh, txTarget: r29Fresh,
        'main.freqHz': r29Fresh, 'main.mode': r29Fresh, 'main.filter': r29Fresh,
        'main.preamp': preampStatus,
      },
    } as unknown as ServerState;
  }

  it(
    'keeps the choice enabled with its last selection while stale-but-observed, and a click still dispatches exactly one command',
    () => {
      const stale = { storePath: 'main.preamp', observed: true, freshness: 'stale', availability: 'stale' };
      const view = toRadioViewModel(r29State(stale), r29Caps());
      expect(view).not.toBeNull();
      const onPreampChange = vi.fn();
      const r = renderFinite(view!, { onPreampChange });

      expect(r.el('preamp-1')!.getAttribute('aria-checked')).toBe('true');
      expect(r.el('preamp-2')!.hasAttribute('disabled')).toBe(false);
      r.el('preamp-2')!.click();
      flushSync();
      expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(2);
    },
  );

  it(
    'disables the choice once the field has never been observed at all (structural absence, not staleness)',
    () => {
      const missing = { storePath: 'main.preamp', observed: false, freshness: 'unknown', availability: 'missing' };
      const view = toRadioViewModel(r29State(missing), r29Caps());
      expect(view).not.toBeNull();
      const onPreampChange = vi.fn();
      const r = renderFinite(view!, { onPreampChange });

      for (const value of [0, 1, 2]) expect(r.el(`preamp-${value}`)!.hasAttribute('disabled')).toBe(true);
      r.el('preamp-1')!.click();
      flushSync();
      expect(onPreampChange).not.toHaveBeenCalled();
    },
  );
});

/**
 * Consistency witness (census §E, closing the verifier's Non-blocking(4)):
 * one stale-but-observed state feeds BOTH the REAL scalar accessor
 * (`panel-adapters.ts: getRfSqlControlFeedback`, fixed by #3357, now merged
 * to `main`) and the REAL finite seat (`field-status.ts`, fixed by THIS
 * PR) — through the actual `$lib/stores/radio.svelte`/`capabilities.svelte`
 * stores this file's other describe blocks never touch, not a
 * hand-shaped stand-in for either mechanism. Closes the "scalar enabled,
 * finite neighbor disabled" split the verifier flagged for the state as of
 * #3357 alone.
 */
describe('MOR-2425/R29 consistency witness: one stale-but-observed state, scalar AND finite seat both available', () => {
  const consistencyCaps = (): Capabilities => ({
    stateContractVersion: 1, providerGeneration: 1,
    model: 'R29-CONSISTENCY', scope: false, audio: true, tx: false,
    capabilities: ['audio', 'preamp', 'rf_gain'],
    receivers: 1, vfoScheme: 'single',
    freqRanges: [], modes: [], filters: [], preValues: [0, 1, 2], attValues: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
  } as Capabilities);

  function consistencyState(): ServerState {
    const fresh = {
      storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
      lastObservedMonotonic: 1,
    };
    const stale = {
      storePath: 'x', observed: true, freshness: 'stale', availability: 'stale',
      lastObservedMonotonic: 1,
    };
    return {
      stateContractVersion: 1, providerGeneration: 1,
      revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
      updatedAt: '2026-09-07T00:00:00Z', active: 'MAIN',
      ptt: false, split: false, dualWatch: false, tunerStatus: 0,
      txTarget: { status: 'unknown', reason: 'not-observed' },
      main: {
        freqHz: 14_250_000, mode: 'USB', filter: 1, dataMode: 0,
        att: 0, preamp: 1, nb: false, nr: false, afLevel: 0.5, rfGain: 0.5, squelch: 0, sMeter: 0,
      },
      connection: { rigConnected: true, radioReady: true, controlConnected: true },
      fieldStatus: {
        active: fresh, split: fresh, dualWatch: fresh, txTarget: fresh,
        'main.freqHz': fresh, 'main.mode': fresh, 'main.filter': fresh,
        // One state, two stale-but-observed fields: rfGain (scalar,
        // `panel-adapters.ts`) and preamp (finite, `field-status.ts`).
        'main.preamp': stale, 'main.rfGain': stale,
      },
    } as unknown as ServerState;
  }

  afterEach(() => { resetRadioState(); clearCapabilities(); });

  it('rfGain (scalar, panel-adapters.ts) and preamp (finite, field-status.ts) are both available from the same state', () => {
    const caps = consistencyCaps();
    const state = consistencyState();
    expect(setCapabilities(caps)).toBe(true);
    expect(setRadioState(state)).toBe(true);

    // Scalar side — the REAL panel-adapters.ts accessor the wiring's rfGain
    // slider consumes.
    const feedback = getRfSqlControlFeedback({ state: 'connected', epoch: 1 });
    expect(feedback!.rf.feedback.availability).toBe('available');
    expect(feedback!.rf.feedback.confirmed).toBe(0.5);

    // Finite side — the REAL field-status.ts-backed preamp choice seat.
    const view = toRadioViewModel(state, caps);
    expect(view).not.toBeNull();
    const r = renderFinite(view!, {});
    expect(r.el('preamp-1')!.getAttribute('aria-checked')).toBe('true');
    expect(r.el('preamp-1')!.hasAttribute('disabled')).toBe(false);
  });
});
