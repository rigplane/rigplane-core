"""Regression tests for audio-transport re-arm after a LAN sub-stream drop.

Issue: ``soft_reconnect`` rebuilt only the CI-V control transport; the audio
transport was never torn down, so the stale audio-UDP socket FD leaked and the
next ``_ensure_audio_transport`` re-arm raised
``RuntimeError: File descriptor ... is used by transport``. RX never recovered
without a full app restart.

Regressions, each RED before the fix that pins it:

(a) ``test_ensure_audio_transport_rearm_no_stale_fd_leak`` — re-arming over a
    half-dead transport (stream gone, underlying UDP transport still owning the
    reserved socket FD) must NOT raise the stale-FD ``RuntimeError``; a new
    transport must be bound and the old one closed (no FD leak).

(b) ``test_soft_reconnect_recovers_audio`` — after a simulated audio drop a
    ``soft_reconnect`` must re-arm the audio transport and drive audio recovery
    so RX is live again.

(c) ``test_ensure_audio_transport_failure_releases_local_port`` — a rebuild
    whose discovery times out after the endpoint exists must close that
    endpoint: the session's fixed ``_audio_local_port`` is released (a plain
    ``socket.bind`` succeeds) and a second rebuild fails with the discovery
    timeout again, not EADDRINUSE. The failed transport is dropped without
    recording its reset numbering.

(d) ``test_soft_reconnect_reports_and_retries_a_failed_audio_rebuild`` /
    ``test_soft_reconnect_stops_retrying_audio_rebuild_after_the_bound`` — a
    failed soft-reconnect audio rebuild is reported (WARNING + FAILED to
    ``on_audio_recovery``) and retried with a bound; it never loops unbounded
    and never fails the CI-V reconnect itself.

(e) ``test_soft_reconnect_owner_stop_during_backoff_stops_audio_rebuild_retry``
    — an owner stop that lands inside the retry backoff (scheduled from
    the failing first attempt via ``call_soon``) is caught by the
    ownership re-check that runs immediately before the next attempt:
    exactly one rebuild, no retry.
(f) ``test_soft_reconnect_civ_epoch_change_during_backoff_stops_audio_rebuild_retry``
    — a new CI-V epoch landing inside the retry backoff likewise stops
    the retrying after exactly one rebuild.
(g) ``test_soft_reconnect_failed_rebuild_with_no_audio_demand_stays_quiet`` /
    ``test_soft_reconnect_failed_rebuild_with_auto_recover_off_stays_quiet``
    — with nothing owed to recovery (no snapshot demand, or
    auto-recovery disabled) a failed rebuild is one WARNING attempt: no
    FAILED report, no retry.
(h) ``test_soft_reconnect_owner_stop_during_successful_rebuild_skips_recovery``
    — an owner stop recorded while a rebuild attempt runs skips the
    post-success ``recover``.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct

import pytest

from rigplane.audio import AudioStream
from rigplane.core.exceptions import ConnectionError, TimeoutError
from rigplane.core.transport import CONTROL_SIZE, IcomTransport
from rigplane.runtime._audio_recovery import AudioRecoveryState
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


@pytest.mark.asyncio
async def test_ensure_audio_transport_failure_releases_local_port(
    monkeypatch,
) -> None:
    """A failed rebuild must release the fixed local audio port it bound.

    The soft-reconnect rebuild connects with no pending socket on the
    session's fixed ``_audio_local_port``. When discovery times out after
    the endpoint exists, that port must be released — otherwise every later
    rebuild fails with EADDRINUSE and RX never returns (the field shape:
    INFO "Soft reconnect to ..." with no later "Audio transport connected
    on port").
    """
    import rigplane.core.transport as _core_transport

    monkeypatch.setattr(_core_transport, "DISCOVERY_RETRIES", 1)
    monkeypatch.setattr(_core_transport, "DISCOVERY_TIMEOUT", 0.05)

    def _free_port() -> int:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    host = _StaleFdHost(host="127.0.0.1", audio_port=_free_port())
    host._audio_local_port = _free_port()
    host._audio_sock_pending = None
    host._audio_resume_seq = 7

    # Discovery against a dead loopback port times out.
    with pytest.raises(TimeoutError):
        await host._ensure_audio_transport()

    # The failed transport must have released the fixed local port.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", host._audio_local_port))
    probe.close()

    # A second rebuild fails with the discovery timeout again — not
    # EADDRINUSE from a port the first failure never released.
    with pytest.raises(TimeoutError) as excinfo:
        await host._ensure_audio_transport()
    assert not isinstance(excinfo.value, ConnectionError)

    # The failed rebuild is dropped without recording its reset numbering.
    assert host._audio_transport is None
    assert host._audio_resume_seq == 7


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
        # Live RX demand exists → recoverable; tests set this to None to pin
        # the nothing-owed branch.
        self.snapshot: object | None = object()

    def capture_snapshot(self):
        self.snapshot_calls += 1
        return self.snapshot

    async def recover(self, snapshot) -> None:
        self._host._on_audio_recovery(AudioRecoveryState.RECOVERING)
        self.recovered = True
        # Recovery makes RX live again on the (freshly armed) transport.
        self._host.rx_live = True
        self._host._on_audio_recovery(AudioRecoveryState.RECOVERED)


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
        self._civ_resume_seq = None
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

        # Soft-reconnect audio-tail knobs (regressions d–h): how many
        # ``_ensure_audio_transport`` calls fail before one succeeds, and
        # ownership changes injected around the rebuild attempts.
        self.ensure_failures = 0
        self.bump_owner_stop_in_backoff = False
        self.bump_civ_epoch_in_backoff = False
        self.stop_owner_during_success = False
        self._civ_epoch = 0
        self._owner_stop_epoch = 0
        self.recovery_states: list[AudioRecoveryState] = []
        self._on_audio_recovery = self.recovery_states.append

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

    def _bump_epoch_after_failure(self) -> None:
        """Scheduled with ``call_soon`` from a failing rebuild attempt.

        The bump fires on the event-loop pass inside the retry backoff
        sleep — after the failure handler has logged, before the next
        attempt runs.
        """
        if self.bump_civ_epoch_in_backoff:
            self._civ_epoch += 1
        if self.bump_owner_stop_in_backoff:
            self._owner_stop_epoch += 1

    async def _ensure_audio_transport(self) -> None:
        self._ensure_calls += 1
        if self._ensure_calls <= self.ensure_failures:
            if self.bump_owner_stop_in_backoff or self.bump_civ_epoch_in_backoff:
                asyncio.get_running_loop().call_soon(self._bump_epoch_after_failure)
            raise RuntimeError("audio rebuild failed (simulated)")
        if self.stop_owner_during_success:
            # Recorded while the successful rebuild runs: before the tail's
            # post-success ownership re-check.
            self._owner_stop_epoch += 1
        # A fresh transport is bound on re-arm.
        self._audio_transport = _FakeCivTransport()
        self._audio_stream = _FakeAudioStream()

    @property
    def control_connected(self) -> bool:
        return True


class _TestControlPhaseRuntime(ControlPhaseRuntime):
    """Real ``soft_reconnect``; no-op the CI-V open/close protocol seam."""

    # Keep the retry-backoff wait out of the test's wall time.
    _SOFT_AUDIO_REBUILD_BACKOFF_S = 0.01

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


@pytest.mark.asyncio
async def test_soft_reconnect_reports_and_retries_a_failed_audio_rebuild(
    monkeypatch, caplog
) -> None:
    """One failed rebuild: WARNING with the cause, retry, then recovery."""
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    host.ensure_failures = 1
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="rigplane.runtime._control_phase"):
        await runtime.soft_reconnect()

    # The failed first rebuild was retried and the second one armed audio.
    assert host._ensure_calls == 2
    assert host._audio_runtime.recovered is True
    assert host.rx_live is True
    assert host.recovery_states == [
        AudioRecoveryState.RECOVERING,
        AudioRecoveryState.RECOVERED,
    ]
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "audio re-arm failed" in record.message
    ]
    assert warnings
    assert "audio rebuild failed (simulated)" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_soft_reconnect_stops_retrying_audio_rebuild_after_the_bound(
    monkeypatch,
) -> None:
    """A rebuild that always fails stops after the bound and reports FAILED."""
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    host.ensure_failures = 10  # more than the retry bound
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]

    await runtime.soft_reconnect()

    assert host._ensure_calls == 3
    assert host._audio_runtime.recovered is False
    assert host.recovery_states == [AudioRecoveryState.FAILED]
    # The bounded audio failure never fails the CI-V reconnect itself.
    assert host._civ_transport is not None
    assert host._conn_state == RadioConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_soft_reconnect_owner_stop_during_backoff_stops_audio_rebuild_retry(
    monkeypatch,
) -> None:
    """An owner stop landing inside the retry backoff allows no further rebuild.

    The stop is scheduled with ``call_soon`` from the failing first
    attempt, so it fires on the event-loop pass inside the backoff sleep —
    after the failure is logged, before the next attempt. The tail must
    re-check ownership after the sleep and stop: exactly one rebuild.
    """
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    host.ensure_failures = 10
    host.bump_owner_stop_in_backoff = True
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]

    await runtime.soft_reconnect()

    assert host._ensure_calls == 1
    assert host.recovery_states == [AudioRecoveryState.FAILED]


@pytest.mark.asyncio
async def test_soft_reconnect_civ_epoch_change_during_backoff_stops_audio_rebuild_retry(
    monkeypatch,
) -> None:
    """A new CI-V epoch landing inside the retry backoff stops the retrying.

    Same schedule as the owner-stop variant: the epoch bump is scheduled
    from the failing first attempt and fires inside the backoff sleep. A
    new CI-V generation means this session no longer owns the radio, so
    no second rebuild may run.
    """
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    host.ensure_failures = 10
    host.bump_civ_epoch_in_backoff = True
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]

    await runtime.soft_reconnect()

    assert host._ensure_calls == 1
    assert host.recovery_states == [AudioRecoveryState.FAILED]


@pytest.mark.asyncio
async def test_soft_reconnect_failed_rebuild_with_no_audio_demand_stays_quiet(
    monkeypatch,
) -> None:
    """With no snapshot demand a failed rebuild is not reported or retried.

    Nothing is owed to ``on_audio_recovery`` when the reconnect snapshot
    holds no audio demand, so the failure stays a single WARNING attempt:
    no FAILED report, no retry — the silence the hard path and the audio
    watchdog keep in the same situation.
    """
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    host.ensure_failures = 10
    host._audio_runtime.snapshot = None
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]

    await runtime.soft_reconnect()

    assert host._ensure_calls == 1
    assert host.recovery_states == []


@pytest.mark.asyncio
async def test_soft_reconnect_failed_rebuild_with_auto_recover_off_stays_quiet(
    monkeypatch,
) -> None:
    """With audio auto-recovery off a failed rebuild stays a WARNING too.

    ``_auto_recover_audio`` gates recovery on the hard path and the audio
    watchdog (radio_reconnect); the tail applies the same gate to its
    retrying and FAILED report: one attempt, silence afterwards.
    """
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    host.ensure_failures = 10
    host._auto_recover_audio = False
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]

    await runtime.soft_reconnect()

    assert host._ensure_calls == 1
    assert host.recovery_states == []


@pytest.mark.asyncio
async def test_soft_reconnect_owner_stop_during_successful_rebuild_skips_recovery(
    monkeypatch,
) -> None:
    """An owner stop recorded during a successful rebuild skips recovery.

    ``_ensure_audio_transport`` is itself an await, so ownership can move
    on while the rebuild runs. The tail re-checks the epochs before the
    post-success ``recover``: live demand is not restored onto a radio
    this session no longer owns.
    """
    import rigplane.transport as _transport_mod

    monkeypatch.setattr(_transport_mod, "IcomTransport", _FakeCivTransport)

    host = _SoftReconnectHost()
    host.stop_owner_during_success = True
    runtime = _TestControlPhaseRuntime(host)  # type: ignore[arg-type]

    await runtime.soft_reconnect()

    # The rebuild itself ran and armed a fresh transport...
    assert host._ensure_calls == 1
    assert host._audio_transport is not None
    # ...but no recovery was driven on top of it.
    assert host._audio_runtime.recovered is False
    assert host.rx_live is False
    assert host.recovery_states == []


def _audio_data_packet(seq: int) -> bytes:
    """A non-scope data packet, the shape the radio sends on the audio port."""
    pkt = bytearray(CONTROL_SIZE + 8)
    struct.pack_into("<I", pkt, 0, len(pkt))
    struct.pack_into("<H", pkt, 4, 0x00)
    struct.pack_into("<H", pkt, 6, seq)
    return bytes(pkt)


class _DiscardHost(AudioRuntimeMixin):
    """Real ``start_rx`` / ``stop_rx`` over a real transport and stream.

    The connect check and the transport setup are the radio's job; this
    host already has both, so those seams are skipped.
    """

    def __init__(self) -> None:
        self._audio_transport = IcomTransport()
        self._audio_stream = AudioStream(self._audio_transport)
        self._opus_rx_user_callback = None
        self._opus_rx_jitter_depth = 0

    def _check_connected(self) -> None:
        return None

    async def _ensure_audio_transport(self) -> None:
        return None

    def _arm_pcm_ingress(self) -> None:
        return None


@pytest.mark.asyncio
async def test_stopped_rx_discards_audio_and_restart_queues_again() -> None:
    """An idle audio stream must not queue packets nobody reads.

    After RX stops, incoming data packets are dropped. After RX starts
    again, the same packets are queued and the loop reads them.
    """
    host = _DiscardHost()
    transport = host._audio_transport
    assert transport is not None

    await host.start_rx(lambda _pkt: None, jitter_depth=0)
    await host.stop_rx()

    for seq in range(1, 6):
        transport._handle_packet(_audio_data_packet(seq))
    assert transport._packet_queue.empty()

    await host.start_rx(lambda _pkt: None, jitter_depth=0)
    for seq in range(6, 9):
        transport._handle_packet(_audio_data_packet(seq))

    queued = []
    while not transport._packet_queue.empty():
        queued.append(transport._packet_queue.get_nowait())
    assert [struct.unpack_from("<H", pkt, 6)[0] for pkt in queued] == [6, 7, 8]
    await host.stop_rx()
