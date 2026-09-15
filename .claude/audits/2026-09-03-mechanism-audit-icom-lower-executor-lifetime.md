# Mechanism audit draft: Icom lower-executor lifetime

Audited source: `4e4c1782bf90ba5517d5d193c0344a23c4c8ff66`.
Source tree: `2c6c74005327bb818907ce00d7ccae8cc84124ce`.
Method: `.claude/skills/mechanism-audit/SKILL.md`, bounded helper-level mode.
Status: draft for independent adjudication, not an Agent Review gate verdict.

## Scope, definitions and consumers

Scope is commander task lifetime/admission, captured CI-V execution resources,
and their test helpers. This is not a whole-module step-3a dead-code census.
Evidence method: source reading and literal `rg -n` searches for definitions,
`IcomCommander(`/`CivRuntime(`, `stop`/`join`, `cancel`/`retire`,
`generation`/`epoch`, and the named consumer calls in `src/` and `tests/`.

| Definition | Observed consumers and bounded responsibility |
| --- | --- |
| `src/rigplane/commands/commander.py: IcomCommander.stop`, `send`, `_loop` | `src/rigplane/runtime/_civ_rx.py: CivRuntime.start_worker`, `stop_worker`, `_send_civ_raw` create/use the queue owner; commander tests exercise its task lifecycle. |
| `src/rigplane/runtime/_civ_rx.py: CivRuntime._execute_civ_raw`, `_drain_ack_sinks_before_blocking` | `CivRuntime.execute_civ_raw` is the commander's callback; `CivRuntime._send_civ_raw` also has a direct fallback. `src/rigplane/runtime/radio.py: CoreRadio._execute_civ_raw` delegates to the runtime. |
| `src/rigplane/core/civ.py: CivRequestTracker` | Captured execution uses existing registration, unregister and timeout operations; `CivRuntime.advance_generation` uses its generation/fail-all lifecycle. |
| `tests/test_commander.py: _HeldExecute`, `_stop_and_check_actual_join`, `_cleanup_held_commander` | Held-stop/converse tests consume these fixtures; `tests/test_radio.py: TestCapturedExecuteLifetime` separately controls CI-V suspension and retirement. |

These are definition/call-site observations, not a complete consumer census.
The final row is test scaffolding, not runtime policy.

## Prior ruling and steelman

The accepted 2026-09-01 `docs/plans/2026-09-01-runtime-transmit-authority.md`,
sections Boundaries and composition root and Urgent ForceOff ordering contract,
separates urgent OFF from unrelated cleanup: resource-retirement barriers must
not delay urgent `force_receive`; only a current accepted release clears debt.
It assigns intent/debt/TOT/fence ownership to `ManagedTxAuthority` and preserves
owner-local `PTT_UP`. The 2026-09-02 ForceOff cleanup audit records that boundary.

Steelman: the central authority cannot replace a commander's actual worker join
or a protocol executor's captured transport/tracker cleanup. Conversely, those
local barriers cannot decide managed intent or clear release debt. Observation:
`IcomCommander.stop` retains/shields its captured worker; `_loop` finishes the
inner execution before exit. `CivRuntime._execute_civ_raw` captures the existing
transport, tracker and epoch, checks currency after suspension, and cleans the
captured Future/sink rather than retargeting replacement accounting.
Inference: these are complementary ownership boundaries, not competing owners.

The shared target already exists at
`src/rigplane/runtime/managed_tx_authority.py: ManagedTxAuthority`:
`_force_off_locked` advances the abort fence and `_execute` settles effects.
Literal constructor search found its calls in `tests/test_managed_tx_authority.py`
and `tests/test_managed_tx_ingress_admission.py`, not in `src/`; this limited
search does not establish application assembly or authorize another authority.

## Deletions

None proposed. No exhaustive read/write, dynamic-access or downstream-consumer
census was performed; no symbol is declared dead or safe to delete.

## Provisional helper-level adjudication

- C — commander lifetime/admission: the existing `IcomCommander` owns this queue
  and its join. Cancellation-request isolation is separate from successful-stop
  task completion. No replacement queue or managed admission gate is indicated.
- C — captured execution cleanup: `CivRuntime._execute_civ_raw` and
  `_drain_ack_sinks_before_blocking` serve protocol resource currency. Their
  captured existing epoch is not a newly minted managed abort fence.
- Already shared — `CivRequestTracker` supplies waiter/sink/generation primitives;
  captured cleanup reuses them. There is no proposed parallel tracker.
- C — test helpers: `_stop_and_check_actual_join` inspects captured Task state;
  `_HeldExecute` supplies suspension and `_cleanup_held_commander` joins cleanup.
  They use shared asyncio primitives; event state alone is not completion proof.

For these bounded elements, divergence is responsibility rather than a competing
managed policy; canonical candidates are the existing definitions above.
Actionability: no consolidation proposed. Depends on: separate application
integration and final-OFF ordering evidence before any broader safety claim.
Falsifier: a caller granting these local components intent/debt authority, or a
successful checked stop before actual captured worker/execute termination.

## Executed frozen-matrix evidence

[Initial run, attempt 1](https://github.com/rigplane/rigplane-core/actions/runs/33763404223/attempts/1)
and [independent repeat, attempt 1](https://github.com/rigplane/rigplane-core/actions/runs/33765201547/attempts/1)
used DIAG `fafc6055896032d71f3400af9c7a01978795261a` and the source pin above.
Each recorded 11 invocations / 136 cases: 124 passed and 12 expected CALL
AssertionErrors from eight declared faults. Baseline/equivalent/restored each
passed 28 cases; all five controls passed in all eleven invocations, including
the terminal-with-release-unset converse under unshielded and early-return joins.

These were `full.yml` workflow-dispatch executions of the isolated diagnostic
job, not normal full-matrix runs. The workflow command was:
`uv run --no-sync python "$GITHUB_WORKSPACE/harness/.github/diagnostics/icom_lower_executor_proof.py"`.
Artifact `verdict.json`, `preflight.json`, phase reports and process records
support these counts; environment records pin CPython 3.11.16 and uv 0.12.9.
The strict validator required exact node, CALL exception type/message and final
traceback checkpoint. Mutation inequality, immutable test/import identities,
seed-pristine and restored-source checks passed. Process groups were absent
after normal waits; forced-kill, outer-timeout and signal branches were not run.

[Earlier run 33733903535](https://github.com/rigplane/rigplane-core/actions/runs/33733903535)
remains INVALID: M2 failed the old release-event proxy, not its declared oracle.
The renewed tests distinguish cancellation propagation from actual completion;
this evidence does not retroactively validate that earlier run.

## Weakest link

The local-to-application boundary is unproven here. Check upper setter/RMW finally
joins, queued currency carriers, inline recovery and final-OFF ordering first.
No whole-setter, ACK attribution, shared-queue integration, whole-app, observed-RF
or physical-radio guarantee follows. New integrated-head PR CI is separate.

## Cleared

Within the pinned tract: the captured worker handle, cancellation isolation,
restart identity, captured tracker cleanup and actual-task test oracle are
supported. No additional authority, watchdog, debt registry or product mechanism
is indicated by this bounded analysis. Final independent adjudication remains due.
