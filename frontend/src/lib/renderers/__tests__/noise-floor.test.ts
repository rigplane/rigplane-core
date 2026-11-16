import { describe, expect, it } from 'vitest';
import { estimateNoiseFloor, followNoiseFloor } from '../noise-floor';

describe('MOR-4463 noise floor of a spectrum frame', () => {
  it('is the mean of the samples that do not exceed the frame mean: signals leave themselves out', () => {
    const frame = new Uint8Array(100).fill(10);
    frame.fill(80, 40, 50);
    // Mean 17: only the 90 samples of noise are at or below it.
    expect(estimateNoiseFloor(frame)).toBe(10);
    const sloped = Uint8Array.of(8, 10, 12, 10, 8, 12, 60, 70);
    // Mean 23.75; the six noise samples average 10.
    expect(estimateNoiseFloor(sloped)).toBe(10);
  });

  it('is the level itself for a flat frame and 0 for an empty one', () => {
    expect(estimateNoiseFloor(new Uint8Array(64).fill(37))).toBe(37);
    expect(estimateNoiseFloor(new Uint8Array(0))).toBe(0);
    expect(estimateNoiseFloor(Uint8Array.of(1, 2, 3), 3)).toBe(0);
  });

  it('reads only the samples it is given', () => {
    const frame = Uint8Array.of(200, 200, 20, 20, 20, 20, 200);
    expect(estimateNoiseFloor(frame, 2, 4)).toBe(20);
    expect(estimateNoiseFloor(frame, 2)).toBe(20);
  });

  it('reads a dense frame through at most 512 evenly spaced samples', () => {
    const dense = new Uint8Array(16_384).fill(10);
    // Every 32nd sample is read; a signal in between does not move the floor.
    for (let i = 1; i < dense.length; i += 32) dense[i] = 250;
    expect(estimateNoiseFloor(dense)).toBe(10);
    for (let i = 0; i < dense.length; i += 64) dense[i] = 90;
    // Now half of the samples read are 90: the mean is 50 and the low half is the floor.
    expect(estimateNoiseFloor(dense)).toBe(10);
  });
});

describe('MOR-4463 noise floor followed in time', () => {
  it('takes the first estimate as it is', () => {
    expect(followNoiseFloor(null, 14, 24)).toBe(14);
  });

  it('follows a drifting estimate by 4 % per frame', () => {
    expect(followNoiseFloor(10, 20, 24)).toBeCloseTo(10.4, 12);
    expect(followNoiseFloor(20, 10, 24)).toBeCloseTo(19.6, 12);
    expect(followNoiseFloor(10, 20, 24, 0.5)).toBe(15);
    let floor = 10;
    for (let i = 0; i < 200; i++) floor = followNoiseFloor(floor, 20, 24);
    expect(floor).toBeGreaterThan(19.9);
    expect(floor).toBeLessThan(20);
  });

  it('jumps to an estimate further away than the jump distance, either way', () => {
    expect(followNoiseFloor(10, 34, 24)).toBeCloseTo(10.96, 12);
    expect(followNoiseFloor(10, 34.5, 24)).toBe(34.5);
    expect(followNoiseFloor(60, 30, 24)).toBe(30);
  });
});
