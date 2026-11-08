import { loadScopeWireGolden, scopeWireGoldenNames as names } from './scope-wire-fixture';
import { describe, expect, it } from 'vitest';
import { ingressScopeFrame, parseCurrentScopeFrame } from '../adapters/scope-adapter';

const load = (name: string) => loadScopeWireGolden(name).buffer;
const context = { source: 'radio' as const, sourceId: 'ignored-control-hint', epoch: 900, mode: 3 };

describe('shared Python v2 scope wire fixtures', () => {
  it('discovers every binary fixture with its sidecar', () => {
    expect(names).toHaveLength(8);
    for (const name of names) expect(() => loadScopeWireGolden(name)).not.toThrow();
  });
  it.each(names)('decodes %s with wire identity and zero pixel copies', name => {
    const expected = loadScopeWireGolden(name).expected;
    const buffer = load(name);
    const result = ingressScopeFrame(buffer, context);
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.reason);
    const frame = result.frame;
    expect(frame).toEqual({
      source: expected.source, sourceId: expected.source_id, receiver: expected.receiver,
      viewStartHz: expected.view_start_hz, viewEndHz: expected.view_end_hz,
      coverageStartHz: expected.coverage_start_hz, coverageEndHz: expected.coverage_end_hz,
      width: expected.width, pixels: expected.pixels === null ? null : Uint8Array.from(expected.pixels),
      outOfRange: expected.out_of_range, epoch: expected.epoch, seq: expected.seq, sentAtMs: expected.sent_at_ms,
    });
    expect(Object.isFrozen(frame)).toBe(true);
    if (frame.pixels) {
      expect(frame.pixels.buffer).toBe(buffer);
      expect(frame.pixels.byteOffset).toBe(56 + new DataView(buffer).getUint8(3));
    }
    expect(result.geometry).toMatchObject({ mode: 3, width: expected.width,
      sourceId: expected.source_id, receiver: expected.receiver,
      view: { startHz: expected.view_start_hz, endHz: expected.view_end_hz },
      coverage: { startHz: expected.coverage_start_hz, endHz: expected.coverage_end_hz } });
  });
  it.each([
    ['source', (wire: DataView) => wire.setUint8(1, 3)],
    ['flags', (wire: DataView) => wire.setUint8(2, 4)],
    ['source id', (wire: DataView) => wire.setUint8(56, 32)],
    ['width', (wire: DataView) => wire.setUint16(6, 0, true)],
    ['sent time', (wire: DataView) => wire.setBigInt64(16, -2n, true)],
    ['view NaN', (wire: DataView) => wire.setFloat64(24, NaN, true)],
    ['coverage outside view', (wire: DataView) => wire.setFloat64(40, 0, true)],
  ] as const)('rejects malformed %s', (_name, mutate) => {
    const buffer = load('radio.bin'); mutate(new DataView(buffer));
    expect(parseCurrentScopeFrame(buffer, context).ok).toBe(false);
  });
  it('rejects v1 and truncated or trailing payloads', () => {
    const buffer = load('radio.bin');
    new DataView(buffer).setUint8(0, 1);
    expect(parseCurrentScopeFrame(buffer, context)).toEqual({ ok: false, reason: 'message type' });
    expect(parseCurrentScopeFrame(load('radio.bin').slice(0, -1), context).ok).toBe(false);
    expect(parseCurrentScopeFrame(new ArrayBuffer(55), context)).toEqual({ ok: false, reason: 'short header' });
  });
});
