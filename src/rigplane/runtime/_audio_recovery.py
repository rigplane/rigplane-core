"""Audio snapshot/resume logic for IcomRadio reconnect scenarios.

Session-managed audio (MOR-586, ADR §3.4 rule 4 — "one recovery loop")
recovers through :meth:`AudioSession.reestablish` from the session's LIVE
demand; the legacy ``*_opus``/``*_pcm`` snapshot replay remains only for
direct legacy consumers with no session demand.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from typing import TYPE_CHECKING, Any, Callable

from rigplane.audio import AudioPacket, AudioState

if TYPE_CHECKING:
    from ._runtime_protocols import AudioRuntimeHost

logger = logging.getLogger(__name__)

__all__ = [
    "AudioRecoveryRuntime",
    "AudioRecoveryState",
    "_AudioSnapshot",
]


class AudioRecoveryState(enum.Enum):
    """State emitted by the ``on_audio_recovery`` callback."""

    RECOVERING = "recovering"
    RECOVERED = "recovered"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True, slots=True)
class _AudioSnapshot:
    """Captured audio state before disconnect for auto-recovery."""

    rx_active: bool
    tx_active: bool
    pcm_mode: bool
    pcm_rx_callback: "Callable[[bytes | None], None] | None"
    opus_rx_callback: "Callable[[AudioPacket | None], None] | None"
    pcm_params: tuple[int, int, int] | None
    jitter_depth: int


#: Marker snapshot for session-managed audio (MOR-586): the reconnect hooks
#: gate recovery on ``capture_snapshot() is not None``, so live session
#: demand must produce a snapshot even when the legacy stream state looks
#: idle. The legacy fields are inert — ``recover`` re-derives everything
#: from the session's live demand.
_SESSION_MANAGED_SNAPSHOT = _AudioSnapshot(
    rx_active=False,
    tx_active=False,
    pcm_mode=False,
    pcm_rx_callback=None,
    opus_rx_callback=None,
    pcm_params=None,
    jitter_depth=0,
)


class AudioRecoveryRuntime:
    """Composed audio recovery runtime for IcomRadio reconnect scenarios."""

    def __init__(self, host: "AudioRuntimeHost") -> None:
        self._host = host

    def _session_with_demand(self) -> Any:
        """The host's AudioSession when it exists AND holds live demand.

        Reads the private ``_audio_session`` slot — NOT the lazy
        ``audio_session`` property — so probing never instantiates a
        session on a radio whose consumers are all legacy (the same
        pattern as the web runtime payload).
        """
        session = getattr(self._host, "_audio_session", None)
        if session is None:
            return None
        if session.rx_demand > 0 or session.tx_demand > 0:
            return session
        return None

    def capture_snapshot(self) -> _AudioSnapshot | None:
        """Capture current audio state for recovery after reconnect.

        Returns None if no audio stream is active.
        """
        if self._session_with_demand() is not None:
            # Session-managed audio (MOR-586): the session's LIVE demand —
            # not the legacy stream state — decides recoverability.
            return _SESSION_MANAGED_SNAPSHOT

        if self._host._audio_stream is None:
            return None

        state = self._host._audio_stream.state
        if state == AudioState.IDLE:
            return None

        rx_active = state in (AudioState.RECEIVING, AudioState.TRANSMITTING) and (
            self._host._pcm_rx_user_callback is not None
            or self._host._opus_rx_user_callback is not None
        )
        tx_active = (
            state == AudioState.TRANSMITTING or self._host._pcm_tx_fmt is not None
        )
        pcm_mode = (
            self._host._pcm_rx_user_callback is not None
            or self._host._pcm_tx_fmt is not None
        )

        pcm_params = self._host._pcm_tx_fmt or self._host._pcm_transcoder_fmt

        jitter_depth = (
            self._host._pcm_rx_jitter_depth
            if self._host._pcm_rx_user_callback is not None
            else self._host._opus_rx_jitter_depth
        )

        return _AudioSnapshot(
            rx_active=rx_active,
            tx_active=tx_active,
            pcm_mode=pcm_mode,
            pcm_rx_callback=self._host._pcm_rx_user_callback,
            opus_rx_callback=self._host._opus_rx_user_callback,
            pcm_params=pcm_params,
            jitter_depth=jitter_depth,
        )

    async def recover(self, snapshot: _AudioSnapshot) -> None:
        """Re-establish audio after a transport reconnect.

        Session-managed audio (MOR-586, ADR §3.4 rule 4) re-establishes
        from the session's LIVE demand via
        :meth:`AudioSession.reestablish`. The legacy snapshot replay would
        re-arm the radio BEHIND the session — clobbering the bus RX
        callback ordering, resurrecting demand dropped during the outage,
        and desyncing the session state from the transport. The replay
        remains only for direct legacy ``*_opus``/``*_pcm`` consumers with
        no live session demand.

        Recovery failure is logged but does not raise.
        """
        if self._host._on_audio_recovery is not None:
            self._host._on_audio_recovery(AudioRecoveryState.RECOVERING)

        try:
            session = self._session_with_demand()
            if session is not None:
                await session.reestablish()
            else:
                await self._replay_legacy(snapshot)
        except Exception as exc:
            logger.warning("Audio auto-recovery failed: %s", exc)
            if self._host._on_audio_recovery is not None:
                self._host._on_audio_recovery(AudioRecoveryState.FAILED)
            return

        if self._host._on_audio_recovery is not None:
            self._host._on_audio_recovery(AudioRecoveryState.RECOVERED)

    async def _replay_legacy(self, snapshot: _AudioSnapshot) -> None:
        """Replay the legacy ``*_opus``/``*_pcm`` starts from *snapshot*."""
        if snapshot.rx_active:
            if snapshot.pcm_mode and snapshot.pcm_rx_callback is not None:
                sr, ch, fms = snapshot.pcm_params or (48000, 1, 20)
                await self._host.start_audio_rx_pcm(
                    snapshot.pcm_rx_callback,
                    sample_rate=sr,
                    channels=ch,
                    frame_ms=fms,
                    jitter_depth=snapshot.jitter_depth,
                )
            elif snapshot.opus_rx_callback is not None:
                await self._host.start_audio_rx_opus(
                    snapshot.opus_rx_callback,
                    jitter_depth=snapshot.jitter_depth,
                )

        if snapshot.tx_active:
            if snapshot.pcm_mode and snapshot.pcm_params is not None:
                sr, ch, fms = snapshot.pcm_params
                await self._host.start_audio_tx_pcm(
                    sample_rate=sr,
                    channels=ch,
                    frame_ms=fms,
                )
            else:
                await self._host.start_audio_tx_opus()
