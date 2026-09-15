import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import {
  createFiniteRendererContext, type FiniteControlAppearance, type FiniteRendererContext,
} from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import TxAuxScalarHostFixture from './fixtures/TxAuxScalarHostFixture.svelte';
import { topologyFixtures, withTxAux } from '../fixtures/topologies';
import type { Availability, RadioViewModel, TxAuxViewModel } from '../radio-view-model';
import type { TxAuthoritySnapshot } from '../rx-tx-surface';
import type { TxAuxToggleField } from '../tx-aux-finite';

const RX_IDLE: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null,
};
const BLOCKED: TxAuthoritySnapshot = {
  phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on', fault: null,
};
const fixture = FiniteControlRendererFixture as FiniteControlAppearance['action'];
const appearance = {
  action: fixture,
  toggle: FiniteControlRendererFixture as FiniteControlAppearance['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance['choice'],
} satisfies FiniteControlAppearance;

const base = (): RadioViewModel => withTxAux(topologyFixtures['1/single']);
const withField = (
  field: keyof TxAuxViewModel,
  over: { availability?: Availability; unknown?: boolean; value?: unknown },
): RadioViewModel => {
  const view = base();
  const current = view.txAux![field];
  return {
    ...view,
    txAux: {
      ...view.txAux!,
      [field]: {
        availability: over.availability ?? current.availability,
        reading: over.unknown
          ? { status: 'unknown' }
          : over.value === undefined ? current.reading : { status: 'known', value: over.value },
      },
    } as TxAuxViewModel,
  };
};

type Props = {
  view: RadioViewModel;
  tx: TxAuthoritySnapshot;
  presentation: 'grouped' | 'independent';
  finiteAppearance?: FiniteControlAppearance;
  rendererContext?: FiniteRendererContext | null;
  onToggle?: (field: TxAuxToggleField) => void;
  onAtuTune?: () => void;
};

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});
afterEach(() => {
  resetRetainedInvocations();
  target.remove();
});

function render(over: Partial<Props> = {}) {
  const onToggle = vi.fn(over.onToggle ?? (() => {}));
  const onAtuTune = vi.fn(over.onAtuTune ?? (() => {}));
  const props = proxy<Props>({
    view: base(), tx: RX_IDLE, presentation: 'grouped', ...over, onToggle, onAtuTune,
  });
  const component = mount(TxAuxScalarHostFixture, { target, props });
  flushSync();
  return { props, onToggle, onAtuTune, dispose: () => unmount(component) };
}

const bypassClick = (element: HTMLElement) =>
  element.dispatchEvent(new MouseEvent('click', { bubbles: true }));

describe('TxAuxFiniteHost native composition', () => {
  it('provides exactly five finite handles beside the persistent eight scalars', () => {
    const r = render();
    expect(target.querySelectorAll('.tx-aux-toggle')).toHaveLength(4);
    expect(target.querySelectorAll('[data-testid="tx-aux-atu-tune"]')).toHaveLength(1);
    expect(target.querySelectorAll('[role="slider"]')).toHaveLength(8);

    r.props.presentation = 'independent';
    flushSync();
    expect(target.querySelectorAll('[data-testid="tx-aux-surface"]')).toHaveLength(1);
    expect(target.querySelector('[data-testid="tx-aux-surface"]')?.children).toHaveLength(1);
    expect(target.querySelector('[data-testid="tx-aux-surface"]')?.firstElementChild
      ?.getAttribute('data-testid')).toBe('tx-aux-tune-blocked');
    expect(target.querySelectorAll('.tx-aux-toggle')).toHaveLength(4);
    expect(target.querySelectorAll('[data-testid="tx-aux-atu-tune"]')).toHaveLength(1);
    expect(target.querySelectorAll('[role="slider"]')).toHaveLength(8);
    r.dispose();
  });

  it('keeps ATU tuning pressed and explicitly named as tuning', () => {
    const r = render({ view: withField('atu', { value: 'tuning' }) });
    const atu = target.querySelector<HTMLButtonElement>('[data-testid="tx-aux-atu"]')!;
    expect(atu.ariaPressed).toBe('true');
    expect(atu.textContent).toBe('ATU: tuning');
    expect(atu.ariaLabel).toBe('ATU: tuning');
    r.dispose();
  });

  it('refuses unknown toggle and blocked TUNE even when disabled DOM is bypassed', () => {
    const r = render({ view: withField('vox', { unknown: true }), tx: BLOCKED });
    const vox = target.querySelector<HTMLButtonElement>('[data-testid="tx-aux-vox"]')!;
    const tune = target.querySelector<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!;
    expect(vox.disabled).toBe(true);
    expect(vox.title).toBe('Not yet observed');
    expect(tune.disabled).toBe(true);
    expect(tune.title.length).toBeGreaterThan(0);
    const reasons = target.querySelector('[data-testid="tx-aux-tune-blocked"]')!;
    const scalars = target.querySelectorAll('[role="slider"]');
    expect(target.querySelectorAll('[data-testid="tx-aux-tune-blocked"]')).toHaveLength(1);
    expect(scalars[7]!.compareDocumentPosition(reasons) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    bypassClick(vox);
    bypassClick(tune);
    expect(r.onToggle).not.toHaveBeenCalled();
    expect(r.onAtuTune).not.toHaveBeenCalled();
    r.dispose();
  });

  it('keeps ordinary ATU independent from the TUNE TX gate', () => {
    const r = render({ tx: BLOCKED });
    const atu = target.querySelector<HTMLButtonElement>('[data-testid="tx-aux-atu"]')!;
    expect(atu.disabled).toBe(false);
    atu.click();
    expect(r.onToggle).toHaveBeenCalledExactlyOnceWith('atu');
    expect(r.onAtuTune).not.toHaveBeenCalled();
    r.dispose();
  });
});

describe('TxAuxFiniteHost external leases', () => {
  it('uses host labels and current canonical callbacks', () => {
    const r = render({ finiteAppearance: appearance, rendererContext: createFiniteRendererContext() });
    expect(target.querySelector('[data-testid="tx-aux-atu"]')).toBeNull();
    (target.querySelector('[data-testid="external-VOX"]') as HTMLButtonElement).click();
    (target.querySelector('[data-testid="external-TUNE"]') as HTMLButtonElement).click();
    expect(r.onToggle).toHaveBeenCalledExactlyOnceWith('vox');
    expect(r.onAtuTune).toHaveBeenCalledOnce();
    r.dispose();
  });

  it('preserves ATU tuning text and refuses an unknown external toggle', () => {
    const view = withField('atu', { value: 'tuning' });
    const unknownVox = view.txAux!.vox;
    const r = render({
      finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext(),
      tx: BLOCKED,
      view: {
        ...view,
        txAux: {
          ...view.txAux!,
          vox: { ...unknownVox, reading: { status: 'unknown' } },
        },
      },
    });
    const atu = target.querySelector<HTMLButtonElement>('[data-testid="external-ATU"]')!;
    const vox = target.querySelector<HTMLButtonElement>('[data-testid="external-VOX"]')!;
    expect(atu.ariaPressed).toBe('true');
    expect(atu.ariaLabel).toBe('ATU: tuning');
    expect(vox.disabled).toBe(true);
    const reasons = target.querySelector('[data-testid="tx-aux-tune-blocked"]')!;
    const scalars = target.querySelectorAll('[role="slider"]');
    expect(target.querySelectorAll('[data-testid="tx-aux-tune-blocked"]')).toHaveLength(1);
    expect(reasons.querySelectorAll('[data-reason]')).toHaveLength(2);
    expect(scalars[7]!.compareDocumentPosition(reasons) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    bypassClick(vox);
    expect(r.onToggle).not.toHaveBeenCalled();
    r.dispose();
  });

  it('keeps a selected appearance inert at null context without native fallback', () => {
    const r = render({ finiteAppearance: appearance, rendererContext: null });
    expect(target.querySelector('[data-testid="tx-aux-atu"]')).toBeNull();
    expect(target.querySelector('[data-testid="external-ATU"]')).toBeNull();
    expect(retainedInvocations.size).toBe(0);
    r.dispose();
  });

  it('revokes a retained renderer when grouped content is replaced', () => {
    const r = render({ finiteAppearance: appearance, rendererContext: createFiniteRendererContext() });
    const retained = retainedInvocations.get('VOX')!;
    r.props.presentation = 'independent';
    flushSync();
    retained();
    expect(r.onToggle).not.toHaveBeenCalled();
    retainedInvocations.get('VOX')!();
    expect(r.onToggle).toHaveBeenCalledExactlyOnceWith('vox');
    r.dispose();
  });

  it('permanently revokes A1 across context A-B-A and admits fresh A3', () => {
    const contextA = createFiniteRendererContext();
    const contextB = createFiniteRendererContext();
    const r = render({ finiteAppearance: appearance, rendererContext: contextA });
    const retainedA1 = retainedInvocations.get('VOX')!;

    r.props.rendererContext = contextB;
    flushSync();
    r.props.rendererContext = contextA;
    flushSync();

    retainedA1();
    expect(r.onToggle).not.toHaveBeenCalled();
    retainedInvocations.get('VOX')!();
    expect(r.onToggle).toHaveBeenCalledExactlyOnceWith('vox');
    r.dispose();
  });

  it('keeps a same-context lease live while canonical truth changes', () => {
    const rendererContext = createFiniteRendererContext();
    const r = render({ finiteAppearance: appearance, rendererContext });
    const retained = retainedInvocations.get('VOX')!;
    r.props.view = withField('vox', { value: true });
    flushSync();
    expect(target.querySelector<HTMLButtonElement>('[data-testid="external-VOX"]')!.ariaPressed).toBe('true');
    retained();
    expect(r.onToggle).toHaveBeenCalledExactlyOnceWith('vox');
    r.dispose();
  });

  it('revokes every retained renderer on host teardown', () => {
    const r = render({ finiteAppearance: appearance, rendererContext: createFiniteRendererContext() });
    const retained = [...retainedInvocations.values()];
    expect(retained).toHaveLength(5);
    r.dispose();
    for (const invoke of retained) invoke();
    expect(r.onToggle).not.toHaveBeenCalled();
    expect(r.onAtuTune).not.toHaveBeenCalled();
  });
});
