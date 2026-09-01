# Profile-Driven Command Bytes — Evidence and Analysis

**Date:** 2026-08-29
**Status:** Companion document. Records measurement and argument; proposes no
work of its own and changes no code, test or profile.
**Plan this supports — read together, neither is complete alone:**
`docs/plans/2026-08-29-profile-driven-command-bytes.md`
**Base commit:** `a2de2ab0`. Every figure here was measured against that commit
in a dedicated worktree, not carried forward from an earlier round. `main` has
since moved twice: to `e8fe6e45` (one commit, `test(MOR-1900)` #2808, touching
only `tests/integration/`), then to `f793dd99` (`fix(MOR-2011)` #2817, routing
the Yaesu reference's two remaining hardcoded commands through the profile).
The places either commit is relevant are marked where they appear.
**Format model:** `docs/plans/2026-08-20-transmit-authority.md`

---

## C0. What this is, and why it is separate

The plan says what to do. This says why, and lets you check that the why is
true.

The two were one document. The owner ruled them split, choosing the split over
granting an exception to the repository's 10-file / 1000-changed-line hard
ceiling and over cutting the evidence. The boundary drawn is: **a reader who
wants to execute the migration should be able to read the plan alone and never
feel they are missing something they need in order to act.** Anything that is
evidence for why the plan is shaped as it is — census arithmetic, the
reference-implementation walkthrough, the reverse-index measurements, the test
doubles, the compatibility measurement — is here.

Every section here is cited from the plan as "evidence §Cn". Every section here
that bears on an instruction cites the plan back.

The appendix at the end records how each number was obtained, so a future
reader can re-run the measurement rather than trust the figure. That is the
whole reason this document exists as evidence rather than as prose.

---

## C1. The fallback is not "the IC-7610 profile"

*Inference, from measured data:* the fallback is often described as an IC-7610
value set. That is close but not exact, and the difference matters when someone
proposes "just make IC-7610 the default". `tests/command_map_parity_divergences.txt`
records 13 rows where the fallback disagrees with the *IC-7610* profile's own
map — for example `get_civ_transceive`, where the IC-7610 map says
`1A 05 01 12` and the fallback emits `1A 05 01 29`, and `get_civ_output_ant`,
where the map says `1C 04` and the fallback emits `1A 05 01 30`. The fallback
matches no shipped profile exactly.

---

## C2. How much profile data is actually missing

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
profiles and are not work at all**, for the reason given in §C5. Of what
remains, the one radio that can be confirmed by measurement has **35** missing
commands. X6100's 189 is not 189 problems either — that profile declares 12
commands in total, so it is closer to "this profile was never written" than to
"this profile has gaps", and plan §8.1 D2 rules that it is filled from
`rigs/x6200.toml` rather than from a manual.

---

## C3. Where the belief that this already worked came from

`docs/api/commands.md` states, at `a2de2ab0`:

> **CLI / Radio API** — `cmd_map` is wired automatically from the active rig
> profile. You don't need to pass it manually.

*Observed:* false. Nothing wires it. The same document also says "All 223
command builder functions accept an optional `cmd_map`" (the measured number is
232) and prints a `_build_from_map` signature — three arguments, returning a
tuple — that does not match `commands/_frame.py: _build_from_map`, which takes
seven and returns `bytes`. Rewriting `docs/api/commands.md` is part of the
plan's final step (plan Step Z), not an afterthought: it is the artefact that
made a wrong mental model plausible.

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

---

## C4. The target architecture already exists here — on the Yaesu side

This is the most important fact in either document, and it was missing from the
first draft of the plan, which read as if the design were being invented.

*Observed* at `a2de2ab0`, reading `backends/yaesu_cat/radio.py`:

- `YaesuCatRadio.set_freq` picks a command **name** — `"set_freq"` or
  `"set_freq_sub"` — and calls `YaesuCatRadio._write(cmd_name, freq=freq)`.
  There is no byte, no template and no table in that method.
- `YaesuCatRadio._write` resolves the spec from the profile via
  `YaesuCatRadio._get_spec`, formats the profile's own template through
  `backends/yaesu_cat/parser.py: format_command`, and writes it.
- `YaesuCatRadio._query` does the mirror for reads, and — this is the part the
  CI-V plan has to copy — resolves the **response parser by the same name**:
  `parser = self._parsers.get(cmd_name)`, followed by a `None`-check that
  raises `CommandError` if the profile declared no parse template. The dict
  is built at construction from each spec's `parse` template. One name yields
  both the request and the reply shape.
- When the profile does not declare the command, it refuses aloud. `_get_spec`
  raises `CommandError(f"Command {name!r} not found in profile ...")`; `_write`
  raises `CommandError(f"Command {cmd_name!r} has no write template")`; `_query`
  raises the read and parse equivalents.
- The only thing `backends/yaesu_cat/` imports from `commands/` is
  `hz_to_table_index, table_index_to_hz` — two pure arithmetic helpers. No
  builder, no `CommandMap`, no `cmd_map`.

**The honest limit, found in this same class and fixed by the time of
writing.** At `a2de2ab0`, two methods did not yet fit the description above:
`YaesuCatRadio.get_if_status` sent the literal `"IF;"` and parsed the reply
at hand-picked offsets in Python, and `YaesuCatRadio._read_meter` sent
`f"RM{meter_type};"` with the meter type hardcoded per caller — even though
`rigs/ftx1.toml` already carried a complete `get_meter` entry, read *and*
parse, that the method never consulted. Both are instances of the defect
class this whole document is about, found living in the reference
implementation it holds up as the target. `fix(MOR-2011)` (#2817, `f793dd99`)
routed both through `_get_spec`/`_query`; a bench measurement on the live
FTX-1 taken as part of that fix showed `get_if_status`'s old hand-picked
offsets were wrong — freq was read at body offset 0 (142 Hz for a
14.228 MHz dial), and the fields it called `tx`/`split` held RX CLAR and the
tone mode instead, with the VFO/memory select sitting at the offset the old
parser skipped entirely. As of `f793dd99`, the bullets above are true without
exception.

Read the refusal line again against plan §8.1 D1: *"refuse aloud, never silently
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

---

## C5. The command carrier is CI-V-shaped, and that is why two profiles look empty

§C2 called FTX-1's and TX-500's empty command maps an artefact of how the
census counts. That was too kind, and it is corrected here.

*Observed:* `profiles/rig_loader.py: RigConfig.to_command_map` says so in its own
docstring — "Only CivCommandSpec entries are included; CatCommandSpec entries
are ignored" — and its body filters on `isinstance(spec, CivCommandSpec)`.
`commands/command_spec.py` defines both spec kinds and the `CommandSpec` union,
and the profiles declare both: FTX-1 declares 108 commands, every one CAT;
TX-500 declares 50, every one CAT (measured in §C2's table).

So the two "empty" maps are not missing data and not merely a counting artefact:
**the carrier that takes commands from a profile to a radio speaks only one of
the two languages the profile schema supports.** It is harmless today for
exactly one reason — §C4 — the Yaesu path never consults the map. But a caller
holding a `CommandMap` cannot tell "this profile declares nothing" from "this
profile speaks a language this object does not carry", and both read as an empty
map.

*The per-family dispatch that would make this a non-problem already exists*, and
it is not another loader: `backends/factory.py: create_radio` selects
`YaesuCatRadio` for Yaesu models and one of four Icom serial classes otherwise.
What is missing is not a second loader — it is that the object carrying commands
is shaped for one family. Whether it should become generic over `CommandSpec` or
stay per-family with only the *pattern* shared is Q8 (plan §8.1), settled in
favour of the latter — the evidence below is why.

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

---

## C6. The mechanism: what was searched, and all three candidates

The plan states the recommendation (plan §3.1) and the two rejections
(plan §3.2, plan §3.3). This section carries the search that established no
binder already exists, the scale argument for adding one, and the
re-examination of all three against the working Yaesu implementation.

### C6.1 What was searched before proposing anything new

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
   `backends/rigctld_client/radio.py`. **D1 (plan §8.1) is implemented on top of it
   rather than on a second, parallel gate.**

Nearest existing thing to a binder: `RadioPoller._send_cmd`. It does not fit —
it bypasses the builders entirely and re-implements the wire decomposition, so
adopting it would move the duplication rather than remove it.

*Note, observed:* `profiles: RadioProfile` carries `command_names:
frozenset[str]` but **not** the wire bytes. So `CoreRadio` cannot obtain a
`CommandMap` from `self._profile` today; making one reachable is part of Step 3.

### C6.2 Why a new abstraction is justified here

The repo forbids new abstractions unless the work requires them, and the
rule-of-three forbids generalizing over two coincidental call sites. The
justification here is scale and is stated rather than assumed: 232 builders ×
8 profiles, 233 request sites, 65 matcher sites, 105 unsolicited-decode
comparisons, and one wire-tuple decoder that already exists in two copies.
`BoundCommands` introduces no layer and no protocol; it is one object that holds
one map, and it is the single named place the owner asked to exist.

---

### C6.3 What the Yaesu reference changed, and what it did not

Written after §C4 was found. Three changes, none of which overturns the
recommendation:

1. **It renames the thing.** §C6 justified `BoundCommands` as a new abstraction
   needing an explicit scale argument. It is not new: `YaesuCatRadio` already
   holds its profile (`self._config`) and reaches commands through it. The
   binder is *what the radio holds*. `CoreRadio` holding a `CommandMap` is the
   CI-V instance of a pattern that ships and is exercised on the bench. The
   scale argument still holds; it is no longer the only argument.
2. **It fixed the response half's shape.** See plan §3.1 — `expect` takes the builder,
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

---

## C7. What the compatibility break costs

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

---

## C8. After the migration: one imitation radio instead of three

**This is a target, not a step, and its position is the point.** It is written
after plan §4 deliberately: plan §4 lists work that is ordered and scheduled, and this is
neither yet.

### C8.1 In plain words

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

### C8.2 The sequencing constraint — the part that must not be lost

**This is buildable only after the profile is the source of truth, not before.**
Built today, the double would have to hardcode the same bytes the migration
exists to delete, and the result would be a *fourth* partial double — exactly
the failure being removed. It comes after Step Z (plan §4).

### C8.3 What is there now, measured

*Observed* at `a2de2ab0` unless stated:

| Double | Depth of imitation |
|---|---|
| `tests/serial_stub.py: SerialMockRadio` | Method-call level. Its `send_civ` is a documented no-op — no CI-V ingest at all. |
| `tests/mock_server.py: MockIcomRadio` | Real CI-V frames over UDP, but only eight top-level command branches (`0x03`, `0x04`, `0x05`, `0x06`, `0x14`, `0x15`, `0x1C`, `0x29`); `0x11` and `0x16` are reachable only as the `0x29` wrapper's two recognised inner commands, not standalone; **no `0x07`, `0x1A` or `0x27`** at any level; a single `self._frequency` with no MAIN/SUB split. |
| `tests/test_icom7610_serial_radio.py: _FakeSerialCivLink` | Records sent frames and supports scripted per-send responses; no state model. |

*And a fourth has already appeared.* On `e8fe6e45` (#2808, which is on `main`
and not in this branch's base), `tests/integration/_ptt_reread_fixtures.py`
adds `PttAnsweringSerialMockRadio`, a subclass of `SerialMockRadio`, whose own
docstring gives the reason: `SerialMockRadio` "has no CI-V ingest pipeline at
all (`send_civ` is a documented no-op)". **One correction worth recording,
because the intuitive version is wrong and will be re-derived:** #2808 did not
modify `tests/serial_stub.py` — that file was last touched by #2331 — it added
a new module beside it. The distinction sharpens the point rather than softening
it: the pattern is not "bolt another layer onto the stub", it is "grow a new
double whenever the existing ones are too shallow", and it happened again this
week.

That is the same disease as the production one: several partial implementations
of one mechanism, each grown to its caller's needs.

### C8.4 The blind spot it closes, and the owner's ruling on closing it early

*Measured at `a2de2ab0`, by reading `tests/mock_server.py`'s `_CMD_*` constants
and the `if cmd ==` branches that consume them:* `MockIcomRadio` has **eight**
top-level branches — `0x03`, `0x04`, `0x05`, `0x06`, `0x14`, `0x15`, `0x1C`,
`0x29` (`0x11` and `0x16` exist only as the `0x29` wrapper's two recognised
inner commands, not standalone). Dispatch inside those branches is narrower
than the top level, and this is the part the earlier draft of this section
got wrong: the `0x14` branch handles only sub `0x0A` (RF power), and the
`0x1C` branch handles only sub `0x00` with an empty payload (PTT get) —
everything else on those two commands still NAKs. It has **no `0x1A` branch
and no `0x27` branch** at all.

Now put that against the divergence file. **This is where §C1 and an earlier
version of this section disagreed — §C1 is the one that reproduces.** And an
earlier version of *this* correction still undercounted: it folded in the
`config.py` rows but missed that `ptt.py`'s rows also land inside an existing
branch. Classifying all 76 rows by command and sub on both sides: **54** put
both the map and the fallback side on a command the mock has no branch for at
all — `1A 05` menu addresses (24 in `config.py`, all 16 in `levels.py`, 6 of
`vfo.py`'s 10 rows — **46** rows on `1A 05` alone), `0x27` (all four
`scope.py` rows), and `0x07` (`vfo.py`'s other 4 rows) — so both sides NAK on
the absent top-level branch. The other **22** put at least one side inside
the `0x14`/`0x1C` branches above, in three shapes: 18 `config.py` rows with
a fallback side of `0x14` (`14 11` lan_mod_level ×4, `14 10` usb_mod_level
×8, `14 0B` acc1_mod_level ×6 — the sub is never `0x0A`) whose map side is
still the absent `1A 05`; 2 `config.py` rows with a map side of `1C 04`
(IC-7610 `get_civ_output_ant`/`set_civ_output_ant`, the same row §C1
describes — the sub is `0x04`, not `0x00`) whose fallback side is still the
absent `1A 05`; and 2 `ptt.py` rows (X6100 `ptt_on`/`ptt_off`) where **both**
sides are `1C 00` — the sub matches — but each carries a payload byte on the
fallback side (`01` for `ptt_on`, `00` for `ptt_off`), doubled on the map
side (`01 01` / `00 00`, that doubling being the separate class-C bug plan
§5 describes), and the mock's `0x1C` branch (`if sub == _SUB_PTT and not
rest`) only ACKs a payload-empty GET, so a SET NAKs there too, on both
sides. Of those 22, only the 2 `ptt.py` rows NAK inside an existing branch on
**both** sides; the other 20 NAK on one side inside an existing branch and
on the other side on the absent `1A 05` branch. Either way, both the map
frame and the fallback frame NAK, so **the integration doubles cannot
observe a single one of the 76 frames this migration changes.** They will
stay green through every step of it.

The consequence a reader must not miss: **a green integration run is not
evidence that a migration step was safe** — and "the mock has no `0x1A`
branch" is not, by itself, the whole reason: alone it explains the **46**
rows where both sides are `1A 05` (24 `config.py` + 16 `levels.py` + 6
`vfo.py`). Accounting for all **54** rows that NAK purely on an absent
top-level branch also needs "no `0x27` branch" (4 `scope.py` rows) and "no
`0x07` branch" (`vfo.py`'s other 4 rows); the remaining 22 need the finer,
existing-branch mechanism above. The safety net for plan §4 is
`tests/test_command_map_parity.py` and the per-step tests that plan names —
the wire-level ones — never the integration suite.

**The owner has ruled the two missing families are not patched in now.** The
single profile-driven double closes this without carrying any command list
inside it; adding `0x1A` and `0x27` branches to `MockIcomRadio` today would put
two more hardcoded command families into a test double, deepening the exact
hole being filled. So this is recorded as a **known blind spot with a named
closure** — the closure is C8, and C8 comes after plan Step Z.

### C8.5 The shared missing piece

The double needs **bytes → name**. So does `runtime/_civ_rx.py`. Neither exists
today. That is Q3 (plan §8.1), settled as one deliverable with three
customers rather than as one decoder for one consumer — and ruled out of this
programme's scope, tracked separately as **MOR-1993**, which now blocks
**MOR-2010**. The measured finding behind that scoping — the reverse index is
one-to-many and needs a small per-language rule set beside it — is §C9.

### C8.6 The state the double needs already exists — checked

The owner observed that the state a double must hold — frequency, mode,
transmitting or not — is already modelled by whatever holds internal radio state
today, so the double should reuse that rather than invent its own store. That
was checked against the code.

**It holds, for `core/radio_state.py: RadioState`.** *Observed:* it is a plain
`@dataclass(slots=True)` whose only imports are `dataclasses`, `typing` and
`core/types`; a grep for `asyncio`, `Lock` and `await` across the module returns
**zero** matches. It already models exactly what is needed and more: `main` and
`sub` `ReceiverState`, each with `vfo_a`/`vfo_b` `VfoSlotState` carrying
`freq_hz`, `mode`, `filter_num` and `data_mode`; and at the top level `ptt`,
`power_on`, `split`, `dual_watch`, the meters and `to_dict()`. It lives in
`core/`, which every layer may import, so a test double may hold one with no
layering problem.

Three further facts make the reuse more than merely possible:

- **Production already shares it five ways.** `runtime/radio.py: CoreRadio`,
  `backends/yaesu_cat/radio.py: YaesuCatRadio`,
  `backends/rigctld_client/radio.py: RigctldClientRadio`, `web/server.py` and
  `web/runtime_helpers.py` each construct a `RadioState`. A double holding one
  is holding the same object the production code holds, which is the property
  that makes it worth doing.
- **Tests already construct it directly** — for example in
  `tests/test_main_sub_tracking.py` and `tests/test_yaesu_cat_observation_adapter.py`.
  So this is not a new capability, only an unused one.
- **It is already cross-family.** `RadioState` carries a
  `yaesu: YaesuStateExtension | None` field, so the single state model has a
  per-language extension point already — a small but real precedent for one
  double covering both radio languages.

**It does not hold for `core/state_store.py: StateStore`, and that distinction
matters.** *Observed:* `StateStore` is a different kind of object — provider
generations (`begin_provider_generation`, `subscribe_provider_generation`),
freshness with a clock, TTL, staleness marking (`mark_stale_due`), observation
batches. Its purpose is to say **how fresh a reading from a real radio is**. A
double that owned one would be asserting freshness about itself, which is a
claim it has no standing to make. That is not hypothetical: #2808's
`PttAnsweringSerialMockRadio` owns a private real `StateStore` precisely to
satisfy a freshness gate, and its own docstring frames that as making a double
"``StateStoreCapable``" so the server uses it instead of an empty fallback. It
works, but it is a workaround for a gate, not a model of the radio.

So the sketch is: **the double holds a `RadioState`; the freshness machinery
stays on the production side of the boundary.** Anyone tempted to give the
double a `StateStore` should first say which reading's freshness it is
entitled to assert.

### C8.7 What the profile will never give you

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

### C8.8 The cost, plainly

The existing tests are written against three different double APIs. Converging
means rewriting call sites — real, one-off work — in exchange for ending the
pattern where each new need grows a fourth variant. No estimate is offered here
and none should be inferred.

---

---

## C9. The third population, and the reverse index it needs

Plan §1.4 records that a third population of hardcoded command bytes exists —
`runtime/_civ_rx.py` compares `frame.command` / `frame.sub` against integer
literals **105 times** — and that the plan's scope stops short of it, ruled
by plan §8.1 Q3. This section is the measurement behind that ruling.

**Why it is structurally different.** The other two populations build a frame
or match a reply to a request the code itself just sent, so the code already
knows which command it is dealing with. `_civ_rx.py` decodes frames the radio
sends unprompted: it has bytes and must recover a name. `CommandMap`
(`commands/command_map.py`) offers `get`, `has`, `__iter__`, `__len__` and
`__repr__` — name to bytes only.

**The reverse index is not injective, and not marginally so.** *Measured* by
loading each profile and inverting `RigConfig.to_command_map` twice — once into
`wire tuple -> [names]`, once into `(command, sub) -> {names}`, which is the
granularity `_civ_rx.py` actually matches on:

| Profile | CI-V commands | Distinct wire tuples | Tuples with >1 name | Worst | `(cmd, sub)` keys | Colliding |
|---|---|---|---|---|---|---|
| IC-705 | 234 | 119 | 111 | 4 | 109 | 101 |
| IC-7300 | 319 | 177 | 142 | 2 | 95 | 60 |
| IC-7610 | 208 | 126 | 82 | 2 | 109 | 65 |
| IC-9700 | 323 | 181 | 142 | 2 | 100 | 61 |
| X6200 | 62 | 38 | 22 | 4 | 38 | 22 |
| X6100 | 12 | 9 | 3 | 2 | 8 | 4 |

**Counting method for the `(cmd, sub)` columns:** "sub" is taken
*positionally* — the declared tuple's second byte whenever the tuple has two or
more bytes — not by whether `commands/_frame.py: parse_civ_frame` would assign
that command a sub at all (`_COMMANDS_WITH_SUB`). The numbers above stand under
that definition; the parser-grounded definition, which differs and is the one
used from here on, is in `docs/plans/2026-09-01-reverse-command-index.md` §1.

The worst cases are not exotic. On IC-705 and X6200, `1C 00` resolves to
`{get_transceiver_status, ptt_off, ptt_on, set_transceiver_status}` — four
names on one tuple. On IC-705, `0E` resolves to
`{get_scan, scan_start, scan_stop, set_scan}`. On IC-7300 most collisions are
the ordinary get/set pair: `07` is `{get_vfo, set_vfo}`, `11` is
`{get_attenuator, set_attenuator}`.

**What that means for the design.** A reverse index alone answers neither
consumer. What closes the gap is a small rule set — payload absent means a
read, payload `00` or `01` distinguishes these two writes — and the important
property is that those rules are **per radio language, not per model**: the
get/set-by-payload convention is a fact about CI-V, not about an IC-7300. That
is exactly the category C8.7 already lists as what a profile will never supply.

So the reverse index and the per-language rules are **one deliverable with
three customers**: `runtime/_civ_rx.py`, the single double of C8, and any
future consumer that must interpret a frame it did not build. Whether that
deliverable belongs to this programme or its own was plan §8.1 Q3, ruled
separate — tracked as **MOR-1993**, which now blocks **MOR-2010** — precisely
because it is one thing serving three, not one thing serving one.

---

## Appendix — how the numbers in these two documents were obtained

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
- The IC-9700 copied block: `diff` of the 24 lines following the identical
  comment in the two TOMLs (`get_vox_delay`/`set_vox_delay` sit just outside
  that identical run — same address, differing trailing comment).
- Census figures: read from `tests/command_map_parity_uncovered.txt`, whose
  every number `tests/test_command_map_parity.py` re-measures and asserts; that
  test was run at `a2de2ab0` and passed (5 tests, 0.2s).
- Divergence grouping: the 76 non-comment rows of
  `tests/command_map_parity_divergences.txt`, cut by profile and by builder.
- Gap arithmetic (§C2): `gap` rows of `tests/command_map_parity_uncovered.txt`
  — 201 rows, and 871 when the profile lists are split on commas — against the
  `profile_builder_map_gaps` census of 1006, whose unit is read from
  `tests/test_command_map_parity.py: _report` (`gap_pairs` is a set of
  `(model, Key)` where `Key` is `(module file, function name)`).
- Declared-command counts (§C2): `profiles/rig_loader.py: discover_rigs` over
  `rigs/`, counting each config's commands by `isinstance(spec,
  CatCommandSpec)`. This is what establishes that no CI-V profile declares any
  CAT command, so for those six profiles a gap means the TOML omits the command
  and nothing else.
- Absence of an IC-9700 reference (plan §5, class B): listing
  `docs/validation/cat-audits/`, which holds `ftx1.md`, `ic7300.md`,
  `ic7610.md`, `tx500.md`, `x6200.md`, `x6200-unofficial.md` and `README.md`.
- Builder map keys (plan §3.1): AST walk of `src/rigplane/commands/`, collecting
  the string literal each public `cmd_map`-taking builder passes as
  `_build_from_map`'s second positional argument or as a `cmd_name=` keyword,
  and comparing it to the function's own name — **195 equal, 34 different, 2
  delegating to a shared template without a literal of their own, and 1
  (`commands/speech.py: get_speech`) with no literal because it selects between
  two keys at runtime by probing the map. 195 + 34 + 2 + 1 = 232, which is the
  same 232 the parity fixture reports as `census public_builders`.** An earlier
  version of this line reported only the first three buckets and summed to 231;
  the fourth case existed in the code and was dropped by a `continue` in the
  measuring script, not missing from the tree. Corrected after the discrepancy
  was caught by the arithmetic not closing — which is the argument for stating
  breakdowns precisely enough to add up. `census public_builders 232` in
  `tests/command_map_parity_uncovered.txt` is **not** affected and needs no
  change: it counts defining functions whose signature contains `cmd_map`
  (`tests/test_command_map_parity.py: _builders`, keyed by `value.__name__` so
  the `speech = get_speech` module-level alias collapses onto its function), and
  this AST census counts `FunctionDef` nodes with a `cmd_map` argument. The two
  methods are independent and agree at 232.
- Reverse-index collisions (plan §8.1, Q3): loading each profile, inverting
  `RigConfig.to_command_map` into `wire tuple -> [names]` and separately into
  `(command, sub) -> {names}`, and counting keys with more than one name.
- Test-double depth (§C8): reading each double, plus a `grep` of
  `tests/mock_server.py` for its `_CMD_*` constants and the `if cmd ==` branches
  that consume them. `_ptt_reread_fixtures.py` was read from `origin/main`
  (`e8fe6e45`), not from this branch, and is marked as such where it appears.
- The Yaesu path (§C4): reading `backends/yaesu_cat/radio.py`
  (`set_freq`, `_get_spec`, `_write`, `_query`, `_parsers`),
  `backends/yaesu_cat/parser.py: format_command`, and `grep` for every
  `commands` import under `backends/yaesu_cat/` — one line, two arithmetic
  helpers.
- Readers of `protocol_type` (§C3): `grep` across `src/` — declared and
  validated in `profiles/rig_loader.py`, carried on `RadioProfile`, and read in
  production only by `cli/_validate.py`.

---

## Where the instruction lives

Nothing in this document tells anyone to do anything. The work it supports is
ordered, sized and pinned to named tests in:

`docs/plans/2026-08-29-profile-driven-command-bytes.md`

| You want | Go to |
|---|---|
| What to build, and in what order | plan §4 |
| What "done" looks like | plan §2 |
| The recommended binding mechanism | plan §3.1 |
| The 76 divergences and how to hand them out | plan §5 |
| The response half | plan §6 |
| What could go wrong | plan §7 |
| All ten owner rulings — D1, D2, and Q3–Q8 | plan §8.1 |

Neither document is complete on its own.

