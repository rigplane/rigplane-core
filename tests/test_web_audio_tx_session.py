"""Web AudioHandler TX path through the shared AudioSession lease (MOR-580).

Falsification-first (epic MOR-562 step 13, ADR §3.3): on radios exposing the
radio-owned ``audio_session`` singleton (MOR-579), ``audio_start
direction=tx`` must ACQUIRE A LEASE on that session instead of direct-arming
``radio.start_tx`` — the session's single-lock refcount is what lets the web
handler coexist with the bridge (and future poller PTT hooks) on the same
radio TX leg without double-arms or premature stops.

The CRITICAL coexistence property: with the bridge already holding a lease
on the SAME session, the web handler acquiring + releasing its own lease
must NOT stop radio TX while the bridge still wants it — and vice versa.

Radios without a session (bare test doubles, not-yet-migrated backends)
keep the legacy direct-arm path with the ``_is_benign_tx_restart``
string-match tolerance (covered by ``tests/test_handlers_coverage.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from _order_sensitive_radios import LanLikeRadio

from rigplane.audio.session import AudioSession, AudioSessionState
from rigplane.web.handlers import AudioHandler
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
)


class _SessionLanRadio(LanLikeRadio):
    """LAN-like stub + radio-owned session singleton (MOR-579 shape)."""

    capabilities = {"audio"}
    audio_sample_rate = 48000

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[bytes] = []
        self._audio_session: AudioSession | None = None

    @property
    def audio_session(self) -> AudioSession:
        if self._audio_session is None:
            self._audio_session = AudioSession(self)
        return self._audio_session

    async def push_tx(self, audio_data: bytes) -> None:
        await super().push_tx(audio_data)  # raises unless TX is armed
        self.pushed.append(audio_data)


def _make_handler(radio: _SessionLanRadio) -> AudioHandler:
    ws = SimpleNamespace(recv=AsyncMock(), send_binary=AsyncMock())
    return AudioHandler(ws, radio, None)


def _pcm_tx_frame(payload: bytes) -> bytes:
    return (
        bytes([MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16])
        + b"\x00" * (AUDIO_HEADER_SIZE - 2)
        + payload
    )


async def _start_tx(handler: AudioHandler) -> None:
    await handler._handle_control({"type": "audio_start", "direction": "tx"})


async def _stop_tx(handler: AudioHandler) -> None:
    await handler._handle_control({"type": "audio_stop", "direction": "tx"})


async def test_audio_start_tx_acquires_session_lease_not_direct_arm() -> None:
    """audio_start tx → a lease on radio.audio_session, frames flow, stop releases."""
    radio = _SessionLanRadio()
    session = radio.audio_session
    rx_anchor = await session.subscribe_rx("rx-anchor")  # session RX demand up
    handler = _make_handler(radio)

    await _start_tx(handler)
    assert session.tx_demand == 1, "handler must acquire a session TX lease"
    assert handler._tx_active is True
    # TX armed exactly once, BY the session (refcount), not by a direct call.
    assert radio.calls.count("start_tx") == 1
    assert radio.state == "transmitting"

    # Browser TX PCM reaches the radio through the lease.
    await handler._handle_tx_audio(_pcm_tx_frame(b"web-pcm-frame"))
    assert radio.pushed == [b"web-pcm-frame"]

    # audio_stop releases the lease; last lease down → session disarms TX.
    await _stop_tx(handler)
    assert session.tx_demand == 0
    assert handler._tx_active is False
    assert radio.state == "receiving"

    await rx_anchor.release()


async def test_web_lease_coexists_with_bridge_lease_on_same_session() -> None:
    """CRITICAL: web acquire/release must not disturb the bridge's TX demand."""
    radio = _SessionLanRadio()
    session = radio.audio_session
    # Bridge shape (MOR-577/579): TX lease first, then RX demand arms both.
    bridge_lease = await session.acquire_tx("audio-bridge")
    bridge_rx = await session.subscribe_rx("audio-bridge")
    assert radio.state == "transmitting"
    assert radio.calls.count("start_tx") == 1

    handler = _make_handler(radio)
    await _start_tx(handler)
    # No double-arm: the session refcounts the second lease.
    assert session.tx_demand == 2
    assert radio.calls.count("start_tx") == 1

    # Web stop releases ONLY the web lease — bridge TX keeps running.
    await _stop_tx(handler)
    assert session.tx_demand == 1
    assert radio.state == "transmitting", "web release must not stop bridge TX"

    # Bridge done → TX disarms.
    await bridge_lease.release()
    assert radio.state == "receiving"
    await bridge_rx.release()


async def test_bridge_release_keeps_web_tx_running() -> None:
    """Vice versa: bridge releasing its lease must not stop the web's TX."""
    radio = _SessionLanRadio()
    session = radio.audio_session
    bridge_lease = await session.acquire_tx("audio-bridge")
    bridge_rx = await session.subscribe_rx("audio-bridge")

    handler = _make_handler(radio)
    await _start_tx(handler)

    await bridge_lease.release()
    assert session.tx_demand == 1
    assert radio.state == "transmitting", "bridge release must not stop web TX"
    # Web TX frames still reach the radio.
    await handler._handle_tx_audio(_pcm_tx_frame(b"still-on-air"))
    assert radio.pushed == [b"still-on-air"]

    await _stop_tx(handler)
    assert radio.state == "receiving"
    await bridge_rx.release()


async def test_double_audio_start_tx_reuses_lease_no_leak() -> None:
    """Repeated audio_start tx must not stack leases (one release frees TX)."""
    radio = _SessionLanRadio()
    session = radio.audio_session
    rx_anchor = await session.subscribe_rx("rx-anchor")
    handler = _make_handler(radio)

    await _start_tx(handler)
    await _start_tx(handler)
    assert session.tx_demand == 1, "double start must reuse the held lease"

    await _stop_tx(handler)
    assert session.tx_demand == 0
    assert radio.state == "receiving"
    await rx_anchor.release()


async def test_audio_stop_without_lease_never_direct_stops_session_radio() -> None:
    """audio_stop tx with no held lease must not kill another owner's TX."""
    radio = _SessionLanRadio()
    session = radio.audio_session
    bridge_lease = await session.acquire_tx("audio-bridge")
    bridge_rx = await session.subscribe_rx("audio-bridge")
    assert radio.state == "transmitting"

    handler = _make_handler(radio)
    await _stop_tx(handler)  # force-stop path, but no web lease held
    assert radio.state == "transmitting", "stray stop must not disarm bridge TX"
    assert session.tx_demand == 1

    await bridge_lease.release()
    await bridge_rx.release()


async def test_digital_tx_no_rx_subscriber_pushes_without_error() -> None:
    """Regression: digital TX (FT8/WSJT-X) with NO session RX subscriber.

    Live forensics (IC-7610 over direct LAN): the radio keys via CAT PTT but
    the LAN audio stream was never transitioned to TX, so every pushed frame
    was rejected with ``AudioNotStartedError`` ("Cannot push TX in state
    receiving"). Root cause: a digital client holds ONLY a TX lease — it never
    subscribes the session to RX — and the AudioSession deferred the TX arm
    forever (no RX demand ever arrived), so ``push_tx`` hit a RECEIVING stream.

    Pre-fix: ``audio_start direction=tx`` acquired a lease but armed nothing
    (session IDLE, ``start_tx`` never called), and the first ``_handle_tx_audio``
    frame raised ``AudioNotStartedError``.

    Post-fix: the first push lazily arms the TX leg (session → TX_ONLY) on the
    full-duplex transport, so the frame reaches the radio.
    """
    radio = _SessionLanRadio()
    session = radio.audio_session
    handler = _make_handler(radio)

    # NO subscribe_rx — the digital client only pushes TX audio.
    await _start_tx(handler)
    assert session.rx_demand == 0
    assert session.tx_demand == 1
    # Acquire defers (bridge/poller order preserved): TX not armed yet.
    assert session.state is AudioSessionState.IDLE
    assert radio.state != "transmitting"

    # The browser/companion TX frame must reach the radio, not raise
    # AudioNotStartedError. The push lazily arms TX (→ TX_ONLY).
    await handler._handle_tx_audio(_pcm_tx_frame(b"ft8-tx-modulation"))
    assert radio.pushed == [b"ft8-tx-modulation"]
    assert session.state is AudioSessionState.TX_ONLY
    assert radio.state == "transmitting"

    # Releasing the lone TX lease disarms TX and returns to IDLE.
    await _stop_tx(handler)
    assert session.tx_demand == 0
    assert session.state is AudioSessionState.IDLE
    assert radio.state == "idle"


async def test_handler_exit_cleanup_releases_lease() -> None:
    """run()-finally cleanup path (disconnect) must release the lease too."""
    radio = _SessionLanRadio()
    session = radio.audio_session
    rx_anchor = await session.subscribe_rx("rx-anchor")
    handler = _make_handler(radio)

    await _start_tx(handler)
    assert session.tx_demand == 1

    # Same invocation run() uses in its finally block.
    await handler._stop_tx(reason="handler exit", timeout=2.0, suppress_errors=True)
    assert session.tx_demand == 0
    assert handler._tx_lease is None
    assert radio.state == "receiving"
    await rx_anchor.release()
