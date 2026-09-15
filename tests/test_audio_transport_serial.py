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

MOR-2465: RX frames captured on the PortAudio thread are delivered to
the subscriber on the owning event loop, in capture order; a frame
scheduled before ``stop_rx`` is discarded instead of entering a
restarted stream.
"""

from __future__ import annotations

import asyncio
import threading

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


def _emit_rx_pcm_off_thread(
    usb_audio: _FakeUsbAudioDriver, frames: list[bytes]
) -> None:
    """Emit frames from a background thread, mirroring the PortAudio thread."""
    emitter = threading.Thread(
        target=lambda: [usb_audio.emit_rx_pcm(frame) for frame in frames]
    )
    emitter.start()
    emitter.join()


async def _drain_rx_delivery() -> None:
    """Yield to the loop so scheduled RX deliveries can run."""
    await asyncio.sleep(0)


class _FailFirstStartUsbAudioDriver(_FakeUsbAudioDriver):
    """Fake USB driver whose first ``start_rx`` raises (device open failure)."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next_start = True

    async def start_rx(self, callback, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError("RX start failed")
        await super().start_rx(callback, **kwargs)


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
    await _drain_rx_delivery()
    await radio.stop_rx()
    await radio.disconnect()

    assert [p.ident for p in packets] == [SYNTHETIC_RX_IDENT, SYNTHETIC_RX_IDENT]
    assert [p.send_seq for p in packets] == [0, 1]
    assert [p.data for p in packets] == _PCM_FRAMES
    assert usb_audio.rx_running is False


@pytest.mark.asyncio
async def test_start_rx_delivers_on_owner_loop_from_audio_thread() -> None:
    """MOR-2465: off-thread capture delivers on the loop thread, in order."""
    radio, usb_audio = _make_radio()
    await radio.connect()
    loop_thread = threading.get_ident()
    seen: list[tuple[AudioPacket, int]] = []

    def record(packet: AudioPacket | None) -> None:
        seen.append((packet, threading.get_ident()))

    await radio.start_rx(record)
    _emit_rx_pcm_off_thread(usb_audio, _PCM_FRAMES)
    for _ in range(100):
        if len(seen) == len(_PCM_FRAMES):
            break
        await asyncio.sleep(0.001)
    await radio.stop_rx()
    await radio.disconnect()

    assert [p.data for p, _ in seen] == _PCM_FRAMES
    assert [p.send_seq for p, _ in seen] == [0, 1]
    assert [t for _, t in seen] == [loop_thread, loop_thread]


@pytest.mark.asyncio
async def test_start_rx_frame_queued_across_stop_restart_is_discarded() -> None:
    """MOR-2465: a frame scheduled before stop_rx never enters a new stream."""
    radio, usb_audio = _make_radio()
    await radio.connect()
    packets: list[AudioPacket] = []
    sink = packets.append

    await radio.start_rx(sink)
    _emit_rx_pcm_off_thread(usb_audio, _PCM_FRAMES[:1])
    await radio.stop_rx()
    await _drain_rx_delivery()
    assert packets == []

    await radio.start_rx(sink)
    await _drain_rx_delivery()
    assert packets == []

    usb_audio.emit_rx_pcm(_PCM_FRAMES[1])
    for _ in range(100):
        if packets:
            break
        await asyncio.sleep(0.001)
    await radio.stop_rx()
    await radio.disconnect()

    assert [p.data for p in packets] == [_PCM_FRAMES[1]]
    assert packets[0].send_seq == 0


@pytest.mark.asyncio
async def test_failed_repeated_start_keeps_existing_stream() -> None:
    """MOR-2465: a rejected repeated start_rx must not orphan the running
    stream; the previous session keeps delivering frames."""
    radio, usb_audio = _make_radio()
    await radio.connect()
    first_packets: list[AudioPacket] = []
    first_cb = first_packets.append
    await radio.start_rx(first_cb)

    usb_audio.emit_rx_pcm(_PCM_FRAMES[0])
    await _drain_rx_delivery()
    assert [p.data for p in first_packets] == [_PCM_FRAMES[0]]

    delivery_before = radio._serial_rx_delivery
    second_packets: list[AudioPacket] = []
    with pytest.raises(RuntimeError, match="RX stream already started"):
        await radio.start_rx(second_packets.append)

    assert radio._serial_rx_delivery is delivery_before
    assert radio._opus_rx_user_callback is first_cb

    usb_audio.emit_rx_pcm(_PCM_FRAMES[1])
    await _drain_rx_delivery()
    await radio.stop_rx()
    await radio.disconnect()

    assert [p.data for p in first_packets] == _PCM_FRAMES
    assert second_packets == []


@pytest.mark.asyncio
async def test_failed_initial_start_leaves_no_active_delivery() -> None:
    """MOR-2465: a failed first start_rx arms no delivery and reports no
    active RX session; the next start delivers from a clean state."""
    usb_audio = _FailFirstStartUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    packets: list[AudioPacket] = []

    with pytest.raises(RuntimeError, match="RX start failed"):
        await radio.start_rx(packets.append)

    assert radio._serial_rx_delivery is None
    assert radio._opus_rx_user_callback is None

    await radio.start_rx(packets.append)
    usb_audio.emit_rx_pcm(_PCM_FRAMES[0])
    await _drain_rx_delivery()
    await radio.stop_rx()
    await radio.disconnect()

    assert [p.send_seq for p in packets] == [0]
    assert [p.data for p in packets] == [_PCM_FRAMES[0]]


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
    await _drain_rx_delivery()
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
