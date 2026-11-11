import { afterEach, describe, expect, it, vi } from 'vitest';
import { WaterfallRenderer, defaultWaterfallOptions } from '../waterfall-renderer';
import { countTypedArrays, webglFixture } from './webgl-fixture';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

// MOR-4383: the waterfall's single render path smooths native bins before the
// texture ring: temporal IIR (alpha 0.5 per NEW frame, optional), spatial
// [1 2 1]/4 with repeated edges, and one row group per new frame blending the
// previous filtered frame to the current one (1/3, 2/3, 1 for the 3-row
// group). Ported from the RigDeck presenter (tmp/lane/rd_presenter.c,
// render_console): the group is row_px = 3 ring rows; without a previous
// frame of the same geometry every group row is the current frame.
const options = { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 };

/** Uploaded ring rows of one pushRow call: [bytes, y] per texSubImage2D. */
function uploadedRows(f: ReturnType<typeof webglFixture>): { bytes: number[]; y: number }[] {
  return f.named('texSubImage2D').map(call => {
    const src = call.args[8] as Uint8Array;
    const offset = call.args[9] as number;
    return { bytes: Array.from(src.subarray(offset, offset + (call.args[4] as number))), y: call.args[3] as number };
  });
}

const expectBytes = (f: ReturnType<typeof webglFixture>, expected: number[][]): void => {
  const rows = uploadedRows(f).map(row => row.bytes);
  expect(rows, JSON.stringify(rows)).toEqual(expected);
};

describe('MOR-4383 waterfall smoothing', () => {
  it('applies [1 2 1]/4 with repeated edges and fills the 3-row group with the first frame', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, options);
    r.pushRow(new Uint8Array([0, 80, 0]));
    // filtered = [(0+0+80)/4, (0+160+0)/4, (80+0+0)/4] = [20, 40, 20]; no
    // previous frame: rd_presenter pushes wf_cols for every group row.
    expectBytes(f, [[20, 40, 20], [20, 40, 20], [20, 40, 20]]);
    expect(f.named('texSubImage2D').map(call => call.args[3])).toEqual([0, 1, 2]);
    // One draw per delivered frame, not per group row.
    expect(f.named('drawArrays')).toHaveLength(1);
  });

  it('blends the previous filtered frame across the group as 1/3, 2/3, 1', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, options);
    r.pushRow(new Uint8Array([0, 80, 0]));
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 0, 0]));
    // IIR: state = [(0+0)/2, (80+0)/2, 0] = [0, 40, 0];
    // filtered = [(0+0+40)/4, (0+80+0)/4, (40+0+0)/4] = [10, 20, 10];
    // prev = [20, 40, 20]; blend k/3 rounded:
    //   k=1: [round(50/3), round(100/3), round(50/3)] = [17, 33, 17]
    //   k=2: [round(40/3), round(80/3), round(40/3)]  = [13, 27, 13]
    //   k=3: the current frame itself.
    expectBytes(f, [[17, 33, 17], [13, 27, 13], [10, 20, 10]]);
  });

  it('runs the temporal IIR at alpha 0.5 per new frame', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, options);
    r.pushRow(new Uint8Array([80, 80, 80]));
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 0, 0]));
    // state [40,40,40] -> filtered [40,40,40]; blend from prev [80,80,80]:
    expectBytes(f, [[67, 67, 67], [53, 53, 53], [40, 40, 40]]);
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 0, 0]));
    // state [20,20,20]; blend from prev [40,40,40]: 1/3 -> 33, 2/3 -> 27.
    expectBytes(f, [[33, 33, 33], [27, 27, 27], [20, 20, 20]]);
  });

  it('resets the smoothing state when span, sample centre or bin count changes', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, options);
    r.pushRow(new Uint8Array([0, 80, 0]));
    r.updateOptions({ spanHz: 200_000 });
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 0, 0]));
    // Geometry change: IIR restarts from raw and no previous row blends.
    expectBytes(f, [[0, 0, 0], [0, 0, 0], [0, 0, 0]]);
    r.updateOptions({ spanHz: 200_000, panoramaShiftHz: 1000 });
    f.calls.length = 0;
    r.pushRow(new Uint8Array([80, 0, 0]));
    // Sample-centre change resets: raw [80,0,0] -> [(80+160)/4, (80+0)/4, 0].
    expectBytes(f, [[60, 20, 0], [60, 20, 0], [60, 20, 0]]);
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 80, 0, 0]));
    // Bin count change (3 -> 4) resets: raw + [1 2 1] = [20, 40, 20, 0].
    expectBytes(f, [[20, 40, 20, 0], [20, 40, 20, 0], [20, 40, 20, 0]]);
  });

  it('keeps the spatial filter with temporalSmoothing off and skips the IIR', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, { ...options, temporalSmoothing: false });
    r.pushRow(new Uint8Array([0, 80, 0]));
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 0, 0]));
    // No IIR: raw zeros filter to zeros; the blend still crosses group rows.
    expectBytes(f, [[13, 27, 13], [7, 13, 7], [0, 0, 0]]);
  });

  it('dedupes repeated frame identities and resets on epoch change', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, options);
    r.pushRow(new Uint8Array([0, 80, 0]), { seq: 26_218, epoch: 1 });
    expect(f.named('texSubImage2D')).toHaveLength(3);
    // Same seq again (registration replay): no new rows, no draw.
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 80, 0]), { seq: 26_218, epoch: 1 });
    expect(f.calls).toHaveLength(0);
    // Older seq after a newer one: still ignored.
    r.pushRow(new Uint8Array([0, 80, 0]), { seq: 26_217, epoch: 1 });
    expect(f.calls).toHaveLength(0);
    // Next seq: one new group.
    r.pushRow(new Uint8Array([0, 80, 0]), { seq: 26_219, epoch: 1 });
    expect(f.named('texSubImage2D')).toHaveLength(3);
    // New epoch: processed again, IIR state reset to raw.
    f.calls.length = 0;
    r.pushRow(new Uint8Array([0, 0, 0]), { seq: 1, epoch: 2 });
    expectBytes(f, [[0, 0, 0], [0, 0, 0], [0, 0, 0]]);
  });

  it('allocates no typed arrays per frame after warm-up', () => {
    const f = webglFixture();
    const r = new WaterfallRenderer(f.canvas, options);
    const pixels = new Uint8Array(100).fill(40);
    r.pushRow(pixels);
    const allocated = countTypedArrays();
    for (let i = 0; i < 500; i++) r.pushRow(pixels);
    expect(allocated()).toBe(0);
  });
});
