# Browser TX authority test migration

The browser controller no longer implements radio admission, leases, command
confirmation, release debt, reconnect epochs, or TOT deadlines. Those
invariants remain executable in the server authority suites below; the browser
tests cover only media pre-arm/cleanup, intent transport, lifecycle, and
read-only projection.

| Retired browser invariant | Server-owned executable coverage |
| --- | --- |
| eligibility and admission | `tests/test_managed_tx_authority.py`, `tests/test_web_managed_tx_owner.py` |
| intent/lease state transitions | `tests/test_managed_tx_state.py` |
| serialized ON/ForceOFF effects and races | `tests/test_managed_tx_effect_lane.py`, `tests/test_managed_tx_submission_contract.py` |
| release debt and retry | `tests/test_managed_tx_state.py`, `tests/test_managed_tx_authority.py` |
| disconnect/shutdown safety | `tests/test_managed_tx_production_lifecycle.py`, `tests/test_web_teardown_unkey_gate.py` |
| timeout/TOT expiry | `tests/test_managed_tx_authority.py`, `tests/test_managed_tx_web_projection.py` |
| observed PTT projection | `tests/test_managed_tx_web_projection.py` |

Replacement browser coverage lives in:

- `thin-client-cutover.test.ts`: media pre-arm, late-ON suppression, PTT versus
  TRANSMIT versus ForceOFF routing, server rejection, audio death, lifecycle,
  and projection.
- `browser-dependencies-thin-client.test.ts`: terminal WebSocket admission
  results and canonical snapshot refresh.
- `components-v2/wiring/__tests__/tx-ptt-gesture-thin-client.test.ts`: browser
  gesture-only timing with no radio-state transition.
