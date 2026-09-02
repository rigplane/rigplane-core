# FTX-1 (Yaesu) — CAT manual vs implementation

## Authority and scope

- **Manual:** Yaesu FTX-1 Series CAT Operation Reference Manual **2508-C**.
- **Official source:** <https://www.yaesu.com/Files/4CB893D7-1018-01AF-FA97E9E9AD48B50C/FTX-1_CAT_OM_ENG_2508-C.pdf>
- **Retrieved:** 2026-08-14.
- **Pinned SHA-256:** `fbbd8eb6b12d1fec9474f3771f4b872ba4fd195dbe4b080cc2a1aae2b4ebc56c`.
- **Implementation surfaces:** `src/rigplane/backends/yaesu_cat/`, `rigs/ftx1.toml`, and the validation registry.

The vendor PDF is copyrighted and is not committed. This is a source-to-code audit,
not a hardware-validation record: historical FTX-1 observations are not evidence for
the current release candidate, and this document makes no live, RX, TX, or safety
claim.

### Revision-consistency witness

The audit index records the same manual revision and links to this file, which is the
single repository location for the source URL, retrieval date, and pinned digest.
Review both entries together whenever the manual changes.

## Receiver, VFO, and function semantics

The FTX-1 is a two-receiver (MAIN/SUB) radio. Its CAT words must not be projected as
a generic per-receiver VFO A/B model.

| CAT | Official 2508-C meaning | RigPlane reading | Status / owner |
|---|---|---|---|
| `VS` | Selects the operating receiver: `0` = MAIN TX/RX + SUB RX; `1` = MAIN RX + SUB TX/RX. | `select_receiver()` / `get_active_receiver()` use `VS`; this is receiver selection, not VFO-slot selection. | Existing command mapping; MAIN/SUB dispatch and UI acceptance: MOR-1671. |
| `SV` | Swaps MAIN-side and SUB-side. | A MAIN/SUB exchange, not a VFO-A/VFO-B swap. | No generic A/B swap is exposed; retain this distinction. |
| `AB` / `BA` | Copy MAIN to SUB / SUB to MAIN. | One-way receiver-side copies, not A-to-B or B-to-A VFO copies. | Generic A/B copy/exchange remains unsupported for this topology. |
| `FA` / `FB` | Read/write MAIN-side / SUB-side frequency. | `freq` / `freq_sub` map directly. | Existing mapping; receiver-specific restoration work is MOR-1676. |
| `IF` / `OI` | Read-only information for MAIN-side / opposite-band SUB-side, including frequency, clarifier, mode, memory state, squelch type, and shift. | `IF` is a MAIN composite status read; `OI` is not represented as a generic VFO-A/B status. | Do not infer cross-receiver symmetry from `IF`; profile-domain work is MOR-1670 and its children. |
| `FR` | RX function: `00` dual receive, `01` single receive. | A receive-mode setting, not VFO selection. | Existing command mapping; no per-control VFO abstraction may substitute for it. |
| `FT` | Selects transmitter: `0` MAIN, `1` SUB. | Transmitter-source routing, separate from `VS` and `FR`. | Existing command mapping; TX authority and interlock work remains separately gated. |

In particular, `VS`, `FR`, and `FT` answer three different questions: which receiver
is selected for the TX/RX pairing, whether both receivers are receiving, and which
side is the transmitter. `FA`/`FB` address MAIN/SUB frequency, while `AB`/`BA`/`SV`
operate between those sides. None of those commands establishes a per-receiver VFO
A/B selection, copy, or exchange.

## Material reconciliation from 2507-B

The prior committed audit named 2507-B, reduced the manual to an old command-count
summary, and treated `VS` through the failed generic `vfo_slot` harness path. Revision
2508-C's command list and detailed pages make the following audit corrections
material:

- `VS` is explicitly MAIN/SUB TX/RX selection; it is not the `ab_shared` VFO-slot
  operation. The production/UI follow-up is MOR-1671.
- `AB`, `BA`, and `SV` are respectively MAIN-to-SUB copy, SUB-to-MAIN copy, and
  MAIN/SUB exchange; they are not A/B copy or swap commands. This stays an honesty
  boundary, not a request to emulate missing VFO-A/B behavior.
- `FR` (dual/single receive), `FT` (MAIN/SUB transmitter), `FA`/`FB` (side-specific
  frequency), and the asymmetric `IF`/`OI` status records are separate command
  domains. Profile quantization/restoration defects belong to MOR-1670 and its
  children MOR-1676 through MOR-1682, rather than to a model-name frontend branch.
- The `MC` read form materially changed. Revision 2507-B documented the
  receiver-ambiguous `MC;`; revision 2508-C requires the receiver-qualified
  `MCP1;` in the manual's parameter notation (`MC0;` for MAIN or `MC1;` for
  SUB). The future memory API and parser must preserve that receiver identity;
  MOR-676 owns the feature.
- The old audit's current-looking live-run summary, including a TX-enabled claim, is
  historical and removed. Hardware acceptance remains outside this documentation PR.

## Gap register

| Gap class | Reconciled finding | Live Linear owner / state |
|---|---|---|
| D — receiver mismatch | MAIN/SUB UI dispatch must use documented `VS` semantics, not an A/B control. | MOR-1671 |
| A — profile domains | Native discrete domains need profile-derived lattices, not generic continuous assumptions. | MOR-1670; MOR-1676..MOR-1682 |
| A/C — VOX wiring | `VG` gain and `VD` delay remain unwired in the FTX-1 profile; the backend methods raise `NotImplementedError`. Acceptance requires real command wiring and verified payload widths. | MOR-674 — Backlog |
| B — validation coverage | BI break-in and PR/PL compressor on/off still lack the requested RMVR rows. The `sql_type.set` RMVR now exists, so its remaining failure is routed separately rather than counted as a missing row. | MOR-673 — Backlog; MOR-696 — Backlog |
| C — operator commands | `MX` MOX, `OS` repeater shift, and `TS` TX watch remain absent from the backend/profile surface. Documentation of `MX` or `TS` is not TX-test authorization. | MOR-675 — Backlog |
| C — memory/keyer feature | The memory bank plus CW/voice message-keyer surface remains unimplemented. For `MC`, implementation must use the 2508-C receiver-qualified read `MCP1;` (`MC0;`/`MC1;`), not the 2507-B `MC;`. | MOR-676 — Backlog epic; decompose before implementation |
| D — CAT mismatch | `sql_type.set` exists, and `rigs/ftx1.toml`'s `set_sql_type` now writes the single SQL-type digit 2508-C documents (`CT0{type};`), corrected from a two-digit write by MOR-2104. The recorded non-round-trip evidence is historical; current hardware acceptance is still required. | MOR-696 — Backlog |
| A — unsupported power | CAT power control must not imply a uniformly supported browser power control. | MOR-1673 |
| A — capability-derived bands | Exposed bands must follow actual profile capability rather than a static front-end list. | MOR-1674 |
| A — AF presentation | CAT AF-scale values require the agreed percent formatting. | MOR-1675 |
| D — TX behavior | `FT` routing is not authorization to key or test TX; interlock and RF-authority tickets own that safety work. | MOR-1500; MOR-1625..MOR-1630; MOR-1694 |

This audit deliberately does not create a second backlog, change a profile, or claim
that any listed command has completed hardware validation.

## Out of scope

Front-panel and menu-domain controls (including display, FUNC-knob assignment, and
the broad `EX` menu) are not automatically browser controls. A documented command is
not, by itself, a product-support or hardware-acceptance claim.

---

## Manual/profile census snapshot (2026-09-01)

The official Yaesu **FTX-1 Series CAT Operation Reference Manual 2508-C** is the
command authority; `rigs/ftx1.toml` is the runtime radio-fact SSOT. The companion
[family/domain gap CSV](ftx1-command-gaps.csv) is a dated derived audit snapshot,
not an implementation source or a second profile SSOT. It does not approve a bulk
profile/API update and it does not alter the active IC-7300 MOR-2158 profile HOLD.

This snapshot is pinned to census ref
`8cc5471dbb60f246ccb7a17a5e29f75fd6f20a00`, manual SHA-256
`fbbd8eb6b12d1fec9474f3771f4b872ba4fd195dbe4b080cc2a1aae2b4ebc56c`, and
profile SHA-256 `d81012e2d26cbe61ba1af86c97b0d28e630e4f8508733e4a4faab88792563c3c`.
Its read-only inputs were `ftx1-manual-profile-census.md`
(`751739289a2b19f140b9cf7e67d85611657202504fc32dae1c515e084ee606b2`) and JSON
(`31f6f1ce9af2a38654d8143a9c986a4ad1ffc1bb30ecd5bed51e467258b52859`).

### Reconciled unit of count

- 580 manual rows/subcommands are evidence rows across 90 raw command keys; 427 of
  those rows are `EX` menu addresses.
- The defensible planning unit is **40 wholly absent raw families plus 15 existing
  family extensions** (23 evidence rows). It is not a generated API-name total.
- `EX` is one parameterized menu-address grammar, `EO` one parameterized encoder-
  offset grammar, and `SS` one scope-settings family unless a future API design
  intentionally splits it. The CSV collapses those selector rows accordingly.
- Nine already-covered selector rows (one `AC`, eight `RM`) remain generic coverage,
  not nine new operations. Approved bulk additions: **0**.
- The CSV's stable rows have source IDs from the JSON (the `EX` aggregate records its
  `manual:EX:*` prefix and 427-row cardinality), classification, current declaration/
  reachability, confidence, and next action.

### Required semantic/provenance rulings

- `RC` (`reset_clarifier`) is profile-only and undocumented in 2508-C. Its omission
  does **not** authorize removal: retain it pending separate official provenance or
  hardware evidence.
- `FR00`/`FR01` is dual receive/single receive; it is a receiver-function domain,
  not VFO selection. `AB`/`BA` are MAIN/SUB copy directions, not VFO A/B aliases.
- `BP` documents on/off and frequency parameters but no notch-width register; keep
  any width API absent/stubbed until an official command exists.
- `VX` is the documented VOX status surface; `VG`/`VD` are absent-family evidence,
  while the manual-required/client-side VOX policy is not a new radio-side defect
  claim.

This is manual evidence, not new hardware evidence. It makes no bench, RX, TX, or
current-release validation claim; the historical observation boundary above remains
unchanged.
