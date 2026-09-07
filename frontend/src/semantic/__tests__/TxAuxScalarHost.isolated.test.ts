import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';

const activation = vi.hoisted(() => ({ selected: undefined as unknown }));
const scalarCapture = vi.hoisted(() => ({
  bindings: [] as unknown[],
  leases: [] as Array<{ binding: unknown; lease: unknown }>,
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
        scalarCapture.leases.push({ binding, lease });
        return lease;
      };
      scalarCapture.bindings.push(binding);
      return binding;
    },
  };
});

import ExternalScalarRenderer from '../../components-v2/controls/value-control/__tests__/ExternalScalarRendererFixture.svelte';
import type { Skin } from '../../components-v2/controls/value-control/skin';
import type {
  CommandScalarFeedback,
  ContinuousScalarRendererLease,
} from '../../primitives/scalar/continuous-scalar.svelte';
import TxAuxScalarHostFixture from './fixtures/TxAuxScalarHostFixture.svelte';
import { topologyFixtures, withTxAux } from '../fixtures/topologies';
import type { RadioViewModel } from '../radio-view-model';
import type { TxAuthoritySnapshot } from '../rx-tx-surface';
import type {
  TxAuxFeedbackLevelField,
  TxAuxLevelFeedback,
  TxAuxLevelField,
  TxAuxScalarPresentation,
} from '../tx-aux-scalar';
import { TX_AUX_LEVELS } from '../tx-aux-scalar';

const RX_IDLE: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null,
};
const FEEDBACK_FIELDS = [
  ['micGain', 'mic-gain', 128],
  ['driveGain', 'drive-gain', 128],
  ['voxGain', 'vox-gain', 50],
  ['antiVoxGain', 'anti-vox-gain', 30],
  ['voxDelay', 'vox-delay', 20],
  ['compressorLevel', 'compressor-level', 10],
  ['monitorLevel', 'monitor-gain', 128],
] as const satisfies readonly (readonly [TxAuxFeedbackLevelField, string, number])[];

function feedback(control: string, confirmed: number): Readonly<CommandScalarFeedback> {
  return Object.freeze({
    confirmed, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: 1, sessionEpoch: 1,
    scope: Object.freeze({ control, receiver: 0 as const }),
    repeatPolicy: 'latest-target-wins',
  });
}

function feedbackRecord(): TxAuxLevelFeedback {
  return Object.fromEntries(FEEDBACK_FIELDS.map(([field, control, confirmed]) => [
    field, feedback(control, confirmed),
  ])) as unknown as TxAuxLevelFeedback;
}

type RendererNode = HTMLButtonElement & { readonly rendererLease: ContinuousScalarRendererLease };
type Props = {
  view: RadioViewModel;
  tx: TxAuthoritySnapshot;
  presentation: 'grouped' | 'independent';
  scalarPresentation?: Readonly<TxAuxScalarPresentation>;
  levelFeedback?: TxAuxLevelFeedback;
  onLevelChange?: (field: TxAuxLevelField, value: number) => void;
};

let target: HTMLDivElement;

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  activation.selected = {
    name: 'TxAux test appearance', hbar: ExternalScalarRenderer, knob: ExternalScalarRenderer,
  } satisfies Skin;
  scalarCapture.bindings = [];
  scalarCapture.leases = [];
});

afterEach(() => {
  activation.selected = undefined;
  target.remove();
});

function render(over: Partial<Props> = {}) {
  const onLevelChange = vi.fn(over.onLevelChange ?? (() => {}));
  const props = proxy<Props>({
    view: withTxAux(topologyFixtures['1/single']),
    tx: RX_IDLE,
    presentation: 'grouped',
    levelFeedback: feedbackRecord(),
    ...over,
    onLevelChange,
  });
  const component = mount(TxAuxScalarHostFixture, { target, props });
  flushSync();
  const scalar = (field: TxAuxLevelField) => target.querySelector<RendererNode>(
    `[data-testid="tx-aux-${field}"] [data-external-scalar-renderer]`,
  );
  const row = (field: TxAuxLevelField) => target.querySelector<HTMLElement>(
    `[data-testid="tx-aux-${field}"]`,
  )!;
  return { props, onLevelChange, scalar, row, dispose: () => unmount(component) };
}

function currentLease(binding: unknown): ContinuousScalarRendererLease {
  for (let index = scalarCapture.leases.length - 1; index >= 0; index -= 1) {
    const entry = scalarCapture.leases[index]!;
    if (entry.binding === binding) return entry.lease as ContinuousScalarRendererLease;
  }
  throw new Error('renderer lease not captured');
}

describe('TxAuxScalarHost independent composition', () => {
  it('provides all eight structural scalar handles through the selected appearance', () => {
    const r = render();
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(8);
    expect(r.scalar('rfPower')?.dataset.evidence).toBe('reading');
    for (const [field] of FEEDBACK_FIELDS) {
      expect(r.scalar(field)?.dataset.evidence).toBe('command-feedback');
    }
    const zeroArgumentRow = r.row('micGain');
    expect(zeroArgumentRow.dataset.scalarForm).toBe('hbar');
    expect(zeroArgumentRow.classList).not.toContain('tx-aux-level--presented');
    expect(zeroArgumentRow.querySelector('.tx-aux-name')?.classList).not.toContain('sr-only');
    expect(zeroArgumentRow.querySelector('[data-canonical-value]')?.classList).not.toContain('sr-only');
    expect(r.scalar('micGain')?.dataset.showLabel).toBe('false');
    expect(r.scalar('micGain')?.dataset.showValue).toBe('false');
    expect(r.scalar('micGain')?.dataset.compact).toBe('true');
    expect(scalarCapture.bindings).toHaveLength(8);
    r.dispose();
  });

  it('keeps the same host bindings across keyed grouped/independent composition swaps', () => {
    const r = render();
    const bindings = [...scalarCapture.bindings];
    const staleLease = r.scalar('micGain')!.rendererLease;
    r.onLevelChange.mockImplementationOnce((_field, value) => {
      const current = r.props.levelFeedback!;
      r.props.levelFeedback = {
        ...current,
        micGain: {
          ...current.micGain, target: value, requestedTarget: value, phase: 'submitted', busy: true,
          lifecycleId: 'mic-129', transitionId: 'mic-submitted-129',
        },
        compressorLevel: {
          ...current.compressorLevel, requestedTarget: 20, phase: 'failed',
          lifecycleId: 'comp-20', transitionId: 'comp-failed-20',
          outcome: { phase: 'failed', error: 'radio refused' },
        },
      };
    });
    r.scalar('micGain')!.click();
    flushSync();
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('micGain', 129);
    expect(r.scalar('micGain')?.dataset.display).toBe('51%');

    r.props.presentation = 'independent';
    flushSync();

    expect(target.querySelectorAll('[data-testid="tx-aux-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="tx-aux-atu-tune"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(8);
    expect(scalarCapture.bindings).toEqual(bindings);
    expect(r.scalar('micGain')?.dataset.requested).toBe('129');
    expect(r.scalar('micGain')?.dataset.phase).toBe('submitted');
    expect(r.scalar('compressorLevel')?.dataset.error).toBe('radio refused');
    expect(staleLease.key({ key: 'ArrowRight', fine: false })).toBe(false);
    r.scalar('micGain')!.click();
    expect(r.onLevelChange).toHaveBeenCalledTimes(2);
    r.dispose();
  });

  it('does not fabricate reset-to-min when no scalar declares a default', () => {
    activation.selected = undefined;
    const r = render();
    for (const slider of target.querySelectorAll<HTMLElement>('[role="slider"]')) {
      slider.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    }
    flushSync();
    expect(r.onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it.each([
    ['hbar', 'knob', 'hbar'],
    ['knob', 'hbar', 'knob'],
  ] as const)(
    'retains all bindings and fences stale renderer leases across %s-%s-%s',
    (first, middle, last) => {
      activation.selected = undefined;
      const r = render({
        presentation: 'independent',
        scalarPresentation: { form: first },
      });
      const bindings = [...scalarCapture.bindings];
      const firstLeases = bindings.map(currentLease);
      const pointerTokens = firstLeases.map((lease) => lease.beginPointer());

      r.props.scalarPresentation = {
        form: middle, compact: true, showLabel: false, showValue: false,
      };
      flushSync();

      expect(scalarCapture.bindings).toEqual(bindings);
      for (const [field] of TX_AUX_LEVELS) {
        const row = r.row(field);
        expect(row.dataset.scalarForm).toBe(middle);
        const renderer = row.querySelector<HTMLElement>(middle === 'knob' ? '.vc-knob' : '.vc-hbar')!;
        expect(renderer.classList).toContain('compact');
        expect(renderer.querySelector('.vc-label')).toBeNull();
        expect(renderer.querySelector('.vc-value, .vc-knob-value')).toBeNull();
      }
      for (const [index, lease] of firstLeases.entries()) {
        const token = pointerTokens[index];
        if (token !== null) {
          lease.pointer(token, 1);
          lease.endPointer(token);
        }
        lease.nativeInput(1);
        lease.wheel({ direction: 1, fine: false });
        expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(false);
      }
      expect(r.onLevelChange).not.toHaveBeenCalled();

      const middleLeases = bindings.map(currentLease);
      r.props.scalarPresentation = { form: last };
      flushSync();

      expect(scalarCapture.bindings).toEqual(bindings);
      expect(target.querySelectorAll(last === 'knob' ? '.vc-knob' : '.vc-hbar')).toHaveLength(8);
      for (const lease of middleLeases) {
        lease.nativeInput(1);
        lease.wheel({ direction: -1, fine: true });
        expect(lease.key({ key: 'ArrowLeft', fine: false })).toBe(false);
      }
      expect(r.onLevelChange).not.toHaveBeenCalled();
      r.dispose();
    },
  );

  it('observes a current callback replacement without recreating bindings', () => {
    const r = render({
      presentation: 'independent',
      scalarPresentation: { form: 'knob' },
    });
    const bindings = [...scalarCapture.bindings];
    const replacement = vi.fn();
    r.props.onLevelChange = replacement;
    flushSync();

    r.scalar('micGain')!.click();

    expect(replacement).toHaveBeenCalledExactlyOnceWith('micGain', 129);
    expect(r.onLevelChange).not.toHaveBeenCalled();
    expect(scalarCapture.bindings).toEqual(bindings);
    r.dispose();
  });

  it.each(['hbar', 'knob'] as const)(
    'keeps the existing HBar policy for current %s renderer input', (form) => {
      activation.selected = undefined;
      const r = render({
        presentation: 'independent',
        scalarPresentation: { form },
      });
      const micBinding = scalarCapture.bindings[1]!;
      const lease = currentLease(micBinding);

      expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
      lease.wheel({ direction: 1, fine: false });
      lease.nativeInput(129.44);
      const token = lease.beginPointer();
      expect(token).not.toBeNull();
      lease.pointer(token!, 130.06);
      lease.endPointer(token!);
      lease.reset();

      expect(r.onLevelChange.mock.calls).toEqual([
        ['micGain', 129],
        ['micGain', 132],
        ['micGain', 129],
        ['micGain', 130],
      ]);
      r.dispose();
    },
  );

  it.each(['hbar', 'knob'] as const)(
    'uses a neutral %s seat with one renderer-owned visible label and value', (form) => {
      activation.selected = undefined;
      const r = render({
        presentation: 'independent',
        scalarPresentation: { form },
      });
      const row = r.row('micGain');
      const renderer = row.querySelector<HTMLElement>(form === 'knob' ? '.vc-knob' : '.vc-hbar')!;
      const slider = renderer.querySelector<HTMLElement>('[role="slider"]')!;

      expect(row.classList).toContain('tx-aux-level--presented');
      expect(row.querySelector('.tx-aux-name')?.classList).toContain('sr-only');
      expect(row.querySelector('.tx-aux-name')?.getAttribute('aria-hidden')).toBe('true');
      expect(row.querySelector('[data-canonical-value]')?.classList).toContain('sr-only');
      expect(renderer.classList).not.toContain('compact');
      expect(renderer.querySelectorAll('.vc-label')).toHaveLength(1);
      expect(renderer.querySelectorAll('.vc-value, .vc-knob-value')).toHaveLength(1);
      expect(slider.getAttribute('aria-label')).toBe('Mic gain');
      expect(slider.getAttribute('aria-valuetext')).toContain('Mic gain: 50%');
      r.dispose();
    },
  );

  it.each(['hbar', 'knob'] as const)(
    'keeps canonical, pending, and accessibility evidence on the actual %s slider', (form) => {
    activation.selected = undefined;
    const levels = feedbackRecord();
    const r = render({
      presentation: 'independent',
      scalarPresentation: { form, showLabel: false, showValue: false },
      levelFeedback: {
        ...levels,
        micGain: {
          ...levels.micGain,
          target: 200,
          requestedTarget: 200,
          phase: 'submitted',
          busy: true,
          lifecycleId: 'mic-200',
          transitionId: 'mic-submitted-200',
        },
      },
    });
    const row = target.querySelector<HTMLElement>('[data-testid="tx-aux-micGain"]')!;
    const slider = row.querySelector<HTMLElement>('[role="slider"]')!;

    expect(row.classList).toContain('tx-aux-level--presented');
    expect(row.querySelector('.tx-aux-name')?.classList).toContain('sr-only');
    expect(row.querySelector('[data-canonical-value]')?.classList).toContain('sr-only');
    expect(slider.closest(form === 'knob' ? '.vc-knob' : '.vc-hbar')).not.toBeNull();
    expect(slider.getAttribute('aria-valuenow')).toBe('128');
    expect(slider.getAttribute('aria-valuetext')).toContain('requested 78%');
    expect(slider.getAttribute('aria-valuetext')).toContain('confirmed 50%');
    expect(slider.getAttribute('aria-busy')).toBe('true');
    expect(slider.dataset.commandPhase).toBe('submitted');

    r.props.levelFeedback = {
      ...levels,
      micGain: {
        ...levels.micGain,
        requestedTarget: 200,
        phase: 'failed',
        lifecycleId: 'mic-200',
        transitionId: 'mic-failed-200',
        outcome: { phase: 'failed', error: 'radio refused' },
      },
    };
    flushSync();
    expect(slider.getAttribute('aria-valuenow')).toBe('128');
    expect(slider.getAttribute('aria-valuetext')).toContain('radio refused');
    expect(slider.getAttribute('aria-busy')).toBe('false');
    expect(slider.dataset.commandPhase).toBe('failed');

    r.props.view = {
      ...r.props.view,
      txAux: {
        ...r.props.view.txAux!,
        micGain: {
          reading: { status: 'unknown' },
          availability: { structural: true, operational: false },
        },
      },
    };
    r.props.levelFeedback = {
      ...levels,
      micGain: {
        ...levels.micGain,
        confirmed: null,
        availability: 'unavailable',
        phase: 'unavailable',
      },
    };
    flushSync();
    expect(slider.getAttribute('aria-valuenow')).toBeNull();
    expect(slider.getAttribute('aria-disabled')).toBe('true');
    expect(slider.getAttribute('aria-valuetext')).toContain('Mic gain: ?');
    expect(slider.getAttribute('aria-valuetext')).toContain('unavailable');
    expect(slider.getAttribute('aria-describedby')).not.toBeNull();
    r.dispose();
    },
  );

  it('uses one announcement lane and retires stale HBar speech on form switches', () => {
    activation.selected = undefined;
    const levels = feedbackRecord();
    const failed = (transitionId: string) => ({
      ...levels,
      micGain: {
        ...levels.micGain,
        requestedTarget: 200,
        phase: 'failed' as const,
        lifecycleId: 'mic-200',
        transitionId,
        outcome: { phase: 'failed' as const, error: 'radio refused' },
      },
    });
    const r = render({
      presentation: 'independent',
      scalarPresentation: { form: 'hbar' },
      levelFeedback: failed('mic-failed-hbar'),
    });
    const live = () => target.querySelectorAll('[role="status"][aria-live="polite"]');

    expect(live()).toHaveLength(1);
    expect(target.querySelector('[data-feedback-lane="micGain"]')?.textContent).toContain(
      'radio refused',
    );

    r.props.scalarPresentation = { form: 'knob' };
    flushSync();
    expect(live()).toHaveLength(0);
    expect(target.querySelector('[data-feedback-lane="micGain"]')).toBeNull();

    r.props.levelFeedback = failed('mic-failed-knob');
    flushSync();
    expect(live()).toHaveLength(1);
    expect(live()[0]?.textContent).toContain('radio refused');

    r.props.scalarPresentation = { form: 'hbar' };
    flushSync();
    expect(live()).toHaveLength(0);

    r.props.levelFeedback = failed('mic-failed-hbar-next');
    flushSync();
    expect(live()).toHaveLength(1);
    expect(target.querySelectorAll('[data-feedback-lane="micGain"]')).toHaveLength(1);
    r.dispose();
  });
});
