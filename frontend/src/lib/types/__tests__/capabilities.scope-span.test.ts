import { expect, it } from 'vitest';
import { validateCapabilities } from '../capabilities';
import fixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';

it('parses producer span choices and rejects invalid widths/order', () => {
  const caps = validateCapabilities(structuredClone(fixture));
  expect(caps.span_choices_hz).toEqual([2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000]);
  for (const invalid of [[0], [NaN], ['10000'], [5000, 2500], [2500, 2500], null]) {
    expect(() => validateCapabilities({ ...fixture, span_choices_hz: invalid })).toThrow(/span_choices_hz/);
  }
});
