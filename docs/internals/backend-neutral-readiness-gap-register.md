# Backend-Neutral Readiness Gap Register

Status: MOR-424 release-claim guard
Date: 2026-06-12

This register keeps backend-neutral provider readiness claims aligned with
implemented Core coverage. It is not a private validation matrix and it is not a
hardware pass/fail log. Hardware evidence and release waivers stay in
`docs/internals/radio-state-pipeline-validation.md`; private customer/device
evidence stays outside this open-core repository.

## Claim Rules

- A field may be described as backend-neutral only when it has a public
  `FieldPath`, acquisition policy, and provider adapter coverage, or when the
  provider explicitly declares it unsupported.
- A provider may be described as observation-backed only for fields listed in
  its acquisition profile or emitted by its adapter.
- Hardware-only observations do not become code defects unless they reproduce
  as generic protocol/backend failures.
- Broad claims such as "FTX-1 backend-neutral parity" or "external rigctld
  readiness" require the hardware checklist in
  `radio-state-pipeline-validation.md` to pass or carry an explicit waiver.

## Provider Coverage

| Provider | Implemented Core coverage | Explicit unsupported declarations | Remaining claim boundary |
|---|---|---|---|
| Icom CI-V | The MOR-437 Icom field families are observation-backed through `runtime/_civ_rx.py`; residual compatibility mirrors are listed in `legacy-state-writer-inventory.md`. | Model profiles can still omit unsupported controls or validation capabilities. | Do not claim every v2/IC-7610 front-panel control is observation-backed until the MOR-488/MOR-494 style readback follow-ups are closed or waived. |
| Yaesu CAT / FTX-1 | `rigs/ftx1.toml` declares `provider = "yaesu_cat"` acquisition for freq/mode, PTT, meters, AF/RF/squelch, RF front-end, DSP controls, split/active receiver, RIT, tuner, dial lock, CW, and tone/tSQL fields; `backends/yaesu_cat/observations.py` emits the corresponding observations. | `global.tx_state.power_on` is unsupported in the FTX-1 acquisition profile. `monitor` is intentionally not a gap because the FTX-1 CAT `ML` command is rejected and the monitor surface is EX-menu only. | No broad "FTX-1 control parity" claim without a fresh release decision. Remaining tracked follow-ups include MOR-474 (break-in delay parse width) and MOR-465 (RIT/XIT semantic re-home). |
| External rigctld / Hamlib | `backends/rigctld_client/observations.py` adapts external rigctld reads and command responses for freq/mode, PTT, RF/AF gain, preamp, attenuator, NB, NR, and filter width. | `global.tx_state.power_on` is declared unsupported because external rigctld exposes no power state. `receiver.main.vfo.active_slot` is declared unsupported when the connected rig lacks VFO slot commands. | Do not claim universal Hamlib readiness for rigs without VFO support or power-state reporting. These are provider-contract limits, not hidden Core defects. |
| Hardware release checklist | The public checklist covers IC-7610 LAN, X6200 serial, Yaesu-like polling, and external rigctld/Hamlib scenarios. | Hardware-only gaps are recorded as validation results or waivers under MOR-348. | Hardware checklist failures become code issues only when they identify reproducible generic Core behavior. |

## Tracking Matrix

| Limitation class | Tracking decision | Current state |
|---|---|---|
| FTX-1 observation backing for legacy-only field families | Split into implementation tasks instead of treating MOR-424 as a hidden code bucket. | MOR-443, MOR-444, MOR-445, MOR-446, MOR-447, MOR-448, MOR-449, MOR-452, MOR-458, MOR-460, MOR-461, MOR-462, and MOR-463 are completed. |
| FTX-1 live parser/poll resilience defects | File only deterministic CAT/backend defects found by live validation. | MOR-473 and MOR-474 are completed; `break_in_delay` now accepts the live `SD09;` reply fixture. |
| Public naming / semantic cleanup | Track as compatibility work, not release blockers. | MOR-465 remains open for RIT/XIT field naming/re-home compatibility. |
| IC-7610 front-panel readback audit | Treat as model-specific readback coverage, not a broad backend-neutral readiness claim. | MOR-488 remains the audit umbrella; completed child issues are reflected in the project board. |
| External rigctld power and VFO-slot gaps | Declare unsupported in the provider acquisition profile instead of filing generic code defects. | Implemented in `backends/rigctld_client/observations.py`; no extra code issue is required unless a supported rigctld command misbehaves. |
| Hardware readiness claims | Keep as validation or waiver decisions. | MOR-348 remains the release checklist owner. |

## Regression Matrix Impact

The automated regression matrix may assert provider-neutral behavior only for
implemented provider fields. Tests for unsupported fields should assert
`FieldAvailability.UNSUPPORTED`, missing field status, or a documented waiver;
they should not silently fall back to legacy `RadioState` mirrors and call that
backend-neutral readiness.
