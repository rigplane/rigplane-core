> **Point-in-time audit snapshot (2026-08-30).** Produced by the `auditor`
> subagent (Claude Opus) following `.claude/skills/mechanism-audit/SKILL.md`.
> Tree audited: `8c8a70d4` (merged to main via #2801). Every citation in this
> document — including the file:line forms used where no symbol encloses the
> evidence — is frozen at that revision; resolve against `8c8a70d4`, not HEAD.
> This file is an archived report, not maintained documentation. Owner rulings
> recorded 2026-08-30 (see the Linear ticket package referencing this audit)
> supersede any recommendation here where they differ — in particular F3's
> open contract question is ruled: `receiver=0` means literal MAIN, always;
> the runtime layer is the deviant side and the mechanism is to be
> encapsulated in the runtime write path.

# Mechanism audit — command path (frontend → wire), rigplane-core

**Method:** `.claude/skills/mechanism-audit/SKILL.md`, read from the audited repository at `/Users/moroz/Projects/rigplane-core/.claude/skills/mechanism-audit/SKILL.md` (tracked; committed in `8c8a70d4`). Followed as written, with the dispatch's citation override applied (file + **symbol name** rather than `file:line`; `file:line` only where no symbol encloses the evidence).

**Tree audited:** `8c8a70d4` (`8c8a70d435a2b94bef7bb8dce1c549dfe3798b05`), branch `codex/mechanism-audit-skill`, tracking `origin/codex/mechanism-audit-skill`. `git status --short --branch` reported **clean** — no uncommitted changes, no untracked files. Read-only throughout: no writes, no git/gh writes, no test runs, no builds. All bash foreground.

**Premise handling:** the dispatch supplied four already-ticketed findings and a scope. It supplied no conclusion about what I would find, so there was no hypothesis to put under test. The one asserted fact I did test rather than accept is the CLAUDE.md claim that the CLI predates the state model and its apparent violations may be historical — see *Cleared*, item 8: I checked the CLI's command path and it does **not** reimplement radio truth; the doctrine violation I found is in `rigctld/`, not `cli/`.

**Instruction-shaped text in files:** none found. Comments and docstrings on this path are descriptive (several are *wrong*, which is reported as evidence, not obeyed). No file attempted to direct an agent.

---

## DELETIONS (independent, no design decision)

### D1 — unselected-slot polling in `web/radio_poller.py`: dead cluster behind a constant-returning guard
```
Verdict:          dead
Rank:             n/a (deletion)
```
**Elements** (all in `/Users/moroz/Projects/rigplane-core/src/rigplane/web/radio_poller.py`):
- `RadioPoller._unselected_slot_gate` — body is `_ = receiver; return False`. Guard returning a constant.
- `RadioPoller._poll_unselected_slot` — body is `_ = receiver`. No-op.
- `RadioPoller._last_user_write_ts` — instance field.
- `RadioPoller._last_unselected_poll` — instance field.
- `RadioPoller._UNSELECTED_SLOT_INTERVAL` / `RadioPoller._UNSELECTED_SLOT_DEBOUNCE` — class constants (`radio_poller.py:3945-3946`; the two lines stand immediately above `_unselected_slot_gate`).
- The `for _rx in range(self._profile.receiver_count): await self._poll_unselected_slot(_rx)` loop inside `RadioPoller._run` (`radio_poller.py:1991-1993`, guarded by `if self._acquisition_scheduler is None:`) — every iteration calls the no-op.

**Consumers:** production — none. Tests only: `tests/test_poller_poll_priority.py` (asserts the gate returns `False` and the poll is awaitable), `tests/test_selected_freq_mode.py` (≈8 calls incl. `test_set_freq_updates_last_user_write_ts`), `tests/test_radio_poller_coverage.py`, `tests/test_civ_transaction_ownership.py`, `tests/test_vfo_profile_primitives.py`.

**Written / read (observation; grep is literal):**
- `grep -rn "_last_user_write_ts" src/ tests/ frontend/src` → 9 hits in `src/` (1 init + **8 writes**, all `self._last_user_write_ts = time.monotonic()`), **0 production reads**; 8 hits in `tests/`, all reads/asserts.
- `grep -rn "_last_unselected_poll" src/ tests/` → **1 hit total** (the initialiser). Written once, read nowhere.
- `grep -n "_UNSELECTED_SLOT_INTERVAL" src/rigplane/web/radio_poller.py` → 2 hits: the definition, and a *comment* at `radio_poller.py:709`.

**Guards checked:**
- *Dynamic access* — searched **literally**. Names are private (`_`-prefixed), not in any `__all__`, not string-built, not serialised by field name. `RadioPoller` has no `__slots__` and no `getattr`-driven attribute dispatch on these names.
- *Out-of-repo* — `RadioPoller` is not exported from `rigplane.web.__init__`; the open-core Pro boundary is the `Radio` protocol + `local-extensions/`, not this class. Low risk, not zero: **unknown** whether a private tier subclasses `RadioPoller`.
- *Public API* — no.
- *Tests-only* — **yes, this is the tests-only case.** `test_set_freq_updates_last_user_write_ts` asserts a field nothing reads; `test_poller_poll_priority.py` pins that a no-op is a no-op. Deleting the code deletes those tests. Human decision.

**Collateral — stale prose that must go with it (observation):** the comment at `radio_poller.py:706-709` states the fields exist "so the unselected-slot poll subroutine can debounce around them … refreshed no more than once per `_UNSELECTED_SLOT_INTERVAL`", and the comment at `radio_poller.py:1985-1988` states the loop is "Fully gated (PTT, queue pressure, debounce, per-rx interval)". Neither is true of the code at this revision — both describe behaviour that the constant-`False` gate removed. This is the CLAUDE.md "prose is a claim" defect in its exact stated form.

**Depends on:** none.
**Confidence:** high.
**Falsifier:** a production read of `_last_user_write_ts` or `_last_unselected_poll` reached through dynamic attribute access, or a subclass in a sibling/private repo overriding `_unselected_slot_gate` to return `True`.
**Fix class:** delete.

---

### D2 — `commands/vfo.py: get_main_sub_band`: builder with no caller
```
Verdict:          dead (deletion blocked by a public-API guard → see below)
```
**Element:** `/Users/moroz/Projects/rigplane-core/src/rigplane/commands/vfo.py: get_main_sub_band` — builds CI-V `0x07 0xD2`.

**Consumers:** zero in `src/` outside `commands/`. Zero direct test calls.

**Written / read (observation):** I enumerated all 333 top-level `def`/`class` definitions across `src/rigplane/commands/*.py` (excluding `__init__.py`) with a Python AST sweep, then for each public name ran `grep -rl --include=*.py '\b<name>\b' src/ tests/` and discarded hits inside `src/rigplane/commands/`. **Exactly 1 of 333** came back with zero references outside its own layer: `get_main_sub_band`. That is the sweep result, not an impression — the other 332 all have consumers.

**Guards checked:**
- *Dynamic access* — searched literally, then found one **dynamic consumer**: `tests/test_command_map_parity.py: _graph` parses `commands/*.py` with `ast` and enumerates every `FunctionDef`, so the symbol is reached by source-parsing, not by import. It also appears as inert text in `tests/command_map_parity_divergences.txt` and `tests/command_map_parity_uncovered.txt`.
- *Out-of-repo* — plausible. `rigs/ic7610.toml` and `rigs/ic9700.toml` both declare `get_main_sub_band = [0x07, 0xD2]`, so the wire fact is data-side and would survive.
- *Public API* — **fails.** Re-exported by `commands/__init__.py` (import at `:179`, `__all__` entry at `:660`), and `commands/LAYER.md` states "Other layers import through this front door". A downstream user can `from rigplane.commands import get_main_sub_band`. Not in the top-level `rigplane/__init__.py` lazy map.
- *Tests-only* — the AST parity sweep, above.

Per the method, a candidate failing a guard becomes **undetermined**, not "delete anyway". Actionable form: remove it from `__all__` in one change, delete in a later one — or keep it and give it the runtime method `commands/LAYER.md` says every builder needs.

**Depends on:** MOR-2000 (which deletes this function's `cmd_map` branch) should land first, so the deletion is against the final shape.
**Confidence:** high on "no caller"; medium on "safe to delete" (public export).
**Falsifier:** a caller in `local-extensions/`, rigplane-pro, or any downstream import.
**Fix class:** delete (after the `__all__` decision).

---

### D3 — `RadioPoller._send_cmd` / `_load_command_map` / `_cmd_map`: unreachable for every shipped profile
```
Verdict:          undetermined (dead for all 8 shipped profiles; one non-shipped reachability path survives)
```
**Elements** (`src/rigplane/web/radio_poller.py`): `RadioPoller._load_command_map`, `RadioPoller._send_cmd`, and the `self._cmd_map` field set in `RadioPoller.__init__` (`radio_poller.py:664`).

**Consumers:** exactly **one** production call site — the `else` arm of `case SetAgc(...)` inside `RadioPoller._execute` (`radio_poller.py:2811-2813`), taken only when `CAP_AGC not in self._caps`. Plus `tests/test_mor1537_cmd29_gating.py` (6 direct calls) and `tests/test_radio_poller_coverage.py`.

**Reachability analysis (observation + one inference, labelled):**
- *Observation:* `IcomRadio.capabilities` (`runtime/radio.py`) returns `set(self._profile.capabilities)` — capability tags come straight from the rig TOML.
- *Observation:* `grep -n '"agc"' rigs/*.toml` → declared by `ic705`, `ftx1`, `ic7300`, `ic7610`, `x6200`, `ic9700`. Not declared by `x6100`, `tx500`.
- *Observation:* `grep -n "set_agc" rigs/*.toml` → a CI-V `set_agc = [0x16, 0x12]` entry exists only in `ic705`, `ic7300`, `ic7610`, `ic9700`; `ftx1` has a CAT form (dropped by `to_command_map`, which keeps CI-V only); `x6100` and `tx500` have **no** `set_agc` entry at all.
- *Inference:* therefore, on every shipped profile the `else` arm is either not taken (6 profiles have `agc`) or taken and finds an empty map (x6100, tx500 → `_send_cmd` returns `False` after a `logger.debug`). The cmd29-wrapping code inside `_send_cmd` executes for no shipped radio.
- *Divergence worth noting separately:* on x6100/tx500 a UI `set_agc` is silently discarded at debug level while the WebSocket ack already reported success. That is an honesty defect in the ack, not a mechanism duplicate.

**Guards checked:**
- *Dynamic access* — searched literally; no dynamic dispatch on these names.
- *Out-of-repo* — **fails.** `RadioPoller._runtime_profile` falls back to `IC-7610`/`IC-7300` for a radio with no `profile`, and `_radio_capabilities` returns an empty set when the backend exposes no `capabilities` set. A third-party `Radio` implementation with neither would take the `else` arm and send IC-7300 wire bytes. Not reachable via any in-tree backend.
- *Public API* — no.
- *Tests-only* — `tests/test_mor1537_cmd29_gating.py::TestSendCmdCmd29Unification` builds `RadioPoller` on `MagicMock` radios specifically to exercise this arm.

**Relation to the ticketed set — MOR-2000 is materially bigger than "232 builders":** `grep -rn "to_command_map" src/ tests/` returns **exactly one production call site in the whole tree**: `RadioPoller._load_command_map` (`radio_poller.py:1251`). Everything else is tests. So `commands/command_map.py: CommandMap`, `profiles/rig_loader.py: RigConfig.to_command_map`, and the `[commands]` wire-byte tables' *runtime* role all hang off this one branch. Deleting the builders' `cmd_map` parameter does not finish the job — it leaves `CommandMap` alive solely to serve a web-layer branch that no shipped profile reaches. Whoever executes MOR-2000 should be told this, or the "dead" verdict will be re-derived from scratch in six months.

**Depends on:** MOR-2000.
**Confidence:** high that it is unreachable for shipped profiles; medium that it is deletable (third-party-backend guard).
**Falsifier:** a shipped or supported profile that declares `set_agc` wire bytes without the `agc` capability, or a supported backend exposing no `capabilities` attribute.
**Fix class:** delete — but see F7, which is the same code viewed as a duplicate.

*(Out of scope, noted once and not pursued: the same attribute sweep flagged `WebServer._audio_analyzer_tap` in `web/server.py` as write-only — 1 write at `server.py:848`, 0 reads anywhere. Audio path, not command path.)*

---

## CONSOLIDATIONS (ranked by debugging cost)

### F1 — hamlib capability advertisement and control domains: rigctld hardcodes IC-7610 facts that live in profile data
```
Verdict:          A — displaced
Rank:             diverged
```
**The mechanism, one sentence:** deciding what a radio's attenuator/preamp/filter/frequency domains *are* and reporting them to a hamlib client.

**Elements:**
- `src/rigplane/rigctld/handler.py: _IC7610_DUMP_STATE` (`handler.py:119-145`, a module-level list) — returned verbatim by `RigctldHandler._cmd_dump_state` for **every** Icom radio. Hardcodes rig model `3078`, RX `100000–60000000`, TX `1800000–60000000`, filters `3000/2400/1800`, preamp `"12 20 0"`, attenuator `"6 12 18 0"`.
- `src/rigplane/rigctld/handler.py: _PREAMP_IDX_TO_DB` (`handler.py:189`, `[0, 12, 20]`), read by `RigctldHandler._cmd_get_level` and `RigctldHandler._execute_set_level`.
- `src/rigplane/rigctld/handler.py: RigctldHandler._execute_set_level` — the `ATT` branch contains a function-local `_att_steps = [0, 6, 12, 18]` and snaps the requested dB to the nearest member.
- Canonical location (already exists): `profiles/__init__.py: RadioProfile.att_values`, `RadioProfile.pre_values`, `RadioProfile.agc_modes`, `RadioProfile.hamlib_model_id`.

**Evidence that they do the same job (observation):**
- `rigs/ic7300.toml` `[attenuator] values = [0, 20]`; `rigs/ic7610.toml` `[attenuator] values = [0, 3, 6, …, 45]`; `rigs/ftx1.toml` `[attenuator] values = [0, 1]`. None of these is `[0, 6, 12, 18]`.
- `runtime/radio.py: IcomRadio.set_attenuator_level` reads `self._profile.att_values` and **raises** on a value outside it: `f"Attenuator level must be one of {sorted(att_values)} dB for {self._profile.model}, got {db}"`. Its docstring even names the divergence: *"IC-7300 has a single 20 dB step; IC-7610 has 0..45 in 3 dB steps"*.
- `grep -rn "att_values\|pre_values" src/` → consumed by `runtime/radio.py`, `backends/yaesu_cat/radio.py`, `web/server.py` (capability publishing). **`rigctld/` consumes neither.**

**Observable divergence, live-reachable on the bench IC-7300:** a hamlib client issues `L ATT 20` (a legal IC-7300 value, and the only nonzero one). rigctld snaps it to `18`, calls `IcomRadio.set_attenuator_level(18)`, which raises because `18 ∉ [0, 20]` → the client gets an error for a valid request. The same request over the web control socket passes `db` straight through (`web/handlers/control.py`, `case "set_att" | "set_attenuator"` → `q.put(SetAttenuator(db, receiver=rx))`; `RadioPoller._execute`, `case SetAttenuator` → `radio.set_attenuator_level(db, receiver=rx)`) and succeeds. Two ingresses, one radio, opposite answers.

Separately, every non-IC-7610 Icom is advertised to WSJT-X et al. as an IC-7610: wrong `rig_model`, wrong TX range, wrong attenuator/preamp step lists.

**Prior ruling:** none found justifying the divergence. `docs/api/rigctld.md:274` merely *describes* it (`\dump_state` → "IC-7610 capability block"), and `docs/api/rigctld.md:299-300` restates the invented `0/12/20` and `0/6/12/18` tables as though universal — documentation repeating the defect, not sanctioning it. CHANGELOG `#441` records the *Yaesu* half being fixed.

**In-flight:** partially landed. `rigctld/routing.py: YaesuRouting.dump_state` already does the profile substitution — `state[1] = str(int(getattr(self._radio, "hamlib_model_id", 2028)))` with the comment "Substitute the rig model from the radio's TOML config (closes #441)". Report as **migration incomplete: the profile-driven `dump_state` exists at `rigctld/routing.py: YaesuRouting.dump_state`, consumed by the Yaesu CAT path, not yet by the Icom default path in `RigctldHandler._cmd_dump_state`.**

**Adjudication — competing explanation rejected:** "hamlib's protocol requires a static capability block, so hardcoding is the only option." Rejected on the evidence: `YaesuRouting.dump_state` builds the same block dynamically from the profile in the same module, and `RadioProfile` already carries every field the block needs. The block is static because nobody generalised it, not because the protocol forbids it. A second explanation — "IC-7610 was the reference rig, so its numbers are the sane default" — is rejected because the reference rig was retired 2026-08-04 (CLAUDE.md) and the numbers are wrong for both surviving bench radios.

**Blast radius:** 1 file, 3 elements (`_IC7610_DUMP_STATE`, `_PREAMP_IDX_TO_DB`, the `_att_steps` local), plus `docs/api/rigctld.md` §"Levels" and any test pinning the static block.
**Required surface:** exists (`RadioProfile.att_values` / `.pre_values` / `.agc_modes` / `.hamlib_model_id`). A nearest-legal-value snap helper is the only new code, and it belongs beside the domain, not in `rigctld/`.
**Expensive contract: YES.** This is wire/protocol behaviour on a public interface — `dump_state` is what every hamlib client reads to decide what it may ask for. Changing it changes what WSJT-X and friends will attempt.
**Depends on:** none.
**Confidence:** high.
**Falsifier:** a rigctld test or an owner ruling requiring a fixed IC-7610 block for client-compatibility reasons; or evidence that `L ATT` is unreachable because `has_set_level` = `0x0001791B` masks the ATT bit (I did not decode the bitmask — **unknown**).

---

### F2 — hamlib level name → radio call: two dispatchers, one calling a non-canonical method with different semantics
```
Verdict:          C for existing twice (sanctioned vendor seam) / A for the domain handling inside them
Rank:             diverged
```
**The mechanism:** translating a hamlib `L <NAME> <value>` into a `Radio` method plus a domain conversion.

**Elements:**
- `src/rigplane/rigctld/handler.py: RigctldHandler._execute_set_level` (+ its table `_SET_LEVEL_FLOAT`) — the Icom default path.
- `src/rigplane/rigctld/routing.py: YaesuRouting.set_level` — the Yaesu CAT path.

**Consumers:** `_execute_set_level` is reached whenever `RigctldHandler._routing is None`; `rigctld/routing.py: create_routing` returns `None` for anything that is not `RigctldRoutable` — i.e. every Icom. `YaesuRouting.set_level` is reached for Yaesu CAT. Both are entered from the same `RigctldHandler._cmd_set_level`.

**That two exist is a recorded decision — quoted:** `create_routing`'s docstring — *"Dispatches via the public `RigctldRoutable` Protocol: radios that implement `rigctld_routing(cache, max_power_w)` get their custom strategy (Yaesu CAT today; Kenwood TS-590 or others in the future). Radios that do not — Icom CI-V — return `None` and the handler's built-in Icom routing is used as the default path."* `.importlinter` carries a named exception for it (`rigplane.backends.yaesu_cat.radio -> rigplane.rigctld.routing`), and `backends/LAYER.md` records it as "epic #1322's RigctldRoutable work". **This is a legitimate per-protocol fork; I am not proposing to merge the two dispatchers.**

**What is not sanctioned — the divergence inside them (observation):**

| level | Icom branch | Yaesu branch |
|---|---|---|
| `ATT` | snap to `_att_steps` → `radio.set_attenuator_level(dB)` | `radio.set_attenuator(round(value))` |
| `PREAMP` | dB → index via `_PREAMP_IDX_TO_DB` → `set_preamp(idx)` | `radio.set_preamp(round(value))` |
| `RFPOWER` | `round(value * 255)`, unclamped | `round(value * self._max_power_w)` |
| `MICGAIN` | `×255` | `×100` |
| `NB` / `NR` | `×255` | `×10` / `×15` |

The `ATT` row is a concrete bug on a **live bench radio**. `YaesuRouting.set_level` calls `set_attenuator`, not `set_attenuator_level`. Their contracts differ: `backends/yaesu_cat/radio.py: YaesuCatRadio.set_attenuator` is documented *"Set attenuator state (0 = OFF, 1 = ON)"* and emits `await self._write("set_attenuator", state=str(int(state)))`; `rigs/ftx1.toml:834` defines that write as `set_attenuator = { cat = { write = "RA0{state};" } }`. The sibling `set_attenuator_level` exists two lines below and does the right thing (`await self.set_attenuator(1 if db > 0 else 0, ...)`). So `L ATT 12` on the FTX-1 emits **`RA012;`** — a three-digit parameter where the CAT command takes one — instead of `RA01;`. `L ATT 0` happens to work.

The `PREAMP` row diverges the other way: `L PREAMP 12` succeeds on Icom (index 1) and **raises** on FTX-1, because `YaesuCatRadio.set_preamp` validates `level not in self._config.pre_values` and `rigs/ftx1.toml` declares `[preamp] values = [0, 1, 2]`.

**Adjudication — competing explanation rejected:** "the scale constants differ because the radios' native ranges differ, which is correct." Accepted for the scale columns (`×255` vs `×100` is a real hardware difference) — but rejected as an explanation for `ATT`/`PREAMP`, because there the two branches are not scaling differently, they are calling *different methods with different units* while receiving the identical hamlib argument. And the scale constants themselves are the doctrine violation: `100`, `10`, `15`, `255`, `_max_power_w` are radio facts written in `rigctld/`, while `RadioProfile` already carries the domains.

**Blast radius:** 2 files, ~5 level branches.
**Required surface:** exists for `att_values`/`pre_values`; **missing** for the per-level raw ranges (`NB` 0-10, `NR` 0-15, `MICGAIN` 0-100). Naming it concretely: a profile-declared control domain per level name, of the shape `RadioProfile.controls` already uses (`profiles/__init__.py: RadioProfile.controls: dict[str, ControlSpec | ControlDomainSpec]`), consulted by both branches.
**Expensive contract: YES.** Wire/protocol behaviour toward hamlib clients, and the CAT frame itself is being malformed.
**Depends on:** F1 (same tables).
**Confidence:** high for the `ATT`/`PREAMP` divergence; medium for whether a real client sends `L ATT` given `_YAESU_DUMP_STATE` advertises `"0"` for attenuator — a well-behaved client would not, an operator using `rigctl` directly would.
**Falsifier:** a bench capture showing the FTX-1 accepts `RA012;`; or `has_set_level` masking ATT off for both paths.
**Fix class:** consolidate (the domain source), not the dispatchers.
**Actionable:** yes.

---

### F3 — per-receiver write routing (cmd29 vs temporary VFO switch): implemented twice, and the two disagree on `receiver=0`
```
Verdict:          A — displaced
Rank:             diverged
```
**The mechanism:** getting a write onto MAIN or SUB on a dual-receiver Icom that has no cmd29 route for that command — by temporarily selecting the other receiver, writing, and restoring.

**Elements:**
- `src/rigplane/runtime/_dual_rx_runtime.py: DualRxRuntimeMixin._run_with_receiver_vfo_fallback` — the canonical implementation. **14 call sites** inside `runtime/` (`grep -rn "_run_with_receiver_vfo_fallback" src/`), reached by `IcomRadio.set_freq`, `set_mode`, `set_filter_width`, the tone family, the repeater family, and others.
- `src/rigplane/web/radio_poller.py: RadioPoller._execute`, the `case SetFreq(...)` and `case SetMode(...)` arms — a second, inline implementation using `await self._civ(0x07, data=bytes([self._profile.vfo_sub_code]))` / `vfo_main_code` around the write.

**Consumers:** the runtime helper serves rigctld and every direct `Radio` consumer (rigctld's `_RigctldCommandExecutor.execute` calls `self.handler._radio.set_freq(int(params["freq_hz"]), receiver=...)`). The inline copy serves only the web control socket and the HTTP command API.

**Divergence — three observable differences:**

1. **Restore is not exception-safe on the web copy.** `_run_with_receiver_vfo_fallback` restores inside `try/finally`, and on `TimeoutError` during restore logs *"timeout restoring VFO receiver to %s, retrying once"* and retries before propagating. `RadioPoller._execute`'s inline sequence has no `try`: if `await radio.set_freq(freq)` raises between the two `_civ(0x07, …)` calls, the radio is left parked on the temporarily-selected receiver, and the poller's own `_current_active()` (which reads `radio._radio_state.active`) still believes otherwise.
2. **`receiver=0` means different things.** `RadioPoller._execute`'s `SetFreq` arm, `else` branch: `if current != "MAIN" and self._profile.vfo_main_code is not None:` → it switches to MAIN first. `IcomRadio.set_freq` does `if receiver == RECEIVER_MAIN: await self._set_frequency_main(freq_hz); return` — no check of which receiver is selected; `_set_frequency_main` emits a plain `0x05` frame to whatever the radio currently has selected. On IC-7610 (`[cmd29] routes` contains no `[0x05]`) and IC-9700 (`routes = []`, with the TOML comment *"Keep this list empty so `supports_cmd29()` returns False"*), with SUB selected, `F VFOA <hz>` over rigctld writes **SUB's** frequency. The same request over the web writes MAIN's. *Mitigating (observation):* rigctld's `_resolve_target_vfo(None)` returns `self._active_vfo_name()`, so the common `currVFO` case tracks reality; the mismatch needs an explicit `VFOA` token under `chk_vfo=1`.
3. **Pacing and error text differ.** The web copy inserts `await asyncio.sleep(self._gap)` between switch and write; the runtime copy does not. The web copy raises `"…is unsupported by profile {model}: no cmd29 route and no VFO switch codes"`; the runtime copy raises `"…is unsupported for profile {model}: no SUB VFO select code"`.

**Prior ruling:** none found. `docs/internals/architecture.md` §`web/` states the opposite of what the code does — *"`src/rigplane/web/` must depend on `radio_protocol` protocols (`Radio` + capability protocols), not on concrete `IcomRadio`"* — and `RadioPoller._civ` does route through the `CivCommandCapable` protocol, so the letter of the rule holds while raw CI-V opcode `0x07` and profile VFO codes are being assembled in `web/`. `commands/LAYER.md` is the rule this actually breaks: *"`commands/_frame.py` — the CI-V kernel; do not duplicate framing."*

**Adjudication — competing explanation rejected:** "the poller must do it inline because it owns the serialisation lane, and calling `radio.set_freq(receiver=1)` would let the runtime issue VFO switches the poller cannot pace." Genuinely the strongest case, and it explains the extra `sleep(self._gap)`. Rejected as the whole story for two reasons: (a) the *same arm* already calls `radio.set_freq(freq, receiver=rx)` unchanged whenever `supports_cmd29(0x05)` is true, so the poller demonstrably tolerates the runtime doing its own routing on the cmd29 branch; (b) pacing is a parameter, not a reason to fork — it can be passed into the runtime helper. Divergence #1 (no `finally`) is not explicable as deliberate under any reading.

**Blast radius:** 2 files; 2 arms in `RadioPoller._execute` (`SetFreq`, `SetMode`) plus the third `vfo_sub_code`/`vfo_main_code` site in the `SelectVfo` arm; 8 `_civ(0x07, …)` calls total in the poller (`grep -c "_civ(0x07"` → 8).
**Required surface:** exists — `_run_with_receiver_vfo_fallback` is already the mechanism; it needs a pacing parameter and a public-ish entry point the `web` layer may legally reach (today it is a private mixin method on `IcomRadio`, and `web/` may not import `runtime` internals).
**Expensive contract: YES (partly).** `set_freq(receiver=0)` semantics — "write MAIN" vs "write whatever is selected" — is a `core/radio_protocol.py` contract question that both surfaces answer differently. Settle the contract before moving code. *(Ruled 2026-08-30: literal MAIN, always — see header note.)*
**Depends on:** none, but F3's contract question must be answered before F5.
**Confidence:** high on the code differences; medium on the operational impact of #2 (needs an explicit `VFOA` token, and neither dual-RX Icom is on the live bench — IC-7610 retired, IC-9700 not listed as bench hardware).
**Falsifier:** a `core/radio_protocol.py` docstring or test asserting that `set_freq(receiver=0)` means "the selected receiver", which would make the web copy the deviant one rather than rigctld. *(Post-audit verification 2026-08-30: the protocol docstring says "receiver: 0 = main (default), 1 = sub" — unconditional; a test pins the bare-frame behaviour for `receiver=0` but as a no-select-prefix pin, not a semantics claim. The runtime layer is the deviant side.)*
**Fix class:** design (settle the contract) → consolidate.
**Actionable:** yes.

---

### F4 — queue drain, deferred-TX lane and command-lifecycle failure reporting: written twice, diverging on three behaviours
```
Verdict:          A — displaced
Rank:             diverged
```
**The mechanism:** drain `CommandQueue`, apply the TX interlock, park a DEFER'd command in a single-slot lane until TX ends, then release/supersede/expire it, and report the outcome to `CommandService` as lifecycle events.

**Elements:**
- `src/rigplane/web/radio_poller.py`: `RadioPoller._stage_tx_interlocked_entries`, `RadioPoller._deferred_intent`, `RadioPoller._terminate_deferred_entry`, `RadioPoller._emit_deferred_entry_held`, `RadioPoller._mark_queued_command_failed`, `RadioPoller._execute_queued_entry`.
- `src/rigplane/backends/yaesu_cat/poller.py`: `YaesuCatPoller._drain_commands`, `YaesuCatPoller._emit_deferred_entry_held`, `YaesuCatPoller._mark_queued_command_failed`, `YaesuCatPoller._finish_deferred_entry`, `YaesuCatPoller._cancel_deferred_entry`, `YaesuCatPoller._deferred_release_is_live`.
- Shared primitive both use: `src/rigplane/runtime/tx_interlock.py: DeferredTxCommandLane`.

**Consumers:** the *same* queue instance feeds both. `web/web_startup.py` picks by capability: `isinstance(server._radio, StatePollable)` → `server._radio.create_state_poller(callback=…, command_queue=server._command_queue)` (Yaesu); `else` → `RadioPoller(server._radio, server._command_queue, …)` (Icom). One queue, two drains.

**Evidence they do the same job (observation):** `_emit_deferred_entry_held` exists in both with the same signature `(entry, *, expires_at)` and emits the identical payload — a `CommandIntent(name="queued_completion", …)` with `details={"heldBy": "tx_interlock", "reason": "tx_active", "expiresAt": expires_at}` — after the same "walk `command_service.lifecycle_events()` in reverse, match `command_id` + `source` + `details["session_id"]`, take `event.target`" lookup. The web version factors that lookup into `_deferred_intent`; the Yaesu version inlines it.

**Divergence (three, all observable):**
1. **`timed_out` is computed from different inputs.** Web: `_mark_queued_command_failed(self, entry, exc, *, timed_out: bool = False)` — an explicit keyword, set `True` only by `_terminate_deferred_entry` on `EXPIRED`. A wire timeout during execution therefore reports `timed_out=False` on Icom. Yaesu: `timed_out = isinstance(exc, (TimeoutError, RigplaneTimeoutError, CatTimeoutError))` — the same wire timeout reports `True`. Same field, same client, opposite value depending on the backend.
2. **The typed refusal envelope exists on one side only.** Web adds `params["details"] = {"blockedBy": "tx_interlock", "reason": exc.reason_code}` when the exception is a `TxInterlockRefusal`. The Yaesu version has no such branch, so a TX refusal on the FTX-1 — a live bench radio — reaches the client as a bare failure with no `blockedBy`/`reason`, which is exactly the envelope `docs/plans/2026-08-20-transmit-authority.md` records as shipped at `web/server.py:2161-2187`.
3. **Session liveness is checked on release by one side only.** `YaesuCatPoller._deferred_release_is_live` refuses to release a deferred entry whose control session is gone (`self._command_queue.session_is_live(entry.session_id)`) or whose readback expectations were dropped. `grep -n "session_is_live" src/rigplane/web/radio_poller.py` → **one hit**, inside `_refuse_key_from_gone_session` (PTT-specific). `RadioPoller._stage_tx_interlocked_entries` appends the held entry on `RELEASED` unconditionally. So a command deferred during TX, whose operator then disconnects, executes anyway on Icom and is dropped on Yaesu.

**Prior ruling:** none found that sanctions two drains. `docs/plans/2026-08-20-transmit-authority.md` (dated 2026-08-20) treats the web lane as a deletion target — its §9 row 9 lists *"Web seat deletion: `_enforce_tx_interlock`, `_WEB_IMMEDIATE_BLOCK_FAMILIES`, `_current_rf_state`, the staging lane + instance (taking the `heldBy:"tx_interlock"` emitter at `radio_poller.py:818` with it)"* — which names this exact emitter. That plan is **in flight and gated on owner questions Q1–Q11 + bench B1–B8**, i.e. not a settled ruling.

**In-flight:** `docs/plans/2026-08-20-transmit-authority.md` proposes a single `TransmitAuthority.admit` seat. It is unresolved (Q15 explicitly open as of 2026-08-21; ruled on MOR-1959 per the TX-truth audit). Report as: **a consolidation target is designed but not built; do not start a second one.**

**Adjudication — competing explanation rejected:** "the two pollers must differ because CI-V is fire-and-forget and CAT is request/response." True of the *execution* half and irrelevant to this half: everything cited above operates on `CommandQueueEntry` and `CommandService`, neither of which knows a protocol. Divergences 1–3 are not protocol consequences; they are two people solving the same problem on different days.

**Blast radius:** 2 files, ~6 symbols per side; the lifecycle payload is consumed by the frontend (`radio-intents.ts` wires `controlTransport.onCommandLifecycleDelivery` → `applyCommandLifecycleProjection`), so it is a client-visible surface.
**Required surface:** a backend-neutral queue-drain/deferred-lane runner that both pollers call. `runtime/tx_interlock.py: DeferredTxCommandLane` is the shared *state machine*; what is missing is the shared *driver* around it (drain → evaluate → stage → release → emit → fail). It cannot live in `web/` (backends must not import `web/`); `runtime/` is the layer that owns it — `DeferredTxCommandLane` already lives there.
**Expensive contract: YES.** `timed_out` and `details.blockedBy` are part of the command-lifecycle wire payload the frontend renders.
**Depends on:** the transmit-authority ADR's open owner questions. Do not consolidate ahead of that decision; the two `_mark_queued_command_failed` divergences (1 and 2) can be reconciled independently and cheaply.
**Confidence:** high.
**Falsifier:** an owner ruling that the Yaesu poller is deliberately stricter about gone sessions, or that `timed_out` is a per-backend notion.
**Fix class:** consolidate.
**Actionable:** partly — divergences 1 and 2 now; divergence 3 and the driver after the ADR.

---

### F5 — `Command` dataclass → `Radio` method: two dispatch tables, 48 arms in common, and the smaller one bypasses the protocol names
```
Verdict:          A — displaced (ownership) / consolidation NOT warranted in this form
Rank:             parallel
```
**The mechanism:** unpack a `Command` dataclass from `runtime/_poller_types.py` and call the corresponding method on the `Radio` protocol.

**Elements:**
- `src/rigplane/web/radio_poller.py: RadioPoller._execute` — **117 distinct `case` arms** (`awk 'NR>=2240 && NR<=3640' | grep -c "^            case "` → 121 case lines, 117 unique class names).
- `src/rigplane/backends/yaesu_cat/poller.py: YaesuCatPoller._execute_command` — **49 case lines**.

**Overlap (observation, computed by extracting the `case <ClassName>` tokens from both and running `comm`):** 48 names appear in both — `PttOn/PttOff`, `SelectVfo`, `SetFreq`, `SetMode`, `SetBand`, `SetAgc`, `SetAttenuator`, `SetPreamp`, `SetAfLevel`, `SetRfGain`, `SetSquelch`, `SetNB/SetNR/SetNBLevel/SetNRLevel`, `SetFilter*`, `SetRit*`, `SetSplit`, `SetTunerStatus`, `SetVox`, `VfoSwap`, `VfoEqualize`, … . **Zero** Yaesu-only arms. The Yaesu table is a strict subset by command name.

**The steelman, and why it fails:** the strongest case is "the arms must differ because the wire protocols differ." I tested it by reading every Yaesu arm. The bodies are overwhelmingly `await radio.<method>(<unpacked fields>)` — the *same* `Radio`-protocol surface the web poller uses. Genuinely Yaesu-specific content is three items: `_CIV_TO_YAESU_BAND`, the `SelectVfo` → `set_vfo_select(code)` normalisation, and eight `raise NotImplementedError` arms. Everything else is a second copy of the same unpacking.

Worse, the Yaesu table does not call the canonical names. It calls `radio.set_processor`, `set_processor_level`, `set_lock`, `set_tuner`, `set_keyer_speed`, `set_key_pitch`, `set_monitor_on`, `set_monitor_level`, `set_manual_notch_freq`. I checked whether those are the only names available: `for m in set_compressor set_compressor_level set_key_speed set_cw_pitch set_monitor set_monitor_gain set_dial_lock set_tuner_status set_notch_filter; do grep -c "def $m(" …; done` → **all nine exist on `core/radio_protocol.py`, on `backends/yaesu_cat/radio.py`, and on `runtime/radio.py`.** They are pure aliases on the Yaesu side — e.g. `YaesuCatRadio.set_compressor`: *"Alias for AdvancedControlCapable compatibility."* → `await self.set_processor(on)`; `set_tuner_status`: *"AdvancedControlCapable alias."* → `await self.set_tuner(value)`. So the protocol *is* honoured and the second table declines to use it, which is precisely the failure mode `backends/LAYER.md` promises against: *"conform to the relevant Capability Protocols … so the upper layers detect features via `isinstance` rather than backend-id branching … Zero changes required in `web/` / `rigctld/` / `cli/` if the Capability Protocols are honoured."*

**One arm I checked for divergence and cleared:** `YaesuCatPoller._execute_command`'s `case SetMode(mode=mode, receiver=rx)` drops `filter_width`, while `RadioPoller._execute` passes it. `web/handlers/control.py`'s `case "set_mode"` constructs `SetMode(mode, filter_width=fil_num, receiver=rx)` and its ack even reports `result["filter"] = fil_num`. This *looks* like a dropped parameter — but `backends/yaesu_cat/radio.py: YaesuCatRadio.set_mode` documents `filter_width: Ignored (not supported by this backend)`. **No observable divergence; the steelman wins on this one.** The residual is a prose defect: the ack claims a filter was applied on a backend that cannot apply one. Reported, not escalated.

**Adjudication:** displaced by ownership — this table is a property of the `Command` union, which lives in `runtime/_poller_types.py`, not of any one poller. But the method's own guidance applies verbatim: *"unifying a 112-command serialiser with a 10-verb one yields an abstraction with one real user."* A 117-arm and a 49-arm table merge into a 117-arm table with 68 `NotImplementedError`s. **Ownership: displaced. Actionability: no.** The cheap, correct move is smaller: make the Yaesu arms call the canonical protocol names, so a change to `set_compressor` cannot silently miss the Yaesu poller.

**Blast radius:** 2 files; 9 alias call sites to redirect.
**Required surface:** exists (the canonical `Radio` protocol names).
**Expensive contract:** no — internal call names only; no wire behaviour changes.
**Depends on:** F3 (settle `receiver=0` semantics first, or the merged table inherits the ambiguity).
**Confidence:** high.
**Falsifier:** a Yaesu alias whose canonical twin has different behaviour (I checked `set_compressor` and `set_tuner_status`; both are one-line pass-throughs — the other seven I did **not** open: **unknown**).
**Fix class:** none for the merge; consolidate for the alias call sites.
**Actionable:** yes, for the alias redirect only.

---

### F6 — SET-command coalescing: two collapse mechanisms on one path, and the second one is blind to the key the first one was fixed to include
```
Verdict:          A — displaced
Rank:             diverged
```
**The mechanism:** collapsing redundant SET frames so a slider drag does not flood the radio, last value winning.

**Elements:**
- `src/rigplane/web/handlers/control.py`: `ControlHandler._coalesce_key`, `ControlHandler._coalesce_command`, `ControlHandler._flush_coalesced_command` — time-paced (`self._CMD_MIN_INTERVAL = 0.05`), keyed by `f"{name}:{receiver}"` and, for `_COALESCE_TARGET_PARAM` = `{set_filter, set_vfo, select_vfo, set_band}`, by `f"{name}:{receiver}:{target!r}"`.
- `src/rigplane/runtime/_poller_types.py`: `CommandQueue.put` → `segment.dedup[type(cmd)] = entry`, with `_CommandQueueSegment.dedup: dict[type, CommandQueueEntry]`. Keyed on the **dataclass type alone** — receiver-blind, target-blind.

**Consumers:** every SET on the web path passes through both, in that order. `grep -c "q.put(" src/rigplane/web/handlers/control.py` → 119 call sites; `q.put_ordered` → 0 from that file.

**Divergence:** MOR-1499 fixed the first mechanism to include the receiver and the selector target, and `tests/test_mor1499_coalesce_keys.py` documents why — *"Cross-receiver: `set_nb` on MAIN followed by `set_nb` on SUB inside the pacing window superseded MAIN's frame with SUB's — MAIN's toggle never reached the radio"* and the filter-preset flow *"`set_filter(A)`, `set_filter_width(W)`, `set_filter(B)` in a single tick"*. The second mechanism still keys on type. Two same-type, different-receiver frames now correctly pass the first gate (different coalesce keys, both dispatched immediately) and then land in the same `_CommandQueueSegment.dedup` slot, where the later one overwrites the earlier — reinstating the exact bug, one layer down.

**The test cannot see it (observation):** `test_set_nb_cross_receiver_both_reach_radio` asserts against `_QueueRecorder`, a local fake that appends every `put`. The real `CommandQueue` is never constructed in that file. So the pin is green whatever `CommandQueue.put` does. This is the "check that cannot go red" pattern, not a claim that the fix was wrong.

**Adjudication — competing explanation rejected:** "type-keyed dedup is deliberate: it is the poller's own flood control, at a coarser grain than the ingress pacer." Partly true — the two do run at different grains (time-window vs drain-window), which is why I rank this *diverged duplicate* rather than *redundant copy*. Rejected as a full defence because the grains are orthogonal to the *key*: nothing about drain-window collapsing requires ignoring the receiver, and `CommandQueueEntry` carries everything needed to key on it. The narrower key silently undoes a fix made in the wider one, which is the definition of a diverged duplicate.

**Reachability:** timing-dependent. `RadioPoller._run` drains once per loop iteration and sleeps `self._adaptive_gap()` between iterations, so a burst arriving inside one gap collapses; a burst straddling a drain does not. The frontend preset flow dispatches its three frames in a single JS tick, which is the worst case.

**Blast radius:** 2 files, 1 line of key construction (`_poller_types.py:1067`, the `segment.dedup[type(cmd)] = entry` statement inside `CommandQueue.put`), plus whatever coalescing tests assume type-keying.
**Required surface:** exists — the key `ControlHandler._coalesce_key` already computes could be attached to `CommandQueueEntry` and used as the dedup key, giving one key definition instead of two.
**Expensive contract:** no.
**Depends on:** none.
**Confidence:** medium-high. High that the two keys differ and that the second is receiver-blind; medium on how often the drain window catches a same-type pair, which I did **not** measure.
**Falsifier:** a timing measurement showing `RadioPoller._run` always drains between two WebSocket frames; or an existing test that constructs a real `CommandQueue`, puts two different-receiver commands of one type, and observes both survive.
**Fix class:** consolidate.
**Actionable:** yes.

---

### F7 — rig-TOML directory discovery: four implementations, four different search rules
```
Verdict:          A — displaced
Rank:             parallel (one of the four is also a correctness risk)
```
**The mechanism:** finding the `rigs/` directory and loading the profiles in it.

**Elements and their rules (all observation):**
| # | Location | Rule |
|---|---|---|
| 1 | `profiles/__init__.py: _RIG_DIRS` + `_ensure_loaded` | `[repo_root/rigs, package/rigs]`, dev first; result **cached** in module globals |
| 2 | `cli/__init__.py: _rigs_dir` | `package/rigs` if it exists, else `repo_root/rigs` — **reversed** order |
| 3 | `backends/yaesu_cat/radio.py: _RIGS_DIR` (`radio.py:47`, a module constant `Path(__file__).parents[4] / "rigs"`) | a **single** path, no fallback |
| 4 | `web/radio_poller.py: RadioPoller._load_command_map` | `[repo_root/rigs, package/rigs]`, hand-rolled inline, wrapped in a bare `except Exception` |

**Consumers:** (1) is the canonical registry every `resolve_radio_profile` call goes through. (2) and (3) go through `profiles/rig_loader.py: discover_available_rigs`, which is the resource-aware entry point (it materialises non-filesystem resources into a temp dir for zip imports). (4) is the **only production caller of the raw `discover_rigs(directory)`** — `grep -rn "discover_rigs(" src/` returns exactly one `src/` hit outside `profiles/` itself.

**Divergence:** (3) resolves to `<repo_root>/rigs` in a source checkout and to a nonexistent path under `site-packages`; it is saved by `discover_available_rigs` trying package resources first, but the `load_rig(_RIGS_DIR / f"{profile}.toml")` fallback two lines down (`radio.py:140`) has no such cover. (4) is not resource-aware at all, so it would silently find nothing under a zip import, and its `except Exception: … return {}` converts any failure into an empty command map — which then makes `_send_cmd` return `False` with a debug log. (4) also re-parses every rig TOML on **each `RadioPoller` construction** (`RadioPoller.__init__`, `radio_poller.py:664`), bypassing the cache in (1); `discover_rigs` has no memoisation.

**Adjudication — competing explanation rejected:** "each layer needs its own path because import-linter forbids `web/` importing `profiles/` internals." Checked against `.importlinter`: the layer order is `web > backends > runtime > profiles`, so `web/` may legally import `profiles/` — and it already does (`radio_poller.py` imports `resolve_radio_profile` and `RadioProfile`). The linter is not the reason; nobody looked for the existing helper. (This is the "search on behaviour, not on the name you would have chosen" case from the method's helper-level mode: the existing helper is called `discover_available_rigs`, not `find_rigs_dir`.)

**Blast radius:** 4 files, 4 symbols.
**Required surface:** exists — `profiles/rig_loader.py: discover_available_rigs` is the canonical resource-aware loader, and `profiles/__init__.py: _ensure_loaded` is the canonical cache. Both are importable from every layer that has a copy.
**Expensive contract:** no — internal path resolution; the profile schema itself is untouched.
**Depends on:** D3 (deleting `_load_command_map` removes copy #4 outright, which is why the deletion list comes first).
**Confidence:** high.
**Falsifier:** a packaging test proving `parents[4]` is correct under a wheel install, which would clear (3).
**Fix class:** consolidate.
**Actionable:** yes — and cheapest if D3 lands first.

---

## Weakest link

**F6.** Its *structural* half is certain — `_CommandQueueSegment.dedup` is a `dict[type, …]` and `ControlHandler._coalesce_key` is not, and I read both. Its *impact* half rests on an inference I did not measure: that a same-type, different-receiver (or different-target) pair reliably lands inside one drain window of `RadioPoller._run`. I did not instrument the loop and I did not run anything. If the poller in practice drains between every pair of WebSocket frames, the second dedup never fires and F6 collapses from "diverged duplicate" to "latent redundancy" — still worth removing, no longer worth ranking above F7.

**Check first:** construct a real `CommandQueue` (not `_QueueRecorder`), `put` `SetNB(on=True, receiver=0)` then `SetNB(on=True, receiver=1)` with no intervening `drain_entries()`, and count what `drain_entries()` returns. One entry confirms the collapse; two refutes the whole finding. Then measure `_adaptive_gap()` against the frontend's per-tick dispatch burst.

Runner-up: **F3, divergence #2.** Whether `set_freq(receiver=0)` means "MAIN" or "the selected receiver" is a contract question I found no ruling on. I called the web copy correct because it matches the parameter's name; if the intended contract is the other one, the deviant surface is `web/`, not `rigctld/`, and the fix direction inverts. *(Ruled 2026-08-30: literal MAIN — the web copy's semantics are the contract; the runtime layer is to be fixed and the mechanism encapsulated there.)*

---

## Cleared — examined and healthy

1. **`core/command_service.py: CommandService` as the shared command spine.** Both surfaces genuinely converge on it: `web/handlers/control.py: _ControlCommandExecutor` and `rigctld/handler.py: _RigctldCommandExecutor` both implement the one `CommandExecutor` protocol, and both ingresses build intents with `core.command_service.command_intent_from_request`. Pending overlays, readback expectations, lifecycle emission and level normalisation are defined once, in `core/`. This is the premise-was-wrong outcome the method predicts for step 0, and it is the healthiest thing on this path.
2. **The two `CommandService` instances in `WebServer.__init__` are deliberate, not a duplicate.** `self.command_service` (executor `_SharedControlCommandExecutor`) and `self._http_command_service` (executor `_HttpCommandExecutor`) are separate on purpose, and the comment says why: *"Only `self.command_service` (the websocket-queued path) is subscribed — `self._http_command_service` below executes synchronously and its caller already sees the exception directly."* Verdict: legitimately local.
3. **HTTP command ingress does not fork.** `POST /api/v1/commands` and `/commands/batch` (`web/server.py:4007-4024`) route into `ControlHandler._enqueue_command` (`server.py:4268`, `:4357`) rather than growing a second catalog. `web/server.py: _HttpCommandCollector` looked like a third queue wrapper and is not — it collects instead of enqueuing so HTTP can execute synchronously. Correctly located.
4. **The `Command` union is fully wired at both ends.** I enumerated all **121** members of the `Command` union in `runtime/_poller_types.py` and cross-checked each against construction sites in `web/handlers/control.py` + `web/server.py` and against `case` arms in `RadioPoller._execute`. **Zero** members are never constructed; **zero** constructed members lack a web-poller arm. No orphan commands, no unreachable arms.
5. **Frontend command dispatch has a single validated door.** `lib/runtime/commands/radio-intents.ts: dispatchRadioIntent` validates the envelope against a declarative spec table and is the only path to `sendCommand` for radio control — used 123 times from `panel-commands.ts`, plus `panel-adapters.ts` and `mod-input-auto.svelte.ts`. The two other `sendCommand` consumers are both principled: `lib/runtime/tx-controller/browser-dependencies.ts` (PTT, deliberately excluded — the guard reads *"Only a known non-PTT radio intent may be dispatched"*) and `lib/local-extensions/host-api.ts` (the sanctioned open-core boundary). `lib/transport/http-client.ts` is read-only (`fetchCapabilities`, `fetchInfo`). No shadow command path in the browser.
6. **Frontend and backend command catalogs agree.** All **98** names in `radio-intents.ts`'s `intentSpecs` are members of `ControlHandler._COMMANDS` (153 names). No frontend intent can reach the server as `unknown_command`. Not a hand-maintained divergence.
7. **Frontend `components-v2/wiring/` has no dead modules.** `double-click.ts`, `dual-receiver-strips.ts`, `mobile-ptt-surface.ts`, `tx-ptt-gesture.ts` all have production importers outside `__tests__` (`dual-receiver-strips` via `SemanticRadioSurfaces.svelte` and `presentation/layouts/`). Same for `lib/runtime/`'s `resource-demand`, `resource-host`, `system-controller`, `scope-controller`.
8. **The CLI does not compute radio truth and is not the displacement site.** The dispatch flagged the CLI's age as a possible false-positive generator. Checked: `cli/__init__.py: _cmd_att` passes the operator's dB straight to `radio.set_attenuator_level(db)` and lets the profile validate; `_cmd_preamp` validates `0/1/2`, matching every shipped `[preamp] values`. No parallel dispatch table, no invented domain. The two blemishes are prose, not mechanism: the `print("Attenuator: ON (18 dB)")` literal and the error hint *"a dB value (e.g. 0, 6, 12, 18)"* both quote F1's invented table. The doctrine violation is in `rigctld/`, not here — the historical-shape caveat correctly cleared the CLI.
9. **Instance-attribute and constant sweep, 9 command-path modules.** Enumerated every `self._x = …` (including annotated form) and every module-level `UPPER_CASE` constant across `web/radio_poller.py`, `web/handlers/control.py`, `web/server.py`, `backends/yaesu_cat/poller.py`, `rigctld/handler.py`, `rigctld/routing.py`, `core/command_service.py`, `runtime/_poller_types.py`, `commands/commander.py`, then counted reads separately across all of `src/`, `tests/`, and `frontend/src`. **189 instance attributes → 2 write-only** (`RadioPoller._last_unselected_poll`, D1; `WebServer._audio_analyzer_tap`, out of scope). **74 module-level constants → 0 unread.** The class-level `_UNSELECTED_SLOT_INTERVAL`/`_UNSELECTED_SLOT_DEBOUNCE` fell outside that regex and were caught separately — recorded so the next sweep knows the gap. All searches literal.
10. **`commands/` builder layer is otherwise fully consumed.** 333 top-level definitions, 1 with no consumer outside the layer (D2). `commands/_frame.py: build_cmd29_frame` is called from `freq.py`, `mode.py`, `dsp.py`, `tone.py` — the framing kernel is not duplicated *inside* `commands/`. `commands/LAYER.md`'s "do not duplicate framing" rule holds within the layer; the one breach is upward, in `RadioPoller._send_cmd` (D3).
11. **State-query construction is already shared.** `RadioPoller._build_state_queries` delegates to `runtime/_state_queries.py: build_state_queries`, and `web/radio_poller.py` and `rigctld/server.py` both feed `profile.supports_cmd29` into `core/acquisition_scheduler.py: civ_acquisition_executor_for_provider` rather than re-deriving cmd29 routing for reads. The read path did not grow the fork the write path did.
12. **The ~90 `cmd29 = self._profile.supports_cmd29(…)` sites in `runtime/radio.py` are call sites, not implementations.** Each passes `command29=cmd29` to a distinct `commands/` builder for a distinct CI-V command. Repetitive, but one mechanism used ninety times is not ninety mechanisms. Not a finding.
