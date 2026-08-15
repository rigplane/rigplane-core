import type { ExactDecimal } from '../types/exact-decimal';
import type { ControlDomain, ControlQuantization } from '../types/capabilities';

type Decimal = readonly [coefficient: bigint, scale: number];

const DECIMAL = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$/;

function decimal(value: unknown): Decimal | null {
  if (typeof value !== 'string' || value === '-0' || !DECIMAL.test(value)) return null;
  const negative = value.startsWith('-');
  const unsigned = negative ? value.slice(1) : value;
  const [integer, fraction = ''] = unsigned.split('.');
  return [BigInt(`${negative ? '-' : ''}${integer}${fraction}`), fraction.length];
}

function text([coefficient, scale]: Decimal): ExactDecimal {
  if (coefficient === 0n) return '0' as ExactDecimal;
  const negative = coefficient < 0n ? '-' : '';
  const digits = (coefficient < 0n ? -coefficient : coefficient).toString();
  if (scale === 0) return `${negative}${digits}` as ExactDecimal;
  const padded = digits.padStart(scale + 1, '0');
  const result = `${negative}${padded.slice(0, -scale)}.${padded.slice(-scale)}`.replace(/\.0+$/, '');
  return result as ExactDecimal;
}

function align(values: readonly Decimal[]): bigint[] {
  const scale = Math.max(...values.map((value) => value[1]));
  return values.map(([coefficient, valueScale]) => coefficient * 10n ** BigInt(scale - valueScale));
}

function compare(left: Decimal, right: Decimal): number {
  const [a, b] = align([left, right]);
  return a === b ? 0 : a < b ? -1 : 1;
}

function add(origin: Decimal, steps: bigint, step: Decimal): Decimal {
  const [base, increment] = align([origin, step]);
  return [base + steps * increment, Math.max(origin[1], step[1])];
}

function lattice(value: Decimal, origin: Decimal, step: Decimal): bigint | null {
  if (compare(step, [0n, 0]) <= 0) return null;
  const [item, base, unit] = align([value, origin, step]);
  const difference = item - base;
  return difference % unit === 0n ? difference / unit : null;
}

function floorDivide(value: bigint, divisor: bigint): bigint {
  const quotient = value / divisor;
  return value < 0n && value % divisor !== 0n ? quotient - 1n : quotient;
}

function rawIndex(domain: ControlDomain, raw: number): bigint | null {
  if (!Number.isSafeInteger(raw) || !Number.isSafeInteger(domain.raw_min) || !Number.isSafeInteger(domain.raw_max)
    || !Number.isSafeInteger(domain.raw_step) || !Number.isSafeInteger(domain.raw_origin)
    || domain.raw_min >= domain.raw_max || domain.raw_step <= 0 || domain.raw_origin < domain.raw_min
    || domain.raw_origin > domain.raw_max || raw < domain.raw_min || raw > domain.raw_max) return null;
  const step = BigInt(domain.raw_step);
  if ((BigInt(domain.raw_min) - BigInt(domain.raw_origin)) % step !== 0n
    || (BigInt(domain.raw_max) - BigInt(domain.raw_origin)) % step !== 0n) return null;
  const index = (BigInt(raw) - BigInt(domain.raw_origin)) / step;
  return (BigInt(raw) - BigInt(domain.raw_origin)) % step === 0n ? index : null;
}

function axis(domain: ControlDomain): readonly [Decimal, Decimal, Decimal, Decimal] | null {
  const values = [domain.display_min, domain.display_max, domain.display_step, domain.display_origin].map(decimal);
  if (values.some((value) => !value)) return null;
  const result = values as Decimal[];
  if (compare(result[0]!, result[1]!) >= 0 || compare(result[2]!, [0n, 0]) <= 0) return null;
  if (compare(result[3]!, result[0]!) < 0 || compare(result[3]!, result[1]!) > 0) return null;
  if (lattice(result[0]!, result[3]!, result[2]!) === null || lattice(result[1]!, result[3]!, result[2]!) === null) return null;
  return result as unknown as readonly [Decimal, Decimal, Decimal, Decimal];
}

function inAxis(value: Decimal, values: readonly [Decimal, Decimal, Decimal, Decimal]): boolean {
  return compare(value, values[0]) >= 0 && compare(value, values[1]) <= 0 && lattice(value, values[3], values[2]) !== null;
}

function scalarIndex(domain: ControlDomain): readonly [bigint, bigint] | null {
  const rawMin = rawIndex(domain, domain.raw_min);
  const rawOrigin = rawIndex(domain, domain.raw_origin);
  if (rawMin === null || rawOrigin === null) return null;
  return [rawMin, rawOrigin];
}

function sameCardinality(domain: ControlDomain, values: readonly [Decimal, Decimal, Decimal, Decimal]): boolean {
  const rawMin = rawIndex(domain, domain.raw_min);
  const rawMax = rawIndex(domain, domain.raw_max);
  const displayMin = lattice(values[0], values[3], values[2]);
  const displayMax = lattice(values[1], values[3], values[2]);
  return rawMin !== null && rawMax !== null && displayMin !== null && displayMax !== null
    && rawMax - rawMin === displayMax - displayMin;
}

function isIdentity(domain: ControlDomain, values: readonly [Decimal, Decimal, Decimal, Decimal]): boolean {
  return [domain.raw_min, domain.raw_max, domain.raw_step, domain.raw_origin]
    .every((raw, index) => compare([BigInt(raw), 0], values[index]!) === 0);
}

function domainIsExact(domain: ControlDomain): boolean {
  if (domain.restoration !== 'exact') return false;
  if (domain.mapping !== 'lookup') return true;
  const min = rawIndex(domain, domain.raw_min);
  const max = rawIndex(domain, domain.raw_max);
  if (min === null || max === null || BigInt(domain.lookup.length) !== max - min + 1n) return false;
  const raws = new Set<number>();
  const displays = new Set<string>();
  return domain.lookup.every((point) => Number.isSafeInteger(point.raw) && rawIndex(domain, point.raw) !== null
    && decimal(point.display) !== null && !raws.has(point.raw) && !displays.has(point.display)
    && (raws.add(point.raw), displays.add(point.display), true));
}

/** Returns a canonical display value, or null when the domain/input is unusable. */
export function decodeControlDomain(domain: ControlDomain, raw: number): ExactDecimal | null {
  const values = axis(domain);
  const index = rawIndex(domain, raw);
  if (!values || index === null) return null;
  if (domain.mapping === 'identity') return isIdentity(domain, values) ? text([BigInt(raw), 0]) : null;
  if (domain.mapping === 'lookup') {
    const matches = domain.lookup.filter((point) => point.raw === raw && decimal(point.display) && inAxis(decimal(point.display)!, values));
    return matches.length === 1 ? matches[0]!.display : null;
  }
  if (domain.mapping === 'linear') {
    const indices = scalarIndex(domain);
    const originIndex = lattice(values[3], values[0], values[2]);
    if (!indices || originIndex === null || indices[1] - indices[0] !== originIndex || !sameCardinality(domain, values)) return null;
    const result = add(values[3], index, values[2]);
    return inAxis(result, values) ? text(result) : null;
  }
  if (domain.mapping === 'centered') {
    const center = rawIndex(domain, domain.raw_center);
    const displayCenter = decimal(domain.display_center);
    if (center === null || !displayCenter || !inAxis(displayCenter, values) || !sameCardinality(domain, values)) return null;
    const result = add(displayCenter, index - center, values[2]);
    return inAxis(result, values) ? text(result) : null;
  }
  return null;
}

/** Applies the domain's declared display lattice posture without numeric coercion. */
export function quantizeControlDomain(domain: ControlDomain, display: ExactDecimal): ExactDecimal | null {
  const values = axis(domain);
  const value = decimal(display);
  if (!values || !value) return null;
  const [item, origin, step] = align([value, values[3], values[2]]);
  const difference = item - origin;
  const quotient = floorDivide(difference, step);
  const remainder = difference - quotient * step;
  let index: bigint;
  switch (domain.quantization as ControlQuantization) {
    case 'floor': index = quotient; break;
    case 'ceil': index = remainder === 0n ? quotient : quotient + 1n; break;
    case 'reject': if (remainder !== 0n) return null; index = quotient; break;
    case 'nearest_ties_down': index = remainder * 2n > step ? quotient + 1n : quotient; break;
    case 'nearest_ties_up': index = remainder * 2n >= step ? quotient + 1n : quotient; break;
    default: return null;
  }
  const result = add(values[3], index, values[2]);
  return inAxis(result, values) ? text(result) : null;
}

/** Inverts an exactly restorable domain; unavailable restoration deliberately returns null. */
export function encodeControlDomain(domain: ControlDomain, display: ExactDecimal): number | null {
  if (!domainIsExact(domain)) return null;
  const quantized = quantizeControlDomain(domain, display);
  const values = axis(domain);
  if (!quantized || !values) return null;
  if (domain.mapping === 'identity') {
    if (!isIdentity(domain, values)) return null;
    const value = decimal(quantized);
    if (!value || value[1] !== 0 || value[0] < BigInt(domain.raw_min) || value[0] > BigInt(domain.raw_max)) return null;
    const raw = Number(value[0]);
    return rawIndex(domain, raw) === null ? null : raw;
  }
  if (domain.mapping === 'lookup') {
    const matches = domain.lookup.filter((point) => point.display === quantized);
    return matches.length === 1 ? matches[0]!.raw : null;
  }
  const target = decimal(quantized)!;
  let raw: bigint;
  if (domain.mapping === 'linear') {
    const indices = scalarIndex(domain);
    const originIndex = lattice(values[3], values[0], values[2]);
    const displayIndex = lattice(target, values[3], values[2]);
    if (!indices || originIndex === null || displayIndex === null || indices[1] - indices[0] !== originIndex || !sameCardinality(domain, values)) return null;
    raw = BigInt(domain.raw_origin) + displayIndex * BigInt(domain.raw_step);
  } else if (domain.mapping === 'centered') {
    const center = rawIndex(domain, domain.raw_center);
    const displayCenter = decimal(domain.display_center);
    if (center === null || !displayCenter || !sameCardinality(domain, values)) return null;
    const offset = lattice(target, displayCenter, values[2]);
    if (offset === null) return null;
    raw = BigInt(domain.raw_center) + offset * BigInt(domain.raw_step);
  } else return null;
  const number = Number(raw);
  return Number.isSafeInteger(number) && rawIndex(domain, number) !== null && decodeControlDomain(domain, number) === quantized ? number : null;
}
