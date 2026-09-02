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

import pytest

from rigplane.backends.yaesu_cat.transport import CatCommandRejected, CatTimeoutError
from rigplane.core.radio_protocol import Radio
from rigplane.core.radio_state import RadioState
from rigplane.core.tx_observation import TxStateReading
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


# ---------------------------------------------------------------------------
# MOR-1941 / MOR-2089: the tx.ptt read-back must not depend on a self-written
# mirror, and the capability check gating it must not depend on a
# hardcoded, per-backend method name (``get_ptt``) either -- it must go
# through the ``TransmitStateReadable`` protocol, checked with
# ``isinstance``, so a backend that implements the protocol under a
# different underlying method gets the real read too.
# ---------------------------------------------------------------------------


def _ptt_only_template() -> MatrixTemplate:
    """Minimal template exercising only ``tx.ptt`` (no tuner methods needed)."""
    return MatrixTemplate(
        radio=RadioTarget(model="FTX-1", profile_id="ftx1"),
        entries=[
            CapabilityDeclarationEntry(
                check_id="tx.ptt",
                capability="tx",
                level=ValidationLevel.STRESS_RECOVERY,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="ptt",
                tx_adjacent=True,
            ),
        ],
    )


def _tx_radio_unwritten_mirror(*, start_power: int = 200):
    """A radio matching the Yaesu backend after MOR-1941: ``set_ptt`` does
    NOT self-write ``radio_state.ptt`` -- only a real read-back
    (``read_transmit_state``, the ``TransmitStateReadable`` protocol member,
    MOR-1914) tells the truth. Proves the hardware TX-actuate check
    (``_actuate_tx_ptt``) is driven off a real read, not the mirror.
    """
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"tx"}
    state = RadioState()
    radio.radio_state = state

    wire_ptt = {"value": False}
    power = {"value": start_power}

    async def _set_ptt(on: bool) -> None:
        wire_ptt["value"] = bool(on)
        # Deliberately NOT touching state.ptt -- matches the shipped
        # Yaesu backend once its self-write is deleted (MOR-1941).

    async def _read_transmit_state() -> TxStateReading:
        # Deliberately does NOT touch ``state.ptt`` either, matching the
        # shipped ``YaesuCatRadio.read_transmit_state``: the mirror stays
        # stale, so a PASS here can only come from this read.
        return TxStateReading(
            value=wire_ptt["value"],
            source="yaesu_poll_response",
            verified_readback=True,
        )

    async def _get_rf_power() -> int:
        return power["value"]

    async def _set_rf_power(level: int) -> None:
        power["value"] = int(level)

    radio.set_ptt = AsyncMock(side_effect=_set_ptt)
    radio.read_transmit_state = AsyncMock(side_effect=_read_transmit_state)
    radio.get_rf_power = AsyncMock(side_effect=_get_rf_power)
    radio.set_rf_power = AsyncMock(side_effect=_set_rf_power)
    return radio, power


async def test_tx_ptt_readback_uses_read_transmit_state_not_the_unwritten_mirror():
    """MOR-1941 + MOR-2089: after a backend's ``set_ptt`` self-write is
    deleted (the Yaesu case), ``radio_state.ptt`` is no longer radio truth.
    The TX-actuate check must verify the key from a real read-back
    (``read_transmit_state``, gated on ``isinstance(radio,
    TransmitStateReadable)``) when the backend implements the protocol --
    ``set_ptt`` never writes the mirror on this fake, matching the shipped
    Yaesu backend post-deletion, so a PASS here can only come from the
    ``read_transmit_state`` read.
    """
    radio, _ = _tx_radio_unwritten_mirror()
    prompter, _ = _confirm_prompter(True)

    levels = await execute_hardware_checks(
        radio,
        _ptt_only_template(),
        _FULL_SAFETY,
        allow_writes=True,
        tx_actuate=True,
        prompter=prompter,
    )
    ptt = _flatten(levels)["tx.ptt"]

    assert ptt.status is CheckStatus.PASS
    assert ptt.evidence["keyed"] is True
    assert ptt.evidence["ptt_state"] is True
    assert ptt.evidence["ptt_state_source"] == "readback"
    assert "ptt_read_unavailable" not in ptt.evidence


@pytest.mark.parametrize("exc_cls", [CatTimeoutError, CatCommandRejected])
@pytest.mark.parametrize(
    "radio_factory",
    [
        pytest.param(lambda: _tx_radio(start_power=200), id="mirror-writing"),
        pytest.param(_tx_radio_unwritten_mirror, id="unwritten-mirror-yaesu"),
    ],
)
async def test_tx_ptt_readback_failure_falls_back_to_a_trusted_write_not_a_fail(
    radio_factory, exc_cls
):
    """MOR-1941 review (BLOCKED-1, then BLOCKED-3), mechanism updated by
    MOR-2089: a ``read_transmit_state`` read-back that raises must not turn
    a good key into a FAIL, on EITHER backend shape.

    The first version of this pin (BLOCKED-1) covered only the
    mirror-writing shape, where the fallback could not actually fail: the
    mirror was already ``True`` from ``set_ptt``'s own write, so falling
    back to it landed on the right answer whether the fallback logic was
    correct or not. Review caught that the *unwritten-mirror* shape --
    the real FTX-1, and the entire reason this repoint exists -- still
    FAILed a good key: falling back to a permanently-stale mirror reports
    a fabricated ``keyed: false``, exactly the MOR-1900-class defect §3.7
    exists to eliminate, and worse than the pre-fix crash because nothing
    records that the read even failed.

    Both fixtures are parametrized here so this exact case is covered,
    not just the shape where the bug cannot occur. Since the fix falls
    back to *trusting the accepted write* rather than to the mirror, both
    shapes converge on the same evidence: what changed is the mechanism,
    not the outcome -- which is exactly why the mirror-writing shape
    alone could not have caught the regression.

    ``exc_cls`` is not a claim that Yaesu's ``read_transmit_state`` would
    raise it -- it doesn't. ``CatTimeoutError`` and
    ``CatCommandRejected`` are used here only as two real exception types
    that do not subclass ``validation.hardware._RESTORE_ERRORS``, standing
    in for whatever ``read_transmit_state`` raises, so this pins that
    ``_actuate_tx_ptt``'s generic ``except Exception`` treats it the same
    way regardless of type or backend. ``_tx_radio`` (the mirror-writing
    shape) does not define ``read_transmit_state`` by default -- this test
    adds it, the same way it used to add ``get_ptt``.
    """
    radio, _ = radio_factory()

    async def _raise_read_transmit_state() -> TxStateReading:
        raise exc_cls("no usable TX; reply")

    radio.read_transmit_state = AsyncMock(side_effect=_raise_read_transmit_state)
    prompter, _ = _confirm_prompter(True)

    levels = await execute_hardware_checks(
        radio,
        _ptt_only_template(),
        _FULL_SAFETY,
        allow_writes=True,
        tx_actuate=True,
        prompter=prompter,
    )
    ptt = _flatten(levels)["tx.ptt"]

    assert ptt.status is CheckStatus.PASS
    assert ptt.evidence["keyed"] is True
    assert ptt.evidence["ptt_state"] is True
    assert ptt.evidence["ptt_state_source"] == "unverified-write"
    # The read failure is never silent -- it must be legible in the
    # artifact, matching the file's other best-effort handlers.
    assert ptt.evidence["ptt_read_error"] == "no usable TX; reply"
    assert "actuate_error" not in ptt.evidence


@pytest.mark.parametrize(
    "radio_factory",
    [
        pytest.param(lambda: _tx_radio(start_power=200), id="mirror-writing"),
        pytest.param(_tx_radio_unwritten_mirror, id="unwritten-mirror-yaesu"),
    ],
)
async def test_tx_ptt_readback_with_no_value_falls_back_to_a_trusted_write_not_a_fail(
    radio_factory,
):
    """MOR-2089: ``TransmitStateReadable.read_transmit_state`` is documented
    (``core/radio_protocol.py``) to answer a failed or unverifiable read by
    returning normally with ``TxStateReading(value=None, failure=...)``
    rather than raising -- all three shipped backends do exactly this for
    an ordinary wire-level failure. That path must fall back to trusting the
    accepted write exactly
    like the raising path above -- otherwise ``bool(None)`` would silently
    become a fabricated ``keyed: False`` mislabeled
    ``ptt_state_source: "readback"``, the same MOR-1900-class defect this
    file already guards against for a raised exception.
    """
    radio, _ = radio_factory()

    async def _no_value_read_transmit_state() -> TxStateReading:
        return TxStateReading(value=None, failure="timeout")

    radio.read_transmit_state = AsyncMock(side_effect=_no_value_read_transmit_state)
    prompter, _ = _confirm_prompter(True)

    levels = await execute_hardware_checks(
        radio,
        _ptt_only_template(),
        _FULL_SAFETY,
        allow_writes=True,
        tx_actuate=True,
        prompter=prompter,
    )
    ptt = _flatten(levels)["tx.ptt"]

    assert ptt.status is CheckStatus.PASS
    assert ptt.evidence["keyed"] is True
    assert ptt.evidence["ptt_state"] is True
    assert ptt.evidence["ptt_state_source"] == "unverified-write"
    assert ptt.evidence["ptt_read_error"] == "timeout"


async def test_tx_ptt_readback_falls_back_to_the_mirror_when_transmit_state_readable_is_absent():
    """Best-effort, other direction (MOR-1941), legibility fixed by
    MOR-2089: a backend that does not implement ``TransmitStateReadable``
    must keep reading the mirror exactly as before -- ``_tx_radio`` defines
    neither ``get_ptt`` nor ``read_transmit_state``. This is no longer a
    stand-in for a real Icom radio specifically: ``runtime.radio.CoreRadio``
    has implemented ``TransmitStateReadable`` since MOR-1914, and MOR-2089
    is what makes ``_actuate_tx_ptt`` actually consult it, so an actual
    Icom now takes the ``readback`` path exercised above, not this one --
    this fixture instead stands for whatever future backend genuinely
    lacks the capability.

    What MOR-2089 changes here is legibility: before, this fallback was
    silent (no evidence field said why the source was ``"mirror"``); now
    ``ptt_read_unavailable`` says so explicitly, so a consumer does not
    have to already know that ``ptt_state_source == "mirror"`` can mean
    "no read capability" instead of "read the mirror and it happened to be
    right".
    """
    radio, _ = _tx_radio(start_power=200)
    prompter, _ = _confirm_prompter(True)

    levels = await _run(radio, safety=_FULL_SAFETY, tx_actuate=True, prompter=prompter)
    ptt = _flatten(levels)["tx.ptt"]

    assert ptt.status is CheckStatus.PASS
    assert ptt.evidence["keyed"] is True
    assert ptt.evidence["ptt_state"] is True
    assert ptt.evidence["ptt_state_source"] == "mirror"
    assert (
        ptt.evidence["ptt_read_unavailable"]
        == "radio does not implement TransmitStateReadable"
    )
