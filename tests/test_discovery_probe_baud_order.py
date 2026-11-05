"""Serial discovery probes the profile baud first (MOR-4207).

An Icom radio on ``CI-V USB Baud Rate = Auto`` keeps the baud of the first
access to its port until it is restarted (``tests/fixtures/ic7300/evidence.json``,
MOR-3620). Discovery used to sweep 19200 first, so the radio locked there, the
setup saved 19200 and the scope stayed off ("Scope unavailable at 19200 baud
(minimum 115200)"). The probe order now comes from the profiles'
``default_baud`` data, highest first, then the protocol's other documented
rates; the answering rate is what discovery reports, with a typed low-baud
flag when it is below the identified profile's baud.

The radios are the testkit ``CivSerialFake`` (Icom CI-V, with its baud
modelling) and a Yaesu CAT transport fake; no real port is opened.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from rigplane import discovery, profiles
from rigplane.discovery import (
    RadioDiscoveryResult,
    discover_serial_radios,
    probe_serial_civ,
    probe_serial_yaesu_cat,
    probe_xiegu_model_id,
)
from rigplane.radios import SERIAL_RADIO_MAP
from rigplane_testkit.serial import CivSerialFake

_DEVICE = "/dev/ttyUSB0"  # synthetic node name; the fakes never open it
_SCOPE_BAUD = 115200  # rigs/ic*.toml default_baud: the serial scope minimum
# CI-V radios that answer the 19 00 identity read at their own CI-V address.
_ICOM_MODELS = [
    pytest.param(0xA4, id="IC-705"),
    pytest.param(0x94, id="IC-7300"),
    pytest.param(0x98, id="IC-7610"),
    pytest.param(0xA2, id="IC-9700"),
]


def _port() -> SimpleNamespace:
    """A pyserial ``comports()`` row that passes the USB candidate filter."""
    return SimpleNamespace(
        device=_DEVICE,
        description="USB Serial",
        hwid="USB VID:PID=10C4:EA60",
    )


def _icom_fake(civ_addr: int, **kw: Any) -> CivSerialFake:
    model, model_id = SERIAL_RADIO_MAP[civ_addr]
    return CivSerialFake(model=model, model_id=model_id, **kw)


@contextmanager
def _one_port(timeout: float = 0.05) -> Iterator[None]:
    """One candidate port and a short per-baud answer window.

    The baud order is left at its default: it is what these tests are about.
    """
    with (
        patch("serial.tools.list_ports.comports", return_value=[_port()]),
        patch(
            "rigplane.discovery.probe_serial_civ",
            partial(probe_serial_civ, timeout=timeout),
        ),
        patch(
            "rigplane.discovery.probe_serial_yaesu_cat",
            partial(probe_serial_yaesu_cat, timeout=timeout),
        ),
        patch(
            "rigplane.discovery.probe_xiegu_model_id",
            partial(probe_xiegu_model_id, timeout=timeout),
        ),
    ):
        yield


async def _discover_one(**kw: Any) -> RadioDiscoveryResult:
    results = await asyncio.wait_for(discover_serial_radios(**kw), 30)
    assert len(results) == 1, results
    return results[0]


# ---------------------------------------------------------------------------
# Icom CI-V: the first access is at the scope baud
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("civ_addr", _ICOM_MODELS)
async def test_auto_baud_icom_locks_to_and_is_saved_at_the_scope_baud(
    civ_addr: int,
) -> None:
    fake = _icom_fake(civ_addr, locks_to_first_baud=True)
    with _one_port():
        result = await _discover_one(_open_serial=fake.opener)

    assert result.protocol == "civ"
    assert result.model == SERIAL_RADIO_MAP[civ_addr][0]
    assert result.baudrate == _SCOPE_BAUD
    assert fake.locked_baud == _SCOPE_BAUD
    assert result.preferred_baudrate == _SCOPE_BAUD
    assert result.low_baud is False
    assert result.low_baud_hint is None


@pytest.mark.parametrize("civ_addr", _ICOM_MODELS)
async def test_first_open_and_first_byte_on_the_port_are_at_the_scope_baud(
    civ_addr: int,
) -> None:
    fake = _icom_fake(civ_addr, locks_to_first_baud=True)
    opens: list[int] = []

    async def opener(*, url: str, baudrate: int, **kw: Any) -> tuple[Any, Any]:
        opens.append(baudrate)
        return await fake.opener(url=url, baudrate=baudrate, **kw)

    with _one_port():
        await _discover_one(_open_serial=opener)

    assert opens[0] == _SCOPE_BAUD
    assert fake.frame_bauds[0] == _SCOPE_BAUD


@pytest.mark.parametrize("civ_addr", _ICOM_MODELS)
async def test_radio_fixed_at_19200_is_reported_there_with_the_low_baud_flag(
    civ_addr: int,
) -> None:
    fake = _icom_fake(civ_addr, answers_at_baud=19200)
    with _one_port():
        result = await _discover_one(_open_serial=fake.opener)

    assert result.baudrate == 19200
    assert result.preferred_baudrate == _SCOPE_BAUD
    assert result.low_baud is True
    # The serial backend's own scope guidance, not a new text.
    assert result.low_baud_hint is not None
    assert f"set CI-V USB Baud Rate to {_SCOPE_BAUD}" in result.low_baud_hint
    # The low-baud flag is a typed field of the result contract.
    assert dataclasses.asdict(result)["low_baud"] is True


def test_civ_probe_order_is_the_profile_baud_then_the_documented_fallbacks() -> None:
    assert discovery.serial_probe_baud_rates("civ") == (115200, 19200, 9600, 4800)


async def test_civ_probe_without_explicit_bauds_uses_the_profile_order() -> None:
    seen: list[int] = []

    async def opener(*, url: str, baudrate: int, **_kw: Any) -> tuple[Any, Any]:
        seen.append(baudrate)
        raise OSError("port busy")

    assert await probe_serial_civ(_DEVICE, timeout=0.01, _open_serial=opener) is None
    assert seen == list(discovery.serial_probe_baud_rates("civ"))


# ---------------------------------------------------------------------------
# The order is profile data, per protocol
# ---------------------------------------------------------------------------


def test_probe_order_follows_the_profile_default_baud_data(monkeypatch) -> None:
    """Change the data, not the code: the order follows ``default_baud``."""
    loaded = profiles._ensure_loaded()
    rebased = {
        name: dataclasses.replace(
            profile,
            default_baud=57600 if profile.protocol_type == "civ" else profile.default_baud,
        )
        for name, profile in loaded.items()
    }
    monkeypatch.setattr(profiles, "_profiles", rebased)

    assert discovery.serial_probe_baud_rates("civ") == (57600, 19200, 9600, 115200, 4800)
    assert discovery.serial_probe_baud_rates("yaesu_cat") == (38400, 9600, 115200)


def test_non_icom_radios_keep_their_own_profile_order() -> None:
    # FTX-1 (yaesu_cat) prefers 38400; its order is unchanged by the CI-V fix.
    assert discovery.serial_probe_baud_rates("yaesu_cat") == (38400, 9600, 115200)
    # Xiegu X6200 (CI-V, default_baud 19200) stays in the CI-V order after the
    # Icom/X6100 115200, so a radio fixed at 19200 is still the second probe.
    assert profiles.get_radio_profile("xiegu_x6200").default_baud == 19200
    assert discovery.serial_probe_baud_rates("civ").index(19200) == 1


class _YaesuAtBaud:
    """A Yaesu CAT transport fake for a radio fixed at one baud."""

    def __init__(self, answers_at: int, seen: list[int], **kw: Any) -> None:
        self._baud = int(kw["baudrate"])
        self._answers = self._baud == answers_at
        seen.append(self._baud)

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def query(self, command: str, *, timeout: float | None = None) -> str:
        from rigplane.backends.yaesu_cat.transport import CatTimeoutError

        if not self._answers:
            raise CatTimeoutError("timeout")
        assert command == "ID;"
        return "ID0840"


async def _no_civ(*, url: str, baudrate: int, **_kw: Any) -> tuple[Any, Any]:
    raise OSError("no CI-V radio here")


@pytest.mark.parametrize(
    ("answers_at", "low_baud"),
    [pytest.param(38400, False, id="profile-baud"), pytest.param(9600, True, id="lower")],
)
async def test_yaesu_probe_order_and_low_baud_flag_come_from_its_profile(
    answers_at: int, low_baud: bool
) -> None:
    seen: list[int] = []
    with _one_port():
        result = await _discover_one(
            _open_serial=_no_civ,
            _yaesu_transport_factory=partial(_YaesuAtBaud, answers_at, seen),
        )

    assert seen[0] == 38400  # the FTX-1 profile baud is still probed first
    assert result.protocol == "yaesu_cat"
    assert result.profile_id == "yaesu_ftx1"
    assert result.baudrate == answers_at
    assert result.preferred_baudrate == 38400
    assert result.low_baud is low_baud
    assert result.low_baud_hint is None  # the CI-V scope guidance is not Yaesu's
