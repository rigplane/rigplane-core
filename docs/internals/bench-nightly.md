# Bench nightly

`.github/workflows/bench-nightly.yml` checks the bench radios every night on the
macOS bench runner (`[self-hosted, macOS, ARM64, bench]`). It is informational,
not a required check.

## What runs

One matrix job per radio (IC-7610 over LAN, IC-7300 and FTX-1 over USB serial),
one at a time (`max-parallel: 1`), `fail-fast: false` and
`continue-on-error: true`, so a busy or failing radio does not hide the others.
Each job:

1. **Guard.** `scripts/bench/guard.sh check-env` fails the job when any of
   `ICOM_ALLOW_PTT`, `ICOM_ALLOW_CW_TX`, `ICOM_ALLOW_AUDIO_TX`,
   `ICOM_ALLOW_POWER_CONTROL` or `RIGPLANE_HW_ALLOW_TX` is set, whatever its
   value. Every `rigplane` and `pytest` invocation in the workflow goes
   through `guard.sh run --`, which repeats the environment check and refuses
   argv containing `--tx-allowed` or `--tuner-allowed`, including argparse
   abbreviations such as `--tun`. Commands not wrapped this way are not checked.
2. **Lock.** `scripts/bench/bench-lock.sh acquire <radio>` takes a per-radio
   `mkdir` lock under `/tmp/rigplane-bench-locks` (`BENCH_LOCK_DIR`). A held lock
   records `<radio>: skipped: busy` and the radio steps are skipped. A lock older
   than two hours (`BENCH_LOCK_STALE_SECONDS`) is taken over. Only processes that
   use this script see the lock.
3. **Validate.** `rigplane validate --hardware --allow-hardware --read-only`
   with `--provider native` (or `both` on manual dispatch).
4. **Night-to-night diff.** `scripts/bench/night_diff.py` compares tonight's
   normalized artifact with the previous night's of the same radio, mode and
   provider, and lists checks added, removed or changed. The step fails when
   anything changed.
5. **Hardware pytest** (IC-7610 only; the `tests/integration` LAN fixtures need
   `ICOM_HOST`). On read-only nights only `TestConnection` runs, because the
   shared `radio` fixture in `tests/integration/conftest.py` writes (split,
   receiver, power, mode, DIGI-SEL, frequency) on teardown.
6. **`rigplane diagnose`**, saved on the runner as `diagnose-latest.zip`.
7. **Upload.** The normalized validate artifact and the diff, kept 14 days.

## Modes

| Trigger | Mode |
|---|---|
| Weekday schedule (`30 1 * * 1-5`) | read-only |
| Weekend schedule (`30 1 * * 0,6`) | rx-write-restore: validate without `--read-only` (read, change, verify, restore), and pytest adds `TestFrequency`, `TestMode`, `TestMeters`, `TestStatus`, `TestVFO` |
| `workflow_dispatch` | read-only, unless `rx_write_restore` is ticked |

`dry_run` on manual dispatch runs the guard, the lock and `rigplane diagnose`
only: no validate, diff or pytest.

```bash
gh workflow run bench-nightly.yml --ref <branch> -f dry_run=true
```

## Secrets the owner creates

Repository secrets (Settings → Secrets and variables → Actions). A radio whose
secrets are missing reports `skipped: not configured`. Serial device paths are
secrets too, so the runner log masks them.

| Secret | Used as |
|---|---|
| `BENCH_IC7610_HOST` | `ICOM_HOST` |
| `BENCH_IC7610_USER` | `ICOM_USER` |
| `BENCH_IC7610_PASS` | `ICOM_PASS` |
| `BENCH_IC7300_SERIAL` | `ICOM_SERIAL_DEVICE` for the IC-7300 |
| `BENCH_FTX1_SERIAL` | `ICOM_SERIAL_DEVICE` for the FTX-1 |

## What stays on the runner

Per radio, under `~/.rigplane-bench-nightly/<radio>/<mode>-<provider>/` of the
runner user: `latest.json` (the normalized artifact the next night compares
against), `current.raw.json` (the raw artifact, which records the LAN endpoint)
and `diagnose-latest.zip` (runner logs and configuration). None of them is
uploaded, because workflow artifacts of this public repository are public.
