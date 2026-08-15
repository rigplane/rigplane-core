import { describe, expect, it } from 'vitest';
import type { ExactDecimal } from '../exact-decimal';
import type { ControlDomain, LookupControlDomain, ScalarControlDomain } from '../capabilities';
import { validateCapabilities } from '../capabilities';

function controlDomainTypeContract(linear: ScalarControlDomain, lookup: LookupControlDomain): void {
  // @ts-expect-error normalized bounds are immutable
  linear.raw_min = 1;
  // @ts-expect-error normalized bounds are immutable
  linear.display_max = 1;
  // @ts-expect-error scalar domains cannot carry center fields
  void ({ ...linear, raw_center: 0 } satisfies ControlDomain);
  // @ts-expect-error lookup domains cannot carry center fields
  void ({ ...lookup, raw_center: 0 } satisfies ControlDomain);
  // @ts-expect-error lookup arrays are immutable
  lookup.lookup.push({ raw: 1, display: 1 });
  // @ts-expect-error lookup entries are immutable
  lookup.lookup[0]!.display = 1;
}
void controlDomainTypeContract;
function exactDecimalTypeContract(value: ExactDecimal): void {
  const stringValue: string = value;
  void stringValue;
  // @ts-expect-error exact display strings are never numeric values
  const numberValue: number = value;
  // @ts-expect-error branded strings must not collapse to never
  const impossible: never = value;
  void numberValue;
  void impossible;
}
void exactDecimalTypeContract;
const baseCapabilities = {
  model: 'Test Radio',
  scope: false,
  audio: false,
  tx: false,
  capabilities: [],
  receivers: 1,
  vfoScheme: 'ab',
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
  webrtc: { available: false, enabled: false },
  txBands: null,
};
const linearDomain = {
  raw_min: 0,
  raw_max: 10,
  raw_step: 2,
  raw_origin: 0,
  display_min: 0,
  display_max: 1,
  display_step: 0.2,
  display_origin: 0,
  display_unit: 'dB',
  mapping: 'linear',
  quantization: 'nearest_ties_down',
  restoration: 'exact',
};
function parse(control: Record<string, unknown>): ControlDomain {
  const result = validateCapabilities({
    ...baseCapabilities,
    controls: { test: control },
  });
  return result.controls?.test as ControlDomain;
}
describe('normalized control capability domains', () => {
  it('leaves legacy capability payloads and legacy controls unchanged', () => {
    expect(validateCapabilities(baseCapabilities)).toBe(baseCapabilities);
    const legacy = { range_min: 0, range_max: 10, style: 'stepped' };
    const payload = { ...baseCapabilities, controls: { gain: legacy } };
    expect(validateCapabilities(payload)).toBe(payload);
    expect(() => parse({ raw_min: 0, raw_max: 1, surprise: true })).toThrow(/unknown/);
  });
  it.each([
    ['identity', { ...linearDomain, mapping: 'identity', display_max: 10, display_step: 2 }],
    ['linear', linearDomain],
    ['centered', {
      ...linearDomain, raw_min: -10, raw_origin: -10, display_min: -1, display_origin: -1,
      mapping: 'centered', raw_center: 0, display_center: 0,
    }],
    ['lookup', {
      ...linearDomain, mapping: 'lookup',
      lookup: Array.from({ length: 6 }, (_, index) => ({ raw: index * 2, display: index / 5 })),
    }],
  ])('accepts and freezes a valid %s domain without mutating its input', (_name, control) => {
    const before = structuredClone(control);
    const parsed = parse(control);
    expect(control).toEqual(before);
    expect(parsed).not.toBe(control);
    expect(Object.isFrozen(parsed)).toBe(true);
    if (parsed.mapping === 'lookup') {
      expect(Object.isFrozen(parsed.lookup)).toBe(true);
      expect(parsed.lookup.every(Object.isFrozen)).toBe(true);
    }
  });
  it.each([
    ['missing field', { ...linearDomain, raw_step: undefined }],
    ['empty unit', { ...linearDomain, display_unit: '  ' }],
    ['coerced number', { ...linearDomain, raw_step: '2' }],
    ['coerced boolean', { ...linearDomain, display_max: true }],
    ['non-finite', { ...linearDomain, display_max: Infinity }],
    ['unsafe raw integer', { ...linearDomain, raw_max: Number.MAX_SAFE_INTEGER + 1 }],
    ['unknown mapping', { ...linearDomain, mapping: 'log' }],
    ['unknown quantization', { ...linearDomain, quantization: 'round' }],
    ['unknown restoration', { ...linearDomain, restoration: 'approximate' }],
    ['unknown key', { ...linearDomain, future_magic: 1 }],
  ])('rejects %s', (_name, control) => {
    expect(() => parse(control)).toThrow(/Invalid capabilities payload at \$\.controls\.test/);
  });

  it.each([
    ['reversed raw range', { raw_min: 10, raw_max: 0 }],
    ['zero raw step', { raw_step: 0 }],
    ['raw endpoint off lattice', { raw_max: 9 }],
    ['display endpoint off lattice', { display_max: 1.1 }],
    ['origin outside range', { display_origin: -0.2 }],
    ['identity axes differ', { mapping: 'identity' }],
    ['center missing', { mapping: 'centered' }],
    ['center off lattice', { mapping: 'centered', raw_center: 1, display_center: 0 }],
    ['unexpected center', { raw_center: 0 }],
  ])('rejects invalid scalar domain: %s', (_name, patch) => {
    expect(() => parse({ ...linearDomain, ...patch })).toThrow(TypeError);
  });
  it('uses exact decimal arithmetic for large lattice indices', () => {
    expect(() => parse({
      ...linearDomain,
      display_max: 1_000_000_000_000,
      display_step: 0.000_000_000_000_000_000_01,
    })).not.toThrow();
    expect(() => parse({
      ...linearDomain,
      display_max: 1_000_000_000_000.0001,
      display_step: 0.0002,
    })).toThrow(/lattice/);
  });
  it('normalizes homogeneous legacy display numbers to immutable canonical strings', () => {
    const parsed = parse({ ...linearDomain, display_min: -0, display_max: 1e-7, display_step: 2e-8, display_origin: -0 });
    expect(parsed.display_min).toBe('0');
    expect(parsed.display_step).toBe('0.00000002');
    expect(Object.isFrozen(parsed)).toBe(true);
  });
  it('accepts arbitrary canonical decimal strings without numeric coercion', () => {
    const huge = `1${'0'.repeat(399)}`;
    const parsed = parse({
      ...linearDomain, raw_max: 2, raw_step: 1,
      display_min: '0', display_max: huge, display_step: `5${'0'.repeat(398)}`, display_origin: '0',
    });
    expect(parsed.display_max).toBe(huge);
  });
  it('accepts negative display axes and nonzero origins as canonical strings', () => {
    const parsed = parse({
      ...linearDomain,
      display_min: '-1', display_max: '1', display_step: '0.2', display_origin: '-0.2',
    });
    expect(parsed.display_min).toBe('-1');
    expect(parsed.display_origin).toBe('-0.2');
  });
  it.each(['+1', '1e3', ' 1', '01', '1.0', '-0', '-0.0', '1.', ''])('rejects noncanonical string display %s', (display) => {
    expect(() => parse({ ...linearDomain, display_min: display, display_max: '1', display_step: '0.2', display_origin: '0' })).toThrow(TypeError);
  });
  it('rejects mixed number and string display domains, including lookup values', () => {
    expect(() => parse({ ...linearDomain, display_min: '0' })).toThrow(/homogeneous/);
    expect(() => parse({ ...linearDomain, mapping: 'lookup', restoration: 'unavailable', lookup: [{ raw: 0, display: '0' }] })).toThrow(/homogeneous/);
  });
  const lookup = [{ raw: 0, display: 0 }, { raw: 2, display: 0.2 }, { raw: 4, display: 0.4 }];
  it.each([
    ['empty', []],
    ['duplicate raw', [lookup[0], lookup[0]]],
    ['duplicate display', [lookup[0], { raw: 2, display: 0 }]],
    ['non-monotonic', [lookup[0], lookup[2], lookup[1]]],
    ['out of range', [lookup[0], { raw: 12, display: 0.2 }]],
    ['off lattice', [lookup[0], { raw: 1, display: 0.2 }]],
    ['malformed point', [lookup[0], { raw: '2', display: 0.2 }]],
    ['unknown point key', [lookup[0], { raw: 2, display: 0.2, label: 'x' }]],
  ])('rejects %s lookup data', (_name, points) => {
    expect(() => parse({
      ...linearDomain,
      mapping: 'lookup',
      restoration: 'unavailable',
      lookup: points,
    })).toThrow(TypeError);
  });
  it('requires exact lookup coverage but permits truthful unavailable subsets', () => {
    const partial = {
      ...linearDomain,
      mapping: 'lookup',
      lookup,
    };
    expect(() => parse(partial)).toThrow(/complete/);
    const parsed = parse({ ...partial, restoration: 'unavailable' });
    expect(parsed.mapping).toBe('lookup');
    if (parsed.mapping === 'lookup') expect(parsed.lookup).toHaveLength(3);
  });
  it('normalizes and deeply freezes canonical lookup displays', () => {
    const parsed = parse({
      ...linearDomain, mapping: 'lookup', restoration: 'unavailable',
      display_min: '0', display_max: '1', display_step: '0.2', display_origin: '0',
      lookup: [{ raw: 0, display: '0' }, { raw: 2, display: '0.2' }],
    });
    expect(parsed.mapping).toBe('lookup');
    if (parsed.mapping === 'lookup') {
      expect(parsed.lookup[1]!.display).toBe('0.2');
      expect(Object.isFrozen(parsed.lookup[0])).toBe(true);
    }
  });
  it('accepts negative fractional lookup values', () => {
    const parsed = parse({
      ...linearDomain, raw_max: 2, raw_step: 1, mapping: 'lookup', restoration: 'exact',
      display_min: '-0.1', display_max: '0.1', display_step: '0.1', display_origin: '-0.1',
      lookup: [{ raw: 0, display: '-0.1' }, { raw: 1, display: '0' }, { raw: 2, display: '0.1' }],
    });
    expect(parsed.mapping).toBe('lookup');
    if (parsed.mapping === 'lookup') expect(parsed.lookup[0]!.display).toBe('-0.1');
  });
  it('accepts tiny negative fixed-point display values', () => {
    const parsed = parse({
      ...linearDomain, raw_max: 2, raw_step: 1,
      display_min: '-0.0001', display_max: '0.0001', display_step: '0.0001', display_origin: '-0.0001',
    });
    expect(parsed.display_min).toBe('-0.0001');
  });
  it('rejects large-index lookup values that are only approximately on lattice', () => {
    expect(() => parse({
      ...linearDomain,
      display_max: 1_000_000_000_000.2,
      display_step: 0.2,
      mapping: 'lookup',
      restoration: 'unavailable',
      lookup: [{ raw: 0, display: 1_000_000_000_000.05 }],
    })).toThrow(/lattice/);
  });
});
