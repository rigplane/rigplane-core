/**
 * MOR-1447 — the shared `formatKnownLevel` readout helper.
 *
 * Extracted so every semantic-surface level slider (`RfFrontEndSurface`'s RF
 * gain/squelch, `TxAuxSurface`'s RF power, …) renders a KNOWN reading the
 * same honest way instead of each surface's `textOf` doing `String(value)`
 * on a raw wire float. That raw path is the live MOR-1447 regression: an
 * IC-7300 walkthrough read RF gain back as the literal wire float
 * `0.8196078431372549` (captured verbatim in
 * `lib/runtime/adapters/__tests__/fixtures/ic7300-state.json`'s
 * `main.rfGain`) instead of a formatted "82%".
 *
 * `[min, max]` come from the SAME per-field domain tuple the surface already
 * declares for its `<input type="range">` (`RF_FRONT_END_LEVELS` /
 * `TX_AUX_LEVELS`) — the function stays generic over that declared scale
 * rather than special-casing a field name or a radio vendor, so a control
 * whose domain is genuinely raw (mic gain 0..255, VOX delay 0..20 seconds)
 * keeps rendering its native number, and only a field the surface itself
 * declares as a 0..1 fraction is percent-formatted.
 */
import { describe, expect, it } from 'vitest';
import { formatKnownLevel } from '../format-level';

describe('formatKnownLevel (MOR-1447)', () => {
  it('formats a 0..1 fraction as a rounded percent — the MOR-1447 repro value', () => {
    // The exact live-captured value from ic7300-state.json's `main.rfGain`.
    expect(formatKnownLevel(0.8196078431372549, 0, 1)).toBe('82%');
  });

  it('formats a second 0..1 field (e.g. RF power) the same way', () => {
    expect(formatKnownLevel(0.5529411764705883, 0, 1)).toBe('55%');
  });

  it('rounds 0 and 1 to the clean endpoints', () => {
    expect(formatKnownLevel(0, 0, 1)).toBe('0%');
    expect(formatKnownLevel(1, 0, 1)).toBe('100%');
  });

  it('leaves a non-fractional domain (e.g. raw 0..255 mic gain) as the plain number', () => {
    expect(formatKnownLevel(128, 0, 255)).toBe('128');
  });

  it('leaves a small integer domain (e.g. VOX delay 0..20 seconds) as the plain number', () => {
    expect(formatKnownLevel(5, 0, 20)).toBe('5');
  });

  it('leaves a non-zero-based 0..1-width domain as the plain number (not treated as a fraction)', () => {
    expect(formatKnownLevel(1.5, 1, 2)).toBe('1.5');
  });
});
