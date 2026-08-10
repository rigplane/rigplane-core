import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const runtimeHarness = vi.hoisted(() => ({
  runtime: {
    state: Object.freeze({ identity: 'popover-state' }),
    caps: Object.freeze({ identity: 'popover-capabilities' }),
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
    onModeChange: vi.fn(),
    onEdgeChange: vi.fn(),
    onSpanChange: vi.fn(),
    onSpeedChange: vi.fn(),
    onHoldChange: vi.fn(),
    onRefChange: vi.fn(),
    onDualChange: vi.fn(),
    onReceiverChange: vi.fn(),
    onDuringTxChange: vi.fn(),
    onCenterTypeChange: vi.fn(),
    onVbwChange: vi.fn(),
    onRbwChange: vi.fn(),
  });
  const bound = Object.freeze({ scopeControls });
  return {
    scopeControls,
    bindSemanticSurfaceHandlers: vi.fn(() => bound),
  };
});

const rawRadioAlarm = vi.hoisted(() => ({
  current: {
    scopeControls: {
      centerType: 0,
      vbwNarrow: false,
      rbw: 0,
      duringTx: false,
    },
  } as any,
}));
const rawSendAlarm = vi.hoisted(() => vi.fn());

vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: runtimeHarness.runtime }));
vi.mock('$lib/runtime/adapters/scope-adapter', () => ({
  toSpectrumAuthority: authorityHarness.toSpectrumAuthority,
}));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  bindSemanticSurfaceHandlers: binderHarness.bindSemanticSurfaceHandlers,
}));
vi.mock('$lib/stores/radio.svelte', () => ({ radio: rawRadioAlarm }));
vi.mock('$lib/transport/ws-client', () => ({ sendCommand: rawSendAlarm }));

import ScopeSettingsPopover from '../ScopeSettingsPopover.svelte';

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

function facts(overrides: Record<string, unknown> = {}) {
  return Object.freeze({
    mode: field(0), edge: field(1), span: field(3), speed: field(1),
    hold: field(false), refDb: field(0), dual: field(false), receiver: field(0),
    duringTx: field(true), centerType: field(1), vbwNarrow: field(true), rbw: field(1),
    ...overrides,
  });
}

function authority(scopeControls: ReturnType<typeof facts> | null = facts()) {
  return Object.freeze({ scopeControls });
}

const components: ReturnType<typeof mount>[] = [];
const targets: HTMLElement[] = [];

function mountPopover(onClose = vi.fn()) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  targets.push(target);
  const component = mount(ScopeSettingsPopover, { target, props: { onClose } });
  components.push(component);
  flushSync();
  return { target, onClose };
}

function button(root: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
    .find((item) => item.textContent?.trim() === label);
  if (!found) throw new Error(`button ${label} not found`);
  return found;
}

function radioButtons(root: HTMLElement): HTMLButtonElement[] {
  return Array.from(root.querySelectorAll<HTMLButtonElement>('.setting-btn'));
}

function familyButtons(root: HTMLElement, family: string): HTMLButtonElement[] {
  const group = Array.from(root.querySelectorAll<HTMLElement>('.setting-group'))
    .find((item) => item.querySelector('.setting-label')?.textContent?.trim() === family);
  if (!group) throw new Error(`family ${family} not found`);
  return Array.from(group.querySelectorAll<HTMLButtonElement>('.setting-btn'));
}

function radioSpies(): ReturnType<typeof vi.fn>[] {
  return [
    binderHarness.scopeControls.onCenterTypeChange,
    binderHarness.scopeControls.onVbwChange,
    binderHarness.scopeControls.onRbwChange,
    binderHarness.scopeControls.onDuringTxChange,
  ];
}

beforeEach(() => {
  vi.clearAllMocks();
  authorityHarness.current = authority();
  rawRadioAlarm.current = {
    scopeControls: { centerType: 0, vbwNarrow: false, rbw: 0, duringTx: false },
  };
});

afterEach(() => {
  while (components.length > 0) unmount(components.pop()!);
  while (targets.length > 0) targets.pop()!.remove();
});

describe('ScopeSettingsPopover radio authority', () => {
  it('uses one selector and one bound scope family and renders only known observations active', () => {
    const { target } = mountPopover();
    expect(authorityHarness.toSpectrumAuthority).toHaveBeenCalledWith(
      runtimeHarness.runtime.state,
      runtimeHarness.runtime.caps,
    );
    expect(binderHarness.bindSemanticSurfaceHandlers).toHaveBeenCalledTimes(1);
    expect(button(target, 'Carrier').classList.contains('active')).toBe(true);
    expect(button(target, 'Narrow').classList.contains('active')).toBe(true);
    expect(button(target, 'Mid').classList.contains('active')).toBe(true);
    expect(button(target, 'On').classList.contains('active')).toBe(true);
    expect(rawSendAlarm).not.toHaveBeenCalled();
  });

  it.each([
    ['Filter', 'onCenterTypeChange', 0],
    ['Wide', 'onVbwChange', false],
    ['Narrow', 'onVbwChange', true],
    ['Narrow', 'onRbwChange', 2],
    ['Off', 'onDuringTxChange', false],
    ['On', 'onDuringTxChange', true],
  ] as const)('%s emits exactly one canonical %s action', (label, name, value) => {
    const { target } = mountPopover();
    const candidates = Array.from(target.querySelectorAll<HTMLButtonElement>('button'))
      .filter((item) => item.textContent?.trim() === label);
    const control = name === 'onRbwChange' ? candidates.at(-1)! : candidates[0];
    control.click();
    flushSync();
    expect(binderHarness.scopeControls[name]).toHaveBeenCalledTimes(1);
    expect(binderHarness.scopeControls[name]).toHaveBeenCalledWith(value);
    expect(radioSpies().reduce((count, spy) => count + spy.mock.calls.length, 0)).toBe(1);
    expect(rawSendAlarm).not.toHaveBeenCalled();
  });

  it.each([
    ['centerType', 'Center Type', 1, 3],
    ['vbwNarrow', 'VBW', true, 'yes'],
    ['rbw', 'RBW', 1, 3],
    ['duringTx', 'During TX', true, 1],
  ] as const)('%s fails closed for unknown, unavailable and malformed readings', (name, family, valid, invalid) => {
    for (const replacement of [
      field(valid, { known: false }),
      field(valid, { structural: false }),
      field(valid, { operational: false }),
      field(invalid as never),
    ]) {
      authorityHarness.current = authority(facts({ [name]: replacement }));
      const { target } = mountPopover();
      for (const control of familyButtons(target, family)) {
        expect(control.disabled).toBe(true);
        expect(control.classList.contains('active')).toBe(false);
        control.click();
      }
      flushSync();
      for (const spy of radioSpies()) expect(spy).not.toHaveBeenCalled();
      expect(rawSendAlarm).not.toHaveBeenCalled();
      unmount(components.pop()!);
      targets.pop()!.remove();
      vi.clearAllMocks();
    }
  });

  it('selector null disables every radio family without fabricating false-like choices', () => {
    authorityHarness.current = null;
    const { target } = mountPopover();
    expect(radioButtons(target)).toHaveLength(10);
    for (const control of radioButtons(target)) {
      expect(control.disabled).toBe(true);
      expect(control.classList.contains('active')).toBe(false);
      control.click();
    }
    flushSync();
    for (const spy of radioSpies()) expect(spy).not.toHaveBeenCalled();
    expect(rawSendAlarm).not.toHaveBeenCalled();
  });

  it('close button, backdrop and keyboard close locally with zero radio action', () => {
    const { target, onClose } = mountPopover();
    button(target, 'x').click();
    const backdrop = target.querySelector<HTMLElement>('.popover-backdrop')!;
    backdrop.click();
    backdrop.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    backdrop.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    backdrop.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    flushSync();
    expect(onClose).toHaveBeenCalledTimes(5);
    for (const spy of radioSpies()) expect(spy).not.toHaveBeenCalled();
    expect(rawSendAlarm).not.toHaveBeenCalled();
  });

  it('source has one canonical fact/action route and no raw Store or transport authority', () => {
    const source = readFileSync(
      resolve(process.cwd(), 'src/components/spectrum/ScopeSettingsPopover.svelte'),
      'utf8',
    );
    expect(source).toContain('toSpectrumAuthority(runtime.state, runtime.caps)');
    expect(source.match(/bindSemanticSurfaceHandlers\(\)/g)).toHaveLength(1);
    expect(source).toContain('bindSemanticSurfaceHandlers().scopeControls');
    expect(source).not.toMatch(/stores\/radio\.svelte|sendCommand|\?\? false/);
  });
});
