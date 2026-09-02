# Mechanism audit: ForceOff cleanup ordering

Audited revision: `7387c84ebfecaf1404f0e45357c2a575c07adf7b`.

Method: `.claude/skills/mechanism-audit/SKILL.md`. This is a point-in-time,
read-only code audit. It records a contract for pending work; it does not claim
that a universal ForceOff ordering guarantee is installed or that RF is off.

## Scope, definitions, and prior ruling

The tract is the owner-to-provider path for urgent managed release. Definition
sites are `runtime/managed_tx_authority.py: ManagedTxAuthority`,
`runtime/managed_tx_fence.py: TxAbortFence`, and
`runtime/managed_tx_effect_lane.py: ManagedTxEffectLane`. The legacy runtime
path is `runtime/managed_tx_effect_service.py: _Service` with
`core/tx_safety.py: TxSafetySupervisor`.

The accepted ADR, `docs/plans/2026-09-01-runtime-transmit-authority.md:
Runtime Transmit Authority ADR`, assigns one global abort fence to ForceOff,
but says implementation is pending. Its producer/profile chain is unchanged.
The authority/fence/lane are assembled only by tests at this revision; that is
an incomplete migration, not evidence of dead code or an installed cutover.

## Liveness census and steelman

The audit census found 3 fence attributes and 6 public operations, 21
authority attributes and 35 methods, and 10 lane attributes and 15 methods.
Every internal attribute has an in-repository read. Public and out-of-repo
consumers remain unresolved, so no deletion is verified.

The strongest contrary case is that the fence already advances its epoch before
awaiting registered callbacks, and the lane already displaces old claims and
can isolate them. `CivRuntime` also poisons a physical managed-TX port before
its first retirement await and checks identity near send. Those are useful,
correctly located mechanisms to reuse; they do not prove universal ForceOff
ordering before the components are assembled by production runtime code.

## Deletions

None. The new mechanism has test consumers and an explicit pending migration;
the legacy service still has runtime consumers. Dynamic access and external
public consumers were not disproved, so deletion would be unsound.

## Consolidations and boundary findings

### F1 — ForceOff callback completion can delay the normal release attempt

Verdict: B — gap
Rank: displaced
Elements: `runtime/managed_tx_authority.py: ManagedTxAuthority._execute`,
`runtime/managed_tx_fence.py: TxAbortFence.force_off`
Consumers: authority effects await `_execute`; the fence is used by authority
and its tests
Definition site: the authority owns ForceOff sequencing; the fence owns token
invalidation and cancellation registration
Divergence: `_execute` fixes its deadline before awaiting `force_off`; the
fence then awaits registered callbacks serially. A blocking callback can spend
the normal OFF attempt budget before `ManagedTxEffectLane.settle` is reached.
Prior ruling: accepted 2026-09-01 ADR, pending implementation
In-flight: test-only authority/fence/lane assembly; legacy runtime still live
Required surface: a nonblocking fence advance/cancellation snapshot followed
by urgent `force_receive` only after effective exclusion of old writes, or by
retained debt plus a subsequent current `force_receive` after a possible old
write; cleanup completion is not a precondition to submission
Depends on: implementation none; safe acceptance depends on F2's write-ordering
proof
Confidence: high
Falsifier: production assembly that advances and snapshots synchronously, then
proves urgent OFF starts while a callback remains blocked
Fix class: design
Actionable: yes — owner contract only; no runtime change in this archive

### F2 — Epoch and isolation do not by themselves prove final-OFF ordering

Verdict: B — gap
Rank: diverged
Elements: `runtime/managed_tx_effect_lane.py: ManagedTxEffectLane._claim`,
`runtime/managed_tx_effect_lane.py: ManagedTxEffectLane._invoke`
Consumers: lane is consumed by authority tests; no production assembly found
Definition site: the lane owns effect claims, displacement, and isolation
Divergence: a countermodel is OFF, then late ON, then isolation completes.
An epoch rejects old work but cannot retract a byte already accepted. A current
OFF accepted before awaiting old isolation likewise does not prove it was final.
Prior ruling: accepted 2026-09-01 ADR, pending implementation
In-flight: lane claim/isolation machinery exists, but no future adapter has
been installed to establish this ordering
Required surface: if an old ON may escape, issue a subsequent current OFF;
isolate old work separately from the final-OFF obligation
Depends on: none; this characterization supplies F1's safety-acceptance proof
Confidence: medium
Falsifier: an installed adapter proves every possible late ON is rejected
before transport acceptance, or a later current OFF is ordered after it
Fix class: design
Actionable: yes — contract and future race proof required

### F3 — Mechanisms are presently separated by their proper duties

Verdict: C — legitimately local
Rank: name-collision
Elements: `runtime/managed_tx_fence.py: TxAbortFence`,
`runtime/managed_tx_state.py: EffectToken`,
`runtime/managed_tx_state.py: ManagedTxState`,
`runtime/managed_tx_state.py: reduce_managed_tx`,
`runtime/managed_tx_effect_lane.py: ManagedTxEffectLane`,
`runtime/managed_tx_effect_service.py: _Service`,
`commands/commander.py: IcomCommander`,
`runtime/_civ_rx.py: CivRuntime`
Consumers: fence/authority/lane have test consumers; `_Service` has legacy
runtime consumers; `CivRuntime` has production radio-runtime consumers
Definition site: fence registry; `EffectToken` attempt identity;
`ManagedTxState`/`reduce_managed_tx` state and debt; lane claims/isolation;
`_Service` legacy provider-attempt claims, cancellation, and retirement;
`IcomCommander` command queue; and physical transport-port identity
respectively
Divergence: their names overlap around cancellation, but their ownership does
not: no competing epoch or duplicate owner is established.
Prior ruling: accepted ADR calls for replacement, not wrapping, at cutover
In-flight: authority/fence/lane tests precede production cutover
Required surface: exists
Depends on: F1 and F2 for cutover acceptance
Confidence: high
Falsifier: production code that gives a second component authority to clear
debt or mint a competing fence epoch
Fix class: none
Actionable: no — retain the correctly located mechanisms until cutover

## Weakest link

F2 is weakest: a future adapter could provide a physical pre-send exclusion
not present in this revision. Check that adapter's late-ON/second-current-OFF
race proof first. No RF conclusion follows from this audit.

## Cleared

One owner/fence split, owner-local `PTT_UP`, stale-result isolation distinct
from observer state, and reentry through authority snapshot without holding an
authority lock across a callback await were examined and not found to be
parallel owners or dead code.
