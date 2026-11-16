// Noise floor of a spectrum frame (MOR-4463). One estimate for every view
// that wants to stand its picture on the noise instead of on a fixed level.

/** At most this many samples of a frame are read; a floor needs no more. */
const MAX_SAMPLES = 512;

/**
 * The noise floor of `length` samples of `data` from `offset`, in sample
 * units: the mean of the samples that do not exceed the frame's own mean, so
 * signals leave themselves out. Returns 0 for an empty frame.
 */
export function estimateNoiseFloor(data: Uint8Array, offset = 0, length = data.length - offset): number {
  if (length <= 0) return 0;
  const step = Math.max(1, Math.floor(length / MAX_SAMPLES));
  let sum = 0, count = 0;
  for (let i = 0; i < length; i += step) { sum += data[offset + i]; count++; }
  const mean = sum / count;
  let low = 0, lows = 0;
  for (let i = 0; i < length; i += step) {
    const value = data[offset + i];
    if (value <= mean) { low += value; lows++; }
  }
  return low / lows;
}

/**
 * The floor a picture stands on: it follows the estimates slowly, `share` of
 * the way per frame, and takes an estimate at once when it differs by more
 * than `jump` (another band, an attenuator) or when there is no floor yet.
 */
export function followNoiseFloor(current: number | null, estimate: number, jump: number, share = 0.04): number {
  return current === null || Math.abs(estimate - current) > jump ? estimate : current + share * (estimate - current);
}
