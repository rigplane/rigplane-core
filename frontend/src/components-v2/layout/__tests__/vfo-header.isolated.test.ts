/**
 * Tests for the SCOPE status block in VfoHeader bridge (issue #832).
 *
 * The block renders only when `scopeStatus` prop is non-null, shows the
 * MAIN/SUB source pills + DUAL toggle only when dual receiver is available,
 * and always shows the SPAN/SPEED read-only digest.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

// ── Mocks ──────────────────────────────────────────────────────────────────
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasDualReceiver: vi.fn(() => true),
  hasTx: vi.fn(() => true),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => false),
  hasCapability: vi.fn(() => false),
  getScopeSource: vi.fn(() => null),
  vfoLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'MAIN' : 'SUB')),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  vfoSlotLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'VFO A' : 'VFO B')),
  getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
  getVfoScheme: vi.fn(() => 'main_sub'),
  getAgcModes: vi.fn(() => [0, 1, 2, 3]),
  getAgcLabels: vi.fn(() => ({ 0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW' })),
  getSupportedModes: vi.fn(() => ['USB', 'LSB', 'CW', 'AM', 'FM']),
  getSupportedFilters: vi.fn(() => ['FIL1', 'FIL2', 'FIL3']),
  getAttValues: vi.fn(() => [0, 10, 20]),
  getAttLabels: vi.fn(() => ({ 0: '0dB', 10: '10dB', 20: '20dB' })),
  getPreValues: vi.fn(() => [0, 1, 2]),
  getPreLabels: vi.fn(() => ({ 0: 'OFF', 1: 'PRE1', 2: 'PRE2' })),
  getAntennaCount: vi.fn(() => 1),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

import VfoHeader, { type ScopeStatusProps } from '../VfoHeader.svelte';
import { hasDualReceiver } from '$lib/stores/capabilities.svelte';
import type { VfoStateProps } from '../layout-utils';

const mainVfo: VfoStateProps = {
  receiver: 'main',
  freq: 14074000,
  mode: 'USB',
  filter: 'FIL1',
  sValue: 0,
  isActive: true,
  badges: {},
};

const subVfo: VfoStateProps = {
  receiver: 'sub',
  freq: 7074000,
  mode: 'LSB',
  filter: 'FIL1',
  sValue: 0,
  isActive: false,
  badges: {},
};

let components: ReturnType<typeof mount>[] = [];
const mountedTargets: HTMLElement[] = [];

function mountHeader(props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mountedTargets.push(target);
  const component = mount(VfoHeader, {
    target,
    props: {
      mainVfo,
      subVfo,
      splitActive: false,
      dualWatchActive: false,
      txVfo: 'main',
      ...props,
    },
  });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  mountedTargets.length = 0;
  vi.clearAllMocks();
  vi.mocked(hasDualReceiver).mockReturnValue(true);
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  for (const t of mountedTargets) {
    t.remove();
  }
  mountedTargets.length = 0;
});

describe('VfoHeader SCOPE status block', () => {
  it('hides the block when scopeStatus is null', () => {
    const target = mountHeader({ scopeStatus: null });
    expect(target.querySelector('[data-testid="scope-status"]')).toBeNull();
  });

  it('renders SCOPE title when scopeStatus is provided', () => {
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus });
    const block = target.querySelector('[data-testid="scope-status"]');
    expect(block).not.toBeNull();
    expect(block!.textContent).toContain('SCOPE');
  });

  it('renders MAIN and SUB pills when dual receiver', () => {
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus });
    const pills = Array.from(target.querySelectorAll<HTMLButtonElement>('.scope-pill'));
    const labels = pills.map((p) => p.textContent?.trim());
    expect(labels).toEqual(['MAIN', 'SUB']);
  });

  it('marks the active scope source pill as active (receiver=0 → MAIN)', () => {
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus });
    const pills = Array.from(target.querySelectorAll<HTMLButtonElement>('.scope-pill'));
    expect(pills[0].classList.contains('active')).toBe(true);
    expect(pills[1].classList.contains('active')).toBe(false);
  });

  it('marks SUB pill active when receiver=1', () => {
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 1, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus });
    const pills = Array.from(target.querySelectorAll<HTMLButtonElement>('.scope-pill'));
    expect(pills[0].classList.contains('active')).toBe(false);
    expect(pills[1].classList.contains('active')).toBe(true);
  });

  it('renders DUAL toggle with active state when scopeStatus.dual is true', () => {
    const scopeStatus: ScopeStatusProps = { dual: true, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus });
    const dualBtn = target.querySelector<HTMLButtonElement>('.scope-dual');
    expect(dualBtn).not.toBeNull();
    expect(dualBtn!.textContent?.trim()).toBe('DUAL');
    expect(dualBtn!.classList.contains('active')).toBe(true);
  });

  it('hides MAIN/SUB pills and DUAL toggle when dual receiver is not available', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(false);
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus });
    expect(target.querySelector('.scope-pill')).toBeNull();
    expect(target.querySelector('.scope-dual')).toBeNull();
    // Digest still present
    expect(target.querySelector('.scope-digest')).not.toBeNull();
  });

  it('renders SPAN/SPEED digest from scopeStatus', () => {
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus });
    const digest = target.querySelector('.scope-digest');
    expect(digest).not.toBeNull();
    const text = digest!.textContent!.replace(/\s+/g, ' ').trim();
    expect(text).toBe('\u00b125k MID');
  });

  it('renders correct digest values for different span/speed indices', () => {
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 5, speed: 0 };
    const target = mountHeader({ scopeStatus });
    const digest = target.querySelector('.scope-digest');
    const text = digest!.textContent!.replace(/\s+/g, ' ').trim();
    expect(text).toBe('\u00b1100k FST');
  });

  it('falls back to default digest labels for out-of-range indices', () => {
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 99, speed: 99 };
    const target = mountHeader({ scopeStatus });
    const digest = target.querySelector('.scope-digest');
    const text = digest!.textContent!.replace(/\s+/g, ' ').trim();
    expect(text).toBe('\u00b125k MID');
  });

  it('invokes onScopeReceiverChange with 1 when SUB pill is clicked', () => {
    const onScopeReceiverChange = vi.fn();
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus, onScopeReceiverChange });
    const pills = Array.from(target.querySelectorAll<HTMLButtonElement>('.scope-pill'));
    pills[1].click();
    flushSync();
    expect(onScopeReceiverChange).toHaveBeenCalledWith(1);
  });

  it('invokes onScopeReceiverChange with 0 when MAIN pill is clicked', () => {
    const onScopeReceiverChange = vi.fn();
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 1, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus, onScopeReceiverChange });
    const pills = Array.from(target.querySelectorAll<HTMLButtonElement>('.scope-pill'));
    pills[0].click();
    flushSync();
    expect(onScopeReceiverChange).toHaveBeenCalledWith(0);
  });

  it('invokes onScopeDualToggle when DUAL is clicked', () => {
    const onScopeDualToggle = vi.fn();
    const scopeStatus: ScopeStatusProps = { dual: false, receiver: 0, span: 3, speed: 1 };
    const target = mountHeader({ scopeStatus, onScopeDualToggle });
    const dualBtn = target.querySelector<HTMLButtonElement>('.scope-dual');
    dualBtn!.click();
    flushSync();
    expect(onScopeDualToggle).toHaveBeenCalledTimes(1);
  });
});
