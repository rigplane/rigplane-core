import { describe, expect, it } from 'vitest';

import type { Capabilities, ControlDomain, FilterModeConfig } from '$lib/types/capabilities';
import {
  controlDisplayDomain,
  deriveIfShift,
  mapIfShiftToPbt,
  measuredPbtHzToRaw,
  measuredPbtRawToHz,
  nrRawToDisplay,
  PBT_MEASURED_STEP_HZ,
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

// MOR-498: NB-depth slider is 1-10 (front-panel scale), wire is 0-9. The
// conversion now lives on the shared contract (`resolveControlContract`,
// MOR-2475): an IC-7610-shaped `controls.nb_depth` entry decodes raw 0-9 to
// display 1-10 and encodes display 1-10 back to raw 0-9, and a caps payload
// publishing no `nb_depth` entry resolves to the empty contract — no value,
// never a fabricated 1-10 scale.

const IC7610_NB_DEPTH_CAPS = {
  capabilities: [], receivers: 1,
  controls: { nb_depth: { raw_min: 0, raw_max: 9, display_min: 1, display_max: 10 } },
} as unknown as Capabilities;

describe('nb_depth contract (published IC-7610 domain)', () => {
  const contract = resolveControlContract(IC7610_NB_DEPTH_CAPS, 'nb_depth');

  it('maps wire 0 to display 1', () => {
    expect(contract.rawToDisplay(0)).toBe(1);
  });

  it('maps wire 5 to display 6', () => {
    expect(contract.rawToDisplay(5)).toBe(6);
  });

  it('maps wire 9 to display 10', () => {
    expect(contract.rawToDisplay(9)).toBe(10);
  });

  it('maps display 1 to wire 0', () => {
    expect(contract.displayToRaw(1)).toBe(0);
  });

  it('maps display 6 to wire 5', () => {
    expect(contract.displayToRaw(6)).toBe(5);
  });

  it('maps display 10 to wire 9', () => {
    expect(contract.displayToRaw(10)).toBe(9);
  });

  it('round-trips the slider endpoints exactly', () => {
    expect(contract.rawToDisplay(contract.displayToRaw(1) as number)).toBe(1);
    expect(contract.rawToDisplay(contract.displayToRaw(10) as number)).toBe(10);
  });
});

describe('nb_depth contract (no published domain)', () => {
  it.each([
    ['caps with no controls at all', { capabilities: [], receivers: 1 } as unknown as Capabilities],
    ['caps whose controls omit nb_depth', {
      capabilities: [], receivers: 1, controls: {},
    } as unknown as Capabilities],
    ['null caps', null],
  ])('has no control and no conversion for %s', (_label, caps) => {
    const contract = resolveControlContract(caps, 'nb_depth');
    expect(contract.hasControl).toBe(false);
    expect(contract.displayDomain).toBeNull();
    expect(contract.acceptsRaw(0)).toBe(false);
    expect(contract.rawToDisplay(0)).toBeNull();
    expect(contract.displayToRaw(1)).toBeNull();
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

  it('resolves the empty contract when the radio publishes no nb_depth entry at all', () => {
    // MOR-2475 PR-6: the hardcoded 0-9 -> 1-10 default band is deleted; a
    // radio that publishes nothing has no NB-depth control and no
    // conversion — never a fabricated 1..10 scale.
    const contract = resolveControlContract(domainCaps(['nb'], {}), 'nb_depth');
    expect(contract.hasControl).toBe(false);
    expect(contract.displayDomain).toBeNull();
    expect(contract.acceptsRaw(0)).toBe(false);
    expect(contract.rawToDisplay(0)).toBeNull();
    expect(contract.displayToRaw(1)).toBeNull();
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

// MOR-2497 step 1: the measured IC PBT lattice. The fixture below is DATA,
// not the formula restated: all 73 reachable raw positions at filter width
// 3600 Hz, swept on the bench on 2026-09-16 by writing every raw 0..255 and
// reading back what the radio snapped to, identically on an IC-7610 over LAN
// and an IC-7300 over serial. The 50 Hz step is pinned against the radio's
// own display (raw 254 at filter 3600 read `SFT +900`/`BW 1.8` on the front
// panel — both give an 1800 Hz edge movement, i.e. 36 steps of 50 Hz; a
// 25 Hz step would have read `SFT +450`).

const PBT_LATTICE_3600: readonly number[] = [
  1, 5, 8, 12, 15, 19, 22, 26, 29, 33, 36, 40, 43, 47, 50, 54, 57, 61,
  64, 68, 71, 75, 78, 82, 85, 89, 92, 96, 99, 103, 106, 110, 113, 117,
  120, 124, 128, 131, 135, 138, 142, 145, 149, 152, 156, 159, 163, 166,
  170, 173, 177, 180, 184, 187, 191, 194, 198, 201, 205, 208, 212, 215,
  219, 222, 226, 229, 233, 236, 240, 243, 247, 250, 254,
];

describe('measuredPbt conversion (bench fixture, filter 3600)', () => {
  it('reproduces all 73 measured raw positions in both directions', () => {
    expect(PBT_LATTICE_3600).toHaveLength(73);
    PBT_LATTICE_3600.forEach((raw, i) => {
      const hz = (i - 36) * 50;
      expect(measuredPbtHzToRaw(hz, 3600, PBT_MEASURED_STEP_HZ)).toBe(raw);
      expect(measuredPbtRawToHz(raw, 3600, PBT_MEASURED_STEP_HZ)).toBe(hz);
    });
  });

  it('pins the Hz mapping at the measured points: 254 -> +1800, 128 -> 0, 1 -> -1800', () => {
    expect(measuredPbtRawToHz(254, 3600, PBT_MEASURED_STEP_HZ)).toBe(1800);
    expect(measuredPbtRawToHz(128, 3600, PBT_MEASURED_STEP_HZ)).toBe(0);
    expect(measuredPbtRawToHz(1, 3600, PBT_MEASURED_STEP_HZ)).toBe(-1800);
  });
});

describe('measuredPbt conversion (swept endpoints at filters 1800 and 500)', () => {
  // The same bench sweep recorded position counts and endpoints at the other
  // two widths; interior cells were not separately logged, so only counts,
  // endpoints and the centre are asserted here.
  it('filter 1800: 37 positions, raw 3..252, centre at raw 128, +/-900 Hz', () => {
    const raws = new Set<number>();
    for (let hz = -900; hz <= 900; hz += PBT_MEASURED_STEP_HZ) {
      raws.add(measuredPbtHzToRaw(hz, 1800, PBT_MEASURED_STEP_HZ) as number);
    }
    expect(raws.size).toBe(37);
    expect(measuredPbtHzToRaw(-900, 1800, PBT_MEASURED_STEP_HZ)).toBe(3);
    expect(measuredPbtHzToRaw(900, 1800, PBT_MEASURED_STEP_HZ)).toBe(252);
    expect(measuredPbtHzToRaw(0, 1800, PBT_MEASURED_STEP_HZ)).toBe(128);
    expect(measuredPbtRawToHz(3, 1800, PBT_MEASURED_STEP_HZ)).toBe(-900);
    expect(measuredPbtRawToHz(252, 1800, PBT_MEASURED_STEP_HZ)).toBe(900);
  });

  it('filter 500: 11 positions, raw 11..244, centre at raw 128, +/-250 Hz', () => {
    const raws = new Set<number>();
    for (let hz = -250; hz <= 250; hz += PBT_MEASURED_STEP_HZ) {
      raws.add(measuredPbtHzToRaw(hz, 500, PBT_MEASURED_STEP_HZ) as number);
    }
    expect(raws.size).toBe(11);
    expect(measuredPbtHzToRaw(-250, 500, PBT_MEASURED_STEP_HZ)).toBe(11);
    expect(measuredPbtHzToRaw(250, 500, PBT_MEASURED_STEP_HZ)).toBe(244);
    expect(measuredPbtHzToRaw(0, 500, PBT_MEASURED_STEP_HZ)).toBe(128);
    expect(measuredPbtRawToHz(11, 500, PBT_MEASURED_STEP_HZ)).toBe(-250);
    expect(measuredPbtRawToHz(244, 500, PBT_MEASURED_STEP_HZ)).toBe(250);
  });
});

describe('measuredPbt conversion (honest handling of degenerate input)', () => {
  it.each([0, -100, Number.NaN, Number.POSITIVE_INFINITY, 125])(
    'reports no conversion at filter width %s (zero/negative/non-finite/not a multiple of the step)',
    (width) => {
      expect(measuredPbtRawToHz(128, width, PBT_MEASURED_STEP_HZ)).toBeNull();
      expect(measuredPbtHzToRaw(0, width, PBT_MEASURED_STEP_HZ)).toBeNull();
    },
  );

  it('reports no conversion for a non-positive or non-finite step', () => {
    expect(measuredPbtRawToHz(128, 3600, 0)).toBeNull();
    expect(measuredPbtRawToHz(128, 3600, -50)).toBeNull();
    expect(measuredPbtHzToRaw(0, 3600, Number.NaN)).toBeNull();
  });

  it('reports no conversion for a lattice finer than one raw unit per position', () => {
    // 13000/50 + 1 = 261 positions over 256 raw values — adjacent positions
    // would collide on the same raw, so no honest lattice exists.
    expect(measuredPbtRawToHz(128, 13000, PBT_MEASURED_STEP_HZ)).toBeNull();
    expect(measuredPbtHzToRaw(0, 13000, PBT_MEASURED_STEP_HZ)).toBeNull();
  });

  it('reports no Hz for a raw the wire cannot carry', () => {
    expect(measuredPbtRawToHz(-1, 3600, PBT_MEASURED_STEP_HZ)).toBeNull();
    expect(measuredPbtRawToHz(256, 3600, PBT_MEASURED_STEP_HZ)).toBeNull();
    expect(measuredPbtRawToHz(Number.NaN, 3600, PBT_MEASURED_STEP_HZ)).toBeNull();
  });

  it('snaps a raw between lattice points to the nearest, ties toward the centre', () => {
    // Measured neighbours at filter 3600: raw 1 (-1800) and raw 5 (-1750).
    expect(measuredPbtRawToHz(2, 3600, PBT_MEASURED_STEP_HZ)).toBe(-1800);
    expect(measuredPbtRawToHz(4, 3600, PBT_MEASURED_STEP_HZ)).toBe(-1750);
    // raw 3 sits exactly halfway; the centre-side point wins.
    expect(measuredPbtRawToHz(3, 3600, PBT_MEASURED_STEP_HZ)).toBe(-1750);
  });

  it('snaps Hz between lattice points to the nearest position and clamps beyond the lattice', () => {
    expect(measuredPbtHzToRaw(1760, 3600, PBT_MEASURED_STEP_HZ)).toBe(250); // position of +1750
    expect(measuredPbtHzToRaw(5000, 3600, PBT_MEASURED_STEP_HZ)).toBe(254);
    expect(measuredPbtHzToRaw(-5000, 3600, PBT_MEASURED_STEP_HZ)).toBe(1);
  });

  it('resolves an exact half-step Hz tie toward the centre on both sides of the centre', () => {
    // Positions are exactly stepHz (50 Hz) apart, so only an exact half-step
    // — an odd multiple of 25 Hz — ties. Above the centre, +1775 sits exactly
    // between +1750 (raw 250) and +1800 (raw 254); below it, -1775 sits
    // exactly between -1800 (raw 1) and -1750 (raw 5). Both must resolve to
    // the centre-side position: "always round down" fails the -1775 case,
    // "always round up" fails the +1775 case, and "away from the centre"
    // fails both.
    expect(measuredPbtHzToRaw(1775, 3600, PBT_MEASURED_STEP_HZ)).toBe(250);
    expect(measuredPbtHzToRaw(-1775, 3600, PBT_MEASURED_STEP_HZ)).toBe(5);
    // The same rule one step out from the centre: +25 and -25 tie between
    // 0 Hz (raw 128) and +/-50 Hz and both resolve to the centre.
    expect(measuredPbtHzToRaw(25, 3600, PBT_MEASURED_STEP_HZ)).toBe(128);
    expect(measuredPbtHzToRaw(-25, 3600, PBT_MEASURED_STEP_HZ)).toBe(128);
  });
});

describe('deriveIfShift reaches the shifts the radio actually reaches (MOR-2497)', () => {
  // Measured on the IC-7610 over LAN on 2026-09-17, filter 3600: writing raw
  // 254 to both passband edges reads both back at 254, and raw 0 reads back as
  // raw 1 on both -- the radio accepts both edges at one extreme together. On
  // the measured lattice those are +1800 Hz and -1800 Hz per edge, so the true
  // IF shift reaches +/-1800. The retired `clampToBipolarRange` reported 1200
  // for the same state. The values below are the measured read-backs, not a
  // recomputation of the model.
  const WIDTH = 3600;
  it.each([[254, 1800], [1, -1800]])(
    'both edges at raw %s give a shift of %s Hz, past the retired +/-1200 bound',
    (raw, expected) => {
      const edge = measuredPbtRawToHz(raw, WIDTH, PBT_MEASURED_STEP_HZ);
      expect(edge).toBe(expected);
      expect(deriveIfShift(edge!, edge!)).toBe(expected);
      expect(Math.abs(deriveIfShift(edge!, edge!))).toBeGreaterThan(1200);
    });

  it('still reports the mean when only one edge is displaced', () => {
    // The one case the bench had before today: one edge at raw 254, the other
    // centred, which the front panel showed as SFT +900.
    const moved = measuredPbtRawToHz(254, WIDTH, PBT_MEASURED_STEP_HZ);
    const centred = measuredPbtRawToHz(128, WIDTH, PBT_MEASURED_STEP_HZ);
    expect(centred).toBe(0);
    expect(deriveIfShift(moved!, centred!)).toBe(900);
  });
});

describe('mapIfShiftToPbt keeps the passband width on the write path (MOR-2500)', () => {
  // Edges in Hz, as `measuredPbtRawToHz` reads them: raws 160/96 at filter
  // 3600 are +450/-450 Hz -- a 900 Hz passband.
  it('moves both edges by the requested shift when both stay reachable', () => {
    expect(mapIfShiftToPbt(300, 450, -450, 3600, PBT_MEASURED_STEP_HZ))
      .toEqual({ pbtInner: 750, pbtOuter: -150 });
  });

  it('snaps an off-grid requested shift to a whole lattice step', () => {
    expect(mapIfShiftToPbt(310, 450, -450, 3600, PBT_MEASURED_STEP_HZ))
      .toEqual({ pbtInner: 750, pbtOuter: -150 });
    // 325 Hz sits exactly halfway between the +300 and +350 shifts.
    expect(mapIfShiftToPbt(325, 450, -450, 3600, PBT_MEASURED_STEP_HZ))
      .toEqual({ pbtInner: 800, pbtOuter: -100 });
  });

  it('clamps the SHIFT at the reachable span instead of narrowing the passband', () => {
    // +450/-450 edges at filter 3600: the top edge can only reach +1800, so
    // the requested +1500 shift clamps to +1350 -- the width stays 900 Hz.
    expect(mapIfShiftToPbt(1500, 450, -450, 3600, PBT_MEASURED_STEP_HZ))
      .toEqual({ pbtInner: 1800, pbtOuter: 900 });
    expect(mapIfShiftToPbt(-1500, 450, -450, 3600, PBT_MEASURED_STEP_HZ))
      .toEqual({ pbtInner: -900, pbtOuter: -1800 });
  });
});
