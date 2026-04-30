# External Usage Inventory — `icom_lan.*`

Phase 1 / Discovery artifact (Agent B). Catalogues every place **outside** `src/icom_lan/`
that imports from the `icom_lan` package. Drives the re-export shim plan (Phase 2 / Phase 4).

Method: ripgrep / static text scan; no runtime resolution. Patterns:

```
^\s*from\s+icom_lan(\.<sub>)?\s+import …
^\s*import\s+icom_lan(\.<sub>)?
```

Both top-level and indented (function-local / try-import / TYPE_CHECKING) forms are
counted. The qualifier `icom_lan_pro` is excluded (separate package).

Sources scanned:

| Source                                       | Path                                                                         |
| --- | --- |
| In-tree tests                                | `tests/` (this worktree)                                                     |
| In-tree docs                                 | `docs/` (this worktree, `*.md` and `*.py`)                                   |
| In-tree frontend                             | `frontend/` (this worktree)                                                  |
| Downstream proprietary product (`icom-lan-pro`) | `/Users/moroz/Projects/icom-lan-pro/{src,tests}` — read-only                |

---

## 1. `tests/` (this worktree)

- Distinct test files importing `icom_lan.*`: **177**
- Distinct import paths: **101**
- Total occurrences: **1151**

Top 25 import paths by occurrence:

| import path | files using it | total occurrences |
| --- | --- | --- |
| `icom_lan.commands` | 31 | 278 |
| `icom_lan.types` | 59 | 103 |
| `icom_lan.web.radio_poller` | 14 | 64 |
| `icom_lan` (package root) | 42 | 54 |
| `icom_lan.scope_render` | 2 | 46 |
| `icom_lan.radio` | 39 | 45 |
| `icom_lan.exceptions` | 35 | 41 |
| `icom_lan.web.handlers` | 15 | 36 |
| `icom_lan.radio_protocol` | 20 | 33 |
| `icom_lan.radio_state` | 16 | 23 |
| `icom_lan.web.protocol` | 6 | 23 |
| `icom_lan.web.server` | 13 | 23 |
| `icom_lan.profiles` | 17 | 21 |
| `icom_lan.rigctld.state_cache` | 17 | 20 |
| `icom_lan.web.websocket` | 7 | 20 |
| `icom_lan._connection_state` | 5 | 18 |
| `icom_lan.cli` | 7 | 18 |
| `icom_lan.rigctld.handler` | 5 | 15 |
| `icom_lan.dsp.nodes` | 1 | 15 |
| `icom_lan.audio` | 12 | 14 |
| `icom_lan.rigctld.contract` | 14 | 14 |
| `icom_lan.env_config` | 1 | 13 |
| `icom_lan.rig_loader` | 11 | 12 |
| `icom_lan.audio_bus` | 6 | 10 |
| `icom_lan.backends.yaesu_cat.radio` | 9 | 9 |

Tail (1–9 occurrences): full breakdown follows. Paths with `_` after `icom_lan.` are
private surface — those are also called out separately in §5.

| import path | files using it | total occurrences |
| --- | --- | --- |
| `icom_lan.scope` | 9 | 9 |
| `icom_lan.backends.icom7610.drivers.serial_stub` | 8 | 8 |
| `icom_lan.rigctld.server` | 7 | 8 |
| `icom_lan._civ_rx` | 4 | 8 |
| `icom_lan.web.rtc` | 1 | 8 |
| `icom_lan.backends.config` | 7 | 7 |
| `icom_lan.transport` | 3 | 6 |
| `icom_lan.audio.backend` | 4 | 5 |
| `icom_lan.audio_bridge` | 3 | 5 |
| `icom_lan.backends.factory` | 4 | 4 |
| `icom_lan.rigctld.circuit_breaker` | 4 | 4 |
| `icom_lan.backends.icom7610` | 4 | 4 |
| `icom_lan._poller_types` | 1 | 4 |
| `icom_lan.commander` | 4 | 4 |
| `icom_lan.audio.usb_driver` | 2 | 4 |
| `icom_lan.rigctld.poller` | 3 | 3 |
| `icom_lan.dsp.resample` | 1 | 3 |
| `icom_lan.auth` | 3 | 3 |
| `icom_lan.backends.yaesu_cat.parser` | 2 | 3 |
| `icom_lan.command_map` | 3 | 3 |
| `icom_lan.backends.ic7300.serial` | 3 | 3 |
| `icom_lan.web.handlers.audio` | 1 | 3 |
| `icom_lan.proxy` | 2 | 3 |
| `icom_lan.sync` | 2 | 2 |
| `icom_lan.civ` | 2 | 2 |
| `icom_lan.protocol` | 2 | 2 |
| `icom_lan.rigctld.protocol` | 2 | 2 |
| `icom_lan.backends.ic705.serial` | 2 | 2 |
| `icom_lan.backends.icom7610.serial` | 2 | 2 |
| `icom_lan.capabilities` | 2 | 2 |
| `icom_lan.dsp.nodes.nr_scipy` | 1 | 2 |
| `icom_lan.dsp.tap_registry` | 2 | 2 |
| `icom_lan.web.handlers.control` | 2 | 2 |
| `icom_lan.web.runtime_helpers` | 2 | 2 |
| `icom_lan.ic705` | 2 | 2 |
| `icom_lan.backends.ic9700.serial` | 2 | 2 |
| `icom_lan._audio_codecs` | 2 | 2 |
| `icom_lan._audio_transcoder` | 2 | 2 |
| `icom_lan.backends.yaesu_cat.transport` | 2 | 2 |
| `icom_lan.command_spec` | 2 | 2 |
| `icom_lan.commands._codec` | 2 | 2 |
| `icom_lan.audio.resample` | 1 | 1 |
| `icom_lan.audio_fft_scope` | 1 | 1 |
| `icom_lan.backends.icom7610.drivers.usb_audio` | 1 | 1 |
| `icom_lan.dsp.exceptions` | 1 | 1 |
| `icom_lan.web.dx_cluster` | 1 | 1 |
| `icom_lan.backends.icom7610.drivers.serial_civ_link` | 1 | 1 |
| `icom_lan.backends.yaesu_cat.poller` | 1 | 1 |
| `icom_lan.audio_analyzer` | 1 | 1 |
| `icom_lan.cw_auto_tuner` | 1 | 1 |
| `icom_lan.rigctld` | 1 | 1 |
| `icom_lan.web` | 1 | 1 |
| `icom_lan.meter_cal` | 1 | 1 |
| `icom_lan.backends.yaesu_cat` | 1 | 1 |
| `icom_lan._state_queries` | 1 | 1 |
| `icom_lan._shared_state_runtime` | 1 | 1 |
| `icom_lan.web.discovery` | 1 | 1 |
| `icom_lan._bridge_metrics` | 1 | 1 |
| `icom_lan._bridge_state` | 1 | 1 |
| `icom_lan.audio.lan_stream` | 1 | 1 |
| `icom_lan.discovery` | 1 | 1 |
| `icom_lan._bounded_queue` | 1 | 1 |
| `icom_lan.rigctld.audit` | 1 | 1 |
| `icom_lan.audio.dsp` | 1 | 1 |
| `icom_lan.backends.icom7610.drivers.contracts` | 1 | 1 |
| `icom_lan.dsp.pipeline` | 1 | 1 |
| `icom_lan.rigctld.utils` | 1 | 1 |
| `icom_lan.web.tls` | 1 | 1 |
| `icom_lan.usb_audio_resolve` | 1 | 1 |
| `icom_lan.radios` | 1 | 1 |
| `icom_lan.commands.tx_band` | 1 | 1 |
| `icom_lan.commands._frame` | 1 | 1 |
| `icom_lan.audio.config` | 1 | 1 |
| `icom_lan.web._delta_encoder` | 1 | 1 |
| `icom_lan.profiles_runtime` | 1 | 1 |
| `icom_lan.dsp` | 1 | 1 |

---

## 2. `docs/` (this worktree, `*.md` and `*.py`)

- Distinct doc files containing imports: **27**
- Distinct import paths: **22**
- Total occurrences: **107**

| import path | files using it | total occurrences |
| --- | --- | --- |
| `icom_lan` (package root) | 22 | 52 |
| `icom_lan.radio_protocol` | 1 | 6 |
| `icom_lan.rig_loader` | 3 | 6 |
| `icom_lan.scope` | 2 | 5 |
| `icom_lan.rigctld.contract` | 1 | 5 |
| `icom_lan.backends.factory` | 3 | 4 |
| `icom_lan.backends.config` | 3 | 4 |
| `icom_lan.scope_render` | 1 | 3 |
| `icom_lan.commands` | 1 | 3 |
| `icom_lan.rigctld.audit` | 1 | 3 |
| `icom_lan.exceptions` | 2 | 2 |
| `icom_lan.sync` | 2 | 2 |
| `icom_lan.rigctld` | 1 | 2 |
| `icom_lan.rigctld.server` | 2 | 2 |
| `icom_lan.radio_state` | 1 | 1 |
| `icom_lan.command_map` | 1 | 1 |
| `icom_lan.rigctld.handler` | 1 | 1 |
| `icom_lan.rigctld.poller` | 1 | 1 |
| `icom_lan.rigctld.circuit_breaker` | 1 | 1 |
| `icom_lan.rigctld.state_cache` | 1 | 1 |
| `icom_lan.rigctld.protocol` | 1 | 1 |
| `icom_lan.web.handlers` | 1 | 1 |

All docs imports are public (no `_*` prefix after `icom_lan.`). Each path
that the docs reference must remain importable by the same name, or the
documentation will silently break at the next build / example run.

---

## 3. `frontend/` (this worktree)

Confirmed **zero** Python files importing `icom_lan` (or `icom_lan.*`). The
frontend is TypeScript / Svelte; it speaks to the backend only over HTTP /
WebSocket / WebRTC, not via Python imports. No constraint on the
modularization from this layer.

Verification: `grep -rEn '(from|import)\s+icom_lan' frontend/` returns nothing.
`find frontend -name '*.py'` returns nothing.

---

## 4. `icom-lan-pro` (downstream proprietary product)

Path: `/Users/moroz/Projects/icom-lan-pro/{src,tests}`. Read-only here.

- Distinct files importing `icom_lan.*`: **8**
- Distinct import paths: **5**
- Total occurrences: **29**

### 4.1 Per-path breakdown

| import path | files using it | total occurrences |
| --- | --- | --- |
| `icom_lan.audio.backend` | 3 (`src/icom_lan_pro/companion/integrations/audio/bridge.py`, `src/icom_lan_pro/companion/integrations/audio/pacat_backend.py`, `tests/test_audio.py`) | 19 |
| `icom_lan.dsp.pipeline` | 4 (`src/icom_lan_pro/companion/cli/main.py`, `tests/test_dsp_integration.py`, `tests/test_proxy_server.py`, `tests/test_rnnoise.py`) | 4 |
| `icom_lan.dsp.nodes.base` | 3 (`src/icom_lan_pro/companion/cli/main.py`, `tests/test_dsp_integration.py`, `tests/test_proxy_server.py`) | 3 |
| `icom_lan.dsp.exceptions` | 2 (`src/icom_lan_pro/dsp/rnnoise.py`, `tests/test_rnnoise.py`) | 2 |
| `icom_lan.audio.dsp` | 1 (`src/icom_lan_pro/companion/integrations/audio/bridge.py`) | 1 |

### 4.2 Top 10 most-used import paths in `icom-lan-pro`

There are only 5 paths total; ranked by occurrence:

1. `icom_lan.audio.backend` — 19 occurrences (almost all in `tests/test_audio.py`, function-local imports; plus the production backend bridges)
2. `icom_lan.dsp.pipeline` — 4
3. `icom_lan.dsp.nodes.base` — 3
4. `icom_lan.dsp.exceptions` — 2
5. `icom_lan.audio.dsp` — 1

### 4.3 Phase 1 escalation gate

Threshold: **>30 import sites in `icom-lan-pro`** triggers an escalation per the
orchestrator brief.

Actual: **29 occurrences across 8 files / 5 distinct paths**.

**NOT exceeded.** The downstream surface is small and concentrated on two
sub-namespaces (`icom_lan.audio.*` and `icom_lan.dsp.*`). Both are obvious
candidates to remain stable contracts in the new layer scheme; both already live
in dedicated subpackages today, so no shim is needed if they keep their import
paths. If either is moved, a single `from … import *` shim per file path is
sufficient.

### 4.4 No internal-symbol leaks from `icom-lan-pro`

Zero hits for `icom_lan(\.<sub>)*\._<name>` in `icom-lan-pro`. Pro consumes only
the public surface. This is good news for the migration.

---

## 5. Internal-symbol leaks (private name reach-through)

Definition: any external import path where the segment immediately after
`icom_lan.` (or the trailing component within a subpackage) starts with `_`.
These are shim landmines for Phase 2.

- Total leak occurrences in `tests/`: **43** (across **15** test files)
- Total leak occurrences in `docs/`: **0**
- Total leak occurrences in `icom-lan-pro`: **0**

### 5.1 All test-side internal imports (verbatim)

```
tests/test_sync_coverage.py:9:from icom_lan._connection_state import RadioConnectionState
tests/test_lifecycle_diagnostics.py:14:from icom_lan._connection_state import RadioConnectionState
tests/test_bsr_band_switching.py:42:        from icom_lan._civ_rx import CivRuntime
tests/test_bsr_band_switching.py:49:        from icom_lan._civ_rx import CivRuntime
tests/test_bsr_band_switching.py:58:        from icom_lan._civ_rx import CivRuntime
tests/test_yaesu_cat_poller.py:872:    from icom_lan._poller_types import SetApf
tests/test_yaesu_cat_poller.py:886:    from icom_lan._poller_types import SetApf
tests/test_yaesu_cat_poller.py:905:    from icom_lan._poller_types import SetPower
tests/test_yaesu_cat_poller.py:923:    from icom_lan._poller_types import SetPower
tests/test_state_queries.py:9:from icom_lan._state_queries import build_state_queries
tests/test_web_audio_streaming_profile.py:18:from icom_lan._audio_codecs import decode_ulaw_to_pcm16
tests/test_shared_state_runtime.py:7:from icom_lan._shared_state_runtime import (
tests/test_audio_bridge.py:12:from icom_lan._bridge_metrics import BridgeMetrics
tests/test_audio_bridge.py:13:from icom_lan._bridge_state import BridgeState, BridgeStateChange
tests/test_audio_transcoder.py:9:from icom_lan._audio_transcoder import PcmAudioFormat, PcmOpusTranscoder
tests/test_civ_rx_coverage.py:41:from icom_lan._civ_rx import CIV_HEADER_SIZE
tests/test_rig_ic7300.py:12:from icom_lan.commands._codec import filter_hz_to_index, filter_index_to_hz
tests/test_reconnect.py:8:from icom_lan._connection_state import RadioConnectionState
tests/test_bounded_queue.py:9:from icom_lan._bounded_queue import BoundedQueue
tests/test_audio_transcoder_coverage.py:21:from icom_lan._audio_transcoder import (
tests/test_audio_codecs.py:5:from icom_lan._audio_codecs import decode_ulaw_to_pcm16
tests/test_docs_runtime_sync.py:11:from icom_lan._connection_state import RadioConnectionState
tests/test_civ_rx_mixin_host.py:14:from icom_lan._civ_rx import CivRuntime
tests/test_dsp_filter_family.py:20:from icom_lan.commands._codec import (
tests/test_delta_encoder.py:7:from icom_lan.web._delta_encoder import DeltaEncoder, apply_delta
tests/test_tx_band_edge.py:16:from icom_lan.commands._frame import _CMD_TX_BAND_EDGE
tests/test_tx_band_edge.py:194:        from icom_lan._civ_rx import CivRuntime
tests/test_tx_band_edge.py:218:        from icom_lan._civ_rx import CivRuntime
tests/test_tx_band_edge.py:236:        from icom_lan._civ_rx import CivRuntime
tests/test_radio_coverage.py:122:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:161:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:171:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:180:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:204:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1282:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1334:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1529:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1553:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1582:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1611:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1637:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1660:    from icom_lan._connection_state import RadioConnectionState
tests/test_radio_coverage.py:1681:    from icom_lan._connection_state import RadioConnectionState
```

### 5.2 Distinct private modules reached into

| private module | test files |
| --- | --- |
| `icom_lan._connection_state` | `test_sync_coverage.py`, `test_lifecycle_diagnostics.py`, `test_reconnect.py`, `test_docs_runtime_sync.py`, `test_radio_coverage.py` |
| `icom_lan._civ_rx` | `test_bsr_band_switching.py`, `test_civ_rx_coverage.py`, `test_civ_rx_mixin_host.py`, `test_tx_band_edge.py` |
| `icom_lan._poller_types` | `test_yaesu_cat_poller.py` |
| `icom_lan._state_queries` | `test_state_queries.py` |
| `icom_lan._audio_codecs` | `test_web_audio_streaming_profile.py`, `test_audio_codecs.py` |
| `icom_lan._shared_state_runtime` | `test_shared_state_runtime.py` |
| `icom_lan._bridge_metrics` | `test_audio_bridge.py` |
| `icom_lan._bridge_state` | `test_audio_bridge.py` |
| `icom_lan._audio_transcoder` | `test_audio_transcoder.py`, `test_audio_transcoder_coverage.py` |
| `icom_lan._bounded_queue` | `test_bounded_queue.py` |
| `icom_lan.commands._codec` | `test_rig_ic7300.py`, `test_dsp_filter_family.py` |
| `icom_lan.commands._frame` | `test_tx_band_edge.py` |
| `icom_lan.web._delta_encoder` | `test_delta_encoder.py` |

Migration implication: when these modules move, every test above still has to
resolve the same dotted path or be edited. Two options for Phase 2:
1.  Preserve the `icom_lan._foo` path forever as a re-export shim (cheapest;
    keeps tests untouched).
2.  Treat tests as in-tree and rewrite imports as part of the move (acceptable
    because tests are already part of this refactor's scope).

Recommendation will be made in the discovery doc; both are feasible.

---

## 6. Dynamic / lazy imports of `icom_lan`

Patterns scanned:
```
importlib.import_module("icom_lan…
__import__("icom_lan…
```

### `tests/`
| file:line | call |
| --- | --- |
| `tests/test_naming_parity.py:121` | `commands_mod = importlib.import_module("icom_lan.commands")` |

One occurrence. Resolves a public path.

### `docs/`
None.

### `icom-lan-pro`
None.

Implication: no hidden runtime-only paths to worry about. The static catalogue
above is essentially exhaustive.

---

## Methodology notes / caveats

- Static text scan only. Re-exports are not de-duplicated: if a test does
  `from icom_lan import Foo` and `Foo` is actually defined in
  `icom_lan.types`, both paths are independent line items.
- Function-local / TYPE_CHECKING / try-import imports are all counted (relevant
  for `tests/test_radio_coverage.py`, which imports `_connection_state` inside
  many test methods — that file alone accounts for 13 of the 22
  `_connection_state` hits).
- The orchestrator brief's own threshold (>30 sites in `icom-lan-pro`) is
  evaluated against total occurrences (29). It is not exceeded under any
  reasonable counting (8 files, 5 paths, 29 occurrences).
- `frontend/` was verified explicitly. Pattern returned zero results in any
  file extension.
