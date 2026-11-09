export interface ScopeWriteReceipt { sub: number; data: number[]; span: number }

export function parseScopeWriteReceipt(line: string): ScopeWriteReceipt {
  const value: unknown = JSON.parse(line);
  if (typeof value !== 'object' || value === null) throw new Error('invalid scope receipt');
  const record = value as Record<string, unknown>;
  const byte = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 255;
  if (!byte(record.sub) || !Array.isArray(record.data) || !record.data.every(byte)
    || typeof record.span !== 'number' || !Number.isSafeInteger(record.span) || record.span <= 0) {
    throw new Error('invalid scope receipt');
  }
  return { sub: record.sub, data: record.data, span: record.span };
}
