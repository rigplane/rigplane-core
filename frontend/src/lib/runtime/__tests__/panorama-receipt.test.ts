import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { parseScopeWriteReceipt } from '../../../../tests/e2e/journey/panorama-receipt';

it('parses the shared Python receipt and rejects malformed exchange records', () => {
  const wire = readFileSync('../tests/fixtures/panorama-receipt.json', 'utf8');
  expect(parseScopeWriteReceipt(wire)).toEqual({ sub: 21, data: [0, 0, 0, 16, 0, 0], span: 100000 });
  for (const invalid of ['{}', wire.replace('100000', 'true'), wire.replace('[0,', '[256,')]) {
    expect(() => parseScopeWriteReceipt(invalid)).toThrow();
  }
});
