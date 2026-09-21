/**
 * FTX-1 capabilities fixture (MOR-1679) — GENERATED, never hand-written.
 *
 * Produced offline by running the real `WebServer._serve_capabilities`
 * handler (no sockets, no radio I/O — the same construction
 * `tests/test_web_capability_guards.py` uses: a fake radio carrying the
 * real `rigs/ftx1.toml` profile resolved via `resolve_radio_profile`) and
 * capturing the JSON body it writes, at backend HEAD 60d05a42 (includes
 * the merged CAT 2508-C Table 5 corrections, PRs #3550/#3552).
 *
 * The capture is a PROJECTION of that payload: the sections the browser's
 * filter-width contract consumes (filterConfig, modes, filters,
 * filterWidthMin/Max, capabilities, vfo/receivers identity, freqRanges,
 * txBands, audioConfig/webrtc required by `validateCapabilities`), laid
 * out compactly (sorted keys; filterConfig one mode per line). The full
 * byte-faithful payload does not fit this change's line ceiling; the
 * values are the serializer's own output, unedited.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import ftx1CapabilitiesJson from './ftx1-capabilities.json';
import ftx1StateJson from './ftx1-state-unobserved-leaves.json';

export const FTX1_CAPABILITIES = ftx1CapabilitiesJson as unknown as Capabilities;

/**
 * MOR-2513 — the live FTX-1 `/api/v1/state` body captured on the bench at
 * backend 86187ed3, the head that started publishing `null` for every
 * never-observed public leaf. 108 leaves are null, among them
 * `main.filter`, `main.dataMode`, `sub.att`/`sub.preamp` and every
 * `scopeControls.*` leaf — the exact payload the desktop page rejected at
 * ingestion. Keys are sorted; every key and every null is exactly as
 * captured. An identifier scan (IP, device path, hostname, MAC, callsign,
 * username, serial number patterns) over the capture found nothing to
 * replace.
 */
export const FTX1_STATE = ftx1StateJson as unknown as ServerState;

/**
 * The same capture with every leaf the generated `state.ts` types as
 * nullable forced to `null` and its `fieldStatus` entry marked unobserved —
 * the shape a freshly connected radio that observed nothing produces.
 * `txTarget` keeps its in-band unknown-status object; the non-nullable
 * `connection`/`radioDetail`/`radioHealth`/`wsClients` subtrees keep their
 * captured values, matching what the server may actually publish.
 */
export const FTX1_STATE_FULLY_UNOBSERVED: ServerState = (() => {
  const nulled = structuredClone(FTX1_STATE);
  for (const [path, status] of Object.entries(nulled.fieldStatus ?? {})) {
    if (path === 'txTarget' || path.startsWith('connection.')
      || path.startsWith('radioDetail.') || path.startsWith('radioHealth.')) continue;
    const parts = path.split('.');
    let holder: Record<string, unknown> | null = nulled as unknown as Record<string, unknown>;
    for (const part of parts.slice(0, -1)) {
      const next: unknown = holder?.[part];
      holder = typeof next === 'object' && next !== null
        ? next as Record<string, unknown> : null;
      if (holder === null) break;
    }
    if (holder === null) continue;
    holder[parts[parts.length - 1]] = null;
    if (status) status.observed = false;
  }
  return nulled;
})();
