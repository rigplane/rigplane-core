"""Tests for TapRegistry — multi-consumer PCM audio fan-out."""

from __future__ import annotations

import logging

import pytest

from rigplane.dsp.tap_registry import TapHandle, TapRegistry


class TestTapRegistry:
    """Core TapRegistry functionality."""

    def test_register_returns_handle(self) -> None:
        reg = TapRegistry()
        handle = reg.register("test", lambda b: None)
        assert isinstance(handle, TapHandle)

    def test_feed_fans_out_to_multiple_taps(self) -> None:
        reg = TapRegistry()
        received_a: list[bytes] = []
        received_b: list[bytes] = []
        reg.register("a", received_a.append)
        reg.register("b", received_b.append)
        data = b"\x01\x02\x03"
        reg.feed(data)
        assert received_a == [data]
        assert received_b == [data]

    def test_unregister_removes_only_target(self) -> None:
        reg = TapRegistry()
        received_a: list[bytes] = []
        received_b: list[bytes] = []
        handle_a = reg.register("a", received_a.append)
        reg.register("b", received_b.append)
        reg.unregister(handle_a)
        reg.feed(b"\xff")
        assert received_a == []
        assert received_b == [b"\xff"]

    def test_feed_no_taps_is_noop(self) -> None:
        reg = TapRegistry()
        # Should not raise
        reg.feed(b"\x00" * 100)

    def test_active_property(self) -> None:
        reg = TapRegistry()
        assert reg.active is False
        handle = reg.register("x", lambda b: None)
        assert reg.active is True
        reg.unregister(handle)
        assert reg.active is False

    def test_passive_taps_receive_pcm_without_demand(self) -> None:
        reg = TapRegistry()
        received: list[bytes] = []
        assert reg.demand is False
        passive = reg.register("analyzer", received.append, passive=True)
        assert reg.active is True
        assert reg.demand is False
        demanding = reg.register("cw", received.append)
        assert reg.demand is True
        reg.feed(b"\x01\x00")
        assert received == [b"\x01\x00", b"\x01\x00"]
        reg.unregister(demanding)
        reg.unregister(demanding)
        assert reg.active is True
        assert reg.demand is False
        reg.unregister(passive)
        assert reg.active is False
        assert reg.demand is False

    def test_feed_catches_tap_exceptions(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg = TapRegistry()
        received: list[bytes] = []

        def bad_tap(data: bytes) -> None:
            raise RuntimeError("boom")

        reg.register("bad", bad_tap)
        reg.register("good", received.append)

        with caplog.at_level(logging.WARNING):
            reg.feed(b"\xab")

        # Good tap still received the data
        assert received == [b"\xab"]
        assert "boom" in caplog.text

    def test_unregister_unknown_handle_is_noop(self) -> None:
        reg = TapRegistry()
        handle = reg.register("x", lambda b: None)
        reg.unregister(handle)
        # Double unregister should not raise
        reg.unregister(handle)

    def test_duplicate_name_allowed(self) -> None:
        """Multiple taps with the same name should coexist."""
        reg = TapRegistry()
        received_1: list[bytes] = []
        received_2: list[bytes] = []
        reg.register("same", received_1.append)
        reg.register("same", received_2.append)
        reg.feed(b"\x01")
        assert received_1 == [b"\x01"]
        assert received_2 == [b"\x01"]


class TestSetPcmTapCompat:
    """Test set_pcm_tap compatibility wrapper on AudioBroadcaster."""

    def test_set_tap_then_feed(self) -> None:
        """set_pcm_tap(cb) registers; feed delivers data."""
        from rigplane.web.handlers.audio import AudioBroadcaster

        broadcaster = AudioBroadcaster(radio=None)
        received: list[bytes] = []
        broadcaster.set_pcm_tap(received.append)
        broadcaster._tap_registry.feed(b"\xaa\xbb")
        assert received == [b"\xaa\xbb"]

    def test_set_tap_none_unregisters(self) -> None:
        """set_pcm_tap(None) unregisters the legacy tap."""
        from rigplane.web.handlers.audio import AudioBroadcaster

        broadcaster = AudioBroadcaster(radio=None)
        received: list[bytes] = []
        broadcaster.set_pcm_tap(received.append)
        assert broadcaster._tap_registry.active is True
        broadcaster.set_pcm_tap(None)
        assert broadcaster._tap_registry.active is False
        broadcaster._tap_registry.feed(b"\xcc")
        assert received == []

    def test_set_tap_replaces_previous(self) -> None:
        """Calling set_pcm_tap twice replaces the previous legacy tap."""
        from rigplane.web.handlers.audio import AudioBroadcaster

        broadcaster = AudioBroadcaster(radio=None)
        received_1: list[bytes] = []
        received_2: list[bytes] = []
        broadcaster.set_pcm_tap(received_1.append)
        broadcaster.set_pcm_tap(received_2.append)
        broadcaster._tap_registry.feed(b"\xdd")
        assert received_1 == []
        assert received_2 == [b"\xdd"]

    def test_passive_replacement_and_removal_share_post_dsp_registry(self) -> None:
        from rigplane.audio.bus import STAGE_RX_POST_DSP
        from rigplane.web.handlers.audio import AudioBroadcaster

        broadcaster = AudioBroadcaster(radio=None)
        received_1: list[bytes] = []
        received_2: list[bytes] = []
        registry = broadcaster.taps(STAGE_RX_POST_DSP)
        assert registry is broadcaster._tap_registry
        broadcaster.set_passive_pcm_tap(received_1.append)
        broadcaster.set_passive_pcm_tap(received_2.append)
        registry.feed(b"\x01\x00")
        assert received_1 == []
        assert received_2 == [b"\x01\x00"]
        assert registry.active is True
        assert registry.demand is False
        broadcaster.set_pcm_tap(lambda pcm: None)
        assert registry.demand is True
        broadcaster.set_passive_pcm_tap(None)
        assert registry.active is True
        assert registry.demand is True
        broadcaster.set_pcm_tap(None)
        assert registry.active is False
