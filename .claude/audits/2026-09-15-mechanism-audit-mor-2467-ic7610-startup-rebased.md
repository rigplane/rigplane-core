# Mechanism audit: IC-7610 startup-optional scope state after rebase

- Audited revision (exact implementation HEAD):
  `62a1c1070210ef4e96f9ed772c60056dcd96574c`.
- Exact base: `39c88afd6d5d9ebf98882d9c1995c4e4306cd72d`.
- Ticket: MOR-2467.
- Final verdict: **PASS**.
- Read-only throughout; the independent auditor ran no tests, server, LAN, or
  radio operations.
- Supersedes the earlier audit at `d53e91e5` for merge evidence after the base
  moved through PRs #3474 and #3475; that frozen report remains historical.

## Scope and reconstructed path

The bounded tract remains:

`rig profile startup_optional declaration -> rig loader validation ->
FieldCapability.startup_required -> AcquisitionScheduler startup predicate ->
web listener admission`.

The rebase changed only upstream TX-interlock code overlapping the loader test
file. Conflict resolution kept the required `FieldPath` import and did not
restore any removed TX-interlock imports. The startup-optional mechanism is
otherwise semantically identical to the previously audited implementation.

`startup_optional` means excluded from listener readiness only. The fields stay
acquisitive and pollable: the initial state sweep and cadence scheduler may
query them, and the existing scope lifecycle hydrates them after
`EnableScope`.

Mac mini checks and live RX-only browser evidence are separate implementation
evidence and were not produced by this audit.

## Definition, liveness, and dead-code census

| Element | Written / read | Production consumer set | Result |
|---|---|---|---|
| `startup_optional` | profile declaration 1 / loader parse and exact-set test | `_parse_state_acquisition` | live |
| `FieldCapability.startup_required` | declaration, normalization, loader construction / scheduler, serializer, parser | startup predicate and model round-trip | live |
| serialized `startupRequired` | `to_dict` write / `from_dict` read and strict validation | profile model round-trip | live |
| `optional_without_acquisition` | local definition / rejection guard and diagnostic | loader validation | live |
| `_STATE_ACQUISITION_CAPABILITY_KEYS` | definition 1 / parser validation 1 | state-acquisition loader | live |
| IC-7610 13-path list | profile declaration / loader and scheduler projection | startup predicate; paths remain pollable | live |
| `initial_acquisition_complete` | definition 1 / production calls 0 / test calls 6 | tests plus unknown external direct imports | pre-existing, undetermined |

Definition sites are frozen at `rigs/ic7610.toml:269`,
`src/rigplane/profiles/rig_loader.py:1524,1670-1785`,
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
Depends on:       possible external direct import, which is not provable in-repo
Confidence:       medium
Falsifier:        a runtime or supported external consumer
Fix class:        none while the external-consumer guard is unresolved

No delta-added dead runtime code survived the census.

## Prior rulings and in-flight mechanisms

- "the web server serves only after the initial poll has completed" — owner
  ruling R36b, 2026-09-08, commit
  `139fc933961318f143dced8755936ae9a314421e`.
- The profile-owned exception narrows that single gate without adding another
  gate or a Web-specific model check.
- `src/rigplane/runtime/radio_initial_state.py:30-80` remains the initial query
  sweep; `StateFreshnessService.tick` remains the cadence owner; and
  `src/rigplane/web/radio_poller.py:1275` remains post-`EnableScope` hydration.
- `available_when` is not a replacement because it resolves observed canonical
  radio state and has no scope-session lifecycle fact.

## Steelman

The strongest objection is that `startup_optional` could mean "never query
during startup." The scheduler documentation and tests instead bind it to
listener admission while acquisition remains valid. This is narrower than
introducing browser-session state into Core and avoids a false session-deferral
claim.

## Consolidations

### F1 — startup admission belongs to `FieldCapability`

Verdict:          C
Rank:             parallel
Elements:         `startup_optional`; `FieldCapability.startup_required`; `AcquisitionScheduler.unobserved_startup_paths`
Consumers:        loader writer; serializer/parser; scheduler reader; Web startup gate
Definition site:  `rigs/ic7610.toml:269`; `rig_loader.py:1524,1670-1785`; `state_acquisition_policy.py:420-538`; `acquisition_scheduler.py:1047-1098`
Divergence:       none; exact 13/13 scope-display polling-path parity
Prior ruling:     "the web server serves only after the initial poll has completed" — 2026-09-08, owner ruling R36b at `139fc933961318f143dced8755936ae9a314421e`
In-flight:        initial sweep, cadence, and post-`EnableScope` hydration remain distinct
Required surface: capability metadata; policy placement would duplicate field declarations
Depends on:       neither `available_when` nor a WebServer model exception
Confidence:       high
Falsifier:        a requirement that these fields must never be queried before `EnableScope`
Fix class:        none
Actionable:       no — one owner and one startup-admission consumer exist

### F2 — serialization is backward-compatible in-repository

Verdict:          already-shared
Rank:             parallel
Elements:         additive `startupRequired`; strict validation; absent-key default
Consumers:        `FieldCapability.to_dict`; `FieldCapability.from_dict`; loader-created profiles; scheduler
Definition site:  `src/rigplane/core/state_acquisition_policy.py:420-538`
Divergence:       none; an old payload restores `True`, while `False` round-trips
Prior ruling:     none found
In-flight:        no HTTP or WebSocket capability-payload consumer found in-repository
Required surface: `FieldCapability`, preserving portable profile semantics
Depends on:       unknown third-party strict consumers of raw internal-model dictionaries
Confidence:       high in-repository; medium externally
Falsifier:        an external strict parser rejecting additive keys
Fix class:        none
Actionable:       no; document only if promoted to supported wire API

## Weakest link

Unknown third-party strict consumers of `FieldCapability.to_dict()` are the
only compatibility uncertainty. Backward parsing is explicit, and the model is
not exported from the stable root API.

## Cleared

- Rebase conflict resolution and removal of obsolete TX-interlock imports.
- False session-deferral terminology and stale documentation.
- Duplicate scope-session or demand mechanism risk.
- IC-7610 exact 13-path parity and acquisition-route guard.
- Capability-layer ownership and in-repository serialization compatibility.

## Verdict

**PASS.** The rebased fix still changes the existing startup-admission
mechanism at its profile-owned boundary and introduces no competing mechanism.
