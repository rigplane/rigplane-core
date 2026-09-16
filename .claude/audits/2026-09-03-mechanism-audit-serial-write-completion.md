# Mechanism audit: Serial write completion

> **Point-in-time audit snapshot.** Source
> [`f203d64937b0eb2b92c297d9ac155c60feb52851`](https://github.com/rigplane/rigplane-core/commit/f203d64937b0eb2b92c297d9ac155c60feb52851),
> tree `84c96d44ed50aa720032b8e2f7c4afa6677402f4`. This is a bounded
> helper-level audit, not a step-3a whole-module census. Citations and
> conclusions in the snapshot are frozen at that object.

**Method:** `.claude/skills/mechanism-audit/SKILL.md`.

**Scope:** `SerialCivLink` completion, currency, and retirement;
`SerialCivTransport`; and both changed test files. The four-file diff was read
in full. Evidence is exact-object Git reads, definition/consumer searches, and
independently adjudicated Actions runs `33774640300` and `33776217811`; no
local execution occurred. Searches paired write/drain/completion,
session/connection/identity, cancel/retire/join, queue/lane/claim,
release/debt/retry, and epoch/fence.

The intended product audit interval is [`109abf8693f2cddacf7b81ad6b8b62f872aef853`](https://github.com/rigplane/rigplane-core/commit/109abf8693f2cddacf7b81ad6b8b62f872aef853) → [`f203d64937b0eb2b92c297d9ac155c60feb52851`](https://github.com/rigplane/rigplane-core/commit/f203d64937b0eb2b92c297d9ac155c60feb52851), spanning four files with `+902/-49`. The proof manifest carrier [`82585fb4b7957096d9644db5d8b95c3ca1c79077`](https://github.com/rigplane/rigplane-core/commit/82585fb4b7957096d9644db5d8b95c3ca1c79077) → [`f203d64937b0eb2b92c297d9ac155c60feb52851`](https://github.com/rigplane/rigplane-core/commit/f203d64937b0eb2b92c297d9ac155c60feb52851) is separately three files with `+214/-19` (`233`), and is not the audit interval.

Related execution record: [PR #3091](https://github.com/rigplane/rigplane-core/pull/3091).

## Definitions, consumers, and steelman

`src/rigplane/backends/icom7610/drivers/serial_civ_link.py:
SerialCivLink.send_written` owns request completion;
`src/rigplane/backends/icom7610/drivers/serial_session.py:
SerialCivTransport.send_tracked` is its production caller. The adapter is
assembled by `SerialSessionDriver` through
`src/rigplane/backends/_icom_serial_base.py: _IcomSerialRadioBase`.
`src/rigplane/runtime/_civ_rx.py: CivRuntime._send_civ_frame_now` and
`CivRuntime._execute_civ_raw` consume tracked send.

`SerialCivLink.disconnect` and active-send cancellation consume
`SerialCivLink._retire_writer`, creating `_finish_retirement` and using
`_join_retirement`. The writer loop and `send_written` consume
`_write_session_is_current` and `_fail_queued_writes`.

The accepted 2026-09-01 Runtime Transmit Authority ADR assigns debt, retry,
TOT, and abort fence to `runtime/managed_tx_authority.py:
ManagedTxAuthority`, separating urgent OFF submission from resource retirement.
The 2026-09-02 ForceOff cleanup audit records that split.

**Steelman:** the writer, closest to the last possible local ON write,
legitimately owns exclusion and completion. Commander can order commands but
cannot establish this captured writer's drain/close completion. The writer
cannot retain debt across provider replacement, interpret managed owners, or
infer RF; those remain upstream.

## Adjudication

- **C — completion:** `SerialCivLink.send_written` and `_writer_loop` are
  canonical here; raw send is intentionally enqueue-only.
- **C — currency:** captured queue/writer identity and the supplied
  `is_current` predicate exclude stale local writes without minting an
  authority generation or abort epoch.
- **C — retirement:** `_retire_writer`, `_finish_retirement`, and
  `_join_retirement` retain actual cleanup, isolate cancelled waiters, and
  preserve current close-error precedence.
- **Already shared:** `ManagedTxAuthority`, `reduce_managed_tx`,
  `TxAbortFence`, and `ManagedTxEffectLane` own the intended debt, fence, and
  attempt mechanisms.

Production assembly was incomplete at the snapshot: the live path was
`ManagedRadioRuntime`, `TxSafetySupervisor`, and
`managed_tx_effect_service._Service`; no additional owner is warranted.
`CivRuntime` port retirement and the Icom lower-executor captured-resource
contract are complementary, not interchangeable joins. Existing definitions
are the canonical candidates; no consolidation is proposed. Acceptance depends
on separate runtime integration and final-OFF ordering work.

No deletions are proposed. Dynamic, public, and downstream deletion guards
were not established.

## Weakest link and cleared bounds

The weakest link is application-level currency: at this snapshot,
`CivRuntime._send_civ_frame_now` does not pass `is_current`, and the adapter
surface has not completed cutover. `TestPtt.test_managed_ptt_port_token_safety`
retains a stale-send mock; separate source review was BLOCKED pending its
fixture fix.

Captured resources, actual-task retirement, waiter-cancellation isolation,
current close-error precedence, and immutable tests were supported in finite
proof. Both runs reported 96 executions: 91 PASS and 5 expected CALL
Assertions, with 24 witnesses including candidate A/B/B versus mutant A/A/A.
Groups were absent/exact and restores both held. This establishes no hardware,
RF, ACK, OS-I/O calibration, forced-kill, or whole-application claim.

## Follow-up — 2026-09-03 (not a rewrite of the snapshot)

Source [`d355f55ac40af96e4b595e96fab66698f1364c42`](https://github.com/rigplane/rigplane-core/commit/d355f55ac40af96e4b595e96fab66698f1364c42),
tree `8d665de66163ac4bdbaa73e1c2e3ad5e527f688b`, changes
`tests/test_radio.py` only (`+4/-4`): it switches the mock and counts to
`send_written` and accepts the `is_current` keyword. All four prior proof
blobs are unchanged. Independent static review cleared the stale-hook cause.

Initial independent run `33780845770` was
`VALID_BOUNDED_INITIAL_PROOF` (artifact `9903495392`): G1/M/L/G2 = 3/1/3/3;
10 executions, 9 PASS, and 1 CALL `AssertionError` (`835`: `assert 0 == 1`);
written/raw = 1/0 versus 0/1; all 20 setup/teardown checks passed; hashes,
restores, and groups were verified. It makes no stronger hardware or
whole-application claim. Independent unchanged repeat
[run `33781569408`](https://github.com/rigplane/rigplane-core/actions/runs/33781569408)
was `VALID` (artifact `9903774107`) at the same `d355` source and diagnostic
`5c51`: 10 executions, 9 PASS, and the same exact CALL `AssertionError` at
`835`; witnesses, 144 seals, 1,988 inventories, dependency digest, absent
groups, and exact restores matched. The repeat is likewise bounded and makes
no hardware or whole-application claim. Normal integrated CI and final
exact-head review remain pending.
