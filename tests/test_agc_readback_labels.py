"""MOR-4436: readable-but-not-settable AGC modes.

A radio can REPORT a mode its panel selected that the protocol cannot
command (e.g. G7250 panel-selected Medium/Auto). Those codes are declared
in ``[agc].readback_modes`` with display names in the new
``[agc].readback_labels`` table; ``get_agc`` accepts them while
``set_agc`` still gates on the settable ``[agc].modes``. The capabilities
contract carries them as ``agcReadback.labels``.

Every profile here goes through the real ``load_rig`` loader — no
hand-built ``RadioProfile`` stands in for the unit under test.
"""

from __future__ import annotations

import asyncio
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.transport import IcomCivTransport
from rigplane.radio import IcomRadio
from rigplane.rig_loader import RigLoadError, load_rig

from _helpers import observe_selected_receiver
from _helpers import wrap_civ_in_udp as _wrap_civ_in_udp

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"

_MINIMAL_TOML = """\
[radio]
id = "icom_test_agc"
model = "TEST-AGC"
civ_addr = 0x94
receiver_count = 1
has_lan = true
has_wifi = false

[spectrum]
seq_max = 11
amp_max = 160
data_len_max = 475

[capabilities]
features = ["audio", "scope", "meters", "tx"]

[modes]
list = ["USB", "LSB", "CW"]

[filters]
list = ["FIL1", "FIL2"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 30000
end_hz = 60000000

[commands]
get_freq = [0x03]
set_freq = [0x05]
get_agc = [0x16, 0x12]
set_agc = [0x16, 0x12]

[commands.overrides]
"""

_AGC_DECLARATION = """
[agc]
modes = [0, 1, 3]
labels = { "0" = "OFF", "1" = "FAST", "3" = "SLOW" }
readback_modes = [0, 1, 2, 3]
readback_labels = { "2" = "TESTMID" }
"""


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test_agc.toml"
    p.write_text(textwrap.dedent(content))
    return p


def _load_test_profile(tmp_path: Path, agc_section: str = _AGC_DECLARATION):
    return load_rig(_write_toml(tmp_path, _MINIMAL_TOML + agc_section))


class TestReadbackLabelsLoader:
    """The new ``[agc].readback_labels`` table loads through the real loader."""

    def test_loads_readback_modes_and_labels(self, tmp_path: Path) -> None:
        rig = _load_test_profile(tmp_path)
        assert rig.agc_modes == (0, 1, 3)
        assert rig.agc_readback_modes == (0, 1, 2, 3)
        assert rig.agc_readback_labels == {"2": "TESTMID"}
        assert rig.to_profile().agc_readback_labels == {"2": "TESTMID"}

    @pytest.mark.parametrize(
        ("declaration", "error"),
        [
            (
                'readback_labels = { "1" = "FASTX" }',
                r"\[agc\]\.readback_labels key '1'.*settable mode",
            ),
            (
                'readback_labels = { "9" = "NOPE" }',
                r"\[agc\]\.readback_labels key '9'.*readback_modes",
            ),
            (
                'readback_labels = { "MID" = "MID" }',
                r"\[agc\]\.readback_labels key 'MID'.*integer code",
            ),
            (
                'readback_labels = { "2" = "" }',
                r"\[agc\]\.readback_labels values must be non-empty strings",
            ),
            (
                'readback_labels = { "2" = "   " }',
                r"\[agc\]\.readback_labels values must be non-empty strings",
            ),
        ],
    )
    def test_readback_labels_rejects_bad_entries(
        self, tmp_path: Path, declaration: str, error: str
    ) -> None:
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """
[agc]
modes = [0, 1, 3]
labels = { "0" = "OFF", "1" = "FAST", "3" = "SLOW" }
readback_modes = [0, 1, 2, 3]
"""
            + declaration
            + "\n",
        )
        with pytest.raises(RigLoadError, match=error):
            load_rig(p)

    def test_readback_labels_requires_readback_modes(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """
[agc]
modes = [0, 1, 3]
labels = { "0" = "OFF", "1" = "FAST", "3" = "SLOW" }
readback_labels = { "2" = "TESTMID" }
""",
        )
        with pytest.raises(
            RigLoadError, match=r"\[agc\]\.readback_labels requires readback_modes"
        ):
            load_rig(p)

    def test_readback_labels_rejects_non_table(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """
[agc]
modes = [0, 1, 3]
labels = { "0" = "OFF", "1" = "FAST", "3" = "SLOW" }
readback_modes = [0, 1, 2, 3]
readback_labels = "TESTMID"
""",
        )
        with pytest.raises(
            RigLoadError, match=r"\[agc\]\.readback_labels must be a table"
        ):
            load_rig(p)


class TestReadbackLabelsCapabilities:
    """``agcReadback.labels`` appears only when declared; FTX-1 is untouched."""

    def test_emits_labels_without_auto_keys(self, tmp_path: Path) -> None:
        from rigplane.web.server import _serialize_agc_readback

        profile = _load_test_profile(tmp_path).to_profile()
        assert _serialize_agc_readback(profile) == {
            "modes": [0, 1, 2, 3],
            "labels": {"2": "TESTMID"},
        }

    def test_ftx1_payload_is_byte_identical(self) -> None:
        """The only shipped radio with ``readback_modes`` gains no ``labels`` key."""
        from rigplane.web.server import _serialize_agc_readback

        profile = load_rig(RIGS_DIR / "ftx1.toml").to_profile()
        payload = _serialize_agc_readback(profile)
        assert payload == {
            "modes": [0, 1, 2, 3, 4, 5, 6],
            "autoMode": 4,
            "autoSpeedLabels": {"4": "FAST", "5": "MID", "6": "SLOW"},
        }
        assert "labels" not in payload

    @pytest.mark.parametrize("rig", ["ic7300", "x6200"])
    def test_second_radio_has_no_readback_object(self, rig: str) -> None:
        """Radios without ``readback_modes`` still serialize ``agcReadback: None``."""
        from rigplane.web.server import _serialize_agc_readback

        profile = load_rig(RIGS_DIR / f"{rig}.toml").to_profile()
        assert profile.agc_readback_modes is None
        assert profile.agc_readback_labels is None
        assert _serialize_agc_readback(profile) is None


class _MockTransport:
    """Minimal queue-backed transport for single-receiver CI-V reads."""

    ordered_ingress: ClassVar[bool] = IcomCivTransport.ordered_ingress

    def __init__(self) -> None:
        self.connected = False
        self.sent_packets: list[bytes] = []
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self.my_id: int = 0x00010001
        self.remote_id: int = 0xDEADBEEF
        self.send_seq: int = 0

    async def connect(self, host: str, port: int) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        self.sent_packets.append(data)
        self.send_seq += 1

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._responses.get(), timeout=timeout)

    def queue_response(self, data: bytes) -> None:
        self._responses.put_nowait(data)


@pytest.fixture
def mock_transport() -> _MockTransport:
    return _MockTransport()


def _agc_response(civ_addr: int, code: int) -> bytes:
    civ = build_civ_frame(CONTROLLER_ADDR, civ_addr, 0x16, sub=0x12, data=bytes([code]))
    return _wrap_civ_in_udp(civ)


def _connected_test_radio(tmp_path: Path, transport: _MockTransport) -> IcomRadio:
    profile = _load_test_profile(tmp_path).to_profile()
    radio = IcomRadio("192.168.1.100", profile=profile)
    radio._civ_transport = transport  # noqa: SLF001
    radio._ctrl_transport = transport  # noqa: SLF001
    radio._connected = True  # noqa: SLF001
    observe_selected_receiver(radio, 0)
    return radio


class TestReadbackLabelsGetSet:
    """``get_agc`` accepts the readback domain; ``set_agc`` keeps the settable one."""

    @pytest.mark.asyncio
    async def test_get_agc_returns_readback_only_code(
        self, tmp_path: Path, mock_transport: _MockTransport
    ) -> None:
        radio = _connected_test_radio(tmp_path, mock_transport)
        mock_transport.queue_response(_agc_response(0x94, 0x02))
        assert await radio.get_agc() == 2
        radio._connected = False  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_set_agc_refuses_readback_only_code(
        self, tmp_path: Path, mock_transport: _MockTransport
    ) -> None:
        radio = _connected_test_radio(tmp_path, mock_transport)
        with pytest.raises(ValueError, match=r"AGC mode must be one of"):
            await radio.set_agc(2)
        assert mock_transport.sent_packets == []
        radio._connected = False  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_set_agc_still_commands_settable_code(
        self, tmp_path: Path, mock_transport: _MockTransport
    ) -> None:
        radio = _connected_test_radio(tmp_path, mock_transport)
        await radio.set_agc(1)
        assert mock_transport.sent_packets[-1].endswith(b"\x16\x12\x01\xfd")
        radio._connected = False  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_second_radio_domain_behaviour_unchanged(
        self, mock_transport: _MockTransport
    ) -> None:
        """IC-7300/X6200 declare no readback domain: same accept/reject as before."""
        ic7300 = IcomRadio("192.168.1.100", model="IC-7300")
        ic7300._civ_transport = mock_transport  # noqa: SLF001
        ic7300._ctrl_transport = mock_transport  # noqa: SLF001
        ic7300._connected = True  # noqa: SLF001
        observe_selected_receiver(ic7300, 0)
        mock_transport.queue_response(_agc_response(0x94, 0x02))
        assert await ic7300.get_agc() == 2
        ic7300._connected = False  # noqa: SLF001

        x6200 = IcomRadio("192.168.1.100", model="X6200")
        second = _MockTransport()
        x6200._civ_transport = second  # noqa: SLF001
        x6200._ctrl_transport = second  # noqa: SLF001
        x6200._connected = True  # noqa: SLF001
        observe_selected_receiver(x6200, 0)
        second.queue_response(_agc_response(0xA4, 0x09))
        with pytest.raises(ValueError, match=r"AGC mode.*not in declared domain"):
            await x6200.get_agc()
        x6200._connected = False  # noqa: SLF001
