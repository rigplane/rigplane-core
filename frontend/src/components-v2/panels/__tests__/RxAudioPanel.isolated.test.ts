import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { buildMonitorOptions, formatMonitorStatus } from '../audio-utils';
import RxAudioPanel from '../RxAudioPanel.svelte';

// ---------------------------------------------------------------------------
// Mock runtime adapters so RxAudioPanel can self-wire in tests
// ---------------------------------------------------------------------------

const mockProps = {
  monitorMode: 'local' as 'local' | 'live' | 'mute',
  afLevel: 0.5,
  hasAfLevel: true,
  hasLiveAudio: false,
  isAudioConnected: true,
  hasDualReceiver: false,
};

const mockHandlers = {
  onMonitorModeChange: vi.fn(),
  onAfLevelChange: vi.fn(),
};

vi.mock('$lib/runtime/adapters/audio-adapter', () => ({
  deriveRxAudioProps: () => mockProps,
  getRxAudioHandlers: () => mockHandlers,
}));

// ---------------------------------------------------------------------------
// buildMonitorOptions
// ---------------------------------------------------------------------------

describe('buildMonitorOptions', () => {
  
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

it('always includes RADIO as first option', () => {
    const options = buildMonitorOptions(false);
    expect(options[0]).toEqual({ value: 'local', label: 'RADIO' });
  });

  it('always includes MUTE as last option when hasLive=false', () => {
    const options = buildMonitorOptions(false);
    expect(options[options.length - 1]).toEqual({ value: 'mute', label: 'MUTE' });
  });

  it('always includes MUTE as last option when hasLive=true', () => {
    const options = buildMonitorOptions(true);
    expect(options[options.length - 1]).toEqual({ value: 'mute', label: 'MUTE' });
  });

  it('excludes LIVE when hasLive=false', () => {
    const options = buildMonitorOptions(false);
    expect(options.find((o) => o.value === 'live')).toBeUndefined();
  });

  it('includes LIVE when hasLive=true', () => {
    const options = buildMonitorOptions(true);
    expect(options.find((o) => o.value === 'live')).toEqual({ value: 'live', label: 'LIVE' });
  });

  it('returns 2 options when hasLive=false', () => {
    expect(buildMonitorOptions(false)).toHaveLength(2);
  });

  it('returns 3 options when hasLive=true', () => {
    expect(buildMonitorOptions(true)).toHaveLength(3);
  });

  it('LIVE appears between RADIO and MUTE when hasLive=true', () => {
    const options = buildMonitorOptions(true);
    expect(options.map((o) => o.value)).toEqual(['local', 'live', 'mute']);
  });

  it('all option values are strings', () => {
    const options = buildMonitorOptions(true);
    options.forEach((o) => expect(typeof o.value).toBe('string'));
  });
});

// ---------------------------------------------------------------------------
// formatMonitorStatus
// ---------------------------------------------------------------------------

describe('formatMonitorStatus', () => {
  it('returns "Radio speaker output" for local', () => {
    expect(formatMonitorStatus('local')).toBe('Radio speaker output');
  });

  it('returns "Browser audio stream" for live', () => {
    expect(formatMonitorStatus('live')).toBe('Browser audio stream');
  });

  it('returns "Audio muted" for mute', () => {
    expect(formatMonitorStatus('mute')).toBe('Audio muted');
  });

  it('returns empty string for unknown mode', () => {
    expect(formatMonitorStatus('unknown')).toBe('');
  });
});

// ---------------------------------------------------------------------------
// RxAudioPanel component
// ---------------------------------------------------------------------------

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(RxAudioPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  mockProps.monitorMode = 'local';
  mockProps.afLevel = 128;
  mockProps.hasAfLevel = true;
  mockProps.hasLiveAudio = false;
  mockProps.isAudioConnected = true;
  mockProps.hasDualReceiver = false;
  mockHandlers.onMonitorModeChange = vi.fn();
  mockHandlers.onAfLevelChange = vi.fn();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('panel visibility', () => {
  it('renders the panel when only radio AF level is available', () => {
    const t = mountPanel({ hasAfLevel: true, hasLiveAudio: false });
    expect(t.querySelector('.panel-body')).not.toBeNull();
  });

  it('renders the panel when hasLiveAudio is true', () => {
    const t = mountPanel({ hasAfLevel: true, hasLiveAudio: true });
    expect(t.querySelector('.panel-body')).not.toBeNull();
  });

  it('does not render the panel when neither AF level nor live audio is available', () => {
    const t = mountPanel({ hasAfLevel: false, hasLiveAudio: false });
    expect(t.querySelector('.panel-body')).toBeNull();
  });
});

describe('panel structure', () => {
  it('renders the AF Level slider', () => {
    const t = mountPanel({ hasAfLevel: true, hasLiveAudio: false });
    const labels = Array.from(t.querySelectorAll('.vc-label'));
    expect(labels.some((el) => el.textContent === 'AF Level')).toBe(true);
  });

  it('renders the output indicator element', () => {
    const t = mountPanel({ hasLiveAudio: true });
    expect(t.querySelector('.output-indicator')).not.toBeNull();
  });

  it('output indicator shows correct status for local mode', () => {
    const t = mountPanel({ hasLiveAudio: true, monitorMode: 'local' });
    expect(t.querySelector('.output-indicator')?.textContent?.trim()).toBe('Radio speaker output');
  });

  it('output indicator shows correct status for mute mode', () => {
    const t = mountPanel({ hasLiveAudio: true, monitorMode: 'mute' });
    expect(t.querySelector('.output-indicator')?.textContent?.trim()).toBe('Audio muted');
  });

  it('output indicator shows correct status for live mode', () => {
    const t = mountPanel({ hasLiveAudio: true, monitorMode: 'live' });
    expect(t.querySelector('.output-indicator')?.textContent?.trim()).toBe('Browser audio stream');
  });
});

describe('monitor mode options', () => {
  it('does not show LIVE button when hasLiveAudio=false', () => {
    const t = mountPanel({hasLiveAudio: false });
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'LIVE')).toBe(false);
  });

  it('shows LIVE button when hasLiveAudio=true', () => {
    const t = mountPanel({hasLiveAudio: true });
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'LIVE')).toBe(true);
  });
});

describe('callbacks', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls onAfLevelChange when AF Level slider changes', () => {
    const t = mountPanel({ hasLiveAudio: true });
    const slider = t.querySelector<HTMLElement>('[role="slider"]');
    slider!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(60);

    expect(mockHandlers.onAfLevelChange).toHaveBeenCalled();
  });

  it('calls onMonitorModeChange when a mode button is clicked', () => {
    const t = mountPanel({ hasLiveAudio: true });
    const buttons = Array.from(t.querySelectorAll('button'));
    const muteBtn = buttons.find((b) => b.textContent?.trim() === 'MUTE');
    muteBtn!.click();
    expect(mockHandlers.onMonitorModeChange).toHaveBeenCalledWith('mute');
  });
});

function vcValueFor(t: HTMLElement, label: string): string {
  const headers = Array.from(t.querySelectorAll('.vc-header'));
  const header = headers.find(
    (h) => h.querySelector('.vc-label')?.textContent === label,
  );
  if (!header) throw new Error(`ValueControl labeled "${label}" not found`);
  return header.querySelector('.vc-value')?.textContent ?? '';
}

/**
 * A12 (MOR-1409, Core #2317, coordinator adjudication comment 5246487510)
 * — a connected receiver that has never reported `afLevel` (optional
 * field, local-monitor path) passes the `hasAfLevel`/`hasLiveAudio`
 * capability gate with `props.afLevel === NaN` (panel-props.ts no longer
 * fabricates `?? 0.5`). Unguarded, `normalizedPercentDisplay(NaN)` renders
 * the literal "NaN%" (verifier-executed probe on the unguarded candidate).
 * The local `formatAfLevelDisplay` guard must render the established
 * '---'-family placeholder instead.
 */
describe('RxAudioPanel — no "NaN" leak for an unobserved AF level (MOR-1409 A12)', () => {
  it('does not render a "NaN" substring for AF Level when afLevel is non-finite', () => {
    const t = mountPanel({ afLevel: Number.NaN, hasAfLevel: true, hasLiveAudio: false });
    expect(vcValueFor(t, 'AF Level')).not.toMatch(/NaN/);
  });

  it('renders the established "---"-family placeholder for a non-finite AF Level', () => {
    const t = mountPanel({ afLevel: Number.NaN, hasAfLevel: true, hasLiveAudio: false });
    expect(vcValueFor(t, 'AF Level')).toBe('--- %');
  });

  it('still renders the real formatted percentage for a finite AF level', () => {
    const t = mountPanel({ afLevel: 0.42, hasAfLevel: true, hasLiveAudio: false });
    expect(vcValueFor(t, 'AF Level')).toBe('42%');
  });
});
