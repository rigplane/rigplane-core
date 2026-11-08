/**
 * Tests for scope frame parsing and bandwidth derivation in AmberLcdDisplay.
 *
 * The runtime adapter owns scope-frame parsing. These tests verify that canonical
 * decoder and the bandwidth computation that drives AmberAfScope.
 */
import { describe, it, expect } from 'vitest';
import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { parseCurrentScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import type { ScopeFrame } from '$lib/runtime/scope-frame';

/** Encode a scope frame in the binary wire format. */
function encodeFrame(
  receiver: number,
  startFreq: number,
  endFreq: number,
  pixels: Uint8Array,
): ArrayBuffer {
  return scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver,
    viewStartHz: startFreq, viewEndHz: endFreq, pixels });
}

// ── parseScopeFrame ───────────────────────────────────────────────────────────

const context = { source: 'audio_fft' as const, sourceId: 'audio-scope', epoch: 1 };

describe('parseCurrentScopeFrame', () => {
  it('rejects buffers smaller than the 56-byte header with a typed reason', () => {
    expect(parseCurrentScopeFrame(new ArrayBuffer(15), context))
      .toEqual({ ok: false, reason: 'short header' });
  });

  it('rejects a wrong message-type byte with a typed reason', () => {
    const buf = new ArrayBuffer(56);
    new DataView(buf).setUint8(0, 0x01);
    expect(parseCurrentScopeFrame(buf, context))
      .toEqual({ ok: false, reason: 'message type' });
  });

  it('rejects a declared pixelCount that exceeds the buffer length', () => {
    const pixels = new Uint8Array(10);
    const buf = encodeFrame(0, 14_050_000, 14_074_000, pixels);
    // Truncate so pixelCount (10) extends past byteLength
    expect(parseCurrentScopeFrame(buf.slice(0, -1), context))
      .toEqual({ ok: false, reason: 'pixel length' });
  });

  it('parses receiver, view and pixel facts from a valid frame without copying', () => {
    const pixels = new Uint8Array([10, 20, 30, 40, 50]);
    const buf = encodeFrame(0, 14_050_000, 14_098_000, pixels);
    const result = parseCurrentScopeFrame(buf, context);
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.reason);
    expect(Object.isFrozen(result.frame)).toBe(true);
    expect(result.frame.receiver).toBe(0);
    expect(result.frame.viewStartHz).toBe(14_050_000);
    expect(result.frame.viewEndHz).toBe(14_098_000);
    expect(result.frame.pixels?.buffer).toBe(buf);
    expect(Array.from(result.frame.pixels!)).toEqual([10, 20, 30, 40, 50]);
  });

  it('handles receiver = 1 (sub-receiver)', () => {
    const pixels = new Uint8Array(3);
    const buf = encodeFrame(1, 7_000_000, 7_048_000, pixels);
    const result = parseCurrentScopeFrame(buf, context);
    expect(result.ok && result.frame.receiver).toBe(1);
  });
});

// ── Bandwidth derivation (mirrors the logic in AmberLcdDisplay) ───────────────

/**
 * Derives the FFT data bandwidth in Hz from a parsed frame.
 * This mirrors: fftBandwidth = geometry.view.endHz > geometry.view.startHz
 *               ? endHz - startHz : undefined
 */
function deriveBandwidth(parse: { ok: true; frame: ScopeFrame }): number | undefined {
  const { viewEndHz, viewStartHz } = parse.frame;
  return viewEndHz > viewStartHz ? viewEndHz - viewStartHz : undefined;
}

describe('deriveBandwidth', () => {
  it('returns full sampleRate bandwidth for an uncropped frame', () => {
    const center = 14_074_000;
    const sampleRate = 48_000;
    // Full spectrum: ±24 kHz around center
    const frame = parseCurrentScopeFrame(
      encodeFrame(0, center - sampleRate / 2, center + sampleRate / 2, new Uint8Array(2049)), context,
    );
    if (!frame.ok) throw new Error(frame.reason);
    const parsed = frame;
    expect(deriveBandwidth(frame)).toBe(sampleRate); // 48000 Hz
  });

  it('returns cropped bandwidth for SSB 3600 Hz mode', () => {
    const center = 14_074_000;
    // Use integer Hz values (backend rounds to integer bin boundaries)
    const halfHz = 1764; // 76 bins × ~23 Hz/bin ≈ 1764 Hz
    const frame = parseCurrentScopeFrame(
      encodeFrame(0, center - halfHz, center + halfHz, new Uint8Array(153)), context,
    );
    if (!frame.ok) throw new Error(frame.reason);
    const parsed = frame;
    expect(deriveBandwidth(frame)).toBe(2 * halfHz); // 3528 Hz
    // Should be much less than full 48 kHz
    expect(deriveBandwidth(frame)!).toBeLessThan(4000);
  });

  it('returns cropped bandwidth for AM 10000 Hz mode', () => {
    const center = 7_200_000;
    const halfHz = 4992; // 213 bins × ~23 Hz/bin ≈ 4992 Hz (integer)
    const frame = parseCurrentScopeFrame(
      encodeFrame(0, center - halfHz, center + halfHz, new Uint8Array(427)), context,
    );
    if (!frame.ok) throw new Error(frame.reason);
    const bw = deriveBandwidth(frame)!;
    expect(bw).toBe(2 * halfHz); // 9984 Hz
    expect(bw).toBeGreaterThan(9000);
    expect(bw).toBeLessThan(11000);
  });

  it('reports a typed frequency-range rejection for a zero-span frame', () => {
    const frame = parseCurrentScopeFrame(
      encodeFrame(0, 14_074_000, 14_074_000, new Uint8Array(10)), context,
    );
    expect(frame).toEqual({ ok: false, reason: 'frequency range' });
  });
});

// ── effectiveBandwidth (mirrors the fallback logic in AmberAfScope) ───────────

/**
 * Computes the effective nyquist for bin-to-frequency mapping.
 * This mirrors: effectiveBandwidth = bandwidth ?? sampleRate
 */
function effectiveBandwidth(bandwidth: number | undefined, sampleRate = 48_000): number {
  return bandwidth ?? sampleRate;
}

describe('effectiveBandwidth (bin mapping nyquist)', () => {
  it('uses sampleRate as fallback when no bandwidth is provided', () => {
    expect(effectiveBandwidth(undefined)).toBe(48_000);
  });

  it('uses the frame bandwidth when provided (cropped frame)', () => {
    expect(effectiveBandwidth(3600)).toBe(3600);
  });

  it('passes through full sampleRate when uncropped frame bandwidth matches it', () => {
    expect(effectiveBandwidth(48_000)).toBe(48_000);
  });

  it('nyquist (half bandwidth) for SSB crop is ~1800 Hz', () => {
    // SSB 3600 Hz crop → nyquist = 1800 Hz
    // passbandFrac for 2700 Hz filter = min(1, 2700/1800) = 1.0 → whole frame shown
    const nyquist = effectiveBandwidth(3600) / 2;
    const filterHz = 2700;
    const passbandFrac = Math.min(1, filterHz / nyquist);
    expect(passbandFrac).toBe(1.0);
  });

  it('passbandFrac < 1 when filter narrower than cropped frame', () => {
    // SSB 3600 Hz crop, narrow 1200 Hz filter → shows 1/3 of bins
    const nyquist = effectiveBandwidth(3600) / 2; // 1800
    const filterHz = 1200;
    const passbandFrac = Math.min(1, filterHz / nyquist);
    expect(passbandFrac).toBeCloseTo(1200 / 1800, 5);
    expect(passbandFrac).toBeLessThan(1);
  });

  it('passbandFrac is capped at 1 for full-spectrum frame with narrow filter', () => {
    // Full 48 kHz frame, 2700 Hz SSB filter
    const nyquist = effectiveBandwidth(48_000) / 2; // 24000
    const filterHz = 2700;
    const passbandFrac = Math.min(1, filterHz / nyquist);
    expect(passbandFrac).toBeCloseTo(2700 / 24_000, 5);
    expect(passbandFrac).toBeLessThan(1);
  });
});
