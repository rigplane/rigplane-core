/**
 * Tests for scope frame parsing and bandwidth derivation in AmberLcdDisplay.
 *
 * The runtime adapter owns scope-frame parsing. These tests verify that canonical
 * decoder and the bandwidth computation that drives AmberAfScope.
 */
import { describe, it, expect } from 'vitest';
import { parseScopeFrame, type ScopeFrame } from '$lib/runtime/adapters/scope-adapter';

/** Encode a scope frame in the binary wire format. */
function encodeFrame(
  receiver: number,
  startFreq: number,
  endFreq: number,
  pixels: Uint8Array,
): ArrayBuffer {
  const buf = new ArrayBuffer(16 + pixels.length);
  const view = new DataView(buf);
  view.setUint8(0, 0x01);            // MSG_TYPE_SCOPE
  view.setUint8(1, receiver);
  view.setUint8(2, 0);               // mode (0=CTR default for this test helper)
  view.setUint32(3, startFreq, true);
  view.setUint32(7, endFreq, true);
  // bytes 11-13 reserved
  view.setUint16(14, pixels.length, true);
  new Uint8Array(buf, 16).set(pixels);
  return buf;
}

// ── parseScopeFrame ───────────────────────────────────────────────────────────

describe('parseScopeFrame', () => {
  it('returns null for buffers smaller than the 16-byte header', () => {
    expect(parseScopeFrame(new ArrayBuffer(15))).toBeNull();
  });

  it('returns null when first byte is not 0x01', () => {
    const buf = new ArrayBuffer(20);
    new DataView(buf).setUint8(0, 0x02);
    expect(parseScopeFrame(buf)).toBeNull();
  });

  it('returns null when declared pixelCount exceeds buffer length', () => {
    const pixels = new Uint8Array(10);
    const buf = encodeFrame(0, 14_050_000, 14_074_000, pixels);
    // Truncate so pixelCount (10) extends past byteLength
    expect(parseScopeFrame(buf.slice(0, 20))).toBeNull();
  });

  it('parses immutable receiver, mode, frequency, and pixel facts from a valid frame', () => {
    const pixels = new Uint8Array([10, 20, 30, 40, 50]);
    const buf = encodeFrame(0, 14_050_000, 14_098_000, pixels);
    const frame = parseScopeFrame(buf);
    expect(frame).not.toBeNull();
    expect(Object.isFrozen(frame)).toBe(true);
    expect(frame!.receiver).toBe(0);
    expect(frame!.mode).toBe(0);
    expect(frame!.startFreq).toBe(14_050_000);
    expect(frame!.endFreq).toBe(14_098_000);
    expect(Array.from(frame!.pixels)).toEqual([10, 20, 30, 40, 50]);
  });

  it('handles receiver = 1 (sub-receiver)', () => {
    const pixels = new Uint8Array(3);
    const buf = encodeFrame(1, 7_000_000, 7_048_000, pixels);
    expect(parseScopeFrame(buf)!.receiver).toBe(1);
  });
});

// ── Bandwidth derivation (mirrors the logic in AmberLcdDisplay) ───────────────

/**
 * Derives the FFT data bandwidth in Hz from a parsed frame.
 * This mirrors: fftBandwidth = frame.endFreq > frame.startFreq
 *               ? frame.endFreq - frame.startFreq : undefined
 */
function deriveBandwidth(frame: ScopeFrame): number | undefined {
  return frame.endFreq > frame.startFreq
    ? frame.endFreq - frame.startFreq
    : undefined;
}

describe('deriveBandwidth', () => {
  it('returns full sampleRate bandwidth for an uncropped frame', () => {
    const center = 14_074_000;
    const sampleRate = 48_000;
    // Full spectrum: ±24 kHz around center
    const frame = parseScopeFrame(
      encodeFrame(0, center - sampleRate / 2, center + sampleRate / 2, new Uint8Array(2049)),
    )!;
    expect(deriveBandwidth(frame)).toBe(sampleRate); // 48000 Hz
  });

  it('returns cropped bandwidth for SSB 3600 Hz mode', () => {
    const center = 14_074_000;
    // Use integer Hz values (backend rounds to integer bin boundaries)
    const halfHz = 1764; // 76 bins × ~23 Hz/bin ≈ 1764 Hz
    const frame = parseScopeFrame(
      encodeFrame(0, center - halfHz, center + halfHz, new Uint8Array(153)),
    )!;
    expect(deriveBandwidth(frame)).toBe(2 * halfHz); // 3528 Hz
    // Should be much less than full 48 kHz
    expect(deriveBandwidth(frame)!).toBeLessThan(4000);
  });

  it('returns cropped bandwidth for AM 10000 Hz mode', () => {
    const center = 7_200_000;
    const halfHz = 4992; // 213 bins × ~23 Hz/bin ≈ 4992 Hz (integer)
    const frame = parseScopeFrame(
      encodeFrame(0, center - halfHz, center + halfHz, new Uint8Array(427)),
    )!;
    const bw = deriveBandwidth(frame)!;
    expect(bw).toBe(2 * halfHz); // 9984 Hz
    expect(bw).toBeGreaterThan(9000);
    expect(bw).toBeLessThan(11000);
  });

  it('returns undefined when endFreq <= startFreq (malformed frame)', () => {
    const frame = parseScopeFrame(
      encodeFrame(0, 14_074_000, 14_074_000, new Uint8Array(10)),
    )!;
    expect(deriveBandwidth(frame)).toBeUndefined();
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
