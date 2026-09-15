/**
 * Tests for click-to-tune target filtering in SpectrumPanel.
 *
 * Legacy waterfall-only target-filter fixture: this LOCAL helper models
 * only which targets the waterfall tap accepts. The spectrum area's own
 * release-driven click-to-tune (SpectrumPanel pointerup path, MOR-2464)
 * is verified by SpectrumPanel.component.test.ts, not by this copy.
 *
 * Tap-to-tune is handled by WaterfallCanvas gesture onTap (not handleDragEnd).
 * These tests verify this fixture's filter only: waterfall targets tune;
 * spectrum-area / toolbar / band-plan targets fall outside its scope.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Local copy of the waterfall-side tap filter (not production code) ──

/**
 * Determines whether a pointer-up event should trigger click-to-tune.
 * Returns the frequency in Hz if tuning should happen, or null if not.
 *
 * Mirrors the target filtering logic for tap-to-tune.
 */
function shouldTapTune(
  target: HTMLElement,
  clientX: number,
  startFreq: number,
  spanHz: number,
): number | null {
  // Ignore taps on interactive elements
  if (target.closest('button, .toolbar-btn, select, input')) return null;

  // Only tune on waterfall taps — spectrum area taps are ignored
  const area = target.closest('.waterfall-content') as HTMLElement | null;
  if (!area) return null;

  const rect = area.getBoundingClientRect();
  const relX = clientX - rect.left;
  return startFreq + (relX / rect.width) * spanHz;
}

// ── DOM helpers ─────────────────────────────────────────────────────────────────

function createSpectrumLayout(): {
  root: HTMLDivElement;
  spectrumArea: HTMLDivElement;
  spectrumCanvas: HTMLCanvasElement;
  bandSegment: HTMLDivElement;
  waterfallContent: HTMLDivElement;
  waterfallCanvas: HTMLCanvasElement;
} {
  const root = document.createElement('div');
  root.className = 'spectrum-panel';

  const spectrumArea = document.createElement('div');
  spectrumArea.className = 'spectrum-area';
  const spectrumCanvas = document.createElement('canvas');
  spectrumArea.appendChild(spectrumCanvas);

  // Band plan overlay segment inside spectrum area
  const bandSegment = document.createElement('div');
  bandSegment.className = 'band-segment';
  spectrumArea.appendChild(bandSegment);

  const waterfallContent = document.createElement('div');
  waterfallContent.className = 'waterfall-content';
  const waterfallCanvas = document.createElement('canvas');
  waterfallContent.appendChild(waterfallCanvas);

  root.appendChild(spectrumArea);
  root.appendChild(waterfallContent);
  document.body.appendChild(root);

  return { root, spectrumArea, spectrumCanvas, bandSegment, waterfallContent, waterfallCanvas };
}

// ── Tests ────────────────────────────────────────────────────────────────────────

describe('tap-to-tune target filtering', () => {
  let layout: ReturnType<typeof createSpectrumLayout>;

  beforeEach(() => {
    layout = createSpectrumLayout();
    // Mock getBoundingClientRect for waterfall
    vi.spyOn(layout.waterfallContent, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 1000, bottom: 400,
      width: 1000, height: 400, x: 0, y: 0, toJSON: () => {},
    });
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  it('tap on waterfall canvas triggers tune', () => {
    const freq = shouldTapTune(layout.waterfallCanvas, 500, 14_000_000, 350_000);
    expect(freq).not.toBeNull();
    // 500px / 1000px * 350000 + 14000000 = 14175000
    expect(freq).toBe(14_175_000);
  });

  it('tap on waterfall content div triggers tune', () => {
    const freq = shouldTapTune(layout.waterfallContent, 200, 14_000_000, 350_000);
    expect(freq).not.toBeNull();
  });

  it('waterfall-only fixture ignores a spectrum canvas target', () => {
    const freq = shouldTapTune(layout.spectrumCanvas, 500, 14_000_000, 350_000);
    expect(freq).toBeNull();
  });

  it('waterfall-only fixture ignores a spectrum area target', () => {
    const freq = shouldTapTune(layout.spectrumArea, 500, 14_000_000, 350_000);
    expect(freq).toBeNull();
  });

  it('tap on band plan segment does NOT trigger tune', () => {
    const freq = shouldTapTune(layout.bandSegment, 500, 14_000_000, 350_000);
    expect(freq).toBeNull();
  });

  it('tap on button inside waterfall does NOT trigger tune', () => {
    const btn = document.createElement('button');
    layout.waterfallContent.appendChild(btn);
    const freq = shouldTapTune(btn, 500, 14_000_000, 350_000);
    expect(freq).toBeNull();
  });

  it('tap on toolbar button does NOT trigger tune', () => {
    const toolbarBtn = document.createElement('div');
    toolbarBtn.className = 'toolbar-btn';
    layout.spectrumArea.appendChild(toolbarBtn);
    const freq = shouldTapTune(toolbarBtn, 500, 14_000_000, 350_000);
    expect(freq).toBeNull();
  });

  it('frequency maps correctly to click position on waterfall', () => {
    // Left edge → startFreq
    expect(shouldTapTune(layout.waterfallCanvas, 0, 14_000_000, 350_000)).toBe(14_000_000);
    // Right edge → endFreq
    expect(shouldTapTune(layout.waterfallCanvas, 1000, 14_000_000, 350_000)).toBe(14_350_000);
    // Center → midpoint
    expect(shouldTapTune(layout.waterfallCanvas, 500, 14_000_000, 350_000)).toBe(14_175_000);
  });
});
