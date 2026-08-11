/**
 * IC-7300 live-captured profile fixture (MOR-1428, Tier 2 v1).
 *
 * `ic7300-state.json` / `ic7300-capabilities.json` are a byte-faithful
 * capture of `/api/v1/state` and `/api/v1/capabilities` from the live bench
 * IC-7300 stand, taken with `scripts/capture-profile.mjs` (read-only GET,
 * no CI-V writes, no WS traffic):
 *
 *   node scripts/capture-profile.mjs http://192.168.55.152:8099 ic7300
 *
 * Provenance
 * ----------
 *   Base URL:      http://192.168.55.152:8099 (bench stand, MOR-1409/MOR-1421
 *                   walkthrough hardware)
 *   Radio model:    IC-7300 (single-receiver, `vfoScheme: 'ab'`,
 *                   `vfoReadback: 'selected_unselected'`)
 *   Captured:       2026-08-11T13:39:37.080Z
 *   Backend HEAD:   e0b19814 (`origin/main`, includes the MOR-1409 A07-A15
 *                   authority migration through c96ae052 plus the MOR-1418/
 *                   MOR-1421/MOR-1423/MOR-1419/MOR-1427 single-RX fix wave —
 *                   PRs #2372/#2374/#2376/#2373/#2377)
 *
 * The stand's `stateContractVersion`/`providerGeneration` (both `1`) match
 * between the two payloads, as `toSpectrumAuthority` requires.
 *
 * What this fixture actually proves, in one line: `active` reads
 * observed:false/availability:'missing' on this radio — the exact
 * structurally-unobservable shape MOR-1418/MOR-1421/MOR-1423 fixed — while
 * `main.freqHz`/`main.mode`/`main.filter`/`main.att`/`main.preamp`/
 * `main.rfGain`/`main.agc`/`main.nb`/`main.nr`/`main.afLevel`/`split` are
 * genuinely observed, and `ritOn`/`ritFreq`/`ritTx`/`micGain`/`main.nbLevel`/
 * `main.activeSlot`/`main.vfoA.*`/`main.vfoB.*` are genuinely NOT — this is a
 * live radio's real, partial observation state, not a synthetic all-or-
 * nothing fixture. `mor1428-ic7300-conformance.isolated.test.ts` pins both
 * halves: the families the fix wave revived, and the families that still
 * correctly fail closed because a DIFFERENT leaf was never confirmed.
 *
 * Re-capture: re-run the script above against the live stand and commit the
 * refreshed JSON pair plus an updated header comment here (capture date,
 * backend HEAD SHA). The two JSON files carry no embedded metadata of their
 * own on purpose — they must stay exactly what the API returned.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import ic7300StateJson from './ic7300-state.json';
import ic7300CapabilitiesJson from './ic7300-capabilities.json';

export const IC7300_STATE = ic7300StateJson as unknown as ServerState;
export const IC7300_CAPABILITIES = ic7300CapabilitiesJson as unknown as Capabilities;
