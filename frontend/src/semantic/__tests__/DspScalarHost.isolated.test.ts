import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';

const activation = vi.hoisted(() => ({ selected: undefined as unknown }));
const capture = vi.hoisted(() => ({
  bindings: [] as unknown[], leases: [] as Array<{ binding: unknown; lease: unknown }>,
}));
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
      binding.attachRenderer = () => {
        const lease = attachRenderer();
        capture.leases.push({ binding, lease });
        return lease;
      };
      capture.bindings.push(binding);
      return binding;
    },
  };
});

import ExternalScalarRenderer from '../../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import type { Skin } from '../../components-v2/controls/value-control/skin';
import type { CommandScalarFeedback, ContinuousScalarRendererLease }
  from '../../primitives/scalar/continuous-scalar.svelte';
import DspScalarHostFixture from './fixtures/DspScalarHostFixture.svelte';
import { topologyFixtures, withDsp } from '../fixtures/topologies';
import type { RadioViewModel } from '../radio-view-model';
import type { DspScalarFeedback, DspScalarField, DspScalarPresentation } from '../dsp-scalars';

const FIELDS = ['nbLevel', 'nbWidth'] as const satisfies readonly DspScalarField[];
const CONFIRMED = { nbLevel: 64, nbWidth: 2 } as const;
function commandFeedback(
  field: DspScalarField, over: Partial<CommandScalarFeedback> = {},
): Readonly<CommandScalarFeedback> {
  return Object.freeze({
    confirmed: CONFIRMED[field], target: null, requestedTarget: null,
    phase: 'idle', busy: false, availability: 'available', outcome: null,
    lifecycleId: null, transitionId: null, providerGeneration: 3, sessionEpoch: 7,
    scope: Object.freeze({ control: field === 'nbLevel' ? 'nb-level' : 'nb-width', receiver: 0 }),
    repeatPolicy: 'latest-target-wins', ...over,
  });
}
function feedback(
  over: Partial<Record<DspScalarField, Readonly<CommandScalarFeedback>>> = {},
): DspScalarFeedback {
  return Object.freeze({
    nbLevel: over.nbLevel ?? commandFeedback('nbLevel'),
    nbWidth: over.nbWidth ?? commandFeedback('nbWidth'),
  });
}
const view = (): RadioViewModel => withDsp(topologyFixtures['1/single']);

type RendererNode = HTMLButtonElement & { readonly rendererLease: ContinuousScalarRendererLease };
type Props = {
  view: RadioViewModel; feedback: DspScalarFeedback; presentation: 'grouped' | 'independent';
  scalarPresentation?: Readonly<DspScalarPresentation>; nbLevelMax?: number;
  nbLevelPercent?: boolean; onLevelChange?: (field: DspScalarField, value: number) => void;
};
let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  activation.selected = {
    name: 'DSP test appearance', hbar: ExternalScalarRenderer, knob: ExternalScalarRenderer,
  } satisfies Skin;
  capture.bindings = [];
  capture.leases = [];
});
afterEach(() => { activation.selected = undefined; target.remove(); });

function render(over: Partial<Props> = {}) {
  const onLevelChange = vi.fn(over.onLevelChange ?? (() => {}));
  const props = proxy<Props>({
    view: view(), feedback: feedback(), presentation: 'grouped', ...over, onLevelChange,
  });
  const component = mount(DspScalarHostFixture, { target, props });
  flushSync();
  const row = (field: DspScalarField) =>
    target.querySelector<HTMLElement>(`[data-testid="dsp-${field}"]`);
  const scalar = (field: DspScalarField) =>
    row(field)?.querySelector<RendererNode>('[data-external-scalar-renderer]');
  return { props, onLevelChange, row, scalar, dispose: () => unmount(component) };
}
function currentLease(binding: unknown): ContinuousScalarRendererLease {
  for (let index = capture.leases.length - 1; index >= 0; index -= 1) {
    const entry = capture.leases[index]!;
    if (entry.binding === binding) return entry.lease as ContinuousScalarRendererLease;
  }
  throw new Error('renderer lease not captured');
}

describe('two-scalar DSP family host', () => {
  it('places both handles through the real Surface with five native and four finite controls', () => {
    const r = render();
    expect(capture.bindings).toHaveLength(2);
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(2);
    expect(target.querySelectorAll('.dsp-surface input[type="range"]')).toHaveLength(5);
    for (const field of FIELDS) {
      expect(r.row(field)?.closest('[data-testid="dsp-surface"]')).not.toBeNull();
      expect(target.querySelectorAll(`[data-testid="dsp-${field}"]`)).toHaveLength(1);
    }
    for (const field of ['nrActive', 'nbActive', 'notchMode', 'agcMode']) {
      expect(target.querySelectorAll(`[data-testid="dsp-${field}"]`)).toHaveLength(1);
    }
    r.props.presentation = 'independent';
    r.props.scalarPresentation = { form: 'knob', compact: true, showLabel: false, showValue: false };
    flushSync();
    expect(target.querySelectorAll('[data-testid="independent-dsp-scalars"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(2);
    expect(capture.bindings).toHaveLength(2);
    expect(target.querySelectorAll('.dsp-surface input[type="range"]')).toHaveLength(5);
    r.dispose();
  });

  it.each([
    ['hbar', 'knob', 'hbar'], ['knob', 'hbar', 'knob'],
  ] as const)('preserves both bindings and fences stale leases across %s-%s-%s and teardown',
    (first, middle, last) => {
      activation.selected = undefined;
      const r = render({ presentation: 'independent', scalarPresentation: { form: first } });
      const bindings = [...capture.bindings];
      const stale = bindings.map(currentLease);
      const tokens = stale.map(lease => lease.beginPointer());
      r.props.scalarPresentation = { form: middle };
      flushSync();
      expect(capture.bindings).toEqual(bindings);
      for (const [index, lease] of stale.entries()) {
        expect(lease.beginPointer()).toBeNull();
        const token = tokens[index];
        if (token !== null) { lease.pointer(token, 9); lease.endPointer(token); }
        lease.nativeInput(9);
        lease.wheel({ direction: 1, fine: false });
        expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(false);
      }
      const middleLeases = bindings.map(currentLease);
      r.props.scalarPresentation = { form: last };
      flushSync();
      expect(capture.bindings).toEqual(bindings);
      for (const lease of middleLeases) {
        lease.nativeInput(7);
        lease.wheel({ direction: -1, fine: true });
        expect(lease.key({ key: 'ArrowLeft', fine: true })).toBe(false);
      }
      const current = bindings.map(currentLease);
      const teardownTokens = current.map(lease => lease.beginPointer());
      r.dispose();
      for (const [index, lease] of current.entries()) {
        expect(lease.beginPointer()).toBeNull();
        const token = teardownTokens[index];
        if (token !== null) { lease.pointer(token, 7); lease.endPointer(token); }
        lease.nativeInput(7);
        lease.wheel({ direction: 1, fine: false });
        expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(false);
      }
      expect(r.onLevelChange).not.toHaveBeenCalled();
    });

  it.each(['hbar', 'knob'] as const)(
    'uses six native range keys, one-step wheel, inert reset, and immediate optimistic input in %s',
    (form) => {
      activation.selected = undefined;
      const r = render({ presentation: 'independent', scalarPresentation: { form } });
      const lease = currentLease(capture.bindings[0]);
      for (const key of ['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Home', 'End']) {
        expect(lease.key({ key, fine: true })).toBe(true);
      }
      lease.wheel({ direction: 1, fine: true });
      lease.nativeInput(64.6);
      const callsBeforeReset = r.onLevelChange.mock.calls.length;
      lease.reset();
      expect(r.onLevelChange.mock.calls).toEqual([
        ['nbLevel', 65], ['nbLevel', 64], ['nbLevel', 65], ['nbLevel', 64],
        ['nbLevel', 0], ['nbLevel', 255], ['nbLevel', 255], ['nbLevel', 65],
      ]);
      expect(r.onLevelChange).toHaveBeenCalledTimes(callsBeforeReset);
      expect(lease.view.displayed).toBe(65);
      r.dispose();
    });

  it('keeps NB Level raw, caps-bounded, and percent-formatted without rescaling commands', () => {
    const r = render({ nbLevelMax: 200, nbLevelPercent: true });
    const level = r.scalar('nbLevel')!;
    expect(currentLease(capture.bindings[0]).view.domain).toMatchObject({ min: 0, max: 200, step: 1 });
    expect(level.dataset.confirmed).toBe('64');
    expect(level.dataset.display).toBe('32%');
    level.click();
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('nbLevel', 65);
    r.props.nbLevelPercent = false;
    flushSync();
    expect(r.scalar('nbLevel')?.dataset.display).toBe('65');
    r.dispose();
  });

  it('passes current global NB Width lifecycle, errors, and receiver-0 evidence through one binding', () => {
    const r = render({ feedback: feedback({ nbWidth: commandFeedback('nbWidth', {
      target: 3, requestedTarget: 3, phase: 'submitted', busy: true,
      lifecycleId: 'width-3', transitionId: 'width-submitted-3',
    }) }) });
    const binding = capture.bindings[1];
    expect(r.row('nbWidth')?.dataset).toMatchObject({
      feedbackControl: 'nb-width', feedbackReceiver: '0',
    });
    expect(r.scalar('nbWidth')?.dataset).toMatchObject({ confirmed: '2', requested: '3', phase: 'submitted' });
    r.props.feedback = feedback({ nbWidth: commandFeedback('nbWidth', {
      confirmed: 3, requestedTarget: 3, phase: 'confirmed', busy: false,
      lifecycleId: 'width-3', transitionId: 'width-confirmed-3', outcome: { phase: 'confirmed' },
    }) });
    flushSync();
    expect(r.scalar('nbWidth')?.dataset.phase).toBe('confirmed');
    r.props.feedback = feedback({ nbWidth: commandFeedback('nbWidth', {
      confirmed: 3, requestedTarget: 4, phase: 'failed', busy: false,
      lifecycleId: 'width-4', transitionId: 'width-failed-4',
      outcome: { phase: 'failed', error: 'radio refused' },
    }) });
    flushSync();
    expect(r.scalar('nbWidth')?.dataset).toMatchObject({ phase: 'failed', error: 'radio refused' });
    r.props.feedback = feedback({ nbWidth: commandFeedback('nbWidth', {
      confirmed: null, availability: 'unavailable', phase: 'unavailable',
    }) });
    flushSync();
    expect(r.scalar('nbWidth')?.getAttribute('aria-disabled')).toBe('true');
    expect(r.scalar('nbWidth')?.dataset.display).toBe('?');
    expect(capture.bindings[1]).toBe(binding);
    r.dispose();
  });

  it('distinguishes structural absence from visible unknown/unavailable feedback', () => {
    const current = view();
    const r = render({
      view: { ...current, dsp: { ...current.dsp!,
        nbLevel: { ...current.dsp!.nbLevel, availability: { structural: false, operational: false } },
        nbWidth: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      } },
      feedback: feedback({ nbWidth: commandFeedback('nbWidth', {
        confirmed: null, availability: 'unavailable', phase: 'unavailable',
      }) }),
    });
    expect(r.row('nbLevel')).toBeNull();
    expect(r.row('nbWidth')).not.toBeNull();
    expect(r.scalar('nbWidth')?.getAttribute('aria-disabled')).toBe('true');
    expect(r.scalar('nbWidth')?.dataset.accessibilityValueText).toContain('?');
    r.dispose();
  });

  it('reads replacement callbacks and feedback without remounting bindings', () => {
    const r = render();
    const bindings = [...capture.bindings];
    const replacement = vi.fn();
    r.props.onLevelChange = replacement;
    r.props.feedback = feedback({ nbLevel: commandFeedback('nbLevel', {
      confirmed: 64, target: 65, requestedTarget: 65, phase: 'submitted', busy: true,
      lifecycleId: 'level-65', transitionId: 'level-submitted-65',
    }) });
    flushSync();
    expect(r.scalar('nbLevel')?.dataset.requested).toBe('65');
    r.scalar('nbLevel')?.click();
    expect(replacement).toHaveBeenCalledExactlyOnceWith('nbLevel', 65);
    expect(r.onLevelChange).not.toHaveBeenCalled();
    expect(capture.bindings).toEqual(bindings);
    r.dispose();
  });

  it('exposes actual slider accessibility and retires stale HBar status for one fresh lane', () => {
    activation.selected = undefined;
    const failed = (transitionId: string) => feedback({ nbLevel: commandFeedback('nbLevel', {
      confirmed: 64, requestedTarget: 65, phase: 'failed', busy: false,
      lifecycleId: 'level-65', transitionId, outcome: { phase: 'failed', error: 'radio refused' },
    }) });
    const r = render({
      presentation: 'independent', scalarPresentation: { form: 'hbar' },
      nbLevelPercent: true, feedback: failed('failed-hbar'),
    });
    const bindings = [...capture.bindings];
    const live = () => target.querySelectorAll('[role="status"][aria-live="polite"]');
    const slider = r.row('nbLevel')!.querySelector<HTMLElement>('[role="slider"]')!;
    expect(slider.getAttribute('aria-label')).toBe('NB level');
    expect(slider.getAttribute('aria-valuetext')).toContain('NB level: 25%');
    expect(slider.getAttribute('aria-valuetext')).toContain('radio refused');
    expect(target.querySelectorAll('[data-feedback-lane="nbLevel"]')).toHaveLength(1);
    expect(live()).toHaveLength(1);
    r.props.scalarPresentation = { form: 'knob' };
    flushSync();
    expect(capture.bindings).toEqual(bindings);
    expect(target.querySelector('[data-feedback-lane="nbLevel"]')).toBeNull();
    expect(live()).toHaveLength(0);
    r.props.feedback = failed('failed-knob');
    flushSync();
    expect(live()).toHaveLength(1);
    expect(target.querySelector('[data-feedback-lane="nbLevel"]')).toBeNull();
    r.props.scalarPresentation = { form: 'hbar' };
    flushSync();
    expect(live()).toHaveLength(0);
    r.props.feedback = failed('failed-hbar-fresh');
    flushSync();
    expect(target.querySelectorAll('[data-feedback-lane="nbLevel"]')).toHaveLength(1);
    expect(live()).toHaveLength(1);
    r.dispose();
  });
});
