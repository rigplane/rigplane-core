import { describe, expect, it } from 'vitest';
import { missingLivePassband, paintedOutages, passbandGaps, requireProbeStimulus, scopeOutages } from '../../../../tests/e2e/journey/panorama-passband-probe';

describe('passband probe evidence', () => {
  it('rejects no-op tuning and no-op zoom witnesses', () => {
    expect(() => requireProbeStimulus(100, 100, 'up')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 100, 'down')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 101, 'up')).not.toThrow();
    expect(() => requireProbeStimulus(100, 50, 'down')).not.toThrow();
  });
  const sample = (t: number, overlay: boolean, kind: 'frame' | 'dom' = 'frame') =>
    ({ t, kind, overlay, gates: null, state: null });
  it('uses the sampled state at the window boundary for overlay gaps and scope outages', () => {
    const absent = {
      ...sample(-5, false),
      gates: {
        tuneVisible: true, confirmed: true, freqHz: 14_050_000, viewStartHz: 14_000_000, viewEndHz: 14_100_000,
        inView: true, proportional: false, mode: 'USB', widthHz: 2400, shiftHz: 0, tuple: false,
        tupleState: 'unknown' as const, tupleReason: 'frame-unavailable', tupleStale: false,
        scopeFrameLive: true, scopeFreshness: 'fresh' as const, scopeBindingEnded: false,
        controlLinkLost: false, scopeConnectionEpoch: 1,
        passbandHz: 0, spanPositive: true, overlay: false,
      },
    };
    expect(missingLivePassband([sample(-10, false), absent, sample(200, true)], 0, 200)).toBe(absent);
    const offline = { ...absent, gates: { ...absent.gates, spanPositive: false } };
    expect(missingLivePassband([absent, offline, sample(200, true)], 0, 200)).toBeUndefined();
    expect(missingLivePassband([absent, sample(0, true)], 0, 200)).toBeUndefined();
    const live = { ...absent, overlay: true };
    const carried = { ...offline, t: -1 };
    const domOnly = { ...offline, t: 30, kind: 'dom' as const };
    expect(scopeOutages([
      live, carried, { ...offline, t: 10 }, { ...live, t: 20 }, domOnly,
      { ...live, t: 40 }, { ...offline, t: 60 },
    ], 0, 50)).toEqual([carried, domOnly]);
    expect(scopeOutages([carried, { ...live, t: 0 }], 0, 50)).toEqual([]);
    expect(scopeOutages([live, sample(10, false)], 0, 50)).toHaveLength(1);
  });
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
  it('carries a painted absence entering the window as a gap starting at the window', () => {
    const gaps = passbandGaps([sample(-10, false), sample(200, true)], 0, 1000);
    expect(gaps).toMatchObject([{ frames: 1, startMs: 0, durationMs: 200 }]);
    // The last sample before the window was a frame that saw it absent, and the next frame
    // inside the window sees it absent too: two painted frames.
    expect(passbandGaps([sample(-10, false), sample(16, false), sample(40, true)], 0, 1000))
      .toMatchObject([{ frames: 2, startMs: 0, durationMs: 40 }]);
    expect(paintedOutages(passbandGaps([sample(-10, false), sample(300, true)], 0, 1000), 200)).toHaveLength(1);
  });
  it('carries an unpainted absence entering the window, and it is never a painted outage', () => {
    const gaps = passbandGaps([sample(-10, true), sample(-5, false, 'dom'), sample(200, true, 'dom')], 0, 1000);
    expect(gaps).toMatchObject([{ frames: 0, startMs: 0, durationMs: 200 }]);
    expect(paintedOutages(gaps, 100)).toEqual([]);
  });
  it('drops a run that closed before the window', () => {
    expect(passbandGaps([sample(-30, false), sample(-20, true), sample(500, true)], 0, 1000)).toEqual([]);
  });
  it('closes an unpainted run before a later painted outage, so neither hides the other', () => {
    const gaps = passbandGaps([
      sample(-10, true), sample(100, false, 'dom'), sample(200, true, 'dom'),
      sample(300, false), sample(316, false), sample(400, true),
    ], 0, 1000);
    expect(gaps).toMatchObject([{ frames: 0, durationMs: 100 }, { frames: 2, durationMs: 100 }]);
    expect(paintedOutages(gaps, 50)).toMatchObject([{ frames: 2, startMs: 300 }]);
  });
  it('closes a painted outage before a later unpainted run', () => {
    const gaps = passbandGaps([
      sample(-10, true), sample(100, false), sample(116, false), sample(130, true),
      sample(200, false, 'dom'), sample(900, true, 'dom'),
    ], 0, 1000);
    expect(gaps).toMatchObject([{ frames: 2, durationMs: 30 }, { frames: 0, durationMs: 700 }]);
    expect(paintedOutages(gaps, 20)).toHaveLength(1);
  });
  it('keeps two absent frames one gap when a DOM-only restoration falls between them', () => {
    const samples = [sample(-10, true), sample(116, false), sample(120, true, 'dom'), sample(125, false, 'dom'), sample(133, false), sample(150, true)];
    const gaps = passbandGaps(samples, 0, 1000);
    expect(gaps).toHaveLength(1);
    expect(gaps[0]).toMatchObject({ frames: 2, durationMs: 34 });
  });
  it('lets only painted outages longer than the limit count as a blink', () => {
    const unpainted = passbandGaps([sample(-10, true), sample(100, false, 'dom'), sample(900, true, 'dom')], 0, 1000);
    expect(paintedOutages(unpainted, 200)).toEqual([]);
    const painted = passbandGaps([sample(-10, true), sample(100, false), sample(400, false), sample(600, true)], 0, 1000);
    expect(paintedOutages(painted, 200)).toHaveLength(1);
    expect(paintedOutages(painted, 600)).toEqual([]);
  });
});
