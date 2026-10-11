# Mutation testing (weekly trend)

The repository runs a weekly [mutmut](https://mutmut.readthedocs.io/) 3.x
sweep over a fixed list of small, high-risk modules. The job is **trend
only**: it uploads per-module killed/survived counts as artifacts, posts
nothing to any pull request, and no required check depends on it. Its
value is watching the numbers move week over week while the stabilization
freeze limits how the code itself moves.

## Where it runs

* Workflow: `.github/workflows/mutmut-weekly.yml` — `schedule` Sunday
  22:30 UTC plus `workflow_dispatch`, on the `[self-hosted, linux, build]`
  runner, `--max-children 4` (the runner-concurrency cap, MOR-2834).
* Configuration: the `[tool.mutmut]` table in `pyproject.toml`.
* Artifacts: `mutmut-results.txt` (`mutmut results --all=true` text) and
  `mutmut-summary.json` (per-module counters from
  `scripts/mutation/summarize.py`), both attached to the workflow run for
  90 days.

## What gets mutated

`only_mutate` in `pyproject.toml` names the fifteen modules from MOR-3285:
the web delta encoder, the rigctld protocol and routing modules, the CIv
codec, the audio codecs, the command frame codec, the core types and
state store, the acquisition drain, the controller authority, the TX
safety/target/observation modules, the managed TX fence, and the Yaesu CAT
parser. The whole `src/` tree is copied into the `mutants/` sandbox
(`source_paths`) so imports stay intact; `also_copy` brings
`rigplane_testkit/` (tests import it via the pytest `pythonpath` setting)
and `rigs/` (profile TOML data) along.

## Which tests run against the mutants

`pytest_add_cli_args_test_selection` is deliberately **not** every file
that imports the mutated modules. That import closure (~84 test files)
was measured first: popular functions such as the frame codec's
encode/decode inherit every integration test that touches them, and
per-mutant runs reached roughly a minute. The committed selection is the
focused per-module unit-test files, which keeps the full fifteen-module
sweep near fifteen minutes. `mutmut` runs only the tests its stats phase
saw covering each mutated function, so per-mutant cost stays bounded by
those focused tests.

Two kinds of tests must stay out of the selection entirely:

* Source-introspection tests — `tests/test_command_map_parity.py`
  (`_builders` walks `vars()` of the command modules and matches
  `inspect.signature`), `tests/test_profile_command_coverage.py`
  (`test_every_builder_resolves_by_exactly_one_route`), and
  `tests/test_tx_observation.py`
  (`test_live_tree_has_no_retired_transmit_authority_references` scans
  the tracked tree on disk). Inside the `mutants/` sandbox every
  mutated function is a trampoline whose signature is `*args, **kwargs`,
  so these counts and scans disagree with their baselines by design.
* A failed stats phase aborts the whole sweep (`mutmut` runs the
  selection with `-x`), so one incompatible file costs the entire run.

## Baseline (2026-10-11, commit `8081ec75`, 4 children, fresh cache)

Full sweep of all fifteen modules, 4971 mutants:

| killed | survived | timeout | no tests |
|-------:|---------:|--------:|---------:|
| 2358   | 1465     | 7       | 1141     |

Per module (killed / survived / timeout / no tests):

| module | k | s | t | nt |
|---|--:|--:|--:|--:|
| `rigplane.web._delta_encoder` | 143 | 17 | 0 | 2 |
| `rigplane.rigctld.protocol` | 87 | 34 | 0 | 0 |
| `rigplane.core.civ` | 172 | 170 | 0 | 20 |
| `rigplane.audio._codecs` | 26 | 476 | 0 | 0 |
| `rigplane.commands._frame` | 150 | 47 | 0 | 0 |
| `rigplane.core.types` | 56 | 35 | 0 | 29 |
| `rigplane.core.state_store` | 673 | 262 | 0 | 35 |
| `rigplane.core.acquisition_drain` | 207 | 29 | 5 | 0 |
| `rigplane.runtime.controller_authority` | 248 | 161 | 0 | 74 |
| `rigplane.core.tx_safety` | 421 | 151 | 0 | 0 |
| `rigplane.core.tx_target` | 11 | 20 | 0 | 70 |
| `rigplane.core.tx_observation` | 23 | 18 | 0 | 1 |
| `rigplane.runtime.managed_tx_fence` | 62 | 15 | 0 | 0 |
| `rigplane.backends.yaesu_cat.parser` | 79 | 28 | 2 | 0 |
| `rigplane.rigctld.routing` | 0 | 2 | 0 | 910 |

Measured wall times on the self-hosted build runner: the frame module
alone (197 mutants) took 3 m 50 s end to end; the full sweep finished in
about fifteen minutes (4772 of 4971 mutants completed inside a 590 s
window, the slower tail included timeouts at ten times the covering
tests' duration). The workflow's `timeout-minutes: 30` is that measured
full sweep doubled for headroom, well under the 180-minute cap for this
job family.

## Reading a surviving mutant

A **survived** mutant means mutmut changed a line, ran every selected
test that covers that function, and nothing failed. Inspect it with:

```bash
uv run mutmut show rigplane.core.civ.x_parse__mutmut_12
```

which prints the status line and the exact diff of the mutation. Then
apply the house rule:

> **A survivor is a test to write, not a config to tweak.**

Do not make the number look better by moving the module out of
`only_mutate`, adding to `do_not_mutate`, or pruning the test selection.
Either write the missing test that pins the mutated behavior, or record
in the ticket why this particular mutation is behaviorally equivalent
(some `is`/`==` and logging-string mutations are). The trend is useful
exactly because it is honest.

Other statuses: **timeout** (the mutation likely broke a wait/loop and
the covering tests hit their per-test timeout — read it as a kill with a
performance angle), **no tests** (no selected test covers the mutated
function at all — a coverage hole in the focused selection), and
**suspicious** (an indeterminate exit; worth `mutmut show` plus a manual
rerun).

## Known blind spots at the baseline

* `rigplane.rigctld.routing` is effectively unmeasured: 910 of its 912
  mutants report `no tests`. The module builds its route tables at
  import time, so the selected tests exercise table data, not the
  trampolined functions mutmut attributes coverage through. Fixing this
  needs either dedicated routing unit tests or a different measurement
  approach — tracked in MOR-3285, not solved by this job.
* `rigplane.core.tx_target` has no dedicated unit-test file; 70 of 101
  mutants report `no tests`.
* `rigplane.audio._codecs` kills only 26 of 502 mutants: the focused
  codec tests assert container behavior, not the DSP math mutmut flips
  (thresholds, window constants). That is a large, real test gap the
  trend makes visible.

## Running it yourself

Manual sweep from the GitHub Actions tab (`workflow_dispatch`) or
locally with the repository checked out:

```bash
uv run mutmut run --max-children 4
uv run mutmut results --all=true
uv run python scripts/mutation/summarize.py --results <results.txt> <modules...>
```

`mutmut` copies sources into `mutants/` and caches per-mutant results
there; delete the directory for a from-scratch sweep. Be aware a full
sweep binds four CPU workers for roughly fifteen minutes on fast
hardware and longer elsewhere.
