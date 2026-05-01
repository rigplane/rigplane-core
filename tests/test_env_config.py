"""Tests for env_config — environment variable configuration helpers."""

import logging
from unittest.mock import MagicMock

import pytest


def _reload_env_config(monkeypatch, env: dict[str, str]):
    """Import env_config with the given environment variables set."""
    import icom_lan.env_config as mod

    for key, val in env.items():
        monkeypatch.setenv(key, val)
    # Force re-evaluation of module-level helpers (functions read env at call time)
    return mod


class TestGetAudioSampleRate:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_SAMPLE_RATE", raising=False)
        from icom_lan.env_config import get_audio_sample_rate

        assert get_audio_sample_rate() == 48000

    @pytest.mark.parametrize("rate", [8000, 16000, 24000, 48000])
    def test_valid_values(self, monkeypatch, rate):
        monkeypatch.setenv("ICOM_AUDIO_SAMPLE_RATE", str(rate))
        from icom_lan.env_config import get_audio_sample_rate

        assert get_audio_sample_rate() == rate

    def test_invalid_string_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_SAMPLE_RATE", "not_a_number")
        from icom_lan.env_config import get_audio_sample_rate

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_sample_rate()
        assert result == 48000
        assert "ICOM_AUDIO_SAMPLE_RATE" in caplog.text

    def test_unsupported_rate_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_SAMPLE_RATE", "22050")
        from icom_lan.env_config import get_audio_sample_rate

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_sample_rate()
        assert result == 48000
        assert "ICOM_AUDIO_SAMPLE_RATE" in caplog.text


class TestGetAudioBroadcasterHighWatermark:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK", raising=False)
        from icom_lan.env_config import get_audio_broadcaster_high_watermark

        assert get_audio_broadcaster_high_watermark() == 10

    def test_valid_override(self, monkeypatch):
        monkeypatch.setenv("ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK", "25")
        from icom_lan.env_config import get_audio_broadcaster_high_watermark

        assert get_audio_broadcaster_high_watermark() == 25

    def test_invalid_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK", "nope")
        from icom_lan.env_config import get_audio_broadcaster_high_watermark

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_broadcaster_high_watermark()
        assert result == 10
        assert "ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK" in caplog.text

    def test_zero_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK", "0")
        from icom_lan.env_config import get_audio_broadcaster_high_watermark

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_broadcaster_high_watermark()
        assert result == 10


class TestGetAudioClientHighWatermark:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_CLIENT_HIGH_WATERMARK", raising=False)
        from icom_lan.env_config import get_audio_client_high_watermark

        assert get_audio_client_high_watermark() == 10

    def test_valid_override(self, monkeypatch):
        monkeypatch.setenv("ICOM_AUDIO_CLIENT_HIGH_WATERMARK", "25")
        from icom_lan.env_config import get_audio_client_high_watermark

        assert get_audio_client_high_watermark() == 25

    def test_invalid_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_CLIENT_HIGH_WATERMARK", "??")
        from icom_lan.env_config import get_audio_client_high_watermark

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_client_high_watermark()
        assert result == 10
        assert "ICOM_AUDIO_CLIENT_HIGH_WATERMARK" in caplog.text

    def test_zero_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_CLIENT_HIGH_WATERMARK", "0")
        from icom_lan.env_config import get_audio_client_high_watermark

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_client_high_watermark()
        assert result == 10


class TestGetAudioRxJitterBounds:
    def test_floor_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", raising=False)
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", raising=False)
        from icom_lan.env_config import get_audio_rx_jitter_floor_ms

        assert get_audio_rx_jitter_floor_ms() == 50

    def test_ceiling_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", raising=False)
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", raising=False)
        from icom_lan.env_config import get_audio_rx_jitter_ceiling_ms

        assert get_audio_rx_jitter_ceiling_ms() == 300

    def test_floor_valid_override(self, monkeypatch):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "80")
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", raising=False)
        from icom_lan.env_config import get_audio_rx_jitter_floor_ms

        assert get_audio_rx_jitter_floor_ms() == 80

    def test_ceiling_valid_override(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", raising=False)
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "500")
        from icom_lan.env_config import get_audio_rx_jitter_ceiling_ms

        assert get_audio_rx_jitter_ceiling_ms() == 500

    def test_floor_invalid_string_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "bad")
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", raising=False)
        from icom_lan.env_config import get_audio_rx_jitter_floor_ms

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_rx_jitter_floor_ms()
        assert result == 50
        assert "ICOM_AUDIO_RX_JITTER_FLOOR_MS" in caplog.text

    def test_ceiling_invalid_string_falls_back(self, monkeypatch, caplog):
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", raising=False)
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "bad")
        from icom_lan.env_config import get_audio_rx_jitter_ceiling_ms

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            result = get_audio_rx_jitter_ceiling_ms()
        assert result == 300
        assert "ICOM_AUDIO_RX_JITTER_CEILING_MS" in caplog.text

    def test_floor_zero_falls_back(self, monkeypatch):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "0")
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", raising=False)
        from icom_lan.env_config import get_audio_rx_jitter_floor_ms

        assert get_audio_rx_jitter_floor_ms() == 50

    def test_ceiling_zero_falls_back(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", raising=False)
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "0")
        from icom_lan.env_config import get_audio_rx_jitter_ceiling_ms

        assert get_audio_rx_jitter_ceiling_ms() == 300

    def test_cross_floor_greater_than_ceiling_falls_back_both(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "200")
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "100")
        from icom_lan.env_config import (
            get_audio_rx_jitter_ceiling_ms,
            get_audio_rx_jitter_floor_ms,
        )

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            floor = get_audio_rx_jitter_floor_ms()
            ceiling = get_audio_rx_jitter_ceiling_ms()
        assert floor == 50
        assert ceiling == 300
        assert "ICOM_AUDIO_RX_JITTER_FLOOR_MS" in caplog.text
        assert "ICOM_AUDIO_RX_JITTER_CEILING_MS" in caplog.text

    def test_cross_ceiling_exceeds_2000_falls_back_both(self, monkeypatch, caplog):
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", raising=False)
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "2001")
        from icom_lan.env_config import (
            get_audio_rx_jitter_ceiling_ms,
            get_audio_rx_jitter_floor_ms,
        )

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            floor = get_audio_rx_jitter_floor_ms()
            ceiling = get_audio_rx_jitter_ceiling_ms()
        assert floor == 50
        assert ceiling == 300
        assert "ICOM_AUDIO_RX_JITTER_CEILING_MS" in caplog.text

    def test_cross_floor_equals_ceiling_allowed(self, monkeypatch):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "100")
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "100")
        from icom_lan.env_config import (
            get_audio_rx_jitter_ceiling_ms,
            get_audio_rx_jitter_floor_ms,
        )

        assert get_audio_rx_jitter_floor_ms() == 100
        assert get_audio_rx_jitter_ceiling_ms() == 100

    def test_cross_ceiling_exactly_2000_allowed(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", raising=False)
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "2000")
        from icom_lan.env_config import get_audio_rx_jitter_ceiling_ms

        assert get_audio_rx_jitter_ceiling_ms() == 2000

    def test_floor_valid_ceiling_invalid_string_reverts_both(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "100")
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "bad")
        from icom_lan.env_config import (
            get_audio_rx_jitter_ceiling_ms,
            get_audio_rx_jitter_floor_ms,
        )

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            floor = get_audio_rx_jitter_floor_ms()
            ceiling = get_audio_rx_jitter_ceiling_ms()
        assert floor == 50
        assert ceiling == 300
        assert "ICOM_AUDIO_RX_JITTER_CEILING_MS" in caplog.text

    def test_floor_invalid_ceiling_valid_reverts_both(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "bad")
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "500")
        from icom_lan.env_config import (
            get_audio_rx_jitter_ceiling_ms,
            get_audio_rx_jitter_floor_ms,
        )

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            floor = get_audio_rx_jitter_floor_ms()
            ceiling = get_audio_rx_jitter_ceiling_ms()
        assert floor == 50
        assert ceiling == 300
        assert "ICOM_AUDIO_RX_JITTER_FLOOR_MS" in caplog.text

    def test_floor_valid_ceiling_zero_reverts_both(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "100")
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "0")
        from icom_lan.env_config import (
            get_audio_rx_jitter_ceiling_ms,
            get_audio_rx_jitter_floor_ms,
        )

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            floor = get_audio_rx_jitter_floor_ms()
            ceiling = get_audio_rx_jitter_ceiling_ms()
        assert floor == 50
        assert ceiling == 300
        assert "ICOM_AUDIO_RX_JITTER_CEILING_MS" in caplog.text

    def test_floor_zero_ceiling_valid_reverts_both(self, monkeypatch, caplog):
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_FLOOR_MS", "0")
        monkeypatch.setenv("ICOM_AUDIO_RX_JITTER_CEILING_MS", "500")
        from icom_lan.env_config import (
            get_audio_rx_jitter_ceiling_ms,
            get_audio_rx_jitter_floor_ms,
        )

        with caplog.at_level(logging.WARNING, logger="icom_lan.env_config"):
            floor = get_audio_rx_jitter_floor_ms()
            ceiling = get_audio_rx_jitter_ceiling_ms()
        assert floor == 50
        assert ceiling == 300
        assert "ICOM_AUDIO_RX_JITTER_FLOOR_MS" in caplog.text


class TestHandlerIntegration:
    """Verify that env vars are picked up when handlers are instantiated."""

    def test_audio_broadcaster_uses_env_watermark(self, monkeypatch):
        monkeypatch.setenv("ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK", "25")
        from icom_lan.web.handlers import AudioBroadcaster

        broadcaster = AudioBroadcaster(radio=None)
        assert broadcaster.HIGH_WATERMARK == 25

    def test_audio_broadcaster_default_watermark(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK", raising=False)
        from icom_lan.web.handlers import AudioBroadcaster

        broadcaster = AudioBroadcaster(radio=None)
        assert broadcaster.HIGH_WATERMARK == 10

    def test_audio_handler_uses_env_watermark(self, monkeypatch):
        monkeypatch.setenv("ICOM_AUDIO_CLIENT_HIGH_WATERMARK", "30")
        from icom_lan.web.handlers import AudioHandler
        from icom_lan.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = AudioHandler(mock_ws, radio=None)
        assert handler.HIGH_WATERMARK == 30

    def test_audio_handler_default_watermark(self, monkeypatch):
        monkeypatch.delenv("ICOM_AUDIO_CLIENT_HIGH_WATERMARK", raising=False)
        from icom_lan.web.handlers import AudioHandler
        from icom_lan.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = AudioHandler(mock_ws, radio=None)
        assert handler.HIGH_WATERMARK == 10
