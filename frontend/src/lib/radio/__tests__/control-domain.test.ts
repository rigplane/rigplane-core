import { describe, expect, it } from 'vitest';
import type { ControlDomain } from '../../types/capabilities';
import { decodeControlDomain, encodeControlDomain, quantizeControlDomain } from '../control-domain';

const linear: ControlDomain = Object.freeze({
  raw_min: -4, raw_max: 4, raw_step: 2, raw_origin: 0,
  display_min: '-1' as never, display_max: '1' as never, display_step: '0.5' as never, display_origin: '0' as never,
  display_unit: 'dB', mapping: 'linear', quantization: 'nearest_ties_down', restoration: 'exact',
});

describe('control-domain exact math', () => {
  it('round-trips a valid identity domain', () => {
    const domain = { ...linear, raw_min: -1, raw_max: 1, raw_step: 1, raw_origin: 0,
      display_min: '-1' as never, display_max: '1' as never, display_step: '1' as never, display_origin: '0' as never, mapping: 'identity' as const };
    expect(decodeControlDomain(domain, -1)).toBe('-1');
    expect(encodeControlDomain(domain, '1' as never)).toBe(1);
  });
  it('maps linear lattice points with negative values and nonzero raw origins', () => {
    expect(decodeControlDomain(linear, -4)).toBe('-1');
    expect(decodeControlDomain(linear, 0)).toBe('0');
    expect(encodeControlDomain(linear, '0.5' as never)).toBe(2);
    expect(encodeControlDomain(linear, '-0.75' as never)).toBe(-4);
  });
  it.each([
    ['nearest_ties_down', '-0.75', '-1'], ['nearest_ties_up', '-0.75', '-0.5'],
    ['floor', '-0.75', '-1'], ['ceil', '-0.75', '-0.5'], ['reject', '-0.75', null],
  ] as const)('uses %s at negative half-step boundaries', (quantization, input, expected) => {
    expect(quantizeControlDomain({ ...linear, quantization }, input as never)).toBe(expected);
  });
  it.each(['nearest_ties_down', 'nearest_ties_up', 'floor', 'ceil', 'reject'] as const)('keeps supported quantization %s valid', (quantization) => {
    const domain = { ...linear, quantization };
    expect(decodeControlDomain(domain, 0)).toBe('0');
    expect(quantizeControlDomain(domain, '0' as never)).toBe('0');
    expect(encodeControlDomain(domain, '0' as never)).toBe(0);
  });
  it('keeps tiny steps and huge coefficients exact', () => {
    const huge = `1${'0'.repeat(300)}`;
    const domain = { ...linear, raw_min: 0, raw_max: 2, raw_step: 1, raw_origin: 0,
      display_min: huge as never, display_origin: huge as never, display_step: '0.00000000000000000001' as never,
      display_max: `${huge}.00000000000000000002` as never };
    expect(decodeControlDomain(domain, 1)).toBe(`${huge}.00000000000000000001`);
    expect(encodeControlDomain(domain, `${huge}.00000000000000000002` as never)).toBe(2);
  });
  it('canonicalizes mixed-scale arithmetic without signed or fractional zeroes', () => {
    const domain = { ...linear, raw_min: -2, raw_max: 0, raw_step: 1, raw_origin: 0,
      display_min: '0' as never, display_max: '0.1' as never, display_step: '0.05' as never, display_origin: '0.1' as never };
    expect(decodeControlDomain(domain, 0)).toBe('0.1');
    expect(quantizeControlDomain(domain, '0.1' as never)).toBe('0.1');
  });
  it('uses centered anchors independently of nonzero lattice origins', () => {
    const domain = { ...linear, mapping: 'centered' as const, raw_center: 0, display_center: '0' as never };
    for (const [raw, display] of [[-4, '-1'], [-2, '-0.5'], [0, '0'], [2, '0.5'], [4, '1']] as const) {
      expect(decodeControlDomain(domain, raw)).toBe(display);
      expect(encodeControlDomain(domain, display as never)).toBe(raw);
    }
  });
  it('inverts descending lookup values and rejects ambiguous entries', () => {
    const domain = { ...linear, mapping: 'lookup' as const, lookup: [
      { raw: -4, display: '1' as never }, { raw: -2, display: '0.5' as never }, { raw: 0, display: '0' as never },
      { raw: 2, display: '-0.5' as never }, { raw: 4, display: '-1' as never },
    ] };
    expect(decodeControlDomain(domain, 2)).toBe('-0.5');
    expect(encodeControlDomain(domain, '-0.5' as never)).toBe(2);
    expect(encodeControlDomain({ ...domain, lookup: [...domain.lookup, { raw: 4, display: '-0.5' as never }] }, '-0.5' as never)).toBeNull();
  });
  it('never claims reversible encoding for unavailable restoration', () => {
    expect(encodeControlDomain({ ...linear, restoration: 'unavailable' }, '0' as never)).toBeNull();
    expect(decodeControlDomain({ ...linear, restoration: 'unavailable' }, 0)).toBe('0');
  });
  it('rejects values outside the axis before quantization or encoding', () => {
    expect(quantizeControlDomain(linear, '1.1' as never)).toBeNull();
    expect(quantizeControlDomain(linear, '-1.1' as never)).toBeNull();
    expect(encodeControlDomain(linear, '1.1' as never)).toBeNull();
  });
  it.each([undefined, null, {}, [{ raw: 0, display: '0', extra: true }], [{ raw: -4, display: '-1' }, { raw: -4, display: '0' }]])('fails closed for malformed lookup %p', (lookup) => {
    const domain = { ...linear, mapping: 'lookup' as never, lookup } as ControlDomain;
    expect(decodeControlDomain(domain, 0)).toBeNull();
    expect(quantizeControlDomain(domain, '0' as never)).toBeNull();
    expect(encodeControlDomain(domain, '0' as never)).toBeNull();
  });
  it('rejects a centered domain whose center offsets do not align', () => {
    const domain = { ...linear, mapping: 'centered' as const, raw_center: 0, display_center: '0.5' as never };
    expect(decodeControlDomain(domain, -4)).toBeNull();
    expect(decodeControlDomain(domain, 4)).toBeNull();
    expect(quantizeControlDomain(domain, '0' as never)).toBeNull();
    expect(encodeControlDomain(domain, '0' as never)).toBeNull();
  });
  it.each([undefined, null, 0, {}, [], '', 'nearest'])('fails closed for invalid quantization %p', (quantization) => {
    const domain = { ...linear, quantization } as ControlDomain;
    expect(decodeControlDomain(domain, 0)).toBeNull();
    expect(quantizeControlDomain(domain, '0' as never)).toBeNull();
    expect(encodeControlDomain(domain, '0' as never)).toBeNull();
  });
  it('fails closed for off-lattice raw, malformed decimals, bad mapping and overflow', () => {
    expect(decodeControlDomain(linear, -3)).toBeNull();
    expect(quantizeControlDomain(linear, '01' as never)).toBeNull();
    expect(decodeControlDomain({ ...linear, mapping: 'future' as never }, 0)).toBeNull();
    expect(encodeControlDomain({ ...linear, raw_max: Number.MAX_SAFE_INTEGER + 1 }, '0' as never)).toBeNull();
  });
});
