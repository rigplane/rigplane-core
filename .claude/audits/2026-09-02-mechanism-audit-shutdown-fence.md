# Mechanism audit: shutdown fence and provider arrival

Audited revision: `cebbae4dc34cad3c1ec5c3581583e255149f6d0f`.
Current revision `3287fcd9f044c88522ac213d4d4e636b74bf7bc9` is identical in the
scoped production code. Proof-test head:
`73b5f7bcc17014dda21f15f51d723deacd42d0c5`; Mini proof:
`3a093dc1893b2a977fcc7a50b3ba3e3050f77439`.

Method: `.claude/skills/mechanism-audit/SKILL.md`. This archive records the
read-only adjudication and Mini characterization; it adds no new measurement.

**Owner decision pending; no production change authorized by this report.**

## Definitions, prior ruling, and consumers

| Definition site | Responsibility | Consumers at the audited revision |
|---|---|---|
| `src/rigplane/runtime/managed_tx_authority.py:ManagedTxAuthority` | One managed intent/debt owner, retry/TOT scheduler, and shutdown lifecycle | Compositions in `tests/test_managed_tx_authority.py`; production assembly absent until MOR-2168 |
| `src/rigplane/runtime/managed_tx_fence.py:TxAbortFence` | One epoch and cancellation registry | `ManagedTxAuthority._execute`; authority and fence tests |
| `src/rigplane/runtime/managed_tx_effect_lane.py:ManagedTxEffectLane` | Claim/dedup, attempt execution, and normalized actuator results | `ManagedTxAuthority._execute`; authority and effect-lane tests |

Importing the fence and lane does not make the authority their definition
site. Their test-only assembly is a sanctioned incomplete migration, not a
reason to delete the replacement before MOR-2168 installs it.

Prior ruling: the accepted 2026-09-01 ADR,
`docs/plans/2026-09-01-runtime-transmit-authority.md`, "ForceOff and the one
abort fence", requires **"increment the fence before provider I/O"**. It
does not explicitly require every cancellation callback to finish before a
replacement provider's release attempt begins. MOR-2230 is the in-flight
shutdown-ordering investigation; its proof uses the existing real fence and
effect lane, not a new production mechanism.

## Liveness census

The read/write census enumerated instance attributes and callable definitions
before adjudication. Counts include module helpers/protocol definitions;
class-qualified attributes distinguish same-named fields on different classes.

| Module | Class-qualified instance attributes | Distinct attribute names | Callable definitions |
|---|---:|---:|---:|
| `managed_tx_authority.py` | 23 | 22 | 39 |
| `managed_tx_fence.py` | 3 | 3 | 7 |
| `managed_tx_effect_lane.py` | 10 | 10 | 18 |

No deletion is warranted by this census. In particular, absence of production
assembly is recorded as the MOR-2168 migration boundary, not silently converted
into dead-code permission. No public/out-of-repository or dynamic-access
deletion claim is made.

## Steelman before verdicts

The strongest case for the current arrangement is the real lane's task hops:
`ManagedTxEffectLane.settle` claims an attempt, `_claim` schedules `_drive`,
and `_invoke` schedules `ManagedTxActuator.actuate`. Entry into `lane.settle`
is therefore not entry into the actuator. Meanwhile,
`TxAbortFence.force_off` increments `_epoch` as its first statement, before
awaiting any cancellation callback. The measured schedule preserves that epoch
ordering even when provider arrival reaches the lane first.

The cancellation callback can re-enter `ManagedTxAuthority.snapshot` without
deadlocking: the current authority does not hold its state lock across the
external fence/callback await. A private completion phase would be legitimately
local to the authority if an owner required a callback-completion barrier, but
that stronger contract is not established by the existing ADR.

## Deletions

None. No dead or vestigial implementation was established in this scope.

## Consolidations and boundary findings

### F1 — Shutdown/provider-arrival seam: the prior gap premise is not established

- **Verdict:** undetermined as a universal scheduling claim; the measured seam
  is cleared against the existing epoch-before-provider-I/O contract.
- **Rank:** diverged candidate (ordering), not a confirmed duplicate.
- **Elements / definition sites:** `ManagedTxAuthority.shutdown`,
  `._complete_shutdown`, `.provider_available`, and `._execute` in
  `managed_tx_authority.py`; `ManagedTxEffectLane._claim`/`._invoke` in
  `managed_tx_effect_lane.py`; `TxAbortFence.force_off` in `managed_tx_fence.py`.
- **Consumers:** authority shutdown/provider-arrival tests call the authority;
  authority calls the shared fence/lane; the lane calls the fake actuator in
  the Mini characterization. Production assembly remains MOR-2168.
- **Divergence:** lane entry can precede fence entry, but measured actuator
  entry occurred at fence epoch 1, before callback completion only.
- **Prior ruling:** 2026-09-01 ADR: "increment the fence before provider I/O".
- **In-flight:** MOR-2230, using the real lane and fence in the proof test.
- **Required surface:** a truthful pin of the accepted epoch-before-actuator
  contract; a callback-completion barrier requires a separate owner ruling.
- **Depends on:** owner decision on the contract; MOR-2168 for production
  assembly, not for interpreting this test's result.
- **Confidence:** high for the observed schedule; medium for unmeasured variants.
- **Falsifier:** actuator entry at epoch 0 under a supported scheduling mode,
  or a prior accepted requirement for callback completion before actuator entry.
- **Fix class / actionable:** none in production; no production defect is
  demonstrated by the stronger completion-order assertion.

### F2 — A private completion phase: legitimately local only if required

- **Verdict:** C, conditional; no new phase is justified by the current ADR.
- **Rank:** displaced candidate (ownership), premise not established.
- **Elements / definition sites:** lifecycle and drain state in
  `managed_tx_authority.py:ManagedTxAuthority.shutdown`/`._complete_shutdown`;
  epoch/callback behavior in `managed_tx_fence.py:TxAbortFence.force_off`.
- **Consumers:** authority shutdown and provider-change paths; no consumer of
  a new completion-barrier contract has been established.
- **Divergence:** epoch advancement and callback completion are distinct
  events; combining them would strengthen the current contract.
- **Prior ruling:** the 2026-09-01 ADR names epoch advancement before I/O, not
  completion of every cancellation callback.
- **In-flight:** MOR-2230; old quarantined WIP is not an accepted implementation.
- **Required surface:** owner-defined completion semantics, if desired.
  Existing lock, shutdown-task, termination, and drain fields already have
  distinct responsibilities and cannot be repurposed as that phase.
- **Depends on:** explicit owner decision; F1 does not supply that authority.
- **Confidence:** high on ownership; no implementation recommendation yet.
- **Falsifier:** an existing shared primitive with the required completion
  semantics, or an accepted contract requiring that barrier now.
- **Fix class / actionable:** none; conditional design only after a ruling.

### F3 — Abort registry: already shared and correctly located

- **Verdict:** already-shared.
- **Rank:** parallel candidate, rejected by definition-site/consumer evidence.
- **Elements / definition site:** `managed_tx_fence.py:TxAbortFence` owns
  `_epoch`, `_next_number`, and `_cancellations`.
- **Consumers:** `ManagedTxAuthority._execute`, fence tests, and the real-fence
  shutdown characterization; no second registry is required.
- **Divergence:** none; lane claims deduplicate attempts, not domain intent or
  cancellation ownership.
- **Prior ruling:** 2026-09-01 ADR requires exactly one runtime-owned fence.
- **In-flight:** MOR-2168 installs the existing shared mechanism in production.
- **Required surface:** exists.
- **Depends on:** none for this verdict; MOR-2168 for rollout.
- **Confidence:** high.
- **Falsifier:** a second live epoch/cancellation registry controlling the same
  managed work, rather than the lane's distinct claim bookkeeping.
- **Fix class / actionable:** none; no consolidation warranted.

## Mini characterization

[Run 33668173553](https://github.com/rigplane/rigplane-core/actions/runs/33668173553),
[job 100374938490](https://github.com/rigplane/rigplane-core/actions/runs/33668173553/job/100374938490),
runner `mm-build-core-2`,
[artifact 9861499114](https://github.com/rigplane/rigplane-core/actions/runs/33668173553/artifacts/9861499114).

Both parametrized cases of
`tests/test_managed_tx_authority.py:test_shutdown_real_lane_waits_for_fence_completion_on_arrival`
were RED only at the stronger final completion-order assertion. Reentrant
snapshot, retained release debt, RX/no-ON intent, one release attempt, and final
drain assertions passed. The relevant observed sequence was:

```text
lane.settle
  -> fence entry / epoch 1
  -> cancellation callback snapshot finished
  -> fake actuator entry (epoch 1)
  -> cancellation callback complete
  -> fence complete
```

This distinguishes lane admission, actuator entry, epoch advancement, and
callback completion. It is software characterization with a fake actuator,
not physical RF or hardware-stop proof.

## Weakest link

F1's scheduling inference is the weakest link: scheduler/eager-task variants
were not measured. First pin and test the exact accepted epoch contract under
supported scheduling modes; do not infer RF behavior or a callback-completion
requirement from this result.

## Cleared

- One authority and release-debt owner: `ManagedTxAuthority`.
- One correctly located abort registry: `TxAbortFence`.
- Epoch advancement before cancellation callbacks: `TxAbortFence.force_off`.
- No state-lock-held external await in the current shutdown/fence path.
- Lane claim/dedup is attempt execution, not a second domain-policy owner.

## Coordinator recommendation, pending owner decision

Make zero production changes. Replace the overstrict proof assertion with a
truthful epoch-before-actuator pin once the owner confirms that contract. Do
not copy the old quarantined WIP: it gates only provider arrival and introduces
unproven state without establishing a complete barrier contract.

**Owner decision pending; no production change authorized by this report.**
