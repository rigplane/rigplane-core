/**
 * Component-level tests for BandPlanOverlay.svelte.
 * Mounts the real component via svelte.mount() and verifies DOM output.
 *
 * Region-aware cases are driven by `fixtures/band-plan-responses.json`: the
 * bodies the production server returns, recorded by the Python real-handler
 * replay `tests/test_band_plan_response_goldens.py` (which keeps the file equal
 * to the handlers' output). The production toolbar -> panel -> overlay wiring is
 * covered in SpectrumPanel.component.test.ts with the same file.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import BandPlanOverlay from '../BandPlanOverlay.svelte';
import BandPlanRegionHarness, { harnessRegion } from './BandPlanRegionHarness.svelte';
import responseGoldens from './fixtures/band-plan-responses.json';

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

interface GoldenSegment {
  start: number;
  end: number;
  label: string;
  notes?: string | null;
}
type GoldenBody = { segments: GoldenSegment[]; plansLoaded: boolean };
const goldenSegments = responseGoldens.segments as unknown as Record<string, GoldenBody>;

// The overlay fetches the viewport +/- 50 %: 145.800-146.200 MHz asks for this window.
const R1_WINDOW = '145600000-146400000';
const goldenBody = (region: string, window = R1_WINDOW): GoldenBody => {
  const body = goldenSegments[`${region}|${window}`];
  if (!body) throw new Error(`no golden for ${region}|${window}`);
  return body;
};
const ok = (body: unknown) => ({ ok: true, json: async () => body }) as Response;
/** Titles the overlay shows for a body in a viewport (title = notes ?? label). */
const expectedTitles = (body: GoldenBody, start: number, end: number) =>
  body.segments.filter((s) => s.end > start && s.start < end).map((s) => s.notes ?? s.label);
const shownTitles = (target: HTMLElement) =>
  Array.from(target.querySelectorAll('.band-segment')).map((s) => s.getAttribute('title'));
const segmentCalls = () =>
  mockFetch.mock.calls.filter(([input]) => String(input).startsWith('/api/v1/band-plan/segments'));

function mountRegionHarness(region: string) {
  harnessRegion.value = region;
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(BandPlanRegionHarness, { target, props: {} });
  flushSync();
  cleanups.push(() => {
    unmount(component);
    target.remove();
  });
  return target;
}

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
  // Default: the install has no band-plan data (plansLoaded=false), which the
  // overlay treats as "unavailable", so the local fallback is used.
  mockFetch.mockResolvedValue(ok({ segments: [], plansLoaded: false }));
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

  it('fetches a fixed visible range exactly once instead of looping (MOR-2605)', async () => {
    mountOverlay({
      startFreq: 14_000_000,
      endFreq: 14_350_000,
    });

    // Well past several 100 ms debounce periods with no prop change.
    await vi.advanceTimersByTimeAsync(1500);
    flushSync();

    const bandPlanCalls = mockFetch.mock.calls.filter(([input]) =>
      String(input).startsWith('/api/v1/band-plan/segments'),
    );
    expect(bandPlanCalls).toHaveLength(1);
    expect(bandPlanCalls[0][0]).toBe(
      '/api/v1/band-plan/segments?start=13825000&end=14525000',
    );
  });

  it('refetches at an unchanged viewport when the region switches', async () => {
    mockFetch.mockImplementation(async () => ok(goldenBody(harnessRegion.value)));
    const target = mountRegionHarness('US');
    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    const us = goldenBody('US');
    const r1 = goldenBody('IARU-R1');
    expect(shownTitles(target)).toEqual(expectedTitles(us, 145_800_000, 146_200_000));
    expect(segmentCalls()).toHaveLength(1);
    const firstUrl = String(segmentCalls()[0][0]);

    // Switch regions without touching the viewport.
    harnessRegion.value = 'IARU-R1';
    flushSync();
    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    // Same viewport (same URL) but a second fetch: the cached US range is invalid.
    expect(segmentCalls()).toHaveLength(2);
    expect(String(segmentCalls()[1][0])).toBe(firstUrl);
    expect(shownTitles(target)).toEqual(expectedTitles(r1, 145_800_000, 146_200_000));
    expect(shownTitles(target)).not.toEqual(expectedTitles(us, 145_800_000, 146_200_000));

    // No fetch loop afterwards: the new region's range now contains the viewport.
    await vi.advanceTimersByTimeAsync(1500);
    flushSync();
    expect(segmentCalls()).toHaveLength(2);
  });

  it('drops a superseded region response that completes after the newer one', async () => {
    const pending: Array<(r: Response) => void> = [];
    mockFetch.mockImplementation(() => new Promise<Response>((resolve) => pending.push(resolve)));
    const target = mountRegionHarness('US');
    await vi.advanceTimersByTimeAsync(150); // request #1 (US) now in flight
    expect(pending).toHaveLength(1);

    harnessRegion.value = 'IARU-R1';
    flushSync();
    await vi.advanceTimersByTimeAsync(150); // request #2 (R1) in flight
    expect(pending).toHaveLength(2);

    // The R1 answer arrives first, the older US answer completes afterwards.
    pending[1](ok(goldenBody('IARU-R1')));
    await vi.advanceTimersByTimeAsync(0);
    flushSync();
    const r1Titles = expectedTitles(goldenBody('IARU-R1'), 145_800_000, 146_200_000);
    expect(shownTitles(target)).toEqual(r1Titles);

    pending[0](ok(goldenBody('US')));
    await vi.advanceTimersByTimeAsync(0);
    flushSync();
    expect(shownTitles(target)).toEqual(r1Titles);

    // ... and the stale answer did not poison the cache: no refetch follows.
    await vi.advanceTimersByTimeAsync(1500);
    flushSync();
    expect(pending).toHaveLength(2);
    expect(shownTitles(target)).toEqual(r1Titles);
  });

  it('drops a superseded response when the region switches while it is in flight', async () => {
    const pending: Array<(r: Response) => void> = [];
    mockFetch.mockImplementation(() => new Promise<Response>((resolve) => pending.push(resolve)));
    const target = mountRegionHarness('US');
    await vi.advanceTimersByTimeAsync(150);
    harnessRegion.value = 'IARU-R1';
    flushSync();

    // The US answer lands inside the debounce window before the R1 request exists.
    // Nothing of it may be shown (the local copy holds no VHF data, so: empty).
    pending[0](ok(goldenBody('US')));
    await vi.advanceTimersByTimeAsync(0);
    flushSync();
    expect(shownTitles(target)).toEqual([]);
  });

  it('does not keep showing the previous region when its replacement fails', async () => {
    mockFetch.mockImplementation(async () =>
      harnessRegion.value === 'US' ? ok(goldenBody('US')) : ({ ok: false, status: 503 } as Response),
    );
    const target = mountRegionHarness('US');
    await vi.advanceTimersByTimeAsync(150);
    flushSync();
    expect(shownTitles(target)).toEqual(expectedTitles(goldenBody('US'), 145_800_000, 146_200_000));

    harnessRegion.value = 'IARU-R1';
    flushSync();
    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    // Failed refresh: none of the US segments stays (the local copy holds no
    // VHF data, so nothing is shown for the unanswered region).
    expect(shownTitles(target)).toEqual([]);
  });

  it('renders an authoritative empty answer as empty instead of the local copy', async () => {
    mockFetch.mockResolvedValue(ok(goldenBody('IARU-R3', '5375000-5395000')));
    const { target } = mountOverlay({
      startFreq: 5_380_000,
      endFreq: 5_390_000,
      region: 'IARU-R3',
    });
    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    expect(String(segmentCalls()[0][0])).toBe(
      '/api/v1/band-plan/segments?start=5375000&end=5395000',
    );
    expect(goldenBody('IARU-R3', '5375000-5395000').segments).toEqual([]);
    expect(target.querySelectorAll('.band-segment')).toHaveLength(0);
    expect(target.querySelector('.bandplan-overlay')).toBeNull();
  });

  it('uses the local copy when the install has no band-plan data', async () => {
    mockFetch.mockResolvedValue(ok(goldenBody('unloaded', '13825000-14525000')));
    const { target } = mountOverlay({ startFreq: 14_000_000, endFreq: 14_350_000 });
    await vi.advanceTimersByTimeAsync(150);
    flushSync();

    expect(shownTitles(target)).toEqual(['CW', 'DIGI', 'PHONE']);
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
