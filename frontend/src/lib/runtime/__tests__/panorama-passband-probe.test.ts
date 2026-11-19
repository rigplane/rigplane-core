import { describe, expect, it } from 'vitest';
import { passbandGaps, requireProbeStimulus } from '../../../../tests/e2e/journey/panorama-passband-probe';

describe('passband probe evidence', () => {
  it('rejects no-op tuning and no-op zoom witnesses', () => {
    expect(() => requireProbeStimulus(100, 100, 'up')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 100, 'down')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 101, 'up')).not.toThrow();
    expect(() => requireProbeStimulus(100, 50, 'down')).not.toThrow();
  });
  const sample = (t: number, overlay: boolean, kind: 'frame' | 'dom' = 'frame') =>
    ({ t, kind, overlay, gates: null, state: null });
  it('rejects an empty interval or one with no state before it instead of passing', () => {
    expect(() => passbandGaps([], 0, 1000)).toThrow('recorded nothing');
    // The sampler only started inside the window: nothing says what the page showed entering it.
    expect(() => passbandGaps([sample(500, true)], 0, 1000)).toThrow('recorded nothing');
  });
  it('does not call a page that merely did not paint a blink', () => {
    // Overlay present entering the window, then no frame and no DOM change for the whole window.
    expect(passbandGaps([sample(-10, true)], 0, 1000)).toEqual([]);
    // Overlay removed by a DOM change and restored by one, with no frame painted in between.
    const unseen = passbandGaps([sample(-10, true), sample(100, false, 'dom'), sample(900, true, 'dom')], 0, 1000);
    expect(unseen).toHaveLength(1);
    expect(unseen[0]).toMatchObject({ frames: 0, durationMs: 800 });
  });
  it('still reports an overlay that stayed absent across painted frames', () => {
    const samples = [sample(-10, true), sample(100, false, 'dom'), sample(116, false), sample(133, false), sample(150, true)];
    expect(passbandGaps(samples, 0, 1000)).toMatchObject([{ frames: 2, durationMs: 50 }]);
  });
  it('carries an absent overlay entering the window as a gap that starts at the window', () => {
    expect(passbandGaps([sample(-10, false), sample(200, true, 'dom')], 0, 1000)).toMatchObject([{ frames: 0, startMs: 0, durationMs: 200 }]);
  });
});
