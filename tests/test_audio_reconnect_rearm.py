"""Regression tests for audio-transport re-arm after a LAN sub-stream drop.

Issue: ``soft_reconnect`` rebuilt only the CI-V control transport; the audio
transport was never torn down, so the stale audio-UDP socket FD leaked and the
next ``_ensure_audio_transport`` re-arm raised
``RuntimeError: File descriptor ... is used by transport``. RX never recovered
without a full app restart.

Two regressions, both RED before the fix:

(a) ``test_ensure_audio_transport_rearm_no_stale_fd_leak`` — re-arming over a
    half-dead transport (stream gone, underlying UDP transport still owning the
    reserved socket FD) must NOT raise the stale-FD ``RuntimeError``; a new
    transport must be bound and the old one closed (no FD leak).

(b) ``test_soft_reconnect_recovers_audio`` — after a simulated audio drop a
    ``soft_reconnect`` must re-arm the audio transport and drive audio recovery
    so RX is live again.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from rigplane.core.transport import IcomTransport
from rigplane.runtime._audio_runtime_mixin import AudioRuntimeMixin
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import ControlPhaseRuntime


# ---------------------------------------------------------------------------
# Test host: real AudioRuntimeMixin over fakes for the radio-side seams.
# ---------------------------------------------------------------------------


class _FakeAudioStream:
    """Minimal AudioStream stand-in: tracks stop_rx/stop_tx."""

    def __init__(self) -> None:
        self.stopped_rx = False
        self.stopped_tx = False

    async def stop_rx(self) -> None:
        self.stopped_rx = True

    async def stop_tx(self) -> None:
        self.stopped_tx = True


class _StaleFdHost(AudioRuntimeMixin):
    """Drives the REAL ``_ensure_audio_transport`` / ``_teardown_audio_transport``.

    ``IcomTransport.connect`` is reduced to its FD-claiming core (bind the
    reserved socket into a real asyncio datagram endpoint) so the stale-FD
    ``RuntimeError`` reproduces without a live-radio discovery handshake.
    """

    def __init__(self, host: str = "127.0.0.1", audio_port: int = 50001) -> None:
        self._host = host
        self._audio_port = audio_port
        self._audio_stream = None
        self._audio_transport = None
        self._audio_sock_pending: socket.socket | None = None
        self._audio_local_port = 0
        self._local_bind_host = "127.0.0.1"

    # Audio open/close + watchdog are radio-side seams; no-op them here so the
    # real transport-binding path runs in isolation.
    async def _send_audio_open_close(self, *, open_stream: bool) -> None:
        return None

    def _start_audio_watchdog(self) -> None:
        return None


async def _connect_fd_only(
    self: IcomTransport,
    host: str,
    port: int,
    *,
    local_host: str | None = None,
    local_port: int = 0,
    sock: socket.socket | None = None,
) -> None:
    """Real FD-claiming half of ``IcomTransport.connect`` (no handshake).

    Hands the reserved socket to ``create_datagram_endpoint(sock=...)`` exactly
    like production; this is the call that raises
    ``RuntimeError: File descriptor ... is used by transport`` when the FD is
    still owned by an undisconnected transport.
    """
    loop = asyncio.get_running_loop()
    assert sock is not None
    sock.setblocking(False)
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        sock=sock,
    )
    self._udp_transport = transport  # type: ignore[assignment]

    # Real keepalive loop starts are no-ops for this harness.

    def _noop() -> None:
        return None

    self.start_ping_loop = _noop  # type: ignore[method-assign]
    self.start_retransmit_loop = _noop  # type: ignore[method-assign]
    self.start_idle_loop = _noop  # type: ignore[method-assign]


def _reserve_audio_socket() -> tuple[socket.socket, int]:
    """Reserve a bound UDP socket the way ``_run_data_port_handshake`` does."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


@pytest.mark.asyncio
async def test_ensure_audio_transport_rearm_no_stale_fd_leak(monkeypatch) -> None:
    """Re-arming over a half-dead transport must not leak the stale UDP FD."""
    monkeypatch.setattr(IcomTransport, "connect", _connect_fd_only, raising=True)

    host = _StaleFdHost()

    # --- First arm: reserve a socket and bind a real datagram transport. ---
    sock1, port1 = _reserve_audio_socket()
    host._audio_local_port = port1
    host._audio_sock_pending = sock1
    await host._ensure_audio_transport()

    first_transport = host._audio_transport
    assert first_transport is not None
    assert first_transport._udp_transport is not None
    # Replace the placeholder stream with a controllable fake.
    host._audio_stream = _FakeAudioStream()  # type: ignore[assignment]
    old_fd = sock1.fileno()

    # --- Simulate the LAN audio sub-stream dying. ---
    # The stream is gone, but the transport (and its FD) lingers — exactly the
    # leak condition the pre-fix short-circuit ignores.
    host._audio_stream = None

    # --- Re-arm with a fresh socket bound to the SAME local port. ---
    # Production re-reserves the audio socket during reconnect; reusing the FD
    # of the still-open first transport is what raises the stale-FD error.
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock2.bind(("127.0.0.1", port1))
    except OSError:
        sock2.bind(("127.0.0.1", 0))
    host._audio_sock_pending = sock2
    host._audio_local_port = sock2.getsockname()[1]

    # Pre-fix: short-circuit only checks ``_audio_stream is None`` then re-arms
    # over the lingering transport → RuntimeError (stale FD). Post-fix: the
    # teardown guard disconnects the first transport first → clean re-arm.
    await host._ensure_audio_transport()

    # A NEW transport is bound...
    assert host._audio_transport is not None
    assert host._audio_transport is not first_transport
    assert host._audio_transport._udp_transport is not None
    # ...and the old transport's UDP datagram transport was closed (no leak).
    assert first_transport._udp_transport is None
    # The original reserved socket FD was released by the teardown.
    with pytest.raises(OSError):
        # fileno() of a closed socket raises; if still open the FD leaked.
        os_fd = old_fd  # noqa: F841 (documents intent)
        sock1.getsockname()

    await host._teardown_audio_transport()
    sock2.close()


# ---------------------------------------------------------------------------
# soft_reconnect host: real ControlPhaseMixin.soft_reconnect over fakes.
# ---------------------------------------------------------------------------


class _FakeCivTransport:
    """Minimal CI-V IcomTransport stand-in for soft_reconnect."""

    def __init__(self) -> None:
        self._udp_transport = object()
        self._udp_error_count = 7

    async def connect(self, *args, **kwargs) -> None:
        self._udp_transport = object()

    def start_ping_loop(self) -> None: ...
    def start_retransmit_loop(self) -> None: ...
    def start_idle_loop(self) -> None: ...

    async def disconnect(self) -> None:
        self._udp_transport = None


class _FakeAudioRuntime:
    """Records snapshot/recover so the re-arm path is observable."""

    def __init__(self, host: "_SoftReconnectHost") -> None:
        self._host = host
        self.recovered = False
        self.snapshot_calls = 0

    def capture_snapshot(self):
        self.snapshot_calls += 1
        # Live RX demand exists → recoverable.
        return object()

    async def recover(self, snapshot) -> None:
        self.recovered = True
        # Recovery makes RX live again on the (freshly armed) transport.
        self._host.rx_live = True


class _SoftReconnectHost:
    """Fake radio host: holds all state for the real ``soft_reconnect``."""

    def __init__(self) -> None:
        self._host = "127.0.0.1"
        self._civ_port = 50002
        self._audio_port = 50003
        self._conn_state = RadioConnectionState.RECONNECTING
        self._civ_stream_ready = False
        self._civ_recovering = True
        self._civ_local_port = 0
        self._local_bind_host = "127.0.0.1"
        self._last_civ_data_received = None
        self._civ_ready_idle_timeout = 3.0
        self._civ_last_waiter_gc_monotonic = 0.0
        self._civ_runtime = None
        self._on_reconnect = None
        self._auto_recover_audio = True

        # CI-V starts gone so soft_reconnect takes the rebuild path.
        self._civ_transport = None
        self._ctrl_transport = _FakeCivTransport()

        # Audio: armed, then dropped (transport lingers, stream gone).
        self._audio_transport = _FakeCivTransport()
        self._audio_stream = None
        self.rx_live = False

        self._audio_runtime = _FakeAudioRuntime(self)
        self._teardown_calls = 0
        self._ensure_calls = 0

    # --- CI-V rebuild seams (no-op; soft_reconnect drives the orchestration) -
    def _advance_civ_generation(self, reason: str) -> None: ...
    async def _stop_civ_rx_pump(self) -> None: ...
    def _start_civ_rx_pump(self) -> None: ...
    def _start_civ_worker(self) -> None: ...
    def _start_civ_data_watchdog(self) -> None: ...

    # --- audio re-arm seams: record + simulate a live transport rebuild. -----
    async def _teardown_audio_transport(self) -> None:
        self._teardown_calls += 1
        self._audio_transport = None
        self._audio_stream = None

    async def _ensure_audio_transport(self) -> None:
        self._ensure_calls += 1
        # A fresh transport is bound on re-arm.
        self._audio_transport = _FakeCivTransport()
        self._audio_stream = _FakeAudioStream()

    @property
    def control_connected(self) -> bool:
        return True


class _TestControlPhaseRuntime(ControlPhaseRuntime):
    """Real ``soft_reconnect``; no-op the CI-V open/close protocol seam."""

    async def _send_open_close(self, *, open_stream: bool) -> None:
        return None


@pytest.mark.asyncio
async def test_soft_reconnect_recovers_audio(monkeypatch) -> None:
    """soft_reconnect must tear down + re-arm audio and drive RX recovery."""
    # soft_reconnect does ``from rigplane.transport import IcomTransport`` for
    # the CI-V rebuild; swap it for the fake so no live radio is required.
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]
    assert host.rx_live is False

    await runtime.soft_reconnect()

    # CI-V was rebuilt.
    assert host._civ_transport is not None
    assert host._conn_state == RadioConnectionState.CONNECTED

    # Audio was snapshotted, torn down, re-armed, and recovered.
    assert host._audio_runtime.snapshot_calls == 1
    assert host._teardown_calls == 1
    assert host._ensure_calls == 1
    assert host._audio_runtime.recovered is True

    # A fresh audio transport + stream are live and RX flows again.
    assert host._audio_transport is not None
    assert host._audio_stream is not None
    assert host.rx_live is True
