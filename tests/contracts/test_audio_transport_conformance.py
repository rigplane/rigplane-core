"""Epic conformance gate for the MOR-532 AudioTransport migration (MOR-545).

Step 12/12: every shipping backend exposes the codec/transport-neutral
``AudioTransport`` surface, the audio spine (bus / poller / broadcaster /
bridge) consumes it, and the public Pro contract is intact. This module
pins:

1. structural ``AudioTransport`` conformance of every shipping backend
   class (LAN ``IcomRadio``, the Icom serial family, ``YaesuCatRadio``);
2. the ``rigplane.audio`` export surface as a SUPERSET of the pre-epic
   list (Pro contract), plus the additive ``SYNTHETIC_RX_IDENT`` export
   deferred from MOR-540;
3. the frozen 14-member ``AudioCapable`` legacy protocol (mirrors the
   pin in ``tests/test_audio_transport_protocol.py`` — both stay, they
   guard different files);
4. uint16 wrap of the serial synthetic RX sequence (0xFFFF -> 0);
5. the AudioBridge radio-side TX path on the neutral surface, including
   the non-PCM degrade;
6. the additive ``audio_setup_order`` descriptor (MOR-575, ADR §3.3) —
   derived from ``audio_duplex_mode``, duck-typed (NOT on the
   ``AudioTransport`` Protocol; the 10-member pin stays frozen).

Known edge (documented per the MOR-544 review note): a custom LAN
profile that negotiates a non-PCM ``tx_codec`` combined with PCM input
from the browser or the WSJT-X bridge was already non-functional on
main — raw PCM bytes went onto the wire under the negotiated codec
ident (codec impersonation). The neutral surface makes the mismatch
explicit: the bridge now degrades to RX-only with a clear warning
instead of pushing mis-typed bytes. No shipping backend negotiates a
non-PCM TX codec for these paths.
"""

from __future__ import annotations

import asyncio
import logging
import types
from unittest.mock import AsyncMock

import pytest
from test_icom7610_serial_radio import (
    _DuplexAwareUsbAudioDriver,
    _FakeSerialCivLink,
    _FakeUsbAudioDriver,
    _RaisingDuplexUsbAudioDriver,
)

import rigplane.audio
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.bridge import AudioBridge, SAMPLES_PER_FRAME
from rigplane.audio.bus import AudioBus
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.ic9700 import Ic9700SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat import YaesuCatRadio
from rigplane.core.radio_protocol import AudioCapable, AudioTransport
from rigplane.core.types import AudioCodec
from rigplane.runtime.radio import IcomRadio

# ---------------------------------------------------------------------------
# 1. Backend structural conformance
# ---------------------------------------------------------------------------

# The full AudioTransport member set (MOR-538). Pinned explicitly — do
# NOT compute it from the Protocol; the point is to fail loudly when the
# neutral surface changes shape.
AUDIO_TRANSPORT_MEMBERS = frozenset(
    {
        "audio_bus",
        "audio_codec",
        "audio_tx_codec",
        "audio_sample_rate",
        "audio_duplex_mode",
        "start_rx",
        "stop_rx",
        "start_tx",
        "push_tx",
        "stop_tx",
    }
)

SHIPPING_BACKENDS = [
    IcomRadio,
    Icom7610SerialRadio,
    Ic705SerialRadio,
    Ic7300SerialRadio,
    Ic9700SerialRadio,
    YaesuCatRadio,
]


def test_member_pin_matches_protocol_definition() -> None:
    """The pinned set above mirrors the Protocol (3.12+ exposes it)."""
    protocol_attrs = getattr(AudioTransport, "__protocol_attrs__", None)
    if protocol_attrs is not None:
        assert set(protocol_attrs) == set(AUDIO_TRANSPORT_MEMBERS)


@pytest.mark.parametrize("backend_cls", SHIPPING_BACKENDS)
def test_shipping_backend_satisfies_audio_transport(backend_cls: type) -> None:
    """Every shipping backend class carries the full neutral surface.

    Class-level attribute checks (not instances) so the gate needs no
    hardware, no event loop, and no constructor arguments.
    """
    missing = {m for m in AUDIO_TRANSPORT_MEMBERS if not hasattr(backend_cls, m)}
    assert not missing, f"{backend_cls.__name__} lacks AudioTransport: {missing}"


def test_serial_backend_instance_isinstance_audio_transport() -> None:
    """At least one cheap-to-build backend passes the runtime isinstance."""
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_FakeUsbAudioDriver(),
    )
    assert isinstance(radio, AudioTransport)


# ---------------------------------------------------------------------------
# 2. rigplane.audio export surface (Pro contract)
# ---------------------------------------------------------------------------

# Exact ``rigplane.audio.__all__`` before the MOR-532 epic. The current
# surface must remain a SUPERSET — names may be added, never removed.
PRE_EPIC_AUDIO_EXPORTS = frozenset(
    {
        "AudioBackend",
        "AudioDeviceId",
        "AudioDeviceInfo",
        "FakeAudioBackend",
        "FakeRxStream",
        "FakeTxStream",
        "PortAudioBackend",
        "RxStream",
        "TxStream",
        "AUDIO_HEADER_SIZE",
        "AudioPacket",
        "AudioState",
        "AudioStats",
        "AudioStream",
        "JitterBuffer",
        "MAX_AUDIO_PAYLOAD",
        "RX_IDENT_0xA0",
        "build_audio_packet",
        "parse_audio_packet",
        "TX_IDENT",
        "DspPipeline",
        "DspStage",
        "Limiter",
        "NoiseGate",
        "RmsNormalizer",
        "PcmResampler",
        "SampleRateNegotiation",
        "negotiate_sample_rate",
        "AudioDeviceSelectionError",
        "AudioDriverLifecycleError",
        "UsbAudioDevice",
        "UsbAudioDriver",
        "list_usb_audio_devices",
        "select_usb_audio_devices",
    }
)


def test_audio_exports_superset_of_pre_epic_surface() -> None:
    current = set(rigplane.audio.__all__)
    removed = PRE_EPIC_AUDIO_EXPORTS - current
    assert not removed, f"Pro contract broken — removed exports: {removed}"
    for name in PRE_EPIC_AUDIO_EXPORTS:
        assert hasattr(rigplane.audio, name), f"rigplane.audio.{name} lost"


def test_synthetic_rx_ident_exported_additively() -> None:
    """MOR-540 deferred this export to the epic gate: additive, value-pinned."""
    assert "SYNTHETIC_RX_IDENT" in rigplane.audio.__all__
    assert rigplane.audio.SYNTHETIC_RX_IDENT == 0x9781


# ---------------------------------------------------------------------------
# 3. AudioCapable legacy protocol frozen (14 members)
# ---------------------------------------------------------------------------


def test_audio_capable_member_set_frozen() -> None:
    """Mirror of the pin in tests/test_audio_transport_protocol.py."""
    expected = {
        "audio_bus",
        "audio_codec",
        "audio_sample_rate",
        "start_audio_rx_opus",
        "stop_audio_rx_opus",
        "push_audio_tx_opus",
        "start_audio_rx_pcm",
        "stop_audio_rx_pcm",
        "start_audio_tx_pcm",
        "push_audio_tx_pcm",
        "stop_audio_tx_pcm",
        "get_audio_stats",
        "start_audio_tx_opus",
        "stop_audio_tx_opus",
    }
    actual = {name for name in vars(AudioCapable) if not name.startswith("_")}
    assert actual == expected


# ---------------------------------------------------------------------------
# 4. Serial synthetic RX sequence uint16 wrap
# ---------------------------------------------------------------------------


async def test_serial_audio_seq_wraps_uint16() -> None:
    """send_seq is uint16: 0xFFFF wraps to 0 on the next synthetic packet."""
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    radio._serial_audio_seq = 0xFFFF
    seqs: list[int] = []
    await radio.start_rx(lambda packet: seqs.append(packet.send_seq))
    frame = b"\x01\x02" * 960
    usb_audio.emit_rx_pcm(frame)
    usb_audio.emit_rx_pcm(frame)
    await radio.stop_rx()
    await radio.disconnect()
    assert seqs == [0xFFFF, 0x0000]


# ---------------------------------------------------------------------------
# 5. AudioBridge radio-side TX on the neutral surface (MOR-545)
# ---------------------------------------------------------------------------

_LOUD_FRAME = (1000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME


def _bridge_backend() -> FakeAudioBackend:
    return FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="BlackHole 2ch",
                input_channels=2,
                output_channels=2,
            )
        ]
    )


def _neutral_radio(
    tx_codec: AudioCodec = AudioCodec.PCM_1CH_16BIT,
) -> types.SimpleNamespace:
    """Radio stub with BOTH the legacy PCM surface and the neutral one."""
    radio: types.SimpleNamespace = types.SimpleNamespace(
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
        start_audio_tx_pcm=AsyncMock(),
        push_audio_tx_pcm=AsyncMock(),
        stop_audio_tx_pcm=AsyncMock(),
        start_tx=AsyncMock(),
        push_tx=AsyncMock(),
        stop_tx=AsyncMock(),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        audio_tx_codec=tx_codec,
        audio_duplex_mode="full",
    )
    radio.audio_bus = AudioBus(radio)
    return radio


async def test_bridge_uses_neutral_tx_surface() -> None:
    """start/push/stop go through the neutral methods, never the legacy."""
    radio = _neutral_radio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=True, backend=backend
    )
    await bridge.start()

    radio.start_tx.assert_awaited_once()
    radio.start_audio_tx_pcm.assert_not_called()

    backend.rx_streams[0].inject_frame(_LOUD_FRAME)
    await asyncio.sleep(0.05)
    await bridge.stop()

    radio.push_tx.assert_awaited_with(_LOUD_FRAME)
    radio.push_audio_tx_pcm.assert_not_called()
    radio.stop_tx.assert_awaited_once()
    radio.stop_audio_tx_pcm.assert_not_called()


async def test_bridge_degrades_to_rx_only_on_non_pcm_tx_codec(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-PCM negotiated TX codec → RX-only with a warning, no mis-typed push.

    The bridge captures raw PCM s16le; pushing it through ``push_tx`` when
    the radio negotiated an Opus TX codec would impersonate the codec
    (see module docstring — already non-functional on main).
    """
    radio = _neutral_radio(tx_codec=AudioCodec.OPUS_1CH)
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=True, backend=backend
    )
    with caplog.at_level(logging.WARNING, logger="rigplane.audio.bridge"):
        await bridge.start()

    assert bridge._tx_enabled is False
    radio.start_tx.assert_not_called()
    radio.start_audio_tx_pcm.assert_not_called()
    assert any("non-PCM TX codec" in rec.message for rec in caplog.records)

    # RX-only: no TX capture stream is opened at all, nothing is pushed.
    assert backend.rx_streams == []
    await bridge.stop()

    radio.push_tx.assert_not_called()
    radio.push_audio_tx_pcm.assert_not_called()
    radio.stop_tx.assert_not_called()


async def test_bridge_legacy_radio_keeps_pcm_path() -> None:
    """Radios without the neutral surface stay on the legacy PCM methods."""
    radio = types.SimpleNamespace(
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
        start_audio_tx_pcm=AsyncMock(),
        push_audio_tx_pcm=AsyncMock(),
        stop_audio_tx_pcm=AsyncMock(),
    )
    radio.audio_bus = AudioBus(radio)
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=True, backend=backend
    )
    await bridge.start()
    radio.start_audio_tx_pcm.assert_awaited_once()

    backend.rx_streams[0].inject_frame(_LOUD_FRAME)
    await asyncio.sleep(0.05)
    await bridge.stop()

    radio.push_audio_tx_pcm.assert_awaited_with(_LOUD_FRAME)
    radio.stop_audio_tx_pcm.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. audio_setup_order descriptor derived from audio_duplex_mode (MOR-575)
# ---------------------------------------------------------------------------

# ADR §3.3 derivation, pinned explicitly: ``"full"`` -> ``"rx_first"``
# (the LAN UDP stream reverts RX state from TRANSMITTING, so RX must be
# armed before TX; USB separate-devices "full" is order-indifferent and
# keeps the same value), ``"exclusive"`` -> ``"atomic"`` (one same-device
# duplex stream — setup does not decompose into rx/tx-first), ``"half"``
# -> ``"rx_first"`` (safe default). Additive duck-typed descriptor: NOT
# on the AudioTransport Protocol (the 10-member pin above stays frozen);
# consumers read it via ``getattr(radio, "audio_setup_order",
# "rx_first")`` — exactly how the bridge reads ``audio_duplex_mode``.
# Nothing consumes it yet (the bridge keeps its own rx-first branch
# until MOR-562 steps 8/9).
DUPLEX_TO_SETUP_ORDER = {
    "full": "rx_first",
    "exclusive": "atomic",
    "half": "rx_first",
}


@pytest.mark.parametrize("backend_cls", SHIPPING_BACKENDS)
def test_shipping_backend_exposes_audio_setup_order(backend_cls: type) -> None:
    """Every shipping backend class carries the MOR-575 descriptor."""
    assert hasattr(backend_cls, "audio_setup_order"), (
        f"{backend_cls.__name__} lacks the audio_setup_order descriptor"
    )


def test_lan_radio_setup_order_is_rx_first() -> None:
    """LAN duplex mode is hard-``"full"`` -> setup order ``"rx_first"``."""
    radio = IcomRadio("192.168.1.100", model="IC-7300")
    assert radio.audio_duplex_mode == "full"
    assert radio.audio_setup_order == "rx_first"


@pytest.mark.parametrize("duplex_mode", sorted(DUPLEX_TO_SETUP_ORDER))
def test_serial_setup_order_consistent_with_duplex_mode(duplex_mode: str) -> None:
    """Serial backends derive setup order from the driver duplex policy."""
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_DuplexAwareUsbAudioDriver(duplex_mode),
    )
    assert radio.audio_duplex_mode == duplex_mode
    assert radio.audio_setup_order == DUPLEX_TO_SETUP_ORDER[duplex_mode]


def test_serial_setup_order_defaults_to_rx_first_when_driver_raises() -> None:
    """Raising duplex enumeration degrades like duplex_mode: full/rx_first."""
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_RaisingDuplexUsbAudioDriver(),
    )
    assert radio.audio_duplex_mode == "full"
    assert radio.audio_setup_order == "rx_first"


@pytest.mark.parametrize("duplex_mode", sorted(DUPLEX_TO_SETUP_ORDER))
def test_yaesu_setup_order_consistent_with_duplex_mode(duplex_mode: str) -> None:
    """YaesuCatRadio (the "exclusive"-capable backend) maps per the table."""
    radio = YaesuCatRadio(
        device="/dev/fake0",
        audio_driver=_DuplexAwareUsbAudioDriver(duplex_mode),  # type: ignore[arg-type]
    )
    assert radio.audio_duplex_mode == duplex_mode
    assert radio.audio_setup_order == DUPLEX_TO_SETUP_ORDER[duplex_mode]


def test_setup_order_not_on_audio_transport_protocol() -> None:
    """MOR-575 is duck-typed — the Protocol member pin must NOT grow."""
    assert "audio_setup_order" not in AUDIO_TRANSPORT_MEMBERS
    protocol_attrs = getattr(AudioTransport, "__protocol_attrs__", None)
    if protocol_attrs is not None:
        assert "audio_setup_order" not in protocol_attrs
