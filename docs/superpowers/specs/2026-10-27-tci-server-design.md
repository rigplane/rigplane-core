# TCI Server Design

Status: draft for review
Date: 2026-10-27
Linear: MOR-3821
Scope: public open-core `rigplane-core`; a TCI (Transceiver Control Interface)
server so TCI client applications can use any RigPlane radio. Docs only: this
file adds no code.

## Summary

Core already serves Hamlib `rigctld` for CAT. Some applications cannot use it:
the logger OpsLog speaks TCI (and FLRig/OmniRig) but not Hamlib as a client.
TCI also carries RX and TX audio over the same WebSocket as control, which
removes the virtual audio cable for digital-mode programs (WSJT-X Improved,
JTDX, MSHV).

The design has two layers inside Core (coordinator decision of 2026-10-27):

- **(a) `rigplane.tci`, a radio-agnostic TCI protocol package.** Message
  parse/encode, binary stream frame formats, and the WebSocket session and
  handshake. It imports nothing from the rest of Core (only the standard
  library and itself), so it can be lifted into a standalone MIT package
  without a rewrite. The rule is enforced by an import-linter contract and a
  test (section 4.1). No separate repository or package is created now; it is
  extracted only when a second user outside Core appears.
- **(b) `rigplane.tci_adapter`, one thin adapter.** A sibling UI-server
  package next to `rigplane.rigctld` and `rigplane.web`. It is the only place
  that knows about Core, and it

  - translates TCI text commands to the same `CommandIntent` pipeline that
    `rigctld` and the Web UI already use;
  - reads state from the same `StateStore` and pushes changes to every client;
  - keys the transmitter **only** through the existing `ManagedTxAuthority`;
  - moves audio through the existing `AudioSession` (RX subscription, TX
    lease) behind the existing fail-closed TX gate.

It adds no radio-specific code, no second TX path, no second audio path and no
second WebSocket implementation. Where the existing mechanism lives in the wrong
place to be shared (inside `rigctld` or inside `web`), the design extracts it
first (section 11, PRs P0a to P0e) instead of copying it. The one existing
RFC 6455 implementation moves into layer (a), so `web` and the adapter share it.

### Decisions

| # | Decision | Section |
|---|---|---|
| D1 | Two layers: leaf protocol package `rigplane.tci` (imports nothing from Core) and one thin adapter `rigplane.tci_adapter`, sibling of `web` and `rigctld` | 4 |
| D2 | One RFC 6455 implementation: move `web/websocket.py` into the leaf as `tci/ws.py`; the Host/Origin guard moves to `core/host_guard.py` and is injected into the leaf; `web` keeps shims | 4.2 |
| D10 | The leaf boundary is enforced by an import-linter contract plus an AST test (stdlib and itself only); no separate repo or package until a second outside user exists | 4.1 |
| D3 | TCI is a new command *ingress* (`source="tci"`), not a new command path | 5.1 |
| D4 | TX only through `ManagedTxAuthority`; the managed-key choreography now private to `rigctld` is extracted and shared | 7 |
| D5 | On a radio without the managed composition, TCI refuses PTT (no legacy unsupervised write) | 7.2 |
| D6 | `tune` and `tune_drive` are not honoured in the first release; answered as "not tuning" | 6.4, 7.5 |
| D7 | `trx_count:1` first; dual-receiver radios are a later extension | 5.3 |
| D8 | Default `127.0.0.1:50001`, opt-in flag, rejects browser `Origin` | 9 |
| D9 | Conformance suite uses `eesdr-tci` as client plus spec-derived fixtures; real-client behaviour is pinned by recorded client-to-server transcripts | 10 |

## 1. Goals and non-goals

Goals

- OpsLog, JTDX, WSJT-X Improved, MSHV and loggers connect, read radio state,
  tune, select mode, switch split, key (where the radio supports managed TX) and,
  in the audio phase, exchange RX/TX audio.
- Same safety as every other ingress: a TCI client never keys around the
  authority, and when it goes away its lease is released through the authority
  (7.4). What that guarantees is bounded by the configured software TOT and by
  an uncertain-release outcome, both stated in 7.4; this document does not
  promise more.
- Work for every radio Core supports through the `Radio` capability protocols,
  with radio facts taken from profiles and capabilities, never from `if model ==`.

Non-goals

- IQ streams, spectrum/panorama, spots on a panorama, line-out recording,
  E-Coder and hardware-sensor commands (TCI phase 4, only if a client needs them).
- CW macros and CW keying over TCI (`cw_macros`, `cw_msg`, `keyer`): they start
  TX and need their own managed-TX semantics. A follow-up, not this program.
- The 200 ms "parameter monopoly" and band/mode memory that ExpertSDR3 does
  (spec section 3.5). Core is last-write-wins on a single radio.
- A TCI client library, or any code copied from AetherSDR (GPL-3.0). See 12.

## 2. Evidence base and its limits

Every statement about *external* behaviour below says how it was established.
Nothing here was captured from a live TCI session: AetherSDR was not running
(`bridge_status` returned no instances) and no TCI client was run.

| Source | What was read | Used for |
|---|---|---|
| ExpertSDR3/TCI `TCI Protocol 2.1.pdf` (MIT), extracted with `pdftotext -layout` on 2026-10-27 | sections 3.1 to 3.5, 4.1 to 4.7 | wire format, command tables, stream header, format notes |
| `ars-ka0s/eesdr-tci` 0.1.0 (BSD-3-Clause-Clear), `README.md`, `pyproject.toml`, `eesdr_tci/tci.py` (command table, packet class), `eesdr_tci/listener.py` | those files | what the test client accepts and sends |
| AetherSDR docs (GPL-3.0; docs only, no source): `docs/user/docs/tci-server.md`, `docs/user/docs/generated/tci-commands.md`, `docs/architecture/tci-routing-ordering.md` | whole files | second-hand description of how WSJT-X Improved 3.1.0, JTDX 2.2.159 and wfview drive a TCI server, and of server-side pitfalls |
| OpsLog wiki, page `CAT-Control` | TCI section | how OpsLog uses TCI |
| WSJT-X Improved / JTDX source | **not read** | n/a |

Consequences:

- Client behaviour in 5.2 is *reported*, not observed. Capture C0 (section 10.5)
  turns each reported fact into a recorded transcript before the PR that depends
  on it merges. Where reports disagree or are silent the doc says "open".
- The spec PDF is dated September 2026 and describes version 2.1. `eesdr-tci`
  is written against version 2.0 (its README); its command dictionary lacks
  `CW_MON`, `HW_SENSORS`, `HW_SENSORS_ENABLE` and `LINEOUT_STREAM` (stream type 4).
  The server must never emit anything outside that dictionary (10.2).

## 3. Existing pieces in Core

Citations are file plus symbol, per the repo's documentation rule. All read on
`origin/main` at `a12f992e1`.

| Need | Existing piece | Role for TCI |
|---|---|---|
| Hamlib server, model for the new one | `rigctld/server.py: RigctldServer` (accept loop, per-connection `rigctld-client-N` owner id, `_release_session_tx`), `rigctld/handler.py: RigctldHandler`, `rigctld/contract.py: RigctldConfig` | structure and lifecycle to mirror |
| Shared radio API | `core/radio_protocol.py: Radio` and the `*Capable` protocols (`SplitCapable`, `RitXitCapable`, `LevelsCapable`, `PowerControlCapable`, `RelativeVfoFrequencyWritable`, `AudioTransport`) | what TCI commands map to |
| Command ingress | `core/command_service.py: command_intent_from_request`, `core/command_dispatch.py: prepare_command_intent`, `enqueue_command_intent`; `core/state_pipeline_contracts.py: CommandSource` | TCI writes become intents (`source="tci"`) |
| State reads and change feed | `core/state_store.py: StateStore.snapshot, delta_since`; `core/tx_observation.py: OBSERVED_PTT_PATH, project_observed_ptt`; `StateModelService.ensure_fresh` | init burst and push notifications |
| Managed TX authority | `runtime/managed_tx_authority.py: ManagedTxAuthority` (`start_ptt_submission`, `submit_ptt`, `owner_disconnect`, `held_by_other`, `radio_tx_refusal_nowait`, `snapshot`), `runtime/managed_tx_state.py: ManagedTxOutcome`, `runtime/managed_tx_ingress.py: bind_managed_tx, refuse_key_without_owner`, `core/tx_safety.py: TxSource`, `runtime/controller_authority.py: ControllerAuthority`, `runtime/managed_tx_config.py: DEFAULT_MANAGED_TX_TOT_SECONDS` | the only way to key |
| Managed-key choreography | `rigctld/server.py: _ManagedPttReady` and the PTT block of `_handle_client`; `rigctld/handler.py: _RigctldCommandExecutor._execute_managed_ptt, _execute_managed_tuner` | **currently private to rigctld; extracted in P0c** |
| RX audio | `audio/session.py: AudioSession.subscribe_rx`, `audio/lan_stream.py: AudioPacket`, `audio/pcm.py: PcmFrame`, `audio/resample.py: PcmResampler`, `audio/_codecs.py: decode_ulaw_to_pcm16`, `runtime/_audio_runtime_mixin.py: add_pcm_rx_tap` | RX source |
| TX audio | `audio/session.py: AudioSession.acquire_tx, TxLease.push(submission_guard=..., admission=...)`, `core/radio_protocol.py: AudioTransport.audio_tx_codec, push_tx`, `audio/route.py: resolve_audio_route, rigctld_wsjtx_policy` | TX sink and route policy |
| Fail-closed TX gate | `web/server.py: WebServer._bridge_tx_gate_open` (uses `ManagedTxAuthority.snapshot`, `project_observed_ptt`, `ControllerAuthority.mode`), consumed by `audio/bridge.py: AudioBridge(tx_gate=...)` | **lives in `web`; extracted in P0b** |
| WebSocket | `web/websocket.py` (`WebSocketConnection`, `make_frame`, `make_accept_key`, `keepalive_loop`), handshake inside `web/server.py: WebServer._handle_websocket`, `web/host_guard.py: host_header_allowed, same_origin_allowed` | **in `web`; the framing moves to the leaf `tci/ws.py` and the guard to `core/host_guard.py` in P0a** |
| Listener config | `cli/__init__.py: _cmd_web` (constructs `RigctldServer` with the managed composition; host `127.0.0.1` when managed, else `0.0.0.0`), `--rigctld/--no-rigctld`, `--rigctld-port`, `check_ports_available`, `rigctld/contract.py: RigctldConfig` | model for `--tci*` flags |
| Test doubles | `tests/test_rigctld_server.py` (ephemeral port `port=0`), `tests/test_rigctld_managed_tx.py`, `tests/serial_stub.py: SerialMockRadio`, `rigplane_testkit/icom/simulator.py`, `rigplane_testkit/yaesu/cat_fake.py`, `rigplane_testkit/sim/`, `audio/backend.py: FakeAudioBackend` | seam and E2E tests |
| Layering rules | `.importlinter` (`layers`, `independence-top`), `src/rigplane/rigctld/LAYER.md` | new package must fit |
| Architecture of ingress | `docs/superpowers/specs/2026-06-02-radio-state-pipeline-design.md` ("command ingress" and "data ingress") | TCI is one more ingress |

Searches done before designing (two vocabularies each): "websocket server, RFC
6455, handshake, upgrade" found only `web/websocket.py` and the handshake in
`WebServer._handle_websocket`; `aiohttp` is a required dependency but imported
only by `diagnostics/upload.py` and `cli/_diagnose.py` as a client. "managed key,
ptt submission, owner disconnect, lease" found the authority plus the
rigctld-private choreography above. "tx gate, fail closed, bridge" found
`_bridge_tx_gate_open` only. "pcm frame, decode packet, resample" found at least three
places that decode RX packets (see 8.2). Nothing existing implements TCI.

## 4. Architecture: two layers

```text
TCI client (WSJT-X Improved / JTDX / MSHV / OpsLog / logger)
   |  ws://127.0.0.1:50001   text = commands, binary = audio
   v
rigplane.tci                       (a) protocol package, imports nothing from Core
   ws.py        RFC 6455 framing and server upgrade (moved from web/websocket.py)
   protocol.py  command grammar: parse / format, pure functions
   frames.py    binary stream header; sample-type conversion (stdlib only)
   server.py    listener, one session per socket, broadcast with 30 ms
                coalescing, ping liveness; Origin/Host predicate is injected
   handler.py   TciHandler: the Protocol the adapter implements
   |   TciHandler: on_connect (returns the init burst), on_command,
   |   on_binary, on_disconnect
   v
rigplane.tci_adapter               (b) the only code that knows Core
   handler.py   implements TciHandler; mints owner "tci-client-N"; refusal rules
   commands.py  TCI command -> Core operation table (phase "control")
   notifier.py  StateStore delta -> TCI notifications
   audio.py     (phase "audio") RX pump, TX receiver, TX_CHRONO, resample and
                channel mapping
   |
   v   CommandIntent (source="tci")          ManagedTx ingress (shared, from P0c)
Radio API / CommandService / StateStore  --->  ManagedTxAuthority  --->  Radio.set_ptt
   |
   v   AudioSession.subscribe_rx / acquire_tx (+ shared TX gate from P0b)
```

What belongs where:

| Concern | Layer |
|---|---|
| Grammar (spec 3.1, 4.7), command names, argument parsing, formatting | (a) |
| Stream header, `StreamType`, `SampleType`, sample-type conversion, per-stream `count` | (a) |
| WebSocket framing, upgrade, ping/pong liveness, session and broadcast plumbing | (a) |
| What a command means for a radio, owners, refusals, mode names, power scaling | (b) |
| `StateStore` projection, init burst content, notifications | (b) |
| `ManagedTxAuthority`, `CommandIntent`, `AudioSession`, resampling, channel mapping | (b) |
| CLI flags, wiring in `_cmd_web` | (b) |

The leaf offers no radio vocabulary: a command reaches `TciHandler.on_command`
as a name plus a tuple of strings, and the adapter decides what it means.

### 4.1 Boundary rule and its enforcement

Rule: every module under `src/rigplane/tci/` imports only the standard library
and `rigplane.tci` itself. No other `rigplane` package or module, and no
third-party package (not `numpy`, not `aiohttp`). The adapter and `web` may
import the leaf; the leaf imports neither. Needs that appear to require Core are
met by injection: the Origin/Host predicate is a callable the adapter passes
in; the handler is a Protocol the leaf defines.

Why a leaf: a TCI server with no radio in it is the part another program could
use. It is extracted into its own MIT package only when a second user outside
Core appears; no repository or package is created now.

Enforcement, written first and failing before the package exists:

1. **import-linter.** In `.importlinter`, `rigplane.tci` becomes the bottom row
   of `layers` (below `rigplane.core`), so nothing the layered contract knows
   about can be imported by it. A new `forbidden` contract `tci-is-a-leaf`
   names `rigplane.tci` as source and the current top-level packages
   (`core`, `commands`, `runtime`, `audio`, `profiles`, `scope`, `dsp`, `sdr`,
   `backends`, `web`, `rigctld`, `tci_adapter`, `cli`, `validation`) as
   forbidden. It shows up in the normal `lint-imports` gate. Limit: it cannot
   say "stdlib only", and a package added later is not in its list.
2. **AST test** `tests/test_tci_leaf_boundary.py`. It parses every `.py` under
   `src/rigplane/tci/` and fails on any import whose top-level name is not in
   `sys.stdlib_module_names`, `__future__`, or `rigplane.tci` (relative imports
   inside the package included). It asserts the directory contains the expected
   modules, so an empty or missing package fails rather than passes. A second
   test feeds the scanner `import numpy` and `from rigplane.core import x`
   and requires both to be reported, so the check is shown able to fail. This
   test is the complete rule; the linter contract is the visible, early signal.

`.importlinter` also changes the top row to
`rigplane.web | rigplane.rigctld | rigplane.tci_adapter` and adds
`tci_adapter` to `independence-top`. The adapter may depend on what `web`
may (`core`, `commands`, `profiles`, `audio`, `runtime`, plus the leaf; not
`backends`), because it needs audio, which `rigctld` does not. A
`LAYER.md` for each of the two packages is part of their first PRs, with
`rigctld/LAYER.md`'s forbidden patterns (no backend-id branches, no telemetry,
no `from rigplane.web`/`rigctld`).

### 4.2 One WebSocket implementation

Neither `tci_adapter` nor the leaf may import `web`. The two reusable parts are
in `web` today: framing (`web/websocket.py`) and the upgrade handshake (inline
in `WebServer._handle_websocket`: Host allowlist, Origin check,
`Sec-WebSocket-Key`, 101 response). Writing a second RFC 6455 server for TCI
would be two mechanisms for one job; adding `aiohttp` as a server would be a
second stack, break the "stdlib only" charter of `web/LAYER.md`, and is barred
from the leaf by 4.1.

P0a creates the leaf package with `tci/ws.py`: the moved `web/websocket.py` plus
a server-side upgrade function (key check, accept header, response bytes, an
injected `allow(headers) -> bool` predicate). `web/websocket.py` becomes a
`sys.modules`-alias shim to it, the repo's existing convention for moved modules
(`CLAUDE.md`, "Layer boundaries"), and `WebServer._handle_websocket` calls the new
function with the predicate built from `web/host_guard.py`. That guard
(`host_header_allowed`, `same_origin_allowed`) is plain header logic with no
radio dependency; it moves to `core/host_guard.py` with a `web` shim, so the
adapter can build the same predicate without importing `web`. Existing web tests
are the pin, plus the new boundary test.

Consequence to accept: `web` now depends on the leaf for framing. The shim keeps
`rigplane.web.websocket` working, so no web code changes its import. The
alternative, a separate leaf `rigplane.ws` lifted together with `tci`, avoids
that naming oddity at the price of a second package to extract; decided against (Q9).

Limits to keep: `_MAX_WS_FRAME` is 64 KiB. A TCI audio block is at most
64 + 16384 bytes, so it fits. Per-message deflate stays off for TCI (clients do
not negotiate it; audio does not compress). `WebSocketConnection`'s pong
timeout stays as it is for `web`; TCI's watchdog is separate (7.4).

## 5. Phase "control"

### 5.1 Command path

A TCI write is parsed to a `CommandIntent` with `command_intent_from_request`
and `source="tci"` (add `"tci"` to `CommandSource`; add `TxSource.TCI` and
`"tci": TxSource.TCI` to `_STABLE_OWNER_SOURCES` in `runtime/managed_tx_ingress.py`).
It then takes the same queue and executor as a `rigctld` write. Reads project
from `StateStore` the way `RigctldHandler._cmd_get_freq` does, calling
`StateModelService.ensure_fresh` when the projection is stale.

TCI must not start its own acquisition machinery. `RigctldServer.start`
bootstraps state acquisition and a freshness task; whether that is needed when
the web server already feeds the shared store in the managed composition is
unresolved here. C1 settles it with a failing test first: a TCI server that
starts no scheduler and still answers `vfo:0,0;` from the composed store.

### 5.2 What clients send (reported, to be captured)

Evidence tags: **[spec]** = TCI 2.1 PDF; **[AS]** = AetherSDR docs as above
(second-hand); **[OL]** = OpsLog wiki; **[RN]** = WSJT-X Release Notes
(second-hand, report of 2026-10-29, link in the ecosystem notes);
**[open]** = nobody in the evidence base says; capture C0 decides.

| Client | At connect | During a QSO | Evidence |
|---|---|---|---|
| all | WebSocket upgrade, then waits for the init burst up to `ready;` and seeds its state from it | reads/writes below | [spec] 3.1, 4.1; [AS] "WSJT-X seeds requested frequencies from the init burst" |
| WSJT-X 3.0.0 and newer (official) | [open] | control plus audio over TCI | [RN] |
| WSJT-X Improved 3.1.0 | `vfo:0,0,<hz>` (waits about 2 s for the echo); `split_enable:0,false` before it programs channel 1; `vfo:0,1,<hz>` (waits about 1 s); `audio_start:0` (sent automatically); reads `drive`/`tune_drive` and indexes the power as the second argument after the receiver. Mode set: **[open]** | `trx:0,true,tci` and `trx:0,false`; TX audio frames on each `TX_CHRONO` | [AS] routing doc (startup tune, rig compatibility, power shape), user doc (`audio_start`) |
| JTDX 2.2.159 | Rig-split sequence `split_enable:0,true`, then `vfo:0,1,<tx hz>` | `trx:0,true,tci`, then `TX_CHRONO` | [AS] routing doc |
| OpsLog (TCI backend) | host and port (default 40001 in OpsLog), frequency, mode, PTT, split; optional spots; asks for real split rather than "Fake It". Which command names it sends: **[open]** (the wiki names none) | the wiki's statement that it retries refused writes describes OpsLog's own TCI and Hamlib *servers*, not this client role | [OL] |
| MSHV | TCI interface with host and port, optional audio streaming | audio and PTT | [AS] (one line) |
| Log4OM v2 | spots in both directions | `clicked_on_spot` back | [AS] (one line) |

Reported client constraints that shape the design:

1. A write must **always be answered**, even when it changes nothing; WSJT-X
   waits for the echo. A write the radio refuses is answered with the state the
   radio is actually in, not with silence and not with an error.
2. A refused PTT is answered with `trx:<n>,false;` ([AS]); the spec has no error
   reply. WSJT-X shows "failed to set ptt". The refusal goes **only to the
   requester**. Broadcast notifications carry the actual radio state and nothing
   else: if client B holds PTT and A's key is refused, A is told `false`, while B
   and any observers keep `true` (they are told `false` only when the observed or
   managed state really changes).
3. `drive` and `tune_drive` replies and notifications always carry
   `<trx>,<power>`; a bare power value breaks ESDR3-mode WSJT-X/JTDX ([AS]).
   `drive:<trx>` is a read; only `drive:<trx>,<power>` writes.
4. The `protocol` line may switch client behaviour ([AS], "ESDR3-mode"). Which
   `protocol:` and `device:` strings to announce is **open** (decision Q4).
5. JTDX "can crash with some TCI servers" ([AS] user doc). Any deviation in the
   init burst is a risk; capture C0 must include JTDX.

### 5.3 Receiver and channel model

TCI addresses a transceiver (`trx`, "receiver periodic number") and two
channels per receiver (0 = A, 1 = B). The server announces `trx_count:1` and
`channels_count:2`, always for `trx` 0.

- Channel 0 is the frequency and mode the radio receives on.
- Channel 1 is VFO B. It is addressed and reported independently of split
  (WSJT-X Improved programs it right after `split_enable:0,false`, [AS]).
  Split and XIT only decide the *effective TX frequency*: the base is B when
  split is on, else A, and an enabled XIT offset is added to it.
  On Core, channel 1 is the relative-VFO surface:
  `RelativeVfoReadbackCapable.read_relative_vfo(selected=False)` to read and
  `RelativeVfoFrequencyWritable.write_relative_vfo_frequency` to write, which
  `rigctld` already reaches through the `set_split_freq` intent, gated on the
  capability. A radio without that surface reports channel 1 equal to channel 0
  and answers a channel-1 write with the current value.
- Radios with two independent receivers (`ReceiverBankCapable.receiver_count > 1`)
  expose only the receiver that `Radio.get_freq(receiver=0)` addresses. `trx_count:2`
  is a later extension, not part of this program (decision Q5).

A request for `trx` other than 0 is ignored without a reply (spec 4.7, rule 5).

### 5.4 Init burst

One deterministic sequence per connection, ending with `ready;`. The spec lists
the initialisation commands (4.1) and says they are sent first, then the state
(3.1); it fixes no order among them. This design uses identity, topology,
limits, modes, state, stream settings, `ready`, which is the order AetherSDR
documents as its startup barrier (tci-routing-ordering.md, "Startup barrier").
C0 confirms what the clients need:

1. `protocol:<name>,<version>;` `device:<model>;` `receive_only:<bool>;`
2. `trx_count:1;` `channels_count:2;`
3. `vfo_limits:<lo>,<hi>;` (and `if_limits`, see below)
4. `modulations_list:<names>;`
5. state, per the table in 5.5: `dds`, `if`, `vfo:0,0`, `vfo:0,1`, `modulation`,
   `trx`, `tune`, `drive`, `tune_drive`, `rit_enable`, `xit_enable`,
   `split_enable`, `rit_offset`, `xit_offset`, `tx_enable`, `tx_frequency`
6. stream settings (phase audio): `audio_samplerate`, `audio_stream_sample_type`,
   `audio_stream_channels`, `audio_stream_samples`
7. `ready;`

Sources: `device` is `Radio.model`; `receive_only` is true unless the radio has
the TX capability **and** the managed composition is present (7.2);
`vfo_limits` comes from the radio profile frequency ranges (data, not code);
`modulations_list` is the intersection of TCI names (5.6) and the modes the
profile supports. `if_limits` has no Core source (there is no panorama IF); it
is omitted unless capture C0 shows a client waits for it.

### 5.5 Command table, phase "control"

Direction: **R** read (no value argument), **S** set, **N** server notification.
Reads and sets reply with the same command name, to the sender and, for an
accepted set, to every other client (spec 3.1: the server synchronises clients).
A refusal or a failed write is answered to the requester only, with the actual
state; the notifier, not the refusal, tells other clients about state changes.

Tier A is required at connect and in a QSO. Tier B is accepted and answered
from state or ignored harmlessly. Tier C is not honoured; how it is answered is
in 6.

| TCI command | Tier | Core mapping | Notes |
|---|---|---|---|
| `vfo:0,0` R/S | A | read: `StateStore` freq of receiver 0; set: intent `set_freq{freq, receiver=0}` | echo the frequency the radio reached; echo current on no-op or failure |
| `vfo:0,1` R/S | A | read: the unselected VFO's frequency, independent of split; set: intent `set_split_freq` if `RelativeVfoFrequencyWritable`, else echo current | reply even on no-op (WSJT-X waits 1 s); a write with split off must stay readable and must survive enabling split |
| `modulation:0[,mode]` R/S | A | names in 5.6; set: intent `set_mode`, plus `set_data_mode` for DIGU/DIGL | applies `audio.route.rigctld_wsjtx_policy` for data mode and TX input (P0d shares the glue now in `rigctld/server.py: _apply_wsjtx_data_mod_input`) |
| `split_enable:0[,bool]` R/S | A | `SplitCapable.get_split/set_split` via intent `set_split`; no capability: always `false` | `false` while a client programs channel 1 must not erase it ([AS]) |
| `trx:0[,bool[,source]]` R/S | A | PTT through the managed ingress (7) | reply `trx:0,false;` on every refusal; `source` handling in 7.3 |
| `drive:0[,0..100]` R/S | A | read: normalised power level; set: intent `set_rf_power` with `value/100.0`, resolved by `core/command_service.py: resolve_power_level_target` (clamps to the profile range) | reply carries `0,<power>` always |
| `start` / `stop` S | B | acknowledged by echo, no radio action | TCI "device start/stop" has no Core equivalent |
| `rit_enable`, `rit_offset` R/S | B | `RitXitCapable.get/set_rit_status`, `get/set_rit_frequency` | capability absent: `false` / `0` |
| `xit_enable`, `xit_offset` R/S | B | `RitXitCapable.get/set_rit_tx_status`; offset mirrors the RIT value | `RitXitCapable` has one frequency method, so the two offsets are one value |
| `tx_enable:0` N/R | B | server only: true when TX capability and managed composition are present | a client set is ignored and answered with the current value (spec 4.3) |
| `tx_frequency` N | B | the effective TX frequency: (channel 1 when split is on, else channel 0) plus the XIT offset when XIT is enabled | notified on any change of its inputs: split on/off, either VFO, XIT enable, XIT offset |
| `dds`, `if` R | B | `dds:0,<A hz>`; `if:0,0,0`; `if:0,1,<B hz - A hz>` (negative allowed) | TCI relates a channel to the receiver as VFO = DDS + IF, so DDS is anchored to A and channel B carries the difference; all three are re-sent together whenever A or B changes. No panorama; read-only |
| `vfo_limits`, `modulations_list`, `trx_count`, `channels_count`, `device`, `protocol`, `receive_only`, `ready` | B | init only (5.4) | |
| `rx_sensors_enable`, `tx_sensors_enable` S, `rx_sensors`, `tx_sensors` N | B | optional: `MetersCapable.get_s_meter` mapped to dBm if `runtime/meter_cal.py` provides such a mapping (to be checked in that PR); `get_power_meter`, `get_swr` | deferred to a later PR if no captured client sends the enables |
| `cw_keyer_speed`, `cw_macros_speed` R/S | B | `CwControlCapable.get/set_key_speed` | non-TX state only |
| `spot`, `spot_delete`, `spot_clear` S | B | accepted and dropped | OpsLog and Log4OM send spots ([OL], [AS]); no panorama |
| `tune`, `tune_drive` | C | see 6.4 | |
| everything else in spec 4.2 to 4.5 | C | see 6.1 | |

### 5.6 Mode names

A table in `tci_adapter/commands.py` maps TCI names to Core `Mode` and a
data-mode flag, in both directions:

| TCI name | Core |
|---|---|
| `AM`, `LSB`, `USB`, `CW`, `FM`, `WFM` | `Mode.AM`, `LSB`, `USB`, `CW`, `FM`, `WFM` |
| `DIGL` | `Mode.LSB` with data mode on |
| `DIGU` | `Mode.USB` with data mode on |

`rigctld` maps `PKTLSB`/`PKTUSB` the same way. `NFM`, `SAM`, `DSB`, `DRM` are
**not announced and not accepted**: `core/types.py: Mode` has no `NFM` member
(narrow FM is a filter width, not a mode), and a request for a name outside
`modulations_list` is ignored per 6.1. A Core mode with no TCI name (`RTTY`,
`RTTY_R`, `CW_R`, `PSK`, `PSK_R`, `DV`) is not projected: `modulation` keeps the
last announced value and the unmapped mode is logged once. The test iterates
every member of the real `Mode` enum and requires each to be either mapped or
listed as unmapped, so a new `Mode` member fails it until classified. This is
TCI vocabulary, which belongs to the adapter; which names a radio actually
supports is read from the profile, so no per-radio branch exists.

### 5.7 One modulation, two VFOs (split)

TCI carries one `modulation` per transceiver; Core keeps a mode and data-mode
per VFO. With split on, transmission uses VFO B's tuple, so changing only A
leaves B in whatever it had (an IC-7300 with A in USB and B in LSB/DATA-off
would transmit LSB). Rule: the adapter keeps B's mode and data state equal to
A's while split is on or channel 1 is being programmed. On `modulation` set, and
on `split_enable:0,true`, it also writes B through the relative-VFO mode surface
(`RelativeVfoModeWritable.write_relative_vfo_mode`, reached by `rigctld` as
`set_split_mode`, gated on the capability). Before accepting a key it reads
the TX tuple back (`read_relative_vfo`) and refuses (`trx:0,false;`, no PTT
write) if the mode or the **numeric DATA sub-mode** of the TX VFO, or the MOD
input assigned to that sub-mode, differs from what the policy
(`rigctld_wsjtx_policy`) requires. The sub-mode matters: on an IC-7610 over LAN
the policy is DATA2 with the LAN input, and an IC-7610 whose DATA1 input is MIC
would transmit from the microphone if only the DATA flag were compared.
**Prerequisite (P0e):** `RelativeVfoModeWritable.write_relative_vfo_mode`
accepts `data: bool | None` today, and its Icom implementation reads back only
0 and 1, so it cannot write or verify DATA2. P0e widens it to a numeric
sub-mode bounded by the profile's `data_mode_count`, with write and readback
through the capability, no per-radio branch. Radios without the surface have no
separate TX tuple to fix. Test: IC-7610 simulator split case with DATA1=MIC and
DATA2=LAN, asserting B's exact sub-mode and input before key-down.
Tests: start with mismatched A/B modes; change mode before and after enabling
split; assert the tuple actually read at key-down
(`tests/test_rigctld_wsjtx_split_mk2.py` is the existing rigctld precedent for
this behaviour).

## 6. Commands we cannot honour

### 6.1 Rule

The spec says an unknown command, a wrong-typed argument or an out-of-range
receiver is ignored without a reply (4.7, rule 5), and a set whose argument
cannot be parsed degrades to a read (rule 2). The server follows that, and adds:

- A command that is in the spec but outside Tier A/B is **ignored without a
  reply**, never half-applied. It is logged once per connection at INFO with the
  command name only (no arguments), so a captured client's real needs show up.
- Parsing follows rules 1 to 4: empty arguments are discarded, booleans are
  `true`/`1` case-insensitively, frequencies and levels are integers, names are
  case-insensitive. Malformed input never reaches a `CommandIntent`.
- Only commands from the `eesdr-tci` dictionary are ever **emitted** (10.2).

### 6.2 IQ, line-out, spectrum

`iq_start`, `iq_stop`, `iq_samplerate`, `line_out_*`, `rx_sensors`-style panorama
data: ignored. `audio_*` commands belong to phase "audio" (section 8) and, until
that phase ships, are also ignored, so a control-only release does not advertise
audio.

### 6.3 CW over TCI

`cw_macros`, `cw_msg`, `cw_macros_stop`, `cw_terminal`, `keyer`: ignored. They
start transmission and need a decision on owner and release semantics with the
authority; out of scope here.

### 6.4 `tune` and `tune_drive`

Core's tune primitive is `SystemControlCapable.set_tuner_status`: 0 off, 1 on,
2 tuning cycle (the Web UI starts a tune with value 2; `rigctld` `set_func TUNER`
sends 0/1 through `_execute_managed_tuner`). TCI `tune` switches the transceiver
between receive and its Tune mode (spec 4.2), which has its own power setting,
`tune_drive`. These are different operations:

- A tuner cycle is not a managed PTT lease; there is no owner to release on
  disconnect. `ManagedTxAuthority._execute` deliberately aborts only
  `STOP_CW` on full force, because `STOP_TUNE` "would switch the tuner OFF",
  a persistent user setting.
- No Core primitive for "Tune mode at a chosen power" was found. Searching
  `core/radio_protocol.py` and `core/command_dispatch.py` for `carrier` gave no
  hits; for `tune`, the hits are the tuner-status methods above and a docstring
  example.

First release: `tune:0[,x]` is answered `tune:0,false;` and does nothing;
`tune_drive` is read as the same value as `drive` and ignores sets. Honouring
Not honouring `tune` in release 1 is decided (Q3). Option for a later release: map `tune:0,true` to
`set_tuner_status(2)` through the same owner checks the Web UI applies
(`ControlHandler._validate_controller_command`: refused while another owner holds
TX or while the radio transmits with no owner), stop it with value 0 on
`tune:0,false`, and on disconnect send value 0 only for a tune this session
started. That needs a bench run on a dummy load and is a separate PR.

## 7. TX safety

### 7.1 Principles

1. The only keying path is `ManagedTxAuthority`. No TCI code calls
   `Radio.set_ptt`, `send_civ` or any raw frame.
2. A key request always names an owner: `tci-client-<N>`, minted once per
   accepted socket, as `rigctld-client-N` is, so the lease is releasable.
3. An unkey never fails open: it never forces another owner off, and it is
   answered even if nothing was keyed.
4. Anything the client cannot be told about (socket gone) is released by the
   server side, not by the client; the outcome of that release can be uncertain
   (7.4).

### 7.2 Managed composition required

`bind_managed_tx` returns `None` for a radio that publishes no supervisor, and
`rigctld` then falls back to a legacy `Radio.set_ptt` with only its own
key-down bound (`RigctldHandler._arm_key_down_backstop`). TCI does **not** copy
that fallback. `receive_only:true` is announced, `tx_enable` is false, and
`trx:0,true` is answered `trx:0,false;` whenever the managed composition
(`ManagedTxAuthority` + `CommandQueue` + `CommandService`, built by `_cmd_web`)
is absent. Adversary: a TCI client, or a crash of it, on a radio that has no
watchdog. TCI is therefore started only inside `web` and `station`, not in
standalone `serve`. This is stricter than `rigctld` on purpose, and has a
precedent: the `/api/v1/rigctl` channel is attached only when the managed
composition exists, because "only the managed authority enforces" the session's
key (`_cmd_web`, MOR-3536). Relaxing it is a separate decision.

### 7.3 `trx`

`trx:0,true[,source]`:

1. Parse. The optional third argument selects the TX audio source (spec 4.2).
   Core selects TX input only through the route policy of 8.3, so:
   - `tci`: requires phase "audio" (8) and a PCM TX route. The TX input is
     **established and verified inside the authority's admitted key**, never
     before it (7.3a): the policy of 8.3 step 1 is applied even if the client
     never sent `modulation`, read back, and the key is refused if it cannot be
     established or verified;
   - absent: key with the radio's currently selected input (what a rigctld
     `set_ptt` does); the reply is the same whatever DIGU/DIGL selected earlier;
   - `mic1`, `mic2`, `micpc`, `ecoder2` or any other name: **refuse**
     (`trx:0,false;`, no PTT write). Accepting it would key whichever input the
     data route selected, which is not what the client asked for.
   Test: select DIGU, unkey, send `trx:0,true,mic1`, assert no PTT write.
2. Refuse (reply `trx:0,false;`, no radio write) if: no managed composition;
   `ControllerAuthority.mode == "remote"`; `authority.held_by_other(owner)`;
   `authority.radio_tx_refusal_nowait()` is not `None` (radio transmitting with
   no owner, MOR-3537).
3. Otherwise submit through the shared managed-key ingress (P0c), with the
   `before_on` preparation of 7.3a for `source=tci`:
   `ManagedTxAuthority.start_ptt_submission(True, owner, ready=..., expires_at_monotonic=...)`
   and the ordered queue entry, exactly as `rigctld` does for `set_ptt`. The
   reply is `trx:0,true;` when the submission is accepted and its queued write
   completes; any `ManagedTxOutcome.REJECTED`, `CommandError` or
   `ControllerError` is `trx:0,false;`. The notifier then follows the observed
   PTT (`OBSERVED_PTT_PATH`); if the radio contradicts the reply, the next
   notification says so.

### 7.3a Preparation inside the admitted key

Changing mode, DATA state or MOD input is a write that can alter what another
owner is transmitting. It therefore must not run before the authority has
admitted this owner, and checking `held_by_other` first is not enough, because
the routing writes await and another owner can key in between. In
`ManagedTxAuthority._begin_positive_operation` the optional `ready` future is
awaited *before* the admission block, and `admit_managed_write` does not stop
mode or input writes while another owner holds TX, so neither can serve.

Requirement: `ManagedTxAuthority` gains an optional `before_on` coroutine
parameter on `start_ptt_submission`. It runs after the admission block has set
the intent to `PTT(owner)` (so no other owner can key) and before the PTT_ON
effect is dispatched, bounded by the attempt timeout. If it raises, times out or
reports failure, the transition takes the authority's existing
"ON rejected before effect" path (intent back to RX, release debt, scheduled
`force_receive`), no RF is produced, and the requester is told `trx:0,false;`.
Routing writes made during `before_on` go through the normal `CommandIntent`
path with `source="tci"`. The change lands in P0b together with
`owned_tx_submission`, and is used by TCI only.

Tests: another owner holds TX (MIC) and A sends `trx:0,true,tci`: no mode, DATA
or input write is made; another owner keys while A is inside `before_on`: that
key is refused and A's writes complete first or are cancelled; `before_on`
raising or timing out leaves RX with debt scheduled and no PTT_ON write.

`trx:0,false`: `ManagedTxAuthority.submit_ptt(False, owner)`. If this owner holds
nothing, reply the actual state and write nothing, matching `rigctld`
(`RigctldHandler._route_ptt`, "unkey declined").

Open: whether `trx:0,true` should wait for the radio's observed TX before
replying (AetherSDR documents a 1.25 s confirmation window for its radio). The
default here is the `rigctld` behaviour (reply on accepted write); C3 measures
the gap on the simulator and the bench and revisits it only with data.

### 7.4 Disconnect, liveness, bounds

- **Socket closes for any reason** (close frame, FIN, reset, server stop): the
  adapter's `on_disconnect` calls the shared release, the equivalent of `RigctldServer._release_session_tx`:
  `ManagedTxAuthority.owner_disconnect(owner)`. It survives one cancellation, is
  fail-soft, and runs before the socket object is dropped. The shared helper
  from P0c is the single implementation.
- **Peer dies without closing** (power loss, cable): AetherSDR lists this as a
  known issue, the radio stays keyed until the OS gives up on the connection
  ([AS] Known issues). Two bounds apply here.
  (a) Liveness. The leaf server runs, per session, a **deadline watchdog that is
  independent of the heartbeat writes**: it records the time of the last pong
  (or any inbound frame) on the injected clock and, when `now - last > 15 s`,
  aborts the transport (`WebSocketConnection.abort`) and runs the protected
  teardown of the first bullet. Pings are sent every 5 s by a separate task with
  a bounded write. Reason: the existing `keepalive_loop` only checks liveness
  after each sleep and awaits an unbounded `writer.drain()`, so reusing it as is
  can release up to ~20 s late, or never when a write blocks. The loop and its
  60 s fixed pong timeout stay as they are for `web`. The constants live in the
  leaf's `tci/server.py`. Tests (fake clock): a last pong that is not aligned
  with the 5 s tick still releases by 15 s; a permanently blocked `drain` still
  releases.
  (b) The authority's software TOT. `ManagedTxTotConfigStore` takes a
  configured value (default `DEFAULT_MANAGED_TX_TOT_SECONDS`, 180 s) that can be
  disabled or extended, and `ManagedTxAuthority._tot_deadline_locked` follows
  it. So the bound on a keyed over is *the configured TOT*, not a fixed 180 s;
  with TOT disabled a live, ping-answering client can stay keyed
  indefinitely, as any other ingress can.
  (c) Uncertain release. The shared disconnect helper is fail-soft and survives
  one cancellation; after a timeout or a second cancellation the unkey can be
  unconfirmed and the lease stays held with its release obligation unsettled
  (`RELEASE_REQUIRED`), which the uncertain-shutdown diagnostics surface
  (`RigctldServer._release_session_tx` docstring, MOR-1015). TCI inherits this
  and does not claim to remove it.
- **Server stop**: `stop()` closes sessions first, then lets the authority's own
  shutdown run; same order and timeouts as `RigctldServer.stop`
  (`STOP_TIMEOUT_S`).
- **Second owner**: a `rigctld` client and a TCI client share one authority.
  Whoever keys first holds the lease; the other is refused (7.3 step 2). No
  TCI-side arbitration exists.

### 7.5 Drive and tune

`drive` is a level write (`set_rf_power`), not a keying operation; it is
admitted by the same write path as `rigctld` `RFPOWER`, and
`ManagedTxAuthority.admit_managed_write` already passes it while keyed. It is
clamped by `resolve_power_level_target` to the profile range. `tune` and
`tune_drive`: 6.4. No TCI-specific safety mechanism is introduced beyond the watchdog of 7.4(a)
and the owner-specific audio admission of 8.3.

### 7.6 Network exposure

Default bind is `127.0.0.1` (section 9). The adversaries named:

- A web page in the operator's browser opening `ws://127.0.0.1:50001`
  (cross-site WebSocket hijack, DNS rebinding). Native TCI clients send no
  `Origin`; the upgrade is refused with 403 if an `Origin` header is present and
  not allow-listed, and the `Host` header is checked with the moved
  `host_header_allowed`. A test connects with a browser-style `Origin` and a
  rebinding `Host` and expects refusal.
- Another machine on the LAN: only if the operator binds a non-loopback address,
  which is logged at WARNING on start. TCI has no authentication in the spec,
  and the existing `rigctld` listener has none either (it binds `0.0.0.0` when
  unmanaged). No extra gate is added (decided, Q6): remote PTT has parity with
  `rigctld`. Network exposure is one explicit setting shared by both listeners
  (section 9), not a TCI-only opt-in.

## 8. Phase "audio"

### 8.1 Stream format (spec 3.4)

The header layout, the `StreamType` and `SampleType` enums and the conversion
between sample types live in the leaf (`tci/frames.py`, standard library only:
`struct`, `array`). If A1's measurements show pure-Python conversion too slow
at 2048-sample blocks, the conversion moves to the adapter; `numpy` does not
enter the leaf.

Binary frames carry a 64-byte header then data. Little-endian, as `eesdr-tci`
packs it (`<8I32x`):

| Field | Meaning |
|---|---|
| `receiver` | trx number (0) |
| `sample_rate` | the client's `audio_samplerate`: 8000, 12000, 24000, 48000 |
| `format` | 0 int16, 1 int24, 2 int32, 3 float32 (default float32) |
| `codec`, `crc` | always 0 |
| `length` | number of samples in `data`, **all channels counted** (spec wording for the IQ stream: complex samples are `length/channels`; `eesdr-tci` sizes `data` as `bytes_per_sample * length`). Open: confirm against a captured stream (C0). |
| `type` | 1 RX audio, 2 TX audio, 3 TX_CHRONO (4 line-out: not sent) |
| `channels` | 1 or 2. The spec default is 2; this server announces and serves 1 (8.2) |
| `count` | per-stream packet counter, separate for RX audio and TX_CHRONO, reset when switching RX/TX |
| `reserv[7]` | zero |

Per-client settings (spec 4.3): `audio_samplerate` (unsupported value ignored,
current rate echoed), `audio_stream_sample_type` (reply lower case),
`audio_stream_channels` (clamped 1 to 2), `audio_stream_samples` (clamped 100 to
2048; defaults 2048/1024/512/256 samples at 48/24/12/8 kHz),
`tx_stream_audio_buffering` (50 to 500 ms, default 50). Supported sample types:
int16 and float32 are required; int24 and int32 are included if the conversion
stays within the PR budget, otherwise a request for them is "unknown format:
ignored, current echoed", which the spec allows (4.3).

### 8.2 RX path

`audio_start:0` -> `AudioSession.subscribe_rx("tci-<N>")`; `audio_stop:0` or
disconnect -> close the subscription. Per client:

1. Packet -> `PcmFrame` (s16le, rate, channels). **Gap:** there is no shared
   packet-to-PCM helper. At least three places decode RX
   packets on their own (`AudioBridge._rx_loop`,
   `web/handlers/audio.py: AudioBroadcaster`, and `add_pcm_rx_tap`, which is
   Icom LAN-only and has no consumer in the tree). TCI would be the fourth. A1 first proves per backend which packets `subscribe_rx` delivers
   (fake LAN/USB/Yaesu audio, `FakeAudioBackend`) and adds one shared decode
   function in `audio/pcm.py` over the existing decoders. Migrating the three
   existing consumers to it is *not* in this program; it is filed as a follow-up
   ticket.
2. In the adapter: resample to the client rate with
   `audio.resample.PcmResampler`; re-block to `audio_stream_samples`. Then the
   leaf's `frames.py` converts to the client sample type and builds the frame.
   **Channels.** Spec 3.4: with DIGL/DIGU selected, a two-channel stream is a
   *complex* signal and a one-channel stream a real one. Core's RX audio is a
   real demodulated signal, so duplicating it into two channels would hand a
   conforming consumer a stream with no quadrature content. First release
   therefore serves **real mono in every mode**: the init burst announces
   `audio_stream_channels:1;` and a client request for 2 is answered with the
   current value (1), the same "unsupported value ignored, current echoed"
   treatment the spec gives other stream settings. Whether WSJT-X Improved,
   JTDX and MSHV accept that is a C0 item; a true complex (Hilbert) conversion,
   if a client needs it, is a separate design with I/Q phase reference tests.
3. Send on a bounded per-session queue. If a client falls behind, drop the
   oldest audio and count it; never block the radio-side loop or other clients.
   A client that stays behind for 5 s is closed.

Radio facts: IC-7300 profile pins mono PCM, IC-7610 receives stereo-capable
codecs with a mono TX codec (`rigs/ic7300.toml`, `rigs/ic7610.toml: [audio]`),
FTX-1 is mono 16-bit over USB. All are read from `audio_codec`,
`audio_sample_rate` and the route, never branched on.

### 8.3 TX path

Flow, spec 3.4: after `trx:0,true,tci` is accepted the server sends
`TX_CHRONO` (type 3) at the cadence of `length / (channels * sample_rate)`,
because `length` counts all channels (8.1): at 48 kHz, stereo, length 2048 the
interval is 21.33 ms, at mono 42.67 ms. The client answers with TX audio frames
(type 2) of that length, or with a zero-length frame/no frame for silence. The
sample-versus-frame distinction is kept in the code by two named quantities,
`samples` (interleaved scalars) and `frames` (per-channel), and tested with a
fake clock for mono and stereo: request timestamps and delivered sample
throughput.

1. Accept `trx ...,tci` only if two things hold. (i) Structure:
   `browser_tx_audio_facts`-equivalent route facts say PCM TX is possible. The
   existing check lives in `web/handlers/audio.py: browser_tx_audio_facts`; A2
   moves the radio-only part (codec, route, required MOD input source) to
   `audio/route.py`, or reuses `resolve_audio_route` directly, whichever is
   smaller; the web function then calls it. It reports what the radio *can* use;
   it does not select or verify the input in force. First release supports radios
   whose `audio_tx_codec` is `PCM_1CH_16BIT`; an Opus TX codec refuses
   `source=tci`. (ii) Input in force: the adapter applies the policy from
   `rigctld_wsjtx_policy(resolve_audio_route(radio))` (required DATA state and MOD
   input source) for the current mode, reads it back, and only then lets the key
   proceed. The mode must be one the policy defines a data route for (SSB, AM,
   FM per profile); in CW, RTTY or any other mode, or if the readback does not
   match, the key is refused with no PTT write. Restoring the previous input
   after the over is **not done in release 1** (decided, Q11; a documented known
   limitation, section 9): the web
   path has no restore to reuse, because `ControlHandler`'s teardown hook only
   clears its bookkeeping (`_clear_mod_input_restore_on_teardown`, pinned by
   `tests/test_web_mod_input_restore.py`).
2. `lease = await radio.audio_session.acquire_tx("tci-<N>")` at the moment the
   key is accepted; release at unkey/disconnect.
3. Convert client frames to s16le mono at the radio TX rate: de-interleave
   (in DIGU/DIGL a two-channel frame is I/Q, and the real part, channel 0, is
   used; otherwise channels are averaged), resample, clamp to [-1, 1] and
   **replace NaN/Inf with 0**, scale to int16.
4. **Playout buffer.** Converted audio goes into a bounded per-session buffer
   whose target depth is the client's `tx_stream_audio_buffering` (50 to 500 ms,
   default 50). A playout task starts once the buffer holds the target and then
   pushes one radio frame (20 ms) per tick on the event-loop clock. Underrun
   while keyed: a frame of silence is pushed so playout stays continuous, and
   the underrun is counted. Overrun (depth above target plus 500 ms): drop the
   oldest and count. The buffer is flushed on unkey, on disconnect, and when the
   gate closes. Test: the same arrival jitter at buffering 50 ms and 150 ms
   must yield continuous playout at 150 ms and a measurably smaller underrun
   count than at 50 ms, with fixed added latency equal to the setting.
5. **Owner-specific admission, not the global gate.** The playout task pushes
   with `lease.push(pcm, admission=...)`, where the admission is a new
   `ManagedTxAuthority.owned_tx_submission(owner)` async context manager, modelled
   on `outside_tx_submission` (MOR-3764): it takes the authority's
   `_outside_tx_lock`, which every positive keying transition also takes before
   `_lock`, and yields True only while the held intent is `PTT(owner)` and the
   authority is open and unfaulted; the push runs inside it under the same
   budget. Consequence: a frame admitted for owner A completes before owner B's
   key transition is applied, and a frame for A after A unkeyed or B keyed is
   refused. The shared global gate of P0b (any keyed intent or observed PTT ON)
   is *not* sufficient here, because it cannot tell A's frame from B's over; it
   stays what `AudioBridge` and the web path use. `TxLease.push` takes a guard or
   an admission, never both, so TCI uses the admission only; frames also stop
   when the managed intent leaves `PTT(owner)` because the buffer is flushed.
   Test: A keys, a frame is suspended before admission, A unkeys, B keys, the
   frame resumes: nothing of A reaches B; and the same with the suspension
   inside the push. Sibling to report: the web and bridge paths use the global
   gate and have the same shape; this program does not change them and files a
   follow-up.
6. Frames with no keyed owner, or from a session that is not the owner, are
   dropped and counted with the throttled warning pattern
   `AudioHandler._warn_tx_throttled` uses. A frame never keys the radio.

The same policy is also applied when the client selects DIGU/DIGL (5.5), so a
client that sets the mode first and one that only sends `trx ...,tci` end in the
same state. Test: start in USB with the microphone input selected and send
`trx:0,true,tci` with no earlier `modulation`: the input is selected and
verified before the PTT write, or there is no PTT write.

### 8.4 Latency and pacing

The timing state is the chrono cadence, the RX queue limits of 8.2 and the TX
playout buffer of 8.3. No fixed sleeps as synchronisation: all of it runs on the
event-loop clock and the clock is injectable for tests.

## 9. Listener, configuration, enabling

| Item | Decision |
|---|---|
| Default port | `50001`. Evidence: AetherSDR docs give 50001 as its TCI default and tell WSJT-X users to enter `localhost:50001`. OpsLog's TCI backend defaults to 40001 ([OL]); a MacLoggerDX help page is reported (search snippet only, page not read) to give 40001 as the ExpertSDR2 default. Q1. |
| Default bind | `127.0.0.1`; non-loopback logged at WARNING. `rigctld` binds `0.0.0.0` when unmanaged; TCI never does by default. |
| Network exposure | **One explicit setting shared by the `rigctld` and TCI listeners (decided, Q6)**, default loopback. Today `rigctld`'s host is hard-coded in `_cmd_web` (`127.0.0.1` when managed, else `0.0.0.0`) and `web`/`station` have no `--rigctld-host`; C4 introduces the shared setting and makes both listeners read it, instead of adding a TCI-only host flag. Whether it also changes `rigctld`'s unmanaged `0.0.0.0` default is not decided here; C4 keeps that behaviour unchanged and records the question in its PR. |
| Flags | `web` and `station`: `--tci` / `--no-tci`, `--tci-port N`, plus the shared exposure setting above, parallel to `--rigctld`, `--rigctld-port`. **Off in Core unless `--tci`; Pro turns it on (decided, Q2).** No `serve` support (7.2). |
| Known limitations (documented in `docs/api/tci.md`) | the TX input chosen for a `source=tci` over is not restored afterwards (Q11); `tune` and `tune_drive` are not honoured (Q3); IQ, spots on a panorama, CW over TCI and a second receiver are not offered. |
| Env | none, matching `rigctld`, which has none. |
| Wiring | in `cli/__init__.py: _cmd_web`, next to `RigctldServer`, the adapter receives the same `managed_tx_authority`, `command_queue`, `command_service` and passes its handler to the leaf's server; `OSError` on bind aborts startup the way `rigctld` does; stop in the same `finally`; include the port in `check_ports_available`. |
| Coexistence with `rigctld` | two independent sockets on one radio and one authority. Owners are namespaced by source (`rigctld-client-N`, `tci-client-N`). The authority refuses a second keyer. Neither listener starts, stops or configures the other. |
| Core and Pro | the feature is Core, MIT, no gating. Pro runs Core as a sidecar process on `127.0.0.1:8470` (`docs/architecture/open-core-policy.md`, sections 1 and 8) and enables TCI by passing `--tci` and a port from its own settings; any Pro UI toggle is a `rigplane-pro` task. Core has no Python hook for starting extra listeners and this design adds none. |
| Docs | `docs/api/tci.md` (new), a client setup page (WSJT-X Improved, JTDX, OpsLog), `docs/CHANGELOG.md`. |

Note the port conflict the owner will hit: AetherSDR already listens on 50001.
`--tci-port` exists for this and is reported by `check_ports_available`.

## 10. Test plan

Rules from the repo: test first and watch it fail on the exact SHA
(`focused.yml`), no tests on the laptop (Mac mini), no bare `MagicMock` (`tests/test_meta_mock_specs.py`;
`tests/test_meta_assertions.py` also applies), real dataclasses for
signature-sensitive objects, `FakeAudioBackend` only for audio, and
`docs/development/review-checklist.md` walked before pushing.

### 10.1 Leaf `rigplane.tci` (no radio anywhere)

Everything here runs without Core. This is what makes the package liftable, and
it is the test suite that would travel with it.

- Grammar: every example string in spec 4.1 to 4.5, plus the seven rules of 4.7,
  as a table-driven test of `tci/protocol.py` (empty arguments, read vs set,
  booleans, case, unknown command, wrong type).
- Binary header: byte-exact vectors built by hand from the 3.4 struct for RX,
  TX and `TX_CHRONO` frames, all four sample types, `count` wrap, and
  round-trip with `eesdr-tci`'s `TciDataPacket`.
- Framing and upgrade: the moved `web/websocket.py` tests, plus rejection of a
  bad key and of a request the injected predicate refuses.
- Server conformance with `eesdr-tci`: the real leaf server on an ephemeral
  port (`port=0`) with a **fake `TciHandler`**, driven by
  `eesdr_tci.listener.Listener`. Dev-only dependency (adds `websockets`
  transitively; BSD-3-Clause-Clear, never shipped). Covers connect, init burst
  and the `ready` event, multi-client broadcast and coalescing, ping liveness
  with a fake clock, the output guard below, and typed `TciDataPacket`s both ways.
- **Output guard:** `Listener._listen_main` raises `ValueError` on any command
  name outside its dictionary, which ends its receive task, so a full scripted
  session that completes proves the server emitted only known commands.
- The boundary tests of 4.1.

Limit: the library follows TCI 2.0, so the `count` field and 2.1-only commands
are covered by the byte-level fixtures, not by it.

### 10.2 Adapter `rigplane.tci_adapter`

Unit: mode table, power scaling (`drive` 0, 1, 50, 100 on watts-native and
raw-255 radios), channel mapping, resampling, NaN/Inf replacement.

Conformance through the real adapter: the same `eesdr-tci` client, the leaf
server, the adapter, and a fake radio (`SerialMockRadio` or a
`rigplane_testkit` simulator):

- init burst order of 5.4; read/set round trips for `vfo`, `modulation`,
  `split_enable`, `rit_*`, `drive`;
- multi-client sync: a set from client A reaches client B and A;
- refused PTT replies `trx:0,false;`; unknown commands produce no reply;
- phase audio: `audio_start`, receive typed `TciDataPacket`s, send TX frames.

### 10.3 Seam and E2E (TX safety is the point)

Against the real `ManagedTxAuthority` composition used by
`tests/test_rigctld_managed_tx.py`, with simulator-backed fake radios
(`SerialMockRadio`, `rigplane_testkit` simulators). Each is written to fail before the code that satisfies it:

- key via TCI: authority intent is `PTT(tci-client-N)`; unkey releases;
- socket reset / close frame / server stop / task cancel while keyed: lease
  released, radio back to RX (assert authority snapshot, not only the reply);
- silent peer: the deadline watchdog (fake clock) releases within 15 s for a
  non-aligned last pong and for a permanently blocked `drain`;
- TOT disabled and TOT extended: the over is bounded by the configured value,
  not by 180 s, and the test asserts exactly that; a failed or twice-cancelled
  release leaves the documented `RELEASE_REQUIRED` outcome, not a silent success;
- `trx:0,true,mic1` after DIGU: refused, no PTT write; absent source keys the
  current input;
- refusal scope: B holds PTT, A's key is refused: A receives `trx:0,false;`,
  B and an observer keep `trx:0,true;`;
- `tx_frequency` and `dds`/`if` fixtures: split on/off, positive and negative
  XIT, XIT enable/disable, unequal A/B including B below A;
- split mode tuple: mismatched A/B modes, mode change before and after enabling
  split, asserted at key-down; `trx:0,true,tci` from USB/MIC without a prior
  `modulation`;
- channel B: `split_enable:0,false`, `vfo:0,1,<hz>` echoes and reads B, then
  `split_enable:0,true` leaves B unchanged;
- TX handoff A to B with A's frame suspended before admission and inside the
  push: no frame of A reaches B's over;
- `TX_CHRONO` cadence for mono and stereo; playout buffer at 50 ms and 150 ms
  under identical jitter;
- contention: a `rigctld` client holds PTT, TCI key is refused with
  `trx:0,false;`; and the reverse;
- unmanaged radio: `trx:0,true` refused, no `set_ptt` call recorded;
- `ControllerAuthority.mode == "remote"`: refused;
- TX audio with gate closed (unkeyed, stale observation, exception) pushes
  nothing to the lease; with gate open pushes converted s16le;
- browser `Origin` and rebinding `Host` refused at upgrade; native client with
  neither header connects;
- start/stop twice, port in use, cancellation during accept.

Mutation check: before merging C3 and A2, delete the owner release and the
gate check in a scratch branch and confirm the matching tests go red.

### 10.4 Existing suites as the pin for the refactors

P0a, P0c and P0d change no behaviour (P0a also adds the boundary test of 4.1, which is new). P0b and P0e add capability (new authority methods, a widened capability) and carry their own new tests. The pin for the unchanged parts is the current `rigctld`, web and
bridge suites (`tests/test_rigctld_*.py`, `tests/test_audio_bridge.py`, web
WebSocket tests) plus one new test per extracted unit that is written first and
fails (the new module does not exist yet).

### 10.5 Capture C0 and the real-app smoke

C0 (first, before C1 merges): a transparent WebSocket recording proxy (a small
script under `scripts/`: text frames and binary headers, no audio payloads)
placed between each real client and a TCI server the owner already runs
(AetherSDR with Flex). Targets: WSJT-X Improved, JTDX, OpsLog, MSHV.

The recorder keeps **both directions, labelled** (`c2s`, `s2c`), because some
open items can only be read from the server side: the announced `protocol` and
`device` strings, the RX binary-header fields (`length`, `channels`, `count`),
and the init-burst order. Handling:

- The full two-way capture is evidence, kept **outside the repository** (repo
  rule: detailed reports stay outside worktrees). It is never committed and
  never used as a test expectation, so no fixture reproduces another program's
  output.
- What goes into the repo is (i) the **client-to-server** lines as fixtures
  under `tests/fixtures/tci/`, and (ii) a short findings note of *facts*
  extracted from the server side (for example "`length` counted all channels")
  with the capture file name and line as provenance. Server replies in tests
  are authored from the spec.
- The recorder emits a coverage report: for every open item of section 5.2 it
  states whether the capture contained the events needed to answer it. An item
  without such events stays **open**; C0 is complete only when the report says
  so for the items a given PR depends on, and the recorder has a test that a
  capture lacking those events reports "incomplete".

Scope is split. **C0-RX** is RX-only with no PTT pressed and answers the
connect, VFO/mode/split, audio-start and RX-header items. TX items (`trx`,
`TX_CHRONO` exchange, TX header fields) need a **C0-TX** session with a dummy
load, minimum power, the owner present and his yes for each run (decided, Q10),
after the TX safety PR (C3) has merged; until then those items stay open and
A2 does not merge. It needs the owner's machine and radio.

Real-app smoke (RX only, one of JTDX or WSJT-X Improved, bench radio IC-7300 or
FTX-1): connect and "Test CAT" passes; dial and mode changes follow both ways;
band change; FT8 decodes for 15 minutes with audio from the TCI stream (phase
audio). "Enable Tx" off and PTT never pressed. Verify first through the server
API and logs (`radio-selfcheck`); evidence goes to `docs/validation/`. A TX
check is a separate acceptance on a dummy load at minimum power with the owner's
authorisation, and is not part of CI.

## 11. Delivery plan

The work is broken into **units**, then bundled into **five PRs** (coordinator
decision Q8: the owner's rule is PRs up to 4000 lines, so fewer, larger PRs, not
the 1000-line ceiling). Sizes are rough changed lines including tests. Unit
labels: P0 prerequisites, L leaf package, C control (adapter), A audio.

| Unit | Content | ~Lines |
|---|---|---|
| P0a | create `rigplane.tci` with `ws.py` (moved `web/websocket.py` plus server upgrade function with injected predicate; `web` behaviour, including its 60 s pong timeout, unchanged); move `host_guard` to `core/host_guard.py`; shims in `web`; `.importlinter` rows and `tests/test_tci_leaf_boundary.py` (written first, red) | 450 |
| P0b | extract the fail-closed TX gate from `WebServer._bridge_tx_gate_open` into a shared function; `WebServer` and the bridge call it; add `ManagedTxAuthority.owned_tx_submission(owner)` and the `before_on` preparation hook of 7.3a, with their tests | 650 |
| P0c | extract the managed-PTT/tuner submission choreography and session release out of `rigctld` into a shared module; `rigctld` calls it; add `"tci"` to `CommandSource` and `TxSource.TCI` | 900 |
| P0d | share the WSJT-X data-mode/MOD-input glue now in `rigctld/server.py` (apply and readback; restoring the input is not done in release 1, Q11) | 300 |
| P0e | widen `RelativeVfoModeWritable.write_relative_vfo_mode` to a numeric DATA sub-mode with readback, bounded by `data_mode_count`; IC-7610 simulator test | 300 |
| C0 | recording proxy script with coverage report, C0-RX fixtures and findings note (C0-TX follows C3) | 350 |
| L1 | leaf `protocol.py` (grammar, formatting) and `frames.py` (stream header, sample types), with spec fixtures and `eesdr-tci` round trips | 600 |
| L2 | leaf `server.py` and `handler.py`: listener, sessions, broadcast and coalescing, ping and independent deadline watchdog, Origin/Host predicate; conformance with a fake handler | 800 |
| C1 | adapter skeleton: handler, init burst, read-only commands, notifier; `tci_adapter/LAYER.md`; `.importlinter` adapter rows | 700 |
| C2 | adapter write commands: `vfo`, `modulation`, `split_enable`, `rit`/`xit`, `drive`, Tier B | 650 |
| C3 | TX: `trx`, `tx_enable`, disconnect, `tune` answer; seam tests | 800 |
| C4 | CLI flags and wiring, `check_ports_available`, docs, changelog | 450 |
| A1 | adapter RX path, shared decode helper, resample and channel mapping | 650 |
| A2 | adapter TX path: playout buffer, `TX_CHRONO`, owner-specific admission, route policy | 900 |
| A3 | real-app RX smoke evidence and fixtures | 150 |

| PR | Units | What it delivers | Depends on | ~Lines |
|---|---|---|---|---|
| 1. Leaf package | P0a, L1, L2 | `rigplane.tci` complete and tested with no radio: WebSocket, grammar, frames, server, boundary rule | none | 1850 |
| 2. Shared TX mechanisms | P0b, P0c, P0d, P0e | gate, `owned_tx_submission`, `before_on`, shared managed-key ingress, data-mode glue, numeric DATA sub-mode; `rigctld` and `web` call the shared code | none | 2150 |
| 3. Capture tool | C0 | recorder with coverage report, C0-RX evidence | owner and bench time | 350 |
| 4. Adapter, control | C1, C2, C3, C4 | `rigplane.tci_adapter`, control commands, TX safety, CLI wiring, docs | 1, 2 (3 informs C1 and C3) | 2600 |
| 5. Adapter, audio | A1, A2, A3 | RX and TX audio, real-app RX smoke | 4 | 1700 |

About 8.7k lines in 5 PRs. PR 3 touches no product code and can go any time. PR 1
and PR 2 are independent in content but both edit `web/server.py` (in
`_handle_websocket` and `_bridge_tx_gate_open`) and PR 2 also edits
`rigctld/server.py` in two units, so they merge one after the other and the
second rebases; that is a textual conflict risk, not a design dependency.

How a large PR is kept reviewable:

- **Logical commits, one per unit, in dependency order.** Inside PR 2 the
  behaviour-preserving extractions (P0b gate, P0c, P0d) come first and are
  separate from the added capability (`owned_tx_submission`, `before_on`, P0e).
- **Failing test first, per unit.** Each unit is a RED commit (its tests, failing
  for the stated reason) followed by a GREEN commit; `focused.yml` RED evidence
  is taken on the exact RED SHA. The seam and E2E tests of 10.3 land with the
  unit they pin; nothing is deferred to a later PR.
- **Pins before moves.** For P0a to P0d the existing `rigctld`, web and bridge
  suites are the pin (10.4) and are named in the PR body.
- `tmp/pre-review.sh` clean for the final head, one natural `quick`, exact-head
  review by a different model family from the builder.

Ceilings. The owner's 4000-line rule applies to the line count. `CLAUDE.md` also
caps a PR at 10 files; PR 4 (a new package with its tests) and PR 1 will exceed
it, so each records the file-count exception as the first line of its body, as
`N code + M other = K files`, under the coordinator's delegation of 2026-09-03.
`docs/development/review-checklist.md` sets a 500-line target and measured 2.1
Codex rounds above it; that is a review-efficiency target, and the logical
commits above are the mitigation.

Freeze. `AGENTS.md` prohibits new protocols during the stabilisation freeze
without an exception. The owner decision of 2026-10-27 (MOR-3821) is the
exception: each implementation PR carries `Freeze exception: owner decision
2026-10-27, MOR-3821` as the first body line (the file-count line follows it)
and the `freeze-exception` label. This design PR is `Change class: docs`.

## 12. Licensing and clean-room

- Spec: MIT, may be implemented freely.
- `eesdr-tci`: BSD-3-Clause-Clear, dev/test dependency only, not vendored, not
  shipped. `listener.py` and `tci.py` were read to learn what the test client accepts and sends.
- AetherSDR is GPL-3.0. Only its published docs were read, for client
  behaviour. No source was read, nothing is copied or paraphrased into Core, and
  no fixture contains its output (10.5). Behaviour facts are re-established by
  capture C0 against a running copy, as wire observation.
- WSJT-X Improved and JTDX are GPL; their source was not read.
- Command names, argument orders and stream layout are the spec's, not
  AetherSDR's.
- The leaf is MIT like the rest of Core and contains nothing derived from the
  GPL programs above, so extracting it later needs no relicensing. Its tests
  carry the `eesdr-tci` dev dependency with them.

## 13. Risks and open questions

| # | Item | Owner of the decision |
|---|---|---|
| Q1 | Default port. **Decided 2026-10-27 (coordinator): 50001.** | decided |
| Q2 | Core default. **Decided 2026-10-28 (owner, via coordinator): TCI is off in Core unless `--tci`; Pro turns it on.** | decided |
| Q3 | `tune`. **Decided 2026-10-28 (owner): not honoured in release 1** (6.4) | decided |
| Q4 | `protocol:` and `device:` strings. AetherSDR announces `protocol:ExpertSDR3,<version>` and clients are reported to switch behaviour on it; the safest default is the same program name, with the version and `device:` settled by C0 | C0 evidence |
| Q5 | Receiver count. **Decided 2026-10-27 (coordinator): `trx_count:1` first**; dual-receiver radios later | decided |
| Q6 | Remote PTT. **Decided 2026-10-28 (owner): parity with `rigctld`; the listener binds `127.0.0.1` by default and network exposure is one explicit setting shared by both listeners** | decided |
| Q7 | Shared mechanisms. **Decided 2026-10-27 (coordinator): extract first (P0a to P0e), no duplication.** | decided |
| Q8 | PR sizing. **Decided 2026-10-27 (coordinator): the owner's rule is PRs up to 4000 lines; plan fewer, larger PRs, each with failing-first and seam tests (section 11).** | decided |
| Q9 | WebSocket location. **Decided 2026-10-27 (coordinator): framing stays inside the leaf, `tci/ws.py`.** | decided |
| Q10 | C0-TX session. **Decided 2026-10-28 (owner): it comes later, with a dummy load at minimum power, the owner present and his yes for each run** | decided |
| Q11 | Input restoration after a `source=tci` over. **Decided 2026-10-28 (owner): not done in release 1; a documented known limitation.** If ever wanted it is new behaviour (saved settings; cleanup on preparation failure, rejection, cancellation, unkey and disconnect; fresh confirmed RX and ownership/generation validation before restoring; an explicit unresolved obligation when the release is uncertain; tests that restoration never changes routing during another owner's TX or `RELEASE_REQUIRED`). Raised by the third review round as P2 | decided |

Risks, in order: P0c (inside PR 2) changes the TX path that `rigctld` users rely
on, hence the strictest pin and an unchanged-behaviour review; PRs of up to 4000
lines cost review depth, mitigated only by the commit discipline of section 11; client behaviour is
reported, not captured, until C0; JTDX is reported to crash on non-conforming
servers; `length` semantics and `protocol` strings are open; the shared RX
decode helper may expose backend gaps (FTX-1, serial Icom) that need their own
fixes.
