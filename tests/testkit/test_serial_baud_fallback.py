"""Serial connect at the wrong CI-V baud, through the real backend (MOR-3620).

The bench IC-7300 on ``CI-V USB Baud Rate = Auto`` locked to the first baud it
was contacted at (19200) and was silent at 115200, 9600 and 4800
(``tests/fixtures/ic7300/evidence.json``). ``CivSerialFake`` models that
(``answers_at_baud`` fixed rate, ``locks_to_first_baud`` Auto); the real
``Ic7300SerialRadio`` connects through the production ``SerialCivLink`` and
identity gate, only the answer window is compressed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable
from typing import Any

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.cli import _print_serial_identity_no_response
from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.exceptions import CommandRejectedError
from rigplane.exceptions import ConnectionError as RigplaneConnectionError

from rigplane_testkit.serial import CivSerialFake
from testkit.ic7300_fakes import FIXTURES, ic7300_usb_fake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened
_PROFILE_BAUD = 115200  # rigs/ic7300.toml default, what a saved profile uses
_SWEEP = [19200, 9600, 4800]  # discovery order, minus the profile baud
_EVIDENCE = json.loads((FIXTURES / "evidence.json").read_text())["baud"]
_RECORDED_BAUD = _EVIDENCE["answers_at"][0]  # 19200: where the radio answered
_NO_BAUD = 57600  # none of the documented bauds: the radio never answers
assert _RECORDED_BAUD == _SWEEP[0] and _PROFILE_BAUD in _EVIDENCE["silent_at"]


def _link(fake: CivSerialFake) -> SerialCivLink:
    """A link whose opener opens the fake at the baud the link currently has."""

    def opener() -> Awaitable[tuple[Any, Any]]:
        return fake.open_serial_connection(baudrate=link._baudrate)

    link = SerialCivLink(
        device=_DEVICE, baudrate=_PROFILE_BAUD, open_serial_connection=opener
    )
    return link


def _radio(fake: CivSerialFake) -> Ic7300SerialRadio:
    link = _link(fake)
    radio = Ic7300SerialRadio(
        device=_DEVICE,
        baudrate=_PROFILE_BAUD,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    radio._civ_get_timeout = 0.2  # compressed answer window
    return radio


@contextlib.asynccontextmanager
async def _connected(fake: CivSerialFake) -> AsyncIterator[Ic7300SerialRadio]:
    radio = _radio(fake)
    try:
        await radio.connect()
        yield radio
    finally:
        await radio.disconnect()


@pytest.mark.parametrize("answering", [_RECORDED_BAUD, 9600, 4800])
async def test_silence_at_profile_baud_retries_and_connects_at_answering_baud(
    answering: int,
) -> None:
    fake = ic7300_usb_fake(answers_at_baud=answering)
    async with _connected(fake) as radio:
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


async def test_radio_locked_by_another_program_is_found_at_the_locked_baud() -> None:
    """Auto: a first client at 19200 pins the radio; Core connects at 115200."""
    fake = ic7300_usb_fake(locks_to_first_baud=True)
    _, writer = await fake.opener(url=_DEVICE, baudrate=_RECORDED_BAUD)
    writer.write(bytes.fromhex("fefe94e01900fd"))  # 19 00: the other program
    writer.close()
    async with _connected(fake) as radio:
        assert radio.connected and radio.serial_baudrate == _RECORDED_BAUD
        assert fake.locked_baud == _RECORDED_BAUD  # Core did not move the lock


async def test_reconnect_after_a_radio_restart_goes_back_to_the_profile_baud() -> None:
    """Same radio object: connect (fallback), restart the radio, connect again."""
    fake = ic7300_usb_fake(locks_to_first_baud=True)
    _, writer = await fake.opener(url=_DEVICE, baudrate=_RECORDED_BAUD)
    writer.write(bytes.fromhex("fefe94e01900fd"))
    writer.close()
    async with _connected(fake) as radio:
        assert radio.serial_baudrate == _RECORDED_BAUD
        await radio.disconnect()
        fake.power_cycle()
        await radio.connect()
        assert radio.connected and radio.serial_baudrate == _PROFILE_BAUD
        assert fake.locked_baud == _PROFILE_BAUD and radio.serial_baud_notice is None


async def test_late_reply_from_the_starting_baud_is_not_taken_for_a_new_baud() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_PROFILE_BAUD)
    fake.delay_identity_answer(0.3)  # past the 0.2 s answer window
    async with _connected(fake) as radio:
        assert not radio.connected and radio.serial_baudrate == _PROFILE_BAUD


async def test_a_refused_identity_read_is_not_silence() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_PROFILE_BAUD)
    radio = _radio(fake)

    async def nak(*_args: object, **_kwargs: object) -> None:
        raise CommandRejectedError("NAK")

    radio._execute_civ_raw = nak  # type: ignore[method-assign]
    try:
        await radio.connect()
        assert fake.frame_bauds == () and radio.serial_baudrate == _PROFILE_BAUD
    finally:
        await radio.disconnect()


async def test_answer_at_the_profile_baud_does_not_sweep() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_PROFILE_BAUD)
    async with _connected(fake) as radio:
        assert radio.connected and radio.serial_baud_notice is None
        assert set(fake.frame_bauds) == {_PROFILE_BAUD}


async def test_all_bauds_silent_names_the_cause_and_stays_bounded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = ic7300_usb_fake(answers_at_baud=_NO_BAUD)
    async with _connected(fake) as radio:
        assert not radio.connected
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
        # Bounded: every baud once, the profile baud first, nothing repeated.
        assert fake.frame_bauds == (_PROFILE_BAUD, *_SWEEP)
        # The held link goes back to the profile baud for the background re-read.
        assert radio.serial_baudrate == _PROFILE_BAUD
        assert radio.serial_baud_notice is None
        for fragment in (
            "115200",
            "19200",
            "another program",
            "different speed",
            "switched off",
            "Restart the radio",
            "CI-V USB Baud Rate",
        ):
            assert fragment in (radio.last_error or "")
        _print_serial_identity_no_response(radio)
        assert "another program" in capsys.readouterr().out


async def test_low_baud_override_keeps_the_scope_requirement_in_the_notice() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_RECORDED_BAUD)
    radio = _radio(fake)
    radio._allow_low_baud_scope = True
    try:
        await radio.connect()
        assert "scope needs 115200" in (radio.serial_baud_notice or "")
        assert "override" in (radio.serial_baud_notice or "")
    finally:
        await radio.disconnect()


async def test_low_found_baud_warns_that_scope_needs_115200(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        async with _connected(ic7300_usb_fake(answers_at_baud=_RECORDED_BAUD)) as r:
            notice = r.serial_baud_notice or ""
            assert "scope" in notice and "115200" in notice
            assert any("scope" in rec.getMessage() for rec in caplog.records)
            with pytest.raises(RigplaneConnectionError, match="scope needs 115200"):
                await r.enable_scope()


async def test_wrong_model_at_a_fallback_baud_is_held_without_a_connected_claim() -> (
    None
):
    fake = ic7300_usb_fake(answers_at_baud=_RECORDED_BAUD)
    fake.wrong_model_id()
    async with _connected(fake) as radio:
        assert not radio.connected
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.IDENTITY_MISMATCH
        assert radio.serial_baud_notice is None


async def test_notice_is_dropped_with_the_connection() -> None:
    async with _connected(ic7300_usb_fake(answers_at_baud=_RECORDED_BAUD)) as radio:
        assert radio.serial_baud_notice is not None
    assert radio.serial_baud_notice is None


async def test_retired_attempt_does_not_sweep() -> None:
    """A disconnect during the first read leaves the link to its successor."""
    fake = ic7300_usb_fake(answers_at_baud=_NO_BAUD)
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
    finally:
        await radio.disconnect()


async def test_interrupted_sweep_puts_the_starting_baud_back() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_NO_BAUD)
    radio = _radio(fake)
    task = asyncio.create_task(radio.connect())
    try:
        async with asyncio.timeout(5):  # hang guard only
            while _SWEEP[0] not in fake.frame_bauds:  # the trial query was sent
                await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert radio.serial_baudrate == radio._serial_civ_link._baudrate
        assert radio.serial_baudrate == _PROFILE_BAUD
    finally:
        await radio.disconnect()


async def test_refused_return_reports_the_baud_the_port_really_has() -> None:
    radio = _radio(ic7300_usb_fake(answers_at_baud=_NO_BAUD))
    link = radio._serial_civ_link
    retune = link.set_baudrate
    link.set_baudrate = lambda baud: baud != _PROFILE_BAUD and retune(baud)  # type: ignore[method-assign]
    try:
        await radio.connect()
        assert radio.serial_baudrate == _SWEEP[-1] == link._baudrate
    finally:
        await radio.disconnect()


class _FlakyPort:
    """Caches the rate before it raises, the way pyserial does."""

    def __init__(self, refuse: set[int]) -> None:
        self.baudrate = 115200
        self.refuse = refuse
        self.fail_reset = False

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name == "baudrate" and value in getattr(self, "refuse", ()):
            raise OSError("port refused the baud")

    def reset_input_buffer(self) -> None:
        if self.fail_reset:
            raise OSError("reset failed")


async def test_link_retune_contract() -> None:
    fake = ic7300_usb_fake(answers_at_baud=_PROFILE_BAUD)
    link = _link(fake)
    assert link.set_baudrate(19200) and link._baudrate == 19200  # closed: remembered
    link.set_baudrate(_PROFILE_BAUD)
    await link.connect()
    try:
        link._frames.put_nowait(b"old-rate frame")
        assert link.set_baudrate(19200) and link._baudrate == 19200
        assert link._frames.empty()  # input from the old rate is dropped
        port = link._writer.transport.serial = _FlakyPort({9600})
        port.baudrate = 19200
        assert not link.set_baudrate(9600)  # refused: rolled back to the old rate
        assert port.baudrate == 19200 and link._baudrate == 19200 and link.healthy
        port.fail_reset = True
        assert link.set_baudrate(4800) and link._baudrate == 4800  # best effort
        port.refuse = {1200, 4800}
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
