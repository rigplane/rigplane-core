---
description: "Migrate RigPlane 2.11.1 consumers to Core 3.0 beta, with historical icom-lan to RigPlane rename guidance."
---

# Migrating to RigPlane Core 3.0 beta

The initial package target is `3.0.0b1`. This is a major-version migration with
selected compatibility aliases, not a promise that every 2.x consumer works
unchanged. Core includes the browser SDR interface; Pro packaging and its
release are a later, separate scope.

Installed-package, browser and hardware acceptance apply to the final release
candidate separately. The focused consumer examples below do not certify a
release or blanket compatibility with every 2.x consumer.

## Compatibility decisions from 2.11.1

The row IDs below follow the frozen
[MOR-2336 compatibility matrix](https://linear.app/morozsm/issue/MOR-2336).
Its source baseline is tag `v2.11.1` at
`997cca0e385e78cd47f630d398488d34df29cb17`; its static comparison snapshot is
`0126ca218526ef9b3e1a29e821596a1009d06b2d`. A preservation decision requires a
candidate witness before it becomes a tested compatibility claim.

| Row | 2.11.1 consumer surface | 3.0 migration decision |
|---|---|---|
| PY1 | `profile.vfo_swap_code` | Retain the deprecated, read-only expression `swap_main_sub_code or swap_ab_code`. |
| PY2 | `profile.vfo_equal_code` | Retain `equal_main_sub_code or equal_ab_code` with the same released fallback semantics. |
| PY3 | `from rigplane import TransceiverStatusCapable` | Intentionally removed; no import-only replacement stub. |
| PY4 | `RadioState(tx_freq_monitor=False)` and `.tx_freq_monitor` | Intentionally removed; remove this dependency. `txTarget` is not an alias. |
| PY5 | `set_tone_freq(freq_hz=8850)` / `set_tsql_freq(freq_hz=8850)` | Integer centiHz remains the public contract; Icom accepts the public keyword and the `freq_centihz` alternative. |
| PY6 | `rigplane.radio`, `rigplane.radio_protocol`, `rigplane.sync` | Retain existing module aliases and sync signatures; this does not retain the old TX lifecycle. |
| PY7 | Backend config/factory and session construction | Preserve audited public construction surfaces; installed-candidate witness remains required. |
| WEB1 | Root JSON `txFreqMonitor` | Removed; absence is not a measured `false`. |
| WEB2 | Root JSON `notchFilter` | Select `main.notchFilter` or `sub.notchFilter` explicitly. |
| WEB3 | HTTP `ptt` ON/OFF command payloads | Migrate to canonical owned momentary PTT or intentional latched TRANSMIT, described below. |
| WEB4 | HTTP routes, response metadata, WS framing | Retain existing shapes with the WEB1–3 semantic exceptions and application-token removal below; HTTP contract version stays independent of the package version. |
| EXT1 | Extension command boolean | Report the actual client transport boolean through the canonical intent path; false may leave an offline command queued. |
| EXT2 | Permissive extension command/parameter dispatch | Use strict non-TX intents with required parameters and receiver. |
| EXT3 | Host numeric `1`, manifest `host_api: "1.0"` or omission | Host `2`, explicit `host_api: "2.0"`; reject old or omitted declarations. Manifest schema remains `version: 1`. |
| CLI1 | `ptt on && sleep 10 && ptt off` | Use `rigplane ptt --for 10`; the command owns the hold and release. |
| CLI2 | Other CLI inventory, entrypoints, Python/dependencies/extras | Preserve audited inventory except the retired application-token options below; package metadata changes to the beta version. |
| CFG1 | Tone-capable custom profile without a table | Declare a supported named `[ctcss]` table. |
| WIRE1 | Empty successful response after raw timeout | Handle `RPRT -5` as an error. |
| WIRE2 | Raw `w` in read-only mode | Handle `RPRT -22`, including for raw reads; use structured reads. |
| WIRE3 | General rigctld framing and structured operations | Preserve audited wire shape; representative fake-provider witnesses remain required. |
| OUT1 | Private internals, arbitrary external profiles, blanket 2.x emulation | No compatibility promise; Pro release and external-consumer census are outside this scope. |

## Application-token removal

Remove `--auth-token` and `--auth-token-file` from Core launch commands;
the parser rejects both retired options. Core HTTP and WebSocket clients
no longer need a Bearer header or `?token=` query parameter. The stable API
registry keeps contract version 1 and reports `auth: "none"`.

Sources: `src/rigplane/cli/__init__.py: _reject_retired_auth_option`;
`src/rigplane/web/api_contract.py: WEB_API_CONTRACT_VERSION`,
`STABLE_HTTP_ENDPOINTS` and `STABLE_WEBSOCKET_ROUTES`;
`tests/test_web_auth_compare_digest.py: test_http_dispatch_needs_no_application_token`.

## Python APIs and CTCSS units

For new profile consumers, select the operation that matches the radio's VFO
model, using `swap_ab_code` / `equal_ab_code` or `swap_main_sub_code` /
`equal_main_sub_code`. A missing code does not grant an operation. The retained
PY1/PY2 aliases must reproduce the old `or` expression, including its falsy
fallback; they must not choose a new receiver or issue radio commands.

Remove `TransceiverStatusCapable`, `get_tx_freq_monitor`,
`set_tx_freq_monitor` and `tx_freq_monitor` dependencies. There is no general
mechanical replacement. If the intended function was XFC, inspect the actual
radio's supported XFC operation; do not infer support from the removed names
or substitute TX target selection. See the removal entry in the
[changelog](CHANGELOG.md).

The public `RepeaterControlCapable` contract uses integer hundredths of Hz,
despite the parameter spelling `freq_hz`. These are the canonical calls for
88.50 Hz on receiver 0:

```python
await radio.set_tone_freq(freq_hz=8850, receiver=0)
await radio.set_tsql_freq(freq_hz=8850, receiver=0)
```

Only call setters supported by the connected radio and its profile. Receiver
0 and 1 identify MAIN and SUB where available; choosing SUB does not create a
second receiver. Do not multiply an existing integer-centiHz value by 100.
Old Icom float-Hz behavior contradicted the already-published protocol; it is
not the preserved units contract. The Icom implementation also retains
`freq_centihz=8850` as an alternative spelling with the same integer units.
Supplying both non-`None` spellings raises `TypeError` before transport, even
when the values are equal; providing neither also raises `TypeError`.
Use one spelling per call.

Sources: `src/rigplane/core/radio_protocol.py: RepeaterControlCapable`;
`src/rigplane/runtime/radio.py: CoreRadio.set_tone_freq` and
`CoreRadio.set_tsql_freq`; `src/rigplane/profiles/__init__.py: RadioProfile`.

## Receiver state and unknown values

Replace a root notch read with a read for the receiver your consumer controls:

```javascript
// Old: const notch = state.notchFilter;
const receiverKey = selectedReceiver === 0 ? 'main' : 'sub';
const status = state.fieldStatus?.[`${receiverKey}.notchFilter`];
const notch = status?.observed === true && status.availability === 'available'
  ? state[receiverKey]?.notchFilter
  : undefined;
// Keep unavailable/missing values unavailable; do not use `?? 0` or `|| false`.
```

Here `selectedReceiver` must already be validated as a supported `0` or `1`.
`notchFilter` is an integer value, not an ON/OFF boolean. MAIN and SUB may
differ; the historical root field is not a reliable alias for either one.
With fresh MAIN/SUB observations, the witnessed values were respectively 37
and 192, with `fieldStatus` reporting `observed: true` and
`availability: "available"`. With no SUB observation, the response still
contained `sub.notchFilter: 0`, but its metadata was `observed: false`,
`freshness: "unknown"`, `availability: "missing"`. That zero is not observed
telemetry. A single-receiver IC-7300 fixture omitted both `sub` and its field
metadata. Preserve freshness/status information in the display and apply
capability checks; do not infer receiver availability from a numeric default.
Stop reading `txFreqMonitor`; replacing it with `txTarget` or a constant false
would invent different telemetry.

Sources: `src/rigplane/core/radio_state.py: RadioState.to_dict` and
`ReceiverState`; `src/rigplane/web/state_schema.py: ReceiverStatePublic`.
Consumer witnesses:
`tests/test_mor2338_web_compatibility.py: test_released_root_fields_migrate_to_explicit_receiver_state`,
`test_unobserved_receiver_default_is_not_observed_notch_telemetry` and
`test_single_receiver_payload_does_not_invent_sub_receiver`.

## TX consumers: ownership, admission, completion and OFF

Every TX consumer must use the new canonical lifecycle. No compatibility
adapter emulates the former ownership, completion or leave-keyed behavior.

The old HTTP command is intentionally rejected:

```http
POST /api/v1/commands
Content-Type: application/json

{"name":"ptt","params":{"state":true}}
```

On a writable server this is `409 unsupported_command`; the old HTTP OFF
family is also not an unconditional release route. Read-only and unavailable
servers can reject at earlier gates.
The focused witness covers `ptt` with either boolean and `ptt_on` / `ptt_off`
with empty parameters: all four are rejected without authority or radio calls.

Choose the lifecycle before migrating the call:

* **Momentary PTT:** use the canonical WebSocket PTT flow with a stable
  connection owner. Press and release belong to that same session; reconnect
  is not permission to replay an old press. Follow command admission and the
  current managed state through release. An owner release is distinct from
  unconditional OFF. The control command is `ptt` with `{"state":true}` on
  press and `{"state":false}` on release, with a distinct command ID per
  request. A response matching that ID with `ok: true` and `result.state`
  acknowledges admission. Disconnect releases that owner's intent; it does
  not issue unconditional ForceOff.
* **Latched TRANSMIT:** only for a consumer intentionally requesting that
  behavior, submit `{"operation":"transmit_on"}` to
  `POST /api/v1/managed-transmit/command`. This is not a drop-in rename of a
  momentary PTT request.
* **Unconditional OFF:** submit `{"operation":"force_off"}` to the same
  managed endpoint. This requests canonical release; it does not turn an
  unavailable or unobserved radio into confirmed RX.

An HTTP `202` with `result: "accepted"` establishes admission, not completed
device execution or observed RF. Read the managed document at
`GET /api/v1/managed-transmit` and distinguish operation progress from radio
observation; failures, disconnects and unknown observations cannot be shown
as confirmed ON or OFF. Do not retry positive TX automatically after a lost
response. The WebSocket flow must likewise preserve command identity and
session ownership rather than opening a fresh connection for each edge.

Sources: `src/rigplane/web/handlers/control.py: ControlHandler._enqueue_managed_ptt`;
`src/rigplane/web/server.py: WebServer._handle_http_managed_tx`.
The focused witnesses in `tests/test_mor2338_web_compatibility.py`
(`test_released_http_ptt_family_requires_explicit_migration`,
`test_explicit_latched_http_contract_reports_admission_only` and
`test_momentary_ws_commands_keep_owner_and_release_on_disconnect`) exercise
real HTTP connection parsing on an ephemeral TCP listener and production
WebSocket dispatch/JSON serialization with fake authority and radio. The WS
witness does not exercise network upgrade/frame transport or RF. Both HTTP
managed operations return `202 accepted` without waiting for settlement.

For scripts, replace sequential leave-keyed shell commands:

```bash
# Old sequencing no longer describes the command lifecycle:
# rigplane ptt on && sleep 10 && rigplane ptt off
rigplane ptt --for 10
```

Supply your usual connection options. `ptt on` remains alive until interruption
or the requested duration; release belongs to the holding command. Observe its
exit/error outcome and actual radio state. This example is a TX operation,
not an installation smoke test. Parser and lifecycle sources:
`src/rigplane/cli/__init__.py: _finalize_ptt_args`, `_cmd_ptt`, `_hold_ptt`.

## Core extension-host migration

The host contract uses numeric version `2` and an explicit
manifest declaration `host_api: "2.0"`. Old `"1.0"` and missing declarations
must be rejected, rather than interpreted as evidence of compatibility. This
version decision is separate from Python `3.0.0b1` and HTTP contract version 1.
The manifest schema itself stays at `version: 1`. Migrate the commands before
declaring the new host contract:

```json
{"version":1,"host_api":"2.0","extensions":[{"id":"my-control","entry":"/local/control.js","mount":"floating-overlay"}]}
```

The entry URL here is illustrative; it must resolve to your own extension
asset. TypeScript embedders replace `LocalExtensionHostApiV1` with
`LocalExtensionHostApiV2` from the host module.

An extension should use a strict canonical non-TX intent, for example:

```javascript
const host = window.rigplaneExtensionHost;
if (!host || host.version !== 2) throw new Error('Extension requires host API 2');
const accepted = host.sendCommand('set_notch_filter', { value: 128, receiver: 0 });
// `accepted` is client transport acceptance, not server admission or completion.
```

The boolean preserves the existing transport result: `true` means the current
socket accepted the client send. An offline idempotent command may be queued
while returning `false` and retaining a pending lifecycle. Therefore `false`
does not prove that no command is queued; do not blindly retry it. Neither
boolean establishes radio acknowledgement, server admission or completion.

Use this only when the radio advertises the corresponding control. Unknown
commands, PTT, extra parameters and a missing required receiver must not
dispatch. `window.icomLanExtensionHost`, when retained, names the same new
host object; it does not provide the old runtime. An extension that previously
sent PTT through `sendCommand` must migrate its TX integration to the canonical
ownership flow above, not rename its command through this non-TX facade.

Sources: `frontend/src/lib/local-extensions/host-api.ts: installLocalExtensionHostApi`;
`frontend/src/lib/runtime/commands/radio-intents.ts: dispatchRadioIntent`.

## Custom profiles and rigctld diagnostics

Tone-capable profiles declaring `repeater_tone`, `tsql` or `sql_type` require
a named catalog reference. For a radio whose verified supported table is
`standard_50`, the declaration is:

```toml
[ctcss]
table = "standard_50"
```

The catalog is `rigs/_ctcss_tables_v1.toml`; `rigs/ic7300.toml: [ctcss]`
provides a shipped example. Do not copy this name to another model without
checking that model's supported table. Missing, unknown and malformed
references fail loading. `ctcss_tones_centihz` is an ordered integer-centiHz
table; its order matters for index-based providers. A table reference does
not enable unsupported setters.

For raw rigctld diagnostics, handle timeout as `RPRT -5`, not empty success.
Read-only mode rejects every raw `w` request with `RPRT -22`, even a raw read;
use supported structured reads such as `f` for frequency. Never fall back to
raw commands to bypass read-only enforcement. See [rigctld API](api/rigctld.md)
and `src/rigplane/rigctld/server.py: RigctldServer`.

## Core SDR and acceptance scope

The 2.11.1 SDR behavior is the required migration baseline: retain available
spectrum/waterfall controls, tuning, VFO/mode/filter, radio controls, RX audio,
meters, reachable managed PTT/ForceOff, screen navigation and persistence.
This is an acceptance requirement, not a claim that the beta has passed it.
Use each radio's actual capabilities; an unavailable stream is not a blank
successful spectrum. Core packaging/install/rollback and browser/hardware
acceptance remain separate evidence from these compatibility examples.

## Historical migration: `icom-lan` v1 to `rigplane` v2

The following rename guidance describes the v2 transition. Its compatibility
and automatic-migration statements do not extend to the 3.0 changes above.

`rigplane` is the new name of the project formerly known as `icom-lan` (v1.x).
The rename shipped in v2.0.0 (May 2026). The old name was misleading — the
project has supported Yaesu, Discovery, and Xiegu radios alongside Icom since
v1.0 — and the rename also cleared a trademark risk around carrying a vendor
name into the paid Pro tier.

If you have v1.x code in production: **your existing scripts keep working**.
A deprecation shim re-exports the old import paths so v1 code runs against v2
without modification. You'll see a `DeprecationWarning` on first import. This
page is the short guide for moving fully to the new names.

## TL;DR for users

```bash
pip install --upgrade rigplane     # replaces `pip install icom-lan`
rigplane <args>                    # replaces `icom-lan <args>`
```

```python
# Old (still works, emits DeprecationWarning):
from icom_lan import IcomRadio, LanBackendConfig, create_radio

# New canonical form:
from rigplane import IcomRadio, LanBackendConfig, create_radio
```

## Breaking changes

| Surface | v1 (`icom-lan`) | v2 (`rigplane`) | Compatibility shim |
|---|---|---|---|
| PyPI package | `icom-lan` | `rigplane` | `icom-lan` frozen at v1.1.0; no future releases under the old name |
| Python import path | `icom_lan.*` | `rigplane.*` | `icom_lan.*` still importable, emits `DeprecationWarning` |
| CLI binary | `icom-lan` | `rigplane` | `icom-lan` retained as deprecated alias of `rigplane` |
| Exception class | `IcomLanError` | `RigplaneError` | Re-exported from `icom_lan` under both names |
| Env vars | `ICOM_LAN_REPORT_ENDPOINT`, `ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING`, `ICOM_LAN_LOG_DIR` | `RIGPLANE_REPORT_ENDPOINT`, `RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING`, `RIGPLANE_LOG_DIR` | Old names still honoured for one major release |
| LAN discovery wire | `b"ICOM_LAN_DISCOVER\n"` | `b"RIGPLANE_DISCOVER\n"` | Server accepts both request tokens |
| Diagnostic bundle | `icom-lan-bundle-v1` | `rigplane-bundle-v2` (default) | Triage service accepts both for at least 12 months |
| Docs site | `morozsm.github.io/icom-lan/` | `rigplane.dev` | Old GitHub Pages URL still redirects |
| GitHub repo | `morozsm/icom-lan` | `rigplane/rigplane-core` | GitHub auto-redirect active |

The `icom_lan` shim will be removed in a future major release (no specific
date). Move to canonical names when convenient.

## Preserved (intentionally not renamed)

Vendor identifiers stay vendor identifiers — they describe hardware, not the
product brand. Nothing changes here.

- **Vendor classes**: `IcomRadio`, `IcomBackend`, `IcomCommander`,
  `Icom7610Profile`, `YaesuRadio`, `YaesuCatRadio`, etc.
- **Backend directories**: `src/rigplane/backends/icom7610/`,
  `…/yaesu_cat/`, etc.
- **Vendor-config env vars**: `ICOM_HOST`, `ICOM_USER`, `ICOM_PASS`,
  `ICOM_PORT`, `ICOM_AUDIO_*`, `ICOM_CIV_*`, etc.

If your scripts use `IcomRadio` or set `ICOM_HOST=...`, that code is
**unchanged in v2** — no migration needed.

## Pro and local-extensions

If you embed rigplane in a Tauri/Pro shell using extension hooks, the
primary global is now `window.rigplaneExtensionHost`. The legacy alias
`window.icomLanExtensionHost` is preserved for v1.x extensions.

## When to actually update your code

The deprecation shim has no scheduled removal date. You can keep running
v1 imports against v2 indefinitely in the short term. Move to canonical
names when:

- You're already touching the imports for another reason.
- Your CI starts treating `DeprecationWarning` as an error.
- You ship a new release and want to drop the warning in your own logs.

There's no urgency. The shim exists precisely so the rename is a non-event
for downstream users.

## Full release notes

For the complete v2.0.0 entry — including new features, brand assets,
the `rigplane-bundle-v2` diagnostic schema, and CI/grep gates — see the
[CHANGELOG](CHANGELOG.md).
