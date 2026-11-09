import receipt from '../../../../../tests/fixtures/panorama-receipt.json';
import { expect, it } from 'vitest';
import { parseScopeWriteReceipt } from '../../../../tests/e2e/journey/panorama-receipt';

it('parses the shared Python receipt and rejects malformed exchange records', () => {
  for (const item of receipt) expect(parseScopeWriteReceipt(JSON.stringify(item))).toEqual(item);
  const wire = JSON.stringify(receipt.at(-1));
  for (const invalid of ['{}', wire.replace('100000', 'true'), wire.replace('[0,', '[256,')]) {
    expect(() => parseScopeWriteReceipt(invalid)).toThrow();
  }
});
