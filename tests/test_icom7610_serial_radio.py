"""Lifecycle and readiness tests for Icom7610SerialRadio."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from rigplane import IcomRadio, RadioConnectionState
from rigplane.backends._icom_serial_base import (
    _IcomSerialRadioBase,
    _derive_reconnect_glob,
)
from rigplane.backends.discovery import SerialPortCandidate
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    _CMD_FREQ_GET,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.core.acquisition_scheduler import AcquisitionScheduler
from rigplane.core.civ import CivRequestKey
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateStore
from rigplane.exceptions import CommandError, ConnectionError
from rigplane.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import ActuationResult, ManagedTxOutcome
from rigplane.types import AudioCodec
from rigplane.types import bcd_encode
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import _await_initial_state_acquisition

try:  # MOR-3071: absent on the merge-base — the identity-gate tests below
    # fail first there (RED); the try keeps the module collectable so each
    # failure is an individual, behavioral one.
    from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
except ImportError:  # pragma: no cover — RED-only path on the merge-base
    RadioIdentity = None  # type: ignore[assignment,misc]
    RadioIdentityStatus = None  # type: ignore[assignment,misc]


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


async def _silence_clock_reset_gap(radio) -> None:  # type: ignore[no-untyped-def]
    """Quiet gap between timed-out commands (MOR-2861 silence path).

    The silence clock survives the tick that consumes a command-timeout
    delta (a timeout is itself silence evidence), so back-to-back timed-out
    commands would let it run continuously across their windows and declare
    at 2 x answer window + one tick — before the consecutive-timeout
    threshold the MOR-1440 tests pin. Sleeping past two watchdog ticks
    lets one tick see nothing outstanding and clear the clock, keeping each
    command's accrued silence below the limit.
    """
    await asyncio.sleep(radio._SERIAL_WATCHDOG_INTERVAL_S * 3)


class _FakeSerialCivLink:
    def __init__(
        self,
        *,
        fail_connect: BaseException | None = None,
        fail_connect_calls: set[int] | None = None,
        fail_connect_calls_exc: BaseException | None = None,
        lifecycle_events: list[tuple[str, object | None]] | None = None,
        ptt_off_answer: int | None = 0xFB,
        answer_identity: bool = True,
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
        self.lifecycle_events = lifecycle_events
        self.ptt_off_answer = ptt_off_answer
        # MOR-3071: every serial connect now gates CONNECTED on the
        # profile's identity read (19 00), so by default the fake answers
        # it like a real radio would; tests that need an identity-silent
        # link (the no_response gate suite) pass answer_identity=False.
        self.answer_identity = answer_identity
        self.identity_queries = 0

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
        if self.lifecycle_events is not None:
            self.lifecycle_events.append(("disconnect", None))
        self.disconnect_calls += 1
        self.connected = False
        self.ready = False
        self.healthy = False

    async def send(self, frame: bytes) -> None:
        if not self.connected:
            raise ConnectionError("Serial CI-V link is disconnected.")
        payload = bytes(frame)
        if self.lifecycle_events is not None:
            self.lifecycle_events.append(("send", payload))
        self.sent_frames.append(payload)
        send_no = len(self.sent_frames)
        for response in self._responses_by_send.pop(send_no, []):
            self._responses.put_nowait(response)
        # MOR-3071: the connect-time identity read. The reply is a real
        # 19 00 answer from the frame's own radio address, carrying the
        # model-ID byte (0x94 — the payload is opaque and never compared
        # against the CI-V address).
        if payload[4:-1] == b"\x19\x00":
            self.identity_queries += 1
            if self.answer_identity:
                self._responses.put_nowait(
                    bytes(
                        (
                            0xFE,
                            0xFE,
                            payload[3],
                            payload[2],
                            0x19,
                            0x00,
                            0x94,
                            0xFD,
                        )
                    )
                )
            return
        # ``CoreRadio.actuate`` waits for the answer to the ``1C 00 00`` unkey
        # and distrusts it while another write's answer may be unclaimed
        # (MOR-2860), so the managed TX writes are answered here: FB for PTT ON
        # (``1C 00 01``), stop CW (``17 FF``) and tuner off (``1C 01 00``), and
        # ``ptt_off_answer`` for the unkey (``None`` answers nothing).
        answer = {
            b"\x1c\x00\x01": 0xFB,
            b"\x17\xff": 0xFB,
            b"\x1c\x01\x00": 0xFB,
            b"\x1c\x00\x00": self.ptt_off_answer,
        }.get(payload[4:-1])
        if answer is not None:
            self._responses.put_nowait(
                bytes((0xFE, 0xFE, payload[3], payload[2], answer, 0xFD))
            )

    async def send_written(
        self, frame: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        if is_current is not None and not is_current():
            raise CommandError("Serial CI-V write is no longer current.")
        await self.send(frame)

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
    # Send #1 is the MOR-3071 identity read (answered by the fake itself).
    link.queue_response_on_send(2, _freq_response_frame(14_074_000))
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
    assert radio._managed_tx_runtime is None

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


@pytest.mark.asyncio
async def test_serial_connect_arms_mounted_composition_with_actual_transport(
    tmp_path,
) -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)

    await radio.connect()

    transport = radio._civ_transport
    assert transport is not None
    assert composition._live_transport_identity is transport
    assert composition._active_provider is None

    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)
    assert composition._active_provider is not None
    assert composition._active_provider.transport_identity is transport
    assert composition._active_provider.provider_generation == 1

    await radio.disconnect()
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_mounted_session_keeps_serial_transport_as_sole_readiness_identity(
    tmp_path,
) -> None:
    from rigplane.cli import _ManagedTxRadioSession

    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    session = _ManagedTxRadioSession(radio, composition)

    entered = await session.__aenter__()
    transport = radio._civ_transport
    assert entered is radio
    assert transport is not None
    assert composition._live_transport_identity is transport
    assert composition._live_transport_identity is not radio

    await session.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_serial_soft_reconnect_rearms_same_composition_on_new_transport(
    tmp_path,
) -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)

    await radio.connect()
    await radio._stop_civ_data_watchdog()
    first_transport = radio._civ_transport
    assert composition._active_provider is not None
    assert composition._active_provider.transport_identity is first_transport
    assert composition._active_provider.provider_generation == 1

    link.ready = False
    link.healthy = False
    await radio.soft_reconnect()

    second_transport = radio._civ_transport
    assert second_transport is not None
    assert second_transport is not first_transport
    assert radio._managed_tx_composition is composition
    assert composition._active_provider is not None
    assert composition._active_provider.transport_identity is second_transport
    assert composition._active_provider.provider_generation == 2

    await radio.disconnect()
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_serial_failed_soft_reconnect_leaves_composition_not_ready(
    tmp_path,
) -> None:
    link = _FakeSerialCivLink(fail_connect_calls={2})
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)

    await radio.connect()
    await radio._stop_civ_data_watchdog()
    assert composition._active_provider is not None
    link.ready = False
    link.healthy = False

    with pytest.raises(ConnectionError, match="Failed to reconnect serial session"):
        await radio.soft_reconnect()

    projection = await composition.authority.snapshot()
    assert composition._active_provider is None
    assert projection.provider_generation is None
    assert await composition.authority.transmit_on() is ManagedTxOutcome.REJECTED
    with pytest.raises(RuntimeError, match="raw PTT ON is blocked"):
        await radio.set_ptt(True)

    await radio.disconnect()
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_serial_disconnect_retires_composition_before_transport_close(
    tmp_path,
) -> None:
    lifecycle_events: list[tuple[str, object | None]] = []

    async def retire_provider(event) -> None:  # type: ignore[no-untyped-def]
        lifecycle_events.append(("retire", event.transport_identity))

    link = _FakeSerialCivLink(lifecycle_events=lifecycle_events)
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    composition = ManagedTxComposition(
        radio,
        config_path=tmp_path / "managed-tx.json",
        retire_provider=retire_provider,
    )
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)
    await radio.connect()
    transport = radio._civ_transport
    keyed = await composition.authority.submit_ptt(True, "serial-owner")
    assert keyed.outcome is ManagedTxOutcome.ACCEPTED
    await keyed.wait_settlement()
    lifecycle_events.clear()

    await radio.disconnect()

    expected_off = bytes(radio._commands.ptt_off(to_addr=radio._radio_addr))
    off_index = lifecycle_events.index(("send", expected_off))
    retire_index = lifecycle_events.index(("retire", transport))
    close_index = lifecycle_events.index(("disconnect", None))
    assert off_index < retire_index < close_index
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_idle_force_off_on_an_answering_radio_leaves_no_debt(tmp_path) -> None:
    """MOR-2860: an idle ForceOff that the radio answers with FB owes nothing."""
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)
    await radio.connect()
    try:
        assert await composition.authority.force_off() is ManagedTxOutcome.ACCEPTED
        state = (await composition.authority.snapshot()).state
    finally:
        await radio.disconnect()
        await composition.shutdown(asyncio.Event())

    assert state.last_actuation is not None
    assert state.last_actuation.result is ActuationResult.ACCEPTED
    assert not state.release_required


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
    assert radio._serial_watchdog_retry_delay(1025) == cap
    assert radio._serial_watchdog_retry_delay(10**100) == cap
    # Monotonic non-decreasing.
    delays = [radio._serial_watchdog_retry_delay(n) for n in range(1, 12)]
    assert delays == sorted(delays)


@pytest.mark.asyncio
async def test_serial_watchdog_recovers_after_overflow_sized_outage() -> None:
    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 1027)))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.0  # type: ignore[attr-defined]
    radio._SERIAL_WATCHDOG_RETRY_S = 0.0  # type: ignore[attr-defined]
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.0  # type: ignore[attr-defined]

    await radio.connect()
    link.ready = False
    link.healthy = False

    assert await _wait_until(lambda: link.connect_calls >= 1027, timeout_s=2.0)
    assert await _wait_until(lambda: radio.radio_ready)
    assert radio.conn_state == RadioConnectionState.CONNECTED
    assert radio._civ_data_watchdog_task is not None
    assert not radio._civ_data_watchdog_task.done()

    await radio.disconnect()


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
        await _silence_clock_reset_gap(radio)

        for _ in range(radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD - 1):
            with pytest.raises(RigplaneTimeoutError):
                await radio._send_civ_raw(frame, wait_response=True)
            await _silence_clock_reset_gap(radio)

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
        await _silence_clock_reset_gap(radio)
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
        await _silence_clock_reset_gap(radio)
    assert await _wait_until(
        lambda: radio.conn_state == RadioConnectionState.RECONNECTING, timeout_s=2.0
    )

    assert usb_audio.rx_running is False

    await radio.disconnect()


@pytest.mark.asyncio
async def test_silent_link_with_ready_session_declares_link_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2861: a merely-"ready" session with polls outstanding and no parsed
    CI-V frame must be declared link-down (which parks managed TX).

    Fixture shape mirrors the 2026-09-28 IC-7300 incident: the raw session
    reads ready, ``rx_packet_count`` never advances, and the request tracker
    records zero timeouts (fire-and-forget polls and scope GETs cancelled at
    0.2 s never produce one). A fake clock advances artificial seconds while
    a live poller is emulated: a fresh keyed sink (the web poller's
    BACKGROUND shape) is re-dispatched well inside the answer window, so at
    every watchdog tick a poll younger than the window is outstanding.

    Tightened to the silence limit N = 2 x answer window + one watchdog
    tick: no declaration below N, declaration just past it.
    """
    import logging

    # Reconnect attempts fail (device never returns on the same path) so the
    # link-down state does not self-heal mid-assertion.
    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()
    assert radio.radio_ready is True

    tracker = radio._civ_request_tracker

    # Fake clock: jump artificial seconds instead of waiting real ones. Sink
    # stamps are re-based onto the fake clock so waiter ages and the silence
    # clock measure the same time.
    now = {"t": time.monotonic()}
    radio._civ_silence_time_source = lambda: now["t"]  # type: ignore[attr-defined]
    window_s = radio._civ_get_timeout
    silence_limit_s = 2.0 * window_s + radio._SERIAL_WATCHDOG_INTERVAL_S
    poll_cadence_s = window_s / 2.0

    def _dispatch_poll() -> None:
        tracker.register_ack(
            wait=False, response_key=CivRequestKey(command=0x03, sub=None)
        )
        tracker._ack_waiters[-1].created_monotonic = now["t"]

    _dispatch_poll()
    assert tracker.timeout_count == 0

    managed_tx = _FakeManagedTxRuntime()
    radio._managed_tx_runtime = managed_tx  # type: ignore[assignment]

    # The silence clock must start while the fake clock still reads t0: the
    # watchdog needs at least one evidence tick (polls outstanding, rx frozen)
    # BEFORE time advances, otherwise elapsed could never reach the limit.
    assert await _wait_until(
        lambda: getattr(radio, "_civ_silence_started_monotonic", None) is not None,
        timeout_s=2.0,
    ), "watchdog never started the silence clock with polls outstanding"

    started = now["t"]

    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        # A live poller keeps polling the silent radio; step the clock to
        # just below the limit. Below N the link must NOT be declared down.
        while now["t"] - started < silence_limit_s - 2.0 * poll_cadence_s:
            now["t"] += poll_cadence_s
            _dispatch_poll()
            await asyncio.sleep(0.02)
        assert radio.conn_state == RadioConnectionState.CONNECTED, (
            "link declared down below the silence limit "
            f"({now['t'] - started:.1f}s < {silence_limit_s:.1f}s)"
        )

        # Cross N: silence with continuously fresh polls outstanding.
        now["t"] = started + silence_limit_s + poll_cadence_s
        _dispatch_poll()
        assert await _wait_until(
            lambda: radio.conn_state == RadioConnectionState.RECONNECTING,
            timeout_s=2.0,
        ), "silent link with polls outstanding never declared link-down"

    error_lines = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "link-down" in r.getMessage()
    ]
    assert len(error_lines) == 1, (
        f"expected exactly one link-down ERROR line, got {len(error_lines)}"
    )
    assert managed_tx.ready_calls == [False]

    await radio.disconnect()


@pytest.mark.asyncio
async def test_polls_answered_within_window_do_not_declare_link_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2861 negative: polls outstanding and answered within one answer
    window, cycling far past the silence limit, must not declare link-down.

    Each cycle dispatches a fresh keyed poll and parses its answer well
    inside the window (the overlapping fire-and-forget pipeline the web
    poller drives: the next read is dispatched before the previous waiter
    is retired). The parsed frame must reset the silence clock; without
    that reset, the clock started on an intra-cycle frozen tick would
    accumulate across cycles and past the limit, declaring a link that
    answers every poll within the window down.
    """
    import logging

    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()
    assert radio.radio_ready is True

    tracker = radio._civ_request_tracker
    transport = radio._civ_transport

    now = {"t": time.monotonic()}
    radio._civ_silence_time_source = lambda: now["t"]  # type: ignore[attr-defined]
    window_s = radio._civ_get_timeout
    silence_limit_s = 2.0 * window_s + radio._SERIAL_WATCHDOG_INTERVAL_S
    cadence_s = window_s / 2.0

    def _dispatch_poll() -> int:
        token = tracker.register_ack(
            wait=False, response_key=CivRequestKey(command=0x03, sub=None)
        )
        tracker._ack_waiters[-1].created_monotonic = now["t"]
        return token

    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        previous: int | None = None
        # Enough cycles that, without the parsed-frame reset, the clock
        # accumulated across cycles would cross the silence limit.
        cycles = int(silence_limit_s / cadence_s) + 2
        for _ in range(cycles):
            now["t"] += cadence_s
            token = _dispatch_poll()
            # Frozen-rx ticks with the fresh poll outstanding.
            await asyncio.sleep(0.02)
            # The radio answers well within the window: a CI-V frame parses.
            transport.rx_packet_count += 1
            # Retire the answered poll only after the next one is in flight.
            if previous is not None:
                assert tracker.unregister_ack_sink(previous) is True
            previous = token
            # Ticks with rx advanced: the parsed frame resets the clock.
            await asyncio.sleep(0.02)
        assert tracker.unregister_ack_sink(previous) is True

        # The link goes quiet and is held past the silence limit.
        now["t"] += silence_limit_s + 1.0
        await asyncio.sleep(radio._SERIAL_WATCHDOG_INTERVAL_S * 10)

        assert radio.conn_state == RadioConnectionState.CONNECTED, (
            "healthy link with polls answered within the window was declared link-down"
        )
        error_lines = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "link-down" in r.getMessage()
        ]
        assert error_lines == [], (
            f"healthy answered-poll link produced {len(error_lines)} "
            "link-down ERROR line(s)"
        )

    await radio.disconnect()


@pytest.mark.asyncio
async def test_silent_link_clock_clears_when_nothing_is_outstanding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2861 negative: a quiet link with nothing outstanding is idle,
    not down — even held far past the silence limit.

    The silence clock starts on one frozen tick with a poll outstanding,
    then the poll's waiter is retired (its caller gave up), leaving nothing
    outstanding. Without the "nothing outstanding clears the silence
    clock" guard, the started clock would run to the limit and declare a
    healthy, merely-quiet link down.
    """
    import logging

    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()
    assert radio.radio_ready is True

    tracker = radio._civ_request_tracker
    now = {"t": time.monotonic()}
    radio._civ_silence_time_source = lambda: now["t"]  # type: ignore[attr-defined]
    silence_limit_s = 2.0 * radio._civ_get_timeout + radio._SERIAL_WATCHDOG_INTERVAL_S

    token = tracker.register_ack(
        wait=False, response_key=CivRequestKey(command=0x03, sub=None)
    )
    tracker._ack_waiters[-1].created_monotonic = now["t"]
    assert await _wait_until(
        lambda: getattr(radio, "_civ_silence_started_monotonic", None) is not None,
        timeout_s=2.0,
    ), "watchdog never started the silence clock with a poll outstanding"

    # Nothing outstanding any more: the poll's caller retired its waiter.
    assert tracker.unregister_ack_sink(token) is True

    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        now["t"] += silence_limit_s + 5.0
        await asyncio.sleep(radio._SERIAL_WATCHDOG_INTERVAL_S * 10)

        assert radio.conn_state == RadioConnectionState.CONNECTED, (
            "quiet link with nothing outstanding was declared link-down"
        )
        error_lines = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "link-down" in r.getMessage()
        ]
        assert error_lines == [], (
            f"quiet healthy link produced {len(error_lines)} link-down ERROR line(s)"
        )

    await radio.disconnect()


@pytest.mark.asyncio
async def test_lost_keyed_sink_alone_does_not_declare_link_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2861 negative: one lost keyed sink on an otherwise quiet healthy
    link, held past the silence limit, must not declare link-down.

    A keyed fire-and-forget sink whose answer never comes lives in the
    tracker until the 10 s stale cleanup. A waiter older than the answer
    window is no longer evidence that the radio owes us data (its answer
    had a full window to arrive), so the watchdog's pending count must
    ignore it: after the window the link is merely quiet, and a quiet
    link is not down. Held far past the limit — and past any slower
    cadence-derived limit — the lone stale sink must not trip anything.
    """
    import logging

    link = _FakeSerialCivLink(fail_connect_calls=set(range(2, 100)))
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()
    assert radio.radio_ready is True

    tracker = radio._civ_request_tracker
    now = {"t": time.monotonic()}
    radio._civ_silence_time_source = lambda: now["t"]  # type: ignore[attr-defined]
    silence_limit_s = 2.0 * radio._civ_get_timeout + radio._SERIAL_WATCHDOG_INTERVAL_S

    tracker.register_ack(wait=False, response_key=CivRequestKey(command=0x03, sub=None))
    tracker._ack_waiters[-1].created_monotonic = now["t"]
    assert await _wait_until(
        lambda: getattr(radio, "_civ_silence_started_monotonic", None) is not None,
        timeout_s=2.0,
    ), "watchdog never started the silence clock with a poll outstanding"

    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        # The lone sink is never answered and never re-polled; the clock is
        # held far past the silence limit (and past the old profile-derived
        # limits of the pre-fix code).
        now["t"] += silence_limit_s + 30.0
        await asyncio.sleep(radio._SERIAL_WATCHDOG_INTERVAL_S * 10)

        assert radio.conn_state == RadioConnectionState.CONNECTED, (
            "one lost keyed sink on an otherwise quiet healthy link was "
            "declared link-down"
        )
        error_lines = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "link-down" in r.getMessage()
        ]
        assert error_lines == [], (
            f"lost-sink-only link produced {len(error_lines)} link-down ERROR line(s)"
        )

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
        await _silence_clock_reset_gap(radio)
    assert await _wait_until(
        lambda: radio.conn_state == RadioConnectionState.RECONNECTING, timeout_s=2.0
    )

    assert managed_tx.ready_calls == [False]

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_receive_packet_yields_to_event_loop_when_link_drops() -> None:
    """``SerialCivTransport.receive_packet`` must yield to the event loop
    while honoring ``timeout``, even once the underlying link is down.

    ``_FakeSerialCivLink.receive`` (like the real ``SerialCivLink.receive``)
    returns ``None`` with no ``await`` suspension once ``connected`` is
    False. Before the fix, ``receive_packet`` turned that straight into
    ``asyncio.TimeoutError`` without any ``await`` of its own, so a caller
    polling it in a tight ``except TimeoutError: continue`` loop (as
    ``CivRuntime._civ_rx_loop`` does) never got preempted -- the call
    returned in the very same event-loop turn it was made in.

    A concurrent task makes this observable directly: it is scheduled
    before ``receive_packet`` is awaited, so it only gets to run if
    ``receive_packet`` actually suspends at least once. On the unfixed
    code the task never runs and ``ticks`` stays at 0.
    """
    link = _FakeSerialCivLink()  # connected defaults to False
    transport = SerialCivTransport(link)

    ticks = 0

    async def _yielder() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    yielder = asyncio.create_task(_yielder())
    try:
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await transport.receive_packet(timeout=0.05)
        elapsed = time.monotonic() - started
    finally:
        yielder.cancel()
        try:
            await yielder
        except asyncio.CancelledError:
            pass

    assert ticks > 0, (
        "receive_packet() returned without ever yielding to the event "
        "loop -- a caller polling it in a tight loop would starve every "
        "other task on a downed link"
    )
    # Direction, not magnitude: it must actually wait roughly the
    # requested timeout rather than yield once and return early.
    assert elapsed >= 0.04


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
            await _silence_clock_reset_gap(radio)

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
    lan_radio = IcomRadio("192.168.55.40", model="IC-7610")
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

    assert len(signatures) >= 5, (
        f"Expected at least 5 CI-V frames (identity read + 4 scope), got {len(signatures)}"
    )
    # MOR-3071: connect sends the identity read first (its answer carries
    # the 0x94 payload, not the request).
    assert signatures[0] == (0x19, 0x00, b"")
    assert signatures[1] == (0x27, 0x10, b"\x01")
    assert signatures[2] == (0x27, 0x11, b"\x01")
    assert signatures[3] == (0x27, 0x11, b"\x00")
    assert signatures[4] == (0x27, 0x10, b"\x00")


@pytest.mark.asyncio
async def test_serial_scope_capture_scope_frame() -> None:
    link = _FakeSerialCivLink()
    # Send #1 is the MOR-3071 identity read (answered by the fake itself).
    link.queue_response_on_send(2, _scope_wave_frame(pixels=b"\x31\x32\x33"))
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
    # Send #1 is the MOR-3071 identity read (answered by the fake itself).
    link.queue_response_on_send(2, _scope_wave_frame(pixels=b"\x51\x52"))
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
    # Send #1 is the MOR-3071 identity read (answered by the fake itself).
    link.queue_response_on_send(2, _scope_state_response(0x10, True))
    link.queue_response_on_send(3, _scope_state_response(0x11, False))
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
        (0x19, 0x00, b""),  # MOR-3071 identity read on connect
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
    # Only the MOR-3071 identity read of connect() reached the wire — no
    # scope frame at all.
    assert signatures == [(0x19, 0x00, b"")]
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
    # Send #1 is the MOR-3071 identity read (answered by the fake itself).
    link.queue_response_on_send(2, _scope_state_response(0x10, False))
    link.queue_response_on_send(3, _scope_state_response(0x11, False))
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
        (0x19, 0x00, b""),  # MOR-3071 identity read on connect
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
        # Send #1 is the MOR-3071 identity read (answered by the fake
        # itself), so the flood starts one send later than it used to.
        link.queue_response_on_send(4, _scope_wave_frame(pixels=b"\x11\x12\x13"))
    link.queue_response_on_send(4, _freq_response_frame(14_074_000))
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


# ---------------------------------------------------------------------------
# MOR-3071: the Icom serial connect gates CONNECTED on the identity read.
#
# The owner's bench case: an IC-7300 profile on the FTX-1's CAT port
# reports "connected". The seven tests of the Linear ticket, over the
# shared fake serial link with an identity-answer policy — on the
# merge-base (main 1790aae2) they fail first: nothing sends ``19 00``
# there, and the ``connection_identity`` surface does not exist.
#
# Virtual time: the re-read backoff (1, 2, 4, 8 s, then every 15 s) and
# the CI-V answer window are class/instance attributes, compressed here
# to tens of milliseconds — the same seam the MOR-237 watchdog backoff
# tests use.
# ---------------------------------------------------------------------------


class _GateSerialLink(_FakeSerialCivLink):
    """Shared fake CI-V link with a scripted identity-answer policy.

    ``send`` is overridden wholesale so the policy is the single source of
    identity answers, both on the merge-base (where the parent never
    answers ``19 00``) and on this branch: ``answer_identity=False`` keeps
    the link identity-silent forever; ``answer_after_queries=N`` stays
    silent for the first N ``19 00`` queries (the radio-comes-up-later
    case, in query-count units of virtual time).
    """

    def __init__(
        self,
        *,
        answer_identity: bool = True,
        answer_after_queries: int = 0,
        model_id: int = 0x94,
    ) -> None:
        super().__init__()
        self.policy_answer = answer_identity
        self.answer_after_queries = answer_after_queries
        self.model_id = model_id
        self.identity_queries = 0
        # MOR-3071 round 2: writes fail with OSError while ``connected``
        # lingers — the unplugged-cable shape of the real SerialCivLink.
        self.fail_sends = False

    async def connect(self) -> None:
        await super().connect()
        self.fail_sends = False  # the replugged device is back

    async def send(self, frame: bytes) -> None:
        if not self.connected:
            raise ConnectionError("Serial CI-V link is disconnected.")
        if self.fail_sends:
            raise OSError("write failed: device unplugged")
        payload = bytes(frame)
        if self.lifecycle_events is not None:
            self.lifecycle_events.append(("send", payload))
        self.sent_frames.append(payload)
        send_no = len(self.sent_frames)
        for response in self._responses_by_send.pop(send_no, []):
            self._responses.put_nowait(response)
        if payload[4:-1] == b"\x19\x00":
            self.identity_queries += 1
            if self.policy_answer and (
                self.identity_queries > self.answer_after_queries
            ):
                self.queue_response(
                    build_civ_frame(
                        CONTROLLER_ADDR,
                        payload[2],
                        0x19,
                        sub=0x00,
                        data=bytes((self.model_id,)),
                    )
                )
            return
        answer = {
            b"\x1c\x00\x01": 0xFB,
            b"\x17\xff": 0xFB,
            b"\x1c\x01\x00": 0xFB,
            b"\x1c\x00\x00": self.ptt_off_answer,
        }.get(payload[4:-1])
        if answer is not None:
            self._responses.put_nowait(
                bytes((0xFE, 0xFE, payload[3], payload[2], answer, 0xFD))
            )


def _gate_radio(link: _GateSerialLink) -> Ic7300SerialRadio:
    """IC-7300 on a fake link, with the identity cadence compressed."""
    radio = Ic7300SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
        # Hermetic on any host (MOR-1453 seams): no OS port enumeration.
        _enumerate_serial_ports_fn=lambda: [],
    )
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.05
    # MOR-3071 re-read cadence compressed: 50/100/200/400 ms, then 500 ms
    # steady (real time: 1/2/4/8 s, then every 15 s).
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.05, 0.1, 0.2, 0.4)
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 0.5
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.01
    return radio


@pytest.mark.asyncio
async def test_silent_port_holds_the_connect_and_rereads_without_reopen() -> None:
    """Ticket test 1: the owner's case — the port never answers 19 00."""
    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)

    await radio.connect()  # returns without raising; the web starts and serves

    assert radio.connected is False
    assert radio.radio_ready is False
    assert radio.conn_state is not RadioConnectionState.CONNECTED
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.NO_RESPONSE
    # No state query frames and no TX arming: the only wire traffic is the
    # identity read itself.
    payloads = [frame[4:-1] for frame in link.sent_frames]
    assert payloads
    assert set(payloads) == {b"\x19\x00"}
    assert radio._managed_tx_runtime is None
    # One plain sentence naming the port, the profile model and the baud.
    assert radio.last_error is not None
    assert "/dev/ttyUSB0" in radio.last_error
    assert "IC-7300" in radio.last_error
    assert "115200" in radio.last_error

    # Over the compressed backoff window (~1.2 s here; 60 s of the real
    # 1/2/4/8/15 s cadence): the port opener is called exactly once and
    # 19 00 keeps being re-sent on the open link.
    await asyncio.sleep(1.2)
    assert link.connect_calls == 1
    assert link.identity_queries >= 4
    assert radio.connected is False
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE

    # disconnect cancels the re-read and clears the identity.
    await radio.disconnect()
    assert radio.connection_identity is None


@pytest.mark.asyncio
async def test_radio_answering_later_completes_the_connect(tmp_path) -> None:
    """Ticket test 2: the same link starts answering after a while."""
    link = _GateSerialLink(answer_identity=True, answer_after_queries=2)
    radio = _gate_radio(link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    arm_calls: list[int] = []
    real_arm = radio._arm_managed_tx

    async def _counting_arm() -> None:
        arm_calls.append(1)
        await real_arm()

    radio._arm_managed_tx = _counting_arm  # type: ignore[assignment]

    await radio.connect()
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
    assert radio.connected is False

    assert await _wait_until(lambda: radio.connected, timeout_s=3.0)
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert identity.answered_id == "94"  # raw payload, uppercase hex
    assert identity.expected_model == "IC-7300"
    assert identity.answered_model is None
    assert link.connect_calls == 1  # the opener is still called exactly once
    assert arm_calls == [1]  # TX arms exactly once
    assert radio.radio_ready is True

    await radio.disconnect()
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_answering_radio_connects_as_today() -> None:
    """Ticket test 3: a link that answers at once connects as before."""
    link = _GateSerialLink(answer_identity=True)
    radio = _gate_radio(link)

    await radio.connect()

    assert radio.connected is True
    assert radio.radio_ready is True
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert identity.answered_id == "94"
    assert link.sent_frames[0][4:-1] == b"\x19\x00"  # the gate read went first
    await radio.disconnect()
    assert radio.connection_identity is None


@pytest.mark.asyncio
async def test_soft_reconnect_onto_silent_port_never_latches_or_reopens() -> None:
    """Ticket test 4: replug onto a silent port — checking, no_response, no reopen.

    The watchdog stays RUNNING: its ``soft_reconnect`` performs the recovery,
    and while that reopen runs its identity gate the identity-phase guard
    (MOR-3071) must keep it from re-entering or reopening a second time.
    """
    link = _GateSerialLink(answer_identity=True)
    radio = _gate_radio(link)
    await radio.connect()
    assert radio.connected is True

    # The replug: the port comes back identity-silent and not ready; the
    # running watchdog notices and soft_reconnects onto it once.
    link.policy_answer = False  # the replugged port stays identity-silent
    link.ready = False
    link.healthy = False

    assert await _wait_until(
        lambda: (
            radio.connection_identity is not None
            and radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
        ),
        timeout_s=3.0,
    )
    assert radio.connected is False
    assert radio.conn_state is not RadioConnectionState.CONNECTED

    opens_after_reconnect = link.connect_calls  # the watchdog's one reopen
    assert opens_after_reconnect == 2
    await asyncio.sleep(0.8)  # compressed re-read window
    assert link.connect_calls == opens_after_reconnect  # no further reopen
    assert link.identity_queries >= 3  # the gate read plus re-reads
    assert radio.conn_state is not RadioConnectionState.CONNECTED
    await radio.disconnect()


@pytest.mark.asyncio
async def test_power_on_in_no_response_sends_one_frame_without_reopen() -> None:
    """Ticket test 5: POWER ON stays available in no_response (MOR-2841)."""
    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)
    await radio.connect()
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE

    def power_frames() -> list[bytes]:
        # POWER ON is cmd 0x18 with the 0x01 payload (build: ``18 01``).
        return [frame for frame in link.sent_frames if frame[4] == 0x18]

    assert power_frames() == []
    await radio.set_powerstat(True)
    assert len(power_frames()) == 1  # exactly one POWER ON frame, no ACK wait
    assert link.connect_calls == 1  # no reopen

    # Everything else stays refused while the connect is held.
    with pytest.raises(ConnectionError):
        await radio.get_freq()
    with pytest.raises(ConnectionError):
        await radio.set_powerstat(False)
    await radio.disconnect()


@pytest.mark.asyncio
async def test_dead_link_during_identity_hold_hands_over_to_recovery() -> None:
    """Round 2: the link dies while the hold owns it — watchdog recovers."""
    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)

    await radio.connect()
    identity = radio.connection_identity
    assert identity is not None and identity.status is RadioIdentityStatus.NO_RESPONSE
    assert (reread := radio._serial_identity_reread_task) is not None

    # The cable is pulled: writes fail with OSError, the link reports
    # itself not ready while ``connected`` lingers, and the first recovery
    # reopen still finds no device; the replugged device answers again.
    link.policy_answer = True
    link.fail_sends = True
    link.ready = False
    link._fail_connect_calls = {2}

    # The re-read does not die: it hands the link over and the radio leaves
    # the hold (identity null, RECONNECTING, a last_error sentence).
    await asyncio.wait_for(reread, timeout=1.0)
    assert reread.exception() is None
    assert radio.connection_identity is None
    assert radio.conn_state is RadioConnectionState.RECONNECTING
    assert radio.last_error and "/dev/ttyUSB0" in radio.last_error

    # soft_reconnect reopened the port once the device was back; the
    # identity gate ran again on the new link.
    assert await _wait_until(lambda: radio.connected, timeout_s=3.0)
    identity = radio.connection_identity
    assert identity is not None and identity.status is RadioIdentityStatus.UNVERIFIED
    assert link.connect_calls == 3  # initial open + failed reopen + reopen
    await radio.disconnect()


# ---------------------------------------------------------------------------
# MOR-3071 round 4: one invariant — CONNECTED latches only when the identity
# gate has an answer for the CURRENT link (the same CI-V generation). All of
# these tests run with the watchdog RUNNING: the recovery they pin is the
# watchdog's, not a hand-driven state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reread_read_error_is_logged_and_leaves_the_hold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 2: an error raised by the re-read is logged, not swallowed.

    Round 4: the watchdog stays RUNNING — after the handover it takes
    the ready session through ``soft_reconnect`` (which re-runs the
    identity gate) instead of latching CONNECTED without an answer.
    """
    import logging

    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)
    await radio.connect()

    async def _exploding_read() -> bytes | None:
        raise RuntimeError("identity read exploded")

    radio._read_serial_identity_payload = _exploding_read  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger="rigplane.backends._icom_serial_base"):
        assert await _wait_until(
            lambda: any(
                "identity re-read" in r.getMessage() and r.exc_info
                for r in caplog.records
            ),
            timeout_s=2.0,
        )
    # The hold was abandoned: no re-read task owns the link anymore.
    assert radio._serial_identity_reread_task is None
    # The RUNNING watchdog recovered by reopening — the gate inside each
    # soft_reconnect raises the same error, so every backoff slot reopens.
    assert await _wait_until(lambda: link.connect_calls >= 2, timeout_s=2.0)
    identity = radio.connection_identity
    assert identity is None or identity.status is RadioIdentityStatus.CHECKING
    assert radio.conn_state is not RadioConnectionState.CONNECTED
    await radio.disconnect()


@pytest.mark.asyncio
async def test_reread_command_error_never_latches_and_gates_again() -> None:
    """Round 4 (a): a CommandError from the identity read, session ready.

    An unexpected exception type from ``_read_serial_identity_payload``
    used to reach the abandon path with the session still ready, and the
    watchdog's ready-branch latched CONNECTED with no answer at all.
    The invariant: CONNECTED never latches without an answer, and the
    recovery keeps re-running the identity gate.
    """
    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)
    await radio.connect()  # held on silence; the re-read owns the link

    async def _command_error_read() -> bytes | None:
        raise CommandError("the radio rejected the identity read")

    radio._read_serial_identity_payload = _command_error_read  # type: ignore[assignment]

    # ~1.3 s: the abandon at the first re-read slot, then the watchdog's
    # recovery attempts at the retry backoff (0.5 s, 1.0 s, ...). At no
    # sample may CONNECTED have latched.
    deadline = asyncio.get_running_loop().time() + 1.3
    while asyncio.get_running_loop().time() < deadline:
        assert radio.conn_state is not RadioConnectionState.CONNECTED
        await asyncio.sleep(0.02)

    # The gate ran again: each recovery attempt reopens and resets the
    # identity to checking before the read raises.
    assert await _wait_until(
        lambda: (
            radio.connection_identity is not None
            and radio.connection_identity.status is RadioIdentityStatus.CHECKING
        ),
        timeout_s=1.0,
    )
    assert link.connect_calls >= 3  # the initial open plus re-gating reopens
    await radio.disconnect()


@pytest.mark.asyncio
async def test_ready_reconnecting_without_answer_runs_gate_not_latch() -> None:
    """Round 4 (c): RECONNECTING, session reads ready, no answer for the
    current link — the watchdog runs the gate, it does not latch."""
    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)
    await radio.connect()  # held on silence

    real_read = radio._read_serial_identity_payload
    read_calls = 0

    async def _one_shot_error_read() -> bytes | None:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 2:  # the first re-read; the gate's read was call 1
            raise RuntimeError("identity read exploded once")
        return await real_read()

    radio._read_serial_identity_payload = _one_shot_error_read  # type: ignore[assignment]

    def _re_gated_hold() -> bool:
        identity = radio.connection_identity
        return (
            link.connect_calls == 2
            and identity is not None
            and identity.status is RadioIdentityStatus.NO_RESPONSE
            and radio._serial_identity_reread_task is not None
        )

    # The one-shot error abandons the hold; the watchdog must reopen once
    # and re-gate into a fresh no_response hold with a new re-read task.
    assert await _wait_until(_re_gated_hold, timeout_s=3.0)

    # Steady state: the new re-read owns the reopened link — no latch
    # and no further reopen.
    deadline = asyncio.get_running_loop().time() + 0.8
    while asyncio.get_running_loop().time() < deadline:
        assert radio.conn_state is not RadioConnectionState.CONNECTED
        assert link.connect_calls == 2
        await asyncio.sleep(0.02)
    identity = radio.connection_identity
    assert identity is not None and identity.status is RadioIdentityStatus.NO_RESPONSE
    await radio.disconnect()


@pytest.mark.asyncio
async def test_connect_during_hold_with_dead_session_cancels_reread_before_reopen() -> (
    None
):
    """Round 4 (d): connect() cancels a live re-read before the reopen.

    No two readers may ever send ``19 00`` on the new transport. The
    counting wrapper fails the test the moment two identity reads
    overlap; the old re-read's backoff slot is aimed inside the new
    gate's (deliberately long) answer window to catch a missing cancel.
    """
    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)
    # A long answer window so the new gate's read is still awaiting when
    # the old re-read's backoff fires.
    radio._civ_get_timeout = 0.5
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.1, 0.1, 0.1)

    real_read = radio._read_serial_identity_payload
    active_reads = 0
    max_active_reads = 0

    async def _counting_read() -> bytes | None:
        nonlocal active_reads, max_active_reads
        active_reads += 1
        max_active_reads = max(max_active_reads, active_reads)
        try:
            return await real_read()
        finally:
            active_reads -= 1

    radio._read_serial_identity_payload = _counting_read  # type: ignore[assignment]

    await radio.connect()  # held on silence; the re-read sleeps 0.1 s
    old_reread = radio._serial_identity_reread_task
    assert old_reread is not None

    # The session dies under the hold and the radio comes back
    # answering from query 3 (the new gate's query 2 stays silent, so
    # its window covers the old re-read's wake-up).
    link.connected = False
    link.ready = False
    link.policy_answer = True
    link.answer_after_queries = 2

    await asyncio.wait_for(radio.connect(), timeout=3.0)

    # Give the old backoff slot every chance to fire on the new link.
    await asyncio.sleep(0.3)
    assert max_active_reads == 1  # never two readers on the new transport
    await radio.disconnect()


@pytest.mark.asyncio
async def test_reread_arm_failure_keeps_connected_identity_and_logs(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 4 (b): an arm failure after the answer keeps the latch.

    ``connect()`` lets an ``_arm_managed_tx`` failure propagate to its
    caller only AFTER the latch — the connection and the identity
    survive it. The re-read has no caller to raise to: the same failure
    is logged with the full traceback and the latch stands.
    """
    import logging

    link = _GateSerialLink(answer_identity=True, answer_after_queries=1)
    radio = _gate_radio(link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)

    async def _exploding_arm() -> None:
        raise RuntimeError("managed TX arm exploded")

    radio._arm_managed_tx = _exploding_arm  # type: ignore[assignment]

    await radio.connect()  # held on silence; the re-read gets the answer
    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        assert await _wait_until(
            lambda: radio.conn_state is RadioConnectionState.CONNECTED,
            timeout_s=3.0,
        )
        await asyncio.sleep(0.1)  # the arm failure lands right after the latch

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert identity.answered_id == "94"
    assert radio.connected is True
    assert radio._serial_identity_reread_task is None
    assert any(
        "managed TX arming failed" in r.getMessage() and r.exc_info
        for r in caplog.records
    )
    await radio.disconnect()
    await composition.shutdown(asyncio.Event())


# ---------------------------------------------------------------------------
# MOR-3071 round 5: the initial gate must not escape or wedge. A write
# error on an OPENED port (the wrong-port bench shape) becomes rigplane's
# ConnectionError with the OSError as its cause, never leaves CONNECTING,
# and the CLI session still enters so the web serves and the watchdog
# heals the link once the fake stops failing.
# ---------------------------------------------------------------------------


class _BrokenWriteLink(_GateSerialLink):
    """The port opens, but every write fails — until the test relents.

    Skips the ``fail_sends`` reset in ``connect`` so the failure survives
    every reopen.
    """

    async def connect(self) -> None:
        await _FakeSerialCivLink.connect(self)


@pytest.mark.asyncio
async def test_gate_write_error_connect_recovers_serves_and_heals(tmp_path) -> None:
    """Round 5 (a): the gate's OSError converts, the CLI session enters,
    the web serves, and the watchdog heals once the fake stops failing."""
    from rigplane.cli import _ManagedTxRadioSession

    link = _BrokenWriteLink()
    link.fail_sends = True
    radio = _gate_radio(link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)

    with pytest.raises(ConnectionError) as exc_info:
        await radio.connect()
    assert isinstance(exc_info.value.__cause__, OSError)
    assert radio.conn_state is RadioConnectionState.DISCONNECTED
    assert radio._serial_identity_reread_task is None

    entered = await _ManagedTxRadioSession(radio, composition).__aenter__()
    assert entered is radio
    assert radio.conn_state is RadioConnectionState.RECONNECTING
    assert radio._civ_data_watchdog_task is not None

    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    writer = _Writer()
    await server._handle_http(writer, "GET", "/api/v1/info", headers={})  # noqa: SLF001
    body = json.loads(writer.buffer.decode("ascii", "replace").split("\r\n\r\n", 1)[1])
    connection = body["connection"]
    assert connection["rigConnected"] is False
    assert connection["identity"]["status"] == "checking"

    link.fail_sends = False
    assert await _wait_until(lambda: radio.connected, timeout_s=3.0)
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    await radio.disconnect()
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_gate_write_error_in_soft_reconnect_keeps_recovering() -> None:
    """Round 5 (b): soft_reconnect() converts the same error and keeps
    RECONNECTING — the state the watchdog retries from."""
    link = _BrokenWriteLink()
    radio = _gate_radio(link)
    await radio.connect()
    assert radio.connected is True
    await radio._stop_civ_data_watchdog()

    link.fail_sends = True
    link.ready = False
    link.healthy = False
    with pytest.raises(ConnectionError) as exc_info:
        await radio.soft_reconnect()
    assert isinstance(exc_info.value.__cause__, OSError)
    assert radio.conn_state is RadioConnectionState.RECONNECTING

    radio.start_reconnect_recovery()
    link.fail_sends = False
    assert await _wait_until(lambda: radio.connected, timeout_s=3.0)
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    await radio.disconnect()


@pytest.mark.asyncio
async def test_connect_guard_agrees_with_watchdog_guard() -> None:
    """Round 5, finding 2: connect() and the watchdog share ONE hold
    predicate — a hold on a connected-but-not-ready link owns nothing,
    so connect() re-gates instead of returning into it."""
    link = _GateSerialLink(answer_identity=False)
    radio = _gate_radio(link)
    await radio.connect()  # held on silence; the re-read owns the open link
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (10.0,)
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 10.0
    assert radio._serial_identity_reread_task is not None

    # The replug shape: still connected, not ready, answering again.
    link.policy_answer = True
    link.ready = False
    link.healthy = False

    await radio.connect()

    assert radio.conn_state is RadioConnectionState.CONNECTED
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert link.connect_calls == 1  # the open link was reused, not reopened
    assert radio._serial_identity_reread_task is None
    await radio.disconnect()


# ---------------------------------------------------------------------------
# Ticket tests 6-7: /api/v1/info connection.identity
# ---------------------------------------------------------------------------


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


def _identity_radio(
    identity: object, *, connected: bool, ready: bool
) -> SimpleNamespace:
    return SimpleNamespace(
        model="IC-7300",
        connected=connected,
        control_connected=connected,
        radio_ready=ready,
        capabilities=frozenset(),
        connection_identity=identity,
    )


@pytest.mark.asyncio
async def test_info_connection_identity_for_each_status() -> None:
    """Ticket test 6: identity per status; false rigConnected/radioReady when held."""
    cases = [
        (
            RadioIdentity(
                status=RadioIdentityStatus.CHECKING, expected_model="IC-7300"
            ),
            False,
        ),
        (
            RadioIdentity(
                status=RadioIdentityStatus.NO_RESPONSE, expected_model="IC-7300"
            ),
            False,
        ),
        (
            RadioIdentity(
                status=RadioIdentityStatus.UNVERIFIED,
                expected_model="IC-7300",
                answered_id="94",
            ),
            True,
        ),
    ]
    for identity, connected in cases:
        radio = _identity_radio(identity, connected=connected, ready=connected)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
        writer = _Writer()
        await server._handle_http(writer, "GET", "/api/v1/info", headers={})  # noqa: SLF001
        text = writer.buffer.decode("ascii", errors="replace")
        status_code = int(text.split(" ", 2)[1])
        body_start = text.index("\r\n\r\n") + 4
        payload = json.loads(text[body_start:] or "{}")
        assert status_code == 200
        connection = payload["connection"]
        assert connection["rigConnected"] is connected
        assert connection["radioReady"] is connected
        assert connection["identity"] == {
            "status": identity.status.value,
            "expectedModel": "IC-7300",
            "answeredModel": None,
            "answeredId": identity.answered_id,
        }

    # Identity null when the link never opened (no identity at all).
    radio = _identity_radio(None, connected=False, ready=False)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    writer = _Writer()
    await server._handle_http(writer, "GET", "/api/v1/info", headers={})  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    payload = json.loads(text[body_start:] or "{}")
    assert "identity" in payload["connection"]
    assert payload["connection"]["identity"] is None


@pytest.mark.asyncio
async def test_info_connection_identity_keys_are_pinned() -> None:
    """Ticket test 7: contract pin of the connection.identity keys."""
    identity = RadioIdentity(
        status=RadioIdentityStatus.IDENTITY_MISMATCH,
        expected_model="IC-7300",
        answered_model="FTX-1",
        answered_id="0840",
    )
    radio = _identity_radio(identity, connected=False, ready=False)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    writer = _Writer()
    await server._handle_http(writer, "GET", "/api/v1/info", headers={})  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    payload = json.loads(text[body_start:] or "{}")
    served = payload["connection"]["identity"]
    assert set(served) == {"status", "expectedModel", "answeredModel", "answeredId"}
    assert served["status"] in {
        "checking",
        "verified",
        "identity_mismatch",
        "no_response",
        "unverified",
    }


@pytest.mark.asyncio
async def test_startup_gate_releases_at_once_while_identity_is_no_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Behaviour 9: the web startup gate does not wait out the timeouts."""
    import logging

    path = FieldPath.global_("tx_state", "ptt")
    scheduler = AcquisitionScheduler(
        profile=RadioAcquisitionProfile(
            provider="test_provider",
            capabilities=(FieldCapability(path=path, polling=True),),
            field_policies={
                path: AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=15.0)
            },
        )
    )
    radio = SimpleNamespace(
        connection_identity=RadioIdentity(
            status=RadioIdentityStatus.NO_RESPONSE, expected_model="IC-7300"
        ),
        capabilities=frozenset(),
        _acquisition_scheduler=scheduler,
    )
    server = WebServer(  # type: ignore[arg-type]
        radio,
        WebConfig(host="127.0.0.1", port=0, await_initial_state=True),
    )
    assert scheduler.unobserved_startup_paths(()) == (path,)

    with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
        # Must return at once instead of waiting out the acquisition
        # timeout on a connect that has not completed.
        await asyncio.wait_for(
            _await_initial_state_acquisition(server, sweep=False), timeout=1.0
        )

    assert scheduler.startup_defect is None
    assert server._served_with_silent_link is True
    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and "identity" in record.getMessage()
    ]
    assert len(warnings) == 1
