# Runtime Transmit Authority ADR

**Date:** 2026-09-01
**Status:** Accepted architecture; implementation pending
**Replaces:** [Transmit Authority — Command-Ingress Filter ADR](2026-08-20-transmit-authority.md)
**Decision scope:** one managed server runtime for one RigPlane app instance

## Context and decision

RigPlane must keep three facts separate: what this instance intends, what a
provider accepted, and what the radio appears to be doing. Provider success is
not RF proof; observed RF may come from a front panel, VOX, raw command, or
another process. Observation therefore cannot own managed command safety.

One app instance has one configured radio and one stateful server-side
`TransmitAuthority`. Multiple radios require multiple app instances. There is
no public `TxTargetId`; provider generations and effect tokens are runtime
details. The authority covers only managed commands of this runtime, not the
physical radio or unmanaged callers.

This replaces rather than extends the 2026-08-20 backend hazard gate. The
accepted runtime-intent admission policy is stated in
[Relay-family admission policy](#relay-family-admission-policy). Raw/direct
paths are outside this authority; this ADR makes no new observed-admission
ruling for them.

## Scope and glossary

Managed ingress has exactly three semantic forms:

- `PTT_DOWN(owner)` / `PTT_UP(owner)` — momentary, owner-scoped PTT;
- `TRANSMIT_ON` — latched transmit intent;
- `FORCE_OFF` — the unconditional global managed-runtime release.

The Web UI has one latched TRANSMIT switch. Its visible ON action dispatches
`TRANSMIT_ON`; its visible OFF action dispatches `FORCE_OFF` under the hood.
There is no soft `TRANSMIT_OFF` command and no separate ForceOff UI button.
Command names describe semantics; the UI continues to present an ON/OFF switch.

- **Intent** — `RX`, `PTT(owner)`, or `TRANSMIT`; accepted local intent, not
  observed RF.
- **Release debt** — `release_required=true`; a release obligation created by a
  possible managed ON or by explicit `FORCE_OFF`, including ForceOff at idle.
- **ForceOff** — the always-accepted transition that aborts all local
  TX-producing work and drives highest-priority `force_receive` until
  `force_receive ACCEPTED`.
- **Observer** — the sole producer of canonical `ON | OFF | UNKNOWN` radio
  evidence in `StateStore`.
- **Software TOT** — the runtime-owned timer for managed PTT and TRANSMIT,
  distinct from a radio's native TOT and from release-debt durability.

## Boundaries and composition root

| Component | Owns | Must not own |
|---|---|---|
| Browser | gestures, audio/presentation, local countdown interpolation | authority, timers, retries, RF truth |
| Server app composition root | exactly one configured radio, managed runtime, and authority | a second managed radio/runtime in the same instance |
| `ManagedTxAuthority` | intent, debt, TOT, `TxAbortFence`, tokens, retry, API state, managed admission | manufacturer wire vocabulary |
| Provider adapter | execution of one tokened attempt, priority lane, normalized result | its own generation, epoch, attempt IDs, or product intent |
| Rig profile + canonical backend | documented manufacturer vocabulary, provenance, semantic-to-wire mapping | runtime retry/deadline lifecycle |
| State observer | canonical tri-state observation | authority transitions or admission |

The composition root constructs the authority once and reuses it across
provider replacement. An assembly test must reject or make impossible a second
managed runtime/radio in the same server instance.

In the accepted architecture, `runtime/managed_tx_authority.py:
ManagedTxAuthority` is the sole stateful owner. A
`runtime/managed_radio_runtime.py: ManagedRadioRuntime` composition surface may
delegate to it but must not retain authority state. `core/tx_safety.py:
TxSafetySupervisor` may be replaced or reduced to pure policy, but its
observation-driven clearing/gating must not survive beside the authority.
`core/tx_authority.py: TransmitAuthority` must not remain as a parallel backend
hazard authority.

## Authority and diagnostic state

The public domain state stays small:

```text
intent: RX | PTT(owner) | TRANSMIT
release_required: bool
tot_deadline_monotonic: float | null
last_error: string | null
```

Active intent also retains monotonic `tx_started_at` for TOT recalculation.
The debt carries an internal retry plan (`PTT_RELEASE` or `FORCE_RELEASE`);
this is effect bookkeeping, not a new public phase.

An additive diagnostic reports the last settled attempt without becoming a
domain phase: `lastActuation = {operation, result: ACCEPTED | REJECTED |
UNCERTAIN, attemptId}`.

For `FORCE_OFF`, `lastActuation` reports the latest settled `force_receive`
attempt/result. The additive `abortErrors` diagnostic is reset when ForceOff
begins, records subordinate `stop_cw`/`stop_tune` failures, and remains visible
until the next ForceOff; it never changes debt, `last_error`, or
`lastActuation`.

The managed runtime is the sole source of `provider_generation`,
`effect_epoch`, and `attempt_id`. An adapter executes the supplied token and
never creates or compares a competing epoch.

## Ingress transitions

All transitions serialize in the authority. `A` and `B` are distinct PTT
owners. `RX/clean` means `release_required=false`; no ON is accepted while debt
is outstanding. Idempotent ON never resets `tx_started_at` or TOT.

| State/event | Transition and command response |
|---|---|
| `RX/clean` + `PTT_DOWN(A)` | `PTT(A)`, debt before ON dispatch, start TOT; `ACCEPTED` |
| `PTT(A)` + `PTT_DOWN(A)` | no-op; `ACCEPTED` |
| active intent + a different/incompatible ON | unchanged; `REJECTED` |
| `RX/debt` + any ON | unchanged; `REJECTED` as release pending |
| `PTT(A)` + `PTT_UP(A)` | `RX`, keep debt, invalidate that PTT ON, start/retry `force_receive`; `ACCEPTED` |
| no matching `PTT(A)` + `PTT_UP(A)` | unchanged; `REJECTED` as stale/not owner |
| `RX/clean` + `TRANSMIT_ON` | `TRANSMIT`, debt before ON dispatch, start TOT; `ACCEPTED` |
| `TRANSMIT` + `TRANSMIT_ON` | no-op; `ACCEPTED` |
| any state + `FORCE_OFF` | set `RX` and explicit debt before effects; `ACCEPTED`, including clean RX/provider unavailable |
| `PTT(A)` + disconnect of A/session A | enter ForceOff; internal event |
| any state + unrelated ingress disconnect | unchanged; internal no-op |
| `TRANSMIT` + requester/browser disconnect | preserve latched TRANSMIT; internal no-op |
| either active intent + provider invalidation/replacement or managed-runtime shutdown | enter ForceOff; never replay ON |

`PTT_UP` is owner-scoped at admission, then uses the universal provider
`force_receive` effect for its initial attempt and retries. It cannot admit a
release for another owner or for TRANSMIT. UI switch OFF is `FORCE_OFF`, so it
unconditionally releases either managed intent and cancels all registered
local TX-producing work.

## Actuation results and exact field deltas

Ingress returns transition `ACCEPTED` or `REJECTED`; provider effects settle
asynchronously as exactly `ACCEPTED`, `REJECTED`, or `UNCERTAIN`. The original
command response is not rewritten by a later actuation result.

| Event/result | intent | debt | `tx_started_at` / deadline | `last_error` / `lastActuation` | command response |
|---|---|---|---|---|---|
| accepted ON transition, before I/O | requested TX intent | `true` | `now` / configured deadline | clear / unchanged until settlement | `ACCEPTED` |
| ON `ACCEPTED` | unchanged | `true` | unchanged | clear / ON `ACCEPTED` | already returned |
| definitive-before-effect ON `REJECTED` | `RX` | `true`; schedule `force_receive` | clear / clear | rejection / ON `REJECTED` | already returned |
| ON `UNCERTAIN` | `RX` via ForceOff | `true` | clear / clear | error / ON `UNCERTAIN` | already returned |
| matching PTT_UP transition | `RX` | `true` | clear / clear | unchanged / unchanged until settlement | `ACCEPTED` |
| accepted ForceOff, even idle | `RX` | `true` | clear / clear | unchanged / unchanged until settlement | `ACCEPTED` |
| `stop_cw`/`stop_tune` result during ForceOff | unchanged | unchanged | unchanged | append failure to `abortErrors`; do not overwrite primary diagnostics | none |
| `force_receive ACCEPTED` | `RX` | `false` | clear / clear | clear / `force_receive ACCEPTED` | already returned |
| `force_receive REJECTED` or `force_receive UNCERTAIN` | `RX` | `true` | clear / clear | error / `force_receive` + result | already returned; retry |
| stale-generation/epoch completion | no authoritative change | unchanged; a stale ON keeps/creates debt | unchanged | stale error / normalize as `UNCERTAIN` | none; retry current release plan |
| rejected or stale ingress | unchanged | unchanged | unchanged | unchanged | `REJECTED` |

`ACCEPTED` is only the backend's documented normal success boundary, never RF
proof. `REJECTED` is valid only when the requested effect definitively could
not be accepted; timeout, ambiguous reply, cancellation race, disconnect, and
stale completion are `UNCERTAIN`. Only a current-epoch
`force_receive ACCEPTED` clears release debt; ON rejection, stop-CW/tune
results, and observer state do not.

## ForceOff and the one abort fence

There is exactly one runtime-owned `TxAbortFence`/epoch registry. Every local
PTT, TRANSMIT, CW, tune, queued ON, and in-flight ON registers a runtime token
and cancellation handle and must honor the current epoch.

`FORCE_OFF` performs, in order:

1. set `intent=RX`, create/upgrade to `FORCE_RELEASE` debt, clear TOT, and
   increment the fence before provider I/O;
2. cancel all older registered local TX work and poison late ON completions;
3. request provider `stop_cw` and `stop_tune` semantics where supported;
4. submit highest-priority `force_receive`;
5. clear debt only on `force_receive ACCEPTED`; otherwise retry for the runtime
   lifetime.

`stop_cw`/`stop_tune` results are diagnostic only. Their failures are recorded
in `abortErrors`; they never clear debt or replace ForceOff's final
`lastActuation`, which reports the `force_receive` result. Thus
`force_receive ACCEPTED` remains the final release outcome while subordinate
abort failures remain visible.

### Urgent ForceOff ordering contract

This section is normative for the pending implementation. It refines the
ordering above; it is not a claim that the current runtime already enforces it.

ForceOff has two separate duties. First, synchronously before yielding, the
authority sets RX and release debt and advances the global fence. That fence
invalidates old local TX work and prevents further old-epoch writes. Second,
the authority submits the current urgent `force_receive` attempt. It must not
await unrelated asynchronous cancellation, isolation, transport retirement, or
provider cleanup before that urgent attempt. Callback bodies invoked on this
path must not block.

An epoch alone does not retract bytes already accepted by a transport or radio.
If an old ON can escape after the fence, the implementation must submit a
subsequent current-epoch `force_receive`; accepting a new OFF and only then
awaiting old-work isolation does not establish final-OFF ordering. The release
debt must not clear on an OFF whose ordering does not account for the last
possible old ON write.

`PTT_UP(owner)` remains owner-local: it invalidates only its matching PTT work
and does not advance the global fence or cancel other owners. Managed local CW,
tune, future chunks, and queued writes must register with and honor the same
global fence. Provider stop semantics remain best effort for work already
buffered inside a radio.

Graceful shutdown still needs completion barriers for final resource retirement.
Those barriers are separate from urgent ForceOff submission and must not delay
the urgent OFF behind unrelated cleanup. This contract adds neither a watchdog,
an authority phase, nor model-specific authority branches; it leaves the
profile-to-backend producer chain unchanged.

If a provider lacks stop-CW/tune semantics, cancellation or `force_receive`
may not stop work already committed inside the radio; no stronger guarantee is
made. A late ON that may have escaped causes another release attempt. Work
after the new fence requires a new explicit ingress; nothing auto-replays on
provider replacement.

PTT_UP invalidates only its matching ON attempt and retains `PTT_RELEASE`; it
does not perform the global registry cancellation. Explicit ForceOff always
upgrades that plan to `FORCE_RELEASE`.

Retry is non-spinning and bounded per attempt, with no retry-count ceiling
while the runtime lives. Provider replacement reuses the authority and retries
debt against the replacement. Graceful shutdown begins ForceOff and keeps the
authority alive through its shutdown drain; process/host death ends the
guarantee. This is runtime-lifetime durability, not disk persistence, and it
does not justify a browser/backend/fifth watchdog.

## Software TOT and API

TOT configuration lives in persisted app configuration, never a rig profile.
On migration/boot, an existing configured value wins; an absent value defaults
to `180` seconds. `null` disables TOT; API input `0` is normalized to `null`.
Positive finite values enable it; invalid values are rejected. An API edit is
persisted before becoming active, survives restart, and leaves the old value
unchanged if persistence fails.

The authority loads configuration before accepting ingress. It uses monotonic
time only. While PTT or TRANSMIT is active, edits recalculate from the original
`tx_started_at`; a value at/below elapsed time enters ForceOff immediately.
Disabling clears the deadline without releasing intent. A restart restores
configuration only, never intent, deadline, release debt, or a previous
runtime's retry obligation.

Illustrative state (endpoint naming remains to be finalized):

```json
{
  "intent": {"kind": "ptt", "owner": "web-session"},
  "releaseRequired": true,
  "lastError": null,
  "lastActuation": {"operation": "ptt-on", "result": "ACCEPTED", "attemptId": "..."},
  "abortErrors": [],
  "observedPtt": "unknown",
  "tot": {"configuredSeconds": 180, "active": true, "remainingMs": 42500,
          "expiresAt": "2026-09-01T12:34:56.789Z"}
}
```

`expiresAt` is a wall-clock display estimate derived from monotonic remaining
time, never an expiry input. The browser interpolates the countdown locally.
Illustrative commands are `TRANSMIT_ON`, `FORCE_OFF`, and PTT ingress; the UI
maps its one switch's ON/OFF actions to the first two commands.

## Provider/profile contract

The semantic actuator supplies `ptt_on`, `transmit_on`, optional
`stop_cw`/`stop_tune`, and highest-priority `force_receive`, each as a tokened
attempt returning only the three normalized results. PTT_UP and ForceOff both
use `force_receive`; only ForceOff first advances the global abort fence and
requests stop semantics. Stop-CW/tune results are subordinate diagnostics;
only `force_receive ACCEPTED` settles release debt.

The single source chain is:

```text
official manufacturer documentation -> normalized profile truth/provenance
-> canonical backend implementation -> managed actuator -> all managed consumers
```

Manufacturer bytes/tokens and provenance belong in profiles; the canonical
backend maps those semantics to wire operations. The managed runtime
exclusively owns attempt deadlines, `provider_generation`, `effect_epoch`,
`attempt_id`, cancellation settlement, retries, and provider replacement. An
adapter executes the token supplied by the runtime and owns no competing
epoch. No consumer duplicates this lifecycle.

## Observation contract

`StateStore` is the sole canonical tri-state seat. An additive public field
(illustratively `global.tx_state.observed_ptt`) exposes `ON | OFF | UNKNOWN`.
Qualified push/event evidence updates it immediately; polling/readback fills
gaps; missing, stale, invalid, or old-generation evidence becomes `UNKNOWN`.

Existing boolean `global.tx_state.ptt` and legacy `RadioState.ptt` are
deprecated compatibility projections during migration: `true` only for
canonical `ON`, `false` for both `OFF` and `UNKNOWN`. Consumers needing the
difference must use the tri-state field.

Observation never changes intent, owner, debt, TOT, retry, or admission. The
accepted relay-family policy is intent-based and does not consult observation;
raw/direct paths remain out of scope. Front panel, VOX, direct
`Radio.set_ptt`, raw CI-V, CW, tune, and external processes may update
observation but create no authority ownership or debt.

Local server-runtime CW/tune likewise creates no authority intent, ownership,
or TOT, but it must register with and honor the instance's `TxAbortFence` and
participate in best-effort ForceOff cancellation. Only callers outside this app
instance lack that ForceOff relationship.

Migration removes observer-driven clearing/gating from
`core/tx_safety.py: TxSafetySupervisor`. Tests must prove every observation
transition, including ON/OFF/UNKNOWN and generation replacement, leaves every
authority field unchanged. RigPlane may serialize only its own incompatible
operations to prevent an ordering race; that is not observed-RF admission.

## Relay-family admission policy

This is an accepted design policy, not a statement that current command paths
implement it or that any provider accepts a requested operation.

- Band, frequency, mode, VFO, and split mutations are admitted regardless of
  observed RF state.
- `ManagedTxAuthority` refuses antenna switching and tuner ON, engage, or
  start only while it holds managed `PTT(owner)` or `TRANSMIT` intent.
  `observed_ptt` does not participate in that decision.
- Tuner OFF and `FORCE_OFF` are always admitted by this policy, regardless of
  managed intent or observed RF state.
- The policy creates no deferred queue. A refused request is not retained for
  later execution.

Provider capability, a documented provider rejection, and transport failure
remain separate from this runtime admission policy. Local server-runtime CW
and tune remain fence participants without acquiring authority intent,
ownership, or software TOT.

## Failure boundaries and compatibility

- ForceOff cannot guarantee RX against held physical PTT, VOX, another
  process, missing stop semantics, radio/provider failure, or host death.
- `observed_ptt=OFF` is evidence, not proof that no RF exists; `UNKNOWN` is an
  honest lack of qualified evidence.
- Native radio TOT is separate and is not configured by this software TOT.
- Local server-runtime CW/tune may remain unmanaged for authority intent,
  ownership, and TOT, but must register with and honor `TxAbortFence`; ForceOff
  requests best-effort stop semantics before `force_receive`.
- External callers outside this app instance, including CLI, sync SDK,
  standalone/direct APIs, raw writes, and their CW/tune paths, may remain
  explicitly unmanaged for beta, with warnings that authority, ForceOff, and
  software-TOT guarantees do not apply.

Migration must converge existing managed TX ownership into
`ManagedTxAuthority`, replace the old hazard authority rather than wrap it,
add the composition-root cardinality test, establish the profile-backed
actuator, then route every managed Web/runtime TX ingress through it. A
`ManagedRadioRuntime` composition surface may delegate but must not retain
intent, debt, TOT, fence, or admission ownership. Remove the old observed-RF
admission machinery rather than create another gate. Apply the accepted
relay-family policy through managed intent only; raw/direct paths remain out of
scope.

## Explicitly superseded decisions

Superseded from the 2026-08-20 ADR: backend-per-radio authority placement;
`PASS/HAZARD/KEYING/UNKEY`; hazard maps and solicited read-before-write;
`TransmitTruth` as admission input; CW/tune as authority intent or TOT source;
per-delivery deadline drivers; a public target/provider generation; and the
requirement that unmanaged beta callers share the Web authority. Its broad
observed-TX matrix is not inherited. The prior open split/tuner/antenna
question is superseded by the relay-family policy above.

The surviving principles are narrower: provider acceptance is not RF proof,
observation is separate truth, OFF debt precedes uncertain work, and stale
provider effects are fenced.

## Invariants and acceptance criteria

1. One app composition root constructs exactly one managed radio/runtime and
   one authority; provider replacement reuses them and never replays ON.
2. Every managed ON creates debt before dispatch; no ON is accepted over debt.
3. PTT_UP admits only its owner. UI TRANSMIT switch OFF dispatches separate
   FORCE_OFF semantics, not a soft TRANSMIT release.
4. FORCE_OFF is always accepted, creates debt even at idle/provider
   unavailable, increments the sole abort fence, and retries until
   `force_receive ACCEPTED` for the runtime lifetime.
5. Only a current-epoch `force_receive ACCEPTED` clears release debt;
   ON rejection, stop-CW/tune results, and observation never do.
6. Runtime-issued generation/epoch/attempt tokens make stale or late ON unable
   to restore intent; adapters own no competing epoch.
7. Owner disconnect, provider invalidation/replacement, and runtime shutdown
   follow the exact transitions above; latched TRANSMIT survives requester or
   browser disconnect.
8. Fake-clock tests cover TOT boot/default/persistence/live edits and prove
   browser suspension, wall-clock changes, and restart do not extend a timer.
9. Race tests cover PTT_UP and ForceOff versus queued/in-flight ON, CW, tune,
   stale completion, provider replacement, and every `force_receive` result.
10. State/API tests pin the field-delta table, `lastActuation`, `abortErrors`,
    and tri-state to deprecated-bool projection; assembly and profile
    conformance pin singular ownership and the normalized actuator surface.
11. Observed ON/OFF/UNKNOWN never blocks band/frequency, mode, VFO, or split.
    Only managed `PTT(owner)` or `TRANSMIT` intent may refuse antenna switching
    and tuner ON, engage, or start; tuner OFF and ForceOff are always admitted.
    No refused request is deferred. Raw/direct paths remain out of scope.
12. Local server-runtime CW/tune creates no authority intent/ownership/TOT but
    registers with and honors `TxAbortFence` and receives best-effort ForceOff
    cancellation. Only explicitly unmanaged callers outside this app instance
    make no authority, ForceOff, or software-TOT guarantee.
