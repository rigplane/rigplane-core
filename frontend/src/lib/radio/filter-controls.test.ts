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

  // Real red-first coverage for the live-bench values lives here and in the
  // sibling `panel-commands.intent.isolated.test.ts` suite (both call the
  // actual SUT). 1050/2150/3150 Hz are each an EXACT midpoint between two
  // legal 100 Hz-segment values (e.g. 1050 sits exactly between 1000 and
  // 1100) — precisely because the pre-fix bug always added a stray 50 Hz
  // (the wrong, lower segment's step) onto an otherwise-legal 100 Hz value.
  // Tie-break prefers the LOWER candidate (see `snapWithinSegment`'s doc
  // comment, aligned with `scope-adapter.ts`'s `snapStep`/
  // `snapSpectrumFilterWidth`).
  it('snaps illegal mid-drag values in the upper (100 Hz) segment to the nearest legal width', () => {
    expect(quantizeFilterWidthToRule(1050, IC7300_USB_SEGMENTS)).toBe(1000);
    expect(quantizeFilterWidthToRule(2150, IC7300_USB_SEGMENTS)).toBe(2100);
    expect(quantizeFilterWidthToRule(3150, IC7300_USB_SEGMENTS)).toBe(3100);
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
    // Exact midpoint of the gap: a tie prefers the LOWER segment's edge —
    // aligned with `scope-adapter.ts`'s `snapSpectrumFilterWidth` (the
    // spectrum-panel passband-drag path), which resolves the identical
    // 550 Hz tie to 500 Hz too.
    expect(quantizeFilterWidthToRule(550, IC7300_USB_SEGMENTS)).toBe(500);
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
    // 9100 is an exact tie between 9000 and 9200 — tie prefers lower.
    expect(quantizeFilterWidthToRule(9100, AM_STYLE_STEP)).toBe(9000);
    expect(quantizeFilterWidthToRule(9000, AM_STYLE_STEP)).toBe(9000);

    // A third, still-different step (25 Hz) to further pin "data-driven,
    // not a second hardcoded constant".
    const NARROW_STEP: FilterModeConfig = {
      defaults: [300], fixed: false, minHz: 100, maxHz: 500, stepHz: 25,
    };
    expect(quantizeFilterWidthToRule(212, NARROW_STEP)).toBe(200);
  });

  it('passes the value through UNCHANGED when capabilities declare no width rule (no fabricated ceiling, unchanged pre-MOR-1518 behavior)', () => {
    // Pre-MOR-1518 the dispatch path applied NO clamp at all when a mode had
    // no declared filterConfig entry — raw value straight to the wire. This
    // function must keep that exact behavior rather than imposing this
    // module's own IC-7610-shaped 50/3600/50 default grid (`clampFilterWidth`'s
    // fallback) on a radio/mode that declared nothing: a value ABOVE that
    // default's 3600 Hz ceiling (e.g. an FTX-1 table/step mode with a wider
    // declared `filterWidthMax`) must still reach the wire unmodified.
    expect(quantizeFilterWidthToRule(1050, null)).toBe(1050);
    expect(quantizeFilterWidthToRule(1050, undefined)).toBe(1050);
    expect(quantizeFilterWidthToRule(9999, null)).toBe(9999);
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

  it('never throws and passes the value through unchanged for malformed segment data (no fabricated step)', () => {
    const malformed: FilterModeConfig = {
      defaults: [], fixed: false, minHz: 50, maxHz: 3600,
      segments: [{ hzMin: 500, hzMax: 50, stepHz: 0, indexMin: 0 }],
    };
    expect(() => quantizeFilterWidthToRule(1050, malformed)).not.toThrow();
    expect(quantizeFilterWidthToRule(1050, malformed)).toBe(1050);
  });

  it('passes non-finite input through unchanged rather than fabricating a value', () => {
    expect(quantizeFilterWidthToRule(Number.NaN, IC7300_USB_SEGMENTS)).toBeNaN();
  });
});
