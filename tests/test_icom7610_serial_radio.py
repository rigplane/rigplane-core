"""Lifecycle and readiness tests for Icom7610SerialRadio."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rigplane import IcomRadio, RadioConnectionState
from rigplane.backends._icom_serial_base import (
    _IcomSerialRadioBase,
    _derive_reconnect_glob,
)
from rigplane.backends.discovery import SerialPortCandidate
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    _CMD_FREQ_GET,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.exceptions import CommandError, ConnectionError
from rigplane.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.types import AudioCodec
from rigplane.types import bcd_encode


@pytest.fixture(autouse=True)
def _no_real_serial_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """MOR-1453 test hermeticity (review round 2, B1).

    Every construction in this module uses a synthetic device path with
    no real backing hardware, but rediscovery's *default* enumeration/
    identity-probe seams are the real OS-level ones unless a test
    explicitly overrides them. On a host with a real USB-serial adapter
    physically attached (the live bench, or a self-hosted CI runner),
    the synthetic path can fail ``os.path.exists`` while a real sibling
    node still matches the derived glob pattern -- reaching the FALLBACK
    CI-V probe, which opens the real port and writes a real frame.
    Patching the class-level defaults to safe no-ops for every test in
    this module (tests that explicitly pass their own
    ``_civ_identity_probe``/``_enumerate_serial_ports_fn`` are unaffected,
    since an explicit constructor argument always wins over the default)
    makes that impossible regardless of what hardware is attached.
    """
    monkeypatch.setattr(
        _IcomSerialRadioBase,
        "_default_enumerate_serial_ports",
        lambda self: [],
    )

    async def _no_probe(self: object, port: str) -> int | None:
        raise AssertionError(
            f"unexpected real CI-V identity probe attempted on {port!r} "
            "-- this test module must never perform real serial I/O"
        )

    monkeypatch.setattr(_IcomSerialRadioBase, "_default_civ_identity_probe", _no_probe)


def _freq_response_frame(freq_hz: int) -> bytes:
    return build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_FREQ_GET,
        data=bcd_encode(freq_hz),
    )


def _bcd_byte(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def _scope_wave_frame(
    *,
    receiver: int = 0,
    mode: int = 1,
    start_hz: int = 14_000_000,
    end_hz: int = 14_350_000,
    pixels: bytes = b"\x10\x20\x30",
) -> bytes:
    payload = bytes(
        [
            receiver,
            _bcd_byte(1),
            _bcd_byte(1),
            mode,
            *bcd_encode(start_hz),
            *bcd_encode(end_hz),
            0x00,
            *pixels,
        ]
    )
    return build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x27,
        sub=0x00,
        data=payload,
    )


def _scope_state_response(sub: int, enabled: bool) -> bytes:
    return build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x27,
        sub=sub,
        data=bytes([enabled]),
    )


async def _wait_until(predicate, *, timeout_s: float = 1.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return bool(predicate())


class _FakeSerialCivLink:
    def __init__(
        self,
        *,
        fail_connect: BaseException | None = None,
        fail_connect_calls: set[int] | None = None,
        fail_connect_calls_exc: BaseException | None = None,
    ) -> None:
        self._fail_connect = fail_connect
        self._fail_connect_calls = set(fail_connect_calls or set())
        self._fail_connect_calls_exc = fail_connect_calls_exc
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.connected = False
        self.ready = False
        self.healthy = False
        self.sent_frames: list[bytes] = []
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._responses_by_send: dict[int, list[bytes]] = {}
        self.device_history: list[str] = []

    def set_device(self, device: str) -> None:
        self.device_history.append(device)

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls in self._fail_connect_calls:
            if self._fail_connect_calls_exc is not None:
                raise self._fail_connect_calls_exc
            raise OSError(f"connect failed on call {self.connect_calls}")
        if self._fail_connect is not None:
            raise self._fail_connect
        self.connected = True
        self.ready = True
        self.healthy = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False
        self.ready = False
        self.healthy = False

    async def send(self, frame: bytes) -> None:
        if not self.connected:
            raise ConnectionError("Serial CI-V link is disconnected.")
        payload = bytes(frame)
        self.sent_frames.append(payload)
        send_no = len(self.sent_frames)
        for response in self._responses_by_send.pop(send_no, []):
            self._responses.put_nowait(response)

    async def receive(self, timeout: float | None = None) -> bytes | None:
        if not self.connected:
            return None
        timeout_s = 0.05 if timeout is None else timeout
        try:
            return await asyncio.wait_for(self._responses.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None

    def queue_response_on_send(self, send_no: int, frame: bytes) -> None:
        self._responses_by_send.setdefault(send_no, []).append(frame)

    def queue_response(self, frame: bytes) -> None:
        self._responses.put_nowait(frame)


class _FakeUsbAudioDriver:
    def __init__(self) -> None:
        self.rx_running = False
        self.tx_running = False
        self._rx_callback = None
        self.tx_frames: list[bytes] = []
        self.rx_starts = 0
        self.tx_starts = 0
        self.serial_port_history: list[str | None] = []

    def set_serial_port(self, serial_port: str | None) -> None:
        self.serial_port_history.append(serial_port)

    async def start_rx(self, callback, **kwargs) -> None:  # type: ignore[no-untyped-def]
        _ = kwargs
        if self.rx_running:
            raise RuntimeError("RX stream already started.")
        self.rx_running = True
        self.rx_starts += 1
        self._rx_callback = callback

    async def stop_rx(self) -> None:
        self.rx_running = False
        self._rx_callback = None

    async def start_tx(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if self.tx_running:
            raise RuntimeError("TX stream already started.")
        self.tx_running = True
        self.tx_starts += 1
        self.tx_start_kwargs: dict = dict(kwargs)

    async def stop_tx(self) -> None:
        self.tx_running = False

    async def _push_tx_pcm(self, frame: bytes) -> None:
        self.tx_frames.append(bytes(frame))

    def emit_rx_pcm(self, frame: bytes) -> None:
        if self._rx_callback is not None:
            self._rx_callback(frame)


@pytest.mark.asyncio
async def test_serial_radio_connect_disconnect_and_core_command_execution() -> None:
    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _freq_response_frame(14_074_000))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )

    await radio.connect()
    assert radio.connected is True
    assert radio.control_connected is True
    assert await radio.get_freq() == 14_074_000
    assert link.sent_frames
    assert radio.radio_ready is True

    await radio.disconnect()
    assert radio.connected is False
    assert radio.control_connected is False
    assert radio.radio_ready is False
    assert radio._civ_transport is None
    assert radio._civ_rx_task is None
    assert getattr(radio, "_civ_data_watchdog_task", None) is None


@pytest.mark.asyncio
async def test_serial_radio_connect_failure_sets_disconnected_state() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(fail_connect=OSError("permission denied")),
    )

    with pytest.raises(ConnectionError, match="Failed to connect serial session"):
        await radio.connect()

    assert radio.connected is False
    assert radio.control_connected is False
    assert radio.radio_ready is False


def test_serial_radio_rejects_unsupported_ptt_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported serial PTT mode"):
        Icom7610SerialRadio(
            device="/dev/ttyUSB0",
            ptt_mode="rts",
        )


@pytest.mark.asyncio
async def test_serial_radio_ready_tracks_serial_link_health() -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )

    await radio.connect()
    assert await _wait_until(lambda: radio.radio_ready)

    link.ready = False
    link.healthy = False
    assert await _wait_until(lambda: not radio.radio_ready)

    link.ready = True
    link.healthy = True
    assert await _wait_until(lambda: radio.radio_ready)

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_watchdog_retries_after_transient_soft_reconnect_failure() -> None:
    link = _FakeSerialCivLink(fail_connect_calls={2})
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.05  # type: ignore[attr-defined]
    radio._SERIAL_WATCHDOG_RETRY_S = 0.01  # type: ignore[attr-defined]

    await radio.connect()
    assert link.connect_calls == 1
    assert radio.radio_ready is True

    link.ready = False
    link.healthy = False
    assert await _wait_until(lambda: link.connect_calls >= 3, timeout_s=2.0)
    assert await _wait_until(lambda: radio.radio_ready, timeout_s=2.0)
    assert radio.conn_state == RadioConnectionState.CONNECTED

    await radio.disconnect()


def test_serial_watchdog_retry_delay_is_capped_exponential_backoff() -> None:
    """MOR-237: repeated reconnect failures back off, capped at the max."""
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
    )
    base = radio._SERIAL_WATCHDOG_RETRY_S
    cap = radio._SERIAL_WATCHDOG_RETRY_MAX_S

    # First failure -> base delay; then doubling; never above the cap.
    assert radio._serial_watchdog_retry_delay(1) == base
    assert radio._serial_watchdog_retry_delay(2) == base * 2
    assert radio._serial_watchdog_retry_delay(3) == base * 4
    # A very large failure count is clamped to the cap.
    assert radio._serial_watchdog_retry_delay(50) == cap
    # Monotonic non-decreasing.
    delays = [radio._serial_watchdog_retry_delay(n) for n in range(1, 12)]
    assert delays == sorted(delays)


@pytest.mark.asyncio
async def test_serial_watchdog_quiet_after_transient_open_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-237: a vanished port (FileNotFoundError) must not flood WARNING+traceback.

    Only the first failure of a run is a WARNING (with traceback); subsequent
    identical failures are demoted to DEBUG. Recovery resets the run.
    """
    import logging

    # Fail soft_reconnect's connect() on calls 2..5 with a "port gone" error,
    # then let it recover on call 6.
    link = _FakeSerialCivLink(
        fail_connect_calls={2, 3, 4, 5},
        fail_connect_calls_exc=FileNotFoundError(
            "[Errno 2] No such file or directory: '/dev/cu.usbmodem58910181093'"
        ),
    )
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.02  # type: ignore[attr-defined]
    radio._SERIAL_WATCHDOG_RETRY_S = 0.01  # type: ignore[attr-defined]
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.05  # type: ignore[attr-defined]

    await radio.connect()
    assert radio.radio_ready is True

    with caplog.at_level(logging.DEBUG, logger="rigplane.backends._icom_serial_base"):
        # Trip the watchdog into recovery.
        link.ready = False
        link.healthy = False
        # Wait until the port "returns" and the session recovers.
        assert await _wait_until(lambda: link.connect_calls >= 6, timeout_s=3.0)
        assert await _wait_until(lambda: radio.radio_ready, timeout_s=2.0)

    warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "soft reconnect failed" in r.getMessage()
    ]
    # The whole multi-failure run must produce at most one WARNING line, and it
    # must not be repeated per retry (the old behaviour logged one per 0.5s).
    assert len(warnings) <= 1, (
        f"expected <=1 WARNING during a transient outage, got {len(warnings)}"
    )

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_disconnect_cleans_watchdog_when_already_disconnected() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
    )
    radio._conn_state = RadioConnectionState.DISCONNECTED
    radio._civ_data_watchdog_task = asyncio.create_task(asyncio.sleep(10))
    await radio.disconnect()
    assert getattr(radio, "_civ_data_watchdog_task", None) is None


class _FakeManagedTxRuntime:
    """Minimal stand-in for the managed-TX supervisor (real async signature)."""

    def __init__(self) -> None:
        self.target_id = "fake-managed-tx"
        self.ready_calls: list[bool] = []

    async def set_provider_ready(self, *, ready: bool) -> None:
        self.ready_calls.append(ready)


@pytest.mark.asyncio
async def test_serial_link_down_detected_when_healthy_flag_stays_stuck_true(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-1440: a vanished USB-serial device that never raises OSError/EOF.

    ``SerialCivLink.healthy`` only flips false on a read/write exception. A
    dead adapter that silently stops answering (observed on the bench) leaves
    it stuck ``True`` forever, so the pre-existing watchdog (which only reacts
    to that flag) never notices. Consecutive CI-V command timeouts must force
    the state machine to link-down regardless of what the raw flag reports.
    """
    import logging

    # No responses ever queued -> every awaited command times out. Reconnect
    # attempts also fail (device never returns on the same path) so the
    # detected link-down state doesn't self-heal mid-assertion.
    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.03
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005

    await radio.connect()
    assert radio.radio_ready is True

    frame = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)

    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        # First timeout alone must not trip anything, and the raw flag must
        # still report healthy — this is exactly the evidence the low-level
        # watchdog (keyed off that flag) cannot see on its own.
        with pytest.raises(RigplaneTimeoutError):
            await radio._send_civ_raw(frame, wait_response=True)
        assert link.healthy is True
        assert radio.conn_state == RadioConnectionState.CONNECTED

        for _ in range(radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD - 1):
            with pytest.raises(RigplaneTimeoutError):
                await radio._send_civ_raw(frame, wait_response=True)

        assert await _wait_until(
            lambda: radio.conn_state == RadioConnectionState.RECONNECTING,
            timeout_s=2.0,
        )

    error_lines = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "link-down" in r.getMessage()
    ]
    assert len(error_lines) == 1, (
        f"expected exactly one link-down ERROR line, got {len(error_lines)}"
    )

    assert radio.connected is False
    assert radio.radio_ready is False

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_link_down_propagates_to_web_radio_health() -> None:
    """MOR-1440: honest propagation — radioHealth reflects link-down, not 'connected'."""
    from rigplane.web.runtime_helpers import classify_radio_health

    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.03
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()

    frame = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)
    for _ in range(radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD):
        with pytest.raises(RigplaneTimeoutError):
            await radio._send_civ_raw(frame, wait_response=True)
    assert await _wait_until(
        lambda: radio.conn_state == RadioConnectionState.RECONNECTING, timeout_s=2.0
    )

    health = classify_radio_health(radio)
    assert health["radioLink"] != "connected"
    assert health["radioLink"] == "reconnecting"

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_link_down_stops_audio_capture() -> None:
    """MOR-1440: audio capture must stop on link-down, same radio same USB."""
    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0", civ_link=link, audio_driver=usb_audio
    )
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.03
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()

    received: list[object] = []
    await radio.start_rx(received.append)
    assert usb_audio.rx_running is True

    frame = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)
    for _ in range(radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD):
        with pytest.raises(RigplaneTimeoutError):
            await radio._send_civ_raw(frame, wait_response=True)
    assert await _wait_until(
        lambda: radio.conn_state == RadioConnectionState.RECONNECTING, timeout_s=2.0
    )

    assert usb_audio.rx_running is False

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_link_down_while_ptt_active_parks_managed_tx_safely() -> None:
    """MOR-1440: link-down with a TX-active session must not orphan the key.

    Mirrors ``soft_disconnect``'s existing PTT-off teardown discipline: mark
    the managed-TX provider not-ready so any lease held across the gap is
    refused rather than granted onto a dead wire.
    """
    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.03
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()

    managed_tx = _FakeManagedTxRuntime()
    radio._managed_tx_runtime = managed_tx  # type: ignore[assignment]

    frame = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)
    for _ in range(radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD):
        with pytest.raises(RigplaneTimeoutError):
            await radio._send_civ_raw(frame, wait_response=True)
    assert await _wait_until(
        lambda: radio.conn_state == RadioConnectionState.RECONNECTING, timeout_s=2.0
    )

    assert managed_tx.ready_calls == [False]

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_civ_watchdog_rebaselines_after_transport_swap_with_banked_timeouts() -> (
    None
):
    """MOR-1440 review round 2 (B1 / probe a): stale baselines must not
    survive a transport swap.

    Reproduces the verifier's fake-transport probe directly against the
    detector. Every (re)connect installs a *brand-new* ``SerialCivTransport``
    whose ``rx_packet_count`` restarts at 0 (``SerialSessionDriver.connect``
    always constructs a fresh one), while ``_civ_request_tracker.timeout_count``
    is a lifetime counter that survives the swap untouched. Without
    re-baselining, a transport that just delivered genuine frames on a
    healthy, recovered link can still be declared dead from timeout evidence
    banked against the *old* transport/outage.
    """
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    await radio.connect()

    old_transport = radio._civ_transport
    threshold = radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD

    # Simulate having already baselined against the OLD (pre-outage)
    # transport, which delivered enough real traffic to build a
    # rx_packet_count high-water mark well above what a brand-new transport
    # starts at.
    radio._civ_watchdog_last_transport = old_transport
    radio._civ_watchdog_last_seen_rx_packets = 50
    radio._civ_watchdog_last_seen_timeouts = radio._civ_request_tracker.timeout_count
    radio._civ_consecutive_timeouts = 0

    # Outage: `threshold` CI-V command timeouts land on the tracker while the
    # watchdog is RECONNECTING. Its own evidence check short-circuits for any
    # state other than CONNECTED (see the loop in
    # ``_serial_civ_watchdog_loop``), so these are unconsumed until the next
    # CONNECTED tick -- e.g. a background poll already in flight when the
    # outage started, timing out mid-outage.
    for _ in range(threshold):
        radio._civ_request_tracker.note_timeout()

    # Reconnect installs a brand-new transport (as SerialSessionDriver.connect()
    # always does) that has already delivered real, fresh frames on the
    # recovered link.
    fresh_transport = SimpleNamespace(rx_packet_count=5)
    radio._civ_transport = fresh_transport  # type: ignore[assignment]

    crossed = radio._serial_civ_timeout_evidence_crossed_threshold()

    assert crossed is False, (
        "a freshly (re)connected transport that just delivered genuine "
        "frames must not be declared dead from timeouts banked against the "
        "OLD transport/outage"
    )
    assert radio._civ_consecutive_timeouts == 0
    assert radio._civ_watchdog_last_transport is fresh_transport
    assert radio._civ_watchdog_last_seen_rx_packets == 5
    assert (
        radio._civ_watchdog_last_seen_timeouts
        == radio._civ_request_tracker.timeout_count
    )

    # Restore a real transport before teardown: the background CI-V RX pump
    # (started by ``connect()``) and ``disconnect()`` both call real methods
    # on ``_civ_transport`` that the bare stub above does not implement.
    radio._civ_transport = old_transport  # type: ignore[assignment]
    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_rebaselines_watchdog_state() -> None:
    """MOR-1440 review round 2 (B1 item 2): the RECONNECTING -> CONNECTED
    transition in ``soft_reconnect`` must re-baseline the detector so an
    outage's banked timeouts are never credited to the just-recovered link.
    """
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    await radio.connect()
    await radio._stop_civ_data_watchdog()  # deterministic: no background tick races.

    # Simulate an outage that banked timeouts while short-circuited (state
    # RECONNECTING, per ``_serial_civ_watchdog_loop``): frozen baseline vs. a
    # tracker total that kept climbing underneath it.
    radio._civ_watchdog_last_seen_timeouts = radio._civ_request_tracker.timeout_count
    for _ in range(5):
        radio._civ_request_tracker.note_timeout()
    radio._civ_consecutive_timeouts = 2
    radio._conn_state = RadioConnectionState.RECONNECTING
    await radio._serial_session.disconnect()

    await radio.soft_reconnect()

    assert radio.conn_state == RadioConnectionState.CONNECTED
    assert radio._civ_consecutive_timeouts == 0
    assert (
        radio._civ_watchdog_last_seen_timeouts
        == radio._civ_request_tracker.timeout_count
    )
    assert getattr(radio, "_civ_watchdog_last_transport", None) is radio._civ_transport

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_link_down_settles_after_successful_reconnect_same_node(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-1440 review round 2 (B3 / probe b): off -> on, SAME node, must
    settle to exactly ONE link-down declaration.

    The existing 4 link-down tests all exercise ``fail_connect_calls=set(range(2,
    100))`` -- reconnect never succeeds -- which hid this defect entirely.
    Here the first reconnect attempt fails once (simulating the node still
    being briefly gone) and the second succeeds on the SAME node
    (``fail_connect_calls={2}``), widening the RECONNECTING window enough to
    deterministically confirm it via polling. While genuinely RECONNECTING,
    one more CI-V command times out -- representing e.g. a background poll
    that was already in flight when the outage started -- landing while the
    watchdog's evidence check is short-circuited for any state other than
    CONNECTED (see ``_serial_civ_watchdog_loop``), so it is banked,
    unconsumed, until the next CONNECTED tick. Pre-fix, that banked evidence
    gets credited as a lump against the just-recovered, healthy link on the
    very first evidence-check tick after reconnect (see
    ``_serial_civ_timeout_evidence_crossed_threshold``): a second, SPURIOUS
    link-down declaration on a link that actually recovered. The commander
    worker executes CI-V commands strictly one at a time, so this cannot be
    reproduced with genuinely concurrent in-flight sends -- direct
    ``note_timeout()`` calls are the honest way to model "another in-flight
    request timed out during the blind window" deterministically.
    """
    import logging

    link = _FakeSerialCivLink(fail_connect_calls={2})
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.02
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    radio._SERIAL_WATCHDOG_RETRY_S = 0.1
    threshold = radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD

    await radio.connect()
    assert radio.radio_ready is True

    frame = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)

    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        for _ in range(threshold):
            with pytest.raises(RigplaneTimeoutError):
                await radio._send_civ_raw(frame, wait_response=True)

        assert await _wait_until(
            lambda: radio.conn_state == RadioConnectionState.RECONNECTING,
            timeout_s=2.0,
        )

        # Bank `threshold` timeouts while genuinely RECONNECTING (confirmed
        # above) -- synchronous, no `await` in between, so nothing else can
        # run and move the state machine before these land.
        for _ in range(threshold):
            radio._civ_request_tracker.note_timeout()

        assert await _wait_until(
            lambda: radio.conn_state == RadioConnectionState.CONNECTED,
            timeout_s=2.0,
        )

        # A few more watchdog ticks to let the evidence check evaluate the
        # now-idle, recovered link.
        await asyncio.sleep(radio._SERIAL_WATCHDOG_INTERVAL_S * 10)

    error_lines = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "link-down" in r.getMessage()
    ]
    assert len(error_lines) == 1, (
        f"expected exactly one link-down ERROR line across the whole "
        f"off->on-same-node cycle, got {len(error_lines)}"
    )
    assert radio.conn_state == RadioConnectionState.CONNECTED
    assert radio.radio_ready is True

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_rediscovers_renumbered_node(tmp_path) -> None:
    """MOR-1453 review round 2 design ruling: PRIMARY identity is the USB
    adapter's own hardware ``serial_number``, captured via OS enumeration
    at the last successful connect -- no candidate port is ever opened to
    confirm it, closing the IC-705/X6200 shared CI-V address 0xA4
    collision entirely for adapters that expose one.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    new_path = tmp_path / "cu.usbserial-9931"

    topology = {
        "candidates": [
            SerialPortCandidate(
                device=str(old_path),
                description="",
                hwid=None,
                vid=0x0403,
                pid=0x6001,
                serial_number="FT-ABC123",
            ),
        ],
    }

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    async def _probe_forbidden(port: str) -> int | None:
        raise AssertionError(
            f"CI-V probe must never run when PRIMARY serial_number "
            f"identity is known (attempted on {port!r})"
        )

    link = _FakeSerialCivLink()
    audio = _FakeUsbAudioDriver()

    radio = Icom7610SerialRadio(
        device=str(old_path),
        civ_link=link,
        audio_driver=audio,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
        _civ_identity_probe=_probe_forbidden,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()  # deterministic: no background tick races.
    assert radio._serial_hw_identity == ("FT-ABC123", 0x0403, 0x6001)

    # Simulate the replug: link health drops (as the watchdog would
    # observe), the old node vanishes from the OS's enumeration, and a
    # new node with the SAME hardware serial_number (same physical
    # adapter) appears.
    link.connected = False
    link.ready = False
    link.healthy = False
    old_path.unlink()
    new_path.write_text("")
    topology["candidates"] = [
        SerialPortCandidate(
            device=str(new_path),
            description="",
            hwid=None,
            vid=0x0403,
            pid=0x6001,
            serial_number="FT-ABC123",
        ),
    ]

    await radio.soft_reconnect()

    assert radio._serial_device == str(new_path)
    assert link.device_history == [str(new_path)]
    assert audio.serial_port_history == [str(new_path)]
    assert radio.conn_state == RadioConnectionState.CONNECTED
    assert radio.radio_ready is True

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_recaptures_identity_after_adoption(
    tmp_path,
) -> None:
    """MOR-1453 review round 3 (M7 gap): ``_capture_serial_identity()``
    must run again after a successful adoption -- not just at the
    original connect -- otherwise ``_serial_hw_identity`` keeps
    describing the OLD node forever. ``pid`` is left unknown (``None``)
    at the first capture (so it never gates the PRIMARY match) and only
    becomes known post-replug, so the post-adoption value can only be
    correct if the second capture actually ran.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    new_path = tmp_path / "cu.usbserial-9931"

    topology = {
        "candidates": [
            SerialPortCandidate(
                device=str(old_path),
                description="",
                hwid=None,
                vid=0x0403,
                pid=None,
                serial_number="ADAPTER-SN",
            ),
        ],
    }

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    link = _FakeSerialCivLink()

    radio = Icom7610SerialRadio(
        device=str(old_path),
        civ_link=link,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()
    assert radio._serial_hw_identity == ("ADAPTER-SN", 0x0403, None)

    link.connected = False
    link.ready = False
    link.healthy = False
    old_path.unlink()
    new_path.write_text("")
    # Same adapter (same serial_number, matched by PRIMARY), but the OS
    # now also surfaces a pid it didn't report before the replug.
    topology["candidates"] = [
        SerialPortCandidate(
            device=str(new_path),
            description="",
            hwid=None,
            vid=0x0403,
            pid=0x1234,
            serial_number="ADAPTER-SN",
        ),
    ]

    await radio.soft_reconnect()

    assert radio._serial_device == str(new_path)
    # Only true if the post-adoption capture ran against the NEW path --
    # a stale value from the original connect would still show pid=None.
    assert radio._serial_hw_identity == ("ADAPTER-SN", 0x0403, 0x1234)

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_primary_ignores_empty_string_serial(
    tmp_path,
) -> None:
    """MOR-1453 review round 3 (reproduced defect): pyserial surfaces an
    empty string, not ``None``, for a stripped-descriptor adapter on some
    Linux/Windows hosts. An empty ``serial_number`` must never be treated
    as a fingerprint -- ``'' == ''`` must not let a candidate get adopted
    on the strength of PRIMARY matching alone.

    The candidate's vid/pid are deliberately IDENTICAL to ours (two cheap
    adapters of the same model, both with stripped descriptors) so the
    PRIMARY vid/pid cross-check (fix item 2) cannot independently save
    this test -- only treating ``""`` as "no fingerprint" (falling
    through to FALLBACK) can. The FALLBACK probe is wired to return
    ``None`` (unconfirmed), so a correct implementation must not adopt --
    but it MUST have reached the probe at all, proving PRIMARY was
    correctly bypassed rather than short-circuiting on the empty match.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    neighbor_path = tmp_path / "cu.usbserial-4471"  # a DIFFERENT, unrelated radio

    topology = {
        "candidates": [
            SerialPortCandidate(
                device=str(old_path),
                description="",
                hwid=None,
                vid=0x10C4,  # CP210x
                pid=0xEA60,
                serial_number="",  # degenerate descriptor
            ),
        ],
    }

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    probed: list[str] = []

    async def _identity_probe(port: str) -> int | None:
        probed.append(port)
        return None  # unconfirmed -- FALLBACK must not adopt on this alone

    link = _FakeSerialCivLink()

    radio = Icom7610SerialRadio(
        device=str(old_path),
        civ_link=link,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
        _civ_identity_probe=_identity_probe,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()
    assert radio._serial_hw_identity == ("", 0x10C4, 0xEA60)

    link.connected = False
    link.ready = False
    link.healthy = False
    old_path.unlink()
    neighbor_path.write_text("")
    topology["candidates"] = [
        SerialPortCandidate(
            device=str(neighbor_path),
            description="",
            hwid=None,
            vid=0x10C4,  # SAME vid/pid as ours -- does not gate FALLBACK
            pid=0xEA60,
            serial_number="",  # ALSO empty -- must not match on that alone
        ),
    ]

    await radio.soft_reconnect()

    # Empty serial must fall through to FALLBACK -- the probe must have
    # run (proving PRIMARY did not short-circuit on '' == '') -- and,
    # since it returned unconfirmed, nothing was adopted.
    assert probed == [str(neighbor_path)]
    assert radio._serial_device == str(old_path)  # unchanged -- never adopted
    assert link.device_history == []

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_primary_rejects_matching_serial_wrong_adapter(
    tmp_path,
) -> None:
    """MOR-1453 review round 3 fix item 2: a candidate whose
    ``serial_number`` coincidentally matches ours but whose vid/pid is
    KNOWN to differ (a cross-vendor serial-string collision) must not be
    adopted via PRIMARY -- and, since PRIMARY never opens a port, must
    never be probed either.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    wrong_vendor_path = tmp_path / "cu.usbserial-4471"

    topology = {
        "candidates": [
            SerialPortCandidate(
                device=str(old_path),
                description="",
                hwid=None,
                vid=0x10C4,
                pid=0xEA60,
                serial_number="SN-1234",
            ),
        ],
    }

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    async def _probe_forbidden(port: str) -> int | None:
        raise AssertionError(
            f"PRIMARY must never open a port, even to reject it ({port!r})"
        )

    link = _FakeSerialCivLink()

    radio = Icom7610SerialRadio(
        device=str(old_path),
        civ_link=link,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
        _civ_identity_probe=_probe_forbidden,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()
    assert radio._serial_hw_identity == ("SN-1234", 0x10C4, 0xEA60)

    link.connected = False
    link.ready = False
    link.healthy = False
    old_path.unlink()
    wrong_vendor_path.write_text("")
    topology["candidates"] = [
        SerialPortCandidate(
            device=str(wrong_vendor_path),
            description="",
            hwid=None,
            vid=0x0403,  # KNOWN different vendor despite the serial match
            pid=0x6001,
            serial_number="SN-1234",  # coincidental collision
        ),
    ]

    await radio.soft_reconnect()

    assert radio._serial_device == str(old_path)  # unchanged -- never adopted
    assert link.device_history == []

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_fallback_skips_known_different_adapter(
    tmp_path,
) -> None:
    """MOR-1453 review round 2 design ruling (port-hijack / 0xA4 collision
    reproduction): FALLBACK (adapter exposes no serial_number) must never
    probe -- never open -- a candidate whose enumerated vid/pid is a
    KNOWN different adapter, even though it would answer at our CI-V
    address if asked. The correct-vid/pid candidate is still probed and
    adopted, proving the exclusion is scoped, not a blanket FALLBACK
    disablement.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    our_new_path = tmp_path / "cu.usbserial-9931"  # same adapter, no serial_number
    neighbor_path = tmp_path / "cu.usbserial-4471"  # a DIFFERENT radio

    topology = {
        "candidates": [
            SerialPortCandidate(
                device=str(old_path),
                description="",
                hwid=None,
                vid=0x10C4,
                pid=0xEA60,
                serial_number=None,
            ),
        ],
    }

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    probed: list[str] = []

    async def _identity_probe(port: str) -> int | None:
        probed.append(port)
        # Both would answer at our configured CI-V address if asked --
        # the neighbor must never even be probed.
        return IC_7610_ADDR

    link = _FakeSerialCivLink()

    radio = Icom7610SerialRadio(
        device=str(old_path),
        civ_link=link,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
        _civ_identity_probe=_identity_probe,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()
    assert radio._serial_hw_identity == (None, 0x10C4, 0xEA60)

    link.connected = False
    link.ready = False
    link.healthy = False
    old_path.unlink()
    our_new_path.write_text("")
    neighbor_path.write_text("")
    topology["candidates"] = [
        SerialPortCandidate(
            device=str(neighbor_path),
            description="",
            hwid=None,
            vid=0x0403,  # KNOWN different adapter -- must never be opened
            pid=0x6001,
            serial_number=None,
        ),
        SerialPortCandidate(
            device=str(our_new_path),
            description="",
            hwid=None,
            vid=0x10C4,  # matches our own captured vid/pid
            pid=0xEA60,
            serial_number=None,
        ),
    ]

    await radio.soft_reconnect()

    assert probed == [str(our_new_path)]  # neighbor never probed/opened
    assert radio._serial_device == str(our_new_path)
    assert link.device_history == [str(our_new_path)]

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_fallback_vid_and_pid_guards_are_independent(
    tmp_path,
) -> None:
    """MOR-1453 review round 3 (M3b/M3c): the FALLBACK vid guard and pid
    guard must each be independently load-bearing. One candidate differs
    ONLY in vid, another ONLY in pid -- both must be skipped without ever
    being probed; only the fully-matching candidate is probed and
    adopted. A mutant that drops either individual guard (but not the
    other) is caught by this test alone.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    vid_only_diff_path = tmp_path / "cu.usbserial-1111"
    pid_only_diff_path = tmp_path / "cu.usbserial-2222"
    match_path = tmp_path / "cu.usbserial-3333"

    topology = {
        "candidates": [
            SerialPortCandidate(
                device=str(old_path),
                description="",
                hwid=None,
                vid=0x10C4,
                pid=0xEA60,
                serial_number=None,
            ),
        ],
    }

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    probed: list[str] = []

    async def _identity_probe(port: str) -> int | None:
        probed.append(port)
        return IC_7610_ADDR

    link = _FakeSerialCivLink()

    radio = Icom7610SerialRadio(
        device=str(old_path),
        civ_link=link,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
        _civ_identity_probe=_identity_probe,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()
    assert radio._serial_hw_identity == (None, 0x10C4, 0xEA60)

    link.connected = False
    link.ready = False
    link.healthy = False
    old_path.unlink()
    for p in (vid_only_diff_path, pid_only_diff_path, match_path):
        p.write_text("")
    topology["candidates"] = [
        SerialPortCandidate(
            device=str(vid_only_diff_path),
            description="",
            hwid=None,
            vid=0x0403,  # differs -- pid still matches
            pid=0xEA60,
            serial_number=None,
        ),
        SerialPortCandidate(
            device=str(pid_only_diff_path),
            description="",
            hwid=None,
            vid=0x10C4,  # matches -- pid differs
            pid=0x6001,
            serial_number=None,
        ),
        SerialPortCandidate(
            device=str(match_path),
            description="",
            hwid=None,
            vid=0x10C4,
            pid=0xEA60,
            serial_number=None,
        ),
    ]

    await radio.soft_reconnect()

    assert probed == [str(match_path)]
    assert radio._serial_device == str(match_path)

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_fallback_rejects_wrong_civ_address(
    tmp_path,
) -> None:
    """FALLBACK safety: with identity fully unknown (no serial_number, no
    vid/pid captured), a candidate that answers with the WRONG CI-V
    address must never be adopted.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    other_radio_path = tmp_path / "cu.usbserial-4471"

    topology: dict[str, list[SerialPortCandidate]] = {"candidates": []}

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    probed: list[str] = []

    async def _identity_probe(port: str) -> int | None:
        probed.append(port)
        return 0x94  # a different radio's CI-V address -- never ours

    link = _FakeSerialCivLink()

    radio = Icom7610SerialRadio(
        device=str(old_path),
        civ_link=link,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
        _civ_identity_probe=_identity_probe,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()
    assert radio._serial_hw_identity is None

    link.connected = False
    link.ready = False
    link.healthy = False
    old_path.unlink()
    other_radio_path.write_text("")
    topology["candidates"] = [
        SerialPortCandidate(device=str(other_radio_path), description="", hwid=None),
    ]

    await radio.soft_reconnect()

    assert probed == [str(other_radio_path)]
    assert radio._serial_device == str(old_path)  # unchanged -- never adopted
    assert link.device_history == []

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_soft_reconnect_skips_rediscovery_when_path_still_present(
    tmp_path,
) -> None:
    """Regression guard (MOR-1453 review round 2, B3): rediscovery must
    never even enumerate while the configured device node is still
    present -- the ordinary same-node reconnect path (MOR-1440's
    lifecycle pins) sees zero behavior change. A real sibling candidate
    (that would be adopted by serial_number if reached) is deliberately
    present so a mutant that removes the ``os.path.exists`` guard is
    caught instead of surviving on an empty candidate list.
    """
    device_path = tmp_path / "cu.usbserial-1420"
    device_path.write_text("")
    sibling_path = tmp_path / "cu.usbserial-9931"
    sibling_path.write_text("")

    enumerate_calls: list[None] = []

    def _enumerate() -> list[SerialPortCandidate]:
        enumerate_calls.append(None)
        return [
            SerialPortCandidate(
                device=str(sibling_path),
                description="",
                hwid=None,
                serial_number="WOULD-BE-ADOPTED-IF-REACHED",
            ),
        ]

    link = _FakeSerialCivLink(fail_connect_calls={2})

    radio = Icom7610SerialRadio(
        device=str(device_path),
        civ_link=link,
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        _enumerate_serial_ports_fn=_enumerate,
    )
    await radio.connect()
    await radio._stop_civ_data_watchdog()
    calls_after_connect = len(enumerate_calls)

    link.connected = False
    link.ready = False
    link.healthy = False

    with pytest.raises(ConnectionError, match="Failed to reconnect"):
        await radio.soft_reconnect()

    # Rediscovery must never enumerate again while the configured path
    # is still present -- the only enumeration is the one already
    # counted from connect()'s identity capture.
    assert len(enumerate_calls) == calls_after_connect
    assert radio._serial_device == str(device_path)

    await radio.disconnect()


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("/dev/cu.usbserial-1420", "/dev/cu.usbserial*"),
        ("/dev/cu.usbmodem-IC7610", "/dev/cu.usbmodem*"),
        ("/dev/cu.SLAB_USBtoUART2", "/dev/cu.SLAB_USBtoUART*"),
        ("/dev/ttyS0", "/dev/ttyS*"),
        ("/dev/customdevice", "/dev/customdevice*"),
    ],
)
def test_derive_reconnect_glob_table(device: str, expected: str) -> None:
    """MOR-1453 review round 2 (B4): the derived pattern eats the
    separator -- ``cu.usbserial-1420`` -> ``cu.usbserial*``, not
    ``cu.usbserial-*`` -- and a path with no trailing digit/suffix run
    falls back to appending ``*`` unchanged.
    """
    assert _derive_reconnect_glob(device) == expected


@pytest.mark.asyncio
async def test_serial_audio_opus_contract_uses_usb_driver_lifecycle() -> None:
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    received: list[bytes] = []
    await radio.start_audio_rx_opus(lambda packet: received.append(packet.data))
    usb_audio.emit_rx_pcm(b"\x01\x02" * 960)
    await asyncio.sleep(0.05)
    await radio.start_audio_tx_opus()
    await radio.push_audio_tx_opus(b"\x11\x22" * 960)
    await radio.stop_audio_tx_opus()
    await radio.stop_audio_rx_opus()
    await radio.disconnect()

    assert usb_audio.rx_starts == 1
    assert usb_audio.tx_starts == 1
    assert received
    assert received[0] == b"\x01\x02" * 960
    assert usb_audio.tx_frames[0] == b"\x11\x22" * 960


@pytest.mark.asyncio
async def test_serial_audio_pcm_contract_bridge_compatible() -> None:
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
        audio_codec=AudioCodec.OPUS_1CH,
    )
    await radio.connect()

    rx_pcm: list[bytes] = []
    await radio.start_audio_rx_pcm(lambda frame: rx_pcm.append(frame or b""))
    usb_audio.emit_rx_pcm(b"\x21\x43" * 960)
    await asyncio.sleep(0.05)

    await radio.start_audio_tx_pcm()
    await radio.push_audio_tx_pcm(b"\x10\x20" * 960)
    await radio.stop_audio_tx_pcm()
    await radio.stop_audio_rx_pcm()
    await radio.disconnect()

    assert rx_pcm
    assert rx_pcm[0] == b"\x21\x43" * 960
    assert usb_audio.tx_frames[0] == b"\x10\x20" * 960


@pytest.mark.asyncio
async def test_serial_audio_tx_requires_start() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_FakeUsbAudioDriver(),
    )
    await radio.connect()
    with pytest.raises(RuntimeError, match="Audio TX not started"):
        await radio.push_audio_tx_opus(b"\x00" * 1920)
    await radio.disconnect()


def test_serial_scope_pacing_profile_is_separate_from_lan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ICOM_CIV_MIN_INTERVAL_MS", raising=False)
    monkeypatch.delenv("ICOM_SERIAL_CIV_MIN_INTERVAL_MS", raising=False)
    serial_radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
    )
    lan_radio = IcomRadio("192.168.55.40")
    assert serial_radio._civ_min_interval > lan_radio._civ_min_interval


@pytest.mark.asyncio
async def test_serial_scope_enable_disable_full_lifecycle_commands() -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    await radio.enable_scope(policy="fast")
    await radio.disable_scope(policy="fast")
    await radio.disconnect()

    # Only parse full CI-V frames (min 6 bytes); skip short/control bytes
    signatures = []
    for frame in link.sent_frames:
        if len(frame) < 6:
            continue
        civ = parse_civ_frame(frame)
        signatures.append((civ.command, civ.sub, civ.data))

    assert len(signatures) >= 4, (
        f"Expected at least 4 scope CI-V frames, got {len(signatures)}"
    )
    assert signatures[0] == (0x27, 0x10, b"\x01")
    assert signatures[1] == (0x27, 0x11, b"\x01")
    assert signatures[2] == (0x27, 0x11, b"\x00")
    assert signatures[3] == (0x27, 0x10, b"\x00")


@pytest.mark.asyncio
async def test_serial_scope_capture_scope_frame() -> None:
    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _scope_wave_frame(pixels=b"\x31\x32\x33"))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    frame = await radio.capture_scope_frame(timeout=1.0)
    await radio.disable_scope(policy="fast")
    await radio.disconnect()

    assert frame.receiver == 0
    assert frame.start_freq_hz == 14_000_000
    assert frame.end_freq_hz == 14_350_000
    assert frame.pixels == b"\x31\x32\x33"


@pytest.mark.asyncio
async def test_serial_scope_callback_streaming_path() -> None:
    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _scope_wave_frame(pixels=b"\x51\x52"))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    seen = []
    radio.on_scope_data(seen.append)
    await radio.enable_scope(policy="verify", timeout=1.0)
    assert await _wait_until(lambda: len(seen) == 1, timeout_s=1.0)
    await radio.disable_scope(policy="fast")
    await radio.disconnect()
    assert seen[0].pixels == b"\x51\x52"


@pytest.mark.asyncio
async def test_serial_scope_low_baud_guardrail_rejects_without_override() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        baudrate=19200,
        civ_link=_FakeSerialCivLink(),
    )
    await radio.connect()
    with pytest.raises(CommandError, match="baudrate"):
        await radio.enable_scope(policy="fast")
    await radio.disconnect()


@pytest.mark.asyncio
async def test_scope_session_restore_preserves_panel_and_output_state_exactly() -> None:
    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _scope_state_response(0x10, True))
    link.queue_response_on_send(2, _scope_state_response(0x11, False))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()

    initial = await radio.get_scope_session_state()
    await radio.enable_scope(policy="fast")
    await radio.restore_scope_session_state(initial)
    await radio.disconnect()

    signatures = [
        (frame.command, frame.sub, frame.data)
        for payload in link.sent_frames
        if len(payload) >= 6
        for frame in [parse_civ_frame(payload)]
    ]
    assert initial == (True, False)
    assert signatures == [
        (0x27, 0x10, b""),
        (0x27, 0x11, b""),
        (0x27, 0x10, b"\x01"),
        (0x27, 0x11, b"\x01"),
        (0x27, 0x10, b"\x01"),
        (0x27, 0x11, b"\x00"),
    ]


@pytest.mark.asyncio
async def test_rejected_low_baud_scope_enable_never_emits_scope_off() -> None:
    from rigplane.web.radio_poller import (
        CommandQueue,
        DisableScope,
        EnableScope,
        RadioPoller,
    )

    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        baudrate=19200,
        civ_link=link,
    )
    await radio.connect()
    queue = CommandQueue()
    poller = RadioPoller(radio, queue, radio_state=radio.radio_state)

    with pytest.raises(CommandError, match="baudrate"):
        await poller._execute(EnableScope(generation=1))
    await poller._execute(DisableScope(generation=2))
    await radio.disconnect()

    signatures = [
        (frame.command, frame.sub, frame.data)
        for payload in link.sent_frames
        if len(payload) >= 6
        for frame in [parse_civ_frame(payload)]
    ]
    assert signatures == []
    assert (0x27, 0x10, b"\x00") not in signatures
    assert (0x27, 0x11, b"\x00") not in signatures


@pytest.mark.asyncio
async def test_verify_timeout_after_scope_on_rolls_back_exact_initial_state() -> None:
    from rigplane.web.radio_poller import (
        CommandQueue,
        DisableScope,
        EnableScope,
        RadioPoller,
    )

    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _scope_state_response(0x10, False))
    link.queue_response_on_send(2, _scope_state_response(0x11, False))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    real_enable_scope = radio.enable_scope

    async def enable_scope_with_short_verify(*, policy: str) -> None:
        await real_enable_scope(policy=policy, timeout=0.01)

    radio.enable_scope = enable_scope_with_short_verify  # type: ignore[method-assign]
    poller = RadioPoller(radio, CommandQueue(), radio_state=radio.radio_state)

    with pytest.raises(RigplaneTimeoutError, match="verification timed out"):
        await poller._execute(EnableScope(policy="verify", generation=1))
    sent_before_disable = len(link.sent_frames)
    await poller._execute(DisableScope(generation=2))
    assert len(link.sent_frames) == sent_before_disable
    await radio.disconnect()

    signatures = [
        (frame.command, frame.sub, frame.data)
        for payload in link.sent_frames
        if len(payload) >= 6
        for frame in [parse_civ_frame(payload)]
    ]
    assert signatures == [
        (0x27, 0x10, b""),
        (0x27, 0x11, b""),
        (0x27, 0x10, b"\x01"),
        (0x27, 0x11, b"\x01"),
        (0x27, 0x10, b"\x00"),
        (0x27, 0x11, b"\x00"),
    ]


@pytest.mark.asyncio
async def test_serial_scope_enable_disconnected_low_baud_keeps_connection_error_contract() -> (
    None
):
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        baudrate=19200,
        civ_link=_FakeSerialCivLink(),
    )
    with pytest.raises(ConnectionError, match="Not connected"):
        await radio.enable_scope(policy="fast")


@pytest.mark.asyncio
async def test_serial_scope_low_baud_guardrail_override_allows_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        baudrate=19200,
        allow_low_baud_scope=True,
        civ_link=_FakeSerialCivLink(),
    )
    await radio.connect()
    with caplog.at_level("WARNING"):
        await radio.enable_scope(policy="fast")
    await radio.disable_scope(policy="fast")
    await radio.disconnect()
    assert "baudrate" in caplog.text.lower()
    assert "override" in caplog.text.lower()


@pytest.mark.asyncio
async def test_serial_scope_flood_does_not_starve_get_frequency() -> None:
    link = _FakeSerialCivLink()
    for _ in range(120):
        link.queue_response_on_send(3, _scope_wave_frame(pixels=b"\x11\x12\x13"))
    link.queue_response_on_send(3, _freq_response_frame(14_074_000))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    await radio.enable_scope(policy="fast")
    assert await radio.get_freq() == 14_074_000
    await radio.disable_scope(policy="fast")
    await radio.disconnect()


# ---------------------------------------------------------------------------
# GH#1382 regression: TX always opens USB CODEC as mono (channels=1)
# IC-7610 USB CODEC mic input is mono-only; opening with channels=2 causes
# PortAudio to negotiate a 2-channel stream, producing 5-10s of TX artifacts
# while CoreAudio settles (regression introduced with stereo-first codec in
# PCM_2CH_16BIT becoming the global default, commit 8cc677df).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serial_tx_always_uses_mono_channels_regression_gh1382() -> None:
    """start_audio_tx_pcm always opens USB CODEC driver with channels=1 (GH#1382).

    Even when the global audio capabilities default to 2 channels (because
    PCM_2CH_16BIT is the preferred codec), the IC-7610 serial TX must open
    the USB CODEC with channels=1.  Callers that pass channels=2 (e.g. the
    CLI reading audio_caps.default_channels) must be clamped to mono.
    """
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
        audio_codec=AudioCodec.PCM_2CH_16BIT,  # stereo codec — reproduces regression
    )
    await radio.connect()

    # Simulate CLI passing channels=2 from audio_caps.default_channels
    await radio.start_audio_tx_pcm(sample_rate=48000, channels=2, frame_ms=20)

    # USB CODEC must be opened mono regardless of what caller requested
    assert usb_audio.tx_start_kwargs.get("channels") == 1, (
        "IC-7610 serial TX must open USB CODEC as mono (channels=1) "
        "regardless of the active audio codec or caller-supplied channels value"
    )
    await radio.stop_audio_tx_pcm()
    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_tx_default_uses_mono_channels() -> None:
    """Default call to start_audio_tx_pcm uses channels=1."""
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    await radio.start_audio_tx_pcm()
    assert usb_audio.tx_start_kwargs.get("channels") == 1
    await radio.stop_audio_tx_pcm()
    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_tx_accepts_none_args_resolving_to_defaults() -> None:
    """Explicit None args resolve to serial defaults (LSP parity with base).

    The base ``AudioRuntimeMixin.start_audio_tx_pcm`` accepts ``int | None``;
    the serial override must too, so a base-typed caller passing ``None`` does
    not hit a ``TypeError``.  ``None`` resolves to sample_rate=48000,
    frame_ms=20, channels=1 (USB CODEC mono clamp).
    """
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    await radio.start_audio_tx_pcm(sample_rate=None, channels=None, frame_ms=None)
    assert usb_audio.tx_start_kwargs.get("sample_rate") == 48000
    assert usb_audio.tx_start_kwargs.get("frame_ms") == 20
    assert usb_audio.tx_start_kwargs.get("channels") == 1
    await radio.stop_audio_tx_pcm()
    await radio.disconnect()


class _DuplexAwareUsbAudioDriver(_FakeUsbAudioDriver):
    """Fake USB driver that exposes the MOR-534 ``duplex_mode`` property."""

    def __init__(self, mode: str = "exclusive") -> None:
        super().__init__()
        self._duplex_mode = mode

    @property
    def duplex_mode(self) -> str:
        return self._duplex_mode


class _RaisingDuplexUsbAudioDriver(_FakeUsbAudioDriver):
    """Fake USB driver whose ``duplex_mode`` raises (offline enumeration)."""

    @property
    def duplex_mode(self) -> str:
        raise RuntimeError("PortAudio device enumeration failed")


def test_serial_audio_descriptors_present_on_serial_backends() -> None:
    """MOR-536: both MOR-532 descriptors exist on the Icom serial backends."""
    for radio_cls in (Icom7610SerialRadio, Ic705SerialRadio):
        radio = radio_cls(
            device="/dev/ttyUSB0",
            civ_link=_FakeSerialCivLink(),
            audio_driver=_FakeUsbAudioDriver(),
        )
        assert radio.audio_tx_codec == AudioCodec.PCM_1CH_16BIT
        assert radio.audio_duplex_mode == "full"


def test_serial_audio_tx_codec_is_mono_pcm_regardless_of_rx_codec() -> None:
    """The serial USB CODEC TX path is always mono PCM (GH#1382 clamp)."""
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_FakeUsbAudioDriver(),
        audio_codec=AudioCodec.PCM_2CH_16BIT,
    )
    assert radio.audio_tx_codec == AudioCodec.PCM_1CH_16BIT


def test_serial_audio_duplex_mode_delegates_to_driver() -> None:
    """``audio_duplex_mode`` returns the driver's MOR-534 duplex policy."""
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_DuplexAwareUsbAudioDriver("exclusive"),
    )
    assert radio.audio_duplex_mode == "exclusive"

    radio_full = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_DuplexAwareUsbAudioDriver("full"),
    )
    assert radio_full.audio_duplex_mode == "full"


def test_serial_audio_duplex_mode_defaults_to_full_when_driver_raises() -> None:
    """Device enumeration failures (offline hosts) fall back to ``"full"``."""
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_RaisingDuplexUsbAudioDriver(),
    )
    assert radio.audio_duplex_mode == "full"
