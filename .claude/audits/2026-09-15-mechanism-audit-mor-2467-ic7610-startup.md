# Mechanism audit: IC-7610 startup-optional scope state

- Audited revision (exact HEAD): `d53e91e5de30d5bb0a2fb7492f78791e813b0741`.
- Exact base: `bd75f60ca0296fcc9c82aebd5ed266dabbbfce74`.
- Ticket: MOR-2467.
- Final verdict: **PASS**.
- Read-only throughout; the independent auditor ran no tests, server, LAN, or
  radio operations.

## Scope and reconstructed path

The bounded tract is the startup-admission exception added for the IC-7610's
scope-control state:

`rig profile startup_optional declaration -> rig loader validation ->
FieldCapability.startup_required -> AcquisitionScheduler startup predicate ->
web listener admission`.

The adjacent acquisition path was checked to distinguish admission from
querying. `startup_optional` does not mean unavailable, non-pollable, or
deferred until a browser session: the initial state sweep and cadence scheduler
may still query these paths, and the existing scope lifecycle hydrates them
again after `EnableScope`.

Mac mini tests, lint, typing, server startup, and live RX-only browser evidence
are separate implementation evidence; they were not produced by the audit.

## Definition, liveness, and dead-code census

| Element | Written / read | Production consumer set | Result |
|---|---|---|---|
| `startup_optional` | profile declaration 1 / loader parse and exact-set test | `_parse_state_acquisition` | live |
| `FieldCapability.startup_required` | declaration, normalization, loader construction / scheduler, serializer, parser | `unobserved_startup_paths`, model round-trip | live |
| serialized `startupRequired` | `to_dict` write / `from_dict` read with strict validation | profile model round-trip | live |
| `optional_without_acquisition` | local definition / rejection guard and diagnostic | loader validation | live |
| `_STATE_ACQUISITION_CAPABILITY_KEYS` | definition 1 / parser validation 1 | state-acquisition loader | live |
| IC-7610 13-path list | profile declaration / loader and scheduler projection | startup predicate; paths remain pollable | live |
| `initial_acquisition_complete` | definition 1 / production calls 0 / test calls 6 | tests plus unknown external direct imports | pre-existing, undetermined |

Definition sites are frozen at
`rigs/ic7610.toml:269`,
`src/rigplane/profiles/rig_loader.py:1533,1679-1794`,
`src/rigplane/core/state_acquisition_policy.py:420-538`, and
`src/rigplane/core/acquisition_scheduler.py:1047-1098`.

Literal-name and dynamic-access searches found no second production writer or
alternate startup gate for the new capability. No sibling-repository consumer
was found. Unknown external direct imports of the internal Core submodule
remain unknowable.

## Deletions

### D1 — pre-existing `initial_acquisition_complete`: undetermined

Verdict:          undetermined
Elements:         `AcquisitionScheduler.initial_acquisition_complete`
Consumers:        zero production calls; six direct test assertions
Written / read:   one definition / six calls, established by literal `rg -n "initial_acquisition_complete" src tests`; the test-module description is not a call
Guards checked:   literal and dynamic access searched; tests are the only in-repo consumers; `rigplane.core` is internal but out-of-repo direct imports cannot be disproved
Collateral:       six test assertions and the `tests/test_startup_state_gate.py` module description would need removal with the helper
Definition site:  `src/rigplane/core/acquisition_scheduler.py:1087-1098`
Divergence:       delegates directly to `unobserved_startup_paths`
Prior ruling:     none found
In-flight:        no production migration or deletion found
Required surface: tests can call the canonical predicate directly
Depends on:       possible external direct import, which is not provable in-repo
Confidence:       medium
Falsifier:        a runtime or supported external consumer
Fix class:        none while the external-consumer guard is unresolved
Actionable:       no; the failed guard requires an owner decision before deletion

No delta-added dead runtime code or stale session-deferral documentation
remains.

## Prior rulings and in-flight mechanisms

- Owner ruling R36b, recorded 2026-09-08 at `139fc933`, retains one startup
  gate that normally waits for declared state before the listener serves.
- The new profile-owned exception narrows that gate without adding another
  gate or a Web-specific radio-model check.
- `src/rigplane/runtime/radio_initial_state.py:30-80` remains the initial query
  sweep.
- `StateFreshnessService.tick` remains the cadence owner.
- `src/rigplane/web/radio_poller.py:1275` remains the post-`EnableScope`
  scope-control hydration path.
- `available_when` is not a replacement: it resolves observed canonical radio
  state and has no scope-session lifecycle fact.

## Steelman

The strongest objection is that `startup_optional` could be read as "do not
query during startup." The scheduler documentation and tests bound the term to
listener admission: the path remains acquisitive and pollable. This is narrower
and more accurate than introducing browser-session state into Core or falsely
claiming that all pre-session queries are suppressed.

## Consolidations

### F1 — startup admission belongs to `FieldCapability`

Verdict:          C
Rank:             parallel
Elements:         `startup_optional`; `FieldCapability.startup_required`; `AcquisitionScheduler.unobserved_startup_paths`
Consumers:        loader writes the field; serializer preserves it; scheduler reads it; startup gate calls the scheduler
Definition site:  `rigs/ic7610.toml:269`; `state_acquisition_policy.py:420-538`; `rig_loader.py:1679-1794`; `acquisition_scheduler.py:1047-1098`
Divergence:       none; all 13 optional paths are exactly the 13 IC-7610 `scope_controls.global.display.*` polling paths
Prior ruling:     "the web server serves only after the initial poll has completed" — 2026-09-08, owner ruling R36b at `139fc933`; the new profile exception keeps that single gate
In-flight:        initial sweep, cadence polling, and post-`EnableScope` hydration remain distinct lifecycle mechanisms
Required surface: capability metadata; an `AcquisitionPolicy` placement would duplicate declarations for pollable paths without explicit policy entries
Depends on:       neither `available_when` nor a WebServer model-specific exception
Confidence:       high
Falsifier:        a requirement that these fields must never be queried before `EnableScope`
Fix class:        none
Actionable:       no — one owner and one startup-admission consumer exist

### F2 — serialization is backward-compatible in-repository

Verdict:          already-shared
Rank:             parallel
Elements:         additive `startupRequired` model key; strict bool validation; missing-key default
Consumers:        `FieldCapability.to_dict`; `FieldCapability.from_dict`; loader-created profiles; scheduler runtime
Definition site:  `src/rigplane/core/state_acquisition_policy.py:420-538`
Divergence:       none; absent `startupRequired` restores `True`, while `False` round-trips
Prior ruling:     no conflicting format contract found
In-flight:        no HTTP or WebSocket capability-payload consumer found
Required surface: `FieldCapability`, preserving profile portability
Depends on:       unknown third-party strict consumers of this internal model's raw dictionary output
Confidence:       high in-repository; medium for unknown external consumers
Falsifier:        an external consumer that rejects additive dictionary keys
Fix class:        none
Actionable:       no; document separately if this raw nested serialization becomes supported wire API

## Weakest link

Unknown external strict consumers of `FieldCapability.to_dict()` are the only
compatibility uncertainty. Internal backward parsing is explicit, and the
model is not exported from the stable root API.

## Cleared

- The false `deferred_until_session` terminology and session-deferral claim.
- The stale scheduler docstring and test-only old naming.
- IC-7610 13-path declaration parity.
- Loader validation that optional paths retain an acquisition route.
- Capability-layer placement versus policy or WebServer special-casing.
- Duplicate scope-session or demand-mechanism risk.
- In-repository serialization compatibility.

## Verdict

**PASS.** The fix changes the existing startup-admission mechanism at its
profile-owned boundary. It does not create a second gate, make the scope state
non-pollable, or claim session deferral that the runtime does not enforce.
