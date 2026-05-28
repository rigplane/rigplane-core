# Raw CI-V Transactions

Raw CI-V transactions are the response-capable counterpart to the
fire-and-forget `send_civ` command path. They are intentionally scoped to one
frame and one explicit expectation.

## Ownership

`CoreRadio.send_civ_transaction()` claims the existing external CAT-session
guard before sending the frame. While the guard is active, cooperating pollers
pause through `external_cat_session_active`, preventing background CI-V reads
or writes from consuming the caller's response. The guard is released in a
`finally` block on success, timeout, cancellation, and errors.

`begin_external_cat_session()` remains idempotent when the external CAT owner
already holds the guard, and `end_external_cat_session()` remains idempotent.
A transaction rejects overlapping ownership instead of interrupting an existing
external CAT session; external CAT begin likewise fails cleanly while a raw
transaction owns the guard.

## Response Matching

Transactions run on `CivRuntime`, not `RadioPoller`. They reuse the existing
CI-V RX pump and `CivRequestTracker`:

- `expect="none"` sends once and returns `status: "sent"`.
- `expect="ack"` drops orphan ACK/NAK backlog, then registers an ACK waiter
  and resolves only a fresh ACK or NAK from the active transaction.
- `expect="data"` registers a keyed response waiter and a NAK-only waiter, so
  a radio NAK returns `status: "nak"` without allowing unrelated ACK frames to
  satisfy the data expectation.

The runtime does not infer response behavior from `_civ_expects_response()` for
transactions. Callers must choose the expectation mode so vendor-specific
commands remain explicit.

## HTTP Surface

`POST /api/v1/civ/transaction` and explicit
`type: "raw_civ_transaction"` steps in `/api/v1/commands/batch` are the HTTP
surfaces that wait for raw CI-V responses. `/api/v1/commands` and legacy
`send_civ` steps in `/api/v1/commands/batch` remain queued fire-and-forget
surfaces, and `wait_response=true` stays rejected there.

## Ordered Batch Transaction Contract

Issue #1633 defines the contract for response-capable raw CI-V transaction
steps in `POST /api/v1/commands/batch`; #1634 and #1635 implement and test the
server behavior under parent #1624. This is a Core API/protocol feature: it
defines generic ordered-batch behavior, backend-neutral radio ownership, and
deterministic per-step results. Core does not store named radio profiles,
managed setup data, hosted-account state, or customer-specific workflows.
Callers may still send caller-owned profile batches as ordinary request bodies.

The batch endpoint must keep the existing legacy command step shape unchanged:

```json
{ "name": "set_freq", "params": { "freq": 144030000 } }
```

Raw CI-V transactions use a new explicit step type, not `name`/`params`:

```json
{
  "type": "raw_civ_transaction",
  "id": "display-type-query",
  "command": 26,
  "sub": 5,
  "data": "0153",
  "expect": "data",
  "timeout_ms": 1000
}
```

Required transaction step fields:

- `type`: exactly `"raw_civ_transaction"`.
- `command`: integer byte from `0` through `255`.
- `expect`: one of `"none"`, `"ack"`, or `"data"`.

Optional transaction step fields:

- `id`: caller-owned JSON value echoed in that step result when present.
- `sub`: integer byte from `0` through `255`.
- `data`: compact even-length hexadecimal string. Input is case-insensitive;
  output hex is uppercase.
- `timeout_ms`: positive finite number of milliseconds for this transaction
  step. If omitted, the transaction step uses the batch step timeout default:
  `10000` milliseconds.

`continue_on_error` remains a batch-level option only. A
`raw_civ_transaction` step-level `continue_on_error` field is invalid, because
mixing batch-level and step-level continuation rules would make ordering hard
to reason about. Legacy command steps keep their existing tolerance for extra
fields; the new transaction step contract should be strict.

## Batch Ordering

The batch executor processes `steps` in array order. It must not enqueue or
start step `N + 1` until step `N` has either succeeded, failed with
`continue_on_error: true`, or caused later steps to be skipped.

Legacy command steps continue to use the ordered command queue. The HTTP
handler waits for the poller/backend future for that queued step before
advancing. Transaction steps bypass `RadioPoller`, acquire the scoped CI-V
transaction owner, send exactly one frame, wait according to `expect`, and
release ownership before the next batch step is considered.

When legacy command steps and transaction steps are mixed, ordering is
therefore:

1. queued command step is enqueued on the exact-order lane;
2. handler waits for that queued command's completion or timeout;
3. transaction step acquires CI-V ownership and completes or fails;
4. later queued command steps are enqueued only after the transaction releases
   ownership.

The ordering guarantee is for steps inside this batch. Existing cross-request
queue behavior is otherwise unchanged. If another external CAT session or raw
transaction already owns the backend when the transaction step tries to start,
the step returns `civ_owner_conflict`.

## Per-Step Timeout

Queued command steps keep the existing 10 second command batch step timeout.
Transaction steps use `timeout_ms` when present, otherwise `10000`
milliseconds. Units are always milliseconds in JSON and seconds only inside
Python internals. Timeout covers ownership acquisition plus the send/wait path
for the transaction step; it does not include earlier or later batch steps.

On timeout, the transaction primitive must unregister pending CI-V waiters,
release ownership, and return a per-step timeout result. It must not leave the
poller paused or the request tracker polluted for later steps.

## Transaction Step Results

The batch response remains HTTP `200` after the request body is accepted. The
top-level `ok` is `true` only when every reported step has `ok: true`.
Transport/auth/root JSON failures keep the existing endpoint-level HTTP error
behavior.

Successful transaction result shapes:

```json
{
  "index": 0,
  "type": "raw_civ_transaction",
  "id": "display-type-query",
  "ok": true,
  "status": "response",
  "result": {
    "frame": "FEFEE0981A050153FD",
    "command": 26,
    "sub": 5,
    "data": "0153"
  }
}
```

`expect: "none"` returns `status: "sent"` and does not wait for a radio frame:

```json
{
  "index": 0,
  "type": "raw_civ_transaction",
  "ok": true,
  "status": "sent",
  "result": {
    "frame": null,
    "command": null,
    "sub": null,
    "data": null
  }
}
```

ACK and NAK results use the same `result` object shape as the single
transaction endpoint:

```json
{
  "index": 0,
  "type": "raw_civ_transaction",
  "ok": true,
  "status": "ack",
  "result": {
    "frame": "FEFEE0A2FBFD",
    "command": 251,
    "sub": null,
    "data": ""
  }
}
```

```json
{
  "index": 0,
  "type": "raw_civ_transaction",
  "ok": false,
  "status": "nak",
  "error": "radio_nak",
  "message": "radio returned CI-V NAK",
  "result": {
    "frame": "FEFEE0A2FAFD",
    "command": 250,
    "sub": null,
    "data": ""
  }
}
```

Failure results:

| Case | `status` | `error` | Notes |
|------|----------|---------|-------|
| timeout | `timed_out` | `transaction_timeout` | Timeout before expected ACK/data response or before ownership could complete |
| owner conflict | `owner_conflict` | `civ_owner_conflict` | Another transaction or external CAT owner already owns the CI-V stream |
| unsupported backend | `unsupported` | `unsupported_command` | Active backend does not implement `CivTransactionCapable` |
| read-only server | `read_only` | `read_only` | Raw CI-V transaction steps are disabled in read-only mode |
| no radio during execution | `no_radio` | `no_radio` | No active radio is available for this step |
| validation failure | `failed_validation` | `invalid_request` or `invalid_step` | Malformed transaction shape, byte range, hex data, expectation, or timeout |
| runtime failure | `failed_execution` | `transaction_failed` | Non-owner-conflict `RuntimeError` raised by `send_civ_transaction()` |
| skipped after failure | `skipped` | `skipped_after_failure` | Step was not validated or executed because an earlier step stopped the batch |

Unsupported typed batch steps are handled separately from malformed
`raw_civ_transaction` steps. If a step has `type` present, the type is not
supported, and no legacy `name` is present, that step returns
`status: "failed_validation"` with `error: "unknown_step_type"`.

Examples:

```json
{
  "index": 0,
  "type": "raw_civ_transaction",
  "ok": false,
  "status": "timed_out",
  "error": "transaction_timeout",
  "message": "raw CI-V transaction timed out"
}
```

```json
{
  "index": 0,
  "type": "raw_civ_transaction",
  "ok": false,
  "status": "owner_conflict",
  "error": "civ_owner_conflict",
  "message": "CI-V stream is already owned by another transaction"
}
```

```json
{
  "index": 0,
  "type": "raw_civ_transaction",
  "ok": false,
  "status": "failed_validation",
  "error": "invalid_request",
  "message": "timeout_ms must be a positive finite number"
}
```

Skipped legacy command steps keep the existing skipped result shape with
`name`. Skipped transaction steps use `type` and echo `id` when those fields
are readable without full validation:

```json
{
  "index": 2,
  "type": "raw_civ_transaction",
  "id": "later-query",
  "ok": false,
  "status": "skipped",
  "error": "skipped_after_failure",
  "message": "skipped after earlier batch failure"
}
```

## `continue_on_error`

`continue_on_error` defaults to `false` and remains a JSON boolean at the
batch root. It applies uniformly to legacy queued command failures and raw
CI-V transaction failures.

When `continue_on_error` is `false`, these failures stop the batch and mark
all later steps as `skipped`:

- transaction `nak`;
- transaction `timed_out`;
- transaction `owner_conflict`;
- transaction `unsupported`;
- transaction `read_only`;
- transaction `no_radio`;
- transaction `failed_validation`;
- transaction `failed_execution`;
- queued command `failed_validation`;
- queued command `timed_out`;
- queued command `failed_execution`.

When `continue_on_error` is `true`, the failing step is reported with
`ok: false` and the executor proceeds to the next step after cleanup. For
transaction failures this means ownership has been released and pending CI-V
waiters have been unregistered before the next step starts. For queued command
timeouts this keeps the current behavior: the unconsumed timed-out queued step
is cancelled before the next batch step is prepared.

Batch root validation failures are not per-step failures and do not honor
`continue_on_error`. Examples include malformed JSON, missing or non-list
`steps`, empty `steps`, too many steps, and a non-boolean batch-level
`continue_on_error`.

## Compatibility

Existing fire-and-forget batch behavior is unchanged:

- legacy `{ "name": ..., "params": ... }` steps retain their request and
  result shape;
- `send_civ` in `/api/v1/commands` and `/api/v1/commands/batch` remains
  queued and fire-and-forget;
- `send_civ` still rejects `wait_response=true`;
- response-capable waiting is used only when the new
  `"type": "raw_civ_transaction"` step is explicitly requested;
- the single `POST /api/v1/civ/transaction` endpoint keeps its current
  contract.

## Implementation Test Matrix

Implementation issues for #1633/#1624 should cover:

| Case | Expected coverage |
|------|-------------------|
| legacy-only batch regression | Existing `{ "name": ..., "params": ... }` batches keep their request shape, result shape, ordering, and success behavior |
| mixed `command -> transaction -> command` batch | The first command completes before the transaction starts, and the later command is enqueued only after transaction ownership is released |
| transaction timeout | A transaction step returns `status: "timed_out"` and `error: "transaction_timeout"`, unregisters waiters, releases ownership, and skips later steps when `continue_on_error` is `false` |
| transaction NAK | A fresh radio NAK returns `status: "nak"` and `error: "radio_nak"` with the NAK result frame, then applies the configured continuation rule |
| owner conflict | Existing external CAT or transaction ownership returns `status: "owner_conflict"` and `error: "civ_owner_conflict"` without interrupting the current owner |
| read-only server | A transaction step in read-only mode returns `status: "read_only"` and `error: "read_only"` before sending any CI-V frame |
| no radio during execution | A transaction step with no active radio returns `status: "no_radio"` and `error: "no_radio"` as a per-step failure |
| unsupported backend | A backend without `CivTransactionCapable` returns `status: "unsupported"` and `error: "unsupported_command"` as a per-step failure |
| `continue_on_error: false` | Transaction failures stop the batch and report later steps as `status: "skipped"` with `error: "skipped_after_failure"` |
| `continue_on_error: true` | Transaction failures are reported with `ok: false`, cleanup completes, and the executor proceeds to the next step in order |
| fire-and-forget `send_civ` regression | `send_civ` in `/api/v1/commands` and legacy batch steps remains queued and fire-and-forget, and `wait_response=true` remains rejected |
