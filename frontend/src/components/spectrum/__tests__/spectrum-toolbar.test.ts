/**
 * Tests for SpectrumToolbar logic: layer visibility toggling,
 * scope control applicability, label lookups, and clamping helpers.
 *
 * These tests extract the pure logic from SpectrumToolbar.svelte
 * and verify it independently of DOM rendering.
 */
import { describe, it, expect } from 'vitest';
import {
  formatSpanHz, SPEED_LABELS, SPEED_STATIC_LABEL, MODE_BUTTONS,
  toggleLayer, isLayerVisible,
  isSpanApplicable, isEdgeApplicable,
  clampSpeed, clampBrt, stepRef, refRangeFromCaps,
  type RefRange,
} from '../spectrum-toolbar-logic';

function nextReceiver(current: number): number {
  return current === 1 ? 0 : 1;
}

// ── Tests ──

describe('SpectrumToolbar constants', () => {
  it('SPEED_LABELS covers indices 0–2', () => {
    expect(SPEED_LABELS[0]).toBe('FST');
    expect(SPEED_LABELS[1]).toBe('MID');
    expect(SPEED_LABELS[2]).toBe('SLO');
  });

  it('SPEED_STATIC_LABEL is the expanded "SPEED" (renamed from SPD)', () => {
    expect(SPEED_STATIC_LABEL).toBe('SPEED');
  });

  it('MODE_BUTTONS has 4 entries with correct labels', () => {
    expect(MODE_BUTTONS).toHaveLength(4);
    const labels = MODE_BUTTONS.map(([, l]) => l);
    expect(labels).toEqual(['CTR', 'FIX', 'S-C', 'S-F']);
  });

  it('MODE_BUTTONS indices are 0–3', () => {
    const indices = MODE_BUTTONS.map(([m]) => m);
    expect(indices).toEqual([0, 1, 2, 3]);
  });
});

describe('Layer visibility', () => {
  it('all layers visible when hiddenLayers is empty', () => {
    expect(isLayerVisible([], 'amateur')).toBe(true);
    expect(isLayerVisible([], 'broadcast')).toBe(true);
  });

  it('hidden layer returns false', () => {
    expect(isLayerVisible(['amateur'], 'amateur')).toBe(false);
  });

  it('non-hidden layer returns true even when others hidden', () => {
    expect(isLayerVisible(['amateur'], 'broadcast')).toBe(true);
  });

  it('toggleLayer hides a visible layer', () => {
    const result = toggleLayer([], 'amateur');
    expect(result).toEqual(['amateur']);
  });

  it('toggleLayer shows a hidden layer', () => {
    const result = toggleLayer(['amateur', 'broadcast'], 'amateur');
    expect(result).toEqual(['broadcast']);
  });

  it('toggleLayer is idempotent round-trip', () => {
    const step1 = toggleLayer([], 'amateur');
    const step2 = toggleLayer(step1, 'amateur');
    expect(step2).toEqual([]);
  });

  it('toggleLayer preserves other layers', () => {
    const hidden = ['amateur', 'broadcast', 'utility'];
    const result = toggleLayer(hidden, 'broadcast');
    expect(result).toEqual(['amateur', 'utility']);
  });
});

describe('Scope mode applicability', () => {
  it('span applicable in CTR mode (0)', () => {
    expect(isSpanApplicable(0)).toBe(true);
  });

  it('span applicable in S-C mode (2)', () => {
    expect(isSpanApplicable(2)).toBe(true);
  });

  it('span not applicable in FIX mode (1)', () => {
    expect(isSpanApplicable(1)).toBe(false);
  });

  it('span not applicable in S-F mode (3)', () => {
    expect(isSpanApplicable(3)).toBe(false);
  });

  it('span not applicable when undefined', () => {
    expect(isSpanApplicable(undefined)).toBe(false);
  });

  it('edge applicable in FIX mode (1)', () => {
    expect(isEdgeApplicable(1)).toBe(true);
  });

  it('edge applicable in S-F mode (3)', () => {
    expect(isEdgeApplicable(3)).toBe(true);
  });

  it('edge not applicable in CTR (0) or S-C (2)', () => {
    expect(isEdgeApplicable(0)).toBe(false);
    expect(isEdgeApplicable(2)).toBe(false);
  });

  it('span and edge are mutually exclusive for all modes', () => {
    for (const [mode] of MODE_BUTTONS) {
      expect(isSpanApplicable(mode) !== isEdgeApplicable(mode)).toBe(true);
    }
  });
});

describe('Speed clamping (inverted delta)', () => {
  it('delta +1 decreases speed value (visual ▶ = faster)', () => {
    expect(clampSpeed(1, 1)).toBe(0);
  });

  it('delta -1 increases speed value (visual ◀ = slower)', () => {
    expect(clampSpeed(1, -1)).toBe(2);
  });

  it('clamps at lower bound', () => {
    expect(clampSpeed(0, 1)).toBe(0);
  });

  it('clamps at upper bound', () => {
    expect(clampSpeed(2, -1)).toBe(2);
  });
});

describe('Receiver switching', () => {
  it('switches from MAIN (0) to SUB (1)', () => {
    expect(nextReceiver(0)).toBe(1);
  });

  it('switches from SUB (1) to MAIN (0)', () => {
    expect(nextReceiver(1)).toBe(0);
  });

  it('any other value defaults to MAIN toggle (becomes 1)', () => {
    expect(nextReceiver(2)).toBe(1);
  });
});

describe('BRT level clamping', () => {

  it('increases within range', () => {
    expect(clampBrt(0, 5)).toBe(5);
  });

  it('decreases within range', () => {
    expect(clampBrt(0, -5)).toBe(-5);
  });

  it('clamps at +30', () => {
    expect(clampBrt(30, 5)).toBe(30);
  });

  it('clamps at -30', () => {
    expect(clampBrt(-30, -5)).toBe(-30);
  });
});

describe('REF stepping on the 5 dB grid (MOR-3694)', () => {
  const ic705: RefRange = { min: -20, max: 20 };
  const ic7610: RefRange = { min: -30, max: 10 };

  it('steps down from an off-grid integer readback onto the grid: -4 -> -5, up -> 0', () => {
    expect(stepRef(-4, -1, ic705)).toBe(-5);
    expect(stepRef(-4, 1, ic705)).toBe(0);
  });

  it('steps from a half-dB readback: -4.5 down -> -5, up -> 0', () => {
    expect(stepRef(-4.5, -1, ic705)).toBe(-5);
    expect(stepRef(-4.5, 1, ic705)).toBe(0);
  });

  it('steps by 5 from a grid point', () => {
    expect(stepRef(0, 1, ic705)).toBe(5);
    expect(stepRef(0, -1, ic705)).toBe(-5);
    expect(stepRef(5, -1, ic705)).toBe(0);
    expect(stepRef(1, 1, ic705)).toBe(5);
  });

  it('clamps to the declared IC-705 range at both ends', () => {
    expect(stepRef(20, 1, ic705)).toBe(20);
    expect(stepRef(-20, -1, ic705)).toBe(-20);
    expect(stepRef(19, 1, ic705)).toBe(20);
    expect(stepRef(-19, -1, ic705)).toBe(-20);
    expect(stepRef(19, -1, ic705)).toBe(15);
    expect(stepRef(-19, 1, ic705)).toBe(-15);
  });

  it('honours a different declared range (IC-7610 -30..10)', () => {
    expect(stepRef(10, 1, ic7610)).toBe(10);
    expect(stepRef(-30, -1, ic7610)).toBe(-30);
    expect(stepRef(9, 1, ic7610)).toBe(10);
  });

  it('never emits an out-of-range or non-integer value, even from an out-of-range start', () => {
    const down = stepRef(-22, -1, ic705);
    expect(Number.isInteger(down)).toBe(true);
    expect(down).toBeGreaterThanOrEqual(ic705.min);
    expect(down).toBeLessThanOrEqual(ic705.max);
    expect(down).toBe(-20);
    expect(stepRef(-22, 1, ic705)).toBe(-20);
  });
});

describe('refRangeFromCaps (MOR-3694)', () => {
  it('reads the declared range from capability fields', () => {
    expect(refRangeFromCaps({ ref_min_db: -20, ref_max_db: 20 })).toEqual({ min: -20, max: 20 });
    expect(refRangeFromCaps({ ref_min_db: -30.0, ref_max_db: 10.0 })).toEqual({ min: -30, max: 10 });
  });

  it('fails closed to null when no range is published', () => {
    expect(refRangeFromCaps({})).toBeNull();
    expect(refRangeFromCaps(null)).toBeNull();
    expect(refRangeFromCaps(undefined)).toBeNull();
    expect(refRangeFromCaps({ ref_min_db: -20 })).toBeNull();
  });
});

describe('Label display formatting', () => {
  it('formats granted full width as ±half width', () => {
    expect(formatSpanHz(100000)).toBe('±50k');
  });

  it('SPEED defaults to MID when index is 1', () => {
    expect(SPEED_LABELS[1]).toBe('MID');
  });

  it('fallback for unknown speed index matches component behavior', () => {
    const fallback = SPEED_LABELS[99] ?? 'MID';
    expect(fallback).toBe('MID');
  });
});
