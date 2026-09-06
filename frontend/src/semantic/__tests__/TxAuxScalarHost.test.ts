import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';

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
} from '../tx-aux-scalar';

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
  levelFeedback?: TxAuxLevelFeedback;
  onLevelChange?: (field: TxAuxLevelField, value: number) => void;
};

let target: HTMLDivElement;

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  activation.selected = {
    name: 'TxAux test appearance', hbar: ExternalScalarRenderer,
  } satisfies Skin;
  scalarCapture.bindings = [];
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
  return { props, onLevelChange, scalar, dispose: () => unmount(component) };
}

describe('TxAuxScalarHost independent composition', () => {
  it('provides all eight structural scalar handles through the selected appearance', () => {
    const r = render();
    expect(target.querySelectorAll('[data-external-scalar-renderer]')).toHaveLength(8);
    expect(r.scalar('rfPower')?.dataset.evidence).toBe('reading');
    for (const [field] of FEEDBACK_FIELDS) {
      expect(r.scalar(field)?.dataset.evidence).toBe('command-feedback');
    }
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
});
