/**
 * MOR-2037 — meter conformance suite.
 *
 * Exercises every component listed in `meter-contract.ts`'s
 * `METER_REGISTRY` against the domain contract described there. Two
 * independent guarantees:
 *
 *  1. CENSUS — every `.svelte` file directly under `components-v2/meters/`
 *     is registered, every registration still names a real file, and
 *     every registered file has a mount mapping in this suite. A new
 *     meter added without registering (or without a mount mapping) fails
 *     here, not silently. Separately, every registered file actually
 *     mounts here regardless of its declared domain, so a registration
 *     under a domain with no conformance suite below cannot silently
 *     skip being exercised — but that is a weaker guarantee than
 *     conformance-checked: the mount only fails on rendering no text at
 *     all, so a real meter registered under such a domain mounts,
 *     renders its normal output, and passes here with its derivation
 *     never checked against anything, because no suite below exists for
 *     a domain nobody wrote one for.
 *  2. DOMAIN CONFORMANCE — a 'calibrated-db-rel-s9' meter's rendered
 *     S-unit/dBm text must come from `smeter-scale.ts`'s own functions,
 *     never a local reimplementation; a 'preformatted' meter must render
 *     its `displayValue` verbatim and derive no text of its own from
 *     `value`.
 *
 * HOW THE CALIBRATED CHECK DISCRIMINATES — read this before adding a case,
 * or before "fixing" a failure by loosening it. `NON_UNIFORM_CAL` below
 * seeds a deliberately non-uniform calibration curve — nine S0-S9 steps of
 * 6/3/3/6/6/18/6/3/3 dB, one stretched to 18 dB on purpose — and
 * `CALIBRATED_PROBES` reads the mounted component's text back at three
 * values along it, not one. This shape mirrors a real defect pattern
 * (MOR-2024): `components-v2/panels/meter-utils.ts`'s `formatSMeter` (a
 * sibling directory, out of this contract's scope) independently derived
 * S-unit labels via a hardcoded 6 dB/S-unit ladder — a fixed per-S-unit
 * step over what is actually a table-driven, non-uniform domain. A fixed
 * step agrees with `calibratedToSUnit` only when the curve happens to be
 * uniform; FTX-1's real, currently-live curve is not (re-derived from
 * `rigs/ftx1.toml` as of this PR: 6/3/3/3/3/3/15/9/9 dB steps).
 *
 * Why three probes and not one: a single probe only catches a fixed step
 * that disagrees AT THAT ONE POINT. This file used to probe only -11 (dB
 * relative to S9); the true answer there is S6, and the real MOR-2024
 * shape (a fixed 6 dB step counted from S0's -54) answers S7 there
 * (floor(43/6) = 7) — a real disagreement. But a fixed 7 dB step answers
 * floor(43/7) = 6 at that same point, matching S6 by coincidence and
 * passing unnoticed. Worked out generally: at a probe whose distance from
 * S0 is d and whose true S-unit is k, a fixed step N reproduces k there
 * exactly when N falls in (d/(k+1), d/k]. For the three probes below
 * (-43, -33, -11; d = 11, 21, 43; k = 2, 4, 6) those intervals are
 * (11/3, 11/2], (21/5, 21/4], and (43/7, 43/6] — and no single N lies in
 * all three, because the third interval (~6.14 to ~7.17) is entirely
 * above the other two (both end at or below 5.5). So no fixed step of any
 * size reproduces the true S-unit at all three probes at once, including
 * both N=6 (the real MOR-2024 shape, which lands in none of the three)
 * and N=7 (the near-miss this file used to let through, which lands only
 * in the third). The same three probes also rule out any constant output:
 * their true S-units (S2, S4, S6) are three different labels, and a
 * constant can match at most one.
 *
 * Probe selection is constrained to EVEN sub-S9 S-units (S2, S4, S6, not
 * S1/S3/S5/S7/S9): `LinearSMeter`'s own scale ruler permanently renders
 * the odd anchors S1/S3/S5/S7/S9 as axis labels regardless of `value`
 * (`smeter-scale.ts: getScaleMarks`), so asserting an odd result with a
 * plain substring match would risk a false pass — the ruler would already
 * contain that digit even if the live readout were wrong. This is why the
 * third probe (-11) sits in the 6 dB S6-S7 step right after the
 * deliberate 18 dB S5-S6 jump rather than inside the jump itself: every
 * point strictly inside that segment floors to the odd "S5". An even
 * S-unit never appears in the ruler, so its presence in the rendered text
 * can only come from the live readout. The "guards the guard" case below
 * fails loudly if this property is ever broken by an edit to the fixture
 * or a probe value.
 *
 * HOW THE PREFORMATTED CHECK DISCRIMINATES — it feeds a marker string no
 * calibration formula could ever produce as `displayValue`, across several
 * different `value`s, and asserts the ENTIRE rendered text is exactly
 * `label + marker` every time. Its honest limit: it cannot detect
 * derivation that is computed but never rendered (a dead-code question,
 * not a conformance one) — only text that actually reaches the DOM.
 *
 * LIMIT (calibrated check) — sampling finitely many points can never rule
 * out a wrong derivation that happens to agree with `calibratedToSUnit` at
 * every point sampled but diverges only somewhere this file doesn't probe.
 * The concrete case that matters here: a byte-identical reimplementation
 * of the same table-driven interpolation would pass every probe below and
 * is indistinguishable from the real thing by this check — that is a DRY
 * concern, not a correctness one. The three probes above are chosen to
 * catch the two wrong shapes this codebase has actually produced (a
 * constant output, a fixed per-S-unit dB step of any size — see the
 * worked-out reasoning above); they do not, and cannot, rule out every
 * conceivable formula.
 *
 * PROVEN BOTH WAYS DURING DEVELOPMENT (not committed — see this fix
 * round's PR thread for the full failure output):
 *   - A throwaway component whose entire S-unit derivation was the
 *     constant `'S6'` (dBm still correctly derived via
 *     `calibratedToDbm`/`formatDbm`, to isolate the S-unit bug) was
 *     mounted through this exact calibrated-domain assertion in place of
 *     the real `LinearSMeter` and failed it: `expected 'S6−116 dBm' to
 *     contain 'S2'` at the -43 probe, `expected 'S6−106 dBm' to contain
 *     'S4'` at the -33 probe — a constant can match at most one of three
 *     probes with three different true answers, and did, at -11.
 *   - A throwaway component reimplementing a fixed 7 dB step (the shape
 *     this file's single old probe let through) was mounted the same way
 *     and failed it too: `expected 'S1−116 dBm' to contain 'S2'` at -43,
 *     `expected 'S3−106 dBm' to contain 'S4'` at -33 — matching only at
 *     -11, per the interval reasoning above.
 *   - The real `LinearSMeter` and `BarGauge` were confirmed to pass every
 *     check below before this file was finalized.
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
import AcceptProbeMeter from '../AcceptProbeMeter.svelte';
import { METER_REGISTRY, type MeterValueDomain } from './meter-contract';

const METERS_DIR = join(__dirname, '..');

/** Every registered file's mounted-component reference. Kept in sync with
 *  METER_REGISTRY by the "has a mounted-component mapping" census case
 *  below, rather than assumed. */
const COMPONENTS: Record<string, unknown> = {
  'BarGauge.svelte': BarGauge,
  'LinearSMeter.svelte': LinearSMeter,
  'AcceptProbeMeter.svelte': AcceptProbeMeter,
};

/** Props broad enough to mount ANY registered component regardless of its
 *  declared domain: a superset of what 'preformatted' components require
 *  (value/label/displayValue — see BarGauge's Props) and what
 *  'calibrated-db-rel-s9' components accept (value, optional label — see
 *  LinearSMeter's Props). Extra props a component doesn't declare are
 *  simply ignored by Svelte's `$props()` destructuring. Used only by the
 *  domain-agnostic mount census check below — the domain-conformance
 *  suites each pass their own domain-appropriate props instead. */
const GENERIC_MOUNT_PROPS: Record<string, unknown> = {
  value: 0,
  label: 'MOUNT-CHECK',
  displayValue: 'MOUNT-CHECK',
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

  it('every METER_REGISTRY entry mounts successfully here, independent of its declared domain (a registration under a domain with no conformance suite below cannot silently skip being exercised)', () => {
    for (const { file, domain } of METER_REGISTRY) {
      const text = renderMeter(file, GENERIC_MOUNT_PROPS).textContent;
      expect(
        text,
        `${file} (domain '${domain}') rendered no text when mounted with generic smoke props — ` +
          'confirm it actually accepts value/label/displayValue before registering it.',
      ).toBeTruthy();
    }
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

// Three probes, dB relative to S9 (matching the `value` prop) — see the
// file header's "HOW THE CALIBRATED CHECK DISCRIMINATES" section for the
// full worked-out reasoning behind these specific values:
//   -43 — the 3 dB S2-S3 step (true S2)
//   -33 — a 6 dB step, S4-S5 (true S4)
//   -11 — the 6 dB S6-S7 step right after the deliberate 18 dB S5-S6 jump
//         (true S6) — not inside the jump itself, because every point
//         strictly inside it floors to the odd "S5"
const CALIBRATED_PROBES: readonly number[] = [-43, -33, -11];

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

    it.each(CALIBRATED_PROBES)(
      'guards the guard: probe %d actually lands on an EVEN sub-S9 S-unit',
      (actual) => {
        // Re-checks the INVARIANT the collision-safety argument in the file
        // header depends on (a bare "S<even digit>" reading never appears in
        // LinearSMeter's own ruler), not just today's specific values — so
        // editing NON_UNIFORM_CAL and CALIBRATED_PROBES together, and
        // landing back on an odd or over-S9 reading by mistake, still fails
        // here instead of the real check below silently losing its
        // ruler-collision-safety property.
        const sUnit = calibratedToSUnit(actual);
        const match = sUnit.match(/^S(\d)$/);
        expect(match, `expected a bare "S<digit>" reading (0-9, no "+"), got "${sUnit}"`).not.toBeNull();
        expect(
          Number(match![1]) % 2,
          `${sUnit} is ODD — it collides with the ruler's own S1/S3/S5/S7/S9 labels`,
        ).toBe(0);
      },
    );

    it.each(CALIBRATED_PROBES)(
      'renders the exact S-unit and dBm text calibratedToSUnit/formatDbm(calibratedToDbm) compute at probe %d, not a local approximation',
      (actual) => {
        const text = renderMeter(file, { value: actual }).textContent ?? '';
        expect(text).toContain(calibratedToSUnit(actual));
        expect(text).toContain(formatDbm(calibratedToDbm(actual)));
      },
    );
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
