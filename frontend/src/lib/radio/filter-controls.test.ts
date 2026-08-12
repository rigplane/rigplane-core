import { describe, expect, it } from 'vitest';

import type { FilterModeConfig } from '$lib/types/capabilities';
import {
  nbDepthDisplayToRaw,
  nbDepthRawToDisplay,
  nrDisplayToRaw,
  nrRawToDisplay,
  quantizeFilterWidthToRule,
} from './filter-controls';

// MOR-490: NR-level slider is 0-15 (front-panel scale), wire is 0-255 BCD.
// With no capabilities loaded these helpers use the IC-7610 fallback range
// (raw 0..255 <-> display 0..15), which is the path exercised in tests.

describe('nrDisplayToRaw (fallback range)', () => {
  it('maps the full-scale slider value to the full-scale wire value', () => {
    expect(nrDisplayToRaw(15)).toBe(255);
  });

  it('maps zero to zero', () => {
    expect(nrDisplayToRaw(0)).toBe(0);
  });

  it('maps the midpoint slider value to the midpoint wire value', () => {
    // round(8 * 255 / 15) = round(136) = 136
    expect(nrDisplayToRaw(8)).toBe(136);
  });

  it('clamps out-of-range display values to the wire range', () => {
    expect(nrDisplayToRaw(-5)).toBe(0);
    expect(nrDisplayToRaw(99)).toBe(255);
  });
});

describe('nrRawToDisplay (fallback range)', () => {
  it('maps the full-scale wire value to the full-scale slider value', () => {
    expect(nrRawToDisplay(255)).toBe(15);
  });

  it('maps zero to zero', () => {
    expect(nrRawToDisplay(0)).toBe(0);
  });

  it('maps the midpoint wire value to the midpoint slider value', () => {
    // round(128 * 15 / 255) = round(7.53) = 8
    expect(nrRawToDisplay(128)).toBe(8);
  });

  it('clamps out-of-range wire values to the slider range', () => {
    expect(nrRawToDisplay(-1)).toBe(0);
    expect(nrRawToDisplay(999)).toBe(15);
  });
});

describe('NR display <-> raw round-trip', () => {
  it('round-trips the slider endpoints exactly', () => {
    expect(nrRawToDisplay(nrDisplayToRaw(0))).toBe(0);
    expect(nrRawToDisplay(nrDisplayToRaw(15))).toBe(15);
  });
});

// MOR-498: NB-depth slider is 1-10 (front-panel scale), wire is 0-9.
// With no capabilities loaded these helpers use the IC-7610 fallback range
// (raw 0..9 <-> display 1..10): a simple +1/-1 offset.

describe('nbDepthDisplayToRaw (fallback range)', () => {
  it('maps display 1 to wire 0', () => {
    expect(nbDepthDisplayToRaw(1)).toBe(0);
  });

  it('maps display 6 to wire 5', () => {
    expect(nbDepthDisplayToRaw(6)).toBe(5);
  });

  it('maps display 10 to wire 9', () => {
    expect(nbDepthDisplayToRaw(10)).toBe(9);
  });

  it('clamps out-of-range display values to the wire range', () => {
    expect(nbDepthDisplayToRaw(-5)).toBe(0);
    expect(nbDepthDisplayToRaw(99)).toBe(9);
  });
});

describe('nbDepthRawToDisplay (fallback range)', () => {
  it('maps wire 0 to display 1', () => {
    expect(nbDepthRawToDisplay(0)).toBe(1);
  });

  it('maps wire 5 to display 6', () => {
    expect(nbDepthRawToDisplay(5)).toBe(6);
  });

  it('maps wire 9 to display 10', () => {
    expect(nbDepthRawToDisplay(9)).toBe(10);
  });

  it('clamps out-of-range wire values to the slider range', () => {
    expect(nbDepthRawToDisplay(-1)).toBe(1);
    expect(nbDepthRawToDisplay(999)).toBe(10);
  });
});

describe('NB-depth display <-> raw round-trip', () => {
  it('round-trips the slider endpoints exactly', () => {
    expect(nbDepthRawToDisplay(nbDepthDisplayToRaw(1))).toBe(1);
    expect(nbDepthRawToDisplay(nbDepthDisplayToRaw(10))).toBe(10);
  });
});

// MOR-1518: the IC-7300's USB/LSB/CW/RTTY width rules (`rigs/ic7300.toml`)
// split into two `segments` with DIFFERENT step sizes either side of
// 500/600 Hz (50 Hz below, 100 Hz above). A mid-drag value snapped to a
// single, fixed 50 Hz step (`FILTER_WIDTH_STEP`) produces exactly the
// illegal widths the live bench reported (1050/2150/3150 Hz) once the drag
// crosses into the coarser upper segment — the backend's
// `filter_hz_to_index` (`src/rigplane/commands/_codec.py`) then rejects
// them with "Filter width N is not aligned to N Hz steps", the reported
// sticky-toast spray. `quantizeFilterWidthToRule` must snap to a value the
// radio's OWN declared rule actually accepts, not a client-invented one.
describe('quantizeFilterWidthToRule (MOR-1518)', () => {
  // Same shape as `panel-commands.intent.isolated.test.ts`'s `A06_SEGMENTS`
  // fixture (and `rigs/ic7300.toml`'s `[filters.width.USB]`): a 50 Hz step
  // 50-500 Hz, an intentional gap, then a 100 Hz step 600-3600 Hz.
  const IC7300_USB_SEGMENTS: FilterModeConfig = {
    defaults: [3000, 2400, 1800],
    fixed: false,
    minHz: 50,
    maxHz: 3600,
    segments: [
      { hzMin: 50, hzMax: 500, stepHz: 50, indexMin: 0 },
      { hzMin: 600, hzMax: 3600, stepHz: 100, indexMin: 10 },
    ],
  };

  it('reproduces the live-bench alignment error path: raw drag values are illegal for the upper (100 Hz) segment', () => {
    // This is the RED-first assertion: without quantization, these exact
    // mid-drag values (reported on the live IC-7300 bench) are NOT aligned
    // to the 100 Hz step that governs 600-3600 Hz — dispatching them raw
    // is what trips the backend's alignment guard.
    for (const raw of [1050, 2150, 3150]) {
      expect(raw % 100).not.toBe(0);
    }
  });

  it('snaps illegal mid-drag values in the upper (100 Hz) segment to the nearest legal width', () => {
    expect(quantizeFilterWidthToRule(1050, IC7300_USB_SEGMENTS)).toBe(1100);
    expect(quantizeFilterWidthToRule(2150, IC7300_USB_SEGMENTS)).toBe(2200);
    expect(quantizeFilterWidthToRule(3150, IC7300_USB_SEGMENTS)).toBe(3200);
  });

  it('leaves already-legal values in either segment untouched', () => {
    expect(quantizeFilterWidthToRule(250, IC7300_USB_SEGMENTS)).toBe(250);
    expect(quantizeFilterWidthToRule(500, IC7300_USB_SEGMENTS)).toBe(500);
    expect(quantizeFilterWidthToRule(600, IC7300_USB_SEGMENTS)).toBe(600);
    expect(quantizeFilterWidthToRule(1800, IC7300_USB_SEGMENTS)).toBe(1800);
  });

  it('snaps a value inside the segment GAP (500-600 Hz, IC-7300 has no filter index there) to the nearer edge', () => {
    expect(quantizeFilterWidthToRule(520, IC7300_USB_SEGMENTS)).toBe(500);
    expect(quantizeFilterWidthToRule(580, IC7300_USB_SEGMENTS)).toBe(600);
    // Exact midpoint of the gap: a tie prefers the upper segment, the same
    // round-half-up convention `Math.round` itself uses for in-segment snapping.
    expect(quantizeFilterWidthToRule(550, IC7300_USB_SEGMENTS)).toBe(600);
  });

  it('clamps out-of-range values to the rule\'s own overall bounds', () => {
    expect(quantizeFilterWidthToRule(10, IC7300_USB_SEGMENTS)).toBe(50);
    expect(quantizeFilterWidthToRule(9999, IC7300_USB_SEGMENTS)).toBe(3600);
  });

  it('is data-driven for a SECOND, differently-stepped rule (not the IC-7300\'s 50/100 Hz split)', () => {
    // A synthetic single-step rule (AM-shaped: `rigs/ic7300.toml`'s
    // `[filters.width.AM]` declares 200 Hz), proving the step comes from
    // the passed-in rule, never a hardcoded 50/100 constant in this module.
    const AM_STYLE_STEP: FilterModeConfig = {
      defaults: [9000, 6000, 3000], fixed: false, minHz: 200, maxHz: 10_000, stepHz: 200,
    };
    expect(quantizeFilterWidthToRule(9100, AM_STYLE_STEP)).toBe(9200);
    expect(quantizeFilterWidthToRule(9000, AM_STYLE_STEP)).toBe(9000);

    // A third, still-different step (25 Hz) to further pin "data-driven,
    // not a second hardcoded constant".
    const NARROW_STEP: FilterModeConfig = {
      defaults: [300], fixed: false, minHz: 100, maxHz: 500, stepHz: 25,
    };
    expect(quantizeFilterWidthToRule(212, NARROW_STEP)).toBe(200);
  });

  it('falls back to the plain clamp/50 Hz step when capabilities declare no width rule (unchanged pre-MOR-1518 behavior)', () => {
    expect(quantizeFilterWidthToRule(1050, null)).toBe(1050);
    expect(quantizeFilterWidthToRule(1050, undefined)).toBe(1050);
  });

  it('passes fixed-width and table-mode rules through UNCHANGED (no synthesized step, no wrong-bounds clamp)', () => {
    // FM's fixed defaults (15000/10000/7000 Hz, `rigs/ic7300.toml`) sit well
    // above the generic clamp's 3600 Hz ceiling — clamping them would
    // silently corrupt an otherwise-legal fixed width, so `fixed` rules must
    // pass through untouched rather than fall into the plain clamp.
    const fixedRule: FilterModeConfig = { defaults: [15000], fixed: true };
    expect(quantizeFilterWidthToRule(15000, fixedRule)).toBe(15000);

    // Table-mode widths are snapped elsewhere (`FilterPanel.svelte`'s
    // `hzToTableIndex`/`tableIndexToHz`) — this function must not crash or
    // invent a step for them either.
    const tableRule: FilterModeConfig = { defaults: [300], fixed: false, table: [300, 600, 1200] };
    expect(quantizeFilterWidthToRule(650, tableRule)).toBe(650);
  });

  it('never throws and never returns a fabricated step for malformed segment data', () => {
    const malformed: FilterModeConfig = {
      defaults: [], fixed: false, minHz: 50, maxHz: 3600,
      segments: [{ hzMin: 500, hzMax: 50, stepHz: 0, indexMin: 0 }],
    };
    expect(() => quantizeFilterWidthToRule(1050, malformed)).not.toThrow();
  });

  it('passes non-finite input through unchanged rather than fabricating a value', () => {
    expect(quantizeFilterWidthToRule(Number.NaN, IC7300_USB_SEGMENTS)).toBeNaN();
  });
});
