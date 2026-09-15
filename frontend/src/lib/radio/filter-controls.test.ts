import { describe, expect, it } from 'vitest';

import type { Capabilities, ControlDomain, FilterModeConfig } from '$lib/types/capabilities';
import {
  controlDisplayDomain,
  nbDepthDisplayToRaw,
  nbDepthRawToDisplay,
  nrRawToDisplay,
  quantizeFilterWidthToRule,
  resolveControlContract,
} from './filter-controls';

// MOR-490: NR-level slider is 0-15 (front-panel scale), wire is 0-255 BCD.
// With no capabilities loaded these helpers use the IC-7610 fallback range
// (raw 0..255 <-> display 0..15), which is the path exercised in tests.

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
// MOR-1682: a control's slider domain comes from the profile's published
// control entry, never a per-radio constant in adapter/surface code. The two
// real shapes: FTX-1's exact identity domain (`rigs/ftx1.toml
// [controls.cw_pitch]`, 300..1050 Hz in 10 Hz steps) and IC-7300's legacy
// range (`rigs/ic7300.toml [controls.cw_pitch]`, raw 0-255 <-> display
// 300-900 Hz, no step) — the step for the legacy shape is the caller's own
// fallback, preserving today's 5 Hz UI behaviour.
describe('controlDisplayDomain (MOR-1682)', () => {
  const FTX1_CW_PITCH: ControlDomain = {
    mapping: 'identity',
    raw_min: 300, raw_max: 1050, raw_step: 10, raw_origin: 300,
    display_min: '300' as never, display_max: '1050' as never,
    display_step: '10' as never, display_origin: '300' as never,
    display_unit: 'Hz', quantization: 'reject', restoration: 'exact',
  };
  const IC7300_CW_PITCH = {
    raw_min: 0, raw_max: 255, display_min: 300, display_max: 900, display_unit: 'Hz',
  };

  it('projects an exact profile domain with its own step and origin', () => {
    expect(controlDisplayDomain(FTX1_CW_PITCH, 5))
      .toEqual({ min: 300, max: 1050, step: 10, origin: 300 });
  });

  it('projects a bipolar exact domain with a negative raw range and origin 0 (if_shift shape, MOR-1681)', () => {
    const FTX1_IF_SHIFT: ControlDomain = {
      mapping: 'identity',
      raw_min: -1200, raw_max: 1200, raw_step: 20, raw_origin: 0,
      display_min: '-1200' as never, display_max: '1200' as never,
      display_step: '20' as never, display_origin: '0' as never,
      display_unit: 'Hz', quantization: 'reject', restoration: 'exact',
    };
    expect(controlDisplayDomain(FTX1_IF_SHIFT, 25))
      .toEqual({ min: -1200, max: 1200, step: 20, origin: 0 });
  });

  it('projects a legacy range onto the caller fallback step anchored at display_min', () => {
    expect(controlDisplayDomain(IC7300_CW_PITCH, 5))
      .toEqual({ min: 300, max: 900, step: 5, origin: 300 });
  });

  it('takes the step from a legacy entry\'s positive integer decode_quantum (MOR-2475 F1)', () => {
    const X6100_CW_PITCH = {
      raw_min: 0, raw_max: 255, display_min: 400, display_max: 1200,
      display_unit: 'Hz', decode_quantum: 10,
    };
    expect(controlDisplayDomain(X6100_CW_PITCH, 5))
      .toEqual({ min: 400, max: 1200, step: 10, origin: 400 });
  });

  it('keeps the caller fallback step when decode_quantum is not a positive integer', () => {
    for (const decode_quantum of [0, -3, 2.5, Number.NaN]) {
      expect(controlDisplayDomain({ ...IC7300_CW_PITCH, decode_quantum }, 5))
        .toEqual({ min: 300, max: 900, step: 5, origin: 300 });
    }
  });

  it('returns null for an absent control entry', () => {
    expect(controlDisplayDomain(undefined, 5)).toBeNull();
    expect(controlDisplayDomain(null, 5)).toBeNull();
  });

  it('returns null for a legacy entry without usable display bounds (no fabricated domain)', () => {
    expect(controlDisplayDomain({ raw_min: 0, raw_max: 255 }, 5)).toBeNull();
    expect(controlDisplayDomain({ ...IC7300_CW_PITCH, display_min: 900, display_max: 300 }, 5)).toBeNull();
    expect(controlDisplayDomain({ ...IC7300_CW_PITCH, display_min: Number.NaN }, 5)).toBeNull();
  });

  it('returns null for an exact domain that fails the decode/encode round-trip', () => {
    expect(controlDisplayDomain({ ...FTX1_CW_PITCH, restoration: 'unavailable' }, 5)).toBeNull();
    expect(controlDisplayDomain({ ...FTX1_CW_PITCH, display_max: '1055' as never }, 5)).toBeNull();
  });
});

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

// MOR-2475: one conversion path for every control a profile can publish as an
// exact domain or a legacy band. The FTX-1 `manual_notch_freq` domain (from
// `rigs/ftx1.toml [controls.manual_notch_freq]`) is the exact case; the
// IC-7610 `nb_depth` band (from `rigs/ic7610.toml [controls.nb_depth]`) is the
// legacy case that must keep its shipped proportional conversion.
describe('resolveControlContract (MOR-2475)', () => {
  const FTX1_MANUAL_NOTCH_FREQ: ControlDomain = {
    mapping: 'linear',
    raw_min: 1, raw_max: 320, raw_step: 1, raw_origin: 1,
    display_min: '10' as never, display_max: '3200' as never,
    display_step: '10' as never, display_origin: '10' as never,
    display_unit: 'Hz', quantization: 'reject', restoration: 'exact',
  };
  const IC7610_NB_DEPTH = {
    raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10,
  };

  function domainCaps(capabilities: string[], controls: Record<string, unknown>): Capabilities {
    return { capabilities, receivers: 1, controls } as unknown as Capabilities;
  }

  it('decodes a published manual_notch_freq exact domain through control-domain.ts', () => {
    const contract = resolveControlContract(
      domainCaps(['notch'], { manual_notch_freq: FTX1_MANUAL_NOTCH_FREQ }), 'manual_notch_freq',
    );
    expect(contract.hasControl).toBe(true);
    expect(contract.displayDomain).toEqual({ min: 10, max: 3200, step: 10, origin: 10 });
    expect(contract.rawToDisplay(160)).toBe(1600);
    expect(contract.rawToDisplay(1)).toBe(10);
    expect(contract.rawToDisplay(320)).toBe(3200);
  });

  it('treats 10 and 3200 as legal display values and 0 and 3201 as illegal', () => {
    const contract = resolveControlContract(
      domainCaps(['notch'], { manual_notch_freq: FTX1_MANUAL_NOTCH_FREQ }), 'manual_notch_freq',
    );
    expect(contract.displayToRaw(10)).toBe(1);
    expect(contract.displayToRaw(3200)).toBe(320);
    expect(contract.displayToRaw(0)).toBeNull();
    expect(contract.displayToRaw(3201)).toBeNull();
  });

  it('accepts only the published manual_notch_freq raw positions', () => {
    const contract = resolveControlContract(
      domainCaps(['notch'], { manual_notch_freq: FTX1_MANUAL_NOTCH_FREQ }), 'manual_notch_freq',
    );
    expect(contract.acceptsRaw(1)).toBe(true);
    expect(contract.acceptsRaw(320)).toBe(true);
    expect(contract.acceptsRaw(0)).toBe(false);
    expect(contract.acceptsRaw(321)).toBe(false);
  });

  it('resolves no manual_notch_freq domain when the radio does not publish one', () => {
    const contract = resolveControlContract(domainCaps(['notch'], {}), 'manual_notch_freq');
    expect(contract.hasControl).toBe(false);
    expect(contract.displayDomain).toBeNull();
    expect(contract.rawToDisplay(160)).toBeNull();
  });

  it('decodes the IC-7610 nb_depth legacy band (raw 0 -> 1, raw 9 -> 10)', () => {
    const contract = resolveControlContract(
      domainCaps(['nb'], { nb_depth: IC7610_NB_DEPTH }), 'nb_depth',
    );
    expect(contract.displayDomain).toEqual({ min: 1, max: 10, step: 1, origin: 1 });
    expect(contract.rawToDisplay(0)).toBe(1);
    expect(contract.rawToDisplay(9)).toBe(10);
    expect(contract.displayToRaw(1)).toBe(0);
    expect(contract.displayToRaw(10)).toBe(9);
  });

  it('falls back to the NB depth default band when the radio publishes no entry at all', () => {
    const contract = resolveControlContract(domainCaps(['nb'], {}), 'nb_depth');
    expect(contract.displayDomain).toEqual({ min: 1, max: 10, step: 1, origin: 1 });
    expect(contract.hasControl).toBe(false);
  });

  it('keeps nr_level on the same shared path (legacy band preserved)', () => {
    const contract = resolveControlContract(
      domainCaps(['nr'], { nr_level: { raw_min: 0, raw_max: 255, display_min: 0, display_max: 15 } }),
      'nr_level',
    );
    expect(contract.hasControl).toBe(true);
    expect(contract.displayDomain).toEqual({ min: 0, max: 15, step: 1, origin: 0 });
    expect(contract.rawToDisplay(128)).toBe(8);
    expect(contract.displayToRaw(8)).toBe(136);
  });
});
