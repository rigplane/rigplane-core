# v3 UI migration — consolidated defect inventory

## What this document is

This is a **snapshot** of what is already known about defects and open
architectural questions relevant to resuming the "UI Composition Architecture
v3" frontend migration. It was compiled by reading the codebase and the
in-repo documentation, findings verified in the session that requested this
consolidation, and Linear (read via a headless API key on the compile date —
see "How Linear was read" below). It merges the same underlying defect where
it is described more than once, in different vocabulary, across different
sources.

**Compiled against:** `origin/main` at commit `a2de2ab04bb76373ab3cbdcd29ebf79d4fb2256e`
("docs: cite file:symbol in audit-ui, not file:line", #2805; committed
2026-08-29), read via `git archive` into a scratch checkout — the shared
working checkout was not touched. Linear and GitHub issue/PR state below
reflect what those systems showed at the time each query ran, which is not
necessarily the same instant as the git read above. The consolidation itself,
and a subsequent independent verification pass covering every code citation
and every Linear/GitHub state below, both happened on **2026-08-29** — the
verification pass re-ran the same Linear queries and re-read #2633/#2803 via
`gh`, and found no drift from what is recorded here.

**This is not a live tracker.** Every "open," "in progress," or "unresolved"
statement below describes the state as read on **2026-08-29**. Items may have
been fixed, superseded, or reprioritized since. Before relying on any item,
re-check it against current `main`, current GitHub, and current Linear rather
than assuming it is still true. Where a fix is already in flight, or where a
ticket is marked Done, that is stated plainly — including where it changes
the picture of what still blocks v3 — but "Done" or "in flight" does not mean
the surrounding problem is fully closed; several items below explain exactly
how much a "Done" ticket actually covers.

This document does not propose fixes, does not estimate effort, and does not
invent contents for tickets it could not read. Where a source could not be
read, or where I chose not to infer a ticket's content from its number alone,
that is stated plainly rather than filled in with a plausible guess.

### How Linear was read

Linear was reachable via a headless path: a BWS-stored API key was exported
into an environment variable for the duration of read-only GraphQL queries
against `api.linear.app/graphql`, then unset. No mutation query was sent. The
key was never printed and was not written into this document or any other
file. The original consolidation ran two queries; a third was added on
**2026-08-29**, during a revision pass folding in an independent review's
findings (see item 7 and the structural-reliability-audit section below for
what changed as a result):

1. All issues on team `MOR` with issue number between 1799 and 1877
   inclusive (the range named for "the structural reliability audit") — 79
   issues returned. Note: this is the exact count for that numeric range: the
   range was previously described elsewhere as "roughly 90 findings"; the
   number actually returned by querying that exact range is 79, not "roughly
   90." I have not investigated the discrepancy — it may simply be that the
   earlier figure was a rounded approximation.
2. A specific list of ticket numbers relevant to items in this inventory:
   MOR-488, MOR-664, MOR-972, MOR-973, MOR-974, MOR-980, MOR-981, MOR-985,
   MOR-989, MOR-990, MOR-991, MOR-993, MOR-1671, and MOR-1986 — 14 of 14
   returned a result.
3. Read on 2026-08-29 (revision pass): the ten tickets previously left
   unqueried as the closure path for item 7's P0 gaps — MOR-982, MOR-986,
   MOR-983, MOR-984, MOR-975, MOR-987, MOR-976, MOR-977, MOR-978, MOR-979 —
   plus MOR-1981 (to fix a discovery-order error in item 1) and MOR-1839,
   MOR-1840, MOR-1874 (to correct the structural-reliability-audit
   title-overlap sweep). 14 of 14 returned a result.

Not queried, and therefore unknown-status in this document: MOR-465 (RIT/XIT
naming, mentioned in item 10, left as previously described from the
repository document alone). A GitHub issue and a GitHub PR referenced from
Linear ticket content were additionally read via `gh` (read-only) to resolve
one specific discrepancy — see item 4.

---

## 1. Duplicated command-building mechanism: the profile-driven path is dead, only the hardcoded IC-7610-shaped fallback runs

**In plain words:** the code has two ways to build the bytes sent to the
radio — one that reads the radio's own declared configuration file, and one
hardcoded fallback. In production, only the fallback ever executes, and that
fallback accreted around one specific radio (IC-7610), so on other radios it
silently sends wrong bytes for some commands.

- **Recorded in:**
  - The requesting session's own findings: proven by reading/executing code —
    232 public builders in `rigplane.commands` accept a command-map argument;
    no production caller passes one.
  - `tests/command_map_parity_divergences.txt`: 76 measured byte-level
    disagreements across 8 rig profiles. `tests/test_command_map_parity.py`
    fails if this file drifts from measured reality — it is a live ratchet,
    not a snapshot. By rig: IC-9700 23, IC-7300 21, IC-705 17, IC-7610 13,
    X6100 2. By command family (top entries): `usb_mod_level` 8,
    `civ_transceive` 8, `quick_split` 6, `data_off_mod_input` 6,
    `data1_mod_input` 6, `civ_output_ant` 6, `acc1_mod_level` 6, then
    `vox_delay` / `scope_center_type` / `nb_width` / `nb_depth` /
    `lan_mod_level` at 4 each. By source module: `commands/config.py` 44,
    `commands/levels.py` 16, `commands/vfo.py` 10, `commands/scope.py` 4,
    `commands/ptt.py` 2.
  - `tests/command_map_parity_uncovered.txt`: census — 230 builders compared,
    232 public builders total, 126 sites compared out of 139
    dual-implementation sites, 1006 `profile_builder_map_gaps`, 16
    `hardcode_only_builders`, 2 `cat_only_profiles` (FTX-1, TX-500).
    **Correction to an earlier framing:** those two radios' empty command
    maps are **not a defect in those radios**. Every command FTX-1 and TX-500
    declare is a serial CAT command, not CI-V, so their whole `CommandMap` is
    structurally empty by construction — the census file itself labels this
    a `cat-only` row and says to read it that way. It is a methodology
    artifact of how the CI-V-vs-fallback comparison is built, not a radio-side
    bug.
  - **Linear ticket MOR-1986, state Done**, title "One source of truth for
    CI-V command bytes — 139 builders each carry two implementations." Its
    own description matches the mechanism above closely and gives a
    per-module count consistent with what was independently measured:
    "139 command builders in `src/rigplane/commands/` each carry two
    implementations of the same knowledge — one that reads the profile
    command map, and one hardcoded in Python as a fallback... Nothing
    compares the two." The ticket explicitly scopes itself to a **first
    step only**: "A test that builds every command in every profile both
    ways and asserts the frames are identical... It does not remove the
    duplication." A **second step** — "Complete the map profile by profile,
    delete fallbacks in batches under the green test" — is explicitly
    deferred and explicitly warned against doing "in one change" (139 pairs
    "far past the hard ceiling"). **"Done" here means the first step (the
    ratchet test) was completed, not that the duplication was removed** —
    consistent with the divergence file still showing 76 rows at this
    revision. **Discovery order, corrected 2026-08-29 by reading MOR-1986's
    own description directly:** an earlier draft of this item had the
    sequence backwards. MOR-1981 ("Eight scope reads omit the required
    Main/Sub selector byte, and the radio is right to refuse them," **state
    Done**) was found first, while chasing why the IC-7300 declined a batch
    of scope queries. MOR-1986's own opening line reads: "Found 2026-08-22
    while establishing why eight scope reads are refused (MOR-1981). That
    defect is an instance; this is the class." So MOR-1981 is the specific
    instance — the `cmd_map` branch of every `get_scope_*` builder silently
    discarding the `receiver` argument that the fallback branch honors — and
    MOR-1986 (this item's ticket) is the general mechanism, named while
    investigating MOR-1981, not the other way around.
  - Origin/main commit history at this revision shows continued work after
    MOR-1986's own "Done" mark: commits under the same ticket (merged PRs
    #2798, #2796, #2795) fix additional bugs *inside* the still-dead cmd_map
    branch (scope receiver selector, a duplicated sub-address, a hardcoded
    command-29 value).
  - **Adjacent but distinct ticket found in the structural reliability
    audit:** MOR-1841 (Backlog, under epic MOR-1803), "Web poller falls back
    to hardcoded IC-7610/IC-7300 profiles and swallows all command-map load
    errors." This is the **same accretion theme** (an IC-7610/IC-7300-shaped
    hardcoded fallback silently substituting for profile-driven behavior)
    but at a **different layer and mechanism**: MOR-1986/this item is about
    individual command-builder functions in `commands/`; MOR-1841 (title
    only read, description not fetched) appears to be about the web poller
    falling back to an entire hardcoded *profile object* when profile
    loading fails. Named here because it is the same class of problem, not
    because it is the same defect — treat as a related, separately tracked
    item.
  - **Measured directly in this session, in the sibling repository
    `rigplane-pro`** (owner-granted access): two independent searches, both
    returning zero matches, with build/venv/cache directories excluded —
    (a) `grep` for `cmd_map|CommandMap` across `*.py`, and
    (b) `grep` for an import of `rigplane.commands` (`from rigplane.commands`
    / `import rigplane.commands` / `rigplane.commands.`) across `*.py`.
    Pro is reported (by the coordinating session, not independently
    re-verified beyond the two zero-result searches above) to import from
    `rigplane.audio`, `rigplane.dsp`, `rigplane.discovery`, `rigplane.cli`,
    `rigplane.web.protocol`, and `rigplane.backends.hamlib_*` — but never the
    command-builder layer. **A future reader cannot re-run these two
    searches from `rigplane-core` alone**: `rigplane-pro` is a private
    sibling repository, not part of this checkout.
  - **Owner ruling (post-dispatch):** the end state is decided — the
    hardcoded fallback and its constants are to be deleted, the command map
    becomes required (not optional), and the request-building side and the
    response-matching side (item 2 below) move together as one piece of
    work. A separate design document for that migration is being written;
    it is the place to look for the actual migration plan. This inventory
    does not duplicate its content and was not written against it. Whether
    that design document is the same effort as MOR-1986's described "second
    step" was not confirmed — the owner's message did not name the
    document, and MOR-1986's own text does not mention a design document.
- **Evidence strength:** proven by executing code (dead-branch claim) +
  test-measured byte divergences + an existing, Done-but-partial-scope
  Linear ticket describing the same mechanism + a direct, session-run
  cross-repository search confirming Pro has no dependency on this
  mechanism.
- **Blocks v3?** Yes. Any v3 panel built or validated against a
  non-IC-7610 profile will observe wrong on-wire behavior that editing the
  profile TOML cannot fix, because the profile-driven path is not live.
  Fixing this also requires touching the response-matching side (item 2),
  which is exactly the kind of backend rework that could land mid-migration
  and force redoing UI-adjacent assumptions made in the meantime.
- **Pinned by:** `tests/test_command_map_parity.py` (regenerate-or-fail
  against the two `.txt` files named above).

---

## 2. Fixing item 1 request-side only would break reply matching (~20 sites)

**Plain words:** even if the profile-driven request builder were switched on,
the code that recognizes the radio's *replies* still expects the old
hardcoded command/sub-address bytes, so the request side and the response
side would disagree if only one of them changed.

- **Recorded in:** the requesting session's own findings only, proven by
  reading `runtime/radio.py`'s response matchers (roughly 20 sites hardcode
  the same command/sub bytes as the dead fallback in item 1).
- **Evidence strength:** proven by reading code. Not found as its own
  ticket in either Linear query run for this inventory; MOR-1986 (item 1)
  scopes itself explicitly to files under `src/rigplane/commands/` and does
  not mention `runtime/radio.py` or response matching, so it does not appear
  to already cover this half of the problem.
- **Blocks v3?** Indirectly — it is a precondition to fixing item 1, and
  item 1 blocks v3 for the reason stated above.
- **Pinned by:** nothing found.
- **Forward pointer:** per the owner's ruling recorded under item 1, this and
  item 1 are to move together in one piece of work, planned in the separate
  design document referenced there. This inventory does not restate that
  plan.

---

## 3. MIC gain and ACC1 MOD level share one CI-V address (IC-7300, live-measured)

**Plain words:** two differently-named controls are wired to the literal same
radio command; moving one physically moves the other.

- **Recorded in:** the requesting session's own findings only. `commands/
  _frame.py` defines `_SUB_MIC_GAIN` and `_SUB_ACC1_MOD_LEVEL` as the same
  value under the same level command family; measured live on the IC-7300 on
  the bench today (writing 178 to ACC1 MOD level moved MIC gain to 178 on the
  radio).
- **Evidence strength:** measured on hardware.
- **Not found in Linear.** Neither "MIC gain," "ACC1," nor a matching
  description appears among the titles of the 79 structural-reliability-audit
  issues or the 14 specifically queried tickets. This appears to be a
  genuinely unfiled defect as of the compile date, within the scope of what
  was queried.
- **Blocks v3?** Yes, for this control specifically — any v3 audio/config
  panel exposing both controls independently ships a UI that misrepresents
  what it is doing, on the one live bench radio, and a rewritten frontend
  would repeat the bug unless it inherits a fix.
- **Pinned by:** nothing found. Not caught by the command-map parity tests,
  which compare map-vs-fallback per command name, not collisions between two
  different command names.

---

## 4. FTX-1: web UI cannot select the second receiver at all

**Plain words:** the FTX-1 profile declares it has two receivers, but the
generic code path that lets the web UI switch between them refuses to run for
FTX-1 specifically, before it ever talks to the radio — even though the radio
does have a working "select receiver" command underneath.

- **Recorded in:**
  - The requesting session's own findings, with the mechanism traced at this
    revision: the FTX-1 rig profile (`rigs/ftx1.toml`) does not declare
    main/sub VFO select codes the way the Icom profiles do (`rigs/ic705.toml`,
    `rigs/ic7300.toml`, `rigs/ic7610.toml`, `rigs/ic9700.toml` all declare
    them; FTX-1 does not), so the loaded `RadioProfile`'s main/sub select
    fields are unset. The web command dispatcher's `select_vfo` handling
    (`web/radio_poller.py`, inside the command-execution method `_execute`)
    raises a `CommandError` for "no MAIN/SUB select code" before it reaches
    a later call to the backend's `select_receiver` method — even though the
    Yaesu CAT backend (`backends/yaesu_cat/radio.py`) implements working
    `select_receiver` / `get_active_receiver` methods via the `VS` CAT
    command.
  - **Linear ticket MOR-1671, state In Progress**, title "FTX-1 MAIN/SUB
    selector must dispatch the documented VS receiver command." Its own
    text confirms the same mechanism in its own words: "The backend typed
    `select_receiver()` path supports this, but the Web poller checks
    CI-V-only MAIN/SUB codes before invoking it; FTX-1 has no such CI-V
    profile codes, so dispatch stops before CAT." Its acceptance criteria
    (unchecked as of the read) require: `Select SUB` emits exactly `VS1;`,
    `Select MAIN` emits exactly `VS0;`, a confirmed `VS;` readback updates
    the active receiver and UI, truthful pending/error/timeout behavior, and
    that CI-V VFO-code requirements are not applied to typed Yaesu receiver
    selection. This replaces the second-hand citation used earlier (which
    quoted only `docs/validation/cat-audits/ftx1.md`, itself still accurate
    and corroborating, as a secondary source naming the same ticket).
  - **A related GitHub issue, #2633, is CLOSED with reason NOT_PLANNED**
    (closed 2026-08-14), titled "bug(web): FTX-1 MAIN/SUB receiver selector
    is blocked by CI-V-only profile-byte guard" — the same defect, described
    almost identically to MOR-1671's own text, including matching acceptance
    criteria. **This is a state discrepancy worth naming plainly rather than
    resolving:** Linear shows MOR-1671 as "In Progress," while what appears
    to be its GitHub-side bounded execution issue was closed as not planned.
    Per this repository's own stated convention (Linear is the control
    plane; GitHub carries only bounded execution issues linked back to it),
    a closed-not-planned GitHub issue plausibly means one delegated attempt
    at implementing MOR-1671 was abandoned without the parent Linear ticket
    itself being resolved — but this inventory does not assert that
    reading; both states are named as found.
  - **A currently open PR, #2803** ("fix: route poller VFO switch dance
    through CoreRadio/select_receiver"), was suggested as possibly fixing
    MOR-1671 because of surface keyword overlap (`select_receiver`, a "no
    MAIN/SUB VFO select code" error message). Reading the PR directly (title,
    full diff, full body, and GitHub's own `closingIssuesReferences` for it,
    which is empty) gives a more precise result than a flat "fixes it" or
    "doesn't fix it": **#2803 demonstrably changes FTX-1 behavior, but not
    the behavior MOR-1671 is about.**
    - **What #2803 actually changes for FTX-1, traced by reading both
      revisions of `web/radio_poller.py`:** on `main` at this revision, the
      `SetFreq`/`SetMode` arms of `_execute` raise `CommandError` for a
      non-MAIN receiver ("no cmd29 route and no VFO switch codes") whenever
      the profile has neither a cmd29 route nor `vfo_main_code`/
      `vfo_sub_code` — exactly FTX-1's case (`rigs/ftx1.toml` declares none
      of the three). #2803's diff deletes that guard on the non-MAIN branch
      and replaces it with an unconditional `await radio.set_freq(freq,
      receiver=rx)` (and the equivalent for `set_mode`), delegating the
      cmd29-vs-VFO-switch decision to the backend. Traced one layer further:
      for an FTX-1 session `radio` is a `backends/yaesu_cat/radio.py:
      YaesuCatRadio` instance (per `backends/factory.py`), whose own
      `set_freq(freq, receiver=1)` sends the `set_freq_sub` CAT template
      (`FB{freq:09d};`, declared in `rigs/ftx1.toml`) directly — no CI-V
      bytes involved. So on #2803's branch, a web `SetFreq`/`SetMode`
      command targeting FTX-1's SUB receiver reaches
      `YaesuCatRadio.set_freq`/`set_mode` and works, where on `main` it is
      refused before ever reaching the radio. This is a real, verified
      change in FTX-1 behavior, confirmed against the PR's actual diff, not
      inferred from its description.
    - **Why that is not MOR-1671:** MOR-1671's own acceptance criteria
      (quoted above) are about *receiver selection* — `Select SUB` emitting
      `VS1;`, `Select MAIN` emitting `VS0;`, and the UI reflecting a
      confirmed `VS;` readback — which is the `SelectVfo` command path, a
      different `_execute` case than `SetFreq`/`SetMode`. #2803's own body
      states explicitly that it leaves "the existing `SelectVfo` site" and
      its guard behavior unchanged, and the diff confirms this: the
      `vfo_main_code is not None` / `vfo_sub_code is not None` guards
      guarding `SelectVfo` are untouched. Because `rigs/ftx1.toml` declares
      neither code, that guard still raises for FTX-1 exactly as before. So
      MOR-1671's own acceptance criteria remain unmet by #2803: after this
      PR, the FTX-1 web UI can set the SUB receiver's frequency and mode,
      but a user still cannot click "Select SUB" and have it take effect.
    - **This fix, for FTX-1, is also unintended and untested.** #2803's
      full diff and body contain no mention of "FTX", "ftx1", "Yaesu", or
      "yaesu" (checked by direct text search over the whole diff); its own
      description frames the change entirely in terms of Icom CI-V
      dual-receiver `0x07` VFO-switch frames (`CoreRadio`,
      `runtime/_dual_rx_runtime.py`), and none of its four changed test
      files exercise the Yaesu CAT backend. The FTX-1 SetFreq/SetMode fix is
      a side effect of an Icom-focused refactor, not a change anyone
      deliberately made or verified for this radio.
    - **Net position:** #2803 closes part of the practical problem this
      item's title names ("web UI cannot select the second receiver at
      all") — SetFreq/SetMode to the SUB receiver now reaches the radio —
      without closing MOR-1671 itself, whose acceptance criteria are
      specifically about the `Select SUB`/`Select MAIN` action. No ticket
      number in this inventory's Linear queries names the narrower
      SetFreq/SetMode defect #2803 happens to fix as its own tracked item.
- **Evidence strength:** proven by reading/executing code, corroborated by
  the ticket's own primary text and by an independently-read secondary
  document (the CAT audit) — but its actual resolution status is genuinely
  unclear given the Linear/GitHub state discrepancy just described.
- **Blocks v3?** Yes, for FTX-1 dual-receiver work specifically. The v3
  architecture decision's own acceptance criteria include "one layout
  supports single- and dual-receiver radios from existing capabilities";
  FTX-1 cannot honor that today for receiver selection, and — per the
  evidence above — there is no confirmed merged fix for it as of the
  compile date.

---

## 5. IC-9700 profile carries copy-pasted IC-7300 values (14 or more commands)

**Plain words:** part of the IC-9700 radio's configuration file was copied
from the IC-7300's configuration file, including a code comment that
justifies a value by pointing at the IC-7300's file. Whether the copied
numbers also happen to be correct for the IC-9700 is unknown — nobody has an
IC-9700 to test against, and there is no manual-based check for it either.

- **Recorded in:** the requesting session's own findings only — the copying
  itself is proven by reading the rig profile source. Corroborated
  indirectly: `docs/validation/cat-audits/README.md` lists completed
  manual-vs-implementation audits for FTX-1, IC-7610, IC-7300, X6200, and
  TX-500 only. **IC-9700, IC-705, and X6100 have no CAT-manual-vs-
  implementation audit at all**, so nothing in the repository could have
  independently caught this, and nothing currently would.
- **Not found in Linear.** No title among the 79 structural-reliability-audit
  issues or the 14 specifically queried tickets mentions IC-9700 or this
  copying.
- **Evidence strength:** the copying is proven by reading code. The
  correctness of the copied values for the IC-9700 is unverified — no IC-9700
  is on the bench, and no manual-based check exists for that radio.
- **Blocks v3?** Unclear / indirect — this is a per-radio correctness risk,
  not an architectural blocker to the v3 UI framework itself, unless the
  resumed migration specifically exercises IC-9700 panels.

---

## 6. IC-7610: most web controls don't reflect a front-panel change (readback)

**Plain words:** turning a knob or pressing a button on the physical radio
should update the value shown in the web UI ("readback"). A dedicated audit
of every IC-7610 web control found that only 15 of roughly 73 controls
actually do this today; the rest either need a code change to work — a
missing polling-list entry (**gap class A1**, 11 controls), a missing
dispatch branch (**gap class A2**, 24 controls), or a missing parser (**gap
class B**, 11 controls), 46 controls across these three code-level classes
in the audit's own table — track only the MAIN receiver and are silently
wrong for SUB (**gap class C**, 5 controls), or are deliberately excluded as
not applicable (**gap class D**, 7 controls).

- **Recorded in:** `docs/internals/ic7610-control-readback-audit.md`. The
  document explicitly labels its verdicts "code-derived predictions —
  confirm on hardware," dated 2026-06-05.
- **Linear ticket MOR-488, state Backlog** (confirmed, not historical),
  title "Systematic audit & fix: front-panel→web readback for ALL IC-7610 v2
  controls." Its own description matches the repository document closely: it
  names the same root pattern (an observation emitter must exist *and* be
  triggered, by transceive or by polling, for a front-panel change to reach
  the web UI), cites the same live findings (DIGI-SEL, IP+, NOTCH,
  SUB-receiver-not-polled), and names the audit document itself
  (`docs/internals/ic7610-control-readback-audit.md`) as one of its planned
  outputs. This confirms the audit document and MOR-488 describe the same
  program, and that the program is **still open** (Backlog), not historical.
- **Evidence strength:** documented (explicitly code-derived, not
  hardware-confirmed, per the audit document itself), corroborated by an
  open Linear ticket with matching live-hardware findings.
- **Blocks v3?** Likely relevant but not certain — this depends on whether
  the resumed migration's reference vertical treats live front-panel/web
  synchronization as a requirement for the IC-7610 specifically.

---

## 7. The v3 discovery baseline (MOR-972) already lists P0 safety/architecture gaps as gating

**Plain words:** before the migration stalled, an internal audit of the
current frontend found several things that are safety-relevant, not just
code-cleanliness: there is no proven guarantee that closing or switching away
from a UI panel actually stops the radio from transmitting; a stale
"start transmitting" command can potentially be replayed after a reconnect;
a degraded-connection state can block the emergency stop-transmit action;
switching which layout is shown can silently reopen or close network
connections to the radio (scope, audio) that should have stayed open; and
there is no single place all safety/status messages funnel through, so
different presentations can show inconsistent or duplicate warnings.

- **Recorded in:** `docs/plans/2026-07-25-ui-composition-discovery-and-parity.md`,
  in its "Runtime, transport, command, and TX safety lifecycle" table and its
  "Prioritized gaps → P0" section, which states outright that these findings
  "must not be classified as code-cleanliness refactors."
- **Linear read for this item — say plainly what is closed:** the discovery
  document (MOR-972, itself **Done**) names several bounded prerequisite
  slices as the path to closing its own P0 gaps. Of the **nine** prerequisite
  tickets named in the original consolidation, **all are marked Done**:
  MOR-973 ("make App the presentation composition root"), MOR-974
  ("consolidate capability-derived presentation selectors"), MOR-980 ("P0:
  make frontend PTT delivery asymmetric and non-replayable" — its own
  acceptance criteria specifically cover `ptt_off` bypassing degraded-health
  rejection and reconnect never emitting stale PTT-ON, i.e. two of the five
  plain-language gaps listed above), MOR-981 ("establish the App
  presentation-selection seam"), MOR-985 ("pin and validate the
  authoritative capability wire"), MOR-989 ("disable raw MediaSession PTT"),
  MOR-990 ("split local TX audio stop from confirmed MOD restore"), MOR-991
  ("enforce exact receiver/VFO topology pairs in profile loader"), and
  MOR-993 ("interim safety: disable ungated backend MOD restore on session
  teardown"). (MOR-972 itself is also Done, but it is the discovery ticket,
  not one of the nine prerequisite slices it names.)
- **The remaining ten tickets, unqueried in the original consolidation, were
  read on 2026-08-29 in response to independent review, and are also all
  Done:** the document's migration sequence had named these as the actual
  closure of "one App-owned TX controller" and "one backend TX supervisor,"
  their downstream implementation, the reference-vertical proof work, and
  later layout/design-language work. Read directly from Linear, as of
  2026-08-29:
  - MOR-982, Done, "Decision: Freeze the App-owned TX controller contract"
  - MOR-986, Done, "Decision: Define backend TX ownership, stale-queue, and
    session-loss policy"
  - MOR-983, Done, "Decision: Freeze presentation-lifetime ownership before
    lazy mounting" (this ticket's own title is itself a decision ticket, not
    the "downstream implementation" label the original consolidation
    inferred for it from the discovery document's prose alone — a
    correction to that inference, not to the ticket's status)
  - MOR-984, Done, "Implement derived presentation capability selectors"
  - MOR-975, Done, "Reference vertical: VFO plus RX/TX semantic presentation
    contract"
  - MOR-987, Done, "Prove the cross-surface TX lifecycle matrix"
  - MOR-976, Done, "Reference layout: dual-receiver-cockpit"
  - MOR-977, Done, "Design: explore and select the reference design
    language"
  - MOR-978, Done, "Program: prove independent product-owned design
    languages without behavior changes"
  - MOR-979, Done, "Workspace: define versioned constrained UI configuration
    and migration"
- **What this does and does not establish:** every ticket the discovery
  document names as part of closing its own P0 gaps — all nineteen
  (MOR-972's nine named prerequisites plus these ten) — is Done in Linear as
  of 2026-08-29. This is a materially different picture from the original
  consolidation, which could only confirm nine of the nineteen and left the
  other ten, including the two ownership-decision tickets and the
  reference-vertical proof work, as unknown. What this still does not
  establish: whether each ticket's acceptance criteria, once implemented,
  actually closed the specific P0 gap it targeted, and whether the current
  frontend source still matches what those tickets describe — this
  inventory does not re-read `docs/plans/2026-07-25-ui-composition-discovery-and-parity.md`'s
  P0 table against current code, nor any of these nineteen tickets' full
  acceptance-criteria text beyond the few quoted above. A "Done" Linear
  status records that the ticket's own scope was completed and closed, not
  that this inventory independently verified the resulting code.
- **Evidence strength:** the P0 gaps themselves are documented (with the
  source document's own "Automated"/"Missing"/"Unknown" evidence labels);
  the completion of all nineteen named tickets is a directly-read Linear
  fact, dated 2026-08-29 for the ten read in this revision pass.
- **Blocks v3?** Every ticket the discovery document names as its own path
  to closing the P0 gaps is now Done in Linear. Whether that means the P0
  gaps themselves are closed in the running code is not established by a
  Linear read alone — re-reading the current frontend source against the
  discovery document's P0 table (see "How to use this," below) is the
  fastest way to settle that question, and this inventory does not do it.

---

## 8. v3's own starting-state defect: the composition root doesn't compose

**Plain words:** the plan wants swappable "skins" (visual styles) without
touching radio logic. As documented on 2026-07-25/26, the app's top-level
component hard-mounted one specific layout component directly; the mechanism
for lazily loading a skin existed in code but nothing called it; and that one
hard-mounted layout component did everything itself — reads runtime state,
resolves which skin to use, builds UI event handlers, and decides desktop vs.
mobile branching — which is the opposite of the intended separation.

- **Recorded in:** `docs/plans/2026-07-25-ui-composition-architecture-v3.md`
  ("Motivation" section) and
  `docs/plans/2026-07-25-ui-composition-discovery-and-parity.md`
  ("Proven current state," items naming the app root, the main layout
  component, and the skin registry module).
- **Linear read for this item — say plainly what is closed:** this is
  precisely the scope of **MOR-973** ("Architecture: make App the
  presentation composition root" — Done) and **MOR-981** ("Establish the App
  presentation-selection seam" — Done), both already listed under item 7.
  MOR-973's own acceptance criteria are: "Skin/layout selection no longer
  originates inside `RadioLayout.svelte`," "Presentation switching does not
  reconnect transport, audio, or scope," and current desktop/LCD/SDR-test/
  mobile behavior remains covered. **If these tickets' acceptance criteria
  were actually met, the starting-state condition this item describes would
  no longer be current** — the documents describing it are dated 2026-07-25,
  and these tickets could have closed afterward.
- **This was not independently re-verified against the current frontend
  source at this revision** (reading `App.svelte`/`RadioLayout.svelte` in
  full was out of scope for a consolidation pass — see Gaps). So there are
  two things on record that are in tension, and this inventory does not
  resolve them: the discovery document's description of a monolithic
  `RadioLayout.svelte` doing app-root work, and Linear's own record that the
  tickets scoped to fix exactly that are marked Done. Both are named; which
  one reflects the code at this exact revision was not checked here.
- **Blocks v3?** As documented, this condition **was** the reason v3 exists
  — the starting state the migration was meant to change. Given the Linear
  state above, whether it is still the current state is now an open
  question this inventory raises rather than answers.

---

## 9. `tests/integration` excluded from all CI + 3 IC-7610 dual-receiver golden replays RED

**Plain words:** a suite of tests that replays recorded real-radio
conversations is not run by any of the automated CI pipelines. Three of those
replays, for setting mode on the IC-7610 in dual-receiver mode via rigctld
(a Hamlib-style `M VFOA USB <passband>` command — `2400` in the fldigi and
js8call goldens, `3000` in the wsjtx golden), currently fail.

- **Recorded in:** the requesting session's own findings only, proven by
  executing the relevant tests/CI configuration. A separate agent was
  reported to be investigating the root cause concurrently; that
  investigation was not re-verified here. No matching ticket title was found
  among the 79 structural-reliability-audit issues (the closest thematic
  match is epic MOR-1799, "rigctld & TX-safety: truth laundering and
  interlock bypass," but none of its 8 children's titles specifically
  describe an `M VFOA USB` / `RPRT -9` failure, so no direct match is
  claimed). Re-run for this revision (2026-08-29): all three goldens
  (wsjtx, fldigi, js8call) fail at their respective `M VFOA USB` line with
  `expected 'RPRT 0', got 'RPRT -9'`; the other three tests in the same file
  pass.
- **Related but treated as distinct:** `docs/validation/cat-audits/ic7610.md`
  documents three **different** live scope round-trip failures on the
  IC-7610 in dual-receiver mode (`scope_dual.set`, `scope_vbw.set`,
  `scope_rbw.set`), attributed to a get/set receiver-parameter asymmetry in
  the scope runtime module. **Linear ticket MOR-664, state Done**, title
  "IC-7610 scope_dual / scope_vbw / scope_rbw .set checks 'did not react' on
  live hardware," confirms this is the same, now-fixed defect: its own
  description gives the identical raw-trace evidence (read→write→read
  sequences per sub-command) and names `src/rigplane/commands/scope.py` and
  `runtime/_scope_runtime.py` as the fix location. **This confirms MOR-664
  is now historical, not live** — it is a different command family (scope,
  not mode) and a different test surface (a live hardware round-trip harness,
  not the recorded replay fixtures this item is about) from the
  `tests/integration` finding, and remains treated as a separate defect from
  it in this inventory: no shared root cause was verified between the two.
- **Blocks v3?** Depends on whether the resumed migration's work exercises
  this exact rigctld / dual-receiver mode-setting path; unclear from the
  sources read.

---

## 10. Administrative and lower-priority items, recorded for completeness

- **Legacy state-writer mirrors** (`docs/internals/legacy-state-writer-inventory.md`):
  a documented, intentional list of Icom field families (inner/outer
  passband tuning, antenna selection, repeater tone/tone-squelch, digital
  selectivity, twin peak filter, IP+ setting, audio peak filter, filter
  shape, dial lock, break-in, SSB TX bandwidth, tuning step, scan, and scope
  controls) still write into an older state-mirror mechanism rather than the
  newer observation-backed store. The document itself tags these
  `deferred_follow_up` and treats them as intentional, tracked debt — not a
  hidden bug. Not independently checked against Linear this round.
- **RIT/XIT naming** (`docs/internals/backend-neutral-readiness-gap-register.md`):
  ticket MOR-465 (RIT/XIT field naming/re-home) is recorded there as open,
  explicitly scoped in that document as "compatibility work, not release
  blocker." MOR-465 was not queried against Linear directly for this
  inventory; its status here is as described by the repository document
  alone.
- **`?ui=v1` legacy fallback** (`docs/plans/2026-04-29-ui-v1-deprecation-audit.md`):
  this document recommended dropping the old `?ui=v1` URL fallback and the
  legacy `AppShell` component. **Confirmed resolved** by reading the current
  frontend entry component: it now only emits a one-line console warning for
  users with stale bookmarks; the legacy layout component is gone. Mentioned
  here only to close the loop, not as an open item.
- **`docs/plans/discovery-artifacts/orphan-candidates.md` — correction to an
  earlier framing:** despite how this document was described going into this
  inventory (as a record of "dead or unreached code"), **it is not a
  dead-code list.** Reading its actual content: it is a pre-modularization
  layering worklist — 56 top-level files that used to sit directly under an
  old, now-renamed top-level package, each tagged with inbound/outbound
  reference counts and a *candidate* target layer, produced as input to a
  since-completed modularization effort. Cross-checking against the current
  tree confirms every file it names has already been moved into a proper
  layered package, with a backward-compatibility re-export left behind at
  its old location (spot-checked for three of the named files). This
  document is **superseded and resolved**, not an open defect list. It is
  recorded here specifically to flag the mismatch between how it was
  characterized and what it actually says.

---

## The structural reliability audit (MOR-1799..MOR-1877): shape, not contents

Queried directly: **79 issues** on team `MOR` numbered 1799 through 1877
inclusive — 8 epics (1799–1806) and 71 child issues (1807–1877). **Every one
of the 79 is in state Backlog** as of the read — none Done, none In Progress,
none cancelled.

The 8 epics, with their child-issue counts:

| Epic | Title | Children |
|---|---|---|
| MOR-1799 | rigctld & TX-safety: truth laundering and interlock bypass | 8 |
| MOR-1800 | State freshness: one TTL authority, honest observations | 6 |
| MOR-1801 | Audio: the server computes the answer, the client is deaf | 10 |
| MOR-1802 | Connection lifecycles: liveness from side-effect traffic | 8 |
| MOR-1803 | Capabilities & profiles: one derivation, honest fallbacks | 8 |
| MOR-1804 | Scope pipeline: frozen truth drives writes | 9 |
| MOR-1805 | Frontend stores: shadow truth outside the TX controller | 9 |
| MOR-1806 | Verification theater: guards not connected to their subjects | 13 |

This inventory does not paste all 71 child-issue titles or descriptions —
only titles were read for the range query, not full descriptions, so their
content beyond the title is not claimed here, and every overlap claim below
is a **title-level** match only: two titles reading alike says nothing about
whether their bodies describe the same underlying mechanism, only that the
words used to name them overlap.

**Corrected 2026-08-29** (an earlier draft of this section found only one
title-level overlap and missed three others under the same epic and theme):
four children, all under epic **MOR-1803** ("Capabilities & profiles: one
derivation, honest fallbacks"), match item 1's "IC-7610-shaped hardcoded
fallback standing in for profile-driven behaviour" theme at title level, and
all four are **Backlog**, read 2026-08-29:
- MOR-1841, "Web poller falls back to hardcoded IC-7610/IC-7300 profiles and
  swallows all command-map load errors" (already named under item 1).
- MOR-1839, "A radio without .profile is silently served the entire IC-7610
  capability sheet."
- MOR-1840, "runtime/radios.py is a second hardcoded radio registry, already
  drifted from rigs/*.toml."
- MOR-1874 (under epic MOR-1806, "Verification theater"), "1532-line v2 UI
  interactive audit runs in no workflow but is credited as a guard in the
  regression matrix" — matches item 9's "a test suite no workflow runs"
  shape, not the MOR-1803 fallback theme.

Two more are worth a reader's attention without being folded into an item
here, because their titles alone suggest overlap with items already listed
but were not read in enough detail to merge confidently: MOR-1877 ("IC-7610
parity matrix asserts 134/134 implemented against a permanently-skipped
reference" — possibly related to item 6 or to the CAT audits, not confirmed)
and MOR-1873 ("Validation dry-run goldens mint `pass` from absent checks" —
possibly related to the `.presence`-only checks mentioned in
`docs/validation/cat-audits/README.md`, not confirmed). No other
title-level overlap with items 1–10 was found among the remaining 65
children — but "not found" here means their titles alone did not suggest
one, not that their bodies were checked and cleared.

**This means the structural reliability audit is, almost entirely, a
separate body of unresolved findings from what is catalogued in items 1–10
above** — four clear title-level overlaps (MOR-1841, MOR-1839, MOR-1840,
MOR-1874) plus two possible-but-unconfirmed ones (MOR-1877, MOR-1873), six
named in total, were found among 71 children — not the "only one" an
earlier draft of this document claimed. Six out of 71 does not change the
bottom line — the audit is still almost entirely a separate body of findings
from items 1–10 — but it does mean the earlier "only one" claim understated
the overlap, and the sweep was titles only: reading each of the 79 tickets'
full descriptions was, and remains, out of scope for this consolidation.

---

## Overlap report

Re-derived now that Linear was read — the conclusion has changed from an
earlier draft of this inventory, which (without Linear access) concluded
four of six findings were new. With Linear read:

| Finding | Already recorded elsewhere? |
|---|---|
| Item 3 — MIC/ACC1 collision | New. Not found in any repository document, test file, or the 93 Linear tickets checked (79 range + 14 specific). |
| Item 1 — dead cmd_map branch / IC-7610 fallback accretion | **Already tracked**: MOR-1986 (Done, but scoped to step one only — see item 1). Related-but-distinct: MOR-1841 (Backlog). The *mechanism* explanation and the direct Pro cross-repository check are still new contributions beyond what the ticket records. |
| Item 2 — response matchers duplicate hardcoded bytes | New. Not found in MOR-1986's own scope statement or elsewhere checked. |
| Item 9 — `tests/integration` excluded, 3 IC-7610 replays RED | New as stated; the thematically-adjacent MOR-664 (scope round-trip failures) is confirmed Done and confirmed to be a different defect. |
| Item 4 — FTX-1 can't select second receiver | **Already tracked and already the subject of an open Linear ticket (MOR-1671, In Progress) and a closed-not-planned GitHub issue (#2633)** — a re-discovery, not a new finding, but this inventory adds the specific correction that an apparently-related open PR (#2803) fixes only part of the practical problem: it makes FTX-1's SUB receiver reachable for `SetFreq`/`SetMode`, but leaves MOR-1671's own scope — the `Select SUB`/`Select MAIN` action itself — untouched. |
| Item 5 — IC-9700 copies IC-7300 values | New. Not found in the repository beyond the copying itself, nor in either Linear query. Explained by a pre-existing, documented coverage gap (no IC-9700 CAT audit exists). |
| Item 6 — IC-7610 readback | **Already tracked**: MOR-488 (Backlog), confirmed to describe the same program as the repository audit document, and confirmed still open, not historical. |
| Item 7 — P0 safety/architecture gaps | **Directly tracked, and — as of a 2026-08-29 revision pass — all nineteen named tickets are Done**: the nine original prerequisite tickets plus the ten (App-owned TX controller decision, backend TX supervisor decision, their downstream implementation, the reference-vertical proof work, and the layout/design-language work) that were unqueried in the original consolidation. Whether the code matches what those tickets' acceptance criteria describe was not independently re-verified — see item 7. |
| Item 8 — composition root doesn't compose | **Directly tracked, and its two named tickets (MOR-973, MOR-981) are marked Done** — this is the most significant single update from reading Linear: if their acceptance criteria were genuinely met, the starting-state problem this item describes may no longer be current, though this was not independently re-verified against the frontend source. |

**Revised bottom line:** of the ten items, five (1, 4, 6, 7, 8) turn out to
already have direct Linear tickets, two of which (7, 8) may already be
resolved per Linear's own record even though the repository documentation
describing them predates that resolution and neither was independently
re-verified against current frontend source. Three items (2, 3, 5) remain
apparently unfiled anywhere checked. Item 9 remains new, with a thematically
related but distinct ticket now confirmed fixed. This is a different, more
nuanced conclusion than "most findings are new" — reading Linear changed the
picture substantially, exactly as flagged as a risk in the original Gaps
section.

---

## Gaps

- **Ticket not queried, listed exactly so a reader knows the boundary of
  what was checked:** MOR-465 (RIT/XIT naming — see item 10). Its title,
  description, and state are not known here and were not guessed at. (The
  ten P0-closure tickets previously listed here — MOR-982, MOR-986, MOR-983,
  MOR-984, MOR-975, MOR-987, MOR-976, MOR-977, MOR-978, MOR-979 — were
  queried in a 2026-08-29 revision pass and are no longer a gap; see item 7.)
- **Full descriptions of 65 of the 71 child issues under the structural
  reliability audit (MOR-1807–1877) were not read** — only titles, via the
  range query, and title-only comparison bounds what this overlap check can
  claim: two titles matching says nothing about whether their bodies
  describe the same underlying mechanism, only that the words used to name
  them overlap. Six children were named above by title-level similarity to
  items already in this inventory: MOR-1841, MOR-1839, and MOR-1840 (item
  1's IC-7610-shaped-fallback theme, all three under epic MOR-1803, all
  Backlog), MOR-1874 (item 9's "a test suite no workflow runs" theme,
  Backlog), and the possible-but-unconfirmed MOR-1877 / MOR-1873. The
  remaining 65 were not matched against anything **by title**; their bodies
  were not read at all, so reading them could surface further overlap or
  further genuinely new defects that this inventory does not capture.
- **Hardware access is limited.** The live bench is IC-7300 and FTX-1 only.
  All claims above about IC-9700, IC-705, X6100, X6200, and TX-500 are either
  code-derived or historical-live-only (from a radio since retired or
  destroyed) and cannot be re-verified against physical hardware right now.
- **Several long repository documents were only partially read**, given the
  scope of this consolidation: `docs/plans/2026-04-12-target-frontend-architecture.md`
  (the original April ADR that the v3 decision extends — only its section
  headers were scanned), `docs/internals/radio-state-pipeline-validation.md`
  (612 lines, not opened), and three v3-rework implementation-slice ruling
  documents dated August 2026 (settings-modal boundary, panel-order/workspace
  boundary, state-backed command lifecycle) — these read, from their opening
  sections, as in-progress implementation rulings rather than defect
  registries, but they were not read in full, so a buried defect inside them
  cannot be ruled out.
- **The current frontend source (`App.svelte`, `RadioLayout.svelte`, and
  related) was not re-read at this revision** to check whether item 8's
  Linear-recorded "Done" tickets actually match the code today. This is the
  single highest-value re-check flagged by this inventory (see "How to use
  this," below).
- **Not every document matching "audit" by content was opened.** Roughly 30
  files under `docs/plans/` and `docs/internals/` matched a search for
  audit-adjacent content; this inventory prioritized the ones most obviously
  relevant to the v3/frontend question given the time available. Every
  document actually opened is named above; anything not named was not read.

---

## How to use this

This inventory is a starting point for resuming the v3 migration, not a
finished checklist. Before resuming work:

- **Re-read `App.svelte` and `RadioLayout.svelte` at current `main` before
  assuming item 8 is still the starting condition.** Linear records the two
  tickets scoped to fix exactly that problem (MOR-973, MOR-981) as Done, and
  this inventory could not verify whether the code matches. This is the
  single most consequential open question this document raises: if it is
  actually fixed, the migration may be starting from a materially better
  position than the last written discovery document describes.
- **Re-check item 7's P0 safety gaps against current frontend source before
  assuming they are closed just because Linear says so.** All nineteen
  tickets the discovery document names as its own closure path — the nine
  original prerequisites plus the ten decision/implementation/reference-
  vertical/design-language tickets read on 2026-08-29 — are Done in Linear.
  Whether the code actually satisfies each ticket's acceptance criteria was
  not independently re-verified here.
- **Re-check items 1 and 2 against the separate design document** referenced
  under item 1 — the owner has already ruled on the end state (delete the
  fallback, require the command map, move the request and response sides
  together), so those two items may already be scheduled or superseded by
  the time work resumes.
- **Re-check item 4 (FTX-1 receiver selection)** against MOR-1671's current
  Linear state and against whether #2803 (or a successor) has merged, and if
  so whether the `SelectVfo` case in `web/radio_poller.py` still guards on
  `vfo_main_code`/`vfo_sub_code` for FTX-1 — #2803 as read for this inventory
  fixes FTX-1's `SetFreq`/`SetMode` dispatch to the SUB receiver but leaves
  that guard, and therefore MOR-1671's own acceptance criteria, untouched.
  Its GitHub-side execution issue (#2633) was already closed once as
  not-planned, so this defect has a history of being picked up and dropped.
- **Item 3 (MIC/ACC1 collision) has no test or ticket found for it anywhere
  checked** — repository, tests, or Linear. It is the item in this list most
  likely to be silently missed unless someone explicitly files it.
- **Read the remaining 65 title-only child issues of the structural
  reliability audit (MOR-1807–1877, minus MOR-1839/1840/1841/1874/1873/1877
  named above)** before assuming this inventory and that audit are fully
  reconciled — only titles were compared, not full descriptions, for any of
  the 71.
- **Closing the remaining Linear gap** (MOR-465, plus full bodies of all 71
  structural-reliability-audit children) needs the same headless BWS-key
  read path used for this inventory, run again with those specific ticket
  numbers.
