# MOR-4777: radio-owned operating modes

Status: proposal for owner review; no implementation or hardware acceptance.
Linear: MOR-4777. Scope: detect, explain, and conditionally enter/leave an
operating context such as FTX-1 WIRES-X/HRI. Operating WIRES-X is out of scope.
Core owns shared state, protocol evidence and profile data; Pro consumes these
through the public boundary for CAT-client explanations. No Core telemetry.

## Evidence and CAT facts

Marks: **[doc]** manufacturer documentation; **[forum]** third-party or owner
report without an independently inspected capture; **[inf]** inference or
proposal. Code citations below describe this checkout, not hardware proof.
Manual references name local text/PDF editions, not an assumed newer revision.
Code references were inspected at baseline `5c7947953657`.

- **[doc]** `FTX-1_WIRES-X_ENG_2603-B`, opening HRI instructions: hold FUNC,
  display PAGE 3/3 FM/C4FM, select HRI MODE; this sets it ON. HRI-200 hardware
  is unnecessary. These instructions do not document OFF or CAT control.
- **[doc]** The same manual, pp.28–29, separately describes Node Mode
  AP RADIO / AP PC / DIRECT. This is a communication-method choice, not an
  HRI ON/OFF switch. Its digital-client exit on p.12 is DISCONNECT if connected,
  then hold GM/X until the frequency display returns and X disappears.
  That documents leaving a local WIRES-X connection, not HRI OFF.
- **[forum]** The MOR-4777 report associates MAIN firmware 1.12, HRI ON and
  rejected mode writes (`?;`), including FM-to-FM and HF, with accepted frequency
  writes; TX, dial lock, split and ordinary memory were reportedly excluded.
  The owner left the observed state by holding GM/X. Older supplied notes
  disagree about frequency-write acceptance; this is no controlled HRI A/B test.
  FUNC reportedly stopped opening the menu in that state.
- **[doc]** `FTX-1_CAT_OM_ENG_2508-C`, MD p.19, defines read `MD0;` / `MD1;`
  and SET `MD<receiver><mode>;`. `4` is FM, `H` C4FM-DN, `I` C4FM-VW;
  `0`, `G`, `J` are unassigned. IF p.17 and the OI definition distinguish modulation
  (P6) from VFO/memory source (P7). No table assigns an HRI meaning to mode 0.
- **[doc]** CAT's ID, IF/OI, MD, VM, LK, FR, FT, ST, RI and TX definitions
  contain no named HRI/WIRES-X active, node, room, or search/connection field.
  LK is dial lock; RI reports ordinary status. These fields cannot certify HRI.
- **[doc]** EX pp.8–15 uses six address digits: `EX<P1:02><P2:02><P3:02>;`
  reads; an appended value is a SET. RADIO SETTING / DIGITAL has 01/06/01
  DIGITAL POPUP, 02 LOCATION SERVICE, 03 STANDBY BEEP; 04/05 have no defined
  payload. No numbered HRI or WIRES-X Node Mode entry is listed.
- **[inf]** No documented CAT detector or enter/leave command was found in this
  revision. A newer manual, firmware behaviour, HRI search/connect replies,
  mode-code-0 correlation and any hidden EX mapping remain unknown. A changed
  EX answer is a candidate correlation, not permission to write that address.
- **[forum]** Supplied research cites FT-991/C4FM polling trouble and secondary
  firmware announcements. Neither establishes an FTX-1 HRI discriminator or
  actuator; those reports must not become protocol constants.

## Existing mechanisms to extend

Search preceded the model proposal. Reuse these seams rather than a radio branch:

| Current seam (file, symbol) | Consequence for this design |
|---|---|
| `src/rigplane/core/radio_state.py`: `ReceiverState`, `RadioState` | Modulation/VFO and global dial-lock/TX state exist; add context separately, never overload `mode` or `dial_lock`. |
| `src/rigplane/core/state_pipeline_contracts.py`: `FieldPath`, `Observation`; `src/rigplane/core/state_store.py`: `StateStore` | Reuse scope, source, timestamp, quality, max-age and provider-generation handling. |
| `src/rigplane/core/acquisition_scheduler.py`: `RadioStateModelService` | Extend the existing freshness/acquisition path, not an independent polling/cache service. |
| `src/rigplane/runtime/_civ_rx.py`: `CivRuntime._record_refused_reads`; `src/rigplane/core/acquisition_scheduler.py`: `AcquisitionScheduler.record_read_refusal`, `end_refusals` | Refused-read attribution is exact-request/path based; preserve it and its recovery semantics. It identifies the refused read, not the physical cause. |
| `tests/test_refused_read_attribution.py`: `test_a_refused_menu_read_refuses_only_its_own_menu_item` | Preserve menu-address and receiver isolation; a bare NAK cannot implicate a sibling path. |
| `src/rigplane/backends/yaesu_cat/radio.py`: `YaesuCatRadio._write`, `_status_mode_name`, `_set_mode_verified`; `src/rigplane/core/state_diagnostics.py`: `StateDiagnosticsRecorder.status_name` | Keep typed rejection distinct from timeout/readback mismatch; retain UNKNOWN for unassigned modulation. |
| `src/rigplane/backends/yaesu_cat/observations.py`: `YaesuObservationAdapter.poll_medium` | Add profile-selected observations through the same MAIN/SUB path. |
| `src/rigplane/web/server.py`: `_command_error_code`; `src/rigplane/rigctld/handler.py`: the `CommandRejectedError` branch in `execute` | Preserve `radio_nak` and Hamlib ERJCTED (`RPRT -9`); add structured explanation without changing wire contracts. |

Pro #2263 already names refused CAT mode writes to the user. Extend that
consumer's explanation using Core evidence; do not duplicate detection in its
relay or treat every nonzero RPRT as proof of HRI. Pro implementation stays in Pro.

## Supported-profile survey

The inventory below comes from each `rigs/<name>.toml` `[modes]` and the cited
manual sections. “No declaration” describes repository coverage, not proof that
hardware has no special context. None of these comparisons licenses new bytes.

| Profile(s) | Comparable context and implications |
|---|---|
| `ftx1` | **[doc]** HRI/node and WIRES-X client contexts above; H/I modulation is independent. **[inf]** Mode-write restriction is a reported effect, its scope and causal discriminator unproven. |
| `ft991a` | Profile `[modes]` maps E to C4FM, not the FTX-1 H/I pair. Supplied WIRES-X research describes external HRI-200 use. No profile HRI detector/actuator; C4FM alone must stay ordinary modulation. |
| `ic705` | **[doc]** Advanced manual rev.5, §§13,15: DR, Terminal and Access Point; Terminal forces DV and DR. CI-V rev.6, command 25/26 descriptions: DR refuses selector 01 (unselected VFO). This is a selector-specific restriction, not a blanket mode lock. |
| `ic905` | **[doc]** Advanced rev.0a, §§10,12: DR, Terminal and Access Point; Access Point permits frequency changes. CI-V rev.2, command 25/26: DR selector-01 restriction. DV in `[modes]` does not identify these contexts. |
| `ic9700` | **[doc]** Advanced rev.3, §§10,12: DR, Terminal and Access Point. CI-V rev.4, command 25/26: DR/memory/call restrict selector 01; satellite has separate restrictions. Do not transfer the FTX-1 restriction set here. |
| `ic7300`, `ic7300mk2` | `[modes]` declares analog/CW/RTTY; no DV or node/terminal declaration found. Leave this feature undeclared; ordinary memory/DATA is not evidence of a node context. |
| `ic7610` | `[modes]` also declares PSK, no DV or node/terminal declaration found. MAIN/SUB and DATA remain separate existing state. |
| `g7250` | `[modes]`: USB/LSB/CW/AM/FM. Profile `[radio]` / `[iq_lan]` describes exclusive HDSDR versus wfview network modes: a transport-context analogue, not evidence of a D-STAR hotspot or a mode-write lock. No detector proposed. |
| `tx500` | `[modes]` includes DIG, no node/terminal declaration; profile header says no runtime backend. DIG is not proof of a hotspot. Do not promise simulator/backend coverage that does not exist. |
| `x6100`, `x6200` | `[modes]` includes DIGI, no DV/C4FM or node/terminal declaration found. Digital audio and a serial terminal port do not establish such a context. |

For the Icom gateway modes, the cited Advanced manuals document entry through
DV GW → Terminal Mode / Access Point Mode and exit through Normal Mode (stop
external gateway software first where used). CAT detection/actuation is not
established by this survey; it remains unknown. These are model-fit examples,
not claims that Core currently controls gateway operation.
IC-705 Advanced rev.5, p.15-15, also says Terminal persists across power cycles
and cancelling it does not restore DV/DR. Reconnection cannot imply normal mode.

## Proposed shared state and attribution contract [inf]

Add optional `radio_owned_modes` to Core's radio state and public projection.
Represent each profile-declared context independently, so DR and Terminal can
coexist. A record contains:

- Stable profile context id, display label, scope (`global` or receiver id).
- Activity `active | inactive | unknown`; an active attribution has certainty
  `certain | probable`. Undeclared support is absent, never “confirmed inactive”.
- Evidence references: direct status versus inferred refusal, correlation to the
  affected command/receiver, and the existing observation freshness/generation.
- Restrictions as operation targets, e.g. `mode_write` on MAIN, or relative
  frequency/mode writes to the unselected VFO. Each is documented/verified or
  suspected; unmeasured effects remain unknown, not “all CAT locked”.
- Separate enter/leave capabilities (`unavailable | verified_cat`) and panel
  instructions with provenance. Neither capability is inferred from the other.

Use canonical `operator_controls` field paths at the declared scope and project
one typed representation into `RadioState`/HTTP/WS; consumers derive labels from
it. Do not add a second mutable truth in a backend or UI. Keep requested
modulation, observed modulation and this context distinct.

**Certain:** an explicit documented status, or an owner-accepted, uniquely
validated read discriminator tied to its tested firmware, identifies activity.
**Probable:** a profile-declared inference has fresh, correctly correlated
support but alternatives remain. A mode SET NAK alone, repeated NAKs, C4FM/DV,
mode 0/G/J, dial lock OFF or successful frequency reads establish neither HRI
nor its absence. Healthy frequency reads do not prove frequency writes work.

For FTX-1, reserve profile data for context `wires_x_hri`: label, entry manual,
owner-reported exit, suspected mode-write effect and evidence references.
Leave its direct detector and CAT actuators undeclared. A probable HRI rule
requires the owner's acceptance of correlated evidence: an actual requested
mode write refused, fresh same-side source/TX/lock/routing observations and
explicit treatment of alternatives such as memory, scan, TX or unsupported
request. Unknown exclusions prevent promotion. Existing accepted frequency
writes may support the case; never issue a write solely to test the hypothesis.
Until that rule is accepted, offer HRI as a conditional troubleshooting hint,
with activity unknown. Do not label today's inference as a confirmed detector.

Attribute a failed user write in its existing command lifecycle, then attach
context evidence. Do not feed write NAKs into read-refusal counters. Keep reads,
writes, receivers, menu addresses and connection generations distinct; a later
unrelated command must not inherit the cause. Timeout/malformed reply stays a
transport/parse failure. Keep the original terminal error for every caller.

Loss/expiry of evidence, reconnect or changed firmware makes activity unknown;
a fresh explicit OFF makes it inactive. Successful same-target mode writes
clear the matching refusal hypothesis, not every context; conflicting direct
status requires reconciliation. No synthetic write or automatic exit is a
recovery mechanism. Read-refusal recovery alone cannot prove HRI OFF.

**What it locks:** report only operation-specific measured/documented effects.
This proposal adds explanation, not a new command guard. The radio remains the
write authority; probable state neither blocks attempts nor disables all UI
controls. Existing TX/PTT safety policy remains independent. The real affected
users are mode-changing CAT clients; the simplest remedy is an attributed
terminal error and panel guidance, with no retry loop or per-radio preflight.

## Proposed English wording and enter/leave [inf]

Owner approval is required for these words; they are not final product copy.

| Evidence | Proposed text |
|---|---|
| Refusal, cause unknown | “The radio refused the mode change. Check the operating mode on the radio.” |
| Accepted probable rule | “The radio refused the mode change. WIRES-X/HRI mode may be active. Check the radio's display.” |
| Future certain HRI detector | “WIRES-X/HRI mode is active. The radio controls mode selection.” Use the second sentence only with a verified restriction. |
| FTX-1 panel guidance | “To enter HRI mode: hold FUNC → PAGE 3/3 FM/C4FM → HRI MODE. To leave: hold GM/X and verify the normal display returns.” Mark the exit as owner-observed until HRI OFF is independently verified. |

Separate a persistent context indicator from the failed-command notification;
show uncertainty and stale/unknown state. Do not assert HRI from `RPRT -9`.
Preserve rigctld replies; the app can explain them through its state channel.

No enter/leave app action is offered for FTX-1 now. Neither inferred HRI OFF
through the FUNC menu nor guessed EX/key-emulation bytes is an actuator.
An app action requires documented commands or a separately accepted, safely
proven procedure with positive state readback, failure/restoration behaviour,
firmware scope and RX validation. Never enter/exit as an implicit mode-change
fallback. For now: detect what evidence permits and explain panel steps.

## One owner-present, five-minute, read-only bench step (not executed)

One appointment, one CAT reader, OFF → ON → OFF; no unattended continuation.
The owner must be at the radio and accept this RX-only comparison. Before the
five minutes, isolate automatic node/gateway/PTT/VOX activation and stop other
CAT writers. Use an exclusive read-only capture, not managed station startup
(which can send force-RX). No SET, key emulation, tuning or test transmission.

1. **0:00–1:00:** owner confirms HRI OFF (normal display alone is insufficient)
   and records firmware, MAIN/SUB, VFO/memory and modulation. Read baseline A using the fixed list
   below, one request at a time, logging timestamps and complete raw replies.
2. **1:00–2:00:** owner enters via the documented FUNC → PAGE 3/3 FM/C4FM →
   HRI MODE path; records the visible ON indication. Read identical list B.
3. **2:00–3:30:** owner leaves using the observed GM/X long press; records
   whether HRI OFF is actually visible versus merely X disappearing. Read C
   with the identical list. Do not equate these two panel observations.
4. **3:30–5:00:** compare B−A and C−A by command and receiver: raw mode chars,
   IF/OI source, refusals/timeouts and EX values. Distinguish expected dynamic
   scan/squelch changes from a repeatable ON/OFF correlation. Record incomplete
   exit/restoration honestly; stop at five minutes without expanding the scan.

Fixed reads: `ID; TX; LK; IF; OI; MD0; MD1; VM0; VM1; FR; FT; ST; RI0; FA; FB;`
and `VE0; VE1; VE2; VE3;` (CAT command definitions; VE is receiver-independent).
EX allowlist from CAT Table 3 only: `EX010601; EX010602; EX010603;` (digital
popup/location/beep), `EX010312; EX010314; EX010315;` (FM modulation source,
RPTT selection, repeater shift). No address enumeration, DP-ID, reset or SET.
Require RX (`TX0;`) before each phase; stop for unexpected TX, a missing RX
answer, lost exclusive ownership or uncertain panel state. Send no corrective
CAT write. An unsupported read is a finding; never guess a replacement.
Keep three phase-labelled captures and panel observations as private evidence;
extract minimal sanitized simulator fixtures. No delta means “no discriminator
in this allowlist”, not “HRI has no CAT representation”. One A/B/A correlation
nominates a candidate for validation; it does not make it certain.

## Test plan and small PR ownership [inf]

Implementation is test-first; this design-only change runs no tests. On the
simulator, add failing cases before each behaviour, then verify remotely with
focused changed-scope runs; do not use a physical radio to discover test bytes.

| PR / one behaviour owner | Regression evidence and scope |
|---|---|
| 1 — Core state/profile owner | Add typed scoped context, profile declarations, serialization/freshness. Parameterize FTX-1, FT-991A and IC-705; inactive/unknown/probable/certain, missing declarations, coexistence, receiver isolation, expiry/reconnect/firmware change. No detector enabled by invented fixtures. |
| 2 — Core attribution owner, after PR1 | Extend exact-request refusal evidence and generic context derivation. FTX-1/FT-991A simulated MD SET NAK versus valid MD reads; ordinary C4FM and 0/G/J never identify HRI. IC-705/IC-9700 DR selector-specific fixtures from their manuals; distinguish memory/satellite alternatives. No state leaks across concurrent commands/receivers; no retry or probe writes. Reuse refused-read attribution regression cases. |
| 3 — Core presentation owner, after PR2 | HTTP/WS/CLI explanation and public state projection; preserve radio_nak, HTTP status and rigctld -9, distinguish timeout/parse/readback failures. Assert exact English strings after owner approval and verify stale evidence cannot produce certain text. |
| 4 — Pro consumer owner, after PR3 | Adapt #2263 to structured evidence and agreed words; verify CAT-client banner recovery, accepted mode write, disconnect and an unrelated failed RPRT. Consume Core; no duplicate detector. |
| Later — Core actuation owner, only after evidence acceptance | Separate PR only if safe enter/leave becomes provable; otherwise retain panel-only capability. Test readback, interrupted/failed transitions and no implicit entry/TX. |

Reuse `rigplane_testkit/yaesu/cat_fake.py` and simulator profiles where present;
add narrowly scoped scripted faults for SET-only rejection (rejecting all MD
also rejects reads). Treat synthetic context samples as model tests, never
hardware proof. IC-9700 cases need explicit simulator support/fixtures; TX-500
is profile-only coverage until a backend exists. Each implementation PR owns
its failing regression and fix, one reviewable contract, and remote run links.

## Questions for the owner

1. Approve the shared model and uncertain wording before implementation?
2. In the observed exit, was HRI OFF verified, or only the normal display/X?
   Can the five-minute appointment capture that distinction safely in RX?
3. Accept a probable inference after controlled evidence, or keep the conditional
   hint until an explicit discriminator exists? Which firmware must be covered?
4. If no read discriminator is found, accept panel-only support and unknown
   activity, with app enter/leave unavailable until Yaesu documents a safe path?
