import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';

const activation = vi.hoisted(() => ({ selected: undefined as unknown }));
const capture = vi.hoisted(() => ({ bindings: [] as unknown[], leases: [] as unknown[] }));

vi.mock('../../component-kits/activation', () => ({
  getSelectedScalarAppearance: () => activation.selected,
}));
vi.mock('../../primitives/scalar/continuous-scalar.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../primitives/scalar/continuous-scalar.svelte')>();
  return {
    ...actual,
    createContinuousScalar: (...args: Parameters<typeof actual.createContinuousScalar>) => {
      const binding = actual.createContinuousScalar(...args);
      const attachRenderer = binding.attachRenderer.bind(binding);
      const destroy = binding.destroy.bind(binding);
      binding.attachRenderer = (...rendererArgs: Parameters<typeof attachRenderer>) => {
        const lease = attachRenderer(...rendererArgs);
        capture.leases.push({ binding, lease });
        return lease;
      };
      binding.destroy = vi.fn(() => destroy());
      capture.bindings.push(binding);
      return binding;
    },
  };
});

import ExternalScalarRenderer from '../../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import type { Skin } from '../../components-v2/controls/value-control/skin';
import type {
  CommandScalarFeedback,
  ContinuousScalarBinding,
  ContinuousScalarRendererLease,
} from '../../primitives/scalar/continuous-scalar.svelte';
import Fixture from './fixtures/CwKeyerInstrumentHostFixture.svelte';
import { topologyFixtures, withCwKeyer } from '../fixtures/topologies';
import type { CwKeyerViewModel, RadioViewModel } from '../radio-view-model';
import {
  CW_CONTINUOUS_LEVELS,
  type CwContinuousField,
  type CwContinuousPresentation,
} from '../CwKeyerInstrumentHost.svelte';

type Feedback = Readonly<CommandScalarFeedback>;
type Props = {
  view: RadioViewModel;
  presentation: 'grouped' | 'independent';
  scalarPresentation?: Readonly<CwContinuousPresentation>;
  pitchFeedback?: Feedback;
  keySpeedFeedback?: Feedback;
  onLevelChange?: (field: CwContinuousField | 'breakInDelay', value: number) => void;
};
type RendererNode = HTMLButtonElement & { readonly rendererLease: ContinuousScalarRendererLease };

const initial = { keyerSpeed: 24, pitchHz: 600 } as const;
const control = { keyerSpeed: 'keyer-speed', pitchHz: 'cw-pitch' } as const;
const base = (): RadioViewModel => withCwKeyer(topologyFixtures['1/single']);
const feedback = (
  field: CwContinuousField,
  over: Partial<CommandScalarFeedback> = {},
): Feedback => Object.freeze({
  confirmed: initial[field], target: null, requestedTarget: null,
  phase: 'idle', busy: false, availability: 'available', outcome: null,
  lifecycleId: null, transitionId: null, providerGeneration: 1, sessionEpoch: 1,
  scope: Object.freeze({ control: control[field], receiver: 0 as const }),
  repeatPolicy: 'latest-target-wins', ...over,
});

let target: HTMLDivElement;
let mounted = new Set<ReturnType<typeof mount>>();
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  activation.selected = {
    name: 'CW host test', hbar: ExternalScalarRenderer, knob: ExternalScalarRenderer,
  } satisfies Skin;
  capture.bindings = [];
  capture.leases = [];
});
afterEach(() => {
  for (const component of mounted) unmount(component);
  mounted = new Set();
  activation.selected = undefined;
  target.remove();
});

function render(over: Partial<Props> = {}) {
  const onLevelChange = vi.fn(over.onLevelChange ?? (() => {}));
  const props = proxy<Props>({
    view: base(), presentation: 'grouped',
    keySpeedFeedback: feedback('keyerSpeed'),
    ...over, onLevelChange,
  });
  const component = mount(Fixture, { target, props });
  mounted.add(component);
  flushSync();
  const row = (field: CwContinuousField) => target.querySelector<HTMLElement>(
    `[data-testid="cw-keyer-${field}"]`,
  )!;
  const renderer = (field: CwContinuousField) => row(field).querySelector<RendererNode>(
    '[data-external-scalar-renderer]',
  );
  const dispose = () => { unmount(component); mounted.delete(component); };
  return { props, component, onLevelChange, row, renderer, dispose };
}

function binding(field: CwContinuousField): ContinuousScalarBinding {
  const index = CW_CONTINUOUS_LEVELS.findIndex(([f]) => f === field);
  return capture.bindings[index] as ContinuousScalarBinding;
}
function currentLease(field: CwContinuousField): ContinuousScalarRendererLease {
  const owner = binding(field);
  for (let index = capture.leases.length - 1; index >= 0; index -= 1) {
    const entry = capture.leases[index] as { binding: unknown; lease: ContinuousScalarRendererLease };
    if (entry.binding === owner) return entry.lease;
  }
  throw new Error('renderer lease not captured');
}

describe('CwKeyerInstrumentHost', () => {
  it('creates one persistent binding per field and destroys each exactly once', () => {
    const r = render();
    expect(capture.bindings).toHaveLength(2);
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(2);
    expect(CW_CONTINUOUS_LEVELS).toEqual([
      ['keyerSpeed', 'Keyer speed', 6, 48, 1, 'WPM', 'set_key_speed'],
      ['pitchHz', 'CW pitch', 300, 900, 5, 'Hz', 'set_cw_pitch'],
    ]);
    const speedOwner = binding('keyerSpeed');
    const pitchOwner = binding('pitchHz');
    expect(speedOwner).not.toBe(pitchOwner);
    r.dispose();
    expect(speedOwner.destroy).toHaveBeenCalledOnce();
    expect(pitchOwner.destroy).toHaveBeenCalledOnce();
  });

  it('keeps both field binding identities across grouped-independent-grouped replacement', () => {
    const r = render();
    const speedOwner = binding('keyerSpeed');
    const pitchOwner = binding('pitchHz');
    r.props.presentation = 'independent'; flushSync();
    expect(binding('keyerSpeed')).toBe(speedOwner);
    expect(binding('pitchHz')).toBe(pitchOwner);
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(2);
    expect(target.querySelectorAll('[data-testid="cw-keyer-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="cw-keyer-breakInDelay"]')).toHaveLength(1);
    r.props.presentation = 'grouped'; flushSync();
    expect(binding('keyerSpeed')).toBe(speedOwner);
    expect(binding('pitchHz')).toBe(pitchOwner);
    expect(target.querySelectorAll('[data-testid="cw-keyer-keyerSpeed"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="cw-keyer-pitchHz"]')).toHaveLength(1);
    r.dispose();
  });

  it.each(['hbar', 'knob'] as const)(
    'uses the rendered-native lattice for every %s interaction', (form) => {
      for (const field of ['keyerSpeed', 'pitchHz'] as const) {
        const [, , min, max, step] = CW_CONTINUOUS_LEVELS.find(([name]) => name === field)!;
        const exercise = (
          invoke: (lease: ContinuousScalarRendererLease) => void,
          expected?: number,
        ) => {
          capture.bindings = [];
          capture.leases = [];
          const r = render({ presentation: 'independent', scalarPresentation: { form } });
          invoke(currentLease(field));
          if (expected === undefined) expect(r.onLevelChange).not.toHaveBeenCalled();
          else expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith(field, expected);
          r.dispose();
        };
        for (const [key, expected] of [
          ['ArrowLeft', initial[field] - step], ['ArrowRight', initial[field] + step],
          ['ArrowDown', initial[field] - step], ['ArrowUp', initial[field] + step],
          ['Home', min], ['End', max],
        ] as const) exercise((lease) => {
          expect(lease.key({ key, fine: false })).toBe(true);
        }, expected);
        exercise((lease) => lease.wheel({ direction: 1, fine: false }), initial[field] + step);
        exercise((lease) => lease.wheel({ direction: 1, fine: true }), initial[field] + step);
        exercise((lease) => lease.nativeInput(initial[field] + step * 2.4), initial[field] + step * 2);
        exercise((lease) => {
          const token = lease.beginPointer();
          expect(token).not.toBeNull();
          lease.pointer(token!, initial[field] + step * 3.4);
          lease.endPointer(token!);
        }, initial[field] + step * 3);
        exercise((lease) => lease.reset());
      }
    },
  );

  it.each(['keyerSpeed', 'pitchHz'] as const)(
    'cancels physical drafts and makes every retained %s renderer operation inert', (field) => {
      const [, , , max] = CW_CONTINUOUS_LEVELS.find(([name]) => name === field)!;
      const r = render({ presentation: 'independent', scalarPresentation: { form: 'hbar' } });
      const stale = currentLease(field);
      const token = stale.beginPointer(); expect(token).not.toBeNull();
      stale.pointer(token!, initial[field] + 1);
      r.onLevelChange.mockClear();

      r.props.scalarPresentation = { form: 'knob' }; flushSync();
      expect(currentLease(field).view.draft).toBeNull();
      expect(stale.beginPointer()).toBeNull();
      stale.pointer(token!, initial[field] + 2); stale.endPointer(token!); stale.cancelPointer(token!);
      stale.nativeInput(initial[field] + 3); stale.wheel({ direction: 1, fine: false });
      expect(stale.key({ key: 'End', fine: false })).toBe(false);
      stale.reset(); stale.dispose();
      expect(r.onLevelChange).not.toHaveBeenCalled();

      expect(currentLease(field).key({ key: 'End', fine: false })).toBe(true);
      expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith(field, max);
      r.props.scalarPresentation = { form: 'hbar' }; flushSync();
      expect(stale.key({ key: 'Home', fine: false })).toBe(false);
      r.dispose();
    },
  );

  it('routes pitch through its own command exactly once, independently of keyerSpeed', () => {
    const r = render();
    currentLease('pitchHz').key({ key: 'ArrowRight', fine: false });
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('pitchHz', initial.pitchHz + 5);
    r.onLevelChange.mockClear();
    currentLease('keyerSpeed').key({ key: 'ArrowRight', fine: false });
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('keyerSpeed', initial.keyerSpeed + 1);
    r.dispose();
  });

  it('reads pitch from command feedback when supplied and falls back to the raw reading otherwise', () => {
    const r = render({ pitchFeedback: feedback('pitchHz', { confirmed: 725 }) });
    expect(target.querySelector('[data-testid="cw-keyer-pitchHz-value"]')?.textContent)
      .toBe('725 Hz');
    r.props.pitchFeedback = undefined; flushSync();
    expect(target.querySelector('[data-testid="cw-keyer-pitchHz-value"]')?.textContent)
      .toBe('600 Hz');
    r.dispose();
  });

  it('preserves command evidence and invalidates a gesture on authority replacement', () => {
    activation.selected = undefined;
    const pending = feedback('keyerSpeed', {
      target: 31, requestedTarget: 31, phase: 'awaiting-confirmation', busy: true,
      lifecycleId: 'speed-31', transitionId: 'speed-awaiting-31',
    });
    const r = render({
      presentation: 'independent', scalarPresentation: { form: 'hbar' },
      keySpeedFeedback: pending,
    });
    expect(r.row('keyerSpeed').querySelector('.vc-value')?.textContent).toBe('24 WPM');
    expect(r.row('keyerSpeed').querySelector('[role="slider"]')?.getAttribute('aria-valuetext'))
      .toContain('requested 31 WPM');
    expect(r.row('keyerSpeed').querySelector('[data-canonical-value]')?.textContent).toBe('24 WPM');
    const before = binding('keyerSpeed').view;
    expect(before).toMatchObject({
      target: 31, requested: 31, phase: 'awaiting-confirmation', busy: true,
      feedback: {
        lifecycleId: 'speed-31', repeatPolicy: 'latest-target-wins',
        providerGeneration: 1, sessionEpoch: 1,
        scope: { control: 'keyer-speed', receiver: 0 },
      },
    });
    const lease = currentLease('keyerSpeed');
    const token = lease.beginPointer(); expect(token).not.toBeNull();
    r.props.keySpeedFeedback = feedback('keyerSpeed', {
      providerGeneration: 2, sessionEpoch: 2,
      scope: { control: 'keyer-speed', receiver: 0, slot: 'sub' },
    });
    flushSync();
    lease.pointer(token!, 32); lease.endPointer(token!);
    expect(r.onLevelChange).not.toHaveBeenCalled();
    expect(binding('keyerSpeed').view).toMatchObject({
      draft: null,
      feedback: { providerGeneration: 2, sessionEpoch: 2, scope: { slot: 'sub' } },
    });
    r.dispose();
    const remounted = render({
      presentation: 'independent', scalarPresentation: { form: 'hbar' },
      keySpeedFeedback: pending,
    });
    expect(remounted.row('keyerSpeed').querySelector('.vc-value')?.textContent).toBe('24 WPM');
    expect(remounted.row('keyerSpeed').querySelector('[role="slider"]')?.getAttribute('aria-valuetext'))
      .toContain('requested 31 WPM');
    expect(remounted.onLevelChange).not.toHaveBeenCalled();
    remounted.dispose();
  });

  it('reads the current callback and preserves raw reading, unknown and absent truth', () => {
    activation.selected = undefined;
    const r = render({ presentation: 'independent', scalarPresentation: { form: 'hbar' } });
    const replacement = vi.fn();
    r.props.onLevelChange = replacement; flushSync();
    currentLease('keyerSpeed').key({ key: 'ArrowRight', fine: false });
    expect(replacement).toHaveBeenCalledExactlyOnceWith('keyerSpeed', 25);
    expect(r.onLevelChange).not.toHaveBeenCalled();

    const slider = () => r.row('keyerSpeed').querySelector<HTMLElement>('[role="slider"]')!;
    r.props.keySpeedFeedback = feedback('keyerSpeed', {
      confirmed: null, availability: 'unavailable', phase: 'unavailable',
    });
    flushSync();
    expect(slider().getAttribute('aria-disabled')).toBe('true');
    expect(r.row('keyerSpeed').querySelector('.vc-value')?.textContent).toBe('—');

    r.props.keySpeedFeedback = undefined;
    flushSync();
    expect([
      slider().getAttribute('aria-valuemin'), slider().getAttribute('aria-valuemax'),
      slider().getAttribute('aria-valuenow'), r.row('keyerSpeed').querySelector('.vc-value')?.textContent,
    ]).toEqual(['6', '48', '24', '24 WPM']);
    currentLease('keyerSpeed').key({ key: 'ArrowRight', fine: false });
    expect(replacement).toHaveBeenCalledTimes(2);

    r.props.view = {
      ...r.props.view,
      cwKeyer: { ...r.props.view.cwKeyer!, keyerSpeed: {
        reading: { status: 'unknown' }, availability: { structural: true, operational: false },
      } },
    };
    flushSync();
    expect(r.row('keyerSpeed').querySelector('.vc-value')?.textContent).toBe('—');
    expect(slider().getAttribute('aria-disabled')).toBe('true');
    expect(r.row('keyerSpeed').dataset.observed).toBe('false');
    currentLease('keyerSpeed').nativeInput(31);
    expect(replacement).toHaveBeenCalledTimes(2);

    r.props.view = {
      ...r.props.view,
      cwKeyer: { ...r.props.view.cwKeyer!, keyerSpeed: {
        reading: { status: 'unknown' }, availability: { structural: false, operational: false },
      } },
    };
    flushSync();
    expect(target.querySelector('[data-testid="cw-keyer-keyerSpeed"]')).toBeNull();
    r.dispose();
  });

  it('keeps one status lane while moving HBar-knob-HBar', () => {
    activation.selected = undefined;
    const failed = (transitionId: string, providerGeneration = 1): Feedback => feedback('keyerSpeed', {
      requestedTarget: 31, phase: 'failed', lifecycleId: 'speed-31', transitionId,
      providerGeneration,
      outcome: { phase: 'failed', error: 'radio refused' },
    });
    const r = render({
      presentation: 'independent', scalarPresentation: { form: 'hbar' },
      keySpeedFeedback: failed('speed-failed-hbar'),
    });
    const live = () => target.querySelectorAll('[role="status"][aria-live="polite"]');
    expect(live()).toHaveLength(1);
    expect(target.querySelector('[data-cw-feedback-status]')?.textContent).toContain('radio refused');
    expect(r.row('keyerSpeed').querySelector('.vc-value')?.textContent).toBe('24 WPM');
    expect(r.row('keyerSpeed').querySelector('.vc-value')?.textContent).not.toContain('31');
    expect(r.row('keyerSpeed').querySelector('[role="slider"]')?.getAttribute('aria-valuenow')).toBe('24');
    const initialStatus = target.querySelector('[data-cw-feedback-status]');
    r.props.keySpeedFeedback = failed('speed-failed-hbar'); flushSync();
    expect(target.querySelector('[data-cw-feedback-status]')).toBe(initialStatus);
    r.props.keySpeedFeedback = failed('speed-failed-hbar', 2); flushSync();
    expect(target.querySelector('[data-cw-feedback-status]')).not.toBe(initialStatus);
    r.props.keySpeedFeedback = feedback('keyerSpeed', {
      confirmed: null, availability: 'unavailable', phase: 'unavailable', providerGeneration: 3,
    });
    flushSync();
    expect(target.querySelector('[data-cw-feedback-status]')).toBeNull();
    r.props.keySpeedFeedback = failed('speed-failed-hbar', 3); flushSync();
    expect(target.querySelector('[data-cw-feedback-status]')).not.toBeNull();

    r.props.scalarPresentation = { form: 'knob' };
    r.props.keySpeedFeedback = failed('speed-failed-knob');
    flushSync();
    expect(live()).toHaveLength(1);
    expect(target.querySelector('[data-cw-feedback-status]')).toBeNull();

    r.props.scalarPresentation = { form: 'hbar' };
    r.props.keySpeedFeedback = failed('speed-failed-hbar-next');
    flushSync();
    expect(live()).toHaveLength(1);
    expect(target.querySelectorAll('[data-cw-feedback-status]')).toHaveLength(1);
    r.dispose();
  });

  it('keeps each field issued-status lane independent, exactly one announcement per field', () => {
    activation.selected = undefined;
    const pitchFailed = feedback('pitchHz', {
      requestedTarget: 725, phase: 'failed', lifecycleId: 'pitch-command',
      transitionId: 'pitch-failed', outcome: { phase: 'failed', error: 'pitch rejected' },
    });
    const speedFailed = feedback('keyerSpeed', {
      requestedTarget: 31, phase: 'failed', lifecycleId: 'speed-command',
      transitionId: 'speed-failed', outcome: { phase: 'failed', error: 'speed rejected' },
    });
    const r = render({
      presentation: 'independent', scalarPresentation: { form: 'hbar' },
      keySpeedFeedback: speedFailed, pitchFeedback: pitchFailed,
    });
    const lanes = [...target.querySelectorAll<HTMLElement>('[data-cw-feedback-status]')];
    expect(lanes).toHaveLength(2);
    expect(lanes.map((node) => node.dataset.feedbackLane).sort()).toEqual(['keyerSpeed', 'pitchHz']);
    expect(lanes.find((n) => n.dataset.feedbackLane === 'pitchHz')?.textContent)
      .toContain('pitch rejected');
    expect(lanes.find((n) => n.dataset.feedbackLane === 'keyerSpeed')?.textContent)
      .toContain('speed rejected');
    expect(lanes.every((node) => node.getAttribute('aria-live') === 'polite')).toBe(true);
    r.dispose();
  });

  it('uses no second projector or form-dependent scalar policy', () => {
    const hostSource = readFileSync('src/semantic/CwKeyerInstrumentHost.svelte', 'utf8');
    expect(hostSource).not.toContain('projectControlFeedbackPresentation');
    expect(hostSource).not.toMatch(/bindings\.[\s\S]*?\.view/);
    expect(hostSource.match(/createRenderedNativeRangeContinuousScalarPolicy\(\)/g)).toHaveLength(1);
    expect(hostSource.match(/createContinuousScalar\(/g)).toHaveLength(1);
    expect(hostSource).not.toMatch(/form[\s\S]{0,80}(?:Policy|policy)/);
  });
});
