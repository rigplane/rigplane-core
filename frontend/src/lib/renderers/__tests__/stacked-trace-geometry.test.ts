import { describe, expect, it } from 'vitest';
import { defaultWaterfallOptions, type WaterfallOptions } from '../waterfall-renderer';
import {
  defaultStackedTraceSettings,
  parseStackedTraceSettings,
  stackedTraceTunables,
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
    expect(farGap).toBeLessThan(nearGap / 2.2);
    expect(steps[10].baseline).toBeCloseTo(settings.floorRise, 12);
    // Near rows keep their full colour; the last one is almost dissolved.
    expect(steps[0].fog).toBe(0);
    expect(steps[1].fog).toBe(0);
    expect(steps[5].fog).toBeGreaterThan(0.2 * settings.haze);
    expect(steps[5].fog).toBeLessThan(0.6 * settings.haze);
    expect(steps[10].fog).toBeCloseTo(settings.haze, 12);
    // A light haze: the far rows stay readable.
    expect(settings.haze).toBeLessThanOrEqual(0.3);
    // The haze starts past the nearest 15 % of the stack.
    expect(at(0.15).fog).toBe(0);
    expect(at(0.2).fog).toBeGreaterThan(0);
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

describe('MOR-4463 stacked-trace perspective', () => {
  const settings = defaultStackedTraceSettings;
  const at = (depth: number): StackedDepth => {
    const out = { baseline: 0, scale: 0, fog: 0 };
    projectStackedDepth(depth, settings, out);
    return out;
  };

  it('narrows the view to 60 % at the last row', () => {
    expect(at(1).scale).toBeCloseTo(0.6, 12);
    expect(at(1).scale).toBeCloseTo(1 / (1 + settings.recede), 12);
  });

  it('runs every frequency along a straight line to one vanishing point above the middle of the pane', () => {
    // A view fraction f on a row is drawn at 0.5 + (f - 0.5) * scale, on the
    // row's baseline. All such points of one f lie on the line from (f, 0)
    // to the vanishing point (0.5, vanishY) exactly when the scale falls
    // linearly with the baseline and reaches zero at vanishY.
    const vanishY = settings.floorRise * (1 + settings.recede) / settings.recede;
    expect(vanishY).toBeGreaterThan(settings.floorRise);
    for (let row = 0; row <= settings.rows; row++) {
      const placed = at(row / settings.rows);
      expect(placed.scale).toBeCloseTo(1 - placed.baseline / vanishY, 12);
    }
  });
});

describe('MOR-4463 stacked-trace adjustable settings', () => {
  it('starts from the agreed values', () => {
    expect(defaultStackedTraceSettings).toEqual({ rows: 96, rowPeriodMs: 100, frontInsetPx: 16, floorRise: 0.58,
      traceHeight: 0.46, recede: 2 / 3, haze: 0.16, rowBlend: 0.6, rowSoften: 1, floorDrop: 8, colorGain: 70 });
  });

  it('offers every adjustable value a range that holds its default', () => {
    expect(stackedTraceTunables.map(item => item.key))
      .toEqual(['recede', 'rows', 'rowPeriodMs', 'floorRise', 'traceHeight', 'haze', 'rowBlend', 'rowSoften', 'floorDrop', 'colorGain']);
    for (const { key, min, max, step } of stackedTraceTunables) {
      expect(defaultStackedTraceSettings[key], key).toBeGreaterThanOrEqual(min);
      expect(defaultStackedTraceSettings[key], key).toBeLessThanOrEqual(max);
      expect(step, key).toBeGreaterThan(0);
    }
  });

  it('keeps the whole scene inside the pane at every corner of the ranges', () => {
    const out = { baseline: 0, scale: 0, fog: 0 };
    for (const recede of [0, 2]) for (const floorRise of [0.2, 0.7]) for (const traceHeight of [0.2, 0.8]) {
      const settings = parseStackedTraceSettings({ recede, floorRise, traceHeight });
      for (const depth of [0, 0.5, 1]) {
        projectStackedDepth(depth, settings, out);
        expect(out.baseline).toBeLessThanOrEqual(floorRise + 1e-12);
        expect(out.scale).toBeGreaterThan(0);
        expect(out.scale).toBeLessThanOrEqual(1);
      }
    }
  });

  it('reads stored settings: defaults for what is missing or not a number, every value held in its range', () => {
    expect(parseStackedTraceSettings(null)).toEqual(defaultStackedTraceSettings);
    expect(parseStackedTraceSettings('rows')).toEqual(defaultStackedTraceSettings);
    expect(parseStackedTraceSettings({ rows: '40', haze: Number.NaN, recede: Number.POSITIVE_INFINITY }))
      .toEqual(defaultStackedTraceSettings);
    const inside = { rows: 40, haze: 0.5, recede: 1, floorRise: 0.3, traceHeight: 0.7, rowPeriodMs: 50, rowBlend: 1,
      rowSoften: 0.5, floorDrop: 12, colorGain: 30 };
    expect(parseStackedTraceSettings({ ...inside, rows: 40.4 })).toEqual({ ...defaultStackedTraceSettings, ...inside });
    expect(parseStackedTraceSettings({ rows: 1, haze: 5, recede: -1, floorRise: 9, traceHeight: 0, rowPeriodMs: 1, rowBlend: 0,
      rowSoften: -3, floorDrop: 99, colorGain: 101 }))
      .toEqual({ ...defaultStackedTraceSettings, rows: 24, haze: 0.9, recede: 0, floorRise: 0.7, traceHeight: 0.2, rowPeriodMs: 40,
        rowBlend: 0.2, rowSoften: 0, floorDrop: 24, colorGain: 100 });
    // The label strip is not adjustable, and unknown keys are dropped.
    expect(parseStackedTraceSettings({ frontInsetPx: 99, colour: 'red' })).toEqual(defaultStackedTraceSettings);
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
