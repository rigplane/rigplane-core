/**
 * Fixture-profile capabilities projection with a narrow table (MOR-4434).
 *
 * The `filterConfig` section is the exact output of the Python producer —
 * `WebServer._serialize_filter_config` (`src/rigplane/web/server.py`) run
 * over `tests/fixtures/rigs/yaesu_single_rx_fixture.toml`: per-mode
 * `defaults`/`fixed`/`table` plus `narrowTable` for the SSB and CW rules
 * that declare `narrow_table`, key order preserved (`defaults`, `fixed`,
 * `table`, `narrowTable`). The surrounding envelope mirrors the committed
 * `ftx1-capabilities.json` projection style: `modes`/`filters`/`freqRanges`
 * from the fixture TOML (`receiver_count` 1, `vfo` scheme `ab`, readback
 * `absolute`, `width_min/max` unset so the profile defaults 50/9999
 * apply), neutral audio/webrtc/tx sections, and the sorted capability
 * tags the HTTP seam variant serves (the fixture features plus the
 * `filter_width` tag the width paths require, the `narrow` feature, and
 * the server-projected `vfo_equalize` — see
 * `projected_vfo_capability_tags`). `providerGeneration`/`stateContractVersion`
 * pin the epoch the websocket seam tests pair with their state envelopes.
 * This file is NEW: the committed panel-props and state-wire goldens are
 * untouched, and no other radio's serialized output changes (the server
 * omits `narrowTable` for rules that declare none).
 */
import type { Capabilities } from '$lib/types/capabilities';
import fixtureNarrowCapabilitiesJson from './fixture-narrow-capabilities.json';

export const FIXTURE_NARROW_CAPABILITIES =
  fixtureNarrowCapabilitiesJson as unknown as Capabilities;
