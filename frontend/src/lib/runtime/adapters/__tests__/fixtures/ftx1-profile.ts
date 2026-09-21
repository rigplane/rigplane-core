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
 * values are the serializer's own output, unedited. Pins against this
 * fixture: `ftx1-filter-width-conformance.test.ts`,
 * `FilterPanel.ftx1-table5.isolated.test.ts`, and the MOR-1679 describe
 * in `panel-commands.intent.isolated.test.ts`.
 */
import type { Capabilities } from '$lib/types/capabilities';
import ftx1CapabilitiesJson from './ftx1-capabilities.json';

export const FTX1_CAPABILITIES = ftx1CapabilitiesJson as unknown as Capabilities;
