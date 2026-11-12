import { describe, expect, it } from 'vitest';
import { passbandGaps } from '../../../../tests/e2e/journey/panorama-passband-probe';

describe('passband probe evidence', () => {
  it('rejects an empty or truncated sampling interval instead of passing', () => {
    expect(() => passbandGaps([], 0, 1000)).toThrow('samples');
    expect(() => passbandGaps([{ t: 500, overlay: true, gates: null, state: null }], 0, 1000)).toThrow('samples');
  });
});
