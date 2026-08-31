> **Point-in-time audit snapshot (2026-08-30).** Produced by the `auditor`
> subagent (Claude Opus) following `.claude/skills/mechanism-audit/SKILL.md`.
> Tree audited: `7479ebd5` (a main commit, merged via #2838), re-checked
> against `6a30cc14` by the commissioning coordinator (the one-commit delta is
> frontend-only and touches nothing in scope). Every citation in this
> document — including the file:line forms used where no symbol encloses the
> evidence — is frozen at `7479ebd5`; resolve against that revision, not HEAD.
> This file is an archived report, not maintained documentation. Paths were
> normalized to repo-relative form at archival time; content is otherwise the
> auditor's verbatim report, except for the archival corrections declared
> below.
>
> **Archival corrections (2026-08-30, from the independent review of this
> archive).** The verifier re-measured the call-site census and the
> coordinator reproduced the re-measurement (AST census over
> `git archive 7479ebd5 src tests`, Python 3.13, zero parse failures).
> Five claims were corrected in place; no verdict changes: the census rows
> for `equalize_vfo_ab` (5 src / 4 tests, was 4/5) and `equalize_main_sub`
> (6 src / 3 tests, was 4/5); the Cleared section's mixin call-site total
> (16, was 13); F1's implementation count in its heading (seven, was five)
> and its Falsifier's web-site count (four, was five); and D3's totality
> claim, narrowed because `runtime/sync.py: IcomRadio.vfo_equalize` /
> `.vfo_exchange` are live public API on the blocking wrapper and out of
> D3's scope. The uncorrected body text survives in git history at the
> branch's first commit (`e754a2e2`).

# Mechanism audit — VFO equalize/swap over CI-V `0x07`

**Method:** `.claude/skills/mechanism-audit/SKILL.md` (read in full; order of operations, verdict taxonomy, deletion guards and report format taken from it verbatim).
**Audited revision:** `7479ebd5`. Tree clean (`git status --short --branch` → no entries). No writes, no git/gh writes, no test runs; all bash foreground with explicit timeouts. One read-only exception to "repo only", declared: I ran a literal `grep` against a local read-only checkout of rigplane-pro (at `900e180`) to settle the out-of-repo guard — read-only, no writes.

**Premise handling.** The dispatch handed me an observed fact plus four competing explanations. I re-established every consumer set myself rather than adopting the initial grep. I also record that PR #2846's branch adds a docstring to `vfo_a_equals_b` asserting the conclusion under test ("No production caller reaches this builder") — I treated that as data, not as a finding, and verified independently.

**Instructions-in-files check.** No file in the audited tree attempts to direct an agent. The PR #2846 body contains agent-workflow prose ("flagged via `spawn_task` for a proper mechanism audit"); it is a report about work, not a directive, and I did not act on it.

---

## Enumeration (step 3a sweep, reported as counts)

`src/rigplane/commands/vfo.py` — 30 module-level names (27 functions, 3 constants, 3 compat aliases; AST enumeration, not spotted while reading). Reference counts computed per name with `git grep -c -w`, excluding `vfo.py` itself, over `src/ tests/ rigs/ frontend/src/`:

| Name | refs in `src/` outside `vfo.py` | what they are |
|---|---|---|
| `vfo_a_equals_b` | **2** | both in `commands/__init__.py` (import + `__all__`) — i.e. **zero references of any kind** beyond re-export |
| `vfo_swap` | 13 | 2 re-export; the other 11 are the unrelated WebSocket intent string `"vfo_swap"` in `web/handlers/control.py` and `web/server.py` — **zero references to the builder** |
| all 28 others | ≥ 2, each with real readers | survive; no further mention |

`VALID_SCAN_TYPES` / `VALID_DF_SPANS` / `VALID_SCAN_RESUME` each show 2 (re-export only) but are read *inside* `vfo.py`'s own guards, so they are alive. Out of this capability's scope.

**Call-site census (AST, not text).** I walked every `ast.Call` in every `.py` under `src/` and `tests/`, matching both `Name(id=…)` and `Attribute(attr=…)` — so `self._commands.vfo_swap(...)`, `commands.vfo_swap(...)` and `raw_commands.vfo_swap(...)` would all have been caught:

| symbol | call sites in `src/` | call sites in `tests/` |
|---|---|---|
| `commands/vfo.py: vfo_a_equals_b` | **0** | 1 — `tests/test_commands_extended.py: TestVfoCommands.test_vfo_a_equals_b` |
| `commands/vfo.py: vfo_swap` | **0** | 1 — `tests/test_commands_extended.py: TestVfoCommands.test_vfo_swap` |
| `_dual_rx_runtime.py: DualRxRuntimeMixin.swap_vfo_ab` | 3 | 6 |
| `…equalize_vfo_ab` | 5 | 4 |
| `…swap_main_sub` | 2 | 6 |
| `…equalize_main_sub` | 6 | 3 |

This is the discriminator the method demands, and it is unambiguous.

---

# Deletions

### D1 — `commands/vfo.py: vfo_a_equals_b` and `commands/vfo.py: vfo_swap`: vestigial fork

**Verdict:** vestigial-fork
**Elements:**
- Abandoned side: `src/rigplane/commands/vfo.py: vfo_a_equals_b`, `: vfo_swap`
- Live side: `src/rigplane/runtime/_dual_rx_runtime.py: DualRxRuntimeMixin.equalize_vfo_ab`, `.swap_vfo_ab`, `.equalize_main_sub`, `.swap_main_sub`

**Consumers:** tests only. `tests/test_commands_extended.py: TestVfoCommands.test_vfo_a_equals_b` and `.test_vfo_swap` (one call each). Plus three name-enumerating harnesses that touch them without exercising them: `tests/test_profile_command_binding.py` (iterates `commands.__all__`), `tests/test_command_map_parity.py` and `tests/test_profile_command_coverage.py` (builder censuses), and `tests/test_ic7610_parity_matrix.py: _resolve_symbol` (resolves `rigplane.commands:vfo_swap` / `:vfo_a_equals_b` named in `docs/parity/ic7610_command_matrix.json` entries #9/#10 via `importlib` + `getattr`). *Observation.*

**Written / read:** `git grep -n "vfo_a_equals_b\|vfo_swap"` across all tracked files → the only `src/` occurrences of `vfo_a_equals_b` are `commands/__init__.py` lines 207 and 759 (import + `__all__`). The AST call census above gives 0 production call sites for both. *Observation.*

**Guards checked:**
- **Dynamic access — searched literally, then structurally.** The only name-based resolution of a `rigplane.commands` builder anywhere in `src/` is `commands/bound.py: BoundCommands.__getattr__`, which does `getattr(_commands, name)` for whatever name a *caller* writes. I enumerated every attribute call in `src/`: no call site writes either name (AST census). `getattr(commands, …)` appears only in test files. No entry-point registry, no string→builder table, no TOML→callable binder exists: `_build_from_map` maps a *string key* to wire bytes, never a builder. `rigs/*.toml` contains neither `vfo_a_equals_b` nor `vfo_swap` as a `[commands]` key (checked all 9 files). ✅
- **Out-of-repo.** rigplane-pro at `900e180` (read-only grep, literal): **zero** occurrences of `vfo_a_equals_b`; zero `from rigplane.commands` / `import rigplane.commands` in any `.py`. The two `vfo_swap` hits are (a) a bundled copy of core's own `rigs/_keyboard-default.toml` keyboard action, (b) a doc row listing WebSocket intent names — neither is the builder. `docs/architecture/open-core-policy.md` §5 settles the policy question: *"Internal helpers under `radios/`, `commands/`, `commander.py`, and transport are **not** part of the boundary. They may change freely as long as the protocol surface is preserved."* No `local-extensions/` Python directory exists; `frontend/src/lib/local-extensions/host-api.ts` is a TypeScript `dispatchCommand(name)` surface over WS intent strings, not Python builders. ⚠️ **Residual (unknown):** I saw one checkout of one Pro branch at one revision; another branch could differ. Settled by a literal grep of every Pro branch, which I cannot run. ✅ with that caveat.
- **Public API surface.** Both are in `rigplane.commands.__all__` and documented in `docs/api/commands.md` §VFO. Neither is in `src/rigplane/__init__.py`'s `_LAZY_MAP`, so `from rigplane import vfo_swap` does not work — only `from rigplane.commands import vfo_swap`. `docs/api/public-api-surface.md` places `rigplane.commands` builders under "Advanced / implementation detail" and assigns them no explicit tier. The project's own precedent for changing them: three CHANGELOG "Breaking changes" entries under `[Unreleased]` covering exactly this module family — MOR-2006 config.py (18 builders), MOR-2006 levels.py (50 builders), MOR-1986 `set_vfo`. So removal is permitted but is a **declared breaking change**, not a silent sweep. ✅ with that cost.
- **Tests as only consumer.** Yes — and flagged: deleting the code means deleting `TestVfoCommands.test_vfo_a_equals_b` / `.test_vfo_swap`, which assert `parsed.data == b"\xa0"` / `b"\xb0"` and nothing else. Those assertions currently pin a byte no shipped rig's profile agrees with on dual-RX (see Divergence in F1). That is a human decision, per the method. ✅

**Collateral:**
- `tests/test_commands_extended.py: TestVfoCommands` (both methods, and the two names in its `from rigplane.commands import (…)` block).
- `docs/api/commands.md` §VFO — two signature lines.
- `docs/parity/ic7610_command_matrix.json` entries #9 ("VFO Swap M/S") and #10 ("VFO Equal MS") `runtime_symbols` — otherwise `tests/test_ic7610_parity_matrix.py: test_ic7610_parity_matrix_supported_entries_have_real_evidence` goes red at `_resolve_symbol`.
- `docs/CHANGELOG.md` — a `### Breaking changes` entry, matching the MOR-2006/MOR-1986 precedent.
- The two generated censuses (`tests/command_map_parity_uncovered.txt`, `tests/profile_command_coverage_gaps.txt`) regenerate; on PR #2846's head they gain `requires-map vfo.py:vfo_a_equals_b` / `:vfo_swap` rows that would then have to come back out.

**The decisive fact (observation).** These are not merely uncalled — they are exact frozen-argument aliases of a live sibling in the same file. `vfo.py: set_vfo` resolves `_build_from_map(cmd_map, "set_vfo", …, data=bytes([code]))`; `vfo_a_equals_b` resolves `_build_from_map(cmd_map, "set_vfo", …, data=b"\xa0")` — **the same map key**, same decoder, same frame builder. `bytes([0xA0]) == b"\xa0"`. The no-map branches are equally identical: `_frame.py:104-105` defines `_CMD_VFO_SELECT = 0x07` and `_CMD_VFO_EQUAL = 0x07`, two names for one byte. So on every path and every profile, `vfo_a_equals_b(to_addr=T, cmd_map=M)` is byte-for-byte `set_vfo(0xA0, to_addr=T, cmd_map=M)`, and `vfo_swap` is `set_vfo(0xB0, …)`. There is no capability here that `set_vfo` does not already offer. *(Inference from reading both functions and the two constants; not executed.)*

**Steelman, before the verdict — the strongest case for keeping them.** Three arguments, taken seriously:
1. *"They are documented public API; an out-of-tree script may import them."* True, and it is why this is not a free deletion. But the same is true of the 68 builders MOR-2006 broke in the two merged PRs immediately preceding this revision, with a CHANGELOG note and nothing else. The repo's recorded policy (`open-core-policy.md` §5) explicitly excludes `commands/` from the protected boundary.
2. *"A tracked contract document names them as the IC-7610 implementation."* `docs/parity/ic7610_command_matrix.json` entry #10 lists `rigplane.commands:vfo_a_equals_b` as a runtime symbol for "VFO Equal MS", wire `\x07\xb1`. This is the strongest-looking evidence for liveness — and it collapses on inspection: the builder emits `\xa0`, not `\xb1`. `test_ic7610_parity_matrix.py: _resolve_symbol` only checks the attribute *exists*; it never compares the builder's output to the `wire` field, so that test cannot discriminate a correct citation from a wrong one. The document is evidence that someone once believed these were live, not that they are.
3. *"They are the raw escape hatch for a script that wants the standard Icom bytes without a profile."* `set_vfo(0xA0, …)` is that escape hatch, and has been since MOR-1986. A second name for it is not a capability.

The steelman does not win. It does establish that the deletion costs a CHANGELOG entry and a doc correction.

**Depends on:** none for deletion itself. **Must not be bundled with** F1 or F2 — this is the abandoned side of a fork; merging its behaviour into the live side would carry the hardcoded byte into working code, the exact failure mode the method warns about.
**Confidence:** high (for "no production consumer in this repository, at this revision"). Medium for "no consumer anywhere", bounded by the single-Pro-branch caveat.
**Falsifier:** any of — (a) a `rigplane.commands` import of either name in a Pro branch I did not see; (b) a call site written as `getattr(self._commands, some_variable)()` where `some_variable` can equal `"vfo_swap"`, which my AST census would miss (I found no such construct in `src/`, but the census matches literal attribute names only); (c) an owner ruling that `rigplane.commands.__all__` is itself the contract, independent of `open-core-policy.md` §5.
**Fix class:** delete.

---

### D2 — `profiles/__init__.py: RadioProfile.vfo_swap_code` and `.vfo_equal_code`: dead properties

**Verdict:** dead
**Elements:** `src/rigplane/profiles/__init__.py: RadioProfile.vfo_swap_code`, `: RadioProfile.vfo_equal_code`. Both are self-documented: *"Legacy alias — prefers `swap_main_sub_code` for dual-RX rigs. Deprecated: use `swap_ab_code` or `swap_main_sub_code` directly (issue #710)."*
**Consumers:** none in production. `git grep -n -w "vfo_swap_code\|vfo_equal_code"` over `src/ tests/ frontend/src/ docs/ rigs/` → 2 hits in `src/` (the two `def` lines) and 9 in `tests/` (`test_rig_ic7300.py`, `test_rig_ic7610.py`, `test_rig_loader.py` ×6, and `test_web_server_coverage.py:3791` which *writes* `radio.profile.vfo_swap_code = None` on a mock — a write to a read-only property alias that no production reader consults). *Observation.*
**Written / read:** read 0 times in `src/`; read 8 times in `tests/`, written once in `tests/`. Search was literal and word-bounded.
**Guards checked:** dynamic access — no `getattr(profile, "vfo_swap_code"…)` anywhere (`git grep "getattr(profile"` reviewed; the profile `getattr` sites in `rigctld/handler.py` and `runtime/_civ_rx.py` name `vfo_scheme`, `vfo_readback`, `supports_cmd29`). Out-of-repo — zero hits in the Pro checkout. Public API — `RadioProfile` is reachable via `rigplane.profiles`, so these two properties are technically importable; they are not named in `docs/api/public-api-surface.md`. Tests-only — yes, and named above. ⚠️ The public-API guard is the weak one here; on strictness this could be argued to **undetermined**, but the properties carry their own deprecation notice with an issue id, which is the repo declaring intent.
**Collateral:** 8 assertions across 3 rig-profile test files (`test_rig_ic7300.py: test_vfo_swap_code`, `test_rig_ic7610.py: test_vfo_swap_code`, and four `test_rig_loader.py` assertions plus two `vfo_equal_code` ones), and the mock write in `test_web_server_coverage.py:3791` (the line reads `radio.profile.vfo_swap_code = None`).
**Depends on:** none. Independent of D1 and F1.
**Confidence:** high.
**Falsifier:** a Pro-side or downstream reader of `RadioProfile.vfo_swap_code`; or an owner position that deprecated profile aliases are kept for a full major cycle.
**Fix class:** delete.

---

### D3 — `tests/serial_stub.py` and `tests/test_web_server.py`: stubs for methods the async Radio surface no longer has

**Verdict:** dead
**Elements:** `tests/serial_stub.py: SerialMockRadio.vfo_swap`, `.vfo_exchange`, `.vfo_a_equals_b`, `.vfo_equalize` (four `async def … : return None` bodies); and `tests/test_web_server.py:477` `radio.vfo_swap = AsyncMock()` and `:482` `radio.vfo_a_equals_b = AsyncMock()`.
**Consumers:** none. No `Radio` capability protocol declares any of these four method names (`core/radio_protocol.py` declares `swap_main_sub`/`equalize_main_sub` on `DualReceiverCapable` and `swap_vfo_ab`/`equalize_vfo_ab` on `VfoSlotCapable` — checked by AST). No implementation of the async `Radio` surface in `src/` defines them — but note (archival correction) that `runtime/sync.py: IcomRadio.vfo_equalize` and `.vfo_exchange` ARE live, documented public API on the blocking wrapper (`docs/api/radio.md`; exercised by `tests/test_sync_coverage.py`); they dispatch to `swap_main_sub`/`swap_vfo_ab`/`equalize_*`, never to these stubs, and are OUT of D3's scope. Deletable here are only the four stub bodies and the two mock attributes. The AST call census found zero `radio.vfo_swap()` / `.vfo_a_equals_b()` calls anywhere. The two `AsyncMock` attributes are never asserted (`grep` of `test_web_server.py` returns only the assignment lines and the unrelated WS-intent test at :1005-1018). *Observation.*
**Written / read:** written 6 times (4 defs + 2 mock assignments), read 0 times.
**Guards checked:** dynamic access — `isinstance` checks against `runtime_checkable` protocols could in principle consult attribute presence, but none of the four names appears in any protocol, so no `isinstance` gate can depend on them (the adjacent comment at `test_web_server.py:478-479` correctly names `swap_main_sub`/`equalize_main_sub` as "the canonical dual-RX VFO methods … post-#1114"). Out-of-repo — n/a, test-tree only. Public API — n/a. Tests-only — by construction.
**Collateral:** none beyond the lines themselves.
**Depends on:** none.
**Confidence:** high.
**Falsifier:** a `hasattr`-based capability probe naming one of these four strings; I searched `src/` for `hasattr(` on VFO names and found only `equalize_main_sub`/`equalize_vfo_ab` in `runtime/profiles_runtime.py: apply_profile`.
**Fix class:** delete.

---

# Consolidations

### F1 — "which VFO primitive does this rig support, and which method implements it": one decision, seven implementations, two incompatible discriminators

**Verdict:** A — Displaced
**Rank:** diverged
**Elements** (paths relative to the repo root):

| # | Symbol | Role | Discriminator |
|---|---|---|---|
| 1 | `src/rigplane/web/handlers/control.py: ControlHandler._capabilities` | advertise the `vfo_swap`/`vfo_equalize` tags in the WS `hello` | `profile.vfo_scheme` + code-not-`None` |
| 2 | `src/rigplane/web/server.py: WebServer._projected_runtime_capabilities` | advertise the same tags over HTTP (consumed at `server.py:3014` and `:3470`) | `profile.vfo_scheme` + code-not-`None` |
| 3 | `src/rigplane/web/handlers/control.py: ControlHandler._enqueue_rc_frequency`, case `"vfo_swap" \| "vfo_equalize"` | admit or refuse the WS command | `profile.vfo_scheme` + code-not-`None` |
| 4 | `src/rigplane/web/radio_poller.py: RadioPoller._execute`, cases `VfoSwap()` / `VfoEqualize()` | route to `swap_vfo_ab(0)` vs `swap_main_sub()` | `profile.vfo_scheme` + code-not-`None` |
| 5 | `src/rigplane/backends/yaesu_cat/poller.py: YaesuCatPoller._execute_command`, cases `VfoSwap()` / `VfoEqualize()` | route on the Yaesu backend | `swap_ab_code`/`equal_ab_code` only, **no scheme check** |
| 6 | `src/rigplane/runtime/sync.py: IcomRadio.vfo_equalize`, `.vfo_exchange` | route for the blocking wrapper | **`profile.receiver_count > 1`** |
| 7 | `src/rigplane/runtime/profiles_runtime.py: apply_profile` (the `if profile.equalize_vfo:` block) | route during profile application | **`receiver_count > 1`, then a `hasattr` ladder** |

**Consumers, per implementation:** #1 → `ControlHandler._send_hello`. #2 → `WebServer` HTTP handlers at `server.py:3014` and `:3470`. #3 → the WS `cmd` dispatcher. #4 → the Icom command queue. #5 → the Yaesu command queue (FTX-1). #6 → `rigplane.sync.IcomRadio` users. #7 → `runtime/ic705.py`, which is the only place setting `equalize_vfo=True`. Every one has consumers; this is duplication, not a fork.

**Definition site:** the primitive that *emits* is `runtime/_dual_rx_runtime.py: DualRxRuntimeMixin` (four methods) plus `backends/yaesu_cat/radio.py: YaesuCatRadio.swap_vfo_ab`/`.equalize_vfo_ab`. The *decision* has no definition site — it is inlined seven times.

**Divergence (observable vs latent — stated separately, because the distinction is the finding):**

- **Observable today, between #1 and #2.** Both strip the two tags and re-add them from the profile, but they resolve the profile differently when `radio.profile` is not a `RadioProfile`. `control.py: ControlHandler._capabilities` iterates `for model in (radio_model, self._radio_model)` — two candidates, so a radio whose own model string resolves to `KeyError` still falls through to the *configured* model and can advertise the tags. `server.py: WebServer._projected_runtime_capabilities` builds `candidates = (raw_model,) if isinstance(raw_model, str) and raw_model.strip() else (self._config.radio_model,)` — mutually exclusive, so the same radio gets no profile and the tags stay stripped. Consequence: for a radio reporting a non-resolving model string, the WS `hello` frame and the HTTP capability payload disagree about whether swap/equalize exists. *Observation, from reading both bodies; not reproduced at runtime.*
- **Latent, between the `vfo_scheme` family (#1–#4) and the `receiver_count` family (#6, #7).** `rigs/ftx1.toml` declares `receiver_count = 2` with `[vfo] scheme = "ab_shared"` — the one shipped profile where the two discriminators disagree. `#6` would call `equalize_main_sub()` on it (a method `YaesuCatRadio` does not define → `AttributeError`), while `#4` raises a clean `NotImplementedError`. **This is not reachable today:** `runtime/sync.py` imports `from .radio import IcomRadio as _AsyncIcomRadio`, so `sync.IcomRadio` can only wrap the Icom runtime, and `#7`'s block is gated on `equalize_vfo`, set only in `runtime/ic705.py` (single-RX). On the four Icom CI-V profiles, `receiver_count > 1` ⟺ `vfo_scheme == "main_sub"` (IC-705/IC-7300: 1/`ab`; IC-7610/IC-9700: 2/`main_sub`). So the divergence is real in the code and unreachable on the shipped fleet. *Observation for the profile values; inference for the unreachability, from the two import/gating facts named.*
- **Byte-level divergence with D1's builders.** Enumerated across all 9 `rigs/*.toml`: `ic705.toml` and `ic7300.toml` declare `swap_ab = [0xB0]`, `equal_ab = [0xA0]`; `ic7610.toml` and `ic9700.toml` declare `swap_main_sub = [0xB0]`, `equal_main_sub = [0xB1]`; `ftx1.toml`, `tx500.toml`, `x6100.toml`, `x6200.toml` declare **none** of the four. So the hardcoded `0xA0` in `vfo_a_equals_b` matches no dual-RX profile's equalize byte (`0xB1`), and the hardcoded `0xB0` in `vfo_swap` matches `swap_main_sub` — the very opcode `_dual_rx_runtime.py: DualRxRuntimeMixin.swap_vfo_ab`'s docstring refuses to reuse for A↔B: *"We do NOT silently fall back to `swap_main_sub_code` because on IC-7610 / IC-9700 that opcode exchanges MAIN↔SUB — a different semantic than A↔B within a single receiver."* On the four profiles declaring nothing, the mixin raises `CommandError` while the builders would happily emit a byte. *Observation.*

**The "set_vfo" map-key oddity, resolved.** Every rig that declares the key does so identically: `set_vfo = [0x07]` (`ic705.toml:485`, `ic7300.toml:913`, `ic7610.toml:1040`, `ic9700.toml:450`, `x6100.toml:378`, `x6200.toml:502`); `ftx1.toml` and `tx500.toml` declare no `[commands] set_vfo`. `_frame.py: decode_wire_tuple` on a 1-element tuple returns `(0x07, None, b"")`, so `_build_from_map` yields `build_civ_frame(to, from, 0x07, sub=None, data=b"\xa0")` — **byte-identical to the no-map branch on every rig that declares the key**, and a `KeyError` on the two that do not. So the odd key produces no wire divergence; what it does produce is a builder whose *operation* byte is still hardcoded while only its *command* byte comes from the profile — a half-migration, and the shape MOR-2007 exists to close. *Inference from reading `decode_wire_tuple`, `_build_from_map`, `build_civ_frame` and the eight TOMLs; not executed.*

**Prior rulings** (quoted, per the method):
- `src/rigplane/commands/LAYER.md`, "Forbidden patterns": *"Hardcoding rig-specific bytes inside a builder. Rig differences live in `commands/command_map.py` overrides applied via `CommandMap`."* — the charter under which D1's `data=b"\xa0"` is a defect regardless of liveness.
- `docs/CHANGELOG.md` `[Unreleased]` → `### Breaking changes`, **MOR-1986**: *"`rigplane.commands.set_vfo` takes a selector byte, not a VFO name … The builder held its own `{"A": 0x00, "B": 0x01, "MAIN": 0xD0, "SUB": 0xD1}` table — rig-specific bytes inside a builder, and a second implementation of a mapping the rig TOMLs declare as `[vfo] main_select` / `sub_select`. It now takes the byte and the profile supplies it."* This ruling was applied to `set_vfo` and **not** to its two immediate neighbours in the same file, which is exactly the residue D1 names. The resolution layer is also ruled: `vfo.py: set_vfo`'s docstring records *"`commands` may not import `profiles`, so the resolution belongs one layer up — `runtime/radio.py: CoreRadio._set_vfo_wire` does it"*, and `radio.py: CoreRadio._set_vfo_wire` implements it: *"The selector byte comes from the profile, not from a table here … A profile that declares no code for the VFO asked for raises, rather than falling back to a byte this method invented."*
- `docs/CHANGELOG.md` `[Unreleased]`, **MOR-2006** (config.py and levels.py, merged as `0aa426c2` and its predecessor): *"`cmd_map` is now a required keyword-only argument … a call that omits it raises `TypeError` … rather than silently emitting the old, sometimes-wrong bytes."* Precedent that the fix for a hardcoded-byte builder is migration or removal, declared in the CHANGELOG — not retention.
- Owner data-driven doctrine, as recorded in `commands/LAYER.md` and `docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1 Q6/Q7: radio-specific values live in profile data; code holds generic mechanism. Both D1 elements violate it; the mixin (#4's target) satisfies it.
- `docs/architecture/open-core-policy.md` §5: `commands/` is outside the Pro boundary; `DualReceiverCapable` and `VfoSlotCapable` (which own the mixin's four methods, per `core/radio_protocol.py`) are inside it and Tier-1 in `docs/api/public-api-surface.md`. **This settles the ownership question in the live side's favour.**

**In-flight:** PR #2846 (`codex/mor-2007-vfo-migration`, open, not draft, base `main`, 30 files). I read `gh pr diff 2846` directly rather than trusting the body — and the body is wrong about this: it states under "What I could not do" that the two builders are *"not touched"*, but the diff **does** change them. It:
- makes `cmd_map` required keyword-only on both and deletes the no-map branch;
- adds `@expose_command_key(lambda cmd_map: "set_vfo")` and `@require_cmd_map` to both;
- **keeps `data=b"\xa0"` / `data=b"\xb0"` hardcoded**;
- adds a docstring to `vfo_a_equals_b` asserting *"No production caller reaches this builder (or `vfo_swap` below) … out of scope for MOR-2007 (no divergence row and no owner ruling name it); left unchanged, migrated only for `cmd_map` per the module-wide contract"*;
- adds `requires-map vfo.py:vfo_a_equals_b` / `:vfo_swap` rows to `tests/command_map_parity_uncovered.txt`;
- rewrites both tests to pass `cmd_map=cmd_map`.

**Effect on the verdict: none, and it slightly raises the cost of inaction.** After #2846 the pair is still dead, still hardcodes the operation byte, and now additionally carries an `@expose_command_key` pointing at `"set_vfo"` — i.e. `BoundCommands.expect(vfo_swap)` would return the shape of a *VFO-select* reply. The migration also makes the pair break on `ftx1`/`tx500` (no `set_vfo` key → `CommandError` via `BoundCommands._refusal_for`) where at this revision they silently emit. So #2846 preserves the defect under a new contract and converts the two tests from "asserts a real byte" to "asserts the byte we chose, against an IC-7610 map". I verified `origin/codex/mor-2002-vfo-scope` (`9ddd1957`, not an ancestor of HEAD, content landed by squash — its `Q7, step 2b-vfo-scope` docstrings are present at HEAD): its diff touches neither builder.

**Required surface:** for F1, one profile-driven resolver — given a `RadioProfile`, return the declared VFO-op primitive (or `None`) — owned by `profiles/` or `runtime/`, consumed by all seven sites. `RadioProfile` already carries the four fields; what is missing is a single accessor that encodes *"scheme + declared code ⇒ which primitive"*, so that the advertise / admit / route decisions cannot disagree. Note `RadioProfile.vfo_swap_code` / `.vfo_equal_code` (D2) were an earlier, wrong attempt at exactly this — they collapse the scheme distinction rather than expressing it, which is presumably why nothing reads them. Do **not** revive them.
**Depends on:** D1 must land first, or at least not be merged into this work — the builders are the abandoned side and must not become the eighth call site of a new resolver. D2 is independent but should not be "fixed" by making the dead aliases the new shared accessor.
**Confidence:** high for the enumeration and for the #1/#2 divergence; medium for the practical severity, since the sharpest divergence (#6/#7) is unreachable on the shipped fleet.
**Falsifier:** a shared helper I missed that all seven sites already funnel through — I searched for one (`git grep "_runtime_capabilities\|_projected_runtime_capabilities\|_VFO_CAPABILITY_TAGS"` over `src/ tests/`; the only shared thing is `_runtime_capabilities`, which produces the *unfiltered* tag set that the four web sites then post-process independently). Also falsified if a profile is added where `vfo_scheme` and `receiver_count` disagree *and* reaches `sync.IcomRadio` — that would flip the latent divergence to observable and raise the rank.
**Fix class:** consolidate.
**Actionable:** yes for #1↔#2 (two web sites, one observable disagreement, no design decision). No, not yet, for the full seven-way merge: it needs the accessor's shape decided first, and `#5` (Yaesu) is legitimately different — a per-protocol backend, which the method's own allowlist guidance treats as sanctioned.

---

### F2 — the `0x07` swap/equalize frame is assembled in `runtime/`, with the command byte hardcoded there

**Verdict:** A — Displaced (ownership), with actionability deliberately separated
**Rank:** displaced
**Elements:** `src/rigplane/runtime/_dual_rx_runtime.py:47` — the module-level constant `_CMD_VFO = 0x07`, standing alone under the comment *"CI-V command byte for VFO select / equal / swap (0x07)"* — and its four uses inside `DualRxRuntimeMixin.swap_main_sub`, `.equalize_main_sub`, `.swap_vfo_ab`, `.equalize_vfo_ab`, each calling `build_civ_frame(self._radio_addr, CONTROLLER_ADDR, _CMD_VFO, data=bytes([code]))` directly. (A fifth use, in `._set_vfo_slot_impl`, is the same pattern.)
**Consumers:** the four methods have the full consumer set listed in F1; the constant has 5 readers, all inside its own module (`grep -n "_CMD_VFO" src/rigplane/runtime/_dual_rx_runtime.py`).
**Definition site:** framing belongs to `commands/` per `commands/LAYER.md` — *"CI-V command builders, parsers … Encodes/decodes wire bytes"*, and `commands/_frame.py` — *"the CI-V kernel; do not duplicate framing"*. The mixin imports `build_civ_frame` from `rigplane.commands` (legal under the layer matrix), but supplies the command byte from a runtime-local literal instead of the `CommandMap`.
**Divergence:** the *operation* byte is correctly profile-driven (`swap_ab_code` etc.); the *command* byte is not. Every other `0x07` operation in `vfo.py` takes `0x07` from the map key (`set_vfo`, `get_vfo`, `get_main_sub_band`, the dual-watch family), and PR #2846 makes that mandatory for all of them. These four methods stay outside that contract. No observable wire divergence today — all six rigs declaring `set_vfo` declare `[0x07]` — so this is a contract gap, not a bug. *Observation for the constant and the calls; inference for "no observable divergence", from the TOML enumeration.*
**Prior ruling:** `docs/plans/2026-08-29-profile-driven-command-bytes.md` §4, Steps 5..N, quoted in PR #2846's own summary: *"(b) move that module's call sites in `runtime/radio.py`, `runtime/_scope_runtime.py`, `runtime/_dual_rx_runtime.py`, `backends/_icom_serial_base.py` onto the binder"*. `_dual_rx_runtime.py` **is** named as an in-scope call-site file for the vfo module. PR #2846 does not move these four methods onto `self._commands`; I confirmed `_dual_rx_runtime.py` is not among the PR's 30 changed files.
**In-flight:** partially — the binder exists and is consumed (`runtime/radio.py:900` constructs `BoundCommands`; `commands/bound.py: BoundCommands.__getattr__` is the entry point), and `config.py`/`levels.py` call sites have moved. The `_dual_rx_runtime.py` swap/equalize call sites have not. This is the method's "migration incomplete" shape: **`BoundCommands` exists at `src/rigplane/commands/bound.py`, consumed by `runtime/radio.py: CoreRadio` for `config.py` and `levels.py` builders, not yet by `runtime/_dual_rx_runtime.py`'s four VFO-op methods.**
**Required surface:** a builder that takes the operation byte as an argument and the command byte from the map — which is `vfo.py: set_vfo` exactly as it stands post-MOR-1986. `self._commands.set_vfo(code, to_addr=self._radio_addr)` replaces each `build_civ_frame(…, _CMD_VFO, data=bytes([code]))`, and `_CMD_VFO` then has zero readers. So: **exists**. Whether the four methods should call it under that name, or whether `set_vfo` should be renamed to something honest about carrying three operations (select / swap / equalize), is a naming decision, not a missing surface.
**Depends on:** D1 (delete the abandoned pair before touching this file, so nobody is tempted to route the mixin through `vfo_swap`/`vfo_a_equals_b`, which would re-hardcode the operation byte). Not blocked by F1.
**Confidence:** high for the ownership claim; high for "the plan names this file and this PR does not touch it".
**Falsifier:** an owner ruling that `runtime/` may keep its own frame constants for the dual-RX path; or a later PR in the Steps 5..N series that moves these call sites (none exists among the 8 open PRs I listed).
**Fix class:** consolidate.
**Actionable:** yes, and cheap — five constant uses in one file, no behaviour change on any shipped profile. But it belongs to the MOR-2007 series' own sequencing, not to a separate change.

---

## Weakest link

**D1's public-API guard.** It is the single verdict most likely to be wrong, and I want to be precise about why. Everything about production liveness is solid: an AST call census (not a text grep) over both trees, a structural check of the one dynamic-resolution path in `src/`, and a literal grep of the Pro checkout. What I cannot establish is *absence of a downstream caller outside both repositories*. `rigplane.commands.__all__` is importable, `docs/api/commands.md` documents both signatures, and the package publishes to PyPI. The method says a deletion candidate failing the public-API guard becomes **undetermined**, never "delete anyway". I have graded it `dead` rather than `undetermined` on the strength of two recorded rulings — `open-core-policy.md` §5 excluding `commands/` from the boundary, and the three `[Unreleased]` CHANGELOG breaking-change entries showing the project does break these builders — but that is an *adjudication*, and a reader who weights the published-package surface more heavily than the open-core policy would land on `undetermined`.

**Check first:** whether `rigplane.commands` builders are Tier 2 or Tier 3 under `docs/api/public-api-surface.md`. The document asserts *"Every public symbol in the package belongs to exactly one tier"* and then assigns these builders to none — they appear only under "Advanced / implementation detail", which is not a tier. That unresolved gap, not anything about VFO, is what makes the guard soft. One owner sentence settles it, and it settles the same question for the other ~230 builders in the package.

Second-weakest: the `#1`/`#2` divergence in F1 is read off two function bodies and has not been reproduced. The specific input that separates them — a radio whose `model` string raises `KeyError` in `resolve_radio_profile` while `self._radio_model` / `self._config.radio_model` resolves — may be unconstructible in practice.

---

## Cleared

Named plainly, because a report that clears nothing is confirming its brief:

- **`runtime/_dual_rx_runtime.py: DualRxRuntimeMixin.swap_vfo_ab`, `.equalize_vfo_ab`, `.swap_main_sub`, `.equalize_main_sub` — healthy, and correctly located.** Profile-driven bytes, explicit refusal when a profile declares nothing, an explicit and correct refusal to substitute `swap_main_sub_code` for `swap_ab_code`, receiver pre-selection on dual-RX, and 16 production call sites across `web/`, `runtime/` and `backends/`. These implement the capability, they satisfy the data-driven doctrine, and they sit on `DualReceiverCapable` / `VfoSlotCapable` — Tier-1 protocols inside the Pro boundary. This is the live side; nothing here should move.
- **`commands/vfo.py: set_vfo`** — the MOR-1986 fix landed correctly. It takes a byte, holds no table, and resolves through the map. It is already the general form of D1's two special cases.
- **`backends/yaesu_cat/radio.py: YaesuCatRadio.swap_vfo_ab` / `.equalize_vfo_ab` and `backends/yaesu_cat/poller.py: YaesuCatPoller._execute_command`'s VFO arms** — legitimately local (verdict C). A second implementation for a different wire protocol is what the backend layer is for, and the `NotImplementedError` bodies are honest: they document that Yaesu CAT's `AB;`/`BA;` are one-way MAIN↔SUB copies, not a symmetric A↔B swap. The guard shape differs from the Icom poller's, correctly — there is no `main_sub` scheme in that backend to discriminate against.
- **`commands/bound.py: BoundCommands`** — the dynamic-access surface, examined closely because it is exactly what a literal grep would miss. It resolves only names a caller writes explicitly, rejects non-`cmd_map` builders by signature inspection (`_takes_cmd_map`), and classifies misses through one policy (`_refusal_for`). It is not a registry and creates no hidden consumers.
- **The WebSocket intent name `"vfo_swap"` is not the builder.** Every one of its ~11 `src/` occurrences, all its frontend occurrences, its `rigs/_keyboard-default.toml` keyboard action, its `docs/api/command-catalog.md` row, its `docs/internals/ui-radio-control-contract.toml` entry, and its `frontend/src/lib/local-extensions/host-api.ts` reachability are a UI command name routed by `ControlHandler._enqueue_rc_frequency` → `VfoSwap()` → `RadioPoller._execute` → the runtime mixin. It never touches `commands/vfo.py`. A name collision, worth stating once so nobody reads a grep count as evidence of liveness.
- **rigctld and the CLI do not implement this capability at all.** No hamlib `vfo_op` verb (`XCHG`, `CPY`), no `swap`/`equalize`/`exchange` token anywhere in `src/rigplane/cli/`. `backends/hamlib_models.py`'s `vfo_ops` field parses hamlib's own capability output and is unrelated. So there is no third front-end quietly growing a fourth copy — the surface is genuinely two-sided (web WS, Python API), which is why F1 is a seven-site cleanup rather than an architectural problem.
