/**
 * Xiegu G7250 live-captured profile fixture (MOR-4562).
 *
 * `g7250-state.json` / `g7250-capabilities.json` are byte-faithful copies of
 * `GET /api/v1/state` and `GET /api/v1/capabilities` (read-only) taken from
 * the owner's RigPlane Pro app while it was connected to a real G7250 over the
 * radio's network port (firmware V1.0.1, wfview/control mode, CI-V 0xA0).
 *
 *   Captured:     2026-11-18 13:25 EST, Core 3.2.1 (rigplane-pro 0.10.0-beta.16-rc.6)
 *   Radio model:  G7250 (single receiver, `vfoScheme: 'ab'`, `vfoReadback: 'none'`)
 *
 * What it proves: `main.freqHz` / `main.mode` / `main.filter` are observed and
 * fresh (CI-V 0x25 / 0x26 replies) while `main.activeSlot` and
 * `main.vfoA.*` / `main.vfoB.*` are never observed, and the screen showed no
 * frequency, mode or filter. Not registered in `conformance/profiles.ts` on
 * purpose (that registry carries the provenance sidecar guard).
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import g7250StateJson from './g7250-state.json';
import g7250CapabilitiesJson from './g7250-capabilities.json';

export const G7250_STATE = g7250StateJson as unknown as ServerState;
export const G7250_CAPABILITIES = g7250CapabilitiesJson as unknown as Capabilities;
