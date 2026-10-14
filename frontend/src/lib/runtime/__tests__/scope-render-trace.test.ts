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
  afterEach(() => { vi.useRealTimers(); });

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
});
