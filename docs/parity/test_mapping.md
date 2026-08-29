# Parity and Reliability → Test Mapping

This document maps **IC-7610 parity command families** and **reliability integration scenarios** to the test files and (where useful) test cases that cover them. Use it to see where to add or extend tests when updating the [command matrix](ic7610_command_matrix.json) or the reliability checklist.

**Source of truth:** `docs/parity/ic7610_command_matrix.json`. Each matrix entry can list `test_patterns` (e.g. `tests/test_commands.py::test_get_frequency`). The mapping below is derived from those patterns per family.

---

## Parity: Command family → tests

| Family | Title | Owner issue | Test file(s) |
|--------|-------|-------------|--------------|
| `advanced_scope` | Advanced scope controls | #137 | `tests/test_scope.py`, `tests/test_commands.py`, `tests/test_radio.py`, `tests/test_civ_rx_coverage.py` |
| `baseline_core` | Baseline core and already-exposed parity | — | `tests/test_commands.py`, `tests/test_commands_extended.py`, `tests/test_civ_rx_coverage.py`, `tests/test_radio.py`, `tests/test_radio_coverage.py`, `tests/test_rf_gain_af_level.py`, `tests/test_scope.py`, `tests/test_data_mode.py`, `tests/test_cmd29_parsing.py` |
| `dsp_levels` | DSP and level controls | #130 | `tests/test_commands.py`, `tests/test_radio.py`, `tests/test_civ_rx_coverage.py` |
| `memory_bandstack` | Memory and band-stacking | #133 | `tests/test_commands.py` |
| `operator_toggles` | Operator toggles and status flags | #131 | `tests/test_commands.py`, `tests/test_radio.py`, `tests/test_civ_rx_coverage.py` |
| `system_config` | System and configuration | #135 | `tests/test_commands.py`, `tests/test_radio.py` |
| `tone_repeater` | Tone and repeater controls | #134 | `tests/test_commands.py` |
| `transceiver_status` | Transceiver, telemetry, and RIT/TX status | #136 | `tests/test_commands.py`, `tests/test_radio.py`, `tests/test_civ_rx_coverage.py`, `tests/test_radio_poller_coverage.py`, `tests/test_web_server.py` |
| `vfo_dualwatch_scan` | VFO, dual-watch, and scan control | #132 | `tests/test_commands.py`, `tests/test_commands_extended.py`, `tests/test_civ_rx_coverage.py`, `tests/test_radio.py`, `tests/test_vfo_dual_watch.py`, `tests/test_main_sub_tracking.py` |

**Representative test cases (by family):**

- **advanced_scope:** `test_scope.py::test_get_scope_main_sub`, `test_scope.py::test_scope_set_span`, `test_civ_rx_coverage.py::test_update_radio_state_advanced_scope_family`
- **baseline_core:** `test_commands.py::test_get_frequency`, `test_commands.py::test_set_mode_usb`, `test_civ_rx_coverage.py::cmd 0x00`, `test_commands_extended.py::test_ack`
- **dsp_levels:** `test_commands.py::test_cmd29_level_builders`, `test_radio.py::test_get_cmd29_dsp_level`, `test_civ_rx_coverage.py::test_update_radio_state_cmd14_receiver_dsp_levels`
- **memory_bandstack:** `test_commands.py::test_build_memory_mode`, `test_commands.py::test_build_memory_write`, `test_commands.py::test_build_memory_to_vfo`
- **operator_toggles:** `test_commands.py::test_cmd29_operator_getters`, `test_radio.py::test_get_bool_operator_toggle`, `test_civ_rx_coverage.py::test_update_radio_state_operator_toggle_family`
- **system_config:** `test_commands.py::test_get_antenna_1`, `test_commands.py::test_get_system_date`, `test_radio.py::test_get_transceiver_id`
- **tone_repeater:** `test_commands.py::test_get_repeater_tone`, `test_commands.py::test_get_tone_freq`, `test_commands.py::test_get_tsql_freq`
- **transceiver_status:** `test_commands.py::test_get_rit_frequency`, `test_civ_rx_coverage.py::test_update_radio_state_rit_frequency`, `test_radio_poller_coverage.py::test_state_queries_include_transceiver_status_reads_for_ic7610`
- **vfo_dualwatch_scan:** `test_commands_extended.py::test_code_reaches_the_wire_unchanged`, `test_radio_extended.py::test_set_vfo_wire_sends_the_profile_code`, `test_civ_rx_coverage.py::dual_watch`, `test_vfo_dual_watch.py::test_start_scan_builds_correct_frame`, `test_main_sub_tracking.py::TestGetMainSubTracking`

---

## Reliability: scenario → test

Reliability integration tests live in **`tests/integration/test_reliability_matrix_integration.py`**. They require a real radio and are marked `@pytest.mark.integration`. Backlog items 1–13 are covered by the following tests:

| Scenario | Test (class / method) | Description |
|----------|------------------------|-------------|
| 1) Sequence wrap-around | `TestReliabilityMatrix::test_transport_sequence_wraparound` | CI-V transport seq near rollover, no degradation |
| 2) ACK mixed stress | `TestReliabilityMatrix::test_ack_mixed_stress_civ_stats` | Mixed wait/non-wait commands, ACK tracker stats |
| 3) Long-run longevity | `TestReliabilityMatrix::test_longevity_session_stability` | Extended mixed activity, session stability (set `ICOM_LONG_SOAK_SECONDS`) |
| 4) Multi-client contention | `TestReliabilityMatrix::test_multi_client_session_contention` | Two concurrent clients (set `ICOM_ALLOW_SESSION_CONTENTION=1`) |
| 5) radio_ready state transitions | `TestReliabilityMatrix::test_radio_ready_state_transitions` | radio_ready across connect, CI-V drop/recovery, disconnect |

**Run reliability integration (with hardware):**

```bash
PYTHONPATH=src pytest -m integration tests/integration/test_reliability_matrix_integration.py -v
```

---

## Matrix and meta-validation

- **Matrix schema and evidence:** `tests/test_ic7610_parity_matrix.py` checks that the JSON matrix matches the wfview rig reference, status/family counts are consistent, and every implemented/partial entry has runtime symbols and test patterns whose referenced files contain the given pattern.
- **Test file collectability:** `tests/test_ic7610_parity_matrix.py::test_parity_matrix_test_files_are_collectable` (see below) ensures every test file referenced in the matrix can be collected by pytest, so renamed or removed test files break the gate.

When adding a new command or family to the matrix, add at least one `test_patterns` entry pointing to an existing (or new) test; then run:

```bash
PYTHONPATH=src pytest tests/test_ic7610_parity_matrix.py -q
```
