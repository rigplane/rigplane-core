/**
 * ModInputTxWarning component (MOR-617).
 *
 * Presentation-only banner for the MOD-input TX preflight guard:
 * renders nothing while the guard is idle, shows the warning with the
 * offending source label, and wires the one-click "Set LAN" and dismiss
 * actions to the runtime adapter handlers.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const mockProps = {
  visible: false,
  sourceLabel: null as string | null,
};

const mockHandlers = {
  onSetLan: vi.fn(),
  onDismiss: vi.fn(),
};

vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ ...mockProps }),
  getModInputTxGuardHandlers: () => mockHandlers,
}));

import ModInputTxWarning from '../ModInputTxWarning.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountWarning(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(ModInputTxWarning, { target });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  mockProps.visible = false;
  mockProps.sourceLabel = null;
  mockHandlers.onSetLan = vi.fn();
  mockHandlers.onDismiss = vi.fn();
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

describe('ModInputTxWarning', () => {
  it('renders nothing while the guard is idle', () => {
    const el = mountWarning();
    expect(el.querySelector('[data-testid="mod-input-tx-warning"]')).toBeNull();
  });

  it('shows the warning with the offending source label when armed', () => {
    const el = mountWarning({ visible: true, sourceLabel: 'MIC' });
    const banner = el.querySelector('[data-testid="mod-input-tx-warning"]');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain('MIC');
    expect(banner?.getAttribute('role')).toBe('alert');
  });

  it('one-click Set LAN calls the adapter handler', () => {
    const el = mountWarning({ visible: true, sourceLabel: 'MIC' });
    const button = el.querySelector<HTMLButtonElement>('[data-testid="mod-input-set-lan"]');
    expect(button).not.toBeNull();
    button?.click();
    flushSync();
    expect(mockHandlers.onSetLan).toHaveBeenCalledTimes(1);
    expect(mockHandlers.onDismiss).not.toHaveBeenCalled();
  });

  it('dismiss calls the adapter handler', () => {
    const el = mountWarning({ visible: true, sourceLabel: 'ACC' });
    const button = el.querySelector<HTMLButtonElement>('[data-testid="mod-input-dismiss"]');
    expect(button).not.toBeNull();
    button?.click();
    flushSync();
    expect(mockHandlers.onDismiss).toHaveBeenCalledTimes(1);
    expect(mockHandlers.onSetLan).not.toHaveBeenCalled();
  });
});
