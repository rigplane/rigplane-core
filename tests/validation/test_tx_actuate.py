"""Tests for ``validate --tx-actuate`` (MOR-666).

Under a full opt-in gate stack plus an explicit interactive ``confirm()`` YES,
the harness ACTUALLY exercises the TX checks: it keys PTT at minimum power for a
brief moment (then unkeys and restores power) and runs a tuner tune-cycle.
Missing ANY gate — or a declined/absent confirm — keeps today's behaviour
(MANUAL_REQUIRED, never actuated). These tests use stateful fake radios; they
never touch real hardware.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from rigplane.core.radio_protocol import Radio
from rigplane.core.radio_state import RadioState
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.interactive import InteractivePrompter
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


def _tx_template() -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="X6200", profile_id="x6200"),
        entries=[
            CapabilityDeclarationEntry(
                check_id="tx.ptt",
                capability="tx",
                level=ValidationLevel.STRESS_RECOVERY,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="ptt",
                tx_adjacent=True,
            ),
            CapabilityDeclarationEntry(
                check_id="tuner.tune",
                capability="tuner",
                level=ValidationLevel.STRESS_RECOVERY,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="tuner tune",
                tx_adjacent=True,
            ),
        ],
    )


def _tx_radio(*, start_power: int = 200):
    """A MagicMock(spec=Radio) whose PTT/power/tuner round-trip via a closure.

    ``set_ptt(True/False)`` mirrors into ``radio_state.ptt`` so the actuating
    handler's readback verification observes the keyed state. RF power and the
    tuner status are likewise stateful.
    """
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"tx", "tuner"}
    state = RadioState()
    radio.radio_state = state

    power = {"value": start_power}

    async def _set_ptt(on: bool) -> None:
        state.ptt = bool(on)

    async def _get_rf_power() -> int:
        return power["value"]

    async def _set_rf_power(level: int) -> None:
        power["value"] = int(level)

    async def _set_tuner_status(value: int) -> None:
        state.tuner_status = int(value)

    async def _get_tuner_status() -> int:
        return state.tuner_status

    radio.set_ptt = AsyncMock(side_effect=_set_ptt)
    radio.get_rf_power = AsyncMock(side_effect=_get_rf_power)
    radio.set_rf_power = AsyncMock(side_effect=_set_rf_power)
    radio.set_tuner_status = AsyncMock(side_effect=_set_tuner_status)
    radio.get_tuner_status = AsyncMock(side_effect=_get_tuner_status)
    return radio, power


def _confirm_prompter(answer: bool):
    """Prompter whose ``confirm()`` returns *answer*, recording prompts seen."""
    seen: list[str] = []

    def _input(prompt: str) -> str:
        seen.append(prompt)
        return "YES" if answer else "no"

    return InteractivePrompter(input_fn=_input, output_fn=lambda _msg: None), seen


async def _run(radio, *, safety, tx_actuate, prompter):
    return await execute_hardware_checks(
        radio,
        _tx_template(),
        safety,
        allow_writes=True,
        tx_actuate=tx_actuate,
        prompter=prompter,
    )


_FULL_SAFETY = OperatorSafetyBlock(tx_allowed=True, tuner_allowed=True)


async def test_full_gate_stack_and_confirm_yes_keys_ptt_and_tunes():
    radio, power = _tx_radio(start_power=200)
    prompter, seen = _confirm_prompter(True)

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=True, prompter=prompter)
    checks = _flatten(levels)

    # The confirm gate was asked exactly once for the whole TX-actuate session.
    assert len(seen) == 1
    assert "YES" in seen[0]

    # tx.ptt actuated: keyed True then False (in order), PASS, evidence recorded.
    ptt = checks["tx.ptt"]
    assert ptt.status is CheckStatus.PASS
    assert ptt.evidence["tx_actuated"] is True
    assert ptt.evidence["keyed"] is True
    assert ptt.evidence["power_restored"] is True
    ptt_calls = [c.args[0] for c in radio.set_ptt.call_args_list]
    assert ptt_calls == [True, False]

    # Power was set to minimum (0) then restored to the original (200).
    power_calls = [c.args[0] for c in radio.set_rf_power.call_args_list]
    assert power_calls[0] == 0
    assert power_calls[-1] == 200
    assert power["value"] == 200

    # Radio is NOT left transmitting.
    assert radio.radio_state.ptt is False

    # tuner.tune actuated: tune-cycle (status 2) triggered, PASS.
    tune = checks["tuner.tune"]
    assert tune.status is CheckStatus.PASS
    assert tune.evidence["tx_actuated"] is True
    tuner_set_calls = [c.args[0] for c in radio.set_tuner_status.call_args_list]
    assert 2 in tuner_set_calls


async def test_refuses_to_transmit_when_min_power_set_fails():
    """If the radio HAS power control but lowering to minimum fails, the handler
    must NOT key the transmitter (harm reduction: never TX at unknown power)."""
    from rigplane.core.exceptions import CommandError

    radio, power = _tx_radio(start_power=200)
    # get_rf_power succeeds, but set_rf_power(min) fails -> can't confirm minimum.
    radio.set_rf_power = AsyncMock(side_effect=CommandError("power set NAK"))
    prompter, seen = _confirm_prompter(True)

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=True, prompter=prompter)
    ptt = _flatten(levels)["tx.ptt"]

    assert ptt.status is CheckStatus.FAIL
    assert "minimum" in (ptt.error or "")
    assert ptt.evidence.get("power_set_to_min") is False
    # Crucially: the transmitter was NEVER keyed.
    radio.set_ptt.assert_not_called()
    assert radio.radio_state.ptt is False


async def test_confirm_declined_no_transmission():
    radio, _ = _tx_radio()
    prompter, seen = _confirm_prompter(False)

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=True, prompter=prompter)
    checks = _flatten(levels)

    assert len(seen) == 1  # confirm asked once
    assert checks["tx.ptt"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["tuner.tune"].status is CheckStatus.MANUAL_REQUIRED
    radio.set_ptt.assert_not_called()
    radio.set_tuner_status.assert_not_called()


async def test_no_tx_actuate_flag_no_transmission():
    radio, _ = _tx_radio()
    prompter, _ = _confirm_prompter(True)

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=False, prompter=prompter)
    checks = _flatten(levels)

    assert checks["tx.ptt"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["tuner.tune"].status is CheckStatus.MANUAL_REQUIRED
    radio.set_ptt.assert_not_called()


async def test_no_prompter_no_transmission():
    """tx_actuate set but no prompter (non-interactive) → never transmit."""
    radio, _ = _tx_radio()

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=True, prompter=None)
    checks = _flatten(levels)

    assert checks["tx.ptt"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["tuner.tune"].status is CheckStatus.MANUAL_REQUIRED
    radio.set_ptt.assert_not_called()


async def test_assume_yes_alone_does_not_authorize_transmission():
    """``--assume-yes`` (assume_yes prompter) must NOT satisfy the confirm gate —
    confirm() always reads a real answer; a 'no' input keeps it MANUAL_REQUIRED."""
    seen: list[str] = []

    def _input(prompt: str) -> str:
        seen.append(prompt)
        return ""  # blank == declined

    prompter = InteractivePrompter(
        input_fn=_input, output_fn=lambda _msg: None, assume_yes=True
    )
    radio, _ = _tx_radio()

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=True, prompter=prompter)
    checks = _flatten(levels)

    # confirm() ignored assume_yes and actually read stdin, which declined.
    assert len(seen) == 1
    assert checks["tx.ptt"].status is CheckStatus.MANUAL_REQUIRED
    radio.set_ptt.assert_not_called()


async def test_unkey_and_restore_even_if_verify_raises():
    """The most important safety test: if the mid-check verify/readback raises
    AFTER keying, the ``finally`` still unkeys PTT and restores power."""
    radio, power = _tx_radio(start_power=200)
    prompter, _ = _confirm_prompter(True)

    state = radio.radio_state
    calls = {"n": 0}

    async def _exploding_set_ptt(on: bool) -> None:
        calls["n"] += 1
        if on:
            # Key succeeds (mirror state) but the post-key verify path explodes.
            state.ptt = True
            raise RuntimeError("injected mid-check failure after keying")
        # Unkey path (the finally) must still run and clear PTT.
        state.ptt = False

    radio.set_ptt = AsyncMock(side_effect=_exploding_set_ptt)

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=True, prompter=prompter)
    checks = _flatten(levels)

    # Whatever the verdict, the radio must NOT be left transmitting and power
    # must be restored.
    assert state.ptt is False
    ptt_calls = [c.args[0] for c in radio.set_ptt.call_args_list]
    assert ptt_calls[0] is True
    assert False in ptt_calls  # finally unkeyed
    assert power["value"] == 200  # power restored
    assert checks["tx.ptt"].status is CheckStatus.FAIL


async def test_tx_allowed_missing_keeps_manual_required():
    """Even with --tx-actuate + confirm, tx.ptt requires tx_allowed authorization;
    without it the pre-gate BLOCKS and nothing transmits."""
    radio, _ = _tx_radio()
    prompter, _ = _confirm_prompter(True)

    # tuner_allowed only — tx.ptt gated solely by tx_allowed remains BLOCKED.
    safety = OperatorSafetyBlock(tx_allowed=False, tuner_allowed=True)
    levels = await _run(radio, safety=safety, tx_actuate=True, prompter=prompter)
    checks = _flatten(levels)

    assert checks["tx.ptt"].status is CheckStatus.BLOCKED
    radio.set_ptt.assert_not_called()
