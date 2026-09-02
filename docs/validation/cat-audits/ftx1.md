# FTX-1 (Yaesu) — CAT manual vs implementation

## Authority and scope

- **Official authority:** Yaesu FTX-1 Series CAT Operation Reference Manual
  **2508-C**.
- **Official source:**
  <https://www.yaesu.com/Files/4CB893D7-1018-01AF-FA97E9E9AD48B50C/FTX-1_CAT_OM_ENG_2508-C.pdf>
- **Retrieved:** 2026-08-14.
- **Manual SHA-256:**
  `fbbd8eb6b12d1fec9474f3771f4b872ba4fd195dbe4b080cc2a1aae2b4ebc56c`.
- **Runtime radio-fact SSOT:** `rigs/ftx1.toml`, SHA-256
  `a18d0026302b9f76f331090894f773207a60a626d4333e748b074220c10d3b63`.
- **Derived decision register:**
  [`ftx1-command-gaps.csv`](ftx1-command-gaps.csv).

The vendor PDF is copyrighted and is not committed. The profile remains the
runtime source of truth after each approved declaration. This document and its
CSV are pinned derived evidence: they classify official-manual coverage and
route bounded follow-up work, but do not authorize a bulk profile/API update.
They also make no live-radio, RX, TX, or current-release validation claim.

The normalized decisions were independently verified from the corrected census
(MD SHA-256
`751739289a2b19f140b9cf7e67d85611657202504fc32dae1c515e084ee606b2`;
JSON SHA-256
`31f6f1ce9af2a38654d8143a9c986a4ad1ffc1bb30ecd5bed51e467258b52859`)
against profile ref `5bb947db845d801d1ab53e47f9b6d9425048e7bf`.

## Reconciled denominator

The former “43 of 89 commands are absent” statement is retired. It mixed an
index count with a different implementation unit and cannot be used as an
acceptance gate.

- The official command-list index contains **89** unique CAT families.
- `EO` exists only in the detail tables, so the official detail-table
  denominator is **90 families**.
- The detail tables contain **580 evidence rows/subcommands**. Of those, 427
  belong to the single parameterized `EX` menu-address grammar.
- The current profile represents 50 documented families. Another 40 families
  are wholly absent, covering 488 evidence rows.
- Fifteen of the 50 represented families have uncovered selector/domain rows,
  covering 23 evidence rows.
- Therefore the implementation-planning register is exactly
  **40 wholly absent families / 488 rows + 15 existing-family extensions /
  23 rows = 55 normalized decisions**.
- The profile has one additional undocumented family, `RC`, handled
  separately as provenance rather than as a manual gap.
- Approved bulk additions: **0**.

`EX` is one parameterized family, not 427 APIs. `EO` is one parameterized
family, not six APIs. `SS` is one selector-aware scope-settings family and
`VE` one selector-aware firmware-version read unless a later public API
decision intentionally splits their semantics.

## Normalized disposition totals

The CSV contains one stable row for each of the 55 decisions, with official
page/domain/direction, current profile coverage, normalized semantic group,
disposition, owner, reachability gate, bench requirement, and bounded next
action.

| Disposition | Families | Meaning |
|---|---:|---|
| `route_existing_owner` | 21 | An existing Linear owner must absorb the documented family/domain without duplicating another implementation. |
| `extend_existing_generic` | 14 | Extend the existing generic family by literal receiver/function selector and prove the active path. |
| `new_profile_candidate` | 6 | A bounded public-domain decision is required before declaration. |
| `composite_only` | 4 | Keep one typed parameterized/composite grammar; expose only approved semantic fields. |
| `intentional_no_public_surface` | 9 | Keep the manual fact in the audit, but do not expose a runtime/UI operation. |
| `unresolved_manual_semantics` | 1 | Resolve the official-manual/live-radio conflict before declaration. |

The totals reconcile to 55 exactly. No row is permission to implement the
remaining register as one batch.

## Required MAIN/SUB semantics

FTX-1 CAT commands describe MAIN/SUB receiver sides. They must not be renamed
or projected as generic VFO A/B operations.

| CAT | Official 2508-C meaning | Required RigPlane ruling | Owner / gate |
|---|---|---|---|
| `FR00` / `FR01` | Dual receive / single receive. | Treat as receiver-function state. Existing templates do not prove executor reachability. | MOR-2189 / MOR-2161 |
| `SV;` | Swap MAIN and SUB. | SET-only MAIN/SUB exchange; never expose as VFO A/B swap. | MOR-2189 |
| `AB;` | Copy MAIN to SUB. | Replace the false `vfo_a_to_b` meaning; prove the active executor path. | MOR-2189 / MOR-2161 |
| `BA;` | Copy SUB to MAIN. | Replace the false `vfo_b_to_a` meaning; prove the active executor path. | MOR-2189 / MOR-2161 |
| `VS` | Select the MAIN/SUB TX/RX pairing. | Receiver selection, separate from `FR` and `FT`. | Existing mapping; MOR-1671 |
| `FT` | Select MAIN or SUB transmitter. | Transmitter-source routing, not permission to key TX. | Existing mapping; RF-authority gates |
| `FA` / `FB` | MAIN / SUB frequency. | Preserve literal side identity. | Existing mapping |
| `IF` / `OI` | MAIN / opposite-band SUB composite status. | Parse into canonical side-specific fields; do not infer A/B symmetry. | `OI`: MOR-2189 / MOR-2161 |

No MAIN/SUB tracking command is documented in 2508-C. Existing tracking stubs
are not evidence for a radio capability.

## Corrections and provenance boundaries

- **`MW` is SET-only.** Both the printed command list and detail table omit
  READ and ANSWER. The earlier read/write census classification was wrong.
- **`RC` is undocumented profile provenance.** The profile declares
  `reset_clarifier`, but 2508-C does not document `RC`. Retain it until an
  official provenance source or bounded bench evidence supports removal; do
  not count it in the 55 manual gaps.
- **`ML` is the sole unresolved family.** The official table documents
  monitor state/level read and write, while the profile comment records a live
  `?;` response. Reproduce by firmware, mode, and source and compare the
  corresponding `EX` monitor address before adding a declaration.
- **`BP` documents state and frequency, not width.** Do not invent a
  manual-notch width register.
- **`DA` is intentionally not public.** MOR-676's product ruling and this
  MOR-2112 decision retain the physical-display control in the census without
  creating a runtime/UI operation.
- **`VD` and `VG` are intentionally not public.** The later MOR-2112 owner
  ruling supersedes the old VOX implementation request; MOR-674 is
  **Canceled**.
- **`OS` ownership is exactly MOR-2111/MOR-2160/MOR-2161.** MOR-2160 now
  routes the official P1 selector through profile-owned `OS{receiver}`
  templates and polls distinct MAIN/SUB readback with the same 30/120 policy.
  The manual's FM-only rule and four-value direction domain remain unchanged;
  MOR-2161 still owns caller-visible executor completion/refusal propagation.

## Reachability and safety gates

A profile declaration is not “implemented” merely because a template or
backend method exists.

### Reads

Every declared read requires all of:

1. a typed response parser;
2. routing into the canonical observation/state model;
3. proof that the active Yaesu receive/observer path reaches it;
4. a loud, test-covered failure for malformed or unsupported responses.

This applies especially to the safe read candidates `OI`, `RI`, `VE`,
and `MR`. Read-only status does not waive parser or observer reachability.

### Writes

Every declared write requires all of:

1. an approved public operation and bounded value domain;
2. admission policy for mode, receiver side, TX authority, and other
   preconditions;
3. proof that the active Yaesu executor sends the command;
4. loud refusal instead of asynchronous false success when the route is
   missing or the radio rejects the command;
5. RMVR/readback verification, or an explicit fire-and-forget rationale with
   a separately observable effect;
6. Mac mini automated validation and, where `bench_required=true`, separate
   live FTX-1 hardware acceptance with restoration evidence.

`MX` and `TS` additionally remain behind managed-TX authority. Documentation
of a TX command is never authorization to key the transmitter.

All 14 generic selector extensions, plus the separately routed `OS`
extension, must use literal MAIN/SUB semantics and the actual Yaesu
executor/observer. Adding profile strings alone would repeat the
declaration-without-reachability failure.

## Ownership and sequencing

1. Retire or freshly reconcile draft PR #1717 before any FTX-1 profile edit.
   It is a stale 88-file change with failed checks and still touches
   `rigs/ftx1.toml`; it does not own these audit documents.
2. Land this normalized documentation and CSV after independent review.
3. Complete `SV`/`AB`/`BA`/`FR` under MOR-2189 behind MOR-2161
   reachability.
4. Route memory/keyer/voice families to MOR-676 and repeater `OS` only to
   MOR-2111/MOR-2160/MOR-2161.
5. Preserve intentional no-public rulings for physical controls and remote
   VOX. Create new children only for approved bounded domains.
6. Close each child only after its exact parser/executor/test/bench acceptance
   evidence lands. Never create a catch-all “remaining 55” implementation
   batch.

## Out of scope

This audit does not modify `rigs/ftx1.toml`, backend/runtime code, public API,
or hardware state. Front-panel and menu-domain commands are not automatically
browser controls, and a documented CAT command is not by itself a
product-support or hardware-acceptance claim.
