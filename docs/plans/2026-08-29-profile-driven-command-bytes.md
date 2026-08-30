# Profile-Driven Command Bytes — Deleting the Hardcoded Fallback

**Date:** 2026-08-29
**Status:** Proposed (design only — this document's own commit changes no code,
no test and no profile).
**Companion — read together, neither is complete alone:**
`docs/plans/2026-08-29-profile-driven-command-bytes-evidence.md`
carries the evidence, the measurements and the analysis that this plan acts on.
The owner ruled the material be split so each part would land inside the
10-file / 1000-changed-line hard ceiling. Review-required corrections since
then took this document past it on its own — 1063 changed lines against the
merge-base (`a2de2ab0`) at this head, measured with `git diff --numstat` —
and the owner granted an explicit exception for this document,
recorded as a PR comment dated 2026-08-29. **This document is the
instruction: what to do, in what order, and what goes red if a step is
wrong.** The companion is the
argument: why the plan is shaped this way, and how every figure in it was
obtained. Sections here cited as "evidence §Cn" live there.
**Base commit:** `a2de2ab0`. Every count, census and file claim below was
re-measured against that commit in a dedicated worktree, not carried forward
from an earlier round. `main` has since moved twice: to `e8fe6e45` (one
commit, `test(MOR-1900)` #2808, touching only `tests/integration/`), then to
`f793dd99` (`fix(MOR-2011)` #2817, routing the Yaesu reference's two
remaining hardcoded commands through the profile). The places either commit
is relevant are marked where they appear.
**Format model:** `docs/plans/2026-08-20-transmit-authority.md`
**Owner ruling this document implements (settled, not re-argued here):** the
hardcoded fallback and its constants are DELETED; the command map becomes
required; it must be structurally impossible to send a byte that did not come
from the profile.
**Two further owner rulings, settled 2026-08-29 and folded in as §8.1:**
what an undeclared command does (**D1**, three states, implemented by Step 4),
and how the profiles of radios nobody can measure get filled (**D2**, from
documentation, with the source recorded per entry). **Six more owner rulings,
settled the evening of the same day and folded into the same section:** Q3–Q8,
covering the reverse index's scope, the measurement hook's charter exception,
the IC-7610 default, the release shape, the tuple contract and the command
carrier. Nothing in §8 remains open.
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

The end state is one place that knows what bytes to send, and what shape to
expect back for the reply to a command it sent, and that place is the
profile. (Decoding a frame the radio sends unprompted is a related, larger
question this document does not answer — see §2 and §8.1 Q3.) This document
says how to get there without a flag day: what to measure first, what order
to move things in, which test turns red at each step, and what cannot be
checked at all because the radios involved are not on the bench.

The acceptance criterion, in the owner's own terms: he believed this binding was
*already* implemented, once, in one place. The work is done when a reader's
assumption that "of course the bytes come from the profile" is true, and when a
new call site cannot silently opt out of it.

**And he was half right, which changes what this plan is.** That assumption is
already true — for Yaesu radios, as of `f793dd99` (`fix(MOR-2011)` #2817).
`backends/yaesu_cat/radio.py: YaesuCatRadio` holds no wire bytes at all: it
names a command, the profile supplies the command text, and an undeclared
command is refused aloud. It ships, and it was exercised on the bench FTX-1
the afternoon this was written.

**It was not always true, and the gap was an instance of this plan's own
defect class, found living in its own reference implementation.** Before
`f793dd99`, two methods held command text in code and never reached
`_get_spec`: `get_if_status` sent the literal `"IF;"` and parsed the reply at
hand-picked offsets in Python, and `_read_meter` sent `f"RM{meter_type};"`
with the meter type hardcoded per caller. A bench measurement on the live
FTX-1 showed `get_if_status`'s hand-picked offsets were wrong — freq was read
at body offset 0, yielding 142 Hz for a 14.228 MHz dial, and the fields it
called `tx`/`split` held RX CLAR and the tone mode instead, with the scan
offset it skipped holding the actual VFO/memory select. `_read_meter`'s case
is the sharper instance of the defect: `rigs/ftx1.toml` already declared a
complete `get_meter` entry, read *and* parse, and the method ignored it —
the same fact held twice, profile and code, inside the class this plan holds
up as the finished target. `fix(MOR-2011)` routed both through
`_get_spec`/`_query`, so the profile is consulted and an absent entry now
refuses aloud (`CommandError`) — where before, neither method consulted the
profile at all: `get_if_status` sent its hardcoded `"IF;"` regardless and
mis-parsed the reply at the wrong offsets; `_read_meter` sent its hardcoded,
per-caller `RM{type};` and parsed the reply correctly, ignoring the
profile's own complete `get_meter` entry rather than failing on it.

So this document is not designing something new. It is a plan to **do on the
Icom side what already works on the Yaesu side** (evidence §C4), and every
mechanism choice below is measured against that working example rather than
argued from first principles.

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
| Private helpers taking `cmd_map` (10 in `commands/_builders.py`, 1 in `commands/_frame.py`) | 11 |
| `_CMD_*` / `_SUB_*` / `_CTL_*` byte constants in `commands/_frame.py` | 140 |
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
  describes `backends/yaesu_cat/radio.py`, as of `f793dd99` (evidence §C4; the
  two exceptions found there, `get_if_status` and `_read_meter`, were fixed
  by `fix(MOR-2011)` #2817 with bench verification on the live FTX-1).
  Arrival is when it also describes `runtime/radio.py`.

**Deleted:**

- Every `if cmd_map is not None:` branch and every hardcoded fallback in
  `src/rigplane/commands/`.
- The `_CMD_*` / `_SUB_*` / `_CTL_*` constants in `commands/_frame.py` that no
  longer have a reader (140 exist at `a2de2ab0`; re-measure at deletion time —
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

**Scope boundary, said out loud (Q3, §8.1):** this document's end state is
*every byte we **send** comes from the profile* — narrower than "every byte",
and stated here explicitly because the owner ruled the reverse direction out
of this programme. Decoding a byte the radio sends unprompted into a command
name is separate work, tracked as **MOR-1993**, which now blocks
**MOR-2010**.

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
| One literal key, different from the function's name | **34** | `commands/system.py: get_system_date` and `set_system_date` both resolve `system_date`; all eleven `commands/scope.py: scope_set_*` setters resolve the matching `get_scope_*`; `commands/vfo.py: set_dual_watch_on` and `set_dual_watch_off` both resolve `set_dual_watch` |
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
- `commands/LAYER.md` — record the temporary exception, approved and bounded
  by Q4 (§8.1).
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

### Step 2 — Apply the pinned wire-tuple contract, and fix the frames whose shape is wrong

*Why before anything moves:* right now a `[commands]` tuple has two meanings.
`rigs/x6100.toml` writes `ptt_on = [0x1C, 0x00, 0x01]`, including the payload
byte; `rigs/ic7300.toml` writes `ptt_on = [0x1C, 0x00]`, not including it. The
builder appends its own payload either way, so the X6100 map branch emits
`1C 00 01 01`. This is class C of §5, and Q7 (§8.1) settles which side is
right: a tuple carries the frame's full constant prefix, selector bytes
included, and the builder appends only what the caller supplies. X6100's
longer tuple already matches that contract; IC-7300's shorter one is the row
that needs to grow, and `commands/_frame.py: _build_from_map` changes once so
it stops assuming every tuple ends at the sub-command.

- `rigs/_schema_v2.md` — record the Q7 contract.
- `commands/_frame.py: _build_from_map` — the one change Q7's ruling requires.
- `profiles/rig_loader.py` — validate the contract at load.
- `rigs/ic7300.toml` — its `ptt_on`/`ptt_off` rows grow to carry the payload
  byte each always sends, so `_build_from_map`'s new contract does not turn a
  working frame into a short one; `rigs/x6100.toml`'s already carry it and
  are not touched.
- `commands/vfo.py: get_dual_watch`, `get_main_sub_band` — their `cmd_map`
  branch appends a selector byte the tuple already carries in full, doubling
  it (`07 C2 C2`). The fix is in the builder, not `rigs/ic7610.toml` /
  `rigs/ic9700.toml`, whose tuples are already the complete prefix and are
  not touched.
- `commands/scope.py: get_scope_center_type` — refuse its `receiver` keyword
  argument (MOR-1981, §5 class C): sub `0x1C` is outside
  `SCOPE_RECEIVER_SELECTOR_SUBS`, so the fallback's `27 1C 00` is a SET, not
  a bare read, and the `cmd_map` branch already sends the correct frame by
  omitting the selector. Removing the argument is a public builder signature
  change, not a tuple edit — `[0x27, 0x1C]` is unchanged in all four
  profiles.
- `tests/command_map_parity_divergences.txt` — delete the 10 class-C rows.

**Red if broken:** `tests/test_command_map_parity.py`. It fails if a class-C row
is deleted while the divergence persists, and equally if a row is left in place
after the divergence is fixed. Plus a new case in `tests/test_rig_loader.py` for
the loader validation.

**Rollback:** revert; the divergence rows come back and the test is green again.

**Split if it does not fit:** 2a = contract + `_build_from_map` + loader
validation + `_schema_v2.md`; 2b = the TOML, `vfo.py` and `scope.py` fixes
with the parity-file deletions.

**Bench dependency:** the fix to the doubled X6100 `ptt_on`/`ptt_off` bytes
alters bytes on the wire and is not on the bench (§7). The `scope.py` change
does not alter what production already sends — `runtime/_scope_runtime.py`
already reaches `receiver=` for exactly the eight eligible sub-commands and
no others, `get_scope_center_type` not among them — but the IC-7300
measurement behind it (`27 1C` reads, `27 1C 00` writes) is worth
reconfirming before the argument is removed.

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
  (re-measure; 140 exist at `a2de2ab0` and some are read by parsers).
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
these)` whose 24 following lines are byte-identical to the same block in
`rigs/ic7300.toml` — I diffed them, including the comment. 18 of the 20
IC-9700 rows come from that identical run. The other two,
`get_vox_delay`/`set_vox_delay`, come from the same block but sit just
outside the byte-identical run: the address matches (`1A 05 01 91`) but the
trailing comment does not (`ic9700.toml` carries a `NOT_IMPLEMENTED` note
`ic7300.toml` does not), so the two lines diverge as text even though the
value they declare does not.

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

### Class C — the tuple contract, pinned by Q7 (10 rows)

Not "which address" but "how many bytes belong in the tuple" — settled by Q7
(§8.1): a tuple carries the frame's full constant prefix, and the builder
appends only what the caller supplies. Three shapes, and what each needs
under the ruled contract:

- `scope.py: get_scope_center_type` (4 rows: IC-705, IC-7300, IC-7610, IC-9700)
  — sub `0x1C` is outside `SCOPE_RECEIVER_SELECTOR_SUBS`, the eight
  sub-commands whose CI-V read legitimately carries a receiver-selector byte
  (`commands/scope.py: _scope_selector_data`'s own docstring names the
  membership and this exception, backed by a live-IC-7300 measurement in
  `tests/test_state_queries.py`). The `cmd_map` branch already sends nothing
  for it, correctly; the **fallback** is the side at fault — `27 1C 00` is
  not a bare read, the radio answers it as a SET of center_type to 0
  (Filter center). Closing the divergence (MOR-1981) means refusing the
  `receiver` argument on the public `get_scope_center_type` signature, not
  touching either tuple: all four profiles already declare the same
  `[0x27, 0x1C]`, which is already the complete frame once that argument is
  gone.
- `vfo.py: get_dual_watch` and `vfo.py: get_main_sub_band` (2 rows each,
  IC-7610 and IC-9700) — the TOML puts the query byte in the tuple *and* the
  builder appends it, so the map branch emits it twice (`07 C2 C2`). Under Q7
  the tuple is already right; the builder is the one that needs to stop
  appending a byte the tuple already carries.
- `ptt.py: ptt_on` / `ptt_off` (2 rows, X6100) — `rigs/x6100.toml` includes the
  payload byte in the tuple where `rigs/ic7300.toml` does not. Under Q7,
  X6100's longer tuple is the one that already matches the contract;
  `rigs/ic7300.toml`'s shorter tuple is the one that needs to grow.

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
change to `CommandMap`'s surface, which this design was told not to reopen. Q3
(§8.1) settles it: the reverse index is a separate programme, tracked as
**MOR-1993**, which now blocks **MOR-2010**. The plan therefore stops at
population 2, and this document's end state is *every byte we **send** comes
from the profile* — narrower than "every byte" (§2). Leaving it is not free:
until MOR-1993 lands, unsolicited frames are still decoded against
IC-7610-era literals, so a radio whose profile disagrees will have its own
broadcasts misread even after every request and reply is profile-driven.

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
fix removes the `receiver` argument rather than adding a byte — adding one
would be the mistake, since on sub `0x1C` an extra byte is a write, not a
read, which is exactly why the `cmd_map` branch already omits it and the
fallback is the side at fault (§5, class C). That `27 1C` alone reads as a
bare GET, and that the retired `27 1C 00` really is answered as a SET, is
confirmable on the IC-7300 and must be.

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

**The IC-7610 default is removed (Q5, §8.1).** `profiles: resolve_radio_profile`
falls back to the IC-7610 profile when nothing identifies the radio — observed
in its body, with the comment "prefer IC-7610 (primary LAN reference rig)".
The owner ruled this fails closed: the silent default goes, an unidentified
radio refuses with a clear message instead of guessing, and a profile passed
explicitly by the caller remains the deliberate override it already is.

**FTX-1 and TX-500 are not stranded.** They are the two `cat_only_profiles`
whose `CommandMap` is empty, which looks fatal for a required map. It is not:
`rigs/ftx1.toml` declares `[protocol] type = "yaesu_cat"` and `rigs/tx500.toml`
`type = "kenwood_cat"`, `backends/factory.py` routes Yaesu models to
`YaesuCatRadio`, and *observed by AST*, `backends/yaesu_cat/` imports no
`cmd_map`-taking builder. They never reach this code. evidence §C5 says why the maps
are empty in the first place, which is a narrower carrier rather than missing
data.

**A green integration run is not evidence that a step was safe — and the
reason is narrower than "no branch".** *Observed:* `tests/mock_server.py:
MockIcomRadio` dispatches eight CI-V commands, including `0x14` and `0x1C`.
But its `0x14` branch handles only sub `0x0A` (RF power) and its `0x1C`
branch handles only sub `0x00` with an empty payload (PTT get) — anything
else on those two commands still NAKs. Of the 76 divergence rows, **54**
involve only commands the mock has no branch for at all — `1A 05` menu
addresses (24 in `config.py`, all 16 in `levels.py`, 6 of `vfo.py`'s 10),
`0x27` (all four `scope.py` rows), and `0x07` (`vfo.py`'s other 4 rows) — so
both sides NAK on the absent top-level branch. The other **22** involve a
side that dispatches into an *existing* mock branch and NAKs below the top
level, in three shapes: 18 `config.py` rows with a fallback side of `0x14`
(`14 11` lan_mod_level ×4, `14 10` usb_mod_level ×8, `14 0B` acc1_mod_level
×6 — the sub is never `0x0A`); 2 `config.py` rows with a map side of `1C 04`
(IC-7610 `get_civ_output_ant`/`set_civ_output_ant` — the sub is `0x04`, not
`0x00`); and 2 `ptt.py` rows (X6100 `ptt_on`/`ptt_off`) where **both** sides
are `1C 00` — the sub matches — but each carries a payload byte on the
fallback side (`01` for `ptt_on`, `00` for `ptt_off`), doubled on the map
side (`01 01` / `00 00`, that doubling being the separate class-C bug §5
describes), and the mock's `0x1C` branch only ACKs a payload-empty GET, so a
SET NAKs there too, on both sides. Either way, both
the map frame and the fallback frame NAK, so the integration doubles cannot
observe a single one of the 76 frames this migration changes, and will stay
green through all of them. "The mock has no `0x1A` branch" is not the reason
to reach for when `MockIcomRadio` is next touched — the safety net for every
step in §4 is `tests/test_command_map_parity.py` and the per-step tests named
there, never the integration suite.

The owner has ruled that the two missing command families are **not** patched
into the existing double now: the single profile-driven double closes the gap
without carrying any command list inside it, and adding two branches today would
deepen the hole being filled. Full measurement and the named closure are in
evidence §C8.

**The compatibility break.** Measured and costed in evidence §C7; the short
version is that it is cheap.

**The measurement hook's charter exception is approved, and bounded (Q4,
§8.1).** `commands/LAYER.md` forbids I/O and forbids import-time mutation,
and Step 1's logging wrapper is arguably both. The exception is recorded in
`commands/LAYER.md` with the date and this ticket, the hook stays confined to
the one file Step 1 already names, and it is deleted in Step Z — with a test
that goes red if it survives past that step.

---

## 8. Decisions taken — nothing left open

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

**Six more, settled the same day, later that evening.** These were open
questions (Q3–Q8) at the time D1 and D2 were ruled; the owner closed all six in
a second pass the same evening.

**Q3 — The reverse index is a separate programme.** `runtime/_civ_rx.py`
decodes what the radio sends unprompted by comparing against 105 hardcoded
command/sub literals. Making that profile-driven needs a **bytes-to-name**
lookup; the code can only go name-to-bytes today (`commands/command_map.py:
CommandMap` offers `get`, `has`, `__iter__`, `__len__`, `__repr__` and nothing
else). It is real, non-trivial work: a reverse index is **not injective**, and
not marginally so — on IC-7300, 142 of 177 wire tuples carry more than one
name, and `1C 00` resolves to four names on IC-705 and X6200 (evidence §C9).
The owner ruled it out of this programme's scope: it is tracked separately as
**MOR-1993**, which now blocks **MOR-2010**. This document's end state
narrows accordingly, and says so out loud: not "every byte", but **every byte
we send comes from the profile** (§2). Reading an unsolicited frame still
resolves against `runtime/_civ_rx.py`'s literals until MOR-1993 lands, and
evidence §C8's single test double stays unbuildable until then too, for the
same reason.

**Q4 — The temporary measurement hook's charter exception is approved,
bounded.** Step 1's logging wrapper installed over the exported builders at
import is a genuine exception to `commands/LAYER.md`'s ban on I/O and
import-time mutation. The owner approved it for the duration: recorded in
`commands/LAYER.md` with the date and this ticket, confined to the one file
Step 1 already names, and deleted in Step Z with a test that goes red if it
survives past that step.

**Q5 — The IC-7610 default fails closed.** `resolve_radio_profile`'s silent
fallback to the IC-7610 profile when nothing identifies the radio is removed.
An unidentified radio refuses with a clear message instead of guessing; a
profile passed explicitly by the caller remains the deliberate override it
already is.

**Q6 — Release shape: minor version bump, no deprecation release.** `cmd_map`
becomes required in the public `rigplane.commands` API with a changelog entry
and no intermediate deprecation cycle — rigplane-pro is measured clean
(evidence §C7) and no other consumer is evidenced. The error the
now-required `cmd_map` raises must itself explain what changed and what to do
about it, not just that a parameter is missing.

**Q7 — The tuple contract carries the full constant prefix.** A `[commands]`
tuple holds every constant byte of the frame, selector bytes included; only a
value the caller supplies at the call site is appended on top.
`rigs/x6100.toml`'s `ptt_on = [0x1C, 0x00, 0x01]`, which already carries the
trailing byte, is valid under the ruling; `rigs/ic7300.toml`'s shorter
`ptt_on = [0x1C, 0x00]` is the one that needs to grow. Consequence:
`commands/_frame.py: _build_from_map` changes once, to stop assuming a tuple
always ends at the sub-command. Step 2 (§4) and §5 class C are written to
this ruling.

**Q8 — Per-family carriers, one shared pattern.** The object that carries
commands from a profile to a radio stays split by family — CI-V and CAT keep
their own path — rather than becoming generic over `CommandSpec`. Three
reasons favoured this going in: the per-family dispatch already exists and
works (`backends/factory.py: create_radio`, evidence §C5); the two families
differ in what the profile carries — a whole command versus an address
(evidence §C4) — so a generic carrier's two branches would share the name
lookup and nothing else; and what is genuinely worth unifying is narrow:
resolution by name and refusal when undeclared, which is D1, and which is
stated once without merging the carriers.

Independently of which way this went, `RigConfig.to_command_map` silently
dropping CAT specs is a defect: "this profile declares nothing" and "this
profile speaks another language" must stop being indistinguishable to every
caller. The fix is to make the drop explicit in the method's name or its
result type — it does not need a generic carrier.

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
