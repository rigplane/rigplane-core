import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScopeRenderTrace } from '../scope-render-trace';
import type { ScopeFrame } from '../adapters/scope-adapter';

const frame: ScopeFrame = Object.freeze({ receiver: 0, mode: 0, startFreq: 1, endFreq: 2, pixels: new Uint8Array(2049) });
const receipt = (sequence: number, wireSequence: number, transportEpoch = 1) => ({
  kind: 'accepted' as const, frame, token: { source: 'audio_fft' as const, transportEpoch, wireSequence, acceptedSequence: sequence },
  callbackAt: performance.now(), acceptedAt: performance.now(),
});

describe('ScopeRenderTrace', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it('stores only scalar stage data and joins an actual odd-length audio frame without retaining its payload in export', () => {
    const trace = new ScopeRenderTrace();
    trace.observe(receipt(1, 65535));
    const token = trace.tokenForFrame(frame);
    expect(token).toMatchObject({ source: 'audio_fft', wireSequence: 65535, acceptedSequence: 1 });
    trace.observe(receipt(2, 0));
    expect(trace.tokenForAcceptedSequence(2, 'audio_fft')?.wireSequence).toBe(0);
    trace.record('spectrum_render', token, 4, true);
    trace.stop();
    const exported = trace.export()!;
    expect(JSON.parse(exported).counts).toMatchObject({ accepted: 2, wireGaps: 0 });
    expect(exported).not.toContain('pixels');
    expect(exported).not.toContain('startFreq');
    expect(exported).not.toContain('endFreq');
    expect(trace.export()).toBe(exported);
    expect(trace.tokenForFrame(frame)).toBeNull();
  });

  it('ends at the hard deadline, removes timers, and ignores later observations', () => {
    const stopped = vi.fn();
    const trace = new ScopeRenderTrace(stopped);
    trace.observe(receipt(1, 1));
    vi.advanceTimersByTime(120_000);
    expect(trace.status).toBe('stopped');
    expect(trace.reason).toBe('deadline');
    expect(stopped).toHaveBeenCalledTimes(1);
    const frozen = trace.export();
    trace.observe(receipt(2, 2));
    trace.record('lag', null, 100);
    expect(trace.export()).toBe(frozen);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('stops on hidden and never resumes on visibility return', () => {
    const trace = new ScopeRenderTrace();
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(trace.reason).toBe('hidden');
    expect(vi.getTimerCount()).toBe(0);
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(trace.status).toBe('stopped');
  });

  it('bounds burst retention while preserving coarse counts and reports dropped details', () => {
    const trace = new ScopeRenderTrace();
    for (let i = 0; i < 2_200; i++) trace.record('coalesced', null, 0, true);
    trace.stop();
    const output = JSON.parse(trace.export()!);
    expect(output.counts.burstDropped).toBe(152);
    expect(output.burst).toHaveLength(2_048);
    expect(output.buckets[0].stages.coalesced.count).toBe(2_200);
    expect(new TextEncoder().encode(trace.export()!).byteLength).toBeLessThanOrEqual(16 * 1024 * 1024);
  });

  it('keeps full chronology opt-in and correlates ordinary frames across wrap', () => {
    const ordinary = new ScopeRenderTrace();
    ordinary.observe(receipt(1, 1)); ordinary.stop();
    expect(JSON.parse(ordinary.export()!)).not.toHaveProperty('timeline');
    const detailed = new ScopeRenderTrace(undefined, undefined, 'detailed', { captureTimeline: true });
    for (const [accepted, wire] of [[1, 65535], [2, 0]]) {
      detailed.observe(receipt(accepted, wire));
      detailed.record('fanout', detailed.tokenForAcceptedSequence(accepted, 'audio_fft'));
    }
    detailed.recordGeometry('spectrum', 1024, 180, 2048, 360, 2);
    detailed.recordGeometry('spectrum', 1000, 180, 2000, 360, 2);
    detailed.stop();
    const output = JSON.parse(detailed.export()!);
    expect(output.timeline.map((e: { stage: string }) => e.stage)).toEqual([
      'callback', 'accepted', 'fanout', 'callback', 'accepted', 'fanout', 'geometry', 'geometry',
    ]);
    expect(output.timeline.filter((e: { stage: string }) => e.stage === 'callback'))
      .toMatchObject([{ wireSequence: 65535, acceptedSequence: 1, transportEpoch: 1 },
        { wireSequence: 0, acceptedSequence: 2, transportEpoch: 1 }]);
    expect(output.timeline.at(-1)).toMatchObject({ canvas: 'spectrum', cssWidth: 1000 });
    expect(output.counts.wireGaps).toBe(0);
    expect(detailed.export()).not.toContain('pixels');
  });

  it.each(['stage', 'geometry'])('fails closed at the detailed %s cap instead of evicting history', (kind) => {
    const stopped = vi.fn();
    const trace = new ScopeRenderTrace(stopped, undefined, 'bounded', { captureTimeline: true });
    for (let i = 0; i < 32_769; i++) {
      if (kind === 'stage') trace.record('fanout');
      else trace.recordGeometry('spectrum', 1024, 180, 2048, 360, 2);
    }
    expect(trace.status).toBe('overflow');
    expect(trace.reason).toBe('overflow');
    const output = JSON.parse(trace.export()!);
    expect(output.timeline).toHaveLength(32_768);
    expect(output.timelineOverflow).toBe(true);
    expect(stopped).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    expect(new TextEncoder().encode(trace.export()!).byteLength).toBeLessThan(16 * 1024 * 1024);
  });

  it('seals detailed capture at the existing deadline and keeps geometry within its cap', () => {
    const trace = new ScopeRenderTrace(undefined, undefined, 'deadline', { captureTimeline: true });
    trace.observe(receipt(1, 4));
    vi.advanceTimersByTime(120_000);
    expect(trace.reason).toBe('deadline');
    const frozen = trace.export();
    trace.recordGeometry('spectrum', 1, 1, 1, 1, 1);
    trace.observe(receipt(2, 5));
    expect(trace.export()).toBe(frozen);
    expect(JSON.parse(frozen!).timelineOverflow).toBe(false);
  });
  it('records document observations through hidden delivery and a callback before return notification', () => {
    let now = 0;
    let hidden = false;
    let focused = true;
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => hidden ? 'hidden' : 'visible');
    vi.spyOn(document, 'hasFocus').mockImplementation(() => focused);
    const trace = new ScopeRenderTrace(undefined, { now: () => now, setTimeout, clearTimeout, setInterval, clearInterval },
      'transition', { captureTransition: true });
    const deliver = (sequence: number) => {
      trace.observe({ ...receipt(sequence, sequence), callbackAt: now, acceptedAt: now });
      trace.record('raf', trace.tokenForAcceptedSequence(sequence, 'audio_fft'));
    };
    now = 10; deliver(1);
    now = 20; focused = false; window.dispatchEvent(new Event('blur'));
    now = 30; hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    expect(trace.status).toBe('recording');
    now = 40; deliver(2);
    now = 50; hidden = false; deliver(3); // State can change before the notification is delivered.
    now = 60; document.dispatchEvent(new Event('visibilitychange'));
    now = 70; focused = true; window.dispatchEvent(new Event('focus'));
    trace.recordGeometry('spectrum', 100, 50, 200, 100, 2);
    trace.stop();
    const report = JSON.parse(trace.export()!);
    expect(report.transition).toEqual({ mode: 'document-transition', visibilityProvenance: 'document-observed' });
    expect(report.timeline.filter((e: { stage: string }) => e.stage === 'visibility')).toMatchObject([
      { atMs: 0, document: { hidden: false, visibilityState: 'visible', hasFocus: true } },
      { atMs: 30, document: { hidden: true, visibilityState: 'hidden', hasFocus: false } },
      { atMs: 60, document: { hidden: false, visibilityState: 'visible', hasFocus: false } },
    ]);
    for (const stage of ['callback', 'raf']) {
      expect(report.timeline.filter((e: { stage: string }) => e.stage === stage)).toMatchObject([
        { atMs: 10, acceptedSequence: 1, wireSequence: 1, transportEpoch: 1, document: { hidden: false } },
        { atMs: 40, acceptedSequence: 2, wireSequence: 2, transportEpoch: 1, document: { hidden: true } },
        { atMs: 50, acceptedSequence: 3, wireSequence: 3, transportEpoch: 1, document: { hidden: false, hasFocus: false } },
      ]);
    }
    expect(report.timeline.every((e: { document?: unknown }) => e.document !== undefined)).toBe(true);
    expect(report.timeline.filter((e: { stage: string }) => ['blur', 'focus'].includes(e.stage)))
      .toMatchObject([{ document: { hasFocus: false } }, { document: { hasFocus: true } }]);
    expect(report.counts.accepted).toBe(3);
    expect(vi.getTimerCount()).toBe(0);
    window.dispatchEvent(new Event('blur'));
    hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    expect(trace.export()).toBe(JSON.stringify(report));
  });

  it('can explicitly start while hidden without changing the ordinary hidden constructor', () => {
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const ordinary = new ScopeRenderTrace();
    expect(ordinary.reason).toBe('hidden');
    expect(JSON.parse(ordinary.export()!)).not.toHaveProperty('transition');
    const trace = new ScopeRenderTrace(undefined, undefined, undefined, { captureTransition: true });
    expect(trace.status).toBe('recording');
    trace.dispose();
    expect(JSON.parse(trace.export()!).timeline[0]).toMatchObject({ stage: 'visibility', document: { hidden: true } });
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(['visibility', 'callback', 'stage', 'geometry', 'focus', 'blur', 'lag', 'stop'])(
    'checks the observation deadline before overdue %s when timers were suspended', (event) => {
      let now = 0;
      const trace = new ScopeRenderTrace(undefined, { now: () => now, setTimeout, clearTimeout, setInterval, clearInterval },
        'late', { captureTransition: true });
      trace.observe({ ...receipt(1, 1), callbackAt: 0, acceptedAt: 0 });
      now = 120_001;
      if (event === 'visibility') document.dispatchEvent(new Event('visibilitychange'));
      if (event === 'callback') trace.observe({ ...receipt(2, 2), callbackAt: 1, acceptedAt: 2 });
      if (event === 'stage') trace.record('raf', null, 0, false, 1);
      if (event === 'geometry') trace.recordGeometry('spectrum', 1, 1, 1, 1, 1);
      if (event === 'focus' || event === 'blur') window.dispatchEvent(new Event(event));
      if (event === 'lag') vi.advanceTimersByTime(100);
      if (event === 'stop') trace.stop();
      expect(trace.reason).toBe('deadline');
      const report = JSON.parse(trace.export()!);
      expect(report.counts.accepted).toBe(1);
      expect(report.timeline.map((e: { stage: string }) => e.stage)).toEqual(['visibility', 'callback', 'accepted']);
      expect(report.buckets.length).toBeLessThanOrEqual(121);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it.each(['source', 'epoch'] as const)('stops transition capture on %s change before accepting a new identity', (change) => {
    const trace = new ScopeRenderTrace(undefined, undefined, undefined, { captureTransition: true });
    trace.observe(receipt(1, 1));
    const next = receipt(2, 2, change === 'epoch' ? 2 : 1);
    trace.observe({ ...next, token: { ...next.token, source: change === 'source' ? 'hardware' : 'audio_fft' } });
    expect(trace.reason).toBe('source_changed');
    expect(JSON.parse(trace.export()!).counts.accepted).toBe(1);
    expect(trace.tokenForAcceptedSequence(1, 'audio_fft')).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('bounds transition history and the token index without silently dropping timeline events', () => {
    const trace = new ScopeRenderTrace(undefined, undefined, undefined, { captureTransition: true });
    for (let i = 1; i <= 513; i++) trace.observe(receipt(i, i));
    expect(trace.tokenForAcceptedSequence(1, 'audio_fft')).toBeNull();
    expect(trace.tokenForAcceptedSequence(513, 'audio_fft')?.wireSequence).toBe(513);
    for (let i = 0; i < 32_768; i++) trace.record('raf', null, 0, true);
    const report = JSON.parse(trace.export()!);
    expect(report.reason).toBe('overflow');
    expect(report.timelineOverflow).toBe(true);
    expect(report.timeline).toHaveLength(32_768);
    expect(report.burst).toHaveLength(2_048);
    expect(report.buckets.length).toBeLessThanOrEqual(121);
    expect(new TextEncoder().encode(trace.export()!).byteLength).toBeLessThanOrEqual(16 * 1024 * 1024);
    expect(vi.getTimerCount()).toBe(0);
  });

});
