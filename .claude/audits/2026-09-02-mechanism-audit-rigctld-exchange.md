> **Point-in-time audit snapshot (2026-09-02).** Produced with the
> `.claude/skills/mechanism-audit/SKILL.md` method. Tree audited:
> `21814f3c0a51f01abd16ed8e051ad666eae1b21d`. This is an archived report,
> not maintained documentation; citations resolve against that tree.

# Mechanism audit — rigctld exchange boundary
**Scope:** `backends/rigctld_client/transport.py: RigctldTransport`, its
canonical radio consumers, and managed-effect contracts. **Method:**
`.claude/skills/mechanism-audit/SKILL.md`. Source-read only; no tests ran.

## Enumeration and guards

`transport.py` defines two classes, 13 methods, two functions, two module
constants, and 11 assigned instance attributes (nine transport attributes plus
`RigctldCommandError.command` and `.code`). Literal searches established a
consumer for every enumerated element; there are no deletion candidates.

The transport attribute write/read counts within its definition are:

| Attribute | Writes / reads |
| --- | --- |
| `host`, `port`, `timeout` | 1 / 10, 1 / 10, 1 / 6 |
| `_reader`, `_writer` | 3 / 6, 3 / 4 |
| `_lock`, `_lifecycle_lock` | 1 / 2, 1 / 2 |
| `_provider_generation_advance`, `_connection_retired` | 2 / 1, 3 / 1 |

`RigctldClientRadio` canonically reaches `query`, `command`, `connect`, and
`close`; its getters/setters include `set_ptt`. `hamlib_probe._query_read`
also consumes the canonical transport surface. The radio observation poller
uses provider retirement to discard readback correlations. Dynamic access was
not established by literal search; out-of-repository consumers were not
checked. `RigctldTransport` is public/importable, so this report makes no dead
code claim.

## Deletions
None.

## Consolidations

### F1 — cancelled rigctld exchange can retain an uncorrelated response

**Verdict:** B — gap
**Rank:** diverged
**Elements:** `transport.py: RigctldTransport.command`, `.query`, `._write_line`,
`._read_line`, `._drain_stale`, `.close`, and `._close_locked`.
**Consumers:** `RigctldClientRadio` owns canonical radio command/read/lifecycle
calls; `RigctldClientObservationPoller` owns readback correlation, not socket
transaction identity. `managed_tx_state: EffectToken`,
`managed_tx_fence: TxAbortFence`, and `managed_tx_effect_lane:
ManagedTxEffectLane` are runtime foundations, not transport consumers.
**Definition site:** `backends/rigctld_client/transport.py: RigctldTransport`.
**Steelman:** `_lock` serializes completed exchanges; a cancellation while
waiting for that lock performs no I/O. `_drain_stale` removes delivered bytes,
and timeout, EOF, and `OSError` already use canonical retirement.
**Divergence:** `_lock` serializes completed drain/write/read exchanges, but
cancelled work after `_write_line` has no `CancelledError` cleanup: it releases
`_lock` while retaining the stream. A late old response can then be read as the
next transaction's response. This is source-flow inference, not an executed test.
`_drain_stale` waits 1 ms for delivered bytes; it cannot exclude bytes that
arrive later and carries no exchange identity.
**Prior ruling:** `docs/plans/2026-09-01-runtime-transmit-authority.md`,
“Boundaries and composition root,” assigns generation, epoch, and attempt IDs
to the managed runtime and says an adapter executes supplied tokens without
creating or comparing a competing epoch.
**In-flight:** `ManagedTxEffectLane` can displace an old provider task and
isolate it, but is not production-constructed as a rigctld transport fix at
this pin.
**Required surface:** captured-exchange connection quarantine before ownership
release. It must preserve the original cancellation, protect a replacement from
stale cleanup, close the captured writer before its callback with ordinary
callback-error handling, and retain the existing closing-writer handle for the
normal close/reconnect wait barrier. This is not a new registry.
**Depends on:** none.
**Confidence:** high for source flow; medium for the delayed-response schedule
until fake TCP coverage runs.
**Falsifier:** deterministic fake TCP evidence that cancellation cannot let
delayed old reply data satisfy a distinct command or query, including current
stale-identity and delayed-`wait_closed` behavior.
**Fix class:** design.
**Actionable:** yes, as a cancellation-response prerequisite only. It does not
prove adapter integration, urgent priority, final OFF ordering, or RF OFF.

## Required tests before a fix claim

Use a delayed fake server for command and query old replies versus a distinct
OFF/refusal; a before-lock-cancellation control; drain/read/resync cancellation;
stale cleanup versus replacement; delayed `wait_closed` with repeated
cancellation; callback-once; and normal `RPRT`, timeout, and EOF controls.
These are required tests, not evidence that they have run.

## Weakest link
Graceful-close, cancellation, and reconnect interleaving is unmeasured. In
particular, `close` waits on `_lifecycle_lock`, then acts on the current writer,
while `_close_locked` detaches and invokes retirement before `wait_closed`.

## Cleared

Canonical transport ownership, serialization of completed exchanges,
normalized `RPRT` outcomes, and retirement callback ownership are correctly
located. No extra product authority exists in this transport.
