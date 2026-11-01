# Bench nightly

`.github/workflows/bench-nightly.yml` checks the bench radios every night.
It is informational, not a required check.

## Runners and radios

Each matrix entry carries its own `runs_on` label list. There are no
GitHub-hosted runners in this workflow.

| Job label | Runner labels | Connection |
|---|---|---|
| FTX-1 | `[self-hosted, macOS, ARM64, bench]` | USB serial (`yaesu-cat`) |
| IC-7610 (simulator) | `[self-hosted, Linux, ARM64, light]` | In-repo IC-7610 LAN simulator on loopback |

The macOS bench runner is `mm-macos-ci-1` (org runner, runner group
`rigplane-ci`): online with labels exactly `self-hosted`, `macOS`, `ARM64`,
`platform`, `bench`. Real-radio legs use `[self-hosted, macOS, ARM64, bench]`.

The IC-7610 hardware leg was removed on 2026-10-23: the bench no longer hosts
an IC-7610. Nightly IC-7610 coverage uses
`rigplane_testkit/icom/simulator.py` (`MockIcomRadio` with the recorded LAN
table from `tests/testkit/ic7610_fakes.py: ic7610_lan_fake`), started on
loopback inside the job via `scripts/bench/ic7610_lan_simulator.py`.

The IC-7300 leg was dropped: that radio moved to the GTR7 Windows host and
there is no self-hosted Windows runner on GTR7 available to `rigplane-core`
today. It will not return until such a runner exists.

The IC-705 leg was dropped while that radio is away from the bench (field
use). It will return through a follow-up that reads the matrix from an
infra-control inventory file; until then it is not in this workflow.

Light-runner legs use `[self-hosted, Linux, ARM64, light]` (repo runners
`mm-light-core-1`/`2`/`3` and org runner `mm-light-org-1`).

## What runs

One matrix job per radio above, one at a time (`max-parallel: 1`);
`fail-fast: false` keeps one radio's failure from cancelling the others.
There is no job-level `continue-on-error`, so a night passes for a radio only
when its job concludes success (see Outcomes below).
Each job:

1. **Guard.** `scripts/bench/guard.sh check-env` fails the job when any of
   `ICOM_ALLOW_PTT`, `ICOM_ALLOW_CW_TX`, `ICOM_ALLOW_AUDIO_TX`,
   `ICOM_ALLOW_POWER_CONTROL` or `RIGPLANE_HW_ALLOW_TX` is set, whatever its
   value. Every `rigplane` and `pytest` invocation in the workflow goes
   through `guard.sh run --`, which repeats the environment check and refuses
   argv containing `--tx-allowed` or `--tuner-allowed`, including argparse
   abbreviations such as `--tun`. Commands not wrapped this way are not checked.
2. **Lock.** For real bench radios, `scripts/bench/bench-lock.sh acquire
   <radio>` takes a per-radio `mkdir` lock under `/tmp/rigplane-bench-locks`
   (`BENCH_LOCK_DIR`). A held lock records `<radio>: skipped: busy`, the
   radio steps are skipped, and the job fails red in the `Outcome: busy`
   step. A lock older than two hours (`BENCH_LOCK_STALE_SECONDS`) is taken
   over. Only processes that use this script see the lock. The simulator leg
   skips the lock (no shared bench hardware) but still runs the guard.
3. **Validate.** `rigplane validate --hardware --allow-hardware --read-only`
   with `--provider native` (or `both` on manual dispatch).
4. **Night-to-night diff.** `scripts/bench/night_diff.py` compares tonight's
   normalized artifact with the previous night's of the same radio, mode and
   provider, and lists checks added, removed or changed. The step fails when
   anything changed.
5. **Hardware pytest** (IC-7610 simulator leg only; needs `ICOM_HOST` on
   loopback). On read-only nights only `TestConnection` runs, because the
   shared `radio` fixture in `tests/integration/conftest.py` writes (split,
   receiver, power, mode, DIGI-SEL, frequency) on teardown. On rx-write-restore
   nights the same class list as the former hardware IC-7610 leg runs.
6. **`rigplane diagnose`**, saved on the runner as `diagnose-latest.zip`.
7. **Upload.** The normalized validate artifact and the diff, kept three days.

## Outcomes

A night passes for a radio only when its job concludes success, which
requires Validate — and, where they run, the night-to-night diff and the
hardware pytest subset — to have run and passed. Three endings that used
to be silent green now fail red with their own step names: `Outcome: not
configured` (radio secrets missing), `Outcome: busy` (bench lock held by
another run) and `Outcome: dry run` (a dry run never certifies a radio).
`rigplane diagnose` and the artifact upload are evidence only and cannot
decide the night; `Release bench lock` still fails the job when it fails,
because a leaked lock matters.

## Modes

| Trigger | Mode |
|---|---|
| Weekday schedule (`30 1 * * 1-5`) | read-only |
| Weekend schedule (`30 1 * * 0,6`) | rx-write-restore: validate without `--read-only` (read, change, verify, restore), and pytest adds `TestFrequency`, `TestMode`, `TestMeters`, `TestStatus`, `TestVFO` |
| `workflow_dispatch` | read-only, unless `rx_write_restore` is ticked |

`dry_run` on manual dispatch runs the guard, the lock (when the matrix entry
uses one) and `rigplane diagnose` only: no validate, diff or pytest. The job
then fails red in the `Outcome: dry run` step: a dry run is never a
certification night.

```bash
gh workflow run bench-nightly.yml --ref <branch> -f dry_run=true
```

## Secrets the owner creates

Repository secrets (Settings → Secrets and variables → Actions). A serial bench
radio whose secret is missing reports `skipped: not configured` and fails red
in the `Outcome: not configured` step. Serial device paths are secrets too, so
the runner log masks them. The simulator leg needs no secrets.

| Secret | Used as |
|---|---|
| `BENCH_FTX1_SERIAL` | `ICOM_SERIAL_DEVICE` for the FTX-1 |

Removed with the dropped legs (secrets may remain in the repo settings but are
unused here): `BENCH_IC705_SERIAL`, `BENCH_IC7610_HOST`, `BENCH_IC7610_USER`,
`BENCH_IC7610_PASS`, `BENCH_IC7300_SERIAL`.

## What stays on the runner

Per radio, under `~/.rigplane-bench-nightly/<radio>/<mode>-<provider>/` of the
runner user: `latest.json` (the normalized artifact the next night compares
against), `current.raw.json` (the raw artifact, which records the LAN endpoint)
and `diagnose-latest.zip` (runner logs and configuration). None of them is
uploaded, because workflow artifacts of this public repository are public.

## Certification status

`.github/workflows/certification-status.yml` (manual dispatch from `main`) runs
`.github/scripts/certification_status.py`. It reads the scheduled runs of
`bench-nightly.yml` on `main`, their jobs and steps, from the Actions API (never
from artifacts) and writes a summary table to the log and the run page.

```bash
gh workflow run certification-status.yml --ref main -f radios=IC-7610 -f nights=3 \
  -f 'no_verdict=<YYYY-MM-DD>:IC-7610'
```

The inputs are `radios` (the scope, default `IC-7610`), `nights` (default 3) and
`no_verdict` (default empty); the script takes them as `--radios`, `--nights` and
`--no-verdict`.

- **Night.** A UTC date, taken from the run's `created_at`. Its run is the
  newest completed `schedule` run created that day; manual dispatches and older
  runs of the same day are ignored. A day between two runs with no run of its
  own is `missing night` for every radio; nothing is counted before the oldest
  run or after the newest. The date is the cron's own date unless the run is
  created 22 h 30 min or more after its 01:30 UTC cron. Saturday and Sunday are
  write-and-restore nights, the other days read-only.
- **PASS.** The radio's job (its name is the radio label) concluded `success`
  and its `Validate` step concluded `success`. Any other night carries a name:
  `busy`, `not configured` or `dry run` (the failed `Outcome: …` step),
  `validate failed`, `diff failed`, `failed: <step>`, `missing` (no job for that
  radio), `missing night` (no run that day), `job <conclusion>`, or
  `validate not run` (the job concluded `success` but its `Validate` step did
  not).
- **No verdict.** A night on which another certification item held the bench
  lease. It is never inferred from logs: it is only what `no_verdict` names, a
  comma-separated list of `YYYY-MM-DD:RADIO` entries, or `YYYY-MM-DD` for every
  radio in scope, recorded by hand. It replaces whatever the night's job did,
  shows as `no verdict (bench lease held by another certification item)`, and
  neither counts toward the streak nor ends it. It is not a write-and-restore
  night, whatever the weekday. Any busy night that is not listed stays `busy`
  and ends the streak.
- **Streak.** PASS nights from the newest backwards, walking over no verdict
  nights, to the first night that is neither. A radio is certified when its
  streak reaches `nights` (default 3) and contains a write-and-restore night
  anywhere in it, not only among the last `nights`.
- **Scope and verdict.** Only the radios named by `radios` (comma-separated job
  labels) are judged; the script has no default scope and refuses an empty
  one. Other radios with a job on the newest night are listed as `out of scope`
  with their nights and never change the verdict. CERTIFIED needs every radio in
  scope certified, and the first line of the summary names them: `CERTIFIED for
  IC-7610` or `NOT CERTIFIED for IC-7610: <reason>`. Exit status 1 means not
  certified; 2 means the run history could not be read or is not newest first,
  or an argument is invalid. At most the 30 newest nights are read.
