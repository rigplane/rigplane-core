import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { URL } from 'node:url';
import { createContext, runInContext } from 'node:vm';
import ts from 'typescript';
import { installSampler, stopSampler, missingLivePassband, paintedOutages, passbandGaps, passbandProbeReady, requireProbeStimulus } from '../../../../tests/e2e/journey/panorama-passband-probe';
import type { ScopePassbandDiagnostics } from '../adapters/scope-passband-display';

describe('passband sampler and renderer work', () => {
  for (const activeRenderer of [false, true]) {
    it(`quiesces the sampler while ${activeRenderer ? 'still rejecting a continuous' : 'accepting an idle'} renderer`, async () => {
      const queued = new Map<number, FrameRequestCallback>();
      let nextId = 0;
      let observing = false;
      const context = createContext({
        performance: { now: () => 0 },
        requestAnimationFrame: (callback: FrameRequestCallback) => {
          const id = ++nextId;
          queued.set(id, callback);
          return id;
        },
        cancelAnimationFrame: (id: number) => queued.delete(id),
        document: { querySelector: () => null },
        HTMLElement: class {},
        MutationObserver: class {
          observe() { observing = true; }
          disconnect() { observing = false; }
        },
        WebGL2RenderingContext: class { texImage2D() {} drawArrays() {} },
        CanvasRenderingContext2D: class { fillText() {} },
      });
      context.window = context;
      const execute = (callback: unknown) => runInContext(ts.transpileModule(`(${String(callback)})();`, {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
      }).outputText, context);
      // Execute the journey's work instrumentation with the sampler.
      const path = new URL('../../../../tests/e2e/journey/panorama-settle.spec.ts', import.meta.url);
      const source = ts.createSourceFile(path.pathname, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true);
      let instrumentation: ts.Node | undefined;
      const visit = (node: ts.Node) => {
        if (!instrumentation && ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
          && node.expression.name.text === 'addInitScript') instrumentation = node.arguments[0];
        ts.forEachChild(node, visit);
      };
      visit(source);
      expect(instrumentation).toBeDefined();
      execute(instrumentation!.getText(source));
      const page = { addInitScript: execute, evaluate: execute };
      await installSampler(page as never);
      const advanceFrames = () => {
        for (let frame = 0; frame < 20; frame++) {
          const callbacks = [...queued.values()];
          queued.clear();
          for (const callback of callbacks) callback(frame * 16.67);
        }
      };
      advanceFrames();
      expect(context.passbandSamples).toHaveLength(20);
      expect(context.panoramaWork.raf).toBe(20);
      expect(observing).toBe(true);
      if (activeRenderer) runInContext(`
        function render() { requestAnimationFrame(render); }
        requestAnimationFrame(render);
      `, context);
      await stopSampler(page as never);
      expect(observing).toBe(false);
      expect(queued.size).toBe(activeRenderer ? 1 : 0);
      const start = context.panoramaWork.raf as number;
      advanceFrames();
      const idleRaf = (context.panoramaWork.raf as number) - start;
      expect(idleRaf).toBe(activeRenderer ? 20 : 0);
      expect(idleRaf <= 4).toBe(!activeRenderer);
      expect(context.passbandSamples).toHaveLength(20);
      expect(queued.size).toBe(activeRenderer ? 1 : 0);
    });
  }
});

describe('passband probe evidence', () => {
  it('rejects no-op tuning and no-op zoom witnesses', () => {
    expect(() => requireProbeStimulus(100, 100, 'up')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 100, 'down')).toThrow('stimulus');
    expect(() => requireProbeStimulus(100, 101, 'up')).not.toThrow();
    expect(() => requireProbeStimulus(100, 50, 'down')).not.toThrow();
  });
  const sample = (t: number, overlay: boolean, kind: 'frame' | 'dom' = 'frame') =>
    ({ t, kind, overlay, gates: null, state: null });
  const current = (t: number, tupleFrameMode: number, kind: 'frame' | 'dom' = 'frame') => ({
    ...sample(t, true, kind), state: 'current',
    gates: {
      tuneVisible: true, confirmed: true, freqHz: 14_250_000,
      viewStartHz: 14_200_000, viewEndHz: 14_300_000, inView: true, proportional: true,
      mode: 'LSB', widthHz: 3000, shiftHz: 0, tuple: true, tupleState: 'current',
      tupleFrameMode, tupleReason: null, tupleStale: false, passbandHz: 3000,
      spanPositive: true, overlay: true,
    } satisfies ScopePassbandDiagnostics,
  });
  it('starts FIX continuity only after its current tuple has painted', () => {
    const samples = [current(-50, 0), sample(-30, false), sample(-20, false), sample(-10, false),
      current(-5, 1, 'dom')];
    expect(samples.at(-1)).toMatchObject({ overlay: true, gates: { proportional: true, tupleState: 'current' } });
    // DOM recovery has not closed the painted setup outage.
    expect(passbandGaps(samples, 0, 10)).toMatchObject([{ frames: 3 }]);
    expect(passbandProbeReady(samples, 1)).toBe(false);
    samples.push(current(5, 1));
    expect(passbandProbeReady(samples, 1)).toBe(true);
    expect(passbandGaps(samples, 6, 10)).toEqual([]);
    samples.push(sample(7, false), sample(8, false), sample(9, false));
    expect(passbandGaps(samples, 6, 10)).toMatchObject([{ frames: 3 }]);
  });
  it('rejects a painted CENTER tuple and a newer loss after a good FIX frame', () => {
    expect(passbandProbeReady([], 1)).toBe(false);
    expect(passbandProbeReady([current(0, 0)], 1)).toBe(false);
    expect(passbandProbeReady([current(0, 1), sample(1, false, 'dom')], 1)).toBe(false);
    expect(passbandProbeReady([current(0, 1), current(1, 0, 'dom')], 1)).toBe(false);
    expect(passbandProbeReady([current(0, 1), current(1, 1, 'dom')], 1)).toBe(true);
  });
  it('detects a live absence carried into the interval when recovery is the next sample', () => {
    const absent = {
      ...sample(-5, false),
      gates: {
        tuneVisible: true, confirmed: true, freqHz: 14_050_000, viewStartHz: 14_000_000, viewEndHz: 14_100_000,
        inView: true, proportional: false, mode: 'USB', widthHz: 2400, shiftHz: 0, tuple: false,
        tupleState: 'unknown' as const, tupleFrameMode: null, tupleReason: 'frame-unavailable', tupleStale: false,
        passbandHz: 0, spanPositive: true, overlay: false,
      },
    };
    expect(missingLivePassband([sample(-10, false), absent, sample(200, true)], 0, 200)).toBe(absent);
    const offline = { ...absent, gates: { ...absent.gates, spanPositive: false } };
    expect(missingLivePassband([absent, offline, sample(200, true)], 0, 200)).toBeUndefined();
    expect(missingLivePassband([absent, sample(0, true)], 0, 200)).toBeUndefined();
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
