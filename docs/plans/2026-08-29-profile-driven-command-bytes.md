# Profile-Driven Command Bytes — Deleting the Hardcoded Fallback

**Date:** 2026-08-29
**Status:** Proposed (design only — this document's own commit changes no code,
no test and no profile).
**Companion — read together, neither is complete alone:**
`docs/plans/2026-08-29-profile-driven-command-bytes-evidence.md`
carries the evidence, the measurements and the analysis that this plan acts on.
The owner ruled the material be split so each part lands inside the 10-file /
1000-changed-line hard ceiling. **This document is the instruction: what to do,
in what order, and what goes red if a step is wrong.** The companion is the
argument: why the plan is shaped this way, and how every figure in it was
obtained. Sections here cited as "evidence §Cn" live there.
**Base commit:** `a2de2ab0`. Every count, census and file claim below was
re-measured against that commit in a dedicated worktree, not carried forward
from an earlier round. `main` has since moved to `e8fe6e45` (one commit,
`test(MOR-1900)` #2808, touching only `tests/integration/`); the two places that
commit is relevant are marked where they appear.
**Format model:** `docs/plans/2026-08-20-transmit-authority.md`
**Owner ruling this document implements (settled, not re-argued here):** the
hardcoded fallback and its constants are DELETED; the command map becomes
required; it must be structurally impossible to send a byte that did not come
from the profile.
**Two further owner rulings, settled 2026-08-29 and folded in as §8.1:**
what an undeclared command does (**D1**, three states, implemented by Step 4),
and how the profiles of radios nobody can measure get filled (**D2**, from
documentation, with the source recorded per entry). Q3–Q8 in §8.2 remain open.
**The spine of this plan, in one line:** do on the Icom side what already
works on the Yaesu side — that architecture ships today for Yaesu radios and is
walked through in evidence §C4.

---

## 0. In plain words

Every CI-V command this library sends is a short string of bytes. Which bytes
mean "set microphone gain" is different on each radio, so the project keeps a
table per radio in `rigs/*.toml`. The command-building functions in
`src/rigplane/commands/` can read that table — they take an optional `cmd_map`
argument for exactly that.

Nothing passes it. Not one production caller. Measured at `a2de2ab0`: a search
for `cmd_map=` across `src/` returns zero hits outside `src/rigplane/commands/`
itself. So all 232 public builders take their other branch, the one with the
bytes written into the source code years ago.

The consequence is not theoretical. On the IC-7300 the "ACC1 modulation level"
control and the "microphone gain" control build the *same eight bytes*. Setting
one sets the other. The web UI's ACC1 slider physically moves MIC gain.

The end state is one place that knows what bytes to send and what bytes to
expect back, and that place is the profile. This document says how to get there
without a flag day: what to measure first, what order to move things in, which
test turns red at each step, and what cannot be checked at all because the
radios involved are not on the bench.

The acceptance criterion, in the owner's own terms: he believed this binding was
*already* implemented, once, in one place. The work is done when a reader's
assumption that "of course the bytes come from the profile" is true, and when a
new call site cannot silently opt out of it.

**And he was half right, which changes what this plan is.** That assumption is
already true — for Yaesu radios. `backends/yaesu_cat/radio.py: YaesuCatRadio`
holds no wire bytes at all: it names a command, the profile supplies the command
text, and an undeclared command is refused aloud. It ships, and it was exercised
on the bench FTX-1 the afternoon this was written. So this document is not
designing something new. It is a plan to **do on the Icom side what already
works on the Yaesu side** (evidence §C4), and every mechanism choice below is measured
against that working example rather than argued from first principles.

---

## 1. What is true today (measured at `a2de2ab0`)

### 1.1 The shape

Every affected builder has the same body:

```python
if cmd_map is not None:
    return _build_from_map(cmd_map, "get_rf_power", to_addr=to_addr, ...)
return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_RF_POWER)
```

(`commands/levels.py: get_rf_power`, quoted from the base commit.) The same
CI-V knowledge is held twice, and only the second copy ever executes.

**Observed counts.** An AST census of `src/rigplane/commands/` at `a2de2ab0`:

| Quantity | Count |
|---|---|
| Public builders taking `cmd_map` | 232 |
| Private helpers taking `cmd_map` (`commands/_builders.py`) | 11 |
| `_CMD_*` / `_SUB_*` / `_CTL_*` byte constants in `commands/_frame.py` | 124 |
| Production call sites passing `cmd_map=` outside `commands/` | **0** |

### 1.2 The collision on the bench radio

Observed by execution against the IC-7300 CI-V address, at `a2de2ab0`:

```
set_acc1_mod_level(178, 0x94) -> fe fe 94 e0 14 0b 01 78 fd
set_mic_gain(178, 0x94)       -> fe fe 94 e0 14 0b 01 78 fd
identical: True
```

Cause, observed in `commands/_frame.py`: `_SUB_MIC_GAIN` and
`_SUB_ACC1_MOD_LEVEL` are both `0x0B`, and both builders combine their sub with
`_CMD_LEVEL` (`0x14`). The IC-7300 profile does not agree: `rigs/ic7300.toml`
declares `set_acc1_mod_level = [0x1A, 0x05, 0x00, 0x64]` — a menu address, not a
level sub-command. The profile has been right the whole time and has never been
consulted.

### 1.3 Who actually calls a builder

*Observed*, by AST, resolving import aliases (`get_attenuator as
get_attenuator_cmd` and friends), counting only calls to a name imported from
`rigplane.commands` that takes `cmd_map`:

| File | Direct builder calls |
|---|---|
| `runtime/radio.py` | 196 |
| `runtime/_scope_runtime.py` | 32 |
| `runtime/_dual_rx_runtime.py` | 4 |
| `backends/_icom_serial_base.py` | 1 |
| **Total** | **233 in 4 files** |

This corrects a figure that has been repeated as "~500 call sites". A naive
grep counts method calls on radio objects (`radio.get_freq()`) that have nothing
to do with the builders; `web/radio_poller.py`, `cli/`, `rigctld/` and
`backends/yaesu_cat/` import no `cmd_map`-taking builder at all. The migration
surface on the request side is four files.

### 1.4 The response half is bigger than the request half in one place

Reading a value back means matching the reply's command/sub bytes. Those bytes
are written as literals at the call site:

```python
return await self._get_bcd_level(civ, key="get_acc1_mod_level", command=0x14, sub=0x0B)
```

*Observed*, by AST at `a2de2ab0`:

| Population | Sites | of which pass an explicit `prefix=` |
|---|---|---|
| `runtime/radio.py: CoreRadio._get_bcd_level` calls | 36 | 9 |
| `runtime/radio.py: CoreRadio._get_bool_value` calls | 22 | 2 |
| `parse_level_response` / `parse_bool_response` direct calls in `runtime/` | 7 | 4 |
| **Request/response matcher total** | **65** | **15** |

There is a third, structurally different population: `runtime/_civ_rx.py`
compares `frame.command` / `frame.sub` against integer literals **105 times**
(census of `ast.Compare` nodes at `a2de2ab0`; the whole tree has 108, the other
three being 2 in `runtime/radio.py` and 1 in `core/civ.py`). That code decodes
*unsolicited* frames the radio sends on its own, so it needs the opposite
lookup — bytes to name — which today's `CommandMap` does not offer. See Q3.

The good news, *observed*: `_builders.py: parse_level_response` and
`_builders.py: parse_bool_response` take exactly `(command, sub, prefix)` — the
same three things `_frame.py: _build_from_map` derives from a wire tuple. The
two halves are the same decomposition run forwards and backwards. That is why
they can, and must, move together.

### 1.5 The safety net already exists

`tests/test_command_map_parity.py` builds every builder it can reach both ways,
for every profile in `rigs/`, and requires byte-identical frames. It is green at
`a2de2ab0` (5 passed). Its census, from `tests/command_map_parity_uncovered.txt`
— every number of which the test re-measures and asserts:

```
builders_compared         230      frames_identical         1344
public_builders           232      frames_diverged            76
dual_implementation_sites 139      profile_builder_map_gaps 1006
sites_compared            126      profiles                    8
hardcode_only_builders     16      cat_only_profiles           2
```

It fails on a new divergence **and** on a row that stopped diverging. That makes
it both the regression net and the worklist: the 76 rows are the complete list
of frames that change on the wire when the fallback dies.

## 2. End state

Concretely enough to tell whether you have arrived.

**Exists:**

- One binder object, constructed once per radio, holding that radio's
  `CommandMap`. Every CI-V frame the runtime sends is built through it, and
  every reply it matches is matched against a shape derived from the same map
  entry.
- Exactly one decoder of a `[commands]` wire tuple into `(command, sub, prefix)`.
  Today there are two: `commands/_frame.py: _build_from_map` and
  `web/radio_poller.py: RadioPoller._send_cmd`.
- A written, loader-validated contract for what a `[commands]` tuple may
  contain (§5, class C).
- The three states of D1 (§8.1) for a command a profile does not declare,
  expressed through the gate that already exists
  (`profiles: RadioProfile.supports_command`), not a new one — and a test that
  makes the third state ("not declared and unknown") impossible to ship.
- A recorded source for every wire byte in every rig TOML, per D2 (§8.1), so a
  reader can tell a value confirmed on hardware from one taken out of a manual
  without asking anyone.
- **One sentence that is true of both radio families**, where today it is true
  of one: *the command comes from the profile, is reached by name, and is
  refused aloud if the profile does not declare it.* That sentence already
  describes `backends/yaesu_cat/radio.py` (evidence §C4). Arrival is when it also
  describes `runtime/radio.py`.

**Deleted:**

- Every `if cmd_map is not None:` branch and every hardcoded fallback in
  `src/rigplane/commands/`.
- The `_CMD_*` / `_SUB_*` / `_CTL_*` constants in `commands/_frame.py` that no
  longer have a reader (124 exist at `a2de2ab0`; re-measure at deletion time —
  some are used by parsers and survive).
- `web/radio_poller.py: RadioPoller._load_command_map`, which re-scans `rigs/`
  off disk by guessing at paths relative to `__file__` and returns `{}` on any
  exception.
- The temporary measurement hook from Step 1.

**Impossible:**

- Sending a byte that is not in the profile. Not "discouraged" — there is no
  code path that produces one. `cmd_map` has no default, so a call that omits it
  raises `TypeError` before any frame exists.
- Matching a reply against bytes the request did not use: both come from one
  `CommandMap.get` on one name.
- Two commands silently sharing a sub-command, as ACC1 MOD level and MIC gain do
  today, unless the profile TOML itself says so — where it is one visible line
  in one file rather than two constants in a 412-line module.

**Completion signal, mechanical:** `tests/command_map_parity_uncovered.txt`
reports `dual_implementation_sites 0`, and
`tests/command_map_parity_divergences.txt` has no rows. The parity test asserts
both, so neither can drift.

---

## 3. How the map reaches the builders

Three candidates were weighed. The search that established no binder already
exists, the scale justification for adding one, and the re-examination of all
three against the working Yaesu implementation are in evidence §C6 — a reader
who only needs to *build* this can take the recommendation below and skip them.

### 3.1 Candidate A — a bound command object (recommended)

A small class in `commands/`, e.g. `commands/bound.py: BoundCommands`,
constructed once from a `CommandMap` in `runtime/radio.py: CoreRadio.__init__`.
Two members:

- `__getattr__(name)` returns the builder with the map already applied, so a
  call site reads `self._commands.set_mic_gain(178, self._radio_addr)`.
- `expect(builder)` returns the `(command, sub, prefix)` triple for the reply,
  computed by the *same* wire-tuple decoder from the *same* map entry, so a
  matcher reads
  `self._get_bcd_level(civ, key=..., shape=self._commands.expect(get_mic_gain))`.

**Why `expect` takes the builder and not a name — the Yaesu reference forced
this correction.** An earlier draft had `expect("get_mic_gain")`, which makes the
call site name the command twice: once by calling the builder, once as a string.
`backends/yaesu_cat/radio.py: YaesuCatRadio._query` names it **once** and gets
both the request template and the parser. The CI-V side can match that, because
the map key is already a literal inside each builder's `_build_from_map` call —
it does not need to be repeated, only exposed.

*Measured, because "just use the function name" would have been wrong.* All 232
public builders that take `cmd_map` fall into four cases, and the four sum to
232:

| Case | Count | Example |
|---|---|---|
| One literal key, equal to the function's own name | **195** | `commands/levels.py: get_rf_power` |
| One literal key, different from the function's name | **34** | `commands/system.py: get_system_date` and `set_system_date` both resolve `system_date`; all thirteen `commands/scope.py: scope_set_*` setters resolve the matching `get_scope_*`; `commands/vfo.py: set_dual_watch_on` and `set_dual_watch_off` both resolve `set_dual_watch` |
| No literal key — delegates to a shared template that supplies one | **2** | `commands/dsp.py: set_attenuator`, `commands/vfo.py: set_dual_watch` |
| **No literal key — chooses between two keys at runtime, by asking the map** | **1** | `commands/speech.py: get_speech` |

So the key must be **exposed by each builder**, never derived from its name, and
a test that asserts every builder exposes exactly the key it passes to
`_build_from_map` is what keeps the two from drifting. Those 34 are also the
direct reason the two gap censuses in evidence §C2 differ.

**The fourth case is one builder, and it constrains the shape of `expect`.**
`commands/speech.py: get_speech` does not hold a key at all; it holds a choice —
`speech_key = "set_speech" if cmd_map.has("set_speech") else "get_speech"` —
so its key is a function of the map, not a property of the builder. Two existing
tests pin that behaviour: `tests/test_commands.py:
test_speech_cmd_map_prefers_set_speech_key` and `tests/test_rig_ic7300.py:
test_get_speech_cmd_map_uses_set_speech`. The consequence for the design is
concrete: what a builder exposes cannot be a plain string attribute, because for
this one it must be evaluated against the map. Either the exposed key is
`Callable[[CommandMap], str]` for every builder — uniform, and every builder but
this one ignores its argument — or `get_speech` is split into `get_speech` and
`set_speech`
and the probe moves to its caller, which would make the exposed key a plain
string everywhere and delete the fourth case. **Whichever is chosen, it is
decided in Step 3 when `BoundCommands` is written, not discovered in Steps 5..N**
— and the two speech tests above are what goes red if the choice breaks the
existing behaviour.

**Can a new call site silently bypass it?** No — but the guarantee comes from
deleting the fallback, not from this object. With `cmd_map` required and no
default, importing the free builder and calling it without a map raises
`TypeError`. There is no wrong-bytes path left, only a loud one. `BoundCommands`
supplies ergonomics and the response half; the *structural* guarantee is the
required parameter.

**Layer rule.** `commands/LAYER.md` allows `core` only and forbids importing
`profiles`. `bound.py` imports nothing but `commands` internals; the map is
injected from above, which is precisely the shape the layer charter prescribes
("`CommandMap` is a runtime container constructed by `profiles`"). Survives.

**mypy --strict.** This is what the recommendation gives up: `__getattr__` types
as `Callable[..., bytes]`, so argument checking is lost at the ~233 migrated
request sites. Two things narrow the loss, and neither cancels it: the builders
keep their real signatures and are still exercised directly with them by
`tests/test_command_map_parity.py`; and per `CLAUDE.md`, `mypy --strict` runs in
CI only over `src/rigplane/web`, so it is not today checking those sites anyway.
The honest statement is that a typo in a builder argument at a migrated call
site becomes a runtime `TypeError` caught by a test rather than a type error
caught by a checker.

**Response half.** Handled by the same object, from the same map entry, reached
by one reference — which is the point, and is what `YaesuCatRadio._query`
already does for the other family.

### 3.2 Candidate B — required keyword argument at every call site (rejected)

Make `cmd_map` a required keyword-only parameter and write
`cmd_map=self._cmd_map` at all 233 request sites. No new type at all.

Keeps full mypy typing. Rejected because the response half has no analogue: the
65 matcher sites would each unpack `self._cmd_map.get(name)` into
command/sub/prefix by hand, producing a third and fourth copy of a decomposition
that should exist once, and leaving request and reply as two separate edits that
a reviewer must remember to keep in step. The owner has ruled that the two are
"the same movement". The matcher sites are also where the mistakes concentrate:
15 of the 65 carry a hand-written `prefix=`.

### 3.3 Candidate C — ambient binding via `contextvars` (rejected)

Set the map in a context variable at connect time; builders read it. Zero call
sites change.

Rejected: it is invisible. Nothing at the call site says where the bytes came
from, which re-creates the exact condition this work exists to remove
("nowhere left to get confused"). A builder called outside the context fails
with an error that names the context, not the command. It also conflicts with
`commands/LAYER.md`'s ban on module-level state.

## 4. Ordered steps

Each step fits the repo guardrails (hard ceiling 10 files / 1000 changed lines)
and leaves the tree shippable. "Red if broken" names the test that fails if the
step is reverted or done wrong; where the test does not exist yet, writing it is
part of the step.

### Step 1 — Measure. Make the fallback shout. (No behaviour change.)

*Why first:* today the list of commands the bench radio actually exercises is a
guess. One session with the IC-7300 turns it into fact.

- `core/env_config.py` — add a reader for `RIGPLANE_COMMAND_FALLBACK_AUDIT`,
  alongside the existing `get_managed_tx_enabled`. This is where env flags
  already live; `commands/` may import `core`.
- `commands/_fallback_audit.py` (new, explicitly temporary) — wraps each
  exported builder so that a call with `cmd_map is None` emits one WARNING
  naming the builder. Off unless the flag is set.
- `commands/__init__.py` — install the wrapper.
- `commands/LAYER.md` — record the temporary exception (see Q4).
- `tests/test_command_fallback_audit.py` (new).

**Red if broken:** `tests/test_command_fallback_audit.py` — flag off, the
exported names are the raw functions and nothing is logged; flag on, a call
without a map logs exactly one warning naming the builder, and a call *with* a
map logs none.

**What the output means, precisely:** since no caller passes a map, "which
fallbacks fired" is identical to "which builders were called". That is exactly
the missing fact — cross it with the 76 divergence rows and the gap rows and you
get, for a real session: how many commands would change on the wire, and how
many have no profile entry at all. It does *not* tell you whether the profile's
bytes are correct. Nothing but a radio or a manual tells you that.

**Rollback:** revert the commit. Nothing depends on it.

### Step 2 — Pin the wire-tuple contract, and fix the frames whose shape is wrong

*Why before anything moves:* right now a `[commands]` tuple has two meanings.
`rigs/x6100.toml` writes `ptt_on = [0x1C, 0x00, 0x01]`, including the payload
byte; `rigs/ic7300.toml` writes `ptt_on = [0x1C, 0x00]`, not including it. The
builder appends its own payload either way, so the X6100 map branch emits
`1C 00 01 01`. This is class C of §5 and it must be settled before any builder
depends on the map.

- `rigs/_schema_v2.md` — state the contract.
- `profiles/rig_loader.py` — validate it at load.
- `rigs/x6100.toml`, `rigs/ic7610.toml`, `rigs/ic9700.toml` — the offending rows.
- `commands/scope.py: get_scope_center_type` — its map branch omits the receiver
  selector the fallback appends (MOR-1981 shape; the parity test's own docstring
  names this one).
- `tests/command_map_parity_divergences.txt` — delete the 10 class-C rows.

**Red if broken:** `tests/test_command_map_parity.py`. It fails if a class-C row
is deleted while the divergence persists, and equally if a row is left in place
after the divergence is fixed. Plus a new case in `tests/test_rig_loader.py` for
the loader validation.

**Rollback:** revert; the divergence rows come back and the test is green again.

**Split if it does not fit:** 2a = contract + loader validation + `_schema_v2.md`;
2b = the TOML and `scope.py` fixes with the parity-file deletions.

**Bench dependency:** the `scope.py` change and the X6100 `ptt_on` change alter
bytes on the wire. IC-7300 can confirm the scope one. X6100 is not on the bench
(§7).

### Step 3 — Bind the map once, at radio construction. (Still no call site changed.)

- `profiles/` — make the loaded `CommandMap` reachable from the resolved
  profile. `RadioProfile` carries `command_names` but not the bytes, so this is
  a new field populated where `command_names` already is, or a lookup beside
  `resolve_radio_profile`.
- `commands/bound.py` (new) — `BoundCommands` per §3.1, plus the single
  wire-tuple decoder extracted from `commands/_frame.py: _build_from_map` so
  both halves share it.
- `runtime/radio.py: CoreRadio.__init__` — construct it.
- **Settle how a builder exposes its map key**, including the one builder whose
  key is a function of the map rather than a constant
  (`commands/speech.py: get_speech`, the fourth case in §3.1). Either every
  builder exposes `Callable[[CommandMap], str]`, or `get_speech` is split and
  every builder exposes a plain string. Deciding it here is the point: doing it
  in Steps 5..N means discovering it module by module.

**Red if broken:** `tests/test_profile_command_binding.py` (new) — for every
CI-V profile in `rigs/`, the radio's bound command set is non-empty and its
names equal those of `RigConfig.to_command_map()` for that profile. Plus the two
existing speech pins, which fail if the key-exposure choice changes behaviour:
`tests/test_commands.py: test_speech_cmd_map_prefers_set_speech_key` and
`tests/test_rig_ic7300.py: test_get_speech_cmd_map_uses_set_speech`.

**Rollback:** revert; nothing reads the binder yet.

### Step 3b — Take the poller off the disk scan

`web/radio_poller.py: RadioPoller._load_command_map` re-discovers `rigs/` by
guessing paths relative to `__file__` and returns `{}` on any exception, so a
load failure silently becomes "no commands". Point it at the bound map from
Step 3 and delete both the scan and `RadioPoller._send_cmd`'s private copy of
the wire decomposition.

**Red if broken:** a new case in `tests/test_radio_poller_coverage.py` asserting
`set_agc` still emits the profile's bytes with the disk scan gone.

### Step 4 — Implement the three states for an undeclared command (decision D1)

*Why before the first module migrates:* today a command the profile does not
declare silently emits the fallback bytes. The moment a module requires the map,
that same call becomes a bare `KeyError` from `CommandMap.get`. Neither is
acceptable, and D1 (§8.1) settles what replaces them. Sizes are in evidence §C2: for the
one radio on the bench, 35 command names.

Implement exactly three states, with **the same behaviour in development and in
production**. The command path does not branch on environment; the only
dev/production asymmetry lives in the guard test.

1. **Declared** → send the profile's bytes. Nothing else changes.
2. **Not declared, and the profile knows the radio does not have the command**
   → report *unsupported by this radio*, through the mechanism that already
   exists: `profiles: RadioProfile.supports_command`, which is already declared
   on the `core/radio_protocol.py` Protocol and implemented by
   `runtime/radio.py`, `backends/yaesu_cat/radio.py` and
   `backends/rigctld_client/radio.py`. Not an exception thrown at the consumer,
   and **not** log-and-continue.
3. **Not declared and not known either way** → this state must not exist at
   release. A test enumerates every builder against every profile and goes red
   on any remaining gap, so the state is eliminated before shipping rather than
   handled at runtime. If it is nevertheless reached at runtime it behaves as
   (2) — refuse aloud and log — and **never silently succeeds**.

*The constraint behind this, which is why (3) cannot simply log:* a log line in
production means the command did nothing while the user believes it worked. That
is the same class of failure as the measured ACC1/MIC-gain collision in §1.2 —
the action produced the wrong result and nobody was told. Silence is the failure
mode this whole programme removes, so silence cannot be its fallback.

*One thing states 2 and 3 need that does not exist yet.* Today a profile has no
way to say "this radio does not have this command" — *observed*:
`profiles/rig_loader.py` builds `command_names` as `frozenset(self.commands)`,
so absence from the TOML is the only representation, and it means "declared
missing" and "nobody has looked" identically. Distinguishing them is what makes
state 2 different from state 3, and it needs no new concept: D2 (§8.1) already
requires every entry to record where its value came from, and "not present on
this model, per <named authority>" is an entry with a source and no bytes.
Adding that spelling to `rigs/_schema_v2.md` and to the loader is part of this
step.

**Red if broken:** two tests.

- `tests/test_undeclared_command_policy.py` (new) — the command path. For a
  profile that does not declare a command, the runtime reports unsupported and
  does not raise `KeyError`; the capability surface reports the command absent;
  and no code path returns success.
- `tests/test_profile_command_coverage.py` (new) — the guard for state 3. It
  enumerates every public builder against every CI-V profile in `rigs/` and
  fails on any command name a profile neither declares nor is recorded as not
  having. This is the test that carries the dev/production asymmetry: it is the
  only place the difference lives. It starts red by construction and turns green
  as D2 (§8.1) fills the profiles; until then it needs an explicit, shrinking
  allow-list, in the style of `tests/command_map_parity_divergences.txt` — a
  committed file the test fails on when a row stops being needed, so the list
  cannot quietly stop shrinking.

**Rollback:** revert; the command path returns to the fallback it had before the
first module migrated.

### Steps 5..N — Migrate module by module, requests and replies together

One domain module per PR. For each: (a) make `cmd_map` required keyword-only and
delete the fallback branch and the constants it alone read; (b) move that
module's call sites in `runtime/radio.py`, `runtime/_scope_runtime.py`,
`runtime/_dual_rx_runtime.py`, `backends/_icom_serial_base.py` onto the binder;
(c) move that module's response matchers **in the same commit**.

Order, riskiest-first among the ones that matter, safest-first among the rest:

1. `config.py` — 44 of the 76 divergence rows, and the module that carries the
   ACC1/MIC collision and `set_data_off_mod_input`, which
   `runtime/profiles_runtime.py: apply_profile` writes with no user action.
2. `levels.py` — 16 rows, the other half of the collision.
3. `vfo.py` (10 rows), `ptt.py` (2), `scope.py` (4) — the modules with class-C
   shapes, after Step 2 has cleared them.
4. Everything else, in batches: `dsp.py`, `mode.py`, `freq.py`, `system.py`,
   `tone.py`, `meters.py`, `antenna.py`, `power.py`, `cw.py`, `speech.py`,
   `memory.py`, `tx_band.py`. These have zero divergence rows, so on every
   profile that declares them the bytes do not change.

**Red if broken, per module:**

- `tests/test_command_map_parity.py`. Its census asserts
  `dual_implementation_sites`; a module that migrates without the census being
  updated fails, and a census updated without the migration fails too. A module
  that migrates its requests but not its replies leaves rows in the divergence
  file that the test then reports as no longer diverging.
- `tests/test_response_shape_from_profile.py` (new, written in Step 5 and
  extended each step) — **this is the keystone.** For each CI-V profile and each
  matcher-backed getter, the `(command, sub, prefix)` used to parse the reply
  must equal the shape derived from the map entry used to build the request. It
  is red for exactly the half-done case: requests moved, replies not.
- The module's own existing tests: `tests/test_rig_ic7300.py`,
  `tests/test_command_map_integration.py`, `tests/test_rig_ic7610.py`,
  `tests/test_rig_ic705_mor1540.py`.

**Rollback:** each module is independent; revert one PR.

### Step Z — Remove the scaffolding and fix the document that lied

- Delete `commands/_fallback_audit.py` and its env flag.
- Delete the `commands/_frame.py` constants that no reader is left for
  (re-measure; 124 exist at `a2de2ab0` and some are read by parsers).
- Rewrite `docs/api/commands.md` — evidence §C3 lists three claims in it that are false
  at `a2de2ab0`.
- `tests/command_map_parity_divergences.txt` empty;
  `tests/command_map_parity_uncovered.txt` reports
  `dual_implementation_sites 0`.

**Red if broken:** `tests/test_command_map_parity.py` asserts both numbers.

*After Step Z, and only after it,* the single profile-driven test double
described in evidence §C8 becomes buildable. It is deliberately not a step here — see
the sequencing constraint in evidence §C8, which is the whole reason it sits outside the
ordered list.

---

## 5. The 76 divergences: each one is a decision

They are not a backlog of identical chores. Each row says the profile and the
source code disagree, and someone has to say which is right. Grouped by shape so
the work can be handed out. **Do not read the rows from here — read
`tests/command_map_parity_divergences.txt`,** which carries the profile, the
builder, the arguments and both frames for every one.

Row counts below are *observed*: I classified all 76 rows by builder and profile
at `a2de2ab0`. The class *assignments* are inference from structure, stated as
such.

### Class A — profile right, fallback wrong (46 rows)

Menu-address disagreements on IC-7300 (20 rows), IC-705 (16) and IC-7610 (10).
The clearest case is the one on the bench: `rigs/ic7300.toml` puts ACC1 MOD level
at menu `1A 05 00 64` while the fallback emits `14 0B`, which is MIC gain.
Migrating these fixes a live bug. Verifiable on the IC-7300; **not** verifiable
for IC-705 or IC-7610 (§7).

### Class B — profile copied from another radio, provenance unknown (20 rows, all IC-9700)

**This class was originally written as "profile wrong, a data bug". That was an
overstatement and is corrected here.**

*Observed:* `rigs/ic9700.toml` contains a block introduced by the comment
`# IC-7300-specific menu sub-addresses (also in wfview; not all rigs share
these)` whose 23 following lines are byte-identical to the same block in
`rigs/ic7300.toml` — I diffed them, including the comment. All 20 IC-9700 rows
come from that block.

*Not observed, and not established:* that any one of those values is wrong for
an IC-9700. Copied is not the same as wrong; Icom reuses menu addresses across
models often enough that a copied block can be right by accident or right by
design. What is established is only that the values carry no IC-9700 provenance,
and that this repository holds none either — `docs/validation/cat-audits/` has
audits for FTX-1, IC-7300, IC-7610, TX-500 and X6200, and **no IC-9700 file**
(observed by listing the directory at `a2de2ab0`). No IC-9700 has ever been on
this bench, so nothing here can be settled by measurement.

*What this class therefore is:* 20 rows whose value may be right and whose
**source is unknown**, which under D2 (§8.1) is itself the defect to fix. Each
row is resolved by finding an IC-9700 authority — the factory manual or wfview's
IC-9700 rig definition — and recording in the TOML which one it was, whether or
not the byte changes. A row that survives unchanged with a recorded source is a
completed row, not a skipped one. The owner has confirmed the factory manuals
are all available online, so this class is **answerable**, just not by
measurement: nothing here is blocked, and the earlier framing of "cannot be
settled" applied only to hardware confirmation.

*Sweeping the class:* I checked whether the same copied block appears in the
other profiles. `rigs/ic705.toml`, `rigs/ic7610.toml`, `rigs/x6100.toml` and
`rigs/x6200.toml` do not carry it — their divergent rows have distinct values
(IC-705 `quick_split` is `1A 05 00 45` against IC-7300's `1A 05 00 30`, for
instance). I did **not** audit the whole of every TOML for copied blocks that
happen *not* to diverge, and those are invisible to the parity file by
definition. D2 makes that sweep part of the work.

### Class C — the tuple contract is not pinned (10 rows)

Not "which address" but "how many bytes belong in the tuple". Three shapes:

- `scope.py: get_scope_center_type` (4 rows: IC-705, IC-7300, IC-7610, IC-9700)
  — the map branch omits the receiver selector byte the fallback appends. On
  `0x1C` an extra byte is a write, not a read.
- `vfo.py: get_dual_watch` and `vfo.py: get_main_sub_band` (2 rows each,
  IC-7610 and IC-9700) — the TOML puts the query byte in the tuple *and* the
  builder appends it, so the map branch emits it twice (`07 C2 C2`).
- `ptt.py: ptt_on` / `ptt_off` (2 rows, X6100) — `rigs/x6100.toml` includes the
  payload byte in the tuple where `rigs/ic7300.toml` does not.

These are Step 2 and they block everything else, because the meaning of a tuple
has to be one thing before 232 builders depend on it.

### How to hand it out

- One person per profile for classes A and B — the authority (manual, wfview,
  radio) is per radio, and the rows never cross profiles. Under D2 (§8.1) that
  person's output per row is a byte *and* a recorded source, and a row that ends
  up unchanged still counts as done once its source is written down.
- Class C is one person, once, because it is a contract, not data.
- IC-7300 first: it is the only profile whose class-A rows can be confirmed by
  pressing a button and watching a meter move. Its 35 undeclared commands
  (evidence §C2) are the same person's second task, and the only such list on this
  bench that can be finished by measurement.
- The copied-block sweep D2 requires is a separate pass over all eight TOMLs,
  not part of any profile's row work: by construction it looks for blocks that
  do *not* appear in the divergence file.

---

## 6. The response half

Not an appendix. If requests become profile-driven and replies do not, reading
breaks on exactly the commands the migration fixed — the 46 class-A rows, where
the request now goes to the right address and the matcher still expects the old
one. The 1344 identical frames keep working, so the failure is partial and
looks like a flaky radio.

Three populations, three different answers.

**Population 1 — the 65 request/response pairs.** `runtime/radio.py:
CoreRadio._get_bcd_level` and `CoreRadio._get_bool_value` are handed
`command=`, `sub=` and sometimes `prefix=` as literals, and pass them to
`_builders.py: parse_level_response` / `parse_bool_response`. Those three
parameters are exactly what `_frame.py: _build_from_map` derives from a wire
tuple. So the fix is mechanical and belongs in the same commit as the request:
`self._commands.expect("get_mic_gain")` replaces the literals, and
`tests/test_response_shape_from_profile.py` asserts build-shape equals
parse-shape for every profile. This is the whole of what "same movement" means
operationally.

**Population 2 — the 15 hand-written `prefix=` arguments.** These encode the
extra bytes beyond command+sub. They are the *same* bytes the wire tuple's tail
carries, which means today they are a hand-maintained duplicate of profile data.
They disappear into `expect()` rather than being migrated.

**Population 3 — `runtime/_civ_rx.py`, 105 comparisons.** Structurally
different: it decodes frames the radio sends unprompted, so it needs bytes to
name, and `CommandMap` offers only name to bytes. Building a reverse index is a
change to `CommandMap`'s surface, which this design was told not to reopen. The
plan therefore stops at population 2 and names this as scope for a decision —
Q3. Leaving it is not free: until it moves, unsolicited frames are still decoded
against IC-7610-era literals, so a radio whose profile disagrees will have its
own broadcasts misread even after every request and reply is profile-driven.

---

## 7. What could go wrong

**A step lands half-done.** The dangerous half is requests moved, replies not:
reads fail on the 46 class-A rows and keep working everywhere else, which looks
like an intermittent radio rather than a bug.
`tests/test_response_shape_from_profile.py` exists to make that state red, and
must land with the first module, not after it.

**A module migrates before Step 4.** Every profile gap turns into a bare
`KeyError` from `CommandMap.get` instead of a wrong frame — neither of the three
states D1 requires. The gaps are largest exactly where verification is hardest
(X6100 189 distinct command names, X6200 153, IC-705 43; sizes and what they do
and do not mean are in evidence §C2).

**Step 2 lands without Step 2's bench check.** The `scope.py: get_scope_center_type`
fix appends a byte on `0x1C`, and on that command an extra byte is a write. It
is confirmable on the IC-7300 and must be.

**What cannot be verified by measurement.** Per `CLAUDE.md`, the live bench is
**IC-7300 and FTX-1**, and FTX-1 is not a CI-V radio. IC-7610 is retired; X6200
was destroyed. So exactly one of the eight profiles can be settled by pressing a
button on a radio. (Whether IC-705, IC-9700 or X6100 were ever on a bench at
some point in the past is **unknown** — I did not verify it, and it does not
change the plan, because none of them is available now.)

| Profile | Divergent rows | Can a radio settle them? |
|---|---|---|
| IC-7300 | 21 | Yes |
| IC-9700 | 23 | No |
| IC-705 | 17 | No |
| IC-7610 | 13 | No — retired |
| X6100 | 2 | No |
| FTX-1, TX-500 | 0 | Not applicable — non-CI-V protocols |

Of the 76 rows, 21 are bench-settleable and 55 are not. D2 (§8.1) rules that
those 55 are filled from the factory manual or wfview's rig definitions, with
the source recorded per entry in the TOML. The IC-9700 block is the argument for
that requirement rather than an objection to it: its values may well be correct,
but nothing in the file says where they came from, so nobody can tell a
verified byte from an inherited one — which is the defect D2 removes.

**The IC-7610 default survives the migration.** `profiles: resolve_radio_profile`
falls back to the IC-7610 profile when nothing identifies the radio — observed
in its body, with the comment "prefer IC-7610 (primary LAN reference rig)".
After this work, a misidentified radio gets IC-7610 bytes from a *profile*
instead of from a *hardcode*. Visible rather than hidden, but still wrong. Q5.

**FTX-1 and TX-500 are not stranded.** They are the two `cat_only_profiles`
whose `CommandMap` is empty, which looks fatal for a required map. It is not:
`rigs/ftx1.toml` declares `[protocol] type = "yaesu_cat"` and `rigs/tx500.toml`
`type = "kenwood_cat"`, `backends/factory.py` routes Yaesu models to
`YaesuCatRadio`, and *observed by AST*, `backends/yaesu_cat/` imports no
`cmd_map`-taking builder. They never reach this code. evidence §C5 says why the maps
are empty in the first place, which is a narrower carrier rather than missing
data.

**A green integration run is not evidence that a step was safe.** *Observed:*
`tests/mock_server.py: MockIcomRadio` dispatches eight CI-V commands and has
**no `0x1A` branch and no `0x27` branch**, while all 60 `config.py`/`levels.py`
divergence rows are `1A 05` menu addresses and all four `scope.py` rows are
`0x27`. The integration doubles therefore cannot observe a single one of the 76
frames this migration changes, and will stay green through all of them. The
safety net for every step in §4 is `tests/test_command_map_parity.py` and the
per-step tests named there — never the integration suite.

The owner has ruled that the two missing command families are **not** patched
into the existing double now: the single profile-driven double closes the gap
without carrying any command list inside it, and adding two branches today would
deepen the hole being filled. Full measurement and the named closure are in
evidence §C8.

**The compatibility break.** Measured and costed in evidence §C7; the short
version is that it is cheap.

**The measurement hook is in tension with its own layer charter.**
`commands/LAYER.md` forbids I/O and forbids import-time mutation. A logging
wrapper installed over the exported builders is arguably both. It is temporary
and deleted in Step Z, which is the argument for allowing it — but it is an
argument, not a fact. Q4.

---

## 8. Decisions taken, and questions still open

### 8.1 Settled by the owner (2026-08-29)

**D1 — What an undeclared command does. Three states, not two.** The command
path behaves identically in development and in production; the difference lives
in a test, not in the command path. (1) Declared → send the profile's bytes.
(2) Not declared and the profile knows the radio does not have the command →
report *unsupported by this radio* through
`profiles: RadioProfile.supports_command` and the reporting path consumers
already use for an unsupported capability — not an exception, not
log-and-continue. (3) Not declared and unknown → this state must not exist at
release; a test enumerates every builder against every profile and goes red on
any gap, and at runtime it behaves as (2), refusing aloud and logging, but
**never silently succeeding**.

The reasoning, recorded because it is the constraint and not a preference: a log
line in production means the command silently did nothing while the user
believes it worked — the same class of failure as the ACC1/MIC-gain collision
measured in §1.2. Silence is the failure mode being removed, so it cannot be the
fallback. An earlier framing of "crash in development, log in production" was
considered and set aside: the command path must not branch on environment, and
the only dev/production asymmetry belongs in the guard test.

Implemented by **Step 4**, which blocks every module migration.

**D2 — Radios nobody can measure: fill from documentation, and record the
source per entry.** Factory manuals and wfview rig definitions are acceptable
authority. Every filled entry must record where its value came from, **in the
TOML**, so that "verified on hardware" and "taken from a manual" are
distinguishable by reading the file rather than by remembering. This applies to
values that survive unchanged as much as to values that are corrected: a byte
with a recorded source is done, a byte without one is not, however plausible it
looks.

Two clarifications the owner added:

- **The manuals are all available online.** So no profile is blocked; the
  distinction D2 protects is between *confirmed on hardware* and *taken from a
  document*, not between answerable and unanswerable.
- **X6100 may be treated as X6200.** Its 189 gaps are largely fillable from
  `rigs/x6200.toml` rather than from a manual, and — as evidence §C5 records — X6100
  declares only 12 commands and no backend on its path reads its map at all. So
  this is one act of "write the profile that was never written", not 189
  decisions. The source recorded per entry is then `rigs/x6200.toml`, which is
  exactly the provenance D2 exists to make visible: an entry inherited from a
  sibling model reads differently from one confirmed on an X6100, and nobody
  has an X6100.

D2 also carries the sweep it implies: **every rig TOML is checked for further
copied blocks**, because `rigs/ic9700.toml` is known to carry one from
`rigs/ic7300.toml` (§5, class B) and blocks that happen not to diverge are
invisible to `tests/command_map_parity_divergences.txt` by construction.

Which radios can never be confirmed by measurement, stated plainly: **the live
bench is IC-7300 and FTX-1.** FTX-1 is not a CI-V radio and is unaffected. So of
the eight profiles, exactly one — IC-7300 — can be settled by pressing a button
and watching the radio. IC-7610 is retired, X6200 was destroyed, and IC-705,
IC-9700 and X6100 are not available. (Whether any of those three was ever on a
bench in the past is unknown and does not matter here — none is available now.)
Of the 76 divergences, 21 are bench-settleable and 55 are
documentation-settleable only.

### 8.2 Still open

Answerable without reading the rest of this document.

**Q3 — The reverse index, which turns out to have three customers, not one.**
`runtime/_civ_rx.py` decodes what the radio sends unprompted by comparing
against 105 hardcoded command/sub literals. Making that profile-driven needs a
**bytes-to-name** lookup; the code can only go name-to-bytes today
(`commands/command_map.py: CommandMap` offers `get`, `has`, `__iter__`, `__len__`
and nothing else). The single test double of evidence §C8 needs the same lookup, for the
same reason. So this is one piece of work with two production consumers and one
test consumer, not two unrelated ones — which changes the answer considerably
from "is the ingress decoder worth it".

*Measured, because the connection is only worth acting on if it survives
measurement:* a reverse index is **not injective**, and not marginally so — on
IC-7300, 142 of 177 wire tuples carry more than one name, and `1C 00` resolves
to four names on IC-705 and X6200. So the index alone answers neither consumer;
what closes the gap is a small per-language rule set written once per radio
language, not per model. Full figures and method in evidence §C9. The
connection therefore holds and is stronger than "same lookup": one deliverable
serves the ingress decoder, the test double, and any future consumer that must
interpret a frame it did not build.

**The question for you is still the scoping one:** is that deliverable part of
this programme, or its own? If it is separate, the end state of *this* document
is "every byte we **send** comes from the profile", which is narrower than
"every byte" and should be said out loud — and evidence §C8 stays unbuildable until the
separate one lands.

**Q4 — The temporary measurement hook.** It installs a logging wrapper over the
exported builders at import. `commands/LAYER.md` forbids I/O and import-time
mutation. Approve the exception for the duration (deleted in Step Z), or take
the slower route of instrumenting each of the 232 fallback branches by hand?

**Q5 — The IC-7610 default.** `resolve_radio_profile` falls back to the IC-7610
profile when nothing identifies the radio. Keep it, or fail closed once bytes
come only from profiles?

**Q6 — Release shape.** `cmd_map` becomes required in the public
`rigplane.commands` API. Minor version bump plus a changelog entry, or a
deprecation release first? rigplane-pro is measured clean (evidence §C7) and no other
consumer is evidenced.

**Q7 — The tuple contract.** Does a `[commands]` tuple stop at the sub-command,
or may it carry payload bytes? `rigs/x6100.toml` says one thing for `ptt_on` and
`rigs/ic7300.toml` says the other. The answer decides whether Step 2 shortens
the X6100 rows or changes `commands/_frame.py: _build_from_map`.

**Q8 — One carrier or two.** evidence §C5: the object that carries commands from a
profile to a radio handles only `CivCommandSpec`, while the profile schema
defines two spec kinds and the profiles use both. Should the carrier become
generic over `CommandSpec` — one mechanism, two spec kinds — or should each
family keep its own path with the *pattern* shared rather than the code?

*Which the evidence favours, stated as input rather than as a decision:*
**per-family paths, pattern shared.** Three reasons. The per-family dispatch
already exists and works (`backends/factory.py: create_radio`, evidence §C5). The two
families differ in what the profile carries — a whole command versus an address
(evidence §C4) — so a generic carrier's two branches would share the name lookup and
nothing else. And what is genuinely worth unifying is narrow: resolution by name
and refusal when undeclared, which is D1, and which can be stated once without
merging the carriers.

*The counter-evidence, which is real:* `RigConfig.to_command_map` silently drops
CAT specs, so "this profile declares nothing" and "this profile speaks another
language" are indistinguishable to every caller. That is a defect whichever way
Q8 goes, and it is fixable without a generic carrier — by making the drop
explicit in the method's name or its result type.

---

## Where the rest of this lives

Every figure quoted above was measured, and the method for each is recorded in
the companion's appendix rather than here, so a future reader can re-run them
instead of trusting them:

`docs/plans/2026-08-29-profile-driven-command-bytes-evidence.md`

| You want | Go to |
|---|---|
| Why the fallback is not simply "the IC-7610 profile" | evidence §C1 |
| What `profile_builder_map_gaps 1006` actually counts | evidence §C2 |
| The documents and comments that claim this already worked | evidence §C3 |
| The Yaesu reference implementation, read in full | evidence §C4 |
| Why two profiles have empty command maps | evidence §C5 |
| The mechanism search, and all three candidates argued | evidence §C6 |
| What the public-API break costs, measured | evidence §C7 |
| One test double instead of three, and the blind spot it closes | evidence §C8 |
| The third population of hardcoded bytes, and the reverse index | evidence §C9 |
| How every number was obtained | evidence appendix |

Neither document is complete on its own. This one tells you what to do; that one
tells you why, and lets you check that the why is true.
