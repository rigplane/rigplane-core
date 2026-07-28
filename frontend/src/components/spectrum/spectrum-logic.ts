/**
 * Pure logic extracted from SpectrumPanel.svelte for testability.
 */

// Compatibility exports: the runtime adapter owns the binary wire decoder.
export { parseScopeFrame } from '$lib/runtime/adapters/scope-adapter';
export type { ScopeFrame } from '$lib/runtime/adapters/scope-adapter';

// --- Scale helpers ---
export function formatFreqOffset(hz: number): string {
  if (hz === 0) return '0';
  const absHz = Math.abs(hz);
  const sign = hz < 0 ? '-' : '+';
  if (absHz >= 1e6) return `${sign}${(absHz / 1e6).toFixed(1)}M`;
  if (absHz >= 1e3) return `${sign}${(absHz / 1e3).toFixed(0)}k`;
  return `${sign}${absHz}`;
}

export function deriveFreqTicks(spanHz: number): { position: number; label: string }[] {
  if (spanHz <= 0) return [];
  return [-1, -0.5, 0, 0.5, 1].map((ratio) => ({
    position: (ratio + 1) * 50,
    label: formatFreqOffset((spanHz * ratio) / 2),
  }));
}

// --- Drag helpers ---
export function getDragInterval(speed: number): number {
  if (speed > 600) return 700;
  if (speed > 200) return 400;
  return 200;
}

// --- Scope mode helpers ---
export function isFixedScope(mode: number): boolean {
  return mode === 1 || mode === 3;
}

// --- Click-to-tune helpers ---
export function freqFromPixel(
  clientX: number,
  rectLeft: number,
  rectWidth: number,
  startFreq: number,
  spanHz: number,
): number {
  return startFreq + ((clientX - rectLeft) / rectWidth) * spanHz;
}
