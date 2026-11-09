import receipt from '../../../../../tests/fixtures/panorama-receipt.json';
import { expect, it } from 'vitest';
import { parseScopeWriteReceipt } from '../../../../tests/e2e/journey/panorama-receipt';

it('parses the shared Python receipt and rejects malformed exchange records', () => {
  const wire = JSON.stringify(receipt);
  expect(parseScopeWriteReceipt(wire)).toEqual({ sub: 21, data: [0, 0, 0, 16, 0, 0], span: 100000 });
  for (const invalid of ['{}', wire.replace('100000', 'true'), wire.replace('[0,', '[256,')]) {
    expect(() => parseScopeWriteReceipt(invalid)).toThrow();
  }
});
