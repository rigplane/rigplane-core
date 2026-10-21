"""Serial connect at the wrong CI-V baud, through the real backend (MOR-3620).

The bench IC-7300 with ``CI-V USB Baud Rate = Auto`` locked to the first baud
it was contacted at (19200) and was silent at 115200, 9600 and 4800 until the
owner fixed the rate by hand: ``tests/fixtures/ic7300/evidence.json`` records
it, and ``rigplane_testkit`` models it (``CivSerialFake(answers_at_baud=...)``
for a fixed rate, ``locks_to_first_baud=True`` for Auto). The real
``Ic7300SerialRadio`` connects through the production ``SerialCivLink`` and
the identity gate; only the answer window is compressed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from pathlib import Path

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.cli import _print_serial_identity_no_response
from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.exceptions import ConnectionError as RigplaneConnectionError

from rigplane_testkit.serial import CivSerialFake
from testkit.ic7300_fakes import FIXTURES, ic7300_usb_fake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened
_PROFILE_BAUD = 115200  # rigs/ic7300.toml default, what a saved profile uses
_SWEEP = [19200, 9600, 4800]  # discovery order, minus the profile baud
_EVIDENCE = json.loads((Path(FIXTURES) / "evidence.json").read_text())["baud"]
_RECORDED_BAUD = _EVIDENCE["answers_at"][0]  # 19200: where the radio answered


def _radio(fake: CivSerialFake, baud: int = _PROFILE_BAUD) -> Ic7300SerialRadio:
    link = SerialCivLink(
        device=_DEVICE,
        baudrate=baud,
        open_serial_connection=partial(fake.open_serial_connection, baudrate=baud),
    )
    radio = Ic7300SerialRadio(
        device=_DEVICE,
        baudrate=baud,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    radio._civ_get_timeout = 0.2  # compressed answer window
    return radio


async def test_recorded_evidence_is_the_wrong_baud_silence() -> None:
    """The scenario below is the recorded one, not an invented one."""
    assert _RECORDED_BAUD in _SWEEP
    assert _PROFILE_BAUD in _EVIDENCE["silent_at"]


@pytest.mark.parametrize("answering", [_RECORDED_BAUD, 9600, 4800])
async def test_silence_at_profile_baud_retries_and_connects_at_answering_baud(
    answering: int,
) -> None:
    fake = ic7300_usb_fake(answers_at_baud=answering)
    radio = _radio(fake)
    try:
        await radio.connect()

        assert radio.connected
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.VERIFIED
        assert radio.serial_baudrate == answering
        # The profile baud once, then each other documented baud once, in
        # discovery order, stopping at the first one that answers.
        tried = [_PROFILE_BAUD, *_SWEEP[: _SWEEP.index(answering) + 1]]
        assert list(dict.fromkeys(fake.frame_bauds)) == tried
        assert radio.serial_baud_notice is not None
        assert str(answering) in radio.serial_baud_notice
        assert str(_PROFILE_BAUD) in radio.serial_baud_notice
    finally:
        await radio.disconnect()


async def test_radio_locked_by_another_program_is_found_at_the_locked_baud() -> None:
    """Auto: a first client at 19200 pins the radio; Core connects at 115200."""
    fake = ic7300_usb_fake(locks_to_first_baud=True)
    _, writer = await fake.opener(url=_DEVICE, baudrate=_RECORDED_BAUD)
    writer.write(bytes.fromhex("fefe94e01900fd"))  # 19 00: the other program
    writer.close()
    assert fake.locked_baud == _RECORDED_BAUD

    radio = _radio(fake)
    try:
        await radio.connect()

        assert radio.connected
        assert radio.serial_baudrate == _RECORDED_BAUD
        assert fake.locked_baud == _RECORDED_BAUD  # Core did not move the lock
    finally:
        await radio.disconnect()


async def test_answer_at_the_profile_baud_does_not_sweep() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_PROFILE_BAUD)
    radio = _radio(fake)
    try:
        await radio.connect()

        assert radio.connected
        assert set(fake.frame_bauds) == {_PROFILE_BAUD}
        assert radio.serial_baudrate == _PROFILE_BAUD
        assert radio.serial_baud_notice is None
    finally:
        await radio.disconnect()


async def test_all_bauds_silent_names_the_cause_and_stays_bounded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = ic7300_usb_fake(answers_at_baud=57600)  # no documented baud
    radio = _radio(fake)
    try:
        await radio.connect()

        assert not radio.connected
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
        # Bounded: every baud once, the profile baud first, nothing repeated.
        assert list(dict.fromkeys(fake.frame_bauds)) == [_PROFILE_BAUD, *_SWEEP]
        assert fake.frame_bauds[: 1 + len(_SWEEP)] == (_PROFILE_BAUD, *_SWEEP)
        # The held link goes back to the profile baud for the background re-read.
        assert radio.serial_baudrate == _PROFILE_BAUD
        assert radio.serial_baud_notice is None
        message = radio.last_error
        assert message is not None
        for fragment in (
            "115200",
            "19200",
            "another program",
            "different speed",
            "switched off",
            "Restart the radio",
            "CI-V USB Baud Rate",
        ):
            assert fragment in message
        _print_serial_identity_no_response(radio)
        assert "another program" in capsys.readouterr().out
    finally:
        await radio.disconnect()


async def test_low_found_baud_warns_that_scope_needs_115200(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = ic7300_usb_fake(answers_at_baud=_RECORDED_BAUD)
    radio = _radio(fake)
    try:
        with caplog.at_level(logging.WARNING):
            await radio.connect()

        notice = radio.serial_baud_notice
        assert notice is not None
        assert "scope" in notice and "115200" in notice
        assert "CI-V USB Baud Rate" in notice
        assert any(
            record.levelno >= logging.WARNING and "scope" in record.getMessage()
            for record in caplog.records
        )
        with pytest.raises(RigplaneConnectionError) as refused:
            await radio.enable_scope()
        text = str(refused.value)
        assert "scope needs 115200" in text and "CI-V USB Baud Rate" in text
    finally:
        await radio.disconnect()


class _FlakyPort:
    """A port that takes a rate and then raises, or refuses some rates."""

    def __init__(self, refuse: set[int], fail_reset: bool = False) -> None:
        self._baud = 115200
        self._refuse = refuse
        self._fail_reset = fail_reset

    @property
    def baudrate(self) -> int:
        return self._baud

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        if value in self._refuse:
            self._baud = value  # pyserial caches the rate before reconfiguring
            raise OSError("port refused the baud")
        self._baud = value

    def reset_input_buffer(self) -> None:
        if self._fail_reset:
            raise OSError("reset failed")


async def test_link_retune_contract() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_PROFILE_BAUD)
    link = SerialCivLink(
        device=_DEVICE,
        open_serial_connection=partial(fake.open_serial_connection, baudrate=115200),
    )
    assert not link.set_baudrate(19200)  # closed: nothing to retune
    await link.connect()
    try:
        link._frames.put_nowait(b"old-rate frame")
        assert link.set_baudrate(19200) and link._baudrate == 19200
        assert link._frames.empty()  # input from the old rate is dropped
        port = link._writer.transport.serial = _FlakyPort({9600})
        port.baudrate = 19200
        assert not link.set_baudrate(9600)  # refused: rolled back to the old rate
        assert port.baudrate == 19200 and link._baudrate == 19200 and link.healthy
        port._fail_reset = True
        assert link.set_baudrate(4800) and link._baudrate == 4800  # best effort
        port._refuse = {1200, 4800}
        assert not link.set_baudrate(1200) and not link.healthy  # no rollback
    finally:
        await link.disconnect()
    plain = SerialCivLink(
        device=_DEVICE, open_serial_connection=CivSerialFake().open_serial_connection
    )
    await plain.connect()
    try:
        assert not plain.set_baudrate(19200)  # no pyserial object behind it
    finally:
        await plain.disconnect()


async def test_wrong_model_at_a_fallback_baud_is_held_without_a_connected_claim() -> (
    None
):
    fake = ic7300_usb_fake(answers_at_baud=_RECORDED_BAUD)
    fake.wrong_model_id()
    radio = _radio(fake)
    try:
        await radio.connect()

        assert not radio.connected
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.IDENTITY_MISMATCH
        assert radio.serial_baud_notice is None
    finally:
        await radio.disconnect()


async def test_notice_is_dropped_with_the_connection() -> None:
    radio = _radio(ic7300_usb_fake(answers_at_baud=_RECORDED_BAUD))
    await radio.connect()
    assert radio.serial_baud_notice is not None
    await radio.disconnect()
    assert radio.serial_baud_notice is None


async def test_retired_attempt_does_not_sweep() -> None:
    """A disconnect during the first read leaves the link to its successor."""
    fake = ic7300_usb_fake(answers_at_baud=57600)
    radio = _radio(fake)
    first_read = radio._read_serial_identity_payload

    async def read_then_retire() -> bytes | None:
        result = await first_read()
        radio._owner_stop_epoch += 1  # what disconnect() does first
        return result

    radio._read_serial_identity_payload = read_then_retire  # type: ignore[method-assign]
    try:
        await radio.connect()
        assert set(fake.frame_bauds) == {_PROFILE_BAUD}  # no retune, no query
        assert radio.serial_baudrate == _PROFILE_BAUD
    finally:
        await radio.disconnect()


async def test_interrupted_sweep_puts_the_starting_baud_back() -> None:
    fake = ic7300_usb_fake(answers_at_baud=57600)
    radio = _radio(fake)
    try:
        # 0.2 s first read, then the cancel lands inside the 19200 trial read.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(radio.connect(), timeout=0.3)
        assert _SWEEP[0] in fake.frame_bauds  # the trial query was sent
        assert radio.serial_baudrate == _PROFILE_BAUD
        assert radio._serial_civ_link._baudrate == _PROFILE_BAUD
    finally:
        await radio.disconnect()


async def test_refused_return_reports_the_baud_the_port_really_has() -> None:
    radio = _radio(ic7300_usb_fake(answers_at_baud=57600))
    link = radio._serial_civ_link
    retune = link.set_baudrate
    link.set_baudrate = lambda baud: baud != _PROFILE_BAUD and retune(baud)  # type: ignore[method-assign]
    try:
        await radio.connect()
        assert radio.serial_baudrate == _SWEEP[-1] == link._baudrate
    finally:
        await radio.disconnect()
