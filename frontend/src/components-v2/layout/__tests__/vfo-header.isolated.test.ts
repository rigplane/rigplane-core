import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { VfoStateProps } from '../layout-utils';

const childHarness = vi.hoisted(() => ({
  vfoOpsProps: null as Record<string, any> | null,
  dualVfoProps: null as Record<string, any> | null,
  vfoPanelProps: null as Record<string, any> | null,
}));

vi.mock('../../vfo/VfoOps.svelte', () => ({
  default: function VfoOpsStub(_anchor: unknown, props: Record<string, any>) {
    childHarness.vfoOpsProps = props;
    return {};
  },
}));
vi.mock('../../panels/vfo/DualVfoDisplay.svelte', () => ({
  default: function DualVfoDisplayStub(_anchor: unknown, props: Record<string, any>) {
    childHarness.dualVfoProps = props;
    return {};
  },
}));
vi.mock('../../vfo/VfoPanel.svelte', () => ({
  default: function VfoPanelStub(_anchor: unknown, props: Record<string, any>) {
    childHarness.vfoPanelProps = props;
    return {};
  },
}));

const capabilityHarness = vi.hoisted(() => ({ dual: true, scope: true }));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasDualReceiver: vi.fn(() => capabilityHarness.dual),
  hasCapability: vi.fn((name: string) => name === 'scope' && capabilityHarness.scope),
  getVfoScheme: vi.fn(() => 'main_sub'),
}));

const runtimeHarness = vi.hoisted(() => ({
  runtime: {
    state: Object.freeze({ identity: 'header-state' }),
    caps: Object.freeze({ identity: 'header-capabilities' }),
  },
}));

const authorityHarness = vi.hoisted(() => {
  const harness = {
    current: null as any,
    toSpectrumAuthority: vi.fn(() => harness.current),
  };
  return harness;
});

const binderHarness = vi.hoisted(() => {
  const scopeControls = Object.freeze({
    onModeChange: vi.fn(), onEdgeChange: vi.fn(), onSpanChange: vi.fn(),
    onSpeedChange: vi.fn(), onHoldChange: vi.fn(), onRefChange: vi.fn(),
    onDualChange: vi.fn(), onReceiverChange: vi.fn(), onDuringTxChange: vi.fn(),
    onCenterTypeChange: vi.fn(), onVbwChange: vi.fn(), onRbwChange: vi.fn(),
  });
  const bound = Object.freeze({ scopeControls });
  const vfo = Object.freeze({
    onMainVfoClick: vi.fn(),
    onSubVfoClick: vi.fn(),
    onFreqChange: vi.fn(),
  });
  return {
    scopeControls,
    vfo,
    bindSemanticSurfaceHandlers: vi.fn(() => bound),
    getVfoHandlers: vi.fn(() => vfo),
  };
});

const storeAlarm = vi.hoisted(() => ({ patchRadioState: vi.fn() }));

vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: runtimeHarness.runtime }));
vi.mock('$lib/runtime/adapters/scope-adapter', () => ({
  toSpectrumAuthority: authorityHarness.toSpectrumAuthority,
}));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  bindSemanticSurfaceHandlers: binderHarness.bindSemanticSurfaceHandlers,
  getVfoHandlers: binderHarness.getVfoHandlers,
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: storeAlarm.patchRadioState,
  patchReceiver: vi.fn(),
}));

import VfoHeader from '../VfoHeader.svelte';

type Field<T> = {
  reading: { status: 'known'; value: T } | { status: 'unknown' };
  availability: { structural: boolean; operational: boolean };
};

function field<T>(value: T, options: {
  known?: boolean;
  structural?: boolean;
  operational?: boolean;
} = {}): Field<T> {
  const { known = true, structural = true, operational = true } = options;
  return Object.freeze({
    reading: known
      ? Object.freeze({ status: 'known' as const, value })
      : Object.freeze({ status: 'unknown' as const }),
    availability: Object.freeze({ structural, operational }),
  });
}

function scopeFacts(overrides: Record<string, unknown> = {}) {
  return Object.freeze({
    mode: field(0), edge: field(1), span: field(3), speed: field(1),
    hold: field(false), refDb: field(0), dual: field(false), receiver: field(0),
    duringTx: field(false), centerType: field(0), vbwNarrow: field(false), rbw: field(0),
    ...overrides,
  });
}

function authority(overrides: Record<string, unknown> = {}) {
  return Object.freeze({ scopeControls: scopeFacts(), ...overrides });
}

const mainVfo: VfoStateProps = {
  receiver: 'main', freq: 14_074_000, mode: 'USB', filter: 'FIL1',
  sValue: 0, isActive: true, badges: {},
};
const subVfo: VfoStateProps = {
  receiver: 'sub', freq: 7_074_000, mode: 'LSB', filter: 'FIL1',
  sValue: 0, isActive: false, badges: {},
};

const components: ReturnType<typeof mount>[] = [];
const targets: HTMLElement[] = [];

function mountHeader(overrides: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  targets.push(target);
  const props = {
    mainVfo: { ...mainVfo },
    subVfo: { ...subVfo },
    splitActive: false,
    dualWatchActive: false,
    txVfo: 'main' as const,
    scopeStatus: { dual: true, receiver: 1, span: 7, speed: 2 },
    ...overrides,
  };
  const component = mount(VfoHeader, { target, props });
  components.push(component);
  flushSync();
  return { target, props };
}

function button(root: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
    .find((item) => item.textContent?.trim() === label);
}

beforeEach(() => {
  vi.clearAllMocks();
  childHarness.vfoOpsProps = null;
  childHarness.dualVfoProps = null;
  childHarness.vfoPanelProps = null;
  capabilityHarness.dual = true;
  capabilityHarness.scope = true;
  authorityHarness.current = authority();
});

afterEach(() => {
  while (components.length > 0) unmount(components.pop()!);
  while (targets.length > 0) targets.pop()!.remove();
});

describe('VfoHeader confirmed receiver authority', () => {
  it('binds one canonical VFO and scope object per mount', () => {
    mountHeader();
    expect(binderHarness.getVfoHandlers).toHaveBeenCalledTimes(1);
    expect(binderHarness.bindSemanticSurfaceHandlers).toHaveBeenCalledTimes(1);
    expect(authorityHarness.toSpectrumAuthority).toHaveBeenCalledWith(
      runtimeHarness.runtime.state,
      runtimeHarness.runtime.caps,
    );
  });

  it('VfoOps receiver selection calls only the canonical handler and leaves highlight observed', () => {
    const passedSub = vi.fn();
    mountHeader({ onSubVfoClick: passedSub });
    expect(childHarness.vfoOpsProps?.activeVfo).toBe('MAIN');
    childHarness.vfoOpsProps?.onActiveVfoChange('SUB');
    flushSync();
    expect(binderHarness.vfo.onSubVfoClick).toHaveBeenCalledTimes(1);
    expect(binderHarness.vfo.onMainVfoClick).not.toHaveBeenCalled();
    expect(passedSub).not.toHaveBeenCalled();
    expect(storeAlarm.patchRadioState).not.toHaveBeenCalled();
    expect(childHarness.vfoOpsProps?.activeVfo).toBe('MAIN');
  });

  it('the retained DualVfoDisplay compatibility callback uses the same canonical handlers', () => {
    const passedMain = vi.fn();
    const passedSub = vi.fn();
    mountHeader({ onMainVfoClick: passedMain, onSubVfoClick: passedSub });
    childHarness.dualVfoProps?.onActivate('SUB');
    childHarness.dualVfoProps?.onActivate('MAIN');
    expect(binderHarness.vfo.onSubVfoClick).toHaveBeenCalledTimes(1);
    expect(binderHarness.vfo.onMainVfoClick).toHaveBeenCalledTimes(1);
    expect(passedMain).not.toHaveBeenCalled();
    expect(passedSub).not.toHaveBeenCalled();
    expect(storeAlarm.patchRadioState).not.toHaveBeenCalled();
  });

  it('a contradictory observation rerender wins immediately and no click creates truth', () => {
    mountHeader();
    childHarness.vfoOpsProps?.onActiveVfoChange('SUB');
    expect(childHarness.vfoOpsProps?.activeVfo).toBe('MAIN');
    unmount(components.pop()!);
    targets.pop()!.remove();
    mountHeader({
      mainVfo: { ...mainVfo, isActive: false },
      subVfo: { ...subVfo, isActive: true },
    });
    expect(childHarness.vfoOpsProps?.activeVfo).toBe('SUB');
    expect(storeAlarm.patchRadioState).not.toHaveBeenCalled();
  });

  it('single-MAIN path also uses the canonical VFO handler', () => {
    capabilityHarness.dual = false;
    const passedMain = vi.fn();
    mountHeader({ onMainVfoClick: passedMain });
    childHarness.vfoPanelProps?.onVfoClick();
    expect(binderHarness.vfo.onMainVfoClick).toHaveBeenCalledTimes(1);
    expect(passedMain).not.toHaveBeenCalled();
  });
});

describe('VfoHeader scope bridge authority', () => {
  it('ignores contradictory legacy props and renders the selector digest', () => {
    const { target } = mountHeader({
      scopeStatus: { dual: true, receiver: 1, span: 7, speed: 2 },
    });
    const digest = target.querySelector('.scope-digest')?.textContent?.replace(/\s+/g, ' ').trim();
    expect(digest).toBe('±25k MID');
    expect(button(target, 'MAIN')?.classList.contains('active')).toBe(true);
    expect(button(target, 'SUB')?.classList.contains('active')).toBe(false);
    expect(button(target, 'DUAL')?.classList.contains('active')).toBe(false);
  });

  it('valid source and dual gestures call exactly one bound typed action', () => {
    const passedReceiver = vi.fn();
    const passedDual = vi.fn();
    const { target } = mountHeader({
      onScopeReceiverChange: passedReceiver,
      onScopeDualToggle: passedDual,
    });
    button(target, 'SUB')?.click();
    expect(binderHarness.scopeControls.onReceiverChange).toHaveBeenCalledWith(1);
    expect(binderHarness.scopeControls.onReceiverChange).toHaveBeenCalledTimes(1);
    expect(passedReceiver).not.toHaveBeenCalled();
    vi.clearAllMocks();
    button(target, 'DUAL')?.click();
    expect(binderHarness.scopeControls.onDualChange).toHaveBeenCalledWith(true);
    expect(binderHarness.scopeControls.onDualChange).toHaveBeenCalledTimes(1);
    expect(passedDual).not.toHaveBeenCalled();
  });

  it.each([
    ['unknown', { known: false }],
    ['structural false', { structural: false }],
    ['operational false', { operational: false }],
  ] as const)('%s scope facts render neutral and disable source/dual actions', (_label, options) => {
    authorityHarness.current = authority({
      scopeControls: scopeFacts({
        dual: field(false, options),
        receiver: field(0, options),
        span: field(3, options),
        speed: field(1, options),
      }),
    });
    const { target } = mountHeader();
    expect(target.querySelector('.scope-digest')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('— —');
    for (const label of ['MAIN', 'SUB', 'DUAL']) {
      expect(button(target, label)?.disabled).toBe(true);
      expect(button(target, label)?.classList.contains('active')).toBe(false);
      button(target, label)?.click();
    }
    expect(binderHarness.scopeControls.onReceiverChange).not.toHaveBeenCalled();
    expect(binderHarness.scopeControls.onDualChange).not.toHaveBeenCalled();
  });

  it('malformed and selector-null facts never restore MAIN/±25k/MID defaults', () => {
    authorityHarness.current = authority({
      scopeControls: scopeFacts({
        receiver: field(2), dual: field('off'), span: field(99), speed: field(99),
      }),
    });
    const malformed = mountHeader().target;
    expect(malformed.querySelector('.scope-digest')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('— —');
    expect(button(malformed, 'MAIN')?.classList.contains('active')).toBe(false);
    expect(button(malformed, 'SUB')?.classList.contains('active')).toBe(false);

    unmount(components.pop()!);
    targets.pop()!.remove();
    authorityHarness.current = null;
    const absent = mountHeader().target;
    expect(absent.querySelector('.scope-digest')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('— —');
  });

  it('one physical receiver hides source and dual controls but keeps neutral/read-only digest', () => {
    capabilityHarness.dual = false;
    authorityHarness.current = authority({
      scopeControls: scopeFacts({
        dual: field(false, { structural: false }),
        receiver: field(0, { structural: false }),
      }),
    });
    const { target } = mountHeader();
    expect(button(target, 'MAIN')).toBeUndefined();
    expect(button(target, 'SUB')).toBeUndefined();
    expect(button(target, 'DUAL')).toBeUndefined();
    expect(target.querySelector('.scope-digest')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('±25k MID');
  });

  it('hides the entire bridge only when structural scope capability is absent', () => {
    capabilityHarness.scope = false;
    const { target } = mountHeader();
    expect(target.querySelector('[data-testid="scope-status"]')).toBeNull();
  });
});

describe('VfoHeader source boundary', () => {
  it('has one selector/binder path and no optimistic or legacy action authority', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components-v2/layout/VfoHeader.svelte'), 'utf8');
    expect(source).toContain('toSpectrumAuthority(runtime.state, runtime.caps)');
    expect(source.match(/bindSemanticSurfaceHandlers\(\)/g)).toHaveLength(1);
    expect(source.match(/getVfoHandlers\(\)/g)).toHaveLength(1);
    expect(source).not.toMatch(/patchRadioState|stores\/radio\.svelte/);
    expect(source).not.toMatch(/onScopeReceiverChange\?\.|onScopeDualToggle\?\./);
  });
});
