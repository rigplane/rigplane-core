> **Point-in-time audit snapshot (2026-09-03).** Produced with the
> `.claude/skills/mechanism-audit/SKILL.md` method in **bounded helper-level
> mode**. Tree audited: `9b1a008441245057c60e73dd4ac3e846be8dd6d8`. This is an
> archived report, not maintained documentation; citations resolve at that tree.

# Mechanism audit — rigctld observed PTT

**Scope:** the existing external-rigctld PTT read, its canonical diagnostic
publication, and the capability declaration. This is not a whole-systematic
dead-code audit, a producer-completion claim, or an RF/hardware guarantee.
Source read only; no local tests, formatting, or scripts ran.

## Evidence and prior rulings

This synthesis records the independent code review on
[PR #3111](https://github.com/rigplane/rigplane-core/pull/3111) separately:
it reported reuse of the normalizer, adapter, and generation mechanism, with
no source defect in this bounded correction. Its PASS is code-only; it does not
claim mutation proof, hardware acceptance, producer migration, or TX cutover.

The accepted Runtime Transmit Authority ADR says the observer is the sole
producer of canonical tri-state evidence and must not own authority transitions
or admission (`docs/plans/2026-09-01-runtime-transmit-authority.md`,
"Boundaries and composition root"). Its state is diagnostic: provider success
is not RF proof. The earlier transmit-authority ADR also permanently keeps the
rigctld-client read `verified_readback=False`; it must never admit a hazard
from that answer (`docs/plans/2026-08-20-transmit-authority.md`, INV-13).

## Helper-level inventory

| Concept | Definition site | Consumer/path | Divergence | Canonical candidate |
| --- | --- | --- | --- | --- |
| strict PTT normalization | `core/tx_observation.py: normalize_observed_ptt` | `RigctldClientObservationAdapter.observed_ptt_observation` | none: booleans map to ON/OFF; all other values map to UNKNOWN | existing normalizer |
| provider metadata and TTL | `core/observation_adapter.py: ProviderObservationAdapter.observation` | rigctld adapter's `_adapter` and `observed_ptt_observation` | none: provider, transport, capability id, timestamp, and profile TTL come from one adapter | existing adapter |
| current-generation publication | `backends/rigctld_client/radio.py: RigctldClientObservationPoller._poll_medium` | callback receives stamped observation only while captured generation is current | canonical diagnostic publish precedes later VFO awaits | existing poller/store generation gate |
| polling declaration | `backends/rigctld_client/observations.py: build_external_rigctld_acquisition_profile` | acquisition policy capability lookup | this pin adds only `OBSERVED_PTT_PATH` with `polling=True` | declared canonical field |

The source has one medium-loop PTT read: `_poll_medium` reads PTT, immediately
publishes canonical `observed_ptt` when its captured generation remains current,
then proceeds to active-VFO and slow-control work. On a PTT read error it
publishes UNKNOWN only for a current generation and re-raises; cancellation
propagates through `_run_loop`. `project_observed_ptt` rejects old-generation,
stale/expired, malformed, and absent evidence as UNKNOWN. The retained
`legacy_ptt_bool` is a documented lossy migration projection, not a second
authority.

## Deletions

None. This bounded helper audit did not enumerate every definition in these
modules. Public/out-of-repository and dynamic consumers were not disproved, so
it makes no dead-code or vestigial-fork verdict.

## Consolidations

### F1 — observed rigctld PTT reuses the canonical diagnostic helper path

**Verdict:** already-shared
**Rank:** parallel
**Elements:** `core/tx_observation.py: normalize_observed_ptt`;
`core/observation_adapter.py: ProviderObservationAdapter`;
`backends/rigctld_client/observations.py:
RigctldClientObservationAdapter.observed_ptt_observation`; and
`backends/rigctld_client/radio.py: RigctldClientObservationPoller._poll_medium`.
**Consumers:** the rigctld poller creates the canonical observation; the
StateStore projection consumes current, fresh evidence. The legacy boolean
projection remains for migration consumers only.
**Divergence:** none established. The product change is the missing
`FieldCapability(OBSERVED_PTT_PATH, polling=True)`, not a new normalizer,
adapter, store, poll loop, or TX owner.
**Prior ruling:** the accepted ADR assigns observation and authority separate
duties; the older ADR forbids treating this rigctld read as verified readback.
**In-flight:** the capability declaration is present at the pinned tree.
**Required surface:** exists.
**Depends on:** none.
**Confidence:** high for this bounded source path.
**Falsifier:** another production `observed_ptt` normalizer, metadata builder,
or PTT poll-and-publish loop with materially different semantics.
**Fix class:** none.
**Actionable:** no. Retain the shared path and the polling-only capability.

## Limits and verification status

The observed field is diagnostic only. `RigctldClientRadio.read_transmit_state`
sets `verified_readback=False`, so neither this observation nor a successful
rigctld reply proves physical RF state or permits a TX-safety admission.
Generation rejection, UNKNOWN-on-error, TTL expiry, and cancellation behavior
are source observations, not hardware proof.

The PR records an independent code-only PASS. At the pinned product head, the
recorded full matrix passed all three Python versions (14,869 tests, 218 skipped,
15 expected failures); recorded quick evidence at merge reported 14,878, 220,
and 15 respectively. A later mutation workflow succeeded, but raw independent
review and repeat evidence were still pending when this archive was written;
this report makes no proven-mutation claim.

## Weakest link

The immediate canonical publication ordering is a source-flow conclusion. Check
the callback/StateStore integration under a generation change during or after
the PTT read before treating it as end-to-end delivery proof.

## Cleared

The shared strict normalizer, existing provider metadata/TTL adapter, one
medium-loop PTT read, current-generation gate, error-to-UNKNOWN behavior,
cancellation propagation, and polling-only capability declaration are correctly
bounded. No second TX authority or duplicate helper-level mechanism was found.
