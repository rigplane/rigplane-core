/**
 * MOR-2037 — meter conformance suite.
 *
 * Exercises every component listed in `meter-contract.ts`'s
 * `METER_REGISTRY` against the domain contract described there. Two
 * independent guarantees:
 *
 *  1. CENSUS — every `.svelte` file directly under `components-v2/meters/`
 *     is registered, every registration still names a real file, and every
 *     registered file has a mount mapping in this suite. A new meter added
 *     without registering (or without a mount mapping) fails here, not
 *     silently.
 *  2. DOMAIN CONFORMANCE — a 'calibrated-db-rel-s9' meter's rendered
 *     S-unit/dBm text must come from `smeter-scale.ts`'s own functions,
 *     never a local reimplementation; a 'preformatted' meter must render
 *     its `displayValue` verbatim and derive no text of its own from
 *     `value`.
 *
 * HOW THE CALIBRATED CHECK DISCRIMINATES — read this before adding a case,
 * or before "fixing" a failure by loosening it. `NON_UNIFORM_CAL` below
 * seeds a deliberately non-uniform calibration curve (an 18 dB jump from
 * S5 to S6, versus 3-6 dB everywhere else) and reads the mounted
 * component's text back for `CALIBRATED_PROBE_VALUE`, which lands inside
 * that jump. This shape is not arbitrary: it mirrors a real defect pattern
 * (MOR-2024) found elsewhere in this codebase — `components-v2/panels/
 * meter-utils.ts`'s `formatSMeter` (a sibling directory, out of this
 * contract's scope, with its own separately-tracked fix in flight)
 * independently derives S-unit labels via a hardcoded 6 dB/S-unit ladder,
 * which agrees with the table-driven `calibratedToSUnit` only when the
 * curve happens to be uniform — FTX-1's real, currently-live curve is not
 * (re-derived from `rigs/ftx1.toml` as of this PR: 6/3/3/3/3/3/15/9/9 dB
 * steps), and the two derivations disagree there. A component INSIDE this
 * directory that re-derives its S-unit from `value` with any formula
 * assuming even steps disagrees with `calibratedToSUnit` at this probe and
 * fails here.
 *
 * Two honest limits on that check:
 *   - A component that reimplements an EQUIVALENT, byte-identical
 *     piecewise interpolation over the same table would still pass. That
 *     is a DRY concern, not a correctness one, and this check cannot see
 *     it — it only catches actual numeric disagreement.
 *   - The probe is chosen to land on an EVEN S-unit (S6) on purpose:
 *     `LinearSMeter`'s own scale ruler permanently renders the ODD anchors
 *     S1/S3/S5/S7/S9 as axis labels regardless of `value`
 *     (`smeter-scale.ts: getScaleMarks`). Asserting an odd result with a
 *     plain substring match would risk a false pass, because the ruler
 *     would already contain that digit even if the live readout were
 *     wrong. An even S-unit never appears in the ruler, so its presence in
 *     the rendered text can only come from the live readout. The
 *     "guards the guard" case below fails loudly if this property is ever
 *     broken by an edit to the fixture or the probe value.
 *
 * HOW THE PREFORMATTED CHECK DISCRIMINATES — it feeds a marker string no
 * calibration formula could ever produce as `displayValue`, across several
 * different `value`s, and asserts the ENTIRE rendered text is exactly
 * `label + marker` every time. Its honest limit: it cannot detect
 * derivation that is computed but never rendered (a dead-code question,
 * not a conformance one) — only text that actually reaches the DOM.
 *
 * PROVEN BOTH WAYS DURING DEVELOPMENT (not committed; see the MOR-2037 PR
 * body for the actual failure message):
 *   - A throwaway component reimplementing the MOR-2024 hardcoded ladder
 *     was mounted through this exact calibrated-domain assertion and
 *     failed it: `expected 'S7' to contain 'S6'` — the ladder's
 *     uniform-step guess (S7) against the real table's answer (S6) at the
 *     same probe value used below.
 *   - The real `LinearSMeter` and `BarGauge` were confirmed to pass both
 *     checks below before this file was finalized.
 */
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { Capabilities, MeterCalPoint } from '$lib/types/capabilities';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { calibratedToSUnit, calibratedToDbm, formatDbm } from '../smeter-scale';
import BarGauge from '../BarGauge.svelte';
import LinearSMeter from '../LinearSMeter.svelte';
import { METER_REGISTRY, type MeterValueDomain } from './meter-contract';

const METERS_DIR = join(__dirname, '..');

/** Every registered file's mounted-component reference. Kept in sync with
 *  METER_REGISTRY by the "has a mounted-component mapping" census case
 *  below, rather than assumed. */
const COMPONENTS: Record<string, unknown> = {
  'BarGauge.svelte': BarGauge,
  'LinearSMeter.svelte': LinearSMeter,
};

function byDomain(domain: MeterValueDomain) {
  return METER_REGISTRY.filter((m) => m.domain === domain);
}

// ── mounting plumbing (shared by both domains) ──────────────────────────────

let mountedComponents: ReturnType<typeof mount>[] = [];
let mountedRoots: HTMLElement[] = [];

function renderMeter(file: string, props: Record<string, unknown>): HTMLElement {
  const Component = COMPONENTS[file];
  const target = document.createElement('div');
  document.body.appendChild(target);
  mountedRoots.push(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(Component as any, { target, props });
  flushSync();
  mountedComponents.push(component);
  return target;
}

afterEach(() => {
  mountedComponents.forEach((c) => unmount(c));
  mountedRoots.forEach((r) => r.remove());
  mountedComponents = [];
  mountedRoots = [];
});

// ── census: the registry is exhaustive, current, and fully wired ───────────

describe('METER_REGISTRY census (MOR-2037)', () => {
  const svelteFiles = readdirSync(METERS_DIR).filter((f) => f.endsWith('.svelte'));

  it('discovers at least one real meter component (guards against a silently empty directory listing)', () => {
    expect(svelteFiles.length).toBeGreaterThan(0);
  });

  it('every .svelte file directly under components-v2/meters/ is registered', () => {
    const registered = new Set(METER_REGISTRY.map((m) => m.file));
    const unregistered = svelteFiles.filter((f) => !registered.has(f));
    expect(
      unregistered,
      `${unregistered.join(', ')} — add a METER_REGISTRY entry (meter-contract.ts) and a ` +
        'conformance case before merging a new meter component.',
    ).toEqual([]);
  });

  it('every METER_REGISTRY entry still names a file that exists (no stale registrations)', () => {
    const stale = METER_REGISTRY.filter((m) => !svelteFiles.includes(m.file)).map((m) => m.file);
    expect(stale, `${stale.join(', ')} — remove the stale METER_REGISTRY entry`).toEqual([]);
  });

  it('every METER_REGISTRY entry has a mounted-component mapping in this suite', () => {
    const missing = METER_REGISTRY.filter((m) => !(m.file in COMPONENTS)).map((m) => m.file);
    expect(
      missing,
      `${missing.join(', ')} — add it to this file's COMPONENTS map so the conformance ` +
        'checks below can actually mount it.',
    ).toEqual([]);
  });
});

// ── 'calibrated-db-rel-s9' domain ───────────────────────────────────────────

// Synthetic fixture — not a real radio's numbers, matching the convention
// LinearSMeter.test.ts's IC7610_LIKE_CAL already documents for this exact
// directory. Deliberately NON-UNIFORM (see file header) so a local
// reimplementation that assumes even steps disagrees with the real
// interpolation somewhere in range.
const NON_UNIFORM_CAL: MeterCalPoint[] = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -45, label: 'S2' },
  { raw: 78, actual: -42, label: 'S3' },
  { raw: 104, actual: -36, label: 'S4' },
  { raw: 130, actual: -30, label: 'S5' },
  { raw: 156, actual: -12, label: 'S6' }, // +18 dB step, vs. 3-6 dB elsewhere
  { raw: 182, actual: -6, label: 'S7' },
  { raw: 208, actual: -3, label: 'S8' },
  { raw: 230, actual: 0, label: 'S9' },
  { raw: 255, actual: 20, label: 'S9+20' },
];

// -11 dB-rel-S9 lands inside NON_UNIFORM_CAL's deliberate S5->S6 jump and
// interpolates to a fractional S-unit whose floor is the EVEN "S6" — see
// file header for why the probe must land on an even S-unit specifically.
const CALIBRATED_PROBE_VALUE = -11;

function makeCaps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'test-radio',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    ...overrides,
  };
}

describe.each(byDomain('calibrated-db-rel-s9'))(
  "'$file': calibrated-db-rel-s9 domain conformance (MOR-2037)",
  ({ file }) => {
    beforeEach(() => {
      setCapabilities(makeCaps({ meterCalibrations: { s_meter: NON_UNIFORM_CAL } }));
    });
    afterEach(() => {
      clearCapabilities();
    });

    it('guards the guard: the probe fixture actually lands on an EVEN sub-S9 S-unit', () => {
      // Re-checks the INVARIANT the collision-safety argument in the file
      // header depends on (a bare "S<even digit>" reading never appears in
      // LinearSMeter's own ruler), not just today's specific value — so
      // editing NON_UNIFORM_CAL and CALIBRATED_PROBE_VALUE together, and
      // landing back on an odd or over-S9 reading by mistake, still fails
      // here instead of the real check below silently losing its
      // ruler-collision-safety property.
      const sUnit = calibratedToSUnit(CALIBRATED_PROBE_VALUE);
      const match = sUnit.match(/^S(\d)$/);
      expect(match, `expected a bare "S<digit>" reading (0-9, no "+"), got "${sUnit}"`).not.toBeNull();
      expect(
        Number(match![1]) % 2,
        `${sUnit} is ODD — it collides with the ruler's own S1/S3/S5/S7/S9 labels`,
      ).toBe(0);
    });

    it('renders the exact S-unit and dBm text calibratedToSUnit/formatDbm(calibratedToDbm) compute, not a local approximation', () => {
      const text = renderMeter(file, { value: CALIBRATED_PROBE_VALUE }).textContent ?? '';
      expect(text).toContain(calibratedToSUnit(CALIBRATED_PROBE_VALUE));
      expect(text).toContain(formatDbm(calibratedToDbm(CALIBRATED_PROBE_VALUE)));
    });
  },
);

// ── 'preformatted' domain ───────────────────────────────────────────────────

const MARKER = 'ZQ-9137-MARK'; // no calibration formula could ever produce this string
const LABEL = 'PROBE';

describe.each(byDomain('preformatted'))(
  "'$file': preformatted domain conformance (MOR-2037)",
  ({ file }) => {
    it.each([0, 0.25, 0.6, 1])(
      'renders displayValue verbatim for value=%s — the rendered text is nothing else (no text derived from value)',
      (value) => {
        const text = renderMeter(file, { value, label: LABEL, displayValue: MARKER }).textContent;
        expect(text).toBe(`${LABEL}${MARKER}`);
      },
    );
  },
);
