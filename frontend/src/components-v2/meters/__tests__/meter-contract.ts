/**
 * MOR-2037 meter conformance contract (not itself a test file — see
 * `meter-contract.test.ts` for the suite this drives).
 *
 * `components-v2/meters/` has exactly two prop shapes today for how a meter
 * component may receive a value that originates in a calibrated meter
 * domain (per-rig curves over engineering units —
 * `docs/architecture/level-meter-calibrated-domain.md`):
 *
 *  - 'calibrated-db-rel-s9': the component receives the S-meter's
 *    calibrated dB-relative-to-S9 reading directly (`LinearSMeter`'s
 *    `value` prop) and derives S-unit/dBm text from it. The one thing it
 *    must NEVER do is derive that text with its own formula — it must call
 *    `smeter-scale.ts`'s functions (`calibratedToSUnit` / `calibratedToDbm`
 *    / `formatDbm`), the correct, table-driven derivation for this
 *    directory. The risk this guards against is not hypothetical: MOR-2024
 *    found `components-v2/panels/meter-utils.ts`'s `formatSMeter` (a
 *    sibling directory, out of this contract's scope) independently
 *    reimplementing the same mapping as a hardcoded 6 dB/S-unit ladder — a
 *    fixed per-S-unit step over what is actually a table-driven,
 *    non-uniform domain. A fixed step agrees with the table-driven
 *    interpolation only when the curve happens to be uniform — FTX-1's
 *    real, currently-live S0-S9 steps are 6/3/3/3/3/3/15/9/9 dB, not a
 *    flat 6 (re-derived from `rigs/ftx1.toml`'s own
 *    `[[meters.s_meter.calibration]]` table as of this PR), so a
 *    fixed-step derivation disagrees with the table at FTX-1's own S6-S9
 *    range.
 *  - 'preformatted': the component receives an ALREADY-FORMATTED display
 *    string (`BarGauge`'s `displayValue`) plus a domain-FREE normalized
 *    0-1 `value` that drives only the bar-fill and peak-marker geometry,
 *    never displayed text. It must render that string verbatim and derive
 *    no calibration-domain text of its own — the calibration already
 *    happened upstream, in whatever wiring built its props.
 *
 * `METER_REGISTRY` is the exhaustive list of `.svelte` files directly under
 * `components-v2/meters/` and which of the two domains each one
 * implements. `meter-contract.test.ts`'s census check re-derives the real
 * directory listing and fails if a `.svelte` file exists with no entry
 * here (a new meter added without registering) or an entry here names a
 * file that no longer exists (a stale registration). The mounted
 * conformance checks — and the documented, honest limits of what each one
 * can and cannot detect — live in that file, next to the assertions they
 * describe, not here.
 *
 * This is a hand-maintained array, not a live `register()` call some
 * meter module invokes at load time: with two components in the census, a
 * runtime registration mechanism is not earned yet (rule of three) — the
 * census check in the test file gives the same "a new meter cannot
 * silently skip registering" guarantee for a fraction of the machinery.
 */

export type MeterValueDomain = 'calibrated-db-rel-s9' | 'preformatted';

// Not exported: nothing outside this file needs the element type by name —
// `METER_REGISTRY`'s own declared type below is enough for consumers that
// destructure its entries (MOR-2037 fix round: this used to be exported
// with no importer anywhere in the repo).
interface MeterRegistration {
  /** Filename directly under `components-v2/meters/`, e.g. 'BarGauge.svelte'. */
  readonly file: string;
  readonly domain: MeterValueDomain;
}

export const METER_REGISTRY: readonly MeterRegistration[] = [
  { file: 'BarGauge.svelte', domain: 'preformatted' },
  { file: 'LinearSMeter.svelte', domain: 'calibrated-db-rel-s9' },
];
