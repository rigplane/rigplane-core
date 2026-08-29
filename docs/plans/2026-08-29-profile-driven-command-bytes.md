# Profile-Driven Command Bytes — Deleting the Hardcoded Fallback

**Date:** 2026-08-29
**Status:** Proposed (design only — this document's own commit changes no code,
no test and no profile). **Size:** this document crosses the repository's
1000-changed-line hard ceiling for a single PR. That ceiling is not
author-waivable; the exception is requested in the PR, with the alternative
(splitting §1.9/§1.10/§10 into a companion document) named there.
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
**Two further owner rulings, settled 2026-08-29 and folded in as §9.1:**
what an undeclared command does (**D1**, three states, implemented by Step 4),
and how the profiles of radios nobody can measure get filled (**D2**, from
documentation, with the source recorded per entry). Q3–Q8 in §9.2 remain open.
**The spine of this plan, in one line:** do on the Icom side what already
works on the Yaesu side (§1.9).

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
works on the Yaesu side** (§1.9), and every mechanism choice below is measured
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

### 1.3 The fallback is not "the IC-7610 profile"

*Inference, from measured data:* the fallback is often described as an IC-7610
value set. That is close but not exact, and the difference matters when someone
proposes "just make IC-7610 the default". `tests/command_map_parity_divergences.txt`
records 13 rows where the fallback disagrees with the *IC-7610* profile's own
map — for example `get_civ_transceive`, where the IC-7610 map says
`1A 05 01 12` and the fallback emits `1A 05 01 29`, and `get_civ_output_ant`,
where the map says `1C 04` and the fallback emits `1A 05 01 30`. The fallback
matches no shipped profile exactly.

### 1.4 Who actually calls a builder

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

### 1.5 The response half is bigger than the request half in one place

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

### 1.6 Where the belief that this already worked came from

`docs/api/commands.md` states, at `a2de2ab0`:

> **CLI / Radio API** — `cmd_map` is wired automatically from the active rig
> profile. You don't need to pass it manually.

*Observed:* false. Nothing wires it. The same document also says "All 223
command builder functions accept an optional `cmd_map`" (the measured number is
232) and prints a `_build_from_map` signature — three arguments, returning a
tuple — that does not match `commands/_frame.py: _build_from_map`, which takes
seven and returns `bytes`. Rewriting this document is part of the final step,
not an afterthought: it is the artefact that made a wrong mental model
plausible.

*Sweeping the class — it is not the only such claim.* Two more, both *observed*
at `a2de2ab0`:

- `backends/factory.py: create_radio` carries a comment on the X6200 branch
  saying "The actual command set comes from the loaded `rigs/x6200.toml` via the
  `model="X6200"` argument". The profile does drive capability gating, cmd29
  routes and filter rules; it does **not** drive the command bytes, because
  nothing passes the map. The sentence is true of everything except the thing it
  names.
- The rig schema declares the protocol family per profile — `[protocol] type`,
  loaded and validated by `profiles/rig_loader.py` and carried onto
  `RadioProfile.protocol_type` — and the code that *chooses* the family ignores
  it. `backends/factory.py: create_radio` dispatches on a hardcoded
  `_YAESU_MODELS` set plus `model.startswith("FT")`. The only production reader
  of `protocol_type` is `cli/_validate.py`.

Both are the same shape as the subject of this document: a fact that lives in
the profile, and code beside it that hardcodes the same fact. Neither is in
scope here; both are recorded so the next reader does not have to rediscover
that the pattern is wider than the builders.

### 1.7 The safety net already exists

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

### 1.8 How much profile data is actually missing

`profile_builder_map_gaps 1006` is the number most likely to be misread, and it
has been misread once already in the course of preparing this document. It does
**not** mean "1006 commands need profile entries".

*Observed*, from `tests/test_command_map_parity.py: _report` at `a2de2ab0`: that
census counts `(profile, builder)` pairs, where a builder is a
`(module file, function name)` key. The `gap` rows in
`tests/command_map_parity_uncovered.txt` count something else — `(profile,
command name)` pairs — and there are **871** of those, over **201 distinct
command names**. The two differ because several builders can resolve the same
command name — *observed*: `vfo.py: set_dual_watch`, `vfo.py: set_dual_watch_on`
and `vfo.py: set_dual_watch_off` all look up the single name `set_dual_watch`,
so one missing name counts once in the 871 and three times in the 1006. I did
not reconcile the two figures pair by pair; what matters is that neither is a
count of work items, and 1006 is the larger and less meaningful of them.

The uncovered file's own header warns that a command lands in a `gap` row for
either of two reasons — the TOML omits it, **or** the TOML declares it as a CAT
command, which `profiles/rig_loader.py: RigConfig.to_command_map` drops — and
that gap rows are a worklist only for profiles with no `cat-only` row.

*Observed*, by loading every profile at `a2de2ab0` and counting
`CatCommandSpec` against `CivCommandSpec`:

| Profile | Commands declared | of which CAT | Distinct gap command names | What that means |
|---|---|---|---|---|
| IC-7300 | 319 | 0 | **35** | CI-V, **on the bench** — the tractable worklist |
| IC-7610 | 208 | 0 | 18 | CI-V, retired radio |
| IC-9700 | 323 | 0 | 31 | CI-V, radio not available |
| IC-705 | 234 | 0 | 43 | CI-V, radio not available |
| X6200 | 62 | 0 | 153 | CI-V, radio destroyed |
| X6100 | 12 | 0 | 189 | CI-V, radio not available; the profile is nearly empty |
| FTX-1 | 108 | 108 | 201 | **Artefact** — every command is CAT, so the map is empty by construction; `[protocol] type = "yaesu_cat"`, never reaches a CI-V builder |
| TX-500 | 50 | 50 | 201 | **Artefact** — same, `[protocol] type = "kenwood_cat"` |

Read the table this way: **402 of the 871 name-pairs are the two non-CI-V
profiles and are not work at all**, for the reason given in §1.10. Of what
remains, the one radio that can be confirmed by measurement has **35** missing
commands. X6100's 189 is not 189 problems either — that profile declares 12
commands in total, so it is closer to "this profile was never written" than to
"this profile has gaps", and §9.1 D2 rules that it is filled from
`rigs/x6200.toml` rather than from a manual.

### 1.9 The target architecture already exists here — on the Yaesu side

This is the most important fact in the document and it was missing from the
first draft, which read as if the design were being invented.

*Observed* at `a2de2ab0`, reading `backends/yaesu_cat/radio.py`:

- `YaesuCatRadio.set_freq` picks a command **name** — `"set_freq"` or
  `"set_freq_sub"` — and calls `YaesuCatRadio._write(cmd_name, freq=freq)`.
  There is no byte, no template and no table in that method.
- `YaesuCatRadio._write` resolves the spec from the profile via
  `YaesuCatRadio._get_spec`, formats the profile's own template through
  `backends/yaesu_cat/parser.py: format_command`, and writes it.
- `YaesuCatRadio._query` does the mirror for reads, and — this is the part the
  CI-V plan has to copy — resolves the **response parser by the same name**:
  `self._parsers[cmd_name]`, a dict built at construction from each spec's
  `parse` template. One name yields both the request and the reply shape.
- When the profile does not declare the command, it refuses aloud. `_get_spec`
  raises `CommandError(f"Command {name!r} not found in profile ...")`; `_write`
  raises `CommandError(f"Command {cmd_name!r} has no write template")`; `_query`
  raises the read and parse equivalents.
- The only thing `backends/yaesu_cat/` imports from `commands/` is
  `hz_to_table_index, table_index_to_hz` — two pure arithmetic helpers. No
  builder, no `CommandMap`, no `cmd_map`.

Read the refusal line again against §9.1 D1: *"refuse aloud, never silently
succeed"* is not a ruling waiting to be implemented. It is implemented, it
ships, and one of the two radio families already behaves that way. What D1 does
is extend it to the other family.

**What differs between the families, and it is a property of the data, not of
the code.** A Yaesu spec carries the *whole* command — `CatCommandSpec` holds
`read`, `write` and `parse` format templates (`commands/command_spec.py`), so
`format_command` needs no per-command Python and name-only dispatch suffices.
An Icom spec carries only the *address* — `CivCommandSpec` holds a tuple of wire
bytes — while the encoding lives in 232 builder functions (BCD, the CW-pitch
curve, the filter index tables). So the CI-V side cannot collapse to
`_write(name, **kwargs)`; a call site must still reach a specific builder. That
asymmetry is real and is not a defect to fix here.

**What should be common, and is what this plan buys:** resolution by name from
the profile, one name serving both halves, and refusal when undeclared.

### 1.10 The command carrier is CI-V-shaped, and that is why two profiles look empty

§1.8 called FTX-1's and TX-500's empty command maps an artefact of how the
census counts. That was too kind, and it is corrected here.

*Observed:* `profiles/rig_loader.py: RigConfig.to_command_map` says so in its own
docstring — "Only CivCommandSpec entries are included; CatCommandSpec entries
are ignored" — and its body filters on `isinstance(spec, CivCommandSpec)`.
`commands/command_spec.py` defines both spec kinds and the `CommandSpec` union,
and the profiles declare both: FTX-1 declares 108 commands, every one CAT;
TX-500 declares 50, every one CAT (measured in §1.8's table).

So the two "empty" maps are not missing data and not merely a counting artefact:
**the carrier that takes commands from a profile to a radio speaks only one of
the two languages the profile schema supports.** It is harmless today for
exactly one reason — §1.9 — the Yaesu path never consults the map. But a caller
holding a `CommandMap` cannot tell "this profile declares nothing" from "this
profile speaks a language this object does not carry", and both read as an empty
map.

*The per-family dispatch that would make this a non-problem already exists*, and
it is not another loader: `backends/factory.py: create_radio` selects
`YaesuCatRadio` for Yaesu models and one of four Icom serial classes otherwise.
What is missing is not a second loader — it is that the object carrying commands
is shaped for one family. Whether it should become generic over `CommandSpec` or
stay per-family with only the *pattern* shared is Q8 (§9.2); the evidence I have
favours per-family, and I say why there rather than deciding it.

*Two consequences that are not hypothetical:*

- **X6100 is unreachable from any CI-V builder path.** `create_radio` has no
  branch for it — it is not in `_YAESU_MODELS`, does not start with `FT`, and is
  not one of the four supported serial models, so it hits the explicit
  "Unsupported serial model" refusal. `rigs/x6100.toml` records this itself, in
  a comment under its empty `[tx_policy]`: "X6100 has no serial backend:
  backends/factory.py refuses the model, so the only path to this radio is the
  rigctld client", and "No backend on this radio's path reads this map today".
  *Verified:* `backends/rigctld_client/` imports nothing from `commands/` and
  builds no CI-V frame. So X6100's two divergence rows are unreachable in
  production, and its 189 gaps are gaps in data nothing reads.
- The residual way an X6100 profile could still be selected is
  `profiles: resolve_radio_profile` matching on `civ_addr`, which is `0x70` for
  X6100 — the address in its divergence rows. That path needs someone to
  construct a CI-V radio with that address explicitly.

---

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
- The three states of D1 (§9.1) for a command a profile does not declare,
  expressed through the gate that already exists
  (`profiles: RadioProfile.supports_command`), not a new one — and a test that
  makes the third state ("not declared and unknown") impossible to ship.
- A recorded source for every wire byte in every rig TOML, per D2 (§9.1), so a
  reader can tell a value confirmed on hardware from one taken out of a manual
  without asking anyone.
- **One sentence that is true of both radio families**, where today it is true
  of one: *the command comes from the profile, is reached by name, and is
  refused aloud if the profile does not declare it.* That sentence already
  describes `backends/yaesu_cat/radio.py` (§1.9). Arrival is when it also
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

### 3.1 What was searched before proposing anything new

Per the repo's scope rule, three searches with different vocabulary, at
`a2de2ab0`:

1. `cmd_map=` across `src/` — zero hits outside `commands/`. No binder exists.
2. `to_command_map` callers — two in production: `profiles/rig_loader.py:
   RigConfig.to_command_map` (the only builder of a `CommandMap`) and
   `web/radio_poller.py: RadioPoller._load_command_map` (its only consumer,
   which flattens it to a plain dict for one command, `set_agc`, via
   `RadioPoller._send_cmd`).
3. `supports_command` / `command_names` — an existing declared-command gate.
   `profiles: RadioProfile.supports_command` is implemented and used in
   production exactly once (`runtime/radio.py`, for `set_filter_width`), and is
   declared on the `core/radio_protocol.py` Protocol with implementations in
   `runtime/radio.py`, `backends/yaesu_cat/radio.py` and
   `backends/rigctld_client/radio.py`. **D1 (§9.1) is implemented on top of it
   rather than on a second, parallel gate.**

Nearest existing thing to a binder: `RadioPoller._send_cmd`. It does not fit —
it bypasses the builders entirely and re-implements the wire decomposition, so
adopting it would move the duplication rather than remove it.

*Note, observed:* `profiles: RadioProfile` carries `command_names:
frozenset[str]` but **not** the wire bytes. So `CoreRadio` cannot obtain a
`CommandMap` from `self._profile` today; making one reachable is part of Step 3.

### 3.2 Candidate A — a bound command object (recommended)

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

*Measured, because "just use the function name" would have been wrong:* of the
231 public builders taking `cmd_map` that contain a literal key, **195 use a key
equal to their own function name and 34 do not** (2 more delegate to a shared
template without a literal). `commands/system.py: get_system_date` and
`set_system_date` both resolve `system_date`; all thirteen
`commands/scope.py: scope_set_*` setters resolve the corresponding `get_scope_*`
key; `commands/vfo.py: set_dual_watch_on` and `set_dual_watch_off` both resolve
`set_dual_watch`. So the key must be **exposed by each builder**, never derived
from its name — and a test that asserts every builder exposes exactly the key it
passes to `_build_from_map` is what keeps the two from drifting. Those 34 are
also the direct reason the two gap censuses in §1.8 differ.

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

### 3.3 Candidate B — required keyword argument at every call site (rejected)

Make `cmd_map` a required keyword-only parameter and write
`cmd_map=self._cmd_map` at all 233 request sites. No new type at all.

Keeps full mypy typing. Rejected because the response half has no analogue: the
65 matcher sites would each unpack `self._cmd_map.get(name)` into
command/sub/prefix by hand, producing a third and fourth copy of a decomposition
that should exist once, and leaving request and reply as two separate edits that
a reviewer must remember to keep in step. The owner has ruled that the two are
"the same movement". The matcher sites are also where the mistakes concentrate:
15 of the 65 carry a hand-written `prefix=`.

### 3.4 Candidate C — ambient binding via `contextvars` (rejected)

Set the map in a context variable at connect time; builders read it. Zero call
sites change.

Rejected: it is invisible. Nothing at the call site says where the bytes came
from, which re-creates the exact condition this work exists to remove
("nowhere left to get confused"). A builder called outside the context fails
with an error that names the context, not the command. It also conflicts with
`commands/LAYER.md`'s ban on module-level state.

### 3.5 Why a new abstraction is justified here

The repo forbids new abstractions unless the work requires them, and the
rule-of-three forbids generalizing over two coincidental call sites. The
justification here is scale and is stated rather than assumed: 232 builders ×
8 profiles, 233 request sites, 65 matcher sites, 105 unsolicited-decode
comparisons, and one wire-tuple decoder that already exists in two copies.
`BoundCommands` introduces no layer and no protocol; it is one object that holds
one map, and it is the single named place the owner asked to exist.

---

### 3.6 What the Yaesu reference changed, and what it did not

Written after §1.9 was found. Three changes, none of which overturns the
recommendation:

1. **It renames the thing.** §3.5 justified `BoundCommands` as a new abstraction
   needing an explicit scale argument. It is not new: `YaesuCatRadio` already
   holds its profile (`self._config`) and reaches commands through it. The
   binder is *what the radio holds*. `CoreRadio` holding a `CommandMap` is the
   CI-V instance of a pattern that ships and is exercised on the bench. The
   scale argument still holds; it is no longer the only argument.
2. **It fixed the response half's shape.** See §3.2 — `expect` takes the builder,
   not a string, because the Yaesu path names a command once and gets both
   halves. This is a correction to the earlier draft, not a refinement.
3. **It strengthens the rejection of Candidate B.** Yaesu passes no spec at any
   call site either; the profile is reached through the object that holds it.
   That rejection is now backed by a working example rather than by argument
   alone.

**What it cannot supply, and this is the honest limit of the analogy.** A Yaesu
spec is a format template, so `_write(name, **kwargs)` needs no per-command
Python and the profile is genuinely the whole command. An Icom spec is an
address; 232 builder functions hold the encoding. So name-only dispatch is not
available on the CI-V side and the call site must still reach a builder. Anyone
proposing to close that gap is proposing to move BCD encoders and filter tables
into TOML, which is a different and much larger question than this document
answers.

---

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
- `commands/bound.py` (new) — `BoundCommands` per §3.2, plus the single
  wire-tuple decoder extracted from `commands/_frame.py: _build_from_map` so
  both halves share it.
- `runtime/radio.py: CoreRadio.__init__` — construct it.

**Red if broken:** `tests/test_profile_command_binding.py` (new) — for every
CI-V profile in `rigs/`, the radio's bound command set is non-empty and its
names equal those of `RigConfig.to_command_map()` for that profile.

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
acceptable, and D1 (§9.1) settles what replaces them. Sizes are in §1.8: for the
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
state 2 different from state 3, and it needs no new concept: D2 (§9.1) already
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
  as D2 (§9.1) fills the profiles; until then it needs an explicit, shrinking
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
- Rewrite `docs/api/commands.md` — §1.6 lists three claims in it that are false
  at `a2de2ab0`.
- `tests/command_map_parity_divergences.txt` empty;
  `tests/command_map_parity_uncovered.txt` reports
  `dual_implementation_sites 0`.

**Red if broken:** `tests/test_command_map_parity.py` asserts both numbers.

*After Step Z, and only after it,* the single profile-driven test double
described in §10 becomes buildable. It is deliberately not a step here — see
the sequencing constraint in §10, which is the whole reason it sits outside the
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
**source is unknown**, which under D2 (§9.1) is itself the defect to fix. Each
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
  radio) is per radio, and the rows never cross profiles. Under D2 (§9.1) that
  person's output per row is a byte *and* a recorded source, and a row that ends
  up unchanged still counts as done once its source is written down.
- Class C is one person, once, because it is a contract, not data.
- IC-7300 first: it is the only profile whose class-A rows can be confirmed by
  pressing a button and watching a meter move. Its 35 undeclared commands
  (§1.8) are the same person's second task, and the only such list on this
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
and do not mean are in §1.8).

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

Of the 76 rows, 21 are bench-settleable and 55 are not. D2 (§9.1) rules that
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
`cmd_map`-taking builder. They never reach this code. §1.10 says why the maps
are empty in the first place, which is a narrower carrier rather than missing
data.

**The integration double cannot see any of the frames that change.** *Observed:*
`tests/mock_server.py: MockIcomRadio` dispatches on eight CI-V commands — `0x03`,
`0x04`, `0x05`, `0x06`, `0x11`, `0x14`, `0x15`, `0x16`, plus `0x1C` and the
`0x29` wrapper. It has **no `0x1A` branch and no `0x27` branch.** Every one of
the 44 `config.py` divergence rows and 16 `levels.py` rows is a `1A 05` menu
address, and the four `scope.py` rows are `0x27`. So the migration's entire
wire-level effect is invisible to that double: it will stay green through a
change that alters 76 frames. The parity test is the net here, not the
integration double — and §10 is where that gap gets closed, after the migration
rather than during it.

**The compatibility break.** §8.

**The measurement hook is in tension with its own layer charter.**
`commands/LAYER.md` forbids I/O and forbids import-time mutation. A logging
wrapper installed over the exported builders is arguably both. It is temporary
and deleted in Step Z, which is the argument for allowing it — but it is an
argument, not a fact. Q4.

---

## 8. What the compatibility break costs

**rigplane-pro: measured clean.** The owner granted access to the sibling
checkout and the searches were run there at the time of writing (this is a
private repository, so a future reader cannot re-run them from rigplane-core
alone). Two searches, with build, virtualenv and cache directories excluded:

1. `cmd_map` / `CommandMap` — **0 matches.**
2. `from rigplane.commands` / `import rigplane.commands` /
   `from rigplane import commands` — **0 matches.**

rigplane-pro does not merely fail to pass a command map; it never imports the
CI-V builders at all. What it does consume from core, by import count: around 65
from `rigplane.audio.backend` (two independent passes counted 64 and 65 — the
difference is not material and was not reconciled), 2 each from
`rigplane.dsp.exceptions`,
`rigplane.dsp.pipeline`, `rigplane.dsp.nodes.base` and `rigplane.discovery`, and
one each from `rigplane.cli`, `rigplane.cli._discover_hamlib`,
`rigplane.web.protocol`, `rigplane.audio.dsp`, `rigplane.audio._transcoder`,
`rigplane.backends.hamlib_probe`, `rigplane.backends.hamlib_models` and
`rigplane.backends.discovery`. Audio, DSP, discovery, CLI, the web protocol and
the hamlib probes — no `commands/` layer anywhere.

**Consequence:** write the cheap case. Making `cmd_map` required breaks nothing
in the sibling product, and no deprecation shim is built for a consumer nobody
has evidenced.

**What remains** is third parties who `pip install rigplane` and call the
builders directly. `docs/api/commands.md` documents `cmd_map` as a supported
optional parameter, so this is a changelog-and-version question: a version bump
and a release note saying the parameter is now required and the hardcoded
defaults are gone. Not an architectural one.

---

## 9. Decisions taken, and questions still open

### 9.1 Settled by the owner (2026-08-29)

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
  `rigs/x6200.toml` rather than from a manual, and — as §1.10 records — X6100
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

### 9.2 Still open

Answerable without reading the rest of this document.

**Q3 — The reverse index, which turns out to have three customers, not one.**
`runtime/_civ_rx.py` decodes what the radio sends unprompted by comparing
against 105 hardcoded command/sub literals. Making that profile-driven needs a
**bytes-to-name** lookup; the code can only go name-to-bytes today
(`commands/command_map.py: CommandMap` offers `get`, `has`, `__iter__`, `__len__`
and nothing else). The single test double of §10 needs the same lookup, for the
same reason. So this is one piece of work with two production consumers and one
test consumer, not two unrelated ones — which changes the answer considerably
from "is the ingress decoder worth it".

*What I checked, because the connection is only worth acting on if it survives
measurement.* A reverse index over a profile's CI-V commands is **not
injective**, and not marginally so. Measured at `a2de2ab0` by inverting each
profile's `CommandMap`: IC-7300's 319 CI-V commands occupy 177 distinct wire
tuples, 142 of which carry more than one name; at `(command, sub)` granularity —
which is what `_civ_rx.py` actually matches on — 95 keys, 60 colliding. IC-705
and X6200 reach four names on one tuple: `1C 00` is
`{get_transceiver_status, ptt_off, ptt_on, set_transceiver_status}`.

So a reverse index alone answers neither consumer. What closes the gap is a
small per-language rule set — payload absent means a read, payload `00`/`01`
means these two writes — written **once per radio language, not per model**,
which is precisely the category §10 already says the profile will never supply.
The connection therefore holds, and it is stronger than "same lookup": the
reverse index and the per-language rules are one deliverable serving the ingress
decoder, the test double, and any future consumer that must interpret a frame it
did not build.

**The question for you is still the scoping one:** is that deliverable part of
this programme, or its own? If it is separate, the end state of *this* document
is "every byte we **send** comes from the profile", which is narrower than
"every byte" and should be said out loud — and §10 stays unbuildable until the
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
deprecation release first? rigplane-pro is measured clean (§8) and no other
consumer is evidenced.

**Q7 — The tuple contract.** Does a `[commands]` tuple stop at the sub-command,
or may it carry payload bytes? `rigs/x6100.toml` says one thing for `ptt_on` and
`rigs/ic7300.toml` says the other. The answer decides whether Step 2 shortens
the X6100 rows or changes `commands/_frame.py: _build_from_map`.

**Q8 — One carrier or two.** §1.10: the object that carries commands from a
profile to a radio handles only `CivCommandSpec`, while the profile schema
defines two spec kinds and the profiles use both. Should the carrier become
generic over `CommandSpec` — one mechanism, two spec kinds — or should each
family keep its own path with the *pattern* shared rather than the code?

*Which the evidence favours, stated as input rather than as a decision:*
**per-family paths, pattern shared.** Three reasons. The per-family dispatch
already exists and works (`backends/factory.py: create_radio`, §1.10). The two
families differ in what the profile carries — a whole command versus an address
(§1.9) — so a generic carrier's two branches would share the name lookup and
nothing else. And what is genuinely worth unifying is narrow: resolution by name
and refusal when undeclared, which is D1, and which can be stated once without
merging the carriers.

*The counter-evidence, which is real:* `RigConfig.to_command_map` silently drops
CAT specs, so "this profile declares nothing" and "this profile speaks another
language" are indistinguishable to every caller. That is a defect whichever way
Q8 goes, and it is fixable without a generic carrier — by making the drop
explicit in the method's name or its result type.

---

## 10. After the migration: one imitation radio instead of three

**This is a target, not a step, and its position is the point.** It is written
after §4 deliberately: §4 lists work that is ordered and scheduled, and this is
neither yet.

### In plain words

The tests need a pretend radio to talk to. Today there are three of them, each
built only as deep as its first caller needed, and each carrying its own private
idea of what the commands are. The owner's goal is one pretend radio that can
imitate **all** radios. The argument is that a double needs exactly three
things: understand an incoming command, hold state (frequency, mode, PTT, per
receiver), and answer the way that radio would. If the commands live in the
profile, the double needs to know none of that itself — it reads **the same
profile the production code reads**, matches the incoming frame against the
declared commands, updates its state, and answers in the declared format. Swap
the profile and it imitates a different radio.

Say the symmetry out loud, because it is the strongest argument for it: **a
profile-driven command carrier and a profile-driven test double are the same
idea pointed in opposite directions.** One turns a name into bytes for a real
radio; the other turns bytes back into a name for a pretend one. Radio support
becomes data in both directions, which is the doctrine the whole document argues
for, applied to the test tree.

### The sequencing constraint — the part that must not be lost

**This is buildable only after the profile is the source of truth, not before.**
Built today, the double would have to hardcode the same bytes the migration
exists to delete, and the result would be a *fourth* partial double — exactly
the failure being removed. It comes after Step Z (§4).

### What is there now, measured

*Observed* at `a2de2ab0` unless stated:

| Double | Depth of imitation |
|---|---|
| `tests/serial_stub.py: SerialMockRadio` | Method-call level. Its `send_civ` is a documented no-op — no CI-V ingest at all. |
| `tests/mock_server.py: MockIcomRadio` | Real CI-V frames over UDP, but only eight commands dispatched (`0x03`, `0x04`, `0x05`, `0x06`, `0x11`, `0x14`, `0x15`, `0x16`, plus `0x1C` and the `0x29` wrapper); **no `0x07`, `0x1A` or `0x27`**; a single `self._frequency` with no MAIN/SUB split. |
| `tests/test_icom7610_serial_radio.py: _FakeSerialCivLink` | Records sent frames and supports scripted per-send responses; no state model. |

*And a fourth has already appeared.* On `e8fe6e45` (#2808, which is on `main`
and not in this branch's base), `tests/integration/_ptt_reread_fixtures.py`
adds `PttAnsweringSerialMockRadio`, a subclass of `SerialMockRadio`, whose own
docstring gives the reason: `SerialMockRadio` "has no CI-V ingest pipeline at
all (`send_civ` is a documented no-op)". **A correction to how this was reported
to me:** #2808 did not modify `tests/serial_stub.py` — that file was last
touched by #2331 — it added a new module beside it. The distinction matters
only because it makes the point sharper: the pattern is not "bolt another layer
onto the stub", it is "grow a new double whenever the existing ones are too
shallow", and it happened again this week.

That is the same disease as the production one: several partial implementations
of one mechanism, each grown to its caller's needs.

### The shared missing piece

The double needs **bytes → name**. So does `runtime/_civ_rx.py`. Neither exists
today. That is Q3 (§9.2), which is now a question about one deliverable with
three customers rather than about one decoder — including the measured finding
that the reverse index is one-to-many and needs a small per-language rule set
beside it.

### What the profile will never give you

Recorded so nobody is surprised later. None of these is a profile entry, and all
of them are written **once per radio language** (Icom-like, Yaesu-like), not per
model:

- how a radio answers a command it does not recognise (a NAK);
- response timing, and what happens on silence;
- frames a radio emits unprompted.

And tests that deliberately simulate a **broken** radio — NAK, silence, a
dropped link — need per-test scripted responses. That is a feature of the single
double, not an argument for keeping several.
`tests/test_icom7610_serial_radio.py: _FakeSerialCivLink` already supports
scripted per-send responses (`_responses`, `_responses_by_send`, `sent_frames`),
and **that is the shape to generalise**: profile-driven behaviour by default,
with a per-test override that pre-empts it.

### The cost, plainly

The existing tests are written against three different double APIs. Converging
means rewriting call sites — real, one-off work — in exchange for ending the
pattern where each new need grows a fourth variant. No estimate is offered here
and none should be inferred.

---

## Appendix — how the numbers in this document were obtained

All at `a2de2ab0`, in a clean worktree, so that a reader can re-derive them.

- Builder and constant counts: AST walk of `src/rigplane/commands/`, counting
  functions whose positional or keyword-only arguments include `cmd_map`.
- Call-site counts: AST walk of `src/rigplane/`, resolving `ImportFrom`
  aliases against `rigplane.commands`, counting only `ast.Call` nodes on an
  `ast.Name` bound to such an import. A plain grep over-counts by roughly a
  factor of two because radio *methods* share the builders' names.
- Matcher counts: AST walk for calls to `_get_bcd_level`, `_get_bool_value`,
  `parse_level_response`, `parse_bool_response` carrying a `command=` keyword;
  and separately for `ast.Compare` nodes whose left side is `.command` or
  `.sub` against an integer literal.
- The ACC1/MIC frame equality: executed, both builders, same address, printed
  hex compared.
- The IC-9700 copied block: `diff` of the 23 lines following the identical
  comment in the two TOMLs.
- Census figures: read from `tests/command_map_parity_uncovered.txt`, whose
  every number `tests/test_command_map_parity.py` re-measures and asserts; that
  test was run at `a2de2ab0` and passed (5 tests, 0.2s).
- Divergence grouping: the 76 non-comment rows of
  `tests/command_map_parity_divergences.txt`, cut by profile and by builder.
- Gap arithmetic (§1.8): `gap` rows of `tests/command_map_parity_uncovered.txt`
  — 201 rows, and 871 when the profile lists are split on commas — against the
  `profile_builder_map_gaps` census of 1006, whose unit is read from
  `tests/test_command_map_parity.py: _report` (`gap_pairs` is a set of
  `(model, Key)` where `Key` is `(module file, function name)`).
- Declared-command counts (§1.8): `profiles/rig_loader.py: discover_rigs` over
  `rigs/`, counting each config's commands by `isinstance(spec,
  CatCommandSpec)`. This is what establishes that no CI-V profile declares any
  CAT command, so for those six profiles a gap means the TOML omits the command
  and nothing else.
- Absence of an IC-9700 reference (§5, class B): listing
  `docs/validation/cat-audits/`, which holds `ftx1.md`, `ic7300.md`,
  `ic7610.md`, `tx500.md`, `x6200.md`, `x6200-unofficial.md` and `README.md`.
- Builder map keys (§3.2): AST walk of `src/rigplane/commands/`, collecting the
  string literal each public `cmd_map`-taking builder passes as
  `_build_from_map`'s second positional argument or as a `cmd_name=` keyword,
  and comparing it to the function's own name — 195 equal, 34 different, 0 with
  more than one key, 2 delegating without a literal.
- Reverse-index collisions (§9.2, Q3): loading each profile, inverting
  `RigConfig.to_command_map` into `wire tuple -> [names]` and separately into
  `(command, sub) -> {names}`, and counting keys with more than one name.
- Test-double depth (§10): reading each double, plus a `grep` of
  `tests/mock_server.py` for its `_CMD_*` constants and the `if cmd ==` branches
  that consume them. `_ptt_reread_fixtures.py` was read from `origin/main`
  (`e8fe6e45`), not from this branch, and is marked as such where it appears.
- The Yaesu path (§1.9): reading `backends/yaesu_cat/radio.py`
  (`set_freq`, `_get_spec`, `_write`, `_query`, `_parsers`),
  `backends/yaesu_cat/parser.py: format_command`, and `grep` for every
  `commands` import under `backends/yaesu_cat/` — one line, two arithmetic
  helpers.
- Readers of `protocol_type` (§1.6): `grep` across `src/` — declared and
  validated in `profiles/rig_loader.py`, carried on `RadioProfile`, and read in
  production only by `cli/_validate.py`.
