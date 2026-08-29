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
into an environment variable for the duration of two read-only GraphQL
queries against `api.linear.app/graphql`, then unset. No mutation query was
sent. The key was never printed and was not written into this document or
any other file. Two queries were run:

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

Not queried, and therefore unknown-status in this document: MOR-982, MOR-986,
MOR-983, MOR-984, MOR-987, MOR-975, MOR-976, MOR-977, MOR-978, MOR-979 (the
decision items and downstream implementation slices that
`docs/plans/2026-07-25-ui-composition-discovery-and-parity.md` names as the
*actual* closure of its P0 safety gaps and reference vertical — see item 7),
and MOR-465 (RIT/XIT naming, mentioned in item 10, left as previously
described from the repository document alone). A GitHub issue and a GitHub
PR referenced from Linear ticket content were additionally read via `gh`
(read-only) to resolve one specific discrepancy — see item 4.

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
    revision. Ticket MOR-1986 also names a second, related defect found
    while investigating this one: the `cmd_map` branch of every
    `get_scope_*` builder silently discards the `receiver` argument that the
    fallback branch honors (filed as its own instance, MOR-1981, not
    separately queried for this inventory).
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
actually do this today; the rest either need a code change to work (a missing
polling-list entry, a missing dispatch branch, or a missing parser — roughly
46 controls across two gap classes), track only the MAIN receiver and are
silently wrong for SUB (5 controls), or are deliberately excluded as not
applicable (7 controls).

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
  slices as the path to closing its own P0 gaps. Of the ones queried for
  this inventory, **all are marked Done**: MOR-973 ("make App the
  presentation composition root"), MOR-974 ("consolidate capability-derived
  presentation selectors"), MOR-980 ("P0: make frontend PTT delivery
  asymmetric and non-replayable" — its own acceptance criteria specifically
  cover `ptt_off` bypassing degraded-health rejection and reconnect never
  emitting stale PTT-ON, i.e. two of the five plain-language gaps listed
  above), MOR-981 ("establish the App presentation-selection seam"), MOR-985
  ("pin and validate the authoritative capability wire"), MOR-989 ("disable
  raw MediaSession PTT"), MOR-990 ("split local TX audio stop from confirmed
  MOD restore"), MOR-991 ("enforce exact receiver/VFO topology pairs in
  profile loader"), and MOR-993 ("interim safety: disable ungated backend
  MOD restore on session teardown").
- **What this does not establish:** each of these tickets is explicitly
  scoped as a narrow, bounded, "atomic implementation slice" (their own
  wording), not the full safety picture the discovery document describes.
  The document's migration sequence names further items as the actual
  closure of "one App-owned TX controller" and "one backend TX supervisor"
  (decision tickets MOR-982 and MOR-986) and their downstream implementation
  (MOR-983, MOR-984), plus the reference-vertical proof work (MOR-975,
  MOR-987) and later layout/design-language work (MOR-976 through MOR-979).
  **None of MOR-982, MOR-986, MOR-983, MOR-984, MOR-975, MOR-987, MOR-976,
  MOR-977, MOR-978, or MOR-979 were queried for this inventory** — their
  status is unknown here. So: the specific bounded fail-closed prerequisites
  listed above are confirmed Done, but whether the P0 gaps they were
  prerequisites *for* are themselves fully closed is not established by this
  read. This was also not re-verified against current frontend source code.
- **Evidence strength:** the P0 gaps themselves are documented (with the
  source document's own "Automated"/"Missing"/"Unknown" evidence labels);
  the prerequisite-ticket completion is a directly-read Linear fact for the
  ten tickets named above, and an explicit unknown for the remainder.
- **Blocks v3?** Partially answered by the above: several specific,
  named prerequisites are closed. Whether the P0 gates as a whole are closed
  cannot be answered from what was queried — re-checking the unqueried
  ticket list above is the fastest way to settle it.

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
(a Hamlib-style `M VFOA USB 2400` command), currently fail.

- **Recorded in:** the requesting session's own findings only, proven by
  executing the relevant tests/CI configuration. A separate agent was
  reported to be investigating the root cause concurrently; that
  investigation was not re-verified here. No matching ticket title was found
  among the 79 structural-reliability-audit issues (the closest thematic
  match is epic MOR-1799, "rigctld & TX-safety: truth laundering and
  interlock bypass," but none of its 8 children's titles specifically
  describe an `M VFOA USB 2400` / `RPRT -9` failure, so no direct match is
  claimed).
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
content beyond the title is not claimed here. Two children were named above
because their titles directly overlap an item in this inventory (MOR-1841
under item 1). A few more are worth a reader's attention without being
folded into an item here, because their titles alone suggest overlap with
items already listed but were not read in enough detail to merge confidently:
MOR-1877 ("IC-7610 parity matrix asserts 134/134 implemented against a
permanently-skipped reference" — possibly related to item 6 or to the CAT
audits, not confirmed) and MOR-1873 ("Validation dry-run goldens mint `pass`
from absent checks" — possibly related to the `.presence`-only checks
mentioned in `docs/validation/cat-audits/README.md`, not confirmed). No other
title-level overlap with items 1–10 was found among the remaining 68
children.

**This means the structural reliability audit is, almost entirely, a
separate body of unresolved findings from what is catalogued in items 1–10
above** — only one clear title-level overlap (MOR-1841) was found. Reading
each of the 79 tickets' full descriptions was out of scope for this
consolidation.

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
| Item 7 — P0 safety/architecture gaps | **Partially tracked and partially closed**: ten named prerequisite tickets confirmed Done; the tickets that would close the gaps *fully* (App-owned TX controller, backend TX supervisor, and their downstream work) were not queried and remain unknown. |
| Item 8 — composition root doesn't compose | **Directly tracked, and its two named tickets (MOR-973, MOR-981) are marked Done** — this is the most significant single update from reading Linear: if their acceptance criteria were genuinely met, the starting-state problem this item describes may no longer be current, though this was not independently re-verified against the frontend source. |

**Revised bottom line:** of the ten items, four (1, 4, 6, 8) turn out to
already have direct Linear tickets, one of which (8) may already be resolved
per Linear's own record even though the repository documentation describing
it predates that resolution. Item 7 is partially resolved by name. Three
items (2, 3, 5) remain apparently unfiled anywhere checked. Item 9 remains
new, with a thematically related but distinct ticket now confirmed fixed.
This is a different, more nuanced conclusion than "most findings are new" —
reading Linear changed the picture substantially, exactly as flagged as a
risk in the original Gaps section.

---

## Gaps

- **Ticket ranges and specific tickets not queried, listed exactly so a
  reader knows the boundary of what was checked:** MOR-982, MOR-986,
  MOR-983, MOR-984, MOR-975, MOR-987, MOR-976, MOR-977, MOR-978, MOR-979
  (referenced by `docs/plans/2026-07-25-ui-composition-discovery-and-parity.md`
  as the actual closure path for its P0 gaps and reference vertical — see
  item 7), and MOR-465 (RIT/XIT naming — see item 10). Their titles,
  descriptions, and states are not known here and were not guessed at.
- **Full descriptions of 69 of the 71 child issues under the structural
  reliability audit (MOR-1807–1877) were not read** — only titles, via the
  range query. Two (MOR-1841, and the possible-but-unconfirmed MOR-1877 /
  MOR-1873) are named above by title-level similarity to items already in
  this inventory; the remaining 68 were not matched against anything, and
  reading their bodies could surface further overlap or further genuinely
  new defects that this inventory does not capture.
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
- **Query MOR-982, MOR-986, MOR-983, MOR-984, MOR-975, MOR-987, MOR-976,
  MOR-977, MOR-978, and MOR-979 before assuming item 7's P0 safety gaps are
  fully closed.** Ten narrower prerequisite tickets are confirmed Done, but
  the tickets that would close the gaps completely were not read for this
  inventory.
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
- **Read the remaining 68 untitled-only child issues of the structural
  reliability audit (MOR-1807–1877, minus MOR-1841/1873/1877 named above)**
  before assuming this inventory and that audit are fully reconciled — only
  titles were compared, not full descriptions.
- **Closing the remaining Linear gap** (the ten specific unqueried tickets
  above, plus MOR-465, plus full bodies of 68 structural-reliability-audit
  children) needs the same headless BWS-key read path used for this
  inventory, run again with those specific ticket numbers.
