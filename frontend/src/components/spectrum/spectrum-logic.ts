/**
 * Pure logic extracted from SpectrumPanel.svelte for testability.
 */

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
/**
 * CI-V wave-data modes where the radio owns the displayed window:
 * 1=FIX, 2=SCROLL-C, 3=SCROLL-F. The single client spelling of the
 * shared server fact RADIO_OWNED_WINDOW_SCOPE_MODES — see
 * contracts/scope-mode-families.json.
 */
export function isRadioOwnedWindowScope(mode: number | undefined): boolean {
  return mode === 1 || mode === 2 || mode === 3;
}
/**
 * Modes where the SPAN control is disabled and the EDGE number selects
 * the window instead: 1=FIX, 3=SCROLL-F. SPAN applies in 0=CTR and
 * 2=SCROLL-C (see isSpanApplicable in spectrum-toolbar-logic.ts).
 */
export function isSpanDisabledScope(mode: number | undefined): boolean {
  return mode === 1 || mode === 3;
}

// --- Native width helpers (MOR-4451) ---
/**
 * The radio's native scope width in Hz for an observed scope-span index.
 *
 * `span_choices_hz` already states FULL view widths (CENTER: 2 × the radio's
 * ±half-width preset — the server publishes view widths since #4565), so the
 * published value is returned unchanged: no client ×2. Null unless the span
 * reading is a known integer index into a non-empty choices list naming a
 * finite positive width.
 */
export function nativeWidthFromSpanChoice(
  choices: readonly number[] | undefined | null,
  reading: { status: string; value?: unknown } | undefined | null,
): number | null {
  if (reading?.status === 'known' && Number.isInteger(reading.value)
    && choices && (reading.value as number) >= 0
    && (reading.value as number) < choices.length) {
    const widthHz = choices[reading.value as number];
    if (Number.isFinite(widthHz) && widthHz > 0) return widthHz;
  }
  return null;
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
