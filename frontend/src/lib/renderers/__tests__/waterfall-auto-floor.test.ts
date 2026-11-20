import { describe, expect, it } from 'vitest';
import {
  WaterfallRenderer, defaultWaterfallOptions, type WaterfallFrameIdentity,
} from '../waterfall-renderer';
import { webglFixture } from './webgl-fixture';

// MOR-4605: AUTO waterfall noise floor. The colour gate replicates the one
// line of the fragment shader that maps a sample to the palette parameter
// (waterfall-renderer.ts fragmentShader, the `t` computation); the browser
// e2e (tests/e2e/renderers/waterfall.spec.ts) pins the real pixels.
function shade(sample: number, floor: number, ref: number, auto: boolean): number {
  const t = Math.min(Math.max((sample - (auto ? floor : 0) + ref * (40 / 60)) / 80, 0), 1);
  return Math.floor(Math.sqrt(t) * 255) / 255;
}

const options = { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 };

/** 100-bin row: flat noise with a 5-bin carrier, `height` above the noise. */
function frame(noise: number, carrier: number): Uint8Array {
  const data = new Uint8Array(100).fill(noise);
  data.fill(carrier, 48, 53);
  return data;
}

/** The per-row metadata rows (12 floats) uploaded by pushRow. */
function rowMetadata(f: ReturnType<typeof webglFixture>): Float32Array[] {
  return f.named('bufferSubData')
    .map(call => call.args[2])
    .filter((arg): arg is Float32Array => arg instanceof Float32Array && arg.length === 12);
}

/** The tracked floor recorded for the most recently uploaded row. */
function lastFloor(f: ReturnType<typeof webglFixture>): number {
  const rows = rowMetadata(f);
  return rows[rows.length - 1][11];
}

/** Bytes of the most recently uploaded ring row (the current frame). */
function lastUploadedRow(f: ReturnType<typeof webglFixture>): number[] {
  const calls = f.named('texSubImage2D');
  const call = calls[calls.length - 1];
  const src = call.args[8] as Uint8Array;
  const offset = call.args[9] as number;
  return Array.from(src.subarray(offset, offset + (call.args[4] as number)));
}

function uniform(f: ReturnType<typeof webglFixture>, name: string): number {
  const calls = f.named('uniform1f').filter(call => call.args[0] === name);
  return calls[calls.length - 1].args[1] as number;
}

describe('MOR-4605 AUTO waterfall noise floor', () => {
  it('is off by default: rows carry no floor and the shader gate stays 0', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, options);
    r.pushRow(frame(20, 60));
    r.pushRow(frame(20, 60));
    // The uploaded bytes are identical to before the option existed: the row
    // metadata floor slot stays 0 and uAutoFloor is 0, so the colour mapping
    // is untouched (waterfall-ring.test.ts pins the full row).
    expect(lastFloor(f)).toBe(0);
    expect(uniform(f, 'uAutoFloor')).toBe(0);
    const background = lastUploadedRow(f)[10];
    expect(background).toBe(20);
    expect(shade(background, 0, 0, false)).toBeGreaterThan(0);
  });

  it('applies the tracked floor to the rows pushed while AUTO is on only', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, options);
    for (let i = 0; i < 5; i++) r.pushRow(frame(20, 60));
    expect(lastFloor(f)).toBe(0);
    expect(uniform(f, 'uAutoFloor')).toBe(0);
    // The toggle alone flips the gate, without a new frame.
    expect(r.updateOptions({ autoFloor: true })).toBe(true);
    r.draw();
    expect(uniform(f, 'uAutoFloor')).toBe(1);
    // Tracking ran while AUTO was off, so the first row after enabling
    // already carries the converged floor and its background colours to the
    // palette start.
    r.pushRow(frame(20, 60));
    expect(lastFloor(f)).toBe(20);
    expect(shade(lastUploadedRow(f)[10], 20, 0, true)).toBe(0);
    // Off again: the gate closes and rows carry no floor.
    expect(r.updateOptions({ autoFloor: false })).toBe(true);
    r.draw();
    expect(uniform(f, 'uAutoFloor')).toBe(0);
    r.pushRow(frame(20, 60));
    expect(lastFloor(f)).toBe(0);
    expect(shade(lastUploadedRow(f)[10], 0, 0, false)).toBeGreaterThan(0);
  });

  it('estimates the floor over the covered bins only', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, {
      ...options, autoFloor: true,
      coverage: { startHz: 14_050_000, endHz: 14_100_000 },
    });
    // 100 bins over [14_000_000, 14_100_000), 1 kHz each. The covered half
    // (bins 50..99) carries noise 20 and a carrier 60; the uncovered half
    // is zero-filled and must not drag the estimate down.
    const data = new Uint8Array(100);
    data.fill(20, 50, 95);
    data.fill(60, 95, 100);
    r.pushRow(data);
    expect(lastFloor(f)).toBe(20);
  });

  it('keeps the background dark and the carrier colour across a mid-stream noise rise', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, { ...options, autoFloor: true });
    // Phase 1: noise 20, carrier 40 above it. The first frame seeds the
    // tracked floor at the estimate; a steady estimate keeps it exact.
    for (let i = 0; i < 60; i++) r.pushRow(frame(20, 60));
    expect(lastFloor(f)).toBe(20);
    const before = lastUploadedRow(f);
    const backgroundBefore = shade(before[10], 20, 0, true);
    const carrierBefore = shade(before[50], 20, 0, true);
    f.calls.length = 0;
    // Phase 2: the noise floor rises by 15 (a known amount); the carrier
    // stays 40 above the noise. 400 frames converge the shared EMA
    // (share 0.04) to 35 - 15*0.96^400 < 35 - 1e-5.
    for (let i = 0; i < 400; i++) r.pushRow(frame(35, 75));
    const floorAfter = lastFloor(f);
    expect(floorAfter).toBeGreaterThan(34.99);
    expect(floorAfter).toBeLessThanOrEqual(35);
    const after = lastUploadedRow(f);
    expect(after[10]).toBe(35);
    expect(after[50]).toBe(75);
    const backgroundAfter = shade(after[10], floorAfter, 0, true);
    const carrierAfter = shade(after[50], floorAfter, 0, true);
    // The background quantises to the same palette parameter before and
    // after the rise, and the carrier keeps its colour exactly.
    expect(Math.abs(backgroundAfter - backgroundBefore)).toBeLessThanOrEqual(1 / 255);
    expect(carrierAfter).toBe(carrierBefore);
  });

  it('treats REF as an offset from the tracked floor while AUTO is on', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, { ...options, autoFloor: true, refLevel: 30 });
    for (let i = 0; i < 10; i++) r.pushRow(frame(20, 60));
    expect(uniform(f, 'uRef')).toBe(30);
    expect(uniform(f, 'uAutoFloor')).toBe(1);
    // A background sample at the tracked floor, REF +30: the offset is
    // 30 * 40/60 = 20 of the 80 scale above the floor, not above zero.
    const background = lastUploadedRow(f)[10];
    expect(background).toBe(20);
    expect(shade(background, 20, 30, true)).toBe(shade(20 + 20, 20, 0, true));
    expect(shade(background, 20, 30, true)).toBeGreaterThan(shade(background, 20, 0, true));
  });

  it('reseeds the tracked floor on a new stream and after clear()', () => {
    const f = webglFixture(100, 50);
    const r = new WaterfallRenderer(f.canvas, { ...options, autoFloor: true });
    const stream = (seq: number, epoch: number): WaterfallFrameIdentity => ({ slot: 'radio:0', seq, epoch });
    for (let seq = 1; seq <= 3; seq++) r.pushRow(frame(20, 60), stream(seq, 1));
    expect(lastFloor(f)).toBe(20);
    // A new epoch is a new stream: the floor reseeds at once instead of
    // EMA-creeping from the old band. The 8-level rise is below the jump
    // distance 24, so only the reseed explains the exact new estimate.
    r.pushRow(frame(28, 68), stream(1, 2));
    expect(lastFloor(f)).toBe(28);
    r.clear();
    f.calls.length = 0;
    r.pushRow(frame(22, 62), stream(2, 2));
    expect(lastFloor(f)).toBe(22);
  });
});
