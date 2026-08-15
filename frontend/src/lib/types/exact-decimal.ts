declare const exactDecimal: unique symbol;

/** A canonical, lossless fixed-point decimal string. */
export type ExactDecimal = string & { readonly [exactDecimal]: 'ExactDecimal' };

const CANONICAL = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$/;

type Parts = readonly [coefficient: bigint, scale: number];

function parts(value: ExactDecimal): Parts {
  const text = value as unknown as string;
  const negative = text.startsWith('-');
  const unsigned = negative ? text.slice(1) : text;
  const [integer, fraction = ''] = unsigned.split('.');
  return [BigInt(`${negative ? '-' : ''}${integer}${fraction}`), fraction.length];
}

function canonicalize(sign: string, digits: string, scale: number): ExactDecimal {
  const stripped = digits.replace(/^0+/, '') || '0';
  if (stripped === '0') return '0' as ExactDecimal;
  let integer: string;
  let fraction = '';
  if (scale === 0) integer = stripped;
  else if (stripped.length <= scale) {
    integer = '0';
    fraction = `${'0'.repeat(scale - stripped.length)}${stripped}`;
  } else {
    integer = stripped.slice(0, -scale);
    fraction = stripped.slice(-scale);
  }
  fraction = fraction.replace(/0+$/, '');
  return `${sign}${integer}${fraction ? `.${fraction}` : ''}` as ExactDecimal;
}

export function exactDecimalString(value: unknown): ExactDecimal | null {
  if (typeof value !== 'string' || value === '-0' || !CANONICAL.test(value)) return null;
  return value as ExactDecimal;
}

/** Converts a finite legacy JavaScript number without using exponential output. */
export function exactDecimalNumber(value: number): ExactDecimal {
  const text = String(value);
  const match = text.match(/^(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i);
  if (!match) throw new TypeError('finite number has no decimal representation');
  const digits = `${match[2]}${match[3] ?? ''}`;
  const scale = (match[3] ?? '').length - Number(match[4] ?? 0);
  if (scale <= 0) return canonicalize(match[1] ?? '', `${digits}${'0'.repeat(-scale)}`, 0);
  return canonicalize(match[1] ?? '', digits, scale);
}

export function compareExactDecimals(left: ExactDecimal, right: ExactDecimal): number {
  const [leftCoefficient, leftScale] = parts(left);
  const [rightCoefficient, rightScale] = parts(right);
  const scale = Math.max(leftScale, rightScale);
  const leftValue = leftCoefficient * (10n ** BigInt(scale - leftScale));
  const rightValue = rightCoefficient * (10n ** BigInt(scale - rightScale));
  return leftValue === rightValue ? 0 : leftValue < rightValue ? -1 : 1;
}

export function exactDecimalInteger(value: number): ExactDecimal {
  return exactDecimalNumber(value);
}

export function exactDecimalOnLattice(value: ExactDecimal, origin: ExactDecimal, step: ExactDecimal): boolean {
  const items = [parts(value), parts(origin), parts(step)] as const;
  const scale = Math.max(...items.map((item) => item[1]));
  const scaled = items.map(([coefficient, itemScale]) => coefficient * (10n ** BigInt(scale - itemScale)));
  return (scaled[0] - scaled[1]) % scaled[2] === 0n;
}
