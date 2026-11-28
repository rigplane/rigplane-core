VERDICT: BLOCK

**A. Open (P1).** The delta fixes the detached recovery join, but the CI-V watchdog join still uses `await task` and swallows `CancelledError`. I reran both races on `11eb18be58` with the real watchdog, mock transports, and a virtual clock. The fast repair made three stale discovery attempts by +21 s. The full recovery was still waiting and had made no fresh login. In the detached case, the full task waited for its predecessor while the predecessor joined the full task through `shield()`. Both were still pending at +21 s. Preserve cancellation at the watchdog join and prevent a superseded predecessor from joining its replacement. The existing preemption test stubs the recovery attempt and misses this boundary. [_civ_rx.py:1128](src/rigplane/runtime/_civ_rx.py:1128), [_civ_rx.py:1944](src/rigplane/runtime/_civ_rx.py:1944), [session_lifecycle.py:1336](src/rigplane/runtime/session_lifecycle.py:1336), [preemption test](tests/test_leon_recovery_lifecycle.py:50)


route run 20261128-003326-ea2edb: exit 0 after 258s; output in /Users/moroz/.claude/state/route-runs/20261128-003326-ea2edb.log
next: route feedback --run 20261128-003326-ea2edb --outcome accepted|rejected|reworked|run_failed [--ref …]
**B. Settled for recovery exceptions.** Owner teardown suppresses failures from the full owner and shared attempt before releasing. The watchdog stop and predecessor joins collect exceptions, the drain uses `asyncio.wait`, and repair callers receive the failure. I injected a `RuntimeError` during cancellation and verified that both disconnect and shutdown reached managed-TX release and closed the mock transports. The unit test passed. The sandbox blocked the loopback test at UDP bind. [Owner teardown](src/rigplane/runtime/session_lifecycle.py:1242), [watchdog stop](src/rigplane/runtime/_civ_rx.py:1165), [unit test](tests/test_civ_rx_coverage.py:946), [loopback test](tests/test_lan_full_recovery_priority.py:164)

**C. Settled in production.** `_civ_epoch` starts at integer `0` and `_soft_recovery_epoch` starts at `None`, so plain recovery stays soft. The test assertions passed, then teardown failed with `LeakedRadioError` because the test leaves its radio connected. [radio.py:665](src/rigplane/runtime/radio.py:665), [_civ_rx.py:1839](src/rigplane/runtime/_civ_rx.py:1839), [epoch test](tests/test_civ_rx_coverage.py:1074)

**D. Settled during `CONNECTING`, open through task completion.** The new test passed with four notices and one completed attempt. Once the radio reports `CONNECTED`, the callback is active while the full task still has work to do. I replayed the identical notice in that window and observed the first recovery get superseded and a second attempt start. The existing MOR-3350 test explicitly pins that restart for a fresh drop. The callback makes the same decision for a duplicate. [_civ_rx.py:1880](src/rigplane/runtime/_civ_rx.py:1880), [_control_phase.py:547](src/rigplane/runtime/_control_phase.py:547), [duplicate-notice test](tests/test_leon_recovery_lifecycle.py:108), [tail restart test](tests/test_civ_rx_coverage.py:1164)

**Other P0/P1.** None found. Both paths use the same lifecycle retry loop, with no per-radio branch.

**P2.** The reboot regression still mixes clocks. The test virtualizes the simulator and TX timers, but control status uses a wall-clock deadline over virtual packet waits. A silent-status probe was still pending after +2.1 virtual seconds against its 2 s deadline. The epoch test’s teardown error also needs fixing. [Reboot test](tests/integration/test_tx_under_fault_matrix.py:707), [status deadline](src/rigplane/runtime/_control_phase.py:1339)

Reviewed local HEAD `11eb18be58`. `gh-app` could not reach GitHub, so live PR and CI status remain unverified. The worktree stayed clean.


