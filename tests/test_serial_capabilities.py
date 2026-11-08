"""Factory capability and shared CI-V identity resolver tests (MOR-3777).

``supports_serial_model`` must answer from the same model -> serial
backend dispatch table ``create_radio`` uses, and
``resolve_civ_radio_identity`` must reproduce the identity rules
``discover_serial_radios`` applied inline before the extraction.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rigplane.backends import factory
from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import (
    _SERIAL_MODEL_CLASSES,
    create_radio,
    supports_serial_model,
)
from rigplane.backends.discovery import (
    CivProbeResult,
    SerialPortCandidate,
    resolve_civ_radio_identity,
)
from rigplane.runtime.radios import SERIAL_RADIO_MAP

# Wire bytes shared with tests/test_discovery.py: the Xiegu-only 0x1D 0x19
# model-ID reply and the Icom NAK at the shared IC-705/X6200 address 0xA4.
_XIEGU_MODEL_ID_REPLY = bytes([0xFE, 0xFE, 0xE0, 0xA4, 0x1D, 0x19, 0x62, 0x00, 0xFD])
_XIEGU_NAK_REPLY = bytes([0xFE, 0xFE, 0xE0, 0xA4, 0xFA, 0xFD])


class TestSupportsSerialModel:
    @pytest.mark.parametrize(
        "model",
        sorted(set(_SERIAL_MODEL_CLASSES) | {"FTX-1", "FT-710", "FT-991A", "FTDX10"}),
    )
    def test_supported_model_matches_create_radio_dispatch(self, model: str) -> None:
        assert supports_serial_model(model) is True
        radio = create_radio(SerialBackendConfig(device="/dev/ttyUSB0", model=model))
        assert radio.backend_id in ("icom_serial", "yaesu_cat")

    def test_mk2_is_supported_and_dispatches_to_the_ic7300_backend(self) -> None:
        """MOR-3777: IC-7300MK2 is factory-supported over serial; the Pro
        catalog's copied list was the only place that missed it."""
        from rigplane.backends.ic7300.serial import Ic7300SerialRadio

        assert supports_serial_model("IC-7300MK2") is True
        radio = create_radio(
            SerialBackendConfig(device="/dev/ttyUSB0", model="IC-7300MK2")
        )
        assert isinstance(radio, Ic7300SerialRadio)

    @pytest.mark.parametrize("model", ["X6100", "TX-500", "IC-R8600"])
    def test_unsupported_model_is_false_and_create_radio_refuses(
        self, model: str
    ) -> None:
        assert supports_serial_model(model) is False
        with pytest.raises(ValueError, match="Unsupported serial model"):
            create_radio(SerialBackendConfig(device="/dev/ttyUSB0", model=model))

    @pytest.mark.parametrize("model", [None, "", "   "])
    def test_blank_model_is_false_like_create_radio_refuses(
        self, model: str | None
    ) -> None:
        assert supports_serial_model(model) is False

    def test_matching_is_case_insensitive(self) -> None:
        assert supports_serial_model("ic-7300mk2") is True
        assert supports_serial_model("x6200") is True
        assert supports_serial_model("ft-710") is True

    def test_unsupported_model_error_message_lists_every_table_model(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            create_radio(SerialBackendConfig(device="/dev/ttyUSB0", model="X6100"))
        assert str(excinfo.value) == (
            "Unsupported serial model 'X6100'; supported: "
            "IC-705, IC-7300, IC-7300MK2, IC-7610, IC-905, IC-9700, X6200, "
            "and Yaesu FT-series (FTX-1, FT-710, ...)."
        )


class TestCreateRadioSymbolSeam:
    """MOR-3777 review R1: ``create_radio`` must resolve serial and Yaesu
    constructors from module globals at call time, so patching the
    factory's public class symbols after import reaches the dispatch —
    the private table is not the seam."""

    @pytest.mark.parametrize(
        ("model", "symbol"),
        [
            ("IC-705", "Ic705SerialRadio"),
            ("IC-7300", "Ic7300SerialRadio"),
            ("IC-7300MK2", "Ic7300SerialRadio"),
            ("IC-7610", "Icom7610SerialRadio"),
            ("IC-9700", "Ic9700SerialRadio"),
            ("X6200", "XieguSerialRadio"),
            ("FTX-1", "YaesuCatRadio"),
        ],
    )
    def test_post_import_symbol_patch_reaches_create_radio(
        self, monkeypatch: pytest.MonkeyPatch, model: str, symbol: str
    ) -> None:
        calls: list[dict[str, object]] = []

        def fake(**kwargs: object) -> None:
            calls.append(kwargs)

        monkeypatch.setattr(factory, symbol, fake)
        create_radio(SerialBackendConfig(device="/dev/ttyUSB0", model=model))
        assert len(calls) == 1
        assert calls[0]["device"] == "/dev/ttyUSB0"


def _civ_hit(address: int, model_id: bytes) -> CivProbeResult:
    return CivProbeResult(
        port="/dev/ttyUSB0", baud=115200, address=address, model_id=model_id
    )


class _Reader:
    def __init__(self, chunks: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for chunk in chunks:
            self._queue.put_nowait(chunk)

    async def read(self, _n: int) -> bytes:
        return await self._queue.get()


class _Writer:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def _make_open(reader: _Reader, writer: _Writer):
    async def _open(*, url: str, baudrate: int, **_kw: object):
        assert url == "/dev/ttyUSB0" and baudrate == 115200
        return reader, writer

    return _open


async def _never_open(**_kw: object) -> tuple[Any, Any]:
    raise AssertionError("no serial port may be opened on this path")


class TestResolveCivRadioIdentity:
    @pytest.mark.asyncio
    async def test_ic7610_address_resolves_without_any_serial_io(self) -> None:
        model, profile_id = await resolve_civ_radio_identity(
            _civ_hit(0x98, b"\x01\x06"), _open_serial=_never_open
        )
        assert (model, profile_id) == ("IC-7610", "icom_ic7610")

    @pytest.mark.asyncio
    async def test_x6200_hwid_fingerprint_wins_without_extra_probe(self) -> None:
        """The MOR-170 USB fingerprint path classifies an X6200 at the
        shared 0xA4 address with no second serial open."""
        port = SerialPortCandidate(
            device="/dev/ttyUSB0",
            description="USB Dual_Serial",
            hwid="USB VID:PID=1A86:55D2",
            vid=0x1A86,
            pid=0x55D2,
            product="USB Dual_Serial",
        )
        model, profile_id = await resolve_civ_radio_identity(
            _civ_hit(0xA4, b"\x01\x05"), port, _open_serial=_never_open
        )
        assert (model, profile_id) == ("X6200", "xiegu_x6200")

    @pytest.mark.parametrize(
        ("address", "model_id", "expected"),
        [
            pytest.param(
                0x94,
                SERIAL_RADIO_MAP[0x94][1],
                ("IC-7300", "icom_ic7300"),
                id="ic7300",
            ),
            pytest.param(
                0xB6, b"\xb6", ("IC-7300MK2", "icom_ic7300mk2"), id="mk2-address-echo"
            ),
            pytest.param(
                0xB6,
                SERIAL_RADIO_MAP[0x94][1],
                ("IC-7300MK2", "icom_ic7300mk2"),
                id="mk2-with-ic7300-id-bytes",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_ic7300_family_identity_cannot_be_confused(
        self, address: int, model_id: bytes, expected: tuple[str, str]
    ) -> None:
        """MOR-3792: 0xB6 resolves through the real identity table with no
        extra probe; the MK2's model-ID reply bytes are undocumented, so
        the single-byte address echo alone names the radio. A model-ID
        mismatch only logs at debug level, so 0xB6 stays the MK2 even when
        the bytes are the IC-7300's own: the address decides."""
        model, profile_id = await resolve_civ_radio_identity(
            _civ_hit(address, model_id), _open_serial=_never_open
        )
        assert (model, profile_id) == expected

    @pytest.mark.asyncio
    async def test_shared_0xA4_without_fingerprint_confirms_xiegu_by_opcode(
        self,
    ) -> None:
        """Without port metadata (a setup check on one chosen port) the
        resolver disambiguates 0xA4 through Core's 0x1D 0x19 opcode probe,
        reusing the injected opener at the CI-V probe's port/baud."""
        writer = _Writer()
        reader = _Reader([_XIEGU_MODEL_ID_REPLY])
        model, profile_id = await resolve_civ_radio_identity(
            _civ_hit(0xA4, b"\x01\x05"), None, _open_serial=_make_open(reader, writer)
        )
        assert (model, profile_id) == ("X6200", "xiegu_x6200")
        assert writer.written == [bytes([0xFE, 0xFE, 0xA4, 0xE0, 0x1D, 0x19, 0xFD])]

    @pytest.mark.asyncio
    async def test_shared_0xA4_nak_keeps_ic705(self) -> None:
        writer = _Writer()
        reader = _Reader([_XIEGU_NAK_REPLY])
        model, profile_id = await resolve_civ_radio_identity(
            _civ_hit(0xA4, b"\x01\x05"), None, _open_serial=_make_open(reader, writer)
        )
        assert (model, profile_id) == ("IC-705", "icom_ic705")

    @pytest.mark.asyncio
    async def test_inconclusive_fingerprint_falls_through_to_opcode(self) -> None:
        port = SerialPortCandidate(
            device="/dev/ttyUSB0",
            description="Generic CDC-ACM",
            hwid="USB VID:PID=DEAD:BEEF",
            vid=0xDEAD,
            pid=0xBEEF,
            product="Generic Serial",
        )
        reader = _Reader([_XIEGU_MODEL_ID_REPLY])
        model, profile_id = await resolve_civ_radio_identity(
            _civ_hit(0xA4, b"\x01\x05"),
            port,
            _open_serial=_make_open(reader, _Writer()),
        )
        assert (model, profile_id) == ("X6200", "xiegu_x6200")
