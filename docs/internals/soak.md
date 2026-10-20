# Operated soak (MOR-3236)

A nightly 1-hour soak operates the real web stack — `WebServer`,
`RigctldServer`, `IcomRadio` — against the IC-7610 simulator
(`rigplane_testkit.icom.simulator.MockIcomRadio`) from one process:
tuning with readback, mode changes, a rigctld replay
(`F`/`f`/`M`/`m`/`t`), forced radio disconnect/connect cycles, and a
WebSocket client subscribed to state and audio throughout, and, 20 s after the
last one ended, a multi-session TX cycle: two controller sessions race to key, one holder ends
by unkey, delete, a cut link or silence, the other keys next (MOR-3538).

The driver lives in `scripts/soak/soak_driver.py`; the run instructions,
cadence constants, pass criteria (including the per-reconnect ±5 leak
gap and what the recovery criterion measures), and the metrics-sampler
contract are documented in `scripts/soak/README.md` — the single source
for those. The workflow is `.github/workflows/soak-nightly.yml`,
informational and not a required check, with a `workflow_dispatch`
duration input for smoke runs and a `certification` input that runs the
24 h job `soak-24h` instead; each job writes its verdict and measured
numbers to the job summary with `scripts/soak/report_summary.py`, and the
report artifact is a best-effort upload.

`tests/test_soak_driver.py` pins the smoke, the criterion math, the
sampler merge, the teardown-time report write, and the mutation teeth
(suppressed connect, unobserved disconnect, cycle outliving the run, a silent
holder keeping the transmitter, a session outliving its channel).
