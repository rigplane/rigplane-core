# Mechanism audit — managed command queue and ingress

**Point-in-time revision:** `3c997a9d7c9dad9b4eb813b567acc1f8216b3081`.
**Method:** `.claude/skills/mechanism-audit/SKILL.md`.
Accepted bounded findings, not implementation or runtime proof. Source paths
below are relative to `src/rigplane/` and resolve only at this pin.

## Scope, definitions, and evidence limits

Scope: whole-operation ordering across ingress while keeping OFF independent.
Receipt, admission, settlement, completion, and ordered response are distinct.

Observed definitions are already shared: `runtime/_poller_types.py: CommandQueue`
and `CommandQueueEntry`, `core/command_service.py: CommandService`, and
`runtime/managed_tx_authority.py: ManagedTxAuthority`.
This does not establish shared instances or authority use across every ingress.
`web/server.py: WebServer.__init__` constructs a queue and separate WebSocket and
HTTP service instances; `web/handlers/control.py: ControlHandler.__init__` reuses
the server's WebSocket service. `rigctld/handler.py: RigctldHandler.__init__`
constructs its own service. This calls for composition, not another implementation.

Evidence: literal `git grep` and `git show` at the pin. Dynamic consumers,
downstream packages, and a complete dead-code census were not enumerated.

## Prior rulings and steelman

The accepted [Runtime Transmit Authority ADR](../../docs/plans/2026-09-01-runtime-transmit-authority.md)
(2026-09-01) assigns one app authority, separates observed RF from admission, and
keeps raw/direct callers outside its managed scope. Its urgent-ordering contract
requires OFF submission without awaiting unrelated cleanup. Its acceptance
criteria do not establish that ingress migration has already happened.

The [command-path audit](2026-08-30-mechanism-audit-command-path.md) (2026-08-30)
identified drain/deferred-lane duplication, not universally movable backend policy.

Steelman: positional rigctld replies, identified WebSocket lifecycle events, and
backend readback/pacing/failure policies justify local leaves and formatting.
They do not justify duplicate intent ownership or interleaving a whole
read-modify-write operation merely to make its reader responsive.

At this pin `runtime/tx_interlock.py: _DEFER_TYPES` excludes `SetFreq`, `SetMode`,
`SetBand`, `SelectVfo`, `VfoSwap`, and `VfoEqualize`; `classify_tx_interlock`
returns `TX_SAFE` for them. `rigctld/handler.py: RigctldHandler._defer_write_gate`
returns before RF lookup for non-DEFER commands. Its historical frequency prose
is not evidence that a canonical frequency command is currently deferred.
History verified with `git show`: public PR #2766 (`9fc909436dcd`) removed
frequency/RIT-XIT from `_DEFER_TYPES`; PR #3019 (`2158205c7eb1`) removed
mode/band/VFO controls and added `_OBSERVED_RF_ADMISSION_FREE_FAMILIES`.

## Deletions

None authorized. Liveness, dynamic/public/downstream and tests-only guards remain
unverified; consolidation findings do not authorize legacy-consumer deletion.

## Consolidations and missing surfaces

### F1 — queued work lacks a one-entry claim and identity cancellation

**Verdict / rank:** B, shared gap / displaced lifetime responsibility.
**Definition / elements:** `runtime/_poller_types.py: CommandQueue.drain_entries`,
`put_ordered`, and `CommandQueueEntry.future`.
**Consumers:** `web/radio_poller.py: RadioPoller._run`,
`backends/yaesu_cat/poller.py: YaesuCatPoller._drain_commands`, and
`backends/rigctld_client/radio.py: RigctldClientObservationPoller._drain_commands`.
**Observation / divergence:** draining removes all queued segments before the
first entry settles. A later segment scan cannot find an already-drained entry;
an empty queue therefore does not establish that ordinary execution is idle.
**Prior ruling:** the 2026-09-01 ADR separates urgent OFF from cleanup barriers.
**In-flight:** the shared ordered entry and optional completion future exist;
the listed drainers already use them. No new queue is warranted.
**Required surface:** one-at-a-time claim and pending cancellation by entry
identity within the existing queue; keep completion distinct from cancellation
of a caller's wait. Do not mark an active operation complete on caller timeout.
**Depends on:** the existing drainer's execution-lifetime/retirement contract.
**Confidence:** high for the queue gap; active-provider isolation is not proven.
**Falsifier:** an existing consumer-visible claim that retains active ownership
and cancels only the identified pending entry across all three drainers.
**Fix class / actionable:** design / yes, bounded queue contract first.

### F2 — a narrow terminal kernel is shared work, backend policy is local

**Verdict / rank:** A for terminal mechanics, C for backend policy / parallel.
**Definition / elements:** `web/radio_poller.py: RadioPoller._execute_queued_entry`
and `_run`; `backends/yaesu_cat/poller.py: YaesuCatPoller._drain_commands`;
`backends/rigctld_client/radio.py: RigctldClientObservationPoller._drain_commands`.
**Consumers:** each poller's run loop invokes its own drain/execute path; each
path consumes the same `runtime/_poller_types.py: CommandQueueEntry` definition.
**Observation / divergence:** each checks cancelled futures and completes or
fails a future around a backend leaf. Icom reconnect backoff, Yaesu deferred
policy, and rigctld physical-readback correlation are not equivalent mechanics.
**Prior ruling:** the 2026-08-30 command-path audit found the drain duplication;
the 2026-09-01 ADR does not move manufacturer semantics into ingress.
**In-flight:** `core/command_service.py: CommandService.execute` already permits
an explicit per-call executor without changing its configured default.
**Required surface:** only the whole-execution terminal/future ownership kernel;
retain backend hooks and the existing service's lifecycle identity. Preserve
coalesced `future=None` entries and their enqueue-return behavior.
**Depends on:** F1 claim and lifetime semantics; do not centralize policy first.
**Confidence:** medium; the exact extractable kernel is the narrow claim.
**Falsifier:** a backend terminal distinction that cannot survive a shared
completion kernel while leaving its policy/readback hook local.
**Fix class / actionable:** consolidate / yes, terminal mechanics only.

### F3 — managed ingress migration is incomplete, authority is not missing

**Verdict / rank:** already shared authority; B at the ingress join / diverged.
**Definition / elements:** `runtime/managed_tx_authority.py:
ManagedTxAuthority.submit_ptt`, `ptt_down`, `ptt_up`, and `owner_disconnect`.
**Consumers:** the public PTT wrappers call `submit_ptt`; the selected ingress
paths are `web/handlers/control.py: ControlHandler.run` and `_execute_intent`,
and `rigctld/server.py: RigctldServer._handle_client` through
`rigctld/handler.py: RigctldHandler.execute` and `_route_ptt`.
**Observation / divergence:** the authority returns its original owned task and
transition/settlement pair, but these serial readers and legacy PTT routes do
not establish the shared ordinary-operation predecessor relationship.
**Prior ruling:** the 2026-09-01 ADR requires owner-scoped UP and one abort fence.
**In-flight:** `submit_ptt` registers pending ON, observes predecessor completion,
and removes registration before transition; OFF revokes the corresponding scope.
**Required surface:** use that task with a real whole-operation ON queue seat;
keep OFF readable and immediately submitted, with ordered truthful rigctld
responses. A fabricated no-op barrier or second pending-ON registry is not a join.
An effectful request without its original settlement cannot acquire a success ACK
merely because admission or task creation succeeded.
**Depends on:** F1/F2 and composition-root injection of consistent references.
**Confidence:** high on the gap, not an unbuilt integration.
**Falsifier:** a real cross-ingress held ordinary → pending ON → OFF trace that
uses the same queue/authority, never emits stale ON, and preserves terminal truth.
**Fix class / actionable:** design / yes, consumer migration rather than authority replacement.

### F4 — foreground turns and safe drainer handoff need explicit boundaries

**Verdict / rank:** B / displaced execution-lifetime responsibility.
**Definition / elements:** `web/radio_poller.py: RadioPoller._run`;
`backends/yaesu_cat/poller.py: YaesuCatPoller._poll_medium` and `_poll_slow`;
`backends/rigctld_client/radio.py: RigctldClientObservationPoller._run_loop`,
`_poll_medium`, `_poll_slow`, and `stop`.
**Consumers:** these loops schedule backend queries and consume foreground work;
rigctld-client's medium loop drains before its readback batch, while its slow
loop independently performs slow observations.
**Observation / inference:** queue priority alone cannot give a turn between
whole background exchanges or prove that an old active leaf has stopped writing.
Cancellation of a task is not proof of provider/transport isolation.
**Prior ruling:** the 2026-09-01 ADR separates shutdown retirement from urgent OFF.
**In-flight:** poller stop and provider generation checks exist; their presence
does not prove cross-ingress whole-operation lifetime or replacement isolation.
**Required surface:** one actual drainer, turns between whole background
exchanges, and proven quiescence/isolation before a replacement can claim work.
Never split an ordinary read-modify-write operation or classify every query as
background merely by name. Unproven retirement must fail closed, not fake completion.
**Depends on:** F1/F2 and the existing provider retirement boundary.
**Confidence:** medium; exact cancellation-resistant leaf behavior is unknown.
**Falsifier:** ungated stop/replacement tests during active exchange and loop
sleep, with repeated cancellation, proving actual join and no late old-provider write.
**Fix class / actionable:** design / yes, bounded lifetime proof before migration.

## Weakest link

F2: verify backend failure/readback ordering before extracting terminal mechanics.

## Cleared

Shared queue/service/authority definitions; per-call executors; positional replies;
backend vocabulary/readback correlation; coalesced enqueue-return; raw/SDK exclusion.
None implies completed managed ingress assembly.
