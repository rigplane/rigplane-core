export type LcdSpectrumSource = 'audio-fft' | 'hardware';
export type LcdSpectrumReceiver = 'MAIN' | 'SUB';
export type LcdSpectrumFreshness = 'fresh' | 'stale';

export interface LcdSpectrumFrame {
  readonly source: LcdSpectrumSource;
  readonly receiver: LcdSpectrumReceiver;
  readonly freshness: LcdSpectrumFreshness;
  readonly startHz: number;
  readonly endHz: number;
  readonly normalizedBins: readonly number[];
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
  | { readonly state: 'ghost'; readonly reason: LcdSpectrumFrameGhostReason };

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
    || value.endHz <= value.startHz
    || !Array.isArray(value.normalizedBins)
    || value.normalizedBins.length < 2) return false;

  for (const sample of value.normalizedBins) {
    if (typeof sample !== 'number' || !Number.isFinite(sample) || sample < 0 || sample > 1) {
      return false;
    }
  }
  return true;
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

  return { state: 'live', frame: input as unknown as LcdSpectrumFrame };
}
