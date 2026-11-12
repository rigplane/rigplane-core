import { describe, expect, it } from 'vitest';
import { passbandGaps, requireProbeStimulus } from '../../../../tests/e2e/journey/panorama-passband-probe';

describe('passband probe evidence', () => {
  it('rejects no-op tuning and no-op zoom witnesses', () => {
    expect(() => requireProbeStimulus(100, 100, 'up')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 100, 'down')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 101, 'up')).not.toThrow();
    expect(() => requireProbeStimulus(100, 50, 'down')).not.toThrow();
  });
  it('rejects an empty or truncated sampling interval instead of passing', () => {
    expect(() => passbandGaps([], 0, 1000)).toThrow('samples');
    expect(() => passbandGaps([{ t: 500, overlay: true, gates: null, state: null }], 0, 1000)).toThrow('samples');
  });
});
