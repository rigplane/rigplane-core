import type { ScopeFrame, ScopeGeometry } from '$lib/runtime/scope-frame';

export type LcdSpectrumSource = 'audio-fft' | 'hardware';
export type LcdSpectrumReceiver = 'MAIN' | 'SUB';
export type LcdSpectrumFreshness = 'fresh' | 'stale';

/**
 * MOR-4270: the LCD frame carries the typed pixel payload (a view over the
 * wire buffer, never normalized bins) plus the qualified geometry facts.
 */
export interface LcdSpectrumFrame {
  readonly state?: 'out-of-range';
  readonly source: LcdSpectrumSource;
  readonly receiver: LcdSpectrumReceiver;
  readonly freshness: LcdSpectrumFreshness;
  readonly startHz: number;
  readonly endHz: number;
  readonly pixels: Uint8Array | null;
}

export interface LcdSpectrumFrameExpectation {
  readonly source: LcdSpectrumSource;
  readonly receiver: LcdSpectrumReceiver | null;
}

export type LcdSpectrumFrameGhostReason =
  | 'missing'
  | 'stale'
  | 'source-mismatch'
  | 'receiver-unknown'
  | 'receiver-mismatch'
  | 'invalid';

export type LcdSpectrumFrameResolution =
  | { readonly state: 'live'; readonly frame: LcdSpectrumFrame }
  | { readonly state: 'out-of-range'; readonly frame: LcdSpectrumFrame }
  | { readonly state: 'ghost'; readonly reason: LcdSpectrumFrameGhostReason };

/** Project the typed frame + qualified geometry into the LCD display frame. */
export function toLcdSpectrumFrame(frame: ScopeFrame, geometry: ScopeGeometry): LcdSpectrumFrame | null {
  const source: LcdSpectrumSource | null = geometry.source === 'radio' ? 'hardware'
    : geometry.source === 'audio_fft' ? 'audio-fft' : null;
  const receiver: LcdSpectrumReceiver | null = geometry.receiver === 0 ? 'MAIN'
    : geometry.receiver === 1 ? 'SUB' : null;
  if (source === null || receiver === null) return null;
  return Object.freeze({
    ...(geometry.outOfRange && !geometry.hasPixels ? { state: 'out-of-range' as const } : {}),
    source,
    receiver,
    freshness: geometry.freshness,
    startHz: geometry.view.startHz,
    endHz: geometry.view.endHz,
    // SDR TX freeze also sets the flag but retains the last live pixels.
    pixels: geometry.hasPixels ? frame.pixels : null,
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasKnownQualifiers(value: Record<string, unknown>): boolean {
  return (value.source === 'audio-fft' || value.source === 'hardware')
    && (value.receiver === 'MAIN' || value.receiver === 'SUB')
    && (value.freshness === 'fresh' || value.freshness === 'stale');
}

function hasValidGeometry(value: Record<string, unknown>): boolean {
  if (typeof value.startHz !== 'number' || !Number.isFinite(value.startHz)
    || typeof value.endHz !== 'number' || !Number.isFinite(value.endHz)
    || value.endHz <= value.startHz) return false;
  if (value.state === 'out-of-range') return true;
  return value.pixels instanceof Uint8Array && value.pixels.length >= 2;
}

export function resolveLcdSpectrumFrame(
  input: unknown,
  expectation: LcdSpectrumFrameExpectation,
): LcdSpectrumFrameResolution {
  if (expectation.receiver === null) return { state: 'ghost', reason: 'receiver-unknown' };
  if (input === undefined || input === null) return { state: 'ghost', reason: 'missing' };
  if (!isRecord(input) || !hasKnownQualifiers(input)) {
    return { state: 'ghost', reason: 'invalid' };
  }
  if (input.source !== expectation.source) return { state: 'ghost', reason: 'source-mismatch' };
  if (input.receiver !== expectation.receiver) {
    return { state: 'ghost', reason: 'receiver-mismatch' };
  }
  if (input.freshness !== 'fresh') return { state: 'ghost', reason: 'stale' };
  if (!hasValidGeometry(input)) return { state: 'ghost', reason: 'invalid' };

  return { state: input.state === 'out-of-range' ? 'out-of-range' : 'live',
    frame: input as unknown as LcdSpectrumFrame };
}
