# Reverse command index and ingress decode rules (MOR-1993 → MOR-2010)

Status: owner-ratified design, 2026-08-31/09-01. Companion to
`docs/plans/2026-08-29-profile-driven-command-bytes.md` (the completed MOR-2000
epic); this plan covers the reverse half that epic's §8.1 Q3 explicitly deferred.

## 1. Problem

The MOR-2000 epic moved every *outgoing* request and every *solicited-reply*
shape onto the profile command map. What remains hardcoded is the ingress
direction — decoding frames the radio sends on its own — plus a small tail the
epic documented as out of scope. Measured at `bce3be1e` (AST census, 2026-08-31,
re-derived rather than inherited from this ticket's 2026-08-29 numbers):

- `runtime/_civ_rx.py`: 105 hardcoded `frame.command`/`frame.sub` comparisons,
  byte-for-byte unchanged since the ticket's baseline, plus 14 literal
  dict-lookup sites (15 hardcoded tables) of the same nature.
- The same file contains **two parallel decode paths**: the canonical
  observation flow (`CivRuntime._observations_from_frame`) and a legacy mirror
  (`_RADIO_STATE_HANDLERS` → `_handle_XX`), each carrying its own copy of the
  byte knowledge. The "26 unsolicited scope-shape sites" flagged in the
  PR #2917 review are exactly this duplication: 13 sites in
  `_scope_control_observations` mirrored by 13 in `_handle_27`.
- `runtime/radio.py`: 5 literal matcher sites remain (`get_filter_width`,
  `get_antenna_1`/`get_antenna_2`, and the two RX-antenna variants) — the
  4 antenna sites were documented exceptions in the epic's batch 2.
- `commands/system.py`: the three date/time/UTC parsers hardcode the control
  register bytes, discarding the profile-derived command/sub their callers
  already compute. Dormant (all profiles agree today), structurally the same
  gap.
- `CivRuntime._route_civ_frame` silently drops any frame not addressed to the
  controller. This is where the IC-7300's own bus-bridged PTT push frames
  (addressed to `0x01`) die today — a documented would-be customer of this
  index (bench observation, draft, 2026-08).

Reverse lookup is not an inversion: at `(command, sub)` granularity — grouping
by what `commands/_frame.py: parse_civ_frame` actually assigns to
`frame.command`/`frame.sub` for each declared tuple, not a positional split of
the tuple — the IC-7300 profile has 100 keys with 64 collisions (measured at
`bce3be1e`); on IC-705 and X6200 the tuple `1C 00` resolves to four names.

## 2. What the collision census actually showed

Programmatic inversion of all eight profiles, with every colliding key
classified by what disambiguates it:

- **(a) payload length** — the majority class. A `get_x`/`set_x` pair shares a
  prefix; the read form has no payload, the write form carries one. The rule
  "no payload = the read name" resolves it. This is *derivable from the
  declared tuples*; no annotation needed.
- **(b) payload value** — the on/off write pairs (`ptt_on`/`ptt_off`,
  `power_on`/`power_off`, scope on/off) are distinct full tuples differing in
  the trailing byte. Full-tuple matching resolves them; the class recurs
  identically across unrelated families, confirming it is a CI-V-wide
  convention, not a per-radio fact.
- **(c) direction-only** — families that exist only as writes and never elicit
  a reply (scan `0x0E`, CW keying `0x17`). Nothing derivable marks them; this
  is the class that needs an *annotation*.
- **(d) genuine residual** — exactly one: `set_transceiver_status` on
  IC-705/X6200 is byte-identical to its get form and has no builder anywhere in
  `src/`. Real ambiguity, no live consumer; annotated for honesty, decides
  nothing.

Conclusion the owner ratified: the rule set is a property of the CI-V protocol,
not of individual radios. There are no per-radio rule exceptions today.

## 3. Owner rulings (2026-08-31, in-session; recorded on MOR-1993/MOR-2010)

1. **Mirror deletion first.** The legacy `_RADIO_STATE_HANDLERS` decode mirror
   is deleted as this plan's first step — its own PR with path-equivalence
   evidence — before any decode migration. Halves the migration surface.
2. **Rules are declared in TOML as fact-annotations**, small fixed vocabulary,
   with one shared interpreter in code containing zero hardcoded bytes. A full
   rule language inside TOML is declined; the upgrade path from annotations is
   additive (a future rules section would consume the same facts; files migrate
   by mechanical regen).
3. **MOR-2010 closes only on FULL migration**: the profile-driven double
   exists, every test using the three hand-written doubles (and the 2026-08-29
   subclass) has moved, and the old doubles are deleted. Migration proceeds in
   reviewed batches.
4. The shared-ICOM-base-profile idea is filed separately (MOR-2088,
   unscheduled) and does not shape this design.

## 4. Design

### 4.1 Reverse index

Built by the rig loader beside `command_map`, exposed as a sibling field on the
profile (the layer precedent: `RadioProfile.command_map`; `core/` is
layer-illegal for profile-specific structures per `.importlinter`). Resolution
order for an incoming frame, all steps data-driven. An annotation is a fact
attached to its declared row, consulted at whichever step below examines that
row — never a separate later step that a derivable rule can preempt with a
confident wrong answer:

1. **Full-tuple match** — the frame's `(command, sub, leading payload)` against
   declared tuples; a matching row's own annotation, if any, decides the
   outcome directly (resolves class (b) pairs exactly as declared).
2. **Prefix match** — for a `(command, sub)` prefix with no full-tuple match:
   if the row(s) declared at that prefix carry an annotation (class (c)/(d)),
   the annotation decides; otherwise the payload-length rule applies — no
   payload beyond the declared prefix = the read name, payload present = the
   write name (class (a)).
3. **No match** — an explicit "unrecognized frame" outcome (never a silent
   guess; mirrors the D1 unknown-command refusal philosophy).

The index is constructed once per profile load; collision counts per profile
are pinned by a census test so the numbers in this plan fail loudly when the
data moves.

### 4.2 Annotation vocabulary (TOML)

Inline-table form on the command row, e.g.
`send_cw = { bytes = [0x17], reply = "none" }`. Initial vocabulary — exactly
what class (c)/(d) needs and nothing speculative:

- `reply = "none"` — write-only; the radio never answers with this tuple. CW
  keying (`0x17`) is already hardcoded this way in `_civ_expects_response`;
  scan (`0x0E`) is not — it falls through to that function's default
  `len(frame.data) == 0` heuristic, which returns `True` (expects a reply) for
  an empty-data scan frame. Migrating scan onto `reply = "none"` is therefore
  a **behavior change** (heuristic expect-reply → none), not a pure refactor,
  and Z3 must validate it separately from the CW case.
- `reply = "echo"` — the radio echoes the set frame (the on/off pairs where
  ingress can legitimately see the write tuple).

Unknown annotation keys or values are load errors (fail closed, matching the
Q5 posture). The vocabulary extends by adding words, never by
embedding logic. `_civ_expects_response`'s hardcoded `frame.command == 0x17`
branch migrates onto `reply = "none"` and is deleted.

### 4.3 Customers, in adoption order

1. `CivRuntime._observations_from_frame` (the canonical ingress decoder).
2. The 5 `runtime/radio.py` stragglers and the 3 `commands/system.py` parsers.
3. The MOR-2010 profile-driven double (state on `core/radio_state.py:
   RadioState`, no `StateStore` ownership).
4. (Future, out of scope) the `0x01`-addressed push-frame path once the drop
   in `_route_civ_frame` is deliberately opened — tracked with TX-authority
   work, not here.

## 5. Steps

Each step is one reviewed PR unless noted; the standard pipeline applies
(builder → independent verifier → CI quick at head → merge; baselines recorded
before EXECUTE).

- **Z1 — mirror deletion.** Delete `_RADIO_STATE_HANDLERS` and the 18
  `_handle_XX` functions; evidence that the canonical path already produces
  every observation the mirror produced (AST inventory of both paths' outputs
  + targeted tests). Expected to be deletion-heavy: counting
  `frame.command`/`frame.sub` `==`/`!=` comparisons against a literal (the
  same method behind the 105 total), the 18 `_handle_XX` functions carry 23
  of them.
- **Z2 — reverse index + census pins.** Loader builds the index; per-profile
  collision censuses pinned; the full-tuple match and prefix + payload-length
  rule implemented, with no annotation lookups yet (added in Z3). Unit
  contract: every declared name round-trips (build → decode) on every
  profile.
- **Z3 — annotation vocabulary.** Loader + validation (unknown = load error);
  `reply = "none"`/`"echo"` rows added to the profiles from the class (c)/(d)
  census (D2 discipline: each row's source is the recon classification, cited);
  `_civ_expects_response` migrates onto the data and its hardcoded `0x17`
  branch is deleted; regen artifacts extended.
- **Z4..Zn — ingress migration in family batches.** `_observations_from_frame`
  and the remaining tables move onto index+rules, family by family (the epic's
  batch discipline: keystone-style red-proofs, two-shape sweeps, regen deltas
  explained). The `radio.py` stragglers and `system.py` parsers fold into their
  families' batches.
- **D1 — the double.** New test-support module: profile-driven fake radio
  (reads the TOML, answers via the index + rules, state on `RadioState`).
  Contract tests written against the double itself.
- **D2..Dm — double migration, full.** The three hand-written doubles (and the
  subclass) retire in reviewed batches; each batch moves a coherent test
  cluster; the old doubles are deleted in the final batch. MOR-2010 closes
  here.

## 6. Guardrails and risks

- Size guardrails per PR as standard (10/1000 hard, 6/600 soft); the batch
  pattern from the epic applies unchanged.
- The mirror deletion (Z1) is the riskiest single step: the equivalence
  evidence must enumerate mirror outputs, not sample them.
- `1C 00` four-name collision: resolved by steps 1–2 of the resolution order
  for the three live names; the fourth (`set_transceiver_status`) is class (d),
  annotated, no consumer.
- The two CAT radios (ftx1, tx500) are outside the CI-V index entirely; the
  Yaesu path already resolves parsers by name
  (`backends/yaesu_cat/radio.py: YaesuCatRadio._query`) — the property this
  plan brings to the CI-V side.
- Bench validation: the double's fidelity claims stay off the bench; live
  validation remains the domain of the hardware validation suites.
