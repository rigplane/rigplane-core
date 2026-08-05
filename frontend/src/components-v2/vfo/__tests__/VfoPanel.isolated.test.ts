import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import VfoPanel from '../VfoPanel.svelte';
import { formatBadges, formatRitOffset } from '../vfo-utils';

// ---------------------------------------------------------------------------
// formatBadges
// ---------------------------------------------------------------------------

describe('formatBadges', () => {
  beforeEach(() => {
    // Mock getComputedStyle to return badge colors from CSS custom properties
    const mockGetPropertyValue = vi.fn((prop: string) => {
      const badgeColors: Record<string, string> = {
        '--v2-badge-atu-color': 'green',
        '--v2-badge-notch-color': 'orange',
        '--v2-badge-nr-color': 'cyan',
        '--v2-badge-pre-color': 'cyan',
        '--v2-badge-nb-color': 'cyan',
        '--v2-badge-default-color': 'cyan',
        '--v2-receiver-main-accent': 'cyan',
        '--v2-receiver-sub-accent': 'white',
      };
      return badgeColors[prop] || '';
    });

    globalThis.getComputedStyle = vi.fn(() => ({
      getPropertyValue: mockGetPropertyValue,
    })) as any;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });
  it('returns empty array for empty input', () => {
    expect(formatBadges({})).toEqual([]);
  });

  it('boolean true → label = uppercased key, active = true', () => {
    const result = formatBadges({ nr: true });
    expect(result).toEqual([{ label: 'NR', active: true, color: 'cyan' }]);
  });

  it('boolean false → label = uppercased key, active = false', () => {
    const result = formatBadges({ nb: false });
    expect(result).toEqual([{ label: 'NB', active: false, color: 'cyan' }]);
  });

  it('string value → label = value itself, active = true', () => {
    const result = formatBadges({ pre: 'P1' });
    expect(result).toEqual([{ label: 'P1', active: true, color: 'cyan' }]);
  });

  it('notch string value uses orange color', () => {
    const result = formatBadges({ notch: 'AUTO' });
    expect(result[0].color).toBe('orange');
  });

  it('atu key uses green color', () => {
    const result = formatBadges({ atu: true });
    expect(result[0].color).toBe('green');
  });

  it('handles mixed badges record', () => {
    const result = formatBadges({ atu: true, pre: 'P1', nr: true, nb: false, notch: 'AUTO' });
    expect(result).toHaveLength(5);
    expect(result.find((b) => b.label === 'ATU')?.active).toBe(true);
    expect(result.find((b) => b.label === 'P1')?.active).toBe(true);
    expect(result.find((b) => b.label === 'NB')?.active).toBe(false);
    expect(result.find((b) => b.label === 'AUTO')?.active).toBe(true);
  });

  it('unknown key defaults to cyan color', () => {
    const result = formatBadges({ foo: true });
    expect(result[0].color).toBe('cyan');
  });

  it('unknown key falls back to the SUB receiver accent when no badge token exists', () => {
    const result = formatBadges({ foo: true }, 'sub');
    expect(result[0].color).toBe('white');
  });
});

// ---------------------------------------------------------------------------
// formatRitOffset
// ---------------------------------------------------------------------------

describe('formatRitOffset', () => {
  it('positive offset shows + sign in kHz', () => {
    expect(formatRitOffset(120)).toBe('+0.12 kHz');
  });

  it('negative offset shows − sign in kHz', () => {
    expect(formatRitOffset(-250)).toBe('−0.25 kHz');
  });

  it('zero offset shows + sign in kHz', () => {
    expect(formatRitOffset(0)).toBe('+0.00 kHz');
  });

  it('formats a multi-kHz offset with 2 decimals', () => {
    expect(formatRitOffset(5000)).toBe('+5.00 kHz');
  });
});

// ---------------------------------------------------------------------------
// VfoPanel component
// ---------------------------------------------------------------------------

vi.mock('$lib/stores/capabilities.svelte', () => ({
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  vfoSlotLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'VFO A' : 'VFO B')),
  getCapabilities: vi.fn(() => ({
    freqRanges: [
      {
        start: 14000000,
        end: 14350000,
        bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14074000 }],
      },
      {
        start: 7000000,
        end: 7300000,
        bands: [{ name: '40m', start: 7000000, end: 7300000, default: 7074000 }],
      },
    ],
  })),
  hasDualReceiver: vi.fn(() => true),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
}));

import { getCapabilities, receiverLabel, vfoSlotLabel } from '$lib/stores/capabilities.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(props: ComponentProps<typeof VfoPanel>) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(VfoPanel, { target: t, props });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  vi.mocked(receiverLabel).mockImplementation((id: 'MAIN' | 'SUB') => id);
  vi.mocked(vfoSlotLabel).mockImplementation((slot: 'A' | 'B') => (slot === 'A' ? 'VFO A' : 'VFO B'));
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

const baseProps: ComponentProps<typeof VfoPanel> = {
  receiver: 'main',
  freq: 14074000,
  mode: 'USB',
  filter: '2.4k',
  sValue: 100,
  isActive: true,
  badges: {},
  onModeClick: vi.fn(),
  onVfoClick: vi.fn(),
};

describe('panel structure', () => {
  it('renders the VFO label MAIN for receiver=main', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.vfo-label')?.textContent?.trim()).toBe('MAIN');
  });

  it('renders the VFO label SUB for receiver=sub', () => {
    const t = mountPanel({ ...baseProps, receiver: 'sub' });
    expect(t.querySelector('.vfo-label')?.textContent?.trim()).toBe('SUB');
  });

  it('renders mode badge with correct mode text', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.mode-badge-wrapper .v2-status-indicator')?.textContent?.trim()).toBe('USB');
  });

  it('renders filter badge with correct filter text', () => {
    const t = mountPanel(baseProps);
    const indicators = Array.from(t.querySelectorAll('.control-strip .v2-status-indicator'));
    expect(indicators.some((el) => el.textContent?.trim() === '2.4k')).toBe(true);
  });

  it('renders a .freq element (FrequencyDisplay)', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.freq')).not.toBeNull();
  });

  it('renders the S-meter svg', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('svg')).not.toBeNull();
  });

  it('renders the active band label from capabilities', () => {
    const t = mountPanel(baseProps);
    const indicators = Array.from(t.querySelectorAll('.control-strip .v2-status-indicator'));
    expect(indicators.some((el) => el.textContent?.trim() === '20m')).toBe(true);
  });

  it('renders BAR and slot tags in the header', () => {
    const t = mountPanel(baseProps);
    const tags = Array.from(t.querySelectorAll('.header-tag')).map((node) => node.textContent?.trim());
    expect(tags).toContain('BAR');
    expect(tags).toContain('A');
  });

  it('renders the control strip even when badges are empty', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.control-strip')).not.toBeNull();
  });

  it('renders slot tag inside the control strip', () => {
    const t = mountPanel(baseProps);
    const indicators = Array.from(t.querySelectorAll('.control-strip .v2-status-indicator'));
    const slotIndicator = indicators.find((el) => el.getAttribute('data-color') === 'muted');
    expect(slotIndicator?.textContent?.trim()).toBe('A');
  });
});

describe('active/inactive state', () => {
  it('panel has active class when isActive=true', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.panel')?.classList.contains('active')).toBe(true);
  });

  it('panel does not have active class when isActive=false', () => {
    const t = mountPanel({ ...baseProps, isActive: false });
    expect(t.querySelector('.panel')?.classList.contains('active')).toBe(false);
  });

  it('FrequencyDisplay has inactive class when isActive=false', () => {
    const t = mountPanel({ ...baseProps, isActive: false });
    expect(t.querySelector('.freq')?.classList.contains('inactive')).toBe(true);
  });

  it('FrequencyDisplay does not have inactive class when isActive=true', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.freq')?.classList.contains('inactive')).toBe(false);
  });

  it('MAIN panel exposes cyan receiver accent vars for control chrome', () => {
    const t = mountPanel(baseProps);
    const style = t.querySelector('.panel')?.getAttribute('style') ?? '';
    expect(style).toContain('--receiver-accent: var(--v2-receiver-main-accent)');
    expect(style).toContain('--receiver-control-border: var(--v2-vfo-main-control-border)');
  });

  it('SUB panel exposes white receiver accent vars for control chrome', () => {
    const t = mountPanel({ ...baseProps, receiver: 'sub' });
    const style = t.querySelector('.panel')?.getAttribute('style') ?? '';
    expect(style).toContain('--receiver-accent: var(--v2-receiver-sub-accent)');
    expect(style).toContain('--receiver-control-border: var(--v2-vfo-sub-control-border)');
  });
});

describe('RIT display', () => {
  it('does not show rit-row when rit is undefined', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.rit-row')).toBeNull();
  });

  it('does not show rit-row when rit.active=false', () => {
    const t = mountPanel({ ...baseProps, rit: { active: false, offset: 120 } });
    expect(t.querySelector('.rit-row')).toBeNull();
  });

  it('shows rit-row when rit.active=true', () => {
    const t = mountPanel({ ...baseProps, rit: { active: true, offset: 120 } });
    expect(t.querySelector('.rit-row')).not.toBeNull();
  });

  it('shows formatted RIT offset in kHz when active', () => {
    const t = mountPanel({ ...baseProps, rit: { active: true, offset: 120 } });
    expect(t.querySelector('.rit-offset')?.textContent?.trim()).toBe('+0.12 kHz');
  });

  it('shows negative RIT offset in kHz correctly', () => {
    const t = mountPanel({ ...baseProps, rit: { active: true, offset: -250 } });
    expect(t.querySelector('.rit-offset')?.textContent?.trim()).toBe('−0.25 kHz');
  });
});

describe('badge rendering', () => {
  it('does not render extra badge indicators when badges is empty', () => {
    const t = mountPanel(baseProps);
    // With empty badges, the control strip only has fixed indicators: mode, slot, band, filter
    // The badge items loop ({#each badgeItems}) renders nothing when badges prop is {}
    const indicators = t.querySelectorAll('.v2-status-indicator');
    // mode (USB) + slot (MAIN, muted) + activeBand (20m) + filter (2.4k) = 4
    expect(indicators.length).toBe(4);
  });

  it('renders badge-row when badges has entries', () => {
    const t = mountPanel({ ...baseProps, badges: { nr: true } });
    expect(t.querySelector('.control-strip')).not.toBeNull();
    expect(t.querySelectorAll('.v2-status-indicator').length).toBeGreaterThan(0);
  });

  it('renders ATU badge when atu=true in badges', () => {
    const t = mountPanel({ ...baseProps, badges: { atu: true } });
    const badges = Array.from(t.querySelectorAll('.v2-status-indicator'));
    expect(badges.some((el) => el.textContent?.trim() === 'ATU')).toBe(true);
  });

  it('renders NB badge as inactive when nb=false', () => {
    const t = mountPanel({ ...baseProps, badges: { nb: false } });
    const badge = t.querySelector('.v2-status-indicator[data-active="false"]');
    expect(badge).not.toBeNull();
  });

  it('renders string badge value as label (pre="P1")', () => {
    const t = mountPanel({ ...baseProps, badges: { pre: 'P1' } });
    const badges = Array.from(t.querySelectorAll('.v2-status-indicator'));
    expect(badges.some((el) => el.textContent?.trim() === 'P1')).toBe(true);
  });
});

describe('callbacks', () => {
  it('does not call onVfoClick when panel is clicked (panel-wide activation removed)', () => {
    const onVfoClick = vi.fn();
    const t = mountPanel({ ...baseProps, onVfoClick });
    t.querySelector<HTMLElement>('.panel')?.click();
    expect(onVfoClick).not.toHaveBeenCalled();
  });

  it('calls onModeClick when mode badge is clicked', () => {
    const onModeClick = vi.fn();
    const t = mountPanel({ ...baseProps, onModeClick });
    t.querySelector<HTMLElement>('.mode-badge-wrapper')?.click();
    expect(onModeClick).toHaveBeenCalledOnce();
  });
});

describe('receiverLabel / vfoSlotLabel integration', () => {
  it('uses receiverLabel("MAIN") for receiver=main', () => {
    mountPanel({ ...baseProps, receiver: 'main' });
    expect(vi.mocked(receiverLabel)).toHaveBeenCalledWith('MAIN');
  });

  it('uses receiverLabel("SUB") for receiver=sub', () => {
    mountPanel({ ...baseProps, receiver: 'sub' });
    expect(vi.mocked(receiverLabel)).toHaveBeenCalledWith('SUB');
  });

  it('uses vfoSlotLabel("A") for receiver=main', () => {
    mountPanel({ ...baseProps, receiver: 'main' });
    expect(vi.mocked(vfoSlotLabel)).toHaveBeenCalledWith('A');
  });

  it('uses vfoSlotLabel("B") for receiver=sub', () => {
    mountPanel({ ...baseProps, receiver: 'sub' });
    expect(vi.mocked(vfoSlotLabel)).toHaveBeenCalledWith('B');
  });

  it('renders the receiver label in the header', () => {
    vi.mocked(receiverLabel).mockReturnValue('MAIN');
    const t = mountPanel({ ...baseProps, receiver: 'main' });
    expect(t.querySelector('.vfo-label')?.textContent?.trim()).toBe('MAIN');
  });

  it('reads band ranges through getCapabilities()', () => {
    mountPanel(baseProps);
    expect(vi.mocked(getCapabilities)).toHaveBeenCalled();
  });
});
