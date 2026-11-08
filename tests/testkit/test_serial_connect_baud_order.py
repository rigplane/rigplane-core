"""A serial connect contacts the radio at its profile baud first (MOR-4207).

An Icom radio on ``CI-V USB Baud Rate = Auto`` keeps the baud of the first
access to its port until it is restarted (``tests/fixtures/ic7300/evidence.json``,
MOR-3620). Setups saved by the old discovery pass ``--serial-baud 19200`` on
every launch, so the connect locked the radio at 19200 and the scope stayed off
(minimum 115200). The connect now follows the rule discovery uses: the first
open and the first byte go out at the radio's profile ``default_baud``; the
baud passed in is a hint, tried right after it; then the protocol's other
rates (``discovery.serial_connect_baud_rates``).

Every test enters through the CLI options Pro passes to Core
(``--model``/``--serial-port``/``--serial-baud``), ``_build_backend_config``
and the backend factory. Only the OS open step is replaced: the Icom radios are
the testkit ``CivSerialFake`` with its baud modelling, the FTX-1 is the testkit
``FakeCatRadio`` behind a port that hears one fixed baud. No real port is
opened; the answer windows are compressed.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.backends import discovery
from rigplane.backends._icom_serial_base import low_baud_scope_guidance
from rigplane.backends.factory import create_radio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.core import serial_open
from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.exceptions import CommandError
from rigplane.exceptions import ConnectionError as RigplaneConnectionError
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.radios import SERIAL_RADIO_MAP
from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.yaesu.cat_fake import FakeCatRadio

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened
_SCOPE_BAUD = 115200  # rigs/ic*.toml default_baud: the serial scope minimum
_SAVED_BAUD = 19200  # what a route saved by the old discovery carries
_ANSWER_WINDOW_S = 0.2  # compressed CI-V answer window (production: 2.0 s)
_ICOM_MODELS = ["IC-705", "IC-7300", "IC-7610", "IC-9700"]


def _icom_fake(model: str, **kw: Any) -> CivSerialFake:
    """The radio answering ``19 00`` with its profile's expected identity.

    A profile without ``[identity].expected_ids`` (IC-705, IC-9700) connects
    ``unverified`` on any answer; it answers its discovery model ID.
    """
    if resolve_radio_profile(model=model).expected_identity_ids:
        return CivSerialFake(model=model, **kw)
    model_id = {name: mid for name, mid in SERIAL_RADIO_MAP.values()}[model]
    return CivSerialFake(model=model, model_id=model_id, **kw)


def _record_opens(
    monkeypatch: pytest.MonkeyPatch, opener: Callable[..., Any]
) -> list[int]:
    """Replace the OS open step of ``open_serial_port``; return the open bauds."""
    opens: list[int] = []

    async def recording_opener(*, url: str, baudrate: int, **kw: Any) -> Any:
        opens.append(baudrate)
        return await opener(url=url, baudrate=baudrate, **kw)

    monkeypatch.setattr(serial_open, "_open_with_idle_lines", recording_opener)
    return opens


async def _station_radio(model: str, serial_baud: int) -> Any:
    """The radio Core builds from the options Pro passes to ``station``."""
    args = _build_parser().parse_args(
        [
            "--model",
            model,
            "--serial-port",
            _DEVICE,
            "--serial-baud",
            str(serial_baud),
            "status",
        ]
    )
    radio = create_radio(await _build_backend_config(args))
    if not isinstance(radio, YaesuCatRadio):
        radio._civ_get_timeout = _ANSWER_WINDOW_S
        radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    return radio


@contextlib.asynccontextmanager
async def _connected(radio: Any) -> AsyncIterator[Any]:
    try:
        await asyncio.wait_for(radio.connect(), 30)
        yield radio
    finally:
        await radio.disconnect()


# ---------------------------------------------------------------------------
# The order is one rule, from profile data
# ---------------------------------------------------------------------------


def test_connect_order_is_profile_baud_then_hint_then_the_probe_order() -> None:
    assert discovery.serial_connect_baud_rates("civ", 115200, 19200) == (
        115200,
        19200,
        9600,
        4800,
    )
    assert discovery.serial_connect_baud_rates("civ", 115200, 115200) == (
        115200,
        19200,
        9600,
        4800,
    )
    # A hint outside the documented rates is still tried, right after the profile.
    assert discovery.serial_connect_baud_rates("civ", 115200, 57600)[:2] == (
        115200,
        57600,
    )
    assert discovery.serial_connect_baud_rates("yaesu_cat", 38400, 9600) == (
        38400,
        9600,
        115200,
    )


def test_xiegu_order_at_its_profile_baud_is_the_old_sweep() -> None:
    """X6200 (default_baud 19200) given 19200: the same order as before MOR-4207."""
    before = (
        19200,
        *(b for b in discovery.serial_probe_baud_rates("civ") if b != 19200),
    )
    assert discovery.serial_connect_baud_rates("civ", 19200, 19200) == before


# ---------------------------------------------------------------------------
# Icom CI-V
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", _ICOM_MODELS)
async def test_auto_radio_given_the_saved_19200_locks_and_connects_at_the_profile_baud(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _icom_fake(model, locks_to_first_baud=True)
    opens = _record_opens(monkeypatch, fake.opener)
    radio = await _station_radio(model, _SAVED_BAUD)
    async with _connected(radio):
        assert opens[0] == _SCOPE_BAUD  # the first port open
        assert fake.frame_bauds[0] == _SCOPE_BAUD  # the first byte
        assert fake.locked_baud == _SCOPE_BAUD
        assert radio.connected
        assert radio.serial_baudrate == _SCOPE_BAUD  # the effective rate
        assert radio.connection_identity.status in (
            RadioIdentityStatus.VERIFIED,
            RadioIdentityStatus.UNVERIFIED,
        )
        # Reported the existing way: the rate the radio answered at.
        assert radio.serial_baud_notice is not None
        assert str(_SCOPE_BAUD) in radio.serial_baud_notice
        # The scope baud guardrail lets the scope through.
        radio._ensure_scope_baud_guardrail()


@pytest.mark.parametrize("model", _ICOM_MODELS)
async def test_radio_fixed_at_19200_connects_there_after_one_miss_with_scope_guidance(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _icom_fake(model, answers_at_baud=_SAVED_BAUD)
    opens = _record_opens(monkeypatch, fake.opener)
    radio = await _station_radio(model, _SAVED_BAUD)
    async with _connected(radio):
        assert radio.connected
        assert radio.serial_baudrate == _SAVED_BAUD
        # One open at the profile baud, one miss there, then the hint answers
        # on the same open link (retuned in place, never reopened).
        assert opens == [_SCOPE_BAUD]
        assert list(dict.fromkeys(fake.frame_bauds)) == [_SCOPE_BAUD, _SAVED_BAUD]
        assert radio.serial_baud_notice is None  # it runs at the given baud
        with pytest.raises(
            (RigplaneConnectionError, CommandError),
            match=re.escape(low_baud_scope_guidance()),
        ):
            await radio.enable_scope()


@pytest.mark.parametrize("model", _ICOM_MODELS)
async def test_hint_at_the_profile_baud_is_one_open_and_no_sweep(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _icom_fake(model, answers_at_baud=_SCOPE_BAUD)
    opens = _record_opens(monkeypatch, fake.opener)
    radio = await _station_radio(model, _SCOPE_BAUD)
    async with _connected(radio):
        assert radio.connected and radio.serial_baudrate == _SCOPE_BAUD
        assert opens == [_SCOPE_BAUD]
        assert set(fake.frame_bauds) == {_SCOPE_BAUD}
        assert radio.serial_baud_notice is None


async def test_xiegu_at_its_profile_baud_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = CivSerialFake(model="X6200", model_id=b"\x00\x01", answers_at_baud=19200)
    opens = _record_opens(monkeypatch, fake.opener)
    radio = await _station_radio("X6200", 19200)
    async with _connected(radio):
        assert radio.connected and radio.serial_baudrate == 19200
        assert opens == [19200]
        assert set(fake.frame_bauds) == {19200}
        assert radio.serial_baud_notice is None


# ---------------------------------------------------------------------------
# Yaesu CAT (no auto-baud: the profile-first attempt is one short miss)
# ---------------------------------------------------------------------------


class _FixedBaudPort:
    """The pyserial object of a port whose radio hears one baud only."""

    def __init__(self, baudrate: int) -> None:
        self.baudrate = baudrate
        self.dtr = False
        self.rts = False


class _FixedBaudWriter:
    """A writer to ``FakeCatRadio`` that delivers bytes at the radio's baud only.

    The fake is a TCP server, which has no baud: this stands in for the line,
    where bytes sent at another rate are noise the radio does not answer.
    """

    def __init__(self, writer: asyncio.StreamWriter, port: _FixedBaudPort, hears: int):
        self._writer = writer
        self._hears = hears
        self.transport = SimpleNamespace(serial=port)
        self.bauds_sent: list[int] = []

    def write(self, data: bytes) -> None:
        baud = self.transport.serial.baudrate
        self.bauds_sent.append(baud)
        if baud == self._hears:
            self._writer.write(data)

    async def drain(self) -> None:
        await self._writer.drain()

    def close(self) -> None:
        self._writer.close()

    async def wait_closed(self) -> None:
        await self._writer.wait_closed()


@contextlib.asynccontextmanager
async def _ftx1_fixed_at(
    hears: int, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[list[int], list[_FixedBaudWriter]]]:
    fake = FakeCatRadio()
    await fake.start()
    writers: list[_FixedBaudWriter] = []

    async def opener(*, url: str, baudrate: int, **_kw: Any) -> Any:
        reader, writer = await asyncio.open_connection("127.0.0.1", fake.port)
        wrapped = _FixedBaudWriter(writer, _FixedBaudPort(baudrate), hears)
        writers.append(wrapped)
        return reader, wrapped

    opens = _record_opens(monkeypatch, opener)
    try:
        yield opens, writers
    finally:
        await fake.stop()


async def test_ftx1_given_9600_on_a_radio_fixed_at_9600_connects_at_9600(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _ftx1_fixed_at(9600, monkeypatch) as (opens, writers):
        radio = await _station_radio("FTX-1", 9600)
        radio._transport._timeout = _ANSWER_WINDOW_S
        assert isinstance(radio, YaesuCatRadio)
        async with _connected(radio):
            assert radio.connected
            assert radio.connection_identity.status is RadioIdentityStatus.VERIFIED
            assert radio.serial_baudrate == 9600
            # The profile baud first (one miss), then the hint, on one open.
            assert opens == [38400]
            [writer] = writers
            assert list(dict.fromkeys(writer.bauds_sent)) == [38400, 9600]


async def test_ftx1_given_its_profile_baud_is_one_open_and_no_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _ftx1_fixed_at(38400, monkeypatch) as (opens, writers):
        radio = await _station_radio("FTX-1", 38400)
        assert isinstance(radio, YaesuCatRadio)
        async with _connected(radio):
            assert radio.connected and radio.serial_baudrate == 38400
            assert opens == [38400]
            [writer] = writers
            assert set(writer.bauds_sent) == {38400}


async def test_silent_ftx1_goes_back_to_the_profile_baud_for_the_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _ftx1_fixed_at(4800, monkeypatch) as (opens, writers):
        radio = await _station_radio("FTX-1", 9600)
        radio._transport._timeout = _ANSWER_WINDOW_S
        async with _connected(radio):
            assert not radio.connected
            assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
            assert opens == [38400]
            [writer] = writers
            assert list(dict.fromkeys(writer.bauds_sent)) == [38400, 9600, 115200]
            assert radio.serial_baudrate == 38400
