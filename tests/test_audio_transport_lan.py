"""AudioTransport neutral methods on the Icom LAN runtime (MOR-539).

Step 6/12 of the MOR-532 epic: ``IcomRadio`` gains the codec-neutral
``start_rx``/``stop_rx``/``start_tx``/``push_tx``/``stop_tx`` methods and the
legacy opus-family methods become one-line delegates onto them.

Uses :class:`FakeAudioStream` (same harness as ``test_audio_recovery``) —
no MagicMock dataclasses. All radio I/O is faked; no hardware needed.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from _audio_stream_fake import FakeAudioStream

from rigplane.core.radio_protocol import AudioTransport
from rigplane.core.types import AudioCodec
from rigplane.radio import IcomRadio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_radio() -> tuple[IcomRadio, FakeAudioStream]:
    """Build a 'connected' IcomRadio with a FakeAudioStream installed."""
    radio = IcomRadio("192.168.1.100")
    radio._connected = True
    radio._civ_transport = MagicMock()
    stream = FakeAudioStream()
    radio._audio_stream = stream
    return radio, stream


def _use_opus_tx_contract(radio: IcomRadio) -> None:
    """Re-pin the negotiated contract to an Opus TX codec."""
    radio._audio_stream_contract = dataclasses.replace(
        radio.audio_stream_contract, tx_codec=AudioCodec.OPUS_1CH
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestAudioTransportConformance:
    def test_icom_radio_satisfies_audio_transport(self) -> None:
        radio = IcomRadio("192.168.1.100")
        assert isinstance(radio, AudioTransport)

    def test_neutral_methods_present(self) -> None:
        radio = IcomRadio("192.168.1.100")
        for name in ("start_rx", "stop_rx", "start_tx", "push_tx", "stop_tx"):
            assert callable(getattr(radio, name))


# ---------------------------------------------------------------------------
# Delegation equivalence: legacy opus methods == neutral methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRxDelegation:
    async def test_start_rx_effects(self) -> None:
        radio, stream = _make_radio()
        cb = lambda pkt: None  # noqa: E731

        await radio.start_rx(cb, jitter_depth=7)

        assert stream.start_rx_count == 1
        assert stream.last_start_rx_callback is cb
        assert stream.last_start_rx_jitter_depth == 7
        # Recovery-critical bookkeeping (read by _audio_recovery.capture_snapshot).
        assert radio._opus_rx_user_callback is cb
        assert radio._opus_rx_jitter_depth == 7

    async def test_legacy_start_matches_neutral(self) -> None:
        cb = lambda pkt: None  # noqa: E731
        legacy, legacy_stream = _make_radio()
        neutral, neutral_stream = _make_radio()

        await legacy.start_audio_rx_opus(cb, jitter_depth=3)
        await neutral.start_rx(cb, jitter_depth=3)

        for stream in (legacy_stream, neutral_stream):
            assert stream.start_rx_count == 1
            assert stream.last_start_rx_callback is cb
            assert stream.last_start_rx_jitter_depth == 3
        for radio in (legacy, neutral):
            assert radio._opus_rx_user_callback is cb
            assert radio._opus_rx_jitter_depth == 3

    async def test_stop_matches(self) -> None:
        for method in ("stop_rx", "stop_audio_rx_opus"):
            radio, stream = _make_radio()
            radio._opus_rx_user_callback = lambda pkt: None
            await getattr(radio, method)()
            assert stream.stop_rx_count == 1
            assert radio._opus_rx_user_callback is None


@pytest.mark.asyncio
class TestTxDelegation:
    async def test_push_matches(self) -> None:
        for method in ("push_tx", "push_audio_tx_opus"):
            radio, stream = _make_radio()
            await getattr(radio, method)(b"\x01\x02\x03")
            assert stream.tx_frames == [b"\x01\x02\x03"]

    async def test_push_raises_without_stream(self) -> None:
        for method in ("push_tx", "push_audio_tx_opus"):
            radio, _ = _make_radio()
            radio._audio_stream = None
            with pytest.raises(RuntimeError, match="Audio TX not started"):
                await getattr(radio, method)(b"\x00")

    async def test_stop_matches(self) -> None:
        for method in ("stop_tx", "stop_audio_tx_opus"):
            radio, stream = _make_radio()
            radio._pcm_tx_fmt = (48000, 1, 20)
            await getattr(radio, method)()
            assert stream.stop_tx_count == 1
            assert radio._pcm_tx_fmt is None


# ---------------------------------------------------------------------------
# start_tx routes per audio_tx_codec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStartTxRouting:
    async def test_pcm_contract_routes_via_pcm_path(self) -> None:
        radio, stream = _make_radio()
        assert radio.audio_tx_codec == AudioCodec.PCM_1CH_16BIT

        await radio.start_tx()

        assert stream.start_tx_count == 1
        contract = radio.audio_stream_contract
        assert radio._pcm_tx_fmt == (
            contract.tx_sample_rate_hz,
            contract.tx_channels,
            20,
        )

    async def test_opus_contract_routes_via_opus_path(self) -> None:
        radio, stream = _make_radio()
        _use_opus_tx_contract(radio)
        assert radio.audio_tx_codec == AudioCodec.OPUS_1CH

        await radio.start_tx()

        assert stream.start_tx_count == 1
        assert radio._pcm_tx_fmt is None

    async def test_legacy_opus_start_never_sets_pcm_fmt(self) -> None:
        """Recovery-critical: start_audio_tx_opus must keep _pcm_tx_fmt None
        even when the negotiated TX codec is PCM (the Icom LAN default), so
        AudioRecoveryRuntime snapshots keep pcm_mode=False for opus users."""
        radio, stream = _make_radio()
        assert radio.audio_tx_codec == AudioCodec.PCM_1CH_16BIT

        await radio.start_audio_tx_opus()

        assert stream.start_tx_count == 1
        assert radio._pcm_tx_fmt is None
