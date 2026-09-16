# Mechanism audit: Icom canonical observed-PTT producer

**Audit date:** 2026-09-03
**Pull request:** #3062, `feat: publish Icom observed PTT readback`
**Audited source:** `04bb81d6255b8f7b0d89e7338852d07b3abed437`
**Audited tree:** `150c3865a35c984a2bcb9c1d9a83d7574bcedb78`
**Review base:** `e9cda21ee2bf0751173844e205ae73add630b18e`
**Diff surface:** `src/rigplane/runtime/_civ_rx.py` (+22/-4) and `tests/test_civ_rx_coverage.py` (+191/-1)
**Audit type:** independent, read-only helper-level mechanism audit

## Scope and method

This audit applies the repository mechanism-audit order: definition sites, prior rulings, in-flight targets, consumer liveness, steelman, then taxonomy. It reviews the Icom producer slice and its immediate existing PTT helper mechanisms. It does not claim a systematic step-3a dead-code sweep of the 3,000-plus-line CI-V module and makes no deletion verdict for that module.

The source was not authored by this auditor. No product or test source, index entry, commit, branch, CI job, local test, import, parser, linter, or hardware path was changed or run during this audit. The two accepted runtime proof reports supplied by the coordinator were treated as prior evidence only: runs `33774723676` and `33776556788` at source `04bb81d6`/diagnostic `DIAG4a4`, each reporting 85 CALL, 75 PASS, 10 expected outcomes, and 20 supervisor/quiescence/restoration records. Direct loaded-module attestation and exercised forced-kill paths remain unknown.

## Definitions, rulings, in-flight state, and liveness

### Definition sites

- `src/rigplane/core/tx_observation.py:19-39` defines the shared `OBSERVED_PTT_PATH`, `ObservedPtt`, and strict `normalize_observed_ptt` vocabulary. `project_observed_ptt` at lines 42-75 enforces type, StateStore freshness, provider generation, finite timestamps, and the exact max-age boundary.
- `src/rigplane/runtime/_civ_rx.py:2114-2474` defines the Icom protocol adapter. The PTT branch at lines 2453-2474 preserves the legacy boolean observation when data is nonempty and always emits the canonical sibling. Exact `b"\x00"`/`b"\x01"` become `OFF`/`ON`; everything else becomes `UNKNOWN` through the shared normalizer.
- `src/rigplane/runtime/_civ_rx.py:2720-2769` defines the existing Icom observation builder. It assigns provenance, the Icom provider/transport identifiers, timestamp, max age, and quality before the existing StateStore apply path consumes the result.
- `src/rigplane/runtime/_civ_rx.py:1570-1618` defines ingress qualification: configured radio address, controller/broadcast destination, and current CI-V epoch. Lines 1879-1948 bind the current StateStore provider generation and apply through the one existing store.
- `src/rigplane/runtime/_civ_rx.py:1635-1669` defines the older direct managed-safety `ProviderPttObservation` producer.
- `src/rigplane/core/observation_adapter.py:25-63` defines the shared profile-aware `ProviderObservationAdapter` used by other backends. Its `max_age` default comes from `profile.policy_for(path).freshness_ttl_seconds`.

### Prior rulings

- The 2026-09-01 runtime transmit-authority ADR, `docs/plans/2026-09-01-runtime-transmit-authority.md:290-317`, rules that StateStore is the sole canonical tri-state seat; missing, stale, invalid, or old-generation evidence becomes `UNKNOWN`; the boolean StateStore field and `RadioState.ptt` are deprecated compatibility projections; observation must not control authority; and observer-driven clearing/gating in `TxSafetySupervisor` must be removed during migration.
- The same ADR at lines 47-61 assigns manufacturer wire vocabulary to the provider adapter and normalized observation truth to the StateStore observer boundary. This sanctions protocol-local CI-V decoding while rejecting a second long-lived truth owner.
- `docs/plans/2026-08-20-transmit-authority.md:1390` already records the Icom PTT max-age migration: source the current 1.0-second value from the profile, retaining the hardcode only as a fallback. The IC-7300 profile currently declares that same value at `rigs/ic7300.toml:297-302`, so this is a source-of-definition migration rather than a window change.
- The earlier 2026-08-30 TX-truth mechanism audit, `.claude/audits/2026-08-30-mechanism-audit-tx-truth.md:309`, cleared `_emit_authoritative_ptt` as one internally coherent generation-bound readback implementation. That ruling predates the accepted sole-StateStore migration target; it supports the helper's internal correctness, not permanent parallel ownership.

### In-flight target and consumer liveness

- The canonical contract already exists in Core and is imported by the Icom producer. `OBSERVED_PTT_PATH` is also a read-only string field in the registry at `src/rigplane/core/state_pipeline_contracts.py:1227-1231`.
- The new producer reaches the live StateStore apply path and state-store-changed notification. Its polling liveness is inherited from the existing `global.tx_state.ptt` acquisition: the same `0x1C/0x00` reply yields both observations, so a second query is neither required nor added.
- `project_observed_ptt` has tests but no literal production caller in this source tree. The web StateStore projection currently omits `observed_ptt`: `_GLOBAL_TX_FIELDS` at `src/rigplane/web/runtime_helpers.py:129-142` does not list it, `_apply_snapshot_field` publishes only listed TX fields at lines 1080-1086, and `ServerStatePublic` at `src/rigplane/web/state_schema.py:287-298` exposes only the legacy `ptt` boolean. The UI control contract names the StateStore path at `docs/internals/ui-radio-control-contract.toml:315`, so this is an incomplete staged migration rather than evidence that the producer is dead.
- `src/rigplane/runtime/radio.py:3437-3472` consumes the same protocol-local decoder for the solicited backend-neutral `TxStateReading`, selecting the legacy PTT observation and requiring a source in `RADIO_READBACK_SOURCES`. This is a one-shot read API, not another StateStore truth seat.
- `_emit_authoritative_ptt` has a live consumer: `src/rigplane/runtime/managed_radio_runtime.py:154-166` forwards exact generation-bound events into `TxSafetySupervisor.observe_ptt`. Its sequence boundary and provider-generation semantics are defined by `src/rigplane/core/tx_safety.py:100-115,175-186,351-365`.

## Steelman of the current arrangement

The strongest case for the current PR is strong. CI-V frame structure, radio/controller addresses, broadcast meaning, command/subcommand bytes, and exact payload validation are manufacturer protocol concerns. Moving those details into the backend-neutral enum or StateStore would contaminate the shared contract with Icom vocabulary. The diff instead maps exact Icom evidence into an already-shared tri-state and publishes it through the existing StateStore path. It keeps the deprecated boolean behavior byte-for-byte compatible so this producer slice does not silently change existing consumers.

The strongest case for keeping `_emit_authoritative_ptt` beside the StateStore producer is that the managed safety path needs causally ordered, provider-generation-bound, sequence-numbered events, while StateStore is a freshness-oriented public observation model. Its stricter event type forbids `UNKNOWN` and its boundary checks answer a different short-lived question: whether a particular post-command readback can settle a managed transition. The prior audit found this helper internally coherent.

That steelman justifies the protocol-local adapter and the one-shot `TxStateReading` API. It does not establish permanent dual truth ownership. The current ADR expressly makes StateStore the sole canonical observation seat and schedules removal of observation-driven supervisor transitions. Therefore the direct stream is best described as a live migration predecessor with distinct current consumers, not as a new defect introduced by PR #3062.

## Deletions

None established. This audit did not perform the systematic step-3a enumeration required for a deletion claim. In particular, absence of a literal production call to `project_observed_ptt` is insufficient for deletion because it is a public Core surface, the StateStore field is registered, a UI contract names the path, dynamic serialization exists elsewhere in the web stack, and out-of-repository open-core consumers were not examined.

## Consolidations

### F1 — CI-V `0x1C/0x00` to canonical tri-state: correctly located protocol adapter

**Verdict:**          C — legitimately local
**Rank:**             parallel
**Elements:**         `src/rigplane/runtime/_civ_rx.py:_observations_from_frame`; `src/rigplane/core/tx_observation.py:normalize_observed_ptt`
**Consumers:**        the local decoder is consumed by `_apply_state_store_observations` and `IcomRadio.read_transmit_state`; the shared normalizer is consumed by the Icom adapter and Core tests
**Definition site:**  wire interpretation at `_civ_rx.py:2453-2474`; backend-neutral vocabulary at `tx_observation.py:19-39`
**Divergence:**       intentional: Icom owns byte validation; Core owns `OFF | ON | UNKNOWN` and rejects truthy coercion
**Prior ruling:**     2026-09-01 ADR lines 47-61 and 290-306 assign manufacturer vocabulary to providers and canonical tri-state truth to StateStore
**In-flight:**        consumer migration is incomplete; the producer slice exists and the public web consumer does not yet
**Required surface:** exists: `OBSERVED_PTT_PATH`, `ObservedPtt`, `normalize_observed_ptt`, `project_observed_ptt`, and StateStore `Observation`
**Depends on:**       none
**Confidence:**       high
**Falsifier:**        a second Icom-neutral wire decoder already shared by all backends that accepts a raw `CivFrame` without importing CI-V vocabulary into Core
**Fix class:**        none
**Actionable:**       no; moving the byte-level branch would place protocol vocabulary in the wrong layer

Malformed and non-boolean evidence is fail-honest on the canonical path. Empty, `02`, `ff`, and multi-byte payloads publish `ObservedPtt.UNKNOWN`. Only exact one-byte `00`/`01` can receive directed `poll_response` provenance. The legacy boolean branch continues truthy coercion for nonempty data, but that behavior remains confined to the ADR's deprecated compatibility field and is explicitly pinned by tests at `tests/test_civ_rx_coverage.py:1192-1229,1353-1375`.

### F2 — direct managed `ProviderPttObservation` stream: live predecessor beside the canonical StateStore seat

**Verdict:**          A — displaced, migration incomplete
**Rank:**             diverged
**Elements:**         `src/rigplane/runtime/_civ_rx.py:_emit_authoritative_ptt`; `_observations_from_frame` plus `_apply_state_store_observations`; `src/rigplane/runtime/managed_radio_runtime.py:_observe_ptt`
**Consumers:**        direct stream -> `ManagedRadioRuntime` -> `TxSafetySupervisor`; canonical stream -> StateStore and state-change notification
**Definition site:**  direct event construction at `_civ_rx.py:1635-1669`; canonical contract at `core/tx_observation.py:19-75`
**Divergence:**       direct stream emits only exact ON/OFF with its own sequence and generation boundary and can drive supervisor transition state; canonical stream also publishes UNKNOWN, TTL, provenance, and Store generation but presently has no authority consumer
**Prior ruling:**     the 2026-08-30 audit cleared the direct helper's internal generation binding; the later 2026-09-01 ADR lines 292-317 rules StateStore sole and requires removal of observer-driven supervisor clearing/gating
**In-flight:**        canonical tri-state contract and this Icom producer exist; downstream authority migration is not in this PR
**Required surface:** already selected by the ADR; the remaining work is consumer migration and old-stream retirement, not another observation primitive
**Depends on:**       the accepted authority migration that removes `TxSafetySupervisor` observation-driven state transitions
**Confidence:**       medium-high
**Falsifier:**        a newer owner ruling that permanently exempts causal managed-readback events from the sole-StateStore observation rule
**Fix class:**        consolidate as part of the existing migration
**Actionable:**       no in PR #3062; deleting or rerouting the live managed stream here would broaden a bounded producer slice and could break managed release causality

This is the audit's key distinction: the CI-V decoder is legitimately local, but two live truth publications from the same frame are a temporary migrated/unmigrated pair. The old stream is not dead, and merging its causal semantics blindly into a general StateStore projection would be unsafe.

### F3 — Icom PTT max age: canonical value, displaced source of definition

**Verdict:**          A — displaced, previously recorded
**Rank:**             parallel
**Elements:**         `_OBSERVATION_MAX_AGE_SECONDS[("global", "tx_state", "ptt")]` at `_civ_rx.py`; `rigs/ic7300.toml` field policy; `core/observation_adapter.py:ProviderObservationAdapter`
**Consumers:**        Icom `_observation` reads the module table; `AcquisitionScheduler` and shared backend adapters read profile policy; the new observed sibling aliases the existing Icom PTT row
**Definition site:**  Icom table currently defines 1.0 seconds; IC-7300 profile independently defines 1.0 seconds
**Divergence:**       none in value for IC-7300 at this source; ownership differs and could drift for another profile or later edit
**Prior ruling:**     `docs/plans/2026-08-20-transmit-authority.md:1390`, row 14, already requires the Icom PTT row to become profile-sourced with hardcoded fallback
**In-flight:**        shared `ProviderObservationAdapter` and profile policy exist; Icom `_observation` has not migrated generally
**Required surface:** exists: `RadioAcquisitionProfile.policy_for(path).freshness_ttl_seconds`; an Icom-compatible fallback decision is already recorded
**Depends on:**       the existing profile-TTL migration owner; not on the observed-PTT producer
**Confidence:**       high on duplicate definition; medium on the exact safest Icom integration because `_observation` covers many fields and has special provenance rules
**Falsifier:**        a current owner ruling that the Icom table, rather than the rig profile, permanently owns observation TTL
**Fix class:**        consolidate under the already planned profile migration
**Actionable:**       no in PR #3062; the diff correctly avoids adding another constant and preserves the current 1.0-second behavior

### F4 — solicited `TxStateReading`: separate read API, not a competing truth store

**Verdict:**          C — legitimately local
**Rank:**             parallel
**Elements:**         `src/rigplane/runtime/radio.py:read_transmit_state`; `_civ_rx.py:_observations_from_frame`; `core/tx_observation.py:TxStateReading`
**Consumers:**        backend-neutral radio callers requesting one bounded read; no StateStore ownership
**Definition site:**  result contract at `core/tx_observation.py:84-92`; Icom transport implementation at `runtime/radio.py:3437-3472`
**Divergence:**       returns `bool | None`, provenance, verification, or failure for one solicited call; it does not retain freshness state or publish another canonical field
**Prior ruling:**     the observation ADR separates polling/readback evidence from authority and gives provider adapters the wire vocabulary
**In-flight:**        none required for this producer review
**Required surface:** exists
**Depends on:**       none
**Confidence:**       high
**Falsifier:**        evidence that callers treat `TxStateReading` as a persistent canonical state seat rather than an ephemeral read result
**Fix class:**        none
**Actionable:**       no

## Weakest link

F2 is the weakest verdict. The code establishes two live output mechanisms, and the current ADR says observer-driven supervisor transitions must not survive beside the StateStore seat. The uncertain part is timing and actionability: this audit did not resolve the exact Linear owner, dependency, or replacement boundary for the direct causal stream. Before acting, verify the current authority-migration owner and whether a post-ADR ruling explicitly preserves `ProviderPttObservation` as a noncanonical causal event channel.

## Cleared

- **Shared tri-state vocabulary and normalization.** One Core definition; the Icom producer imports it rather than copying an enum or truthy normalizer.
- **Malformed/non-boolean handling.** Canonical values fail to `UNKNOWN`; only the deprecated compatibility boolean retains prior truthiness.
- **Ingress and generation qualification.** Foreign source, echo destination, old CI-V epoch, and old StateStore provider generation cannot restore canonical ON/OFF. The tests at `tests/test_civ_rx_coverage.py:1192-1375` cover valid, malformed, broadcast, foreign/echo, epoch, store-generation, and exact-TTL cases.
- **Readback provenance.** Exact directed one-byte PTT replies alone receive `poll_response`; qualified broadcasts receive `civ_unsolicited`; setters, ACKs, and unrelated responses cannot be mislabeled as PTT radio truth.
- **State publication.** The new field uses the existing StateStore apply, generation, notification, and registry mechanisms. No cache, timer, StateStore, policy resolver, or query loop was added.
- **TTL value and boundary.** The sibling inherits the existing Icom PTT 1.0-second max age; `project_observed_ptt` changes to UNKNOWN at `age >= max_age`, even before a freshness-service tick marks the raw field stale.
- **One-shot backend read.** `read_transmit_state` is an ephemeral protocol API, not a second retained truth store.
- **Scope discipline.** The absence of a current web tri-state projection is recorded as staged consumer migration, not converted into a new requirement for the producer PR.

## Unknowns and limits

- No complete step-3a dead-code enumeration of `_civ_rx.py` was performed; this report supplies no module-wide deletion closure.
- Literal in-repository search found no production call to `project_observed_ptt`; dynamic or external open-core consumers were not established.
- The accepted runtime proof's direct loaded-module attestation and exercised forced-kill paths remain unknown.
- No physical-radio, on-wire, owner-present bench, or RF evidence was examined.
- The later LOWER `8bf` change to `_civ_rx.py` is a separate integration and was not audited here.
- The exact downstream consumer-migration and direct-observer-retirement owners were not resolved in this bounded review.
- Profiles other than the observed IC-7300 policy were not exhaustively checked for PTT TTL equivalence.

## Final code verdict

At the pinned source, PR #3062 is acceptable as the bounded Icom producer slice. It places CI-V-specific validation in the protocol adapter, reuses the shared tri-state contract and existing StateStore path, treats malformed evidence honestly, preserves deprecated compatibility behavior, and adds no new truth store or TTL constant. This verdict does not claim completion of the authority/consumer migration, does not waive the recorded profile-TTL consolidation, and does not provide full 3a dead-code closure.
