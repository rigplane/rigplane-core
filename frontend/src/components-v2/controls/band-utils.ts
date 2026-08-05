/**
 * Re-export shim (MOR-1294). The implementations moved to
 * `$lib/radio/band-plan` so the `band` fact group's adapter can consume them
 * — `lib/runtime/**` may not import from `components-v2/**` (eslint zone),
 * and a second copy of the band-plan lookup is exactly the forked derivation
 * the fact layer exists to prevent. Every existing importer of this module
 * keeps working unchanged; see `band-plan.ts` for the doc comments.
 */
export { flattenBands, findActiveBand, type FlatBand } from '$lib/radio/band-plan';
