import { describe, expect, it } from 'vitest';
import { defaultWaterfallOptions, type WaterfallOptions } from '../waterfall-renderer';
import {
  defaultStackedTraceSettings,
  projectStackedDepth,
  projectStackedRow,
  stackedColumnCount,
  stackedGlidePhase,
  stackedRowGeometry,
  type StackedDepth,
  type StackedRowGeometry,
  type StackedRowView,
} from '../stacked-trace-geometry';

const row = (): StackedRowGeometry => ({
  startHz: 0, spanHz: 1, coverStartHz: 0, coverEndHz: 0, samples: 0, sampleOffset: 0, amplitudeMax: 80,
});
const view = (): StackedRowView => ({ scale: 0, offset: 0, coverLo: 0, coverHi: 0 });
const options = (patch: Partial<WaterfallOptions>): WaterfallOptions => ({ ...defaultWaterfallOptions, ...patch });

describe('MOR-4463 stacked-trace row geometry', () => {
  it('anchors a row to the frequencies its samples came from, for any sample count', () => {
    for (const samples of [101, 475, 2048, 16384]) {
      const out = row();
      expect(stackedRowGeometry(options({ centerHz: 14_050_000, spanHz: 100_000 }), samples, out)).toBe(true);
      expect(out).toEqual({
        startHz: 14_000_000, spanHz: 100_000, coverStartHz: 14_000_000, coverEndHz: 14_100_000,
        samples, sampleOffset: 0, amplitudeMax: 80,
      });
    }
  });

  it('places the samples at the SOURCE window while the viewport is shifted or zoomed', () => {
    const out = row();
    stackedRowGeometry(options({
      centerHz: 14_052_000, spanHz: 50_000, sampleSpanHz: 100_000, panoramaShiftHz: 2_000,
      coverage: { startHz: 14_010_000, endHz: 14_090_000 },
    }), 475, out);
    expect(out.startHz).toBe(14_000_000);
    expect(out.spanHz).toBe(100_000);
    expect([out.coverStartHz, out.coverEndHz]).toEqual([14_010_000, 14_090_000]);
  });

  it('honours the sample offset, the amplitude range and a reversed sample window', () => {
    const out = row();
    stackedRowGeometry(options({
      centerHz: 1_500, spanHz: 3_000, sampleOffset: 512, sampleAmplitudeMax: 160,
      sampleWindow: { start: 1, end: 0 },
    }), 1024, out);
    expect(out.samples).toBe(512);
    expect(out.sampleOffset).toBe(512);
    expect(out.amplitudeMax).toBe(160);
    // Coordinate 0 sits at the high edge and the span runs downward.
    expect(out.startHz).toBe(3_000);
    expect(out.spanHz).toBe(-3_000);
    expect([out.coverStartHz, out.coverEndHz]).toEqual([0, 3_000]);
  });

  it('refuses a frame that carries no samples', () => {
    expect(stackedRowGeometry(options({ centerHz: 1, spanHz: 1, sampleOffset: 8 }), 8, row())).toBe(false);
  });
});

describe('MOR-4463 stacked-trace view projection', () => {
  const stored = row();
  stackedRowGeometry(options({ centerHz: 14_050_000, spanHz: 100_000 }), 475, stored);
  const coordinateAt = (v: StackedRowView, x: number) => v.offset + v.scale * x;

  it('maps the view one to one onto a row captured at that view', () => {
    const out = view();
    projectStackedRow(stored, 14_000_000, 100_000, out);
    expect(out).toEqual({ scale: 1, offset: 0, coverLo: 0, coverHi: 1 });
  });

  it('keeps a signal on its frequency when the view retunes and zooms', () => {
    // A carrier at 14.060 MHz is sample coordinate 0.6 of the stored row.
    const retuned = view();
    projectStackedRow(stored, 14_010_000, 100_000, retuned);
    expect(coordinateAt(retuned, 0.5)).toBeCloseTo(0.6, 12);
    expect([retuned.coverLo, retuned.coverHi]).toEqual([-0.1, 0.9]);
    const zoomed = view();
    projectStackedRow(stored, 14_050_000, 20_000, zoomed);
    expect(coordinateAt(zoomed, 0.5)).toBeCloseTo(0.6, 12);
    expect(zoomed.scale).toBeCloseTo(0.2, 12);
  });

  it('keeps sub-hertz placement at VHF frequencies', () => {
    const vhf = row();
    stackedRowGeometry(options({ centerHz: 1_296_100_000.25, spanHz: 5_000 }), 475, vhf);
    const out = view();
    projectStackedRow(vhf, 1_296_100_000.25 - 2_500 + 1, 5_000, out);
    expect(out.offset).toBeCloseTo(1 / 5_000, 9);
  });
});

describe('MOR-4463 stacked-trace depth projection', () => {
  const settings = defaultStackedTraceSettings;
  const at = (depth: number): StackedDepth => {
    const out = { baseline: 0, scale: 0, fog: 0 };
    projectStackedDepth(depth, settings, out);
    return { ...out };
  };

  it('draws the front row full size on the front baseline, without fog', () => {
    expect(at(0)).toEqual({ baseline: 0, scale: 1, fog: 0 });
  });

  it('recedes monotonically: rows climb, shrink and fade, and pack closer with depth', () => {
    const steps = Array.from({ length: 11 }, (_, i) => at(i / 10));
    for (let i = 1; i < steps.length; i++) {
      expect(steps[i].baseline).toBeGreaterThan(steps[i - 1].baseline);
      expect(steps[i].scale).toBeLessThan(steps[i - 1].scale);
      expect(steps[i].fog).toBeGreaterThanOrEqual(steps[i - 1].fog);
    }
    const nearGap = steps[1].baseline - steps[0].baseline;
    const farGap = steps[10].baseline - steps[9].baseline;
    // Far rows sit several times closer together than near ones (a linear
    // floor would give equal gaps).
    expect(farGap).toBeLessThan(nearGap / 3);
    expect(steps[10].baseline).toBeCloseTo(settings.floorRise, 12);
    // Near rows keep their full colour; the last one is almost dissolved.
    expect(steps[0].fog).toBe(0);
    expect(steps[1].fog).toBe(0);
    expect(steps[5].fog).toBeGreaterThan(0.1);
    expect(steps[5].fog).toBeLessThan(0.6);
    expect(steps[10].fog).toBeCloseTo(0.9, 12);
  });

  it('keeps the whole scene inside the pane', () => {
    for (let i = 0; i <= 20; i++) {
      const depth = at(i / 20);
      expect(depth.baseline + settings.traceHeight * depth.scale).toBeLessThan(0.9);
    }
  });

  it('clamps depths outside the stack', () => {
    expect(at(-1)).toEqual(at(0));
    expect(at(2)).toEqual(at(1));
  });
});

describe('MOR-4463 stacked-trace pacing', () => {
  it('scales the column count with the backing width inside fixed bounds', () => {
    expect(stackedColumnCount(0)).toBe(64);
    expect(stackedColumnCount(950)).toBe(475);
    expect(stackedColumnCount(1920)).toBe(960);
    expect(stackedColumnCount(20_000)).toBe(2048);
  });

  it('glides a row one slot back over one row period and then rests', () => {
    expect(stackedGlidePhase(1_000, 1_000, 100)).toBe(0);
    expect(stackedGlidePhase(1_050, 1_000, 100)).toBe(0.5);
    expect(stackedGlidePhase(1_400, 1_000, 100)).toBe(1);
    expect(stackedGlidePhase(900, 1_000, 100)).toBe(0);
  });
});
