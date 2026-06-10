"""Tests for YaesuCatRadio AudioCapable integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.audio_bus import AudioBus
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.exceptions import AudioFormatError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_audio_driver():
    return create_autospec(UsbAudioDriver, instance=True)


@pytest.fixture
def radio(mock_audio_driver):
    with patch("rigplane.backends.yaesu_cat.radio.YaesuCatTransport") as MockTransport:
        transport = MagicMock()
        transport.connected = True
        transport.close = AsyncMock()
        MockTransport.return_value = transport
        r = YaesuCatRadio(
            device="/dev/ttyUSB0",
            audio_driver=mock_audio_driver,
        )
        return r


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_audio_driver_initialized(radio, mock_audio_driver):
    assert radio._audio_driver is mock_audio_driver


def test_audio_bus_none_initially(radio):
    assert radio._audio_bus is None


def test_audio_seq_starts_at_zero(radio):
    assert radio._audio_seq == 0


def test_audio_driver_default_construction():
    """UsbAudioDriver is created with correct args when not injected.

    The driver is imported lazily inside ``__init__`` (so the top-level
    ``rigplane`` import doesn't pull ``audio.backend``), so we patch the
    canonical module path rather than a re-export.

    Since MOR-578 the per-device knobs travel as ONE ``AudioDeviceConfig``
    carrier; the pinned effective values are unchanged.
    """
    from rigplane.audio.usb_driver import AudioDeviceConfig

    with (
        patch("rigplane.backends.yaesu_cat.radio.YaesuCatTransport"),
        patch("rigplane.audio.usb_driver.UsbAudioDriver") as MockDriver,
    ):
        MockDriver.return_value = MagicMock()
        YaesuCatRadio(
            device="/dev/ttyUSB0",
            rx_device="hw:1,0",
            tx_device="hw:1,1",
            audio_sample_rate=48000,
        )
        MockDriver.assert_called_once_with(
            AudioDeviceConfig(
                rx_device="hw:1,0",
                tx_device="hw:1,1",
                sample_rate=48000,
                channels=1,
                frame_ms=20,
                # FTX-1 profile selects the LEFT channel for the stereo→mono RX
                # downmix (MOR-508); USB RX audio is on L only.
                rx_audio_channel="left",
            ),
            serial_port="/dev/ttyUSB0",
            backend=None,
        )


# ---------------------------------------------------------------------------
# AudioBus lazy init
# ---------------------------------------------------------------------------


def test_audio_bus_created_lazily(radio):
    bus = radio.audio_bus
    assert isinstance(bus, AudioBus)
    assert radio._audio_bus is bus


def test_audio_bus_same_instance(radio):
    bus1 = radio.audio_bus
    bus2 = radio.audio_bus
    assert bus1 is bus2


# ---------------------------------------------------------------------------
# start_audio_rx_opus
# ---------------------------------------------------------------------------


async def test_start_audio_rx_opus_calls_driver(radio, mock_audio_driver):
    callback = MagicMock()
    await radio.start_audio_rx_opus(callback)
    mock_audio_driver.start_rx.assert_called_once()
    assert radio._opus_rx_user_callback is callback


async def test_start_audio_rx_opus_bad_callback(radio):
    with pytest.raises(TypeError, match="callback must be callable"):
        await radio.start_audio_rx_opus("not-callable")


async def test_start_audio_rx_opus_bad_jitter_type(radio):
    with pytest.raises(TypeError, match="jitter_depth must be an int"):
        await radio.start_audio_rx_opus(MagicMock(), jitter_depth="5")


async def test_start_audio_rx_opus_negative_jitter(radio):
    with pytest.raises(ValueError, match="jitter_depth must be >= 0"):
        await radio.start_audio_rx_opus(MagicMock(), jitter_depth=-1)


async def test_start_audio_rx_opus_emits_packets(radio, mock_audio_driver):
    received: list[AudioPacket] = []

    async def _start_rx(cb, **_kwargs):
        # Simulate driver calling back with a PCM frame
        cb(b"\x00" * 960)

    mock_audio_driver.start_rx.side_effect = _start_rx

    await radio.start_audio_rx_opus(received.append)

    assert len(received) == 1
    pkt = received[0]
    assert isinstance(pkt, AudioPacket)
    assert pkt.data == b"\x00" * 960
    assert pkt.ident == 0x9781
    assert pkt.send_seq == 0
    assert radio._audio_seq == 1


async def test_audio_seq_wraps(radio, mock_audio_driver):
    radio._audio_seq = 0xFFFF
    received: list[AudioPacket] = []

    async def _start_rx(cb, **_kwargs):
        cb(b"\x01" * 4)
        cb(b"\x02" * 4)

    mock_audio_driver.start_rx.side_effect = _start_rx
    await radio.start_audio_rx_opus(received.append)

    assert received[0].send_seq == 0xFFFF
    assert received[1].send_seq == 0
    assert radio._audio_seq == 1


# ---------------------------------------------------------------------------
# stop_audio_rx_opus
# ---------------------------------------------------------------------------


async def test_stop_audio_rx_opus(radio, mock_audio_driver):
    radio._opus_rx_user_callback = MagicMock()
    await radio.stop_audio_rx_opus()
    mock_audio_driver.stop_rx.assert_called_once()
    assert radio._opus_rx_user_callback is None


# ---------------------------------------------------------------------------
# start_audio_rx_pcm
# ---------------------------------------------------------------------------


async def test_start_audio_rx_pcm_calls_driver(radio, mock_audio_driver):
    callback = MagicMock()
    await radio.start_audio_rx_pcm(callback, sample_rate=48000, channels=1, frame_ms=20)
    mock_audio_driver.start_rx.assert_called_once_with(
        callback, sample_rate=48000, channels=1, frame_ms=20
    )
    assert radio._pcm_rx_user_callback is callback


async def test_start_audio_rx_pcm_bad_callback(radio):
    with pytest.raises(TypeError, match="callback must be callable"):
        await radio.start_audio_rx_pcm(42)


async def test_start_audio_rx_pcm_bad_sample_rate(radio):
    with pytest.raises(TypeError, match="sample_rate must be an int"):
        await radio.start_audio_rx_pcm(MagicMock(), sample_rate=48000.0)


async def test_start_audio_rx_pcm_bad_frame_size(radio):
    # 44100 * 3 = 132300 — not divisible by 1000
    with pytest.raises(AudioFormatError):
        await radio.start_audio_rx_pcm(MagicMock(), sample_rate=44100, frame_ms=3)


async def test_start_audio_rx_pcm_negative_jitter(radio):
    with pytest.raises(ValueError, match="jitter_depth must be >= 0"):
        await radio.start_audio_rx_pcm(MagicMock(), jitter_depth=-1)


# ---------------------------------------------------------------------------
# stop_audio_rx_pcm
# ---------------------------------------------------------------------------


async def test_stop_audio_rx_pcm(radio, mock_audio_driver):
    radio._pcm_rx_user_callback = MagicMock()
    await radio.stop_audio_rx_pcm()
    mock_audio_driver.stop_rx.assert_called_once()
    assert radio._pcm_rx_user_callback is None


# ---------------------------------------------------------------------------
# start_audio_tx_pcm / stop_audio_tx_pcm / _push_pcm_tx
# ---------------------------------------------------------------------------


async def test_start_audio_tx_pcm(radio, mock_audio_driver):
    await radio.start_audio_tx_pcm(sample_rate=48000, channels=1, frame_ms=20)
    mock_audio_driver.start_tx.assert_called_once_with(
        sample_rate=48000, channels=1, frame_ms=20
    )


async def test_start_audio_tx_pcm_bad_frame_size(radio):
    # 44100 * 3 = 132300 — not divisible by 1000
    with pytest.raises(AudioFormatError):
        await radio.start_audio_tx_pcm(sample_rate=44100, frame_ms=3)


async def test_stop_audio_tx_pcm(radio, mock_audio_driver):
    await radio.stop_audio_tx_pcm()
    mock_audio_driver.stop_tx.assert_called_once()


async def test_push_pcm_tx(radio, mock_audio_driver):
    frame = b"\xab" * 960
    await radio._push_pcm_tx(frame)
    mock_audio_driver._push_tx_pcm.assert_called_once_with(frame)


async def test_push_pcm_tx_not_bytes(radio):
    with pytest.raises(TypeError, match="frame must be bytes"):
        await radio._push_pcm_tx("string")


async def test_push_pcm_tx_empty(radio):
    with pytest.raises(ValueError, match="frame must not be empty"):
        await radio._push_pcm_tx(b"")


# ---------------------------------------------------------------------------
# disconnect stops audio driver
# ---------------------------------------------------------------------------


async def test_disconnect_stops_audio(radio, mock_audio_driver):
    await radio.disconnect()
    mock_audio_driver.stop_rx.assert_called_once()
    mock_audio_driver.stop_tx.assert_called_once()
