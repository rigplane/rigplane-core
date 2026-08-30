> **Point-in-time audit snapshot (2026-08-30).** Produced by the `auditor`
> subagent (Claude Opus) following `.claude/skills/mechanism-audit/SKILL.md`.
> Tree audited: `8c8a70d4` (merged to main via #2801). Every citation in this
> document — including the file:line forms used where no symbol encloses the
> evidence — is frozen at that revision; resolve against `8c8a70d4`, not HEAD.
> This file is an archived report, not maintained documentation. Owner rulings
> recorded 2026-08-30 (see the Linear ticket package referencing this audit)
> supersede any recommendation here where they differ.

# Mechanism audit — transmit-truth path, rigplane-core

**Method:** `mechanism-audit`, read from `/Users/moroz/Projects/rigplane-core/.claude/skills/mechanism-audit/SKILL.md` (tracked in the audited repo). Followed as written, with the dispatch's override that citations are `file: Symbol` rather than `file:line`.

**Tree audited:** `8c8a70d4` (`8c8a70d435a2b94bef7bb8dce1c549dfe3798b05`), branch `codex/mechanism-audit-skill`, working tree **clean** (`git status --short --branch` reported no modifications). Read-only throughout: no writes, no git/gh writes, no test runs, no builds. All bash foreground.

**Hypothesis handling.** The dispatch named three known-ticketed items (MOR-1973, MOR-1190, MOR-1500) and asked three questions. I treated "there are exactly three transmit-truth implementations" as the thing under test, not as a premise. It does not survive: at `8c8a70d4` there are **eight** distinct derivations of "is the radio transmitting right now" reading the canonical field, plus three legacy-mirror readers. The enumeration below is a sweep (`git grep` over every reference to the canonical PTT `FieldPath`), not an impression.

**Instructions found in files.** `docs/plans/2026-08-20-transmit-authority.md` contains imperative text addressed to future implementers ("Seat the slot admission at `_set_vfo_slot_impl`, never at the `set_vfo_slot` alias"; "This row must not be read as having settled that"; "whoever classifies these must say where the admission sits"). I treated all of it as evidence about the code and acted on none of it. No text attempted to redirect an auditor.

---

## Enumeration (the sweep, so absence is evidence)

Search that established it — literal, `git grep -n 'global_("tx_state", "ptt")\|"tx_state", "ptt"\|_PTT_PATH\|_PTT_FIELD_PATH' -- src`. **Literal search only**; it would miss dynamic attribute access, and I checked for that separately where a deletion is proposed.

Canonical-field readers that answer "transmitting?" (8):

| # | Site | Checks applied |
|---|---|---|
| 1 | `web/radio_poller.py: RadioPoller._current_rf_state` | FRESH ∧ `type(value) is bool` ∧ provider-gen match ∧ `max_age` present ∧ age window recomputed |
| 2 | `rigctld/handler.py: RigctldHandler._resolve_rigctld_rf_state` | all of the above **plus** finiteness/NaN, `max_age > 0`, `last_observed ≥ 0`, provider-gen type/sign |
| 3 | `web/handlers/control.py: ControlHandler._observed_rf_state` | **FRESH ∧ `value is True/False` only** |
| 4 | `rigctld/server.py: RigctldServer._derive_tx_active` | FRESH ∧ `bool(value)` |
| 5 | `web/radio_poller.py: RadioPoller._send_scheduler_requests` (inline) | FRESH ∧ `bool(value)` |
| 6 | `rigctld/handler.py: RigctldHandler._ptt_observed_after` | `last_observed_monotonic > armed_at` (causal half of #2) |
| 7 | `rigctld/handler.py`, the `t` projection near `ptt_path = FieldPath.global_(...)` | projection-with-freshness, then legacy mirror |
| 8 | `core/tx_authority.py: build_transmit_truth` | provenance-pinned; **no production consumer** |

Non-canonical derivations (3): `backends/yaesu_cat/poller.py: YaesuCatPoller._current_rf_state` / `._current_ptt_observation` (private observation cache, generation + tx-target-generation + age window); `backends/yaesu_cat/poller.py: YaesuCatPoller._poll_fast` (legacy `RadioState.ptt`); `rigctld/handler.py` `t` fallback to `RadioState.ptt`.

PTT truth stores (5): `StateStore global.tx_state.ptt` (canonical), `RadioState.ptt` (legacy mirror, live readers), `YaesuCatPoller._ptt_observation` (private, live), `StateCache.ptt` (dead), `_FallbackRigState.ptt` (dead).

---

# Deletions

### D1 — `core/tx_authority.py` engine: dead, and materially wider than MOR-1973 names
**Verdict:** dead
**Elements:** `core/tx_authority.py: TransmitAuthority` and every symbol in that module **except** `TxStateReading`, `RADIO_READBACK_SOURCES`, `TX_READ_DEADLINE_SECONDS`. Plus, as the wider part: `core/radio_protocol.py: TransmitStateReadable.read_transmit_state` and its three implementations — `runtime/radio.py: CoreRadio.read_transmit_state`, `backends/yaesu_cat/radio.py: YaesuCatRadio.read_transmit_state`, `backends/rigctld_client/radio.py: RigctldClientRadio.read_transmit_state` — and `core/tx_authority.py: build_transmit_truth` / `EMPTY_TRANSMIT_TRUTH`.
**Consumers:** tests only. `tests/test_tx_authority.py`, `tests/contracts/test_tx_authority_conformance.py`, `tests/tx_authority_fakes.py`, `tests/test_tx_policy_shipped_profiles.py`, `tests/test_ftx1_radio.py`.
**Written / read:** AST-driven sweep of every module-level constant, class, function and `self._x` in the module, cross-counted with `git grep -c -w` against `src/` and `tests/`: **72 of 75 names have zero references outside their own file in `src/`.** The three exceptions are consumed by `runtime/radio.py` (import block near `from rigplane.core.tx_authority import (`). `TransmitAuthority` is never instantiated anywhere in `src/` (`git grep -n "_tx_authority\|TransmitAuthority" -- src` returns only the class definition and two docstring mentions). The module says so itself: *"Nothing in the product consumes this module yet"*.
**Guards checked:** dynamic access — searched literally; `tests/contracts/test_tx_authority_conformance.py` probes `getattr(harness.radio, "_tx_authority", None)` and `"_tx_authority_deadline_driver"`, both of which resolve to `None` today (observation). Out-of-repo — no `local-extensions/` directory exists in this tree (observation). Public API — `core.tx_authority` is **not** exported from `src/rigplane/__init__.py`, but `TxStateReading` is named in the public `core/radio_protocol.py` docstrings, so the three live symbols are public-adjacent. Tests-only — yes, and the conformance suite is substantial.
**Collateral:** the whole `tests/contracts/test_tx_authority_conformance.py` suite and `tests/tx_authority_fakes.py` would have to go with a deletion.
**Depends on:** none.
**Confidence:** high (that it is unwired); medium (that MOR-1973 already covers the backend read primitives and `build_transmit_truth`).
**Falsifier:** any `src/` construction of `TransmitAuthority`, or a call to `read_transmit_state` outside tests.
**Fix class:** none — **do not delete.** This is *migration incomplete*, not abandonment: `docs/plans/2026-08-20-transmit-authority.md` §4 rows 7–10 name this module as the target the seats migrate onto. The report-worthy part is the scope correction: MOR-1973 as quoted to me names "the unwired `TransmitAuthority`"; the unwired surface is bigger — a capability protocol row and three backend read primitives that exist solely to feed it, plus `build_transmit_truth`, all production-dead at HEAD.

---

### D2 — `TxPolicy.refused_during_tx`: shipped profile data with zero readers
**Verdict:** dead (data side live, reader side absent)
**Elements:** `profiles/__init__.py: TxPolicy.refused_during_tx`; parser `profiles/rig_loader.py: _parse_tx_policy`. Values shipped in eight profiles: `rigs/ftx1.toml:946` (`refused_during_tx = ["mode", "vfo-topology"]`), and `refused_during_tx = []` in `ic7300.toml`, `ic7610.toml`, `ic705.toml`, `ic9700.toml`, `x6100.toml`, `x6200.toml`, `tx500.toml`.
**Consumers:** none in `src/`. Tests only: `tests/test_rig_loader.py`, `tests/test_tx_policy_shipped_profiles.py`.
**Written / read:** `git grep -n "refused_during_tx"` over the whole repo — 8 profile writes, 1 dataclass field, 5 loader/validation lines, 0 reads in `src/`, 14 test lines, 4 ADR lines. Literal search.
**Guards checked:** dynamic access — the loader builds it by literal key, no `getattr` (observation). Out-of-repo — none visible. Public API — **yes**, `TxPolicy` is exported from `rigplane.profiles`, and the field is user-authorable TOML. Tests-only — yes.
**Collateral:** `tests/test_tx_policy_shipped_profiles.py` asserts the shipped values.
**Depends on:** D1 / MOR-1973 — the ADR's INV-10 (`§6`) is the intended reader ("family ∈ `refused_during_tx` ∧ the radio refused ∧ `TransmitTruth` reads TX").
**Confidence:** high.
**Falsifier:** any `src/` read of `.refused_during_tx`.
**Fix class:** none until MOR-1973 lands. Report as staged data, not as deletable.

---

### D3 — `_FallbackRigState`'s PTT members: dead in a live class
**Verdict:** dead
**Elements:** `rigctld/handler.py: _FallbackRigState.update_ptt`, and the `ptt` / `ptt_ts` fields on that dataclass (`handler.py:409` — the field line reads `ptt_ts: float = 0.0`, directly under `ptt: bool = False`).
**Consumers:** none. The class itself is live (`self._cache = _FallbackRigState()`), but only `data_mode`, `update_mode` and `update_data_mode` are touched by `RigctldHandler`, and only `update_s_meter`/`update_rf_power`/`update_swr` by `rigctld/routing.py: YaesuRouting`.
**Written / read:** `git grep -n -w "update_ptt" -- src tests` → 2 definitions (`core/_state_cache.py`, `rigctld/handler.py`), 1 call site (`runtime/_civ_rx.py`, targeting the *other* class), 0 calls on `_FallbackRigState`. `self._cache` appears 5 times in `handler.py`, none PTT-related.
**Guards checked:** dynamic access — **relevant here.** `_FallbackRigState.is_fresh` reads `getattr(self, f"{field}_ts", 0.0)`, so `ptt_ts` is reachable by string. But `_FallbackRigState.is_fresh` itself has **zero callers** anywhere (`git grep -n -w "is_fresh" -- src tests`: the hits are all on `StateCache.is_fresh`). Out-of-repo — `_FallbackRigState` is module-private. Public API — no. Tests-only — no test touches it either.
**Collateral:** none.
**Depends on:** none. Independent, cheap.
**Confidence:** high — and independently corroborated by a test docstring already in the tree: `tests/test_tx_authority_characterisation.py: TestPin6PttPollFallsBackToTheMirror` states *"That object does define `update_ptt`, but nothing in `src/` calls it — its `ptt` is dead."*
**Falsifier:** a caller of `_FallbackRigState.is_fresh("ptt", …)` or of `update_ptt` on that class.
**Fix class:** delete.

---

### D4 — `StateCache.ptt` / `ptt_ts`: written from the CI-V pump, read by nothing in production
**Verdict:** dead
**Elements:** `core/_state_cache.py: StateCache.update_ptt`, fields `ptt` / `ptt_ts`; the `case "ptt":` arm inside `StateCache.is_fresh`; the `"ptt"` / `"ptt_age"` entries in `StateCache.to_dict`. Writer: `runtime/_civ_rx.py`, the line `host._state_cache.update_ptt(bool(frame.data[0]))`.
**Consumers:** production — none. `StateCache.to_dict` has **zero callers** in `src/` or `tests/` (`git grep -n "\.to_dict()"` — every hit belongs to a different class). `StateCache.is_fresh` is called with `"rf_power"`, `"data_mode"`, `"freq"`, `"mode"` only; never `"ptt"` in `src/`.
**Written / read:** 1 production write, 0 production reads. Tests: `tests/test_state_cache.py` (5 `update_ptt`, 2 `is_fresh("ptt", …)`), `tests/test_radio.py`, `tests/serial_stub.py`, `tests/integration/test_rigctld_audio_pipeline.py`.
**Guards checked:** dynamic access — `is_fresh` takes a `CacheField` literal union and `match`es it; no string-built access to `.ptt` (observation). Out-of-repo — `StateCache` is re-exported from `rigplane.rigctld.state_cache` and typed on `core/radio_protocol.py: StateCacheCapable`, so it **is** public surface. Public API — yes. Tests-only — yes, and `tests/test_state_cache.py` would lose assertions.
**Collateral:** ~7 test assertions; `CacheField`'s `"ptt"` member.
**Depends on:** none.
**Confidence:** high. Also recorded in `docs/plans/2026-08-20-transmit-authority.md` §1.4 ("Dead truth: `StateCache.ptt/ptt_ts` … zero readers").
**Falsifier:** an out-of-repo consumer of `StateCacheCapable.state_cache.ptt` — which the open-core layout cannot rule out from here. **Marked unknown**; this is why the verdict is "dead" but the fix class is hedged.
**Fix class:** delete the *writer* (the `_civ_rx` line) safely; deleting the public field needs an owner call.

---

### D5 — CW auto-tune's interlock check: a guard that cannot refuse
**Verdict:** dead (unreachable branch)
**Elements:** `web/handlers/control.py: ControlHandler._cw_auto_tune` — the `evaluate_tx_interlock(command, rf_state=self._observed_rf_state())` call and the `if not decision.allowed: raise CommandError(...)` under it.
**Consumers:** the branch has none — `decision.allowed` is constantly `True` here.
**Written / read:** derivation (inference, from reading the policy tables, not from execution): the command is `SetFreq`. `runtime/tx_interlock.py: classify_tx_interlock` matches `_ALWAYS_PASS_TYPES` (`PttOff`, `ScanStop`), `SetPowerstat`, `SetTunerStatus`, `_HARD_BLOCK_TYPES`, `_DEFER_TYPES` — `SetFreq` is in none of them since MOR-1940 removed FREQUENCY from `_DEFER_TYPES` — so it falls to `TX_SAFE`. The call site passes no `disposition_overrides`, so `_effective_tx_interlock_disposition` returns `TX_SAFE` unchanged, and `evaluate_tx_interlock` returns `allowed=True` before ever looking at `rf_state`.
**Guards checked:** dynamic access — n/a. Out-of-repo — n/a. Public API — no. Tests-only — no test exercises the raise (searched `tests/` for `_cw_auto_tune` refusal assertions; none).
**Collateral:** none.
**Depends on:** none, but the ADR §4 row 9 already schedules this seat for deletion, so a fixer should coordinate.
**Confidence:** high.
**Falsifier:** a profile override reaching this call site, or `FREQUENCY` returning to `_DEFER_TYPES`.
**Fix class:** delete.

---

# Consolidations

### F1 — RF truth for the tuner-engage gate: the loosest of three resolvers guards the one write that throws a relay
**Verdict:** A — displaced
**Rank:** **diverged** (copies answer the same input differently, and the divergence is on the fail-open side)
**Elements:**
- `web/handlers/control.py: ControlHandler._observed_rf_state` — the loose copy
- `web/radio_poller.py: RadioPoller._current_rf_state` — the strict copy
- `rigctld/handler.py: RigctldHandler._resolve_rigctld_rf_state` — the strictest copy

**Consumers:** `_observed_rf_state` — `ControlHandler._ro_set_tuner_status` and `ControlHandler._cw_auto_tune` (the latter is D5, inert). `_current_rf_state` — `RadioPoller._enforce_tx_interlock`, `._stage_tx_interlocked_entries`, `.teardown_unkey_permitted`. `_resolve_rigctld_rf_state` — the rigctld executor pre-gate, `_defer_write_gate`, `_run_key_down_backstop`.
**Definition site:** each is a private method on its own client class. There is no shared primitive; `runtime/tx_interlock.py` owns only the *decision* (`evaluate_tx_interlock`), never the *truth*.
**Divergence (observation, from reading all three bodies):** `_observed_rf_state` tests `ptt.freshness is FreshnessState.FRESH and ptt.value is False/True` and nothing else. It does **not** compare `field.provider_generation` against the store's, and it does **not** recompute the age against `field.max_age`. Both other resolvers do both. This matters because `core/state_store.py` states in its own class docstring: *"Freshness decay is **not intrinsic** (MOR-432). The store never ages fields on its own"* — decay happens only when `StateFreshnessService` calls `mark_stale_due`. So between a provider-generation turnover (reconnect) and the next freshness tick, `_observed_rf_state` returns `RX` from the *previous connection's* observation where the other two return `UNKNOWN`.
**Why this is the sharp end (observation):** `_ro_set_tuner_status` reaches `await radio.set_tuner_status(value)` **directly on the radio** when the backend advertises `CAP_TUNER`, bypassing the command queue and therefore bypassing `RadioPoller._enforce_tx_interlock` entirely. On that path the loose resolver is the *only* RF gate on a `TUNER_ENGAGE` write — the family `core/tx_interlock_contract.py` classifies BLOCK.
**Adjudication — why displacement, not deliberate:** the competing explanation I tested is *"the tuner seat is a cheap pre-check and the real gate is downstream"*. It is rejected on two observations: (a) the direct-call branch has no downstream gate at all; (b) the docstring on `_observed_rf_state` claims *"fresh, strictly boolean PTT evidence"*, which asserts freshness the code does not establish — a client that believed it had the shared semantics, not one that chose weaker ones. The second explanation, *"nothing shared existed to call"*, is the correct one and is what makes this verdict A rather than C: the code is movable essentially as-is.
**Prior ruling:** none sanctioning it. The arrangement is *documented* as a defect: `docs/plans/2026-08-20-transmit-authority.md` §1.3 (dated 2026-08-20, re-anchored 2026-08-21) lists "RF-truth resolvers — six spellings of one question" and names `control.py`'s as *"FRESH ∧ bool only — gates the tuner"*; §1.2 calls it *"a fourth, independently wired gate call, on the loosest resolver"*. A design document naming a defect is not a ruling that the defect is correct.
**In-flight:** ADR §4 row 9 schedules deletion of `_observed_rf_state` together with the tuner seat, once the backend admission lands. Not started — `TransmitAuthority` has zero consumers (D1).
**Required surface:** one generation-and-age-bound resolver over `StateSnapshot` in a layer at or below `runtime/` that `web/`, `rigctld/` and `backends/` can all import. `core/tx_authority.py: build_transmit_truth` is the closest existing candidate but is provenance-pinned rather than freshness-pinned and answers a different question (display truth, explicitly *"never used for a hazard decision"*).
**Depends on:** none to fix in place; blocked-by-design on MOR-1973 if the fix is to be the final shape.
**Confidence:** high on the divergence; **medium** on operational reachability — I did not measure how wide the window between a generation turnover and the next `mark_stale_due` tick is in a running web server.
**Falsifier:** evidence that `command_state_store`'s freshness service ticks synchronously with `begin_provider_generation`, which would close the window.
**Fix class:** consolidate
**Actionable:** yes — hardening `_observed_rf_state` to match `RadioPoller._current_rf_state` is a few lines and independent of MOR-1973.
**Expensive contract:** **no.** Internal gate; no wire format, public API, profile schema or persisted data changes. (It can *cause* a wire-visible refusal, but the contract itself is internal.)

---

### F2 — The web transmit-safety apparatus lives inside `RadioPoller`, which two shipping backends never start
**Verdict:** A — displaced (with a genuine coverage hole as the consequence)
**Rank:** **displacement forcing reimplementation** — one bad location, one client that copied it, one that did not
**Elements:**
- `web/web_startup.py` — the three-way branch: `if isinstance(server._radio, ObservationPollable)` → backend's own poller; `elif isinstance(server._radio, StatePollable)` → legacy poller; `else` → `RadioPoller`. Mutually exclusive (observation).
- `web/radio_poller.py: RadioPoller` — hosts `_enforce_tx_interlock`, `_current_rf_state`, `_stage_tx_interlocked_entries`, `_deferred_tx_lane`, `_arm_max_key_down` / `_cancel_max_key_down` / `_on_max_key_down`, `_refuse_key_from_gone_session`, `teardown_unkey_permitted`, `_last_keyer`, `_managed_tx`
- `backends/yaesu_cat/poller.py: YaesuCatPoller` — the copy: `_current_rf_state`, `_drain_commands`, `_execute_command`, `_deferred_tx_lane`, `_deferred_tx_entry`, `_emit_deferred_entry_held`, `_deferred_release_is_live`
- `backends/rigctld_client/radio.py: RigctldClientObservationPoller._drain_commands` / `._execute_command` — **no copy at all**

**Consumers, per implementation:** `RadioPoller` — Icom CI-V radios only. `YaesuCatPoller` — FTX-1 (a live bench radio). `RigctldClientObservationPoller` — every hamlib-provider radio behind the Web UI.
**Divergence (observation):**
- `git grep "tx_interlock\|RfState\|evaluate_tx" -- src/rigplane/backends/rigctld_client/` returns **zero matches**. `RigctldClientObservationPoller._execute_command` contains `case PttOn(): await self._radio.set_ptt(True)` with no gate above it. Web-queued writes to a hamlib-provider radio pass no TX interlock at all.
- `refuse_key_without_owner` / `bind_managed_tx` (`runtime/managed_tx_ingress.py`) appear in `web/radio_poller.py`, `web/tx_safety_view.py`, `rigctld/handler.py` — and in neither backend poller. (This half is MOR-1190.)
- `RadioPoller._arm_max_key_down` (the 180 s unmanaged key-down bound) exists only on the Icom branch. A web key on FTX-1 or on a hamlib-provider radio has **no** bound.
- `web/handlers/control.py: ControlHandler._release_ptt_on_teardown` consults `getattr(self._server, "_radio_poller", None)` for `teardown_unkey_permitted`; on both other branches that attribute is `None`, so the MOR-1878 keyer-identity check is skipped and the teardown unkey is unconditional.

**Adjudication — why displacement, not deliberate:** the competing explanation is *"each backend legitimately owns its own drain, so per-backend gates are correct"*. Rejected on the evidence that the queue being drained is **the Web UI's own** `CommandQueue`, handed across by `web/web_startup.py` via `create_observation_poller(command_queue=server._command_queue)`; the Yaesu poller's own docstring says *"Commands come from the web UI CommandQueue"*. A web-owned policy is being enforced (or not) in three places chosen by backend identity, not by policy. The import graph is legal throughout — which is exactly the blind spot the method describes.
**Prior ruling:** none sanctioning it. `docs/plans/2026-08-20-transmit-authority.md` §1.2, last row, names this arrangement and calls it *"load-bearing for placement (§3.2): any gate that wraps the radio object from outside never sees the web write path of two shipping backends, one of them a bench radio."* §3.2 rules the correct seat is the **backend write method**, not any poller — so the Yaesu copy is in the wrong place too, by the same design.
**In-flight:** target is `core/tx_authority.py` (D1), unconsumed; rows 7–10 not started.
**Required surface:** an admission at the last typed hop before transport on every backend, per ADR §3.2 — or, as a cheap interim that needs no design decision, calling the existing `runtime/tx_interlock.py: evaluate_tx_interlock` from `RigctldClientObservationPoller._execute_command` the way `YaesuCatPoller._execute_command` already does.
**Depends on:** the interim fix depends on nothing; the final shape depends on MOR-1973.
**Confidence:** high.
**Falsifier:** a gate on the rigctld-client write path that my literal search missed — e.g. one applied inside `RigctldClientRadio.set_ptt`. I read that method's neighbourhood and saw none, but I did not read the full 800-line class.
**Fix class:** consolidate (interim) / design (final)
**Actionable:** yes for the rigctld-client gap; no for the full consolidation until the ownership question in MOR-1973 is settled.
**Expensive contract:** **yes** — closing the rigctld-client gap changes observable behaviour on the wire for hamlib-provider radios (writes that succeed today would start being refused during TX). Not a schema change, but a behavioural one that a downstream client can see.

---

### F3 — Same refusal, two wire payloads: the FTX-1 web client cannot tell an interlock refusal from a generic failure
**Verdict:** A — displaced
**Rank:** **diverged**
**Elements:**
- `web/radio_poller.py: RadioPoller._enforce_tx_interlock` raises `runtime/tx_interlock.py: TxInterlockRefusal` carrying `reason_code` ∈ {`radio_transmitting`, `rf_state_unknown`}; `RadioPoller._mark_queued_command_failed` turns it into `details={"blockedBy": "tx_interlock", "reason": …}`; `web/server.py` whitelists exactly that shape onto the websocket `failed` envelope.
- `backends/yaesu_cat/poller.py: YaesuCatPoller._drain_commands` and `._execute_command` raise a bare `CommandError(decision.reason)`; `YaesuCatPoller._mark_queued_command_failed` builds `params` with `message` / `timed_out` / `session_id` / `source` and **no `details` key at all**.

**Consumers:** the web frontend. The whitelist comment in `web/server.py` states the codes *"map onto the existing i18n keys (core.rxTx.blocked.rfStateUnknown / radioTransmitting)"*.
**Definition site:** `TxInterlockRefusal` is defined in the shared `runtime/tx_interlock.py`, reachable by both — so this is not a "nothing to call" case.
**Divergence (observation):** identical policy decision, identical `decision.reason` string, two different envelopes. On Icom the operator gets a localized "radio is transmitting"; on FTX-1 the same refusal arrives as an untyped failure. Note the asymmetry is *partial*: `YaesuCatPoller._emit_deferred_entry_held` **does** emit the matching `{"heldBy": "tx_interlock", "reason": "tx_active", "expiresAt": …}` details, so the *held* path is consistent and only the *refused* path is not. That partial match is what makes "deliberate divergence" implausible.
**Adjudication:** competing explanation — *"Yaesu deliberately reports less"*. Rejected: nothing documents such a choice, `TxInterlockRefusal` is in the shared module precisely so any seat can raise it (its docstring: *"Lives beside the policy it reports rather than at a seat that raises it … every enforcement seat may need them"*), and the held-path envelope already matches.
**Prior ruling:** none found. MOR-1879 (`b3ab76b1`) landed the typed envelope on the web seat only.
**In-flight:** none.
**Required surface:** exists — raise `TxInterlockRefusal` in `YaesuCatPoller` and mirror the `details` branch in its `_mark_queued_command_failed`.
**Depends on:** none.
**Confidence:** high.
**Falsifier:** a Yaesu-path test asserting the untyped envelope as intended behaviour.
**Fix class:** consolidate
**Actionable:** yes — small and self-contained.
**Expensive contract:** **yes** — the websocket `failed` envelope is a wire contract with a whitelist in `web/server.py` and matching frontend i18n keys.

---

### F4 — Two unmanaged key-down backstops with different cancellation rules
**Verdict:** A — displaced
**Rank:** **diverged**
**Elements:**
- `web/radio_poller.py: RadioPoller._arm_max_key_down` / `._cancel_max_key_down` / `._on_max_key_down` (MOR-1220)
- `rigctld/handler.py: RigctldHandler._arm_key_down_backstop` / `._run_key_down_backstop` / `._void_key_down_backstop` / `.stop_key_down_backstop` / `._ptt_observed_after` (MOR-1904)
- `core/tx_safety.py: TxSafetySupervisor.tick` (the managed third, for radios that arm a supervisor)
- `core/tx_authority.py: TransmitAuthority._commit` / `.poll` (a fourth, unwired — see D1)

**Consumers:** the web command drain; the rigctld PTT route; `runtime/managed_radio_runtime.py`'s tick loop; nothing.
**Definition site:** only the *duration* is shared — `core/tx_safety.py: BACKEND_MAX_KEY_DOWN_SECONDS` (180.0), imported by both. The *mechanism* is written twice.
**Divergence (observation, from both bodies):** the web one is a `loop.call_later` timer that fires with **no RF check whatever** and enqueues `PttOff`; it is cancelled by its own unkey, by a re-key, and by the poller stopping. The rigctld one is a task ticking every 0.25 s (`_KEY_DOWN_BACKSTOP_TICK_SECONDS`) that additionally **vetoes on an observed causal return to RX** and, at server stop, cancels with a warning rather than firing. So the same 180 s bound behaves differently depending on which seat keyed. This is documented as a user-facing asymmetry in `docs/guide/cli.md` — the rigctld bound is *"cancelled by any `rigctld` PTT write of either polarity, by the server stopping, **and** — unlike the poller's — by an observed return to receive after that key."*
**Adjudication:** competing explanation — *"different seats have different evidence available, so different vetoes are correct"*. Partially credited and then rejected: `RigctldHandler._run_key_down_backstop`'s own docstring argues its veto is *"Still strictly safer than the accepted precedent: MOR-1220 fires with no RF check whatever, so this adds a veto and removes none"* — i.e. the author knew the two differ and judged one strictly better. A strictly-better second implementation of the same mechanism is duplication whose weaker half was left in place, not a deliberate specialization. Both seats read the same `StateStore`; the evidence available is the same.
**Prior ruling:** `docs/guide/cli.md` *describes* the divergence to operators. Describing is not sanctioning; no ADR row rules that the two must stay separate. ADR §1.5 lists them as *"three bounding mechanisms … MOR-1904 added a fourth"*, in a section titled "the corrected inventory" — i.e. as inventory of a problem.
**In-flight:** none for these two. `TransmitAuthority`'s deadline is the intended eventual single owner (D1).
**Required surface:** a shared bound object taking (arm instant, clock, RF resolver, unkey callable) at or below `runtime/`. Does not exist.
**Depends on:** F1 — a shared bound needs a shared RF resolver first, or it will inherit whichever copy it is handed.
**Confidence:** high.
**Falsifier:** an owner ruling that the RX veto must not apply to web-issued keys.
**Fix class:** consolidate
**Actionable:** yes, but rank it after F1.
**Expensive contract:** **no** — behaviour change only; nothing crosses a schema or wire boundary. (It would change when a stuck key is released, which is operator-visible.)

---

### F5 — `tx_active` for the acquisition scheduler: two byte-identical copies
**Verdict:** A — displaced
**Rank:** **parallel** (identical behaviour; maintenance cost only)
**Elements:**
- `rigctld/server.py: RigctldServer._derive_tx_active`
- `web/radio_poller.py: RadioPoller._send_scheduler_requests` — the inline derivation, `tx_active = ptt_field.freshness is FreshnessState.FRESH and bool(ptt_field.value)` with a `KeyError → False` fallback

**Consumers:** `core/acquisition_scheduler.py: AcquisitionScheduler.note_tx_active` / `.due_requests` — the `tx_only` cadence gate.
**Definition site:** neither. The consumer is shared; the derivation is not.
**Divergence:** **none.** The rigctld docstring says so outright: *"Derived identically to the web poller (MOR-1525 / PR #2438)."*
**Adjudication:** competing explanation — *"a two-line predicate is not worth sharing"*. Genuinely arguable, and I credit it: this is the least urgent finding in the list. What tips it is that MOR-1525 records this exact predicate having been *wrong once already* (it read `RadioState.ptt` and desynced, producing a visible SWR flap), and it now exists in two places that must be fixed together. The scheduler layer (`core/acquisition_scheduler.py`) is below both clients and could host it.
**Prior ruling:** none. `AcquisitionScheduler.note_tx_active`'s docstring argues the two *cannot disagree* — which is a claim about today's identical copies, not a mechanism preventing drift.
**In-flight:** none.
**Required surface:** a `tx_active_from_snapshot(snapshot)` helper on the scheduler module or on `core/state_store.py`.
**Depends on:** none. Independent of F1 — this gate is fail-closed-to-`False` and is a polling-cadence decision, not a safety decision, so it does not need F1's strictness.
**Confidence:** high.
**Falsifier:** a deliberate future divergence between standalone-rigctld and web cadence.
**Fix class:** consolidate
**Actionable:** yes, low priority.
**Expensive contract:** **no.**

---

### F6 — Yaesu's legacy meter gate still reads the mirror that MOR-1525 removed on the web side
**Verdict:** undetermined (verdict A if the branch is reachable; dead branch if not)
**Rank:** diverged
**Elements:**
- `backends/yaesu_cat/poller.py: YaesuCatPoller._poll_fast` — the `if state.ptt and "meters" in self._caps:` branch, reading `self._radio.radio_state`
- `backends/yaesu_cat/poller.py: YaesuCatPoller._emit_fast_observations` — the modern sibling, which uses `_current_ptt_observation()`
- `web/radio_poller.py: RadioPoller._send_scheduler_requests` — the same capability on the Icom side, moved *off* the mirror by MOR-1525

**Consumers:** `_poll_fast`'s legacy branch runs only when `self._observation_callback is None`, i.e. when the poller was built by `YaesuCatRadio.create_state_poller`. `web/web_startup.py` prefers `ObservationPollable`, and `YaesuCatRadio` implements both, so the web path never reaches it (observation). `create_state_poller` is on the public `core/radio_protocol.py: StatePollable` protocol, so a downstream caller can reach it.
**Divergence (observation):** the web side's comment records why the mirror was abandoned — *"The mirror was live-proven to desync from the canonical fact: after a TX it stayed True while the StateStore's own observation had already flipped False in RX … operator-visible as the SWR readout flapping 0<->1, MOR-1525."* The Yaesu legacy branch still gates on exactly that mirror.
**Adjudication:** I am not calling this a live defect, because I could not establish reachability in the shipped web startup. It is a *vestigial fork with the known-bad half retained*: two implementations of "poll TX meters while transmitting", one fixed, one not.
**Prior ruling:** none. Not in ADR §1.3's six-resolver list.
**In-flight:** none.
**Required surface:** none new — `_current_ptt_observation()` is already on the same class.
**Depends on:** none.
**Confidence:** medium.
**Falsifier:** a production caller of `YaesuCatRadio.create_state_poller`, which would make it a live defect; or a decision to delete the legacy branch, which would make it a deletion.
**Fix class:** delete the legacy branch, or repoint it at `_current_ptt_observation()`
**Actionable:** yes, cheaply, either way.
**Expensive contract:** **no.**

---

### F7 — `[tx_interlock].disposition_overrides`: a documented profile key honoured by one of four enforcement seats
**Verdict:** B — gap (the shared policy accepts overrides; three of four seats never pass them)
**Rank:** displacement forcing reimplementation
**Elements:**
- `profiles/rig_loader.py: _parse_tx_interlock_disposition_overrides` — parses and validates
- `profiles/__init__.py: RadioProfile.tx_interlock_disposition_overrides` — carries them
- `runtime/tx_interlock.py: evaluate_tx_interlock` / `_effective_tx_interlock_disposition` — accept them
- **Passes them:** `backends/yaesu_cat/poller.py: YaesuCatPoller._tx_interlock_disposition_overrides`, at three call sites
- **Does not pass them:** `web/radio_poller.py: RadioPoller._enforce_tx_interlock` and `._stage_tx_interlocked_entries`; `web/handlers/control.py: ControlHandler._ro_set_tuner_status` and `._cw_auto_tune`; `rigctld/handler.py: _classify_rigctld_tx_intent` (calls `classify_tx_interlock`, the un-overridden form); `backends/rigctld_client/` (no interlock at all — F2)

**Consumers:** none in production data. `git grep -rn "tx_interlock" rigs/` returns only `rigs/_schema.md`; no shipped profile sets it. The ADR states the same at §1.1: *"zero shipped TOMLs use it"*.
**Divergence (observation):** if any profile ever set `disposition_overrides = { "frequency" = "defer" }`, an FTX-1 would honour it and every Icom, rigctld and hamlib-provider path would silently ignore it.
**Adjudication:** not dead — the loader, the validator, the schema doc and one reader all exist. Not a duplicate — one honest reader. It is a **gap**: the shared policy exposes a parameter the shared *seat* would have to pass, and there is no shared seat. `rigs/_schema.md` documents it as a general profile capability with no hint of the restriction, which under this repo's own prose rule (`CLAUDE.md`: *"could this be false without any test failing?"*) is a claim stated wider than the code.
**Prior ruling:** none limiting it to Yaesu.
**In-flight:** the ADR's target seat (backend write method) would give a single reader; not started.
**Required surface:** either a single seat that always passes profile overrides, or an explicit narrowing of the schema doc to say which backends honour the key.
**Depends on:** F2 — the same missing single seat.
**Confidence:** high.
**Falsifier:** a shipped profile using the key on a non-Yaesu rig, which would turn this into a live defect rather than a latent one.
**Fix class:** design
**Actionable:** partly — the doc narrowing is actionable now; the seat is not.
**Expensive contract:** **yes** — `[tx_interlock]` is a documented, user-authorable profile-schema key (`rigs/_schema.md`, "`[tx_interlock]` — Profile Tightening Metadata").

---

## Weakest link

**F1's operational reachability.** I established the *code* divergence with certainty: `ControlHandler._observed_rf_state` checks neither provider generation nor the age window, and both peer resolvers check both. What I did **not** establish is how wide the window is in a running web server — that is, how long a superseded-generation or TTL-expired `global.tx_state.ptt` field can remain flagged `FRESH` before `StateFreshnessService` decays it. If that service ticks synchronously with `begin_provider_generation`, F1 collapses from "fail-open gate on a relay-throwing write" to "redundant checks the other two carry for defence in depth". **Check first:** the tick cadence and trigger of `StateFreshnessService.run` in `core/acquisition_scheduler.py`, and whether `web/server.py` drives it from the same task that advances the provider generation.

Secondary: F6's reachability. I inferred that `web/web_startup.py`'s `elif` can never fire for `YaesuCatRadio` because the class satisfies both protocols; I did not verify the runtime `isinstance` result against `ObservationPollable` under Python 3.11's protocol semantics, which this repo has been bitten by before (gh-102433).

---

## Cleared — examined and healthy, by name

- **`runtime/tx_interlock.py`** — the disposition policy itself. One implementation, four consumers across `web/`, `rigctld/` and `backends/`, correct layer (below every client, imports only `core/`). `classify_tx_interlock`, `evaluate_tx_interlock`, `get_tx_interlock_command_family_metadata`, `DeferredTxCommandLane` are **already shared**. The premise "the interlock is duplicated" is wrong at the policy level; only the truth input and the driver loops are duplicated.
- **`core/tx_interlock_contract.py`** — the 4×17 disposition/family table. Zero dead names in the AST sweep. Single source, no `rigplane` imports, correctly foundational.
- **`runtime/managed_tx_ingress.py`** — `resolve_supervisor`, `bind_managed_tx`, `refuse_key_without_owner`. This is a consolidation that *landed*: three call sites (`web/radio_poller.py`, `web/tx_safety_view.py`, `rigctld/handler.py`) share one two-step supervisor read, correctly placed in `runtime/` for exactly the reason its docstring gives. Clean. One documentation nit, not a mechanism finding: the module docstring says *"the CLI/SDK already do too"*, but `git grep "bind_managed_tx\|refuse_key_without_owner" -- src/rigplane/cli src/rigplane/runtime/sync.py` returns zero — they call `core/radio_protocol.py: ManagedTxApi.bind` directly with a process-lifetime `TxOwner`, which `_stable_owner` would refuse by design.
- **`web/tx_safety_view.py`** — `build_tx_safety_payload`. Pure read-only projection of the supervisor snapshot; asks the key path's own predicate rather than re-deriving eligibility. No second copy of anything. Correctly located in `web/` (it shapes a web document).
- **`commands/ptt.py`** — `ptt_on` / `ptt_off`. Exactly one CI-V PTT frame builder pair, one consumer (`runtime/radio.py: CoreRadio.set_ptt`). No second frame construction anywhere in `src/` outside `commands/`. Yaesu's CAT encoding is separate and is sanctioned per-protocol duplication.
- **`core/tx_safety.py: TxSafetySupervisor`** — the managed lease/watchdog/durable-OFF reducer. One implementation, driven by `runtime/managed_radio_runtime.py`, projected by `web/tx_safety_view.py`, bound through `ManagedTxApi` / `PrivilegedTxApi`. Its unreferenced `TxReleaseReason` members (`APP_SHUTDOWN`, `AUDIO_FAILED`, `CAPABILITY_LOST`, `EXTERNAL_CAT_PREEMPTED`, `PERMIT_LOST`, `RADIO_TRANSPORT_LOST`) are **not** dead code: they are a closed vocabulary, exhaustively enumerated by design, and several are consumed via `_SYSTEM_RELEASE_REASONS`. Explicitly cleared so a later sweep does not re-flag them.
- **`core/radio_protocol.py: ManagedTxCapable` / `ManagedTxApi` / `PrivilegedTxApi`** — the `getattr_static` + explicit-read discipline is written once and reused; `PrivilegedTxApi` has exactly one consumer (`cli/__init__.py`) and correctly declines to bind a supervisor without `force_unkey`.
- **`runtime/_civ_rx.py`** PTT observation path — `bind_ptt_observer`, `request_authoritative_ptt_read`, `_emit_authoritative_ptt`, `_ptt_read_is_current`, `write_managed_ptt`. One implementation of generation-bound authoritative PTT readback; the identity checks are `is`-based throughout, consistent with the known `==`-vs-`is` hazard in this file. No duplicate found.
- **`core/tx_target.py`** and `runtime/managed_ptt_lifecycle.py` / `managed_tx_effect_service.py` — swept for dead names; every flagged name is a module-private helper used inside its own file. No orphans.
- **`profiles/rig_loader.py: _parse_tx_policy`** and `TxPolicy.is_receiving` / `.attribution` — live, single reader (`backends/yaesu_cat/radio.py: YaesuCatRadio._interpret_ptt_token`). Only the `refused_during_tx` sibling is reader-less (D2).
