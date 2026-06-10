"""MOR-579: radio-owned AudioSession singleton (epic MOR-562 step 9b).

MOR-577 had the bridge build its OWN ``AudioSession(radio)``. Before the
web TX handler (step 13) or the poller PTT hooks (step 12) also go through
the session, each consumer building a private session would split the TX
refcount — two sessions per radio arm/stop radio TX independently →
premature stop / double-arm. The session must be ONE per radio: a lazy
cached ``radio.audio_session`` property (mirroring the lazy ``audio_bus``),
with the bridge preferring it via a duck-typed lookup and falling back to
constructing its own for bare test doubles.
"""

from __future__ import annotations

import types

from _order_sensitive_radios import LanLikeRadio

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
)
from rigplane.audio.bridge import AudioBridge
from rigplane.audio.session import AudioSession
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.runtime.radio import IcomRadio

# ---------------------------------------------------------------------------
# radio.audio_session — lazy cached singleton on real backends
# ---------------------------------------------------------------------------


def test_icom_audio_session_is_cached_singleton() -> None:
    """Repeated access returns the SAME session object (lazy + cached)."""
    radio = IcomRadio("192.168.1.100")
    session = radio.audio_session
    assert isinstance(session, AudioSession)
    assert radio.audio_session is session


def test_icom_audio_session_wraps_shared_audio_bus() -> None:
    """The radio-owned session wraps the radio's shared AudioBus."""
    radio = IcomRadio("192.168.1.100")
    assert radio.audio_session.bus is radio.audio_bus


def test_yaesu_audio_session_is_cached_singleton() -> None:
    radio = YaesuCatRadio(
        device="/dev/fake0",
        audio_driver=types.SimpleNamespace(),  # type: ignore[arg-type]
    )
    session = radio.audio_session
    assert isinstance(session, AudioSession)
    assert radio.audio_session is session


def test_yaesu_audio_session_wraps_shared_audio_bus() -> None:
    radio = YaesuCatRadio(
        device="/dev/fake0",
        audio_driver=types.SimpleNamespace(),  # type: ignore[arg-type]
    )
    assert radio.audio_session.bus is radio.audio_bus


# ---------------------------------------------------------------------------
# AudioBridge uses the radio-owned session (not a private one)
# ---------------------------------------------------------------------------


class _SessionOwningLanRadio(LanLikeRadio):
    """LAN-like stub exposing the radio-owned ``audio_session`` property."""

    def __init__(self) -> None:
        super().__init__()
        self._audio_session: AudioSession | None = None

    @property
    def audio_session(self) -> AudioSession:
        if self._audio_session is None:
            self._audio_session = AudioSession(self)
        return self._audio_session


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


async def test_bridge_uses_radio_owned_session() -> None:
    """With a radio exposing ``audio_session``, the bridge must share it —
    never build a second session with an independent TX refcount."""
    radio = _SessionOwningLanRadio()
    bridge = AudioBridge(radio, device_name="BlackHole", backend=_bridge_backend())
    await bridge.start()
    try:
        assert bridge._session is radio.audio_session
        assert radio.audio_session.rx_demand == 1
        assert radio.audio_session.tx_demand == 1
    finally:
        await bridge.stop()
    assert radio.audio_session.rx_demand == 0
    assert radio.audio_session.tx_demand == 0


async def test_bridge_falls_back_to_own_session_without_property() -> None:
    """Duck-typed fallback: a radio double WITHOUT ``audio_session`` still
    lets the bridge construct one (no crash, behavior unchanged)."""
    radio = LanLikeRadio()
    assert not hasattr(radio, "audio_session")
    bridge = AudioBridge(radio, device_name="BlackHole", backend=_bridge_backend())
    await bridge.start()
    try:
        assert isinstance(bridge._session, AudioSession)
        assert bridge._session.bus is radio.audio_bus
    finally:
        await bridge.stop()
