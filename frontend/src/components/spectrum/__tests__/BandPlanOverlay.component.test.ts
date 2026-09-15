/**
 * Component-level tests for BandPlanOverlay.svelte.
 * Mounts the real component via svelte.mount() and verifies DOM output.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import BandPlanOverlay from '../BandPlanOverlay.svelte';

// ── Mock arrl-band-plan (local fallback data) ──

const MOCK_LOCAL_SEGMENTS = [
  {
    segment: { startHz: 14_000_000, endHz: 14_070_000, mode: 'cw', label: 'CW' },
    band: '20m',
  },
  {
    segment: { startHz: 14_070_000, endHz: 14_150_000, mode: 'digital', label: 'DIGI' },
    band: '20m',
  },
  {
    segment: { startHz: 14_150_000, endHz: 14_350_000, mode: 'phone', label: 'PHONE' },
    band: '20m',
  },
];

vi.mock('../../lib/data/arrl-band-plan', () => ({
  getVisibleSegments: vi.fn(() => MOCK_LOCAL_SEGMENTS),
  SEGMENT_COLORS: {
    cw: 'rgba(255,106,0,0.20)',
    digital: 'rgba(74,222,128,0.20)',
    phone: 'rgba(96,165,250,0.20)',
    beacon: 'rgba(250,204,21,0.20)',
    broadcast: 'rgba(192,132,252,0.20)',
  },
  SEGMENT_BORDER_COLORS: {
    cw: 'rgba(255,106,0,0.5)',
    digital: 'rgba(74,222,128,0.5)',
    phone: 'rgba(96,165,250,0.5)',
    beacon: 'rgba(250,204,21,0.5)',
    broadcast: 'rgba(192,132,252,0.5)',
  },
  SEGMENT_LABEL_COLORS: {
    cw: 'rgb(255,150,50)',
    digital: 'rgb(74,222,128)',
    phone: 'rgb(96,165,250)',
    beacon: 'rgb(250,204,21)',
    broadcast: 'rgb(192,132,252)',
  },
}));

// ── Polyfill ResizeObserver (jsdom lacks it; Svelte uses it for bind:clientWidth) ──

class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = FakeResizeObserver as any;

// ── Mock fetch ──

const mockFetch = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>();
globalThis.fetch = mockFetch as any;

// ── Helpers ──

type OverlayProps = ComponentProps<typeof BandPlanOverlay>;

let cleanups: (() => void)[] = [];

function mountOverlay(props: OverlayProps) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(BandPlanOverlay, { target, props });
  flushSync();
  cleanups.push(() => {
    unmount(component);
    target.remove();
  });
  return { component, target };
}

// ── Setup / teardown ──

beforeEach(() => {
  cleanups = [];
  localStorage.clear();
  vi.useFakeTimers();
  mockFetch.mockReset();
  // Default: fetch returns empty segments so local fallback is used
  mockFetch.mockResolvedValue({
    ok: true,
    json: async () => ({ segments: [] }),
  } as Response);
});

afterEach(() => {
  cleanups.forEach((fn) => fn());
  document.body.innerHTML = '';
  vi.useRealTimers();
  vi.restoreAllMocks();
  localStorage.clear();
});

// ── Tests ──

describe('BandPlanOverlay (component)', () => {
  it('mounts without errors with basic props', () => {
    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
    });
    expect(target).toBeTruthy();
  });

  it('renders container .bandplan-overlay', () => {
    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
    });
    const overlay = target.querySelector('.bandplan-overlay');
    expect(overlay).not.toBeNull();
  });

  it('renders segment divs from local fallback data', () => {
    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
    });
    const segments = target.querySelectorAll('.band-segment');
    expect(segments.length).toBe(3);
  });

  it('segment divs have correct title attributes', () => {
    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
    });
    const segments = target.querySelectorAll('.band-segment');
    const titles = Array.from(segments).map((s) => s.getAttribute('title'));
    expect(titles).toContain('CW');
    expect(titles).toContain('DIGI');
    expect(titles).toContain('PHONE');
  });

  it('renders nothing when visible=false', () => {
    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
      visible: false,
    });
    const overlay = target.querySelector('.bandplan-overlay');
    expect(overlay).toBeNull();
  });

  it('renders nothing when endFreq <= startFreq', () => {
    const { target } = mountOverlay({
      startFreq: 14_350_000,
      endFreq: 14_000_000,
    });
    const overlay = target.querySelector('.bandplan-overlay');
    expect(overlay).toBeNull();
  });

  it('renders remote segments when fetch returns data', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        segments: [
          {
            start: 14_000_000, end: 14_100_000, mode: 'cw', label: 'CW-R',
            color: '#FF6A00', opacity: 0.25, band: '20m', layer: 'ham', priority: 1,
          },
          {
            start: 14_100_000, end: 14_350_000, mode: 'phone', label: 'SSB-R',
            color: '#60A5FA', opacity: 0.25, band: '20m', layer: 'ham', priority: 1,
          },
        ],
      }),
    } as Response);

    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
    });

    // Advance past debounce timeout (100ms)
    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    const segments = target.querySelectorAll('.band-segment');
    expect(segments.length).toBe(2);
    const titles = Array.from(segments).map((s) => s.getAttribute('title'));
    expect(titles).toContain('CW-R');
    expect(titles).toContain('SSB-R');
  });

  it('ignores retired tokens when the debounced request runs and keeps 401 fallback behavior', async () => {
    localStorage.setItem('rigplane-auth-token', 'stale-token');
    mockFetch.mockResolvedValue({ ok: false, status: 401 } as Response);
    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
    });
    localStorage.setItem('rigplane-auth-token', 'current-token');

    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/band-plan/segments?start=13825000&end=14525000',
      { headers: {} },
    );
    expect(target.querySelectorAll('.band-segment')).toHaveLength(3);
  });

  it('hidden layers are filtered out', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        segments: [
          {
            start: 14_000_000, end: 14_350_000, mode: 'cw', label: 'HAM',
            color: '#FF6A00', opacity: 0.2, band: '20m', layer: 'ham', priority: 1,
          },
          {
            start: 14_000_000, end: 14_200_000, mode: 'broadcast', label: 'BC',
            color: '#C084FC', opacity: 0.2, band: 'BC', layer: 'eibi', priority: 5,
          },
        ],
      }),
    } as Response);

    const { target } = mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
      hiddenLayers: ['eibi'],
    });

    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    const segments = target.querySelectorAll('.band-segment');
    expect(segments.length).toBe(1);
  });

  it('unmounts cleanly', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(BandPlanOverlay, {
      target,
      props: { startFreq: 14_000_000, endFreq: 14_350_000 },
    });
    flushSync();

    expect(() => {
      unmount(component);
      target.remove();
    }).not.toThrow();
  });
});
