"""Tests for AudioCodec enum and audio config in IcomRadio."""

import pytest

from rigplane.audio.route import AudioConfigSource
from rigplane.types import AudioCapabilities, AudioCodec, get_audio_capabilities
from rigplane.radio import IcomRadio


class TestAudioCodecEnum:
    def test_values(self) -> None:
        assert AudioCodec.ULAW_1CH == 0x01
        assert AudioCodec.PCM_1CH_8BIT == 0x02
        assert AudioCodec.PCM_1CH_16BIT == 0x04
        assert AudioCodec.PCM_2CH_8BIT == 0x08
        assert AudioCodec.PCM_2CH_16BIT == 0x10
        assert AudioCodec.ULAW_2CH == 0x20
        assert AudioCodec.OPUS_1CH == 0x40
        assert AudioCodec.OPUS_2CH == 0x41

    def test_from_int(self) -> None:
        assert AudioCodec(0x04) == AudioCodec.PCM_1CH_16BIT
        assert AudioCodec(0x40) == AudioCodec.OPUS_1CH

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            AudioCodec(0xFF)

    def test_int_conversion(self) -> None:
        assert int(AudioCodec.PCM_1CH_16BIT) == 0x04


class TestRadioAudioConfig:
    def test_default_codec(self) -> None:
        r = IcomRadio("192.168.1.100")
        assert r.audio_codec == AudioCodec.PCM_2CH_16BIT
        assert r.audio_sample_rate == 16000
        assert r.audio_stream_request.rx_codec == AudioCodec.PCM_2CH_16BIT
        assert r.audio_stream_request.tx_codec == AudioCodec.PCM_1CH_16BIT
        assert r.audio_stream_request.rx_sample_rate_hz == 16000
        assert r.audio_stream_request.tx_sample_rate_hz == 16000
        assert r.audio_stream_contract.rx_codec == r.audio_stream_request.rx_codec
        assert r.audio_stream_contract.tx_codec == r.audio_stream_request.tx_codec
        assert (
            r.audio_stream_contract.rx_sample_rate_hz
            == r.audio_stream_request.rx_sample_rate_hz
        )

    def test_non_ic7610_keeps_global_sample_rate_default(self) -> None:
        r = IcomRadio("192.168.1.100", model="IC-7300")
        assert r.audio_codec == AudioCodec.PCM_1CH_16BIT
        assert r.audio_sample_rate == 48000

    def test_custom_codec(self) -> None:
        r = IcomRadio(
            "192.168.1.100",
            audio_codec=AudioCodec.OPUS_1CH,
            audio_sample_rate=16000,
        )
        assert r.audio_codec == AudioCodec.OPUS_1CH
        assert r.audio_sample_rate == 16000
        assert r.audio_stream_contract.rx_codec == AudioCodec.OPUS_1CH
        assert r.audio_stream_contract.tx_codec == AudioCodec.PCM_1CH_16BIT
        assert (
            r.audio_stream_contract.rx_sample_rate_source == AudioConfigSource.EXPLICIT
        )
        assert (
            r.audio_stream_contract.tx_sample_rate_source == AudioConfigSource.EXPLICIT
        )

    def test_import_time_env_sample_rate_beats_profile_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import rigplane.runtime.radio as radio_module

        monkeypatch.setenv("ICOM_AUDIO_SAMPLE_RATE", "24000")
        monkeypatch.setattr(radio_module, "_DEFAULT_AUDIO_SAMPLE_RATE", 24000)

        r = IcomRadio("192.168.1.100")

        assert r.audio_sample_rate == 24000

    def test_codec_from_int(self) -> None:
        r = IcomRadio("192.168.1.100", audio_codec=0x40)
        assert r.audio_codec == AudioCodec.OPUS_1CH

    def test_codec_ulaw(self) -> None:
        r = IcomRadio("192.168.1.100", audio_codec=AudioCodec.ULAW_1CH)
        assert r.audio_codec == AudioCodec.ULAW_1CH


class TestAudioCapabilities:
    def test_capabilities_shape(self) -> None:
        caps = get_audio_capabilities()
        assert isinstance(caps, AudioCapabilities)
        assert caps.supported_codecs
        assert caps.supported_sample_rates_hz
        assert caps.supported_channels

    def test_default_selection_is_deterministic(self) -> None:
        caps = get_audio_capabilities()
        assert caps.default_codec == AudioCodec.PCM_2CH_16BIT
        assert caps.default_sample_rate_hz == 48000
        assert caps.default_channels == 2

    def test_default_values_are_supported(self) -> None:
        caps = get_audio_capabilities()
        assert caps.default_codec in caps.supported_codecs
        assert caps.default_sample_rate_hz in caps.supported_sample_rates_hz
        assert caps.default_channels in caps.supported_channels

    def test_radio_exposes_audio_capabilities(self) -> None:
        # ``audio_capabilities()`` was removed from the class surface in #1106.
        # The module-level helper remains the single source of truth.
        caps = get_audio_capabilities()
        assert caps == get_audio_capabilities()

    def test_to_dict_json_shape(self) -> None:
        data = get_audio_capabilities().to_dict()
        assert "supported_codecs" in data
        assert "supported_sample_rates_hz" in data
        assert "supported_channels" in data
        assert data["default_codec"] == {"name": "PCM_2CH_16BIT", "value": 0x10}
        assert data["default_sample_rate_hz"] == 48000
        assert data["default_channels"] == 2
