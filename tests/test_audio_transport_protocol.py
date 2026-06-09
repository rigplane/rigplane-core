"""Tests for the codec-neutral ``AudioTransport`` protocol (MOR-538).

Step 5/12 of the MOR-532 AudioTransport epic: a NEW ``@runtime_checkable``
Protocol next to ``AudioCapable``. This module verifies:

1. a minimal fake implementing every member satisfies
   ``isinstance(fake, AudioTransport)``;
2. a fake missing one method (``push_tx``) does NOT;
3. ``AudioCapable``'s member set is byte-frozen — the MOR-538 design
   decision is that the legacy protocol stays untouched (it is
   isinstance-checked in ``web/runtime_helpers.py``; adding members
   would change runtime narrowing results);
4. the symbol is wired through the same re-export paths as the other
   capability protocols (canonical ``rigplane.core.radio_protocol``,
   legacy ``rigplane.radio_protocol`` shim, top-level ``rigplane``).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from rigplane.core.radio_protocol import AudioCapable, AudioTransport
from rigplane.core.types import AudioCodec


class _FakeBus:
    """Stand-in for AudioBus; AudioTransport only requires presence."""


class _FullTransport:
    """Minimal object implementing every AudioTransport member."""

    @property
    def audio_bus(self) -> Any:
        return _FakeBus()

    @property
    def audio_codec(self) -> AudioCodec:
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_tx_codec(self) -> AudioCodec:
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_sample_rate(self) -> int:
        return 48_000

    @property
    def audio_duplex_mode(self) -> str:
        return "full"

    async def start_rx(self, callback: Callable[..., Awaitable[None]]) -> None: ...

    async def stop_rx(self) -> None: ...

    async def start_tx(self) -> None: ...

    async def push_tx(self, data: bytes) -> None: ...

    async def stop_tx(self) -> None: ...


class _MissingPushTx:
    """Same surface as _FullTransport but without ``push_tx``."""

    @property
    def audio_bus(self) -> Any:
        return _FakeBus()

    @property
    def audio_codec(self) -> AudioCodec:
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_tx_codec(self) -> AudioCodec:
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_sample_rate(self) -> int:
        return 48_000

    @property
    def audio_duplex_mode(self) -> str:
        return "full"

    async def start_rx(self, callback: Callable[..., Awaitable[None]]) -> None: ...

    async def stop_rx(self) -> None: ...

    async def start_tx(self) -> None: ...

    async def stop_tx(self) -> None: ...


def test_full_fake_satisfies_audio_transport() -> None:
    assert isinstance(_FullTransport(), AudioTransport)


def test_fake_missing_push_tx_does_not_satisfy() -> None:
    assert not isinstance(_MissingPushTx(), AudioTransport)


def test_audio_capable_member_set_frozen() -> None:
    """AudioCapable is byte-frozen by the MOR-538 design decision.

    The legacy ``*_opus``/``*_pcm`` methods remain as permanent
    back-compat shims; the neutral surface lives on ``AudioTransport``.
    If this test fails, someone changed ``AudioCapable`` — that breaks
    the isinstance checks in ``web/runtime_helpers.py``.
    """
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


def test_reexported_through_capability_protocol_paths() -> None:
    """Same wiring as AudioCapable: canonical, legacy shim, top level."""
    import rigplane
    import rigplane.core.radio_protocol as canonical
    import rigplane.radio_protocol as legacy

    assert legacy.AudioTransport is canonical.AudioTransport
    assert rigplane.AudioTransport is canonical.AudioTransport
    assert "AudioTransport" in canonical.__all__
    assert "AudioTransport" in rigplane.__all__
