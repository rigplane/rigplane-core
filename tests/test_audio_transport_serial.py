"""AudioTransport neutral methods on the Icom serial base (MOR-540).

Step 7/12 of the MOR-532 AudioTransport epic: the Icom serial backends
gain the codec/transport-neutral ``start_rx`` / ``stop_rx`` /
``start_tx`` / ``push_tx`` / ``stop_tx`` methods over the USB audio
driver, the legacy ``*_opus`` family delegates onto them, and the
synthetic RX packet ident is documented as
``rigplane.audio.lan_stream.SYNTHETIC_RX_IDENT``.

Packet bytes must be identical to the legacy path: same ident value
(0x9781), same wrapping uint16 sequence, same payload (MOR-242/MOR-238
pin the TX clamp and RX channel behaviour elsewhere).
"""

from __future__ import annotations

import pytest
from test_icom7610_serial_radio import _FakeSerialCivLink, _FakeUsbAudioDriver

from rigplane.audio import AudioPacket
from rigplane.audio.lan_stream import SYNTHETIC_RX_IDENT
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.core.radio_protocol import AudioTransport

_PCM_FRAMES = [b"\x01\x02" * 960, b"\x03\x04" * 960]
_TX_FRAME = b"\x11\x22" * 960


def _make_radio(radio_cls=Icom7610SerialRadio):  # type: ignore[no-untyped-def]
    usb_audio = _FakeUsbAudioDriver()
    radio = radio_cls(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    return radio, usb_audio


def test_serial_backends_satisfy_audio_transport_protocol() -> None:
    """Both Icom serial backends are runtime instances of AudioTransport."""
    for radio_cls in (Icom7610SerialRadio, Ic705SerialRadio):
        radio, _ = _make_radio(radio_cls)
        assert isinstance(radio, AudioTransport), radio_cls.__name__


@pytest.mark.asyncio
async def test_start_rx_packets_carry_synthetic_ident() -> None:
    """Byte-compat lock: start_rx frames carry ident 0x9781 and wrap seq."""
    assert SYNTHETIC_RX_IDENT == 0x9781

    radio, usb_audio = _make_radio()
    await radio.connect()
    packets: list[AudioPacket] = []
    await radio.start_rx(packets.append)
    for frame in _PCM_FRAMES:
        usb_audio.emit_rx_pcm(frame)
    await radio.stop_rx()
    await radio.disconnect()

    assert [p.ident for p in packets] == [SYNTHETIC_RX_IDENT, SYNTHETIC_RX_IDENT]
    assert [p.send_seq for p in packets] == [0, 1]
    assert [p.data for p in packets] == _PCM_FRAMES
    assert usb_audio.rx_running is False


async def _run_rx_session(*, neutral: bool) -> tuple[list[AudioPacket], int]:
    radio, usb_audio = _make_radio()
    await radio.connect()
    packets: list[AudioPacket] = []
    if neutral:
        await radio.start_rx(packets.append)
    else:
        await radio.start_audio_rx_opus(packets.append)
    for frame in _PCM_FRAMES:
        usb_audio.emit_rx_pcm(frame)
    if neutral:
        await radio.stop_rx()
    else:
        await radio.stop_audio_rx_opus()
    await radio.disconnect()
    return packets, usb_audio.rx_starts


@pytest.mark.asyncio
async def test_neutral_rx_matches_legacy_packet_framing() -> None:
    """Legacy and neutral RX produce identical AudioPackets and driver calls."""
    legacy_packets, legacy_starts = await _run_rx_session(neutral=False)
    neutral_packets, neutral_starts = await _run_rx_session(neutral=True)

    assert neutral_packets == legacy_packets
    assert legacy_starts == neutral_starts == 1
    assert legacy_packets, "expected RX packets in both sessions"


async def _run_tx_session(*, neutral: bool) -> _FakeUsbAudioDriver:
    radio, usb_audio = _make_radio()
    await radio.connect()
    if neutral:
        await radio.start_tx()
        await radio.push_tx(_TX_FRAME)
        await radio.stop_tx()
    else:
        await radio.start_audio_tx_opus()
        await radio.push_audio_tx_opus(_TX_FRAME)
        await radio.stop_audio_tx_opus()
    await radio.disconnect()
    return usb_audio


@pytest.mark.asyncio
async def test_neutral_tx_matches_legacy_driver_calls() -> None:
    """Legacy and neutral TX arm the driver identically and push same bytes."""
    legacy_driver = await _run_tx_session(neutral=False)
    neutral_driver = await _run_tx_session(neutral=True)

    assert neutral_driver.tx_start_kwargs == legacy_driver.tx_start_kwargs
    assert neutral_driver.tx_frames == legacy_driver.tx_frames == [_TX_FRAME]
    assert legacy_driver.tx_running is False
    assert neutral_driver.tx_running is False


@pytest.mark.asyncio
async def test_push_tx_requires_start_tx() -> None:
    """push_tx keeps the legacy 'Audio TX not started' guard."""
    radio, _ = _make_radio()
    await radio.connect()
    with pytest.raises(RuntimeError, match="Audio TX not started"):
        await radio.push_tx(_TX_FRAME)
    await radio.disconnect()
