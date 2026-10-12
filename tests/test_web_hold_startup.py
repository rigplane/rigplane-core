"""MOR-3363: ``rigplane web`` comes up when the first connect lands in the hold.

The recorded IC-7610 allocated its session but never answered the
``0x19 00`` identity read, so the radio sat at
``connected=False, radio_ready=False, control_connected=True`` with a
``NO_RESPONSE`` identity. The bundled rigctld's start-up check aborted
the whole process before the web listener ever bound.

The simulator (``rigplane_testkit/icom/simulator.py``) cannot stage
this: it always answers ``0x19 0x00`` with its model id and offers no
knob to withhold that answer, and the simulator is another lane's. So
these tests use a narrow fake carrying exactly the field state the
start-up check reads, started in the order ``cli: _cmd_web`` uses
(bundled rigctld first, web server second).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.radio_state import RadioState
from rigplane.rigctld.contract import RigctldConfig, RigctldResponse
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.startup_checks import assert_radio_startup_ready
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer


class _HoldRadio:
    """A LAN radio whose first connect landed in the identity hold.

    The session is allocated (control connected) but the identity read
    never answered, so the radio is neither connected nor ready while a
    real ``NO_RESPONSE`` identity owns the link — the state the web
    gate already serves through (``web_startup: _serve_with_identity_pending``).
    """

    backend_id = "rigplane"
    model = "FAKE-HOLD"
    capabilities: set[str] = set()

    def __init__(self) -> None:
        self.connected = False
        self.control_connected = True
        self.radio_ready = False
        self.connection_identity = RadioIdentity(
            status=RadioIdentityStatus.NO_RESPONSE,
            expected_model="IC-7610",
        )
        self.radio_state = RadioState()

    def supports_command(self, _command: str) -> bool:
        return False


class _HoldHandler:
    """Answers the way the held radio does: refused until ready."""

    def __init__(self, radio: _HoldRadio) -> None:
        self._radio = radio

    async def execute(self, _cmd: object, **_kwargs: object) -> RigctldResponse:
        if not self._radio.radio_ready:
            raise ConnectionError("radio is not ready")
        return RigctldResponse(values=["14074000"], error=0)


def _web_config() -> WebConfig:
    return WebConfig(
        host="127.0.0.1",
        port=0,
        discovery=False,
        await_initial_state=True,
    )


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _get_readyz(server: WebServer) -> tuple[int, dict[str, Any]]:
    """GET /readyz over a real socket: status code plus the JSON body."""

    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            b"GET /readyz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=5.0)
    finally:
        await _close_writer(writer)
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), json.loads(body.decode())


async def _rigctld_line(rig: RigctldServer, line: bytes) -> bytes:
    """Send one command line to the rigctld listener; return its answer."""

    assert rig._server is not None
    host, port = rig._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(line)
        await writer.drain()
        return await asyncio.wait_for(reader.read(4096), timeout=5.0)
    finally:
        await _close_writer(writer)


@pytest.mark.asyncio
async def test_web_and_bundled_rigctld_come_up_against_identity_hold() -> None:
    """The MOR-3363 composition: hold-state radio, both servers listen."""

    radio = _HoldRadio()
    rig = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        _handler=_HoldHandler(radio),
        allow_not_ready=True,
    )
    # The bundled seat starts first, exactly as ``cli: _cmd_web`` does —
    # this is the call that aborted the whole process with
    # "rigctld startup aborted: radio is not ready ...".
    await rig.start()
    assert rig._server is not None

    server = WebServer(radio, _web_config())
    fake_poller = MagicMock(
        drain_tx_safety_commands=AsyncMock(),
        select_vfo_a_on_connect=AsyncMock(),
    )
    try:
        with patch("rigplane.web.web_startup.RadioPoller", return_value=fake_poller):
            await server.start()
        assert server._server is not None
        # The web gate released through the identity-pending branch, the
        # same branch a serial hold takes — not through the abort.
        assert server._served_with_silent_link is True

        status, payload = await _get_readyz(server)
        assert status == 503
        assert payload == {"status": "not_ready", "radioReady": False}

        # The bundled rigctld listens; the fake handler refuses `f` while
        # the radio is not ready, which the server maps to an error RPRT.
        assert await _rigctld_line(rig, b"f\n") == b"RPRT -6\n"

        # The hold resolves the way the LAN re-read does (an acceptable
        # identity answer reaches `radio.py: _finish_lan_identity_completion`;
        # staged here by hand): the exact check that aborted start-up now
        # passes, rigctld serves normally, and /readyz reports ready honestly.
        radio.connected = True
        radio.radio_ready = True
        assert_radio_startup_ready(radio, component="rigctld startup")
        assert await _rigctld_line(rig, b"f\n") == b"14074000\n"
        status, payload = await _get_readyz(server)
        assert status == 200
        assert payload == {"status": "ready", "radioReady": True}
    finally:
        await server.stop()
        await rig.stop()


class _BusyRadio:
    """A LAN radio whose first connect waits out a busy reject.

    Reports the start-up busy hold through the public CoreRadio surface
    (``startup_busy_hold_event`` / ``startup_busy_hold_retry_in_seconds``)
    until the test grants the session; ``__aenter__`` blocks the way the
    resident first connect does, so a managed session enter plus a web
    server can be staged around it exactly as ``cli: _cmd_web`` does.
    """

    backend_id = "rigplane"
    # Borrows the IC-7610 profile so /api/v1/state serves; the hold
    # behaviour itself is staged by hand below.
    model = "IC-7610"
    capabilities: set[str] = set()

    def __init__(self, *, retry_in_seconds: float = 30.0) -> None:
        self.connected = False
        self.control_connected = False
        self.radio_ready = False
        # Production ``CoreRadio.connect`` reports CHECKING before the
        # lifecycle's first attempt, so a busy hold always reads CHECKING
        # here until the grant lands.
        self.connection_identity: RadioIdentity | None = RadioIdentity(
            status=RadioIdentityStatus.CHECKING,
            expected_model="IC-7610",
        )
        self.radio_state = RadioState()
        self.startup_busy_hold_event = asyncio.Event()
        self.startup_busy_hold_event.set()
        self._retry_in_seconds: float | None = retry_in_seconds
        self._grant = asyncio.Event()
        self._give_up: BaseException | None = None
        self.enter_calls = 0
        self.exit_calls = 0
        self.fetch_initial_state_calls = 0

    @property
    def startup_busy_hold_retry_in_seconds(self) -> float | None:
        if not self.startup_busy_hold_event.is_set():
            return None
        return self._retry_in_seconds

    def supports_command(self, _command: str) -> bool:
        return False

    async def _fetch_initial_state(self) -> None:
        self.fetch_initial_state_calls += 1

    async def actuate(self, _token: Any, _operation: Any, *, is_current: Any) -> Any:
        """Minimal managed-TX actuator surface (mirrors the CLI busy fake)."""

        from rigplane.runtime.managed_tx_state import ActuationResult

        return ActuationResult.ACCEPTED if is_current() else ActuationResult.REJECTED

    async def set_ptt(self, _on: bool) -> None:
        raise AssertionError("raw PTT must be replaced before connect")

    def grant(self) -> None:
        """Grant the session: the hold clears and the radio becomes ready."""

        self._grant.set()

    def give_up(self, error: BaseException) -> None:
        """End the hold in the give-up error, the way the 600 s cap does."""

        self._give_up = error
        self._grant.set()

    async def __aenter__(self) -> "_BusyRadio":
        self.enter_calls += 1
        await self._grant.wait()
        self.startup_busy_hold_event.clear()
        if self._give_up is not None:
            raise self._give_up
        self.connected = True
        self.control_connected = True
        self.radio_ready = True
        self.connection_identity = RadioIdentity(
            status=RadioIdentityStatus.VERIFIED,
            expected_model="IC-7610",
            answered_model="IC-7610",
        )
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.exit_calls += 1
        return False


class _BusyHandler:
    """Answers the way the busy-held radio does: refused until ready."""

    def __init__(self, radio: _BusyRadio) -> None:
        self._radio = radio

    async def execute(self, _cmd: object, **_kwargs: object) -> RigctldResponse:
        if not self._radio.radio_ready:
            raise ConnectionError("radio is not ready")
        return RigctldResponse(values=["14074000"], error=0)


async def _get_json(server: WebServer, path: str) -> tuple[int, dict[str, Any]]:
    """GET a JSON route over a real socket: status code plus the body."""

    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), timeout=10.0)
    finally:
        await _close_writer(writer)
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), json.loads(body.decode())


def _busy_poller() -> MagicMock:
    """A poller double shaped like the real RadioPoller for the busy rows.

    A spec mock passes the ``isinstance`` checks the late-connect
    completion uses, so granting the hold exercises the same completion
    the production poller gets (initial-state fetch, VFO-A select).
    """

    poller = MagicMock(spec=RadioPoller)
    poller.drain_tx_safety_commands = AsyncMock()
    poller.select_vfo_a_on_connect = AsyncMock()
    poller.reset_vfo_session = MagicMock()
    poller._seed_scan_facts_at_connect = MagicMock()
    poller._initial_fetch_done = asyncio.Event()
    poller._initial_fetch_done.set()
    return poller


async def _start_busy_stack(
    radio: _BusyRadio, poller: MagicMock
) -> tuple[RigctldServer, WebServer, int]:
    """Start the bundled rigctld first, then the web server, like _cmd_web.

    Returns the rigctld server, the web server and the rigctld port, with
    ``_runtime_rigctld_addr`` recorded the way ``cli: _cmd_web`` records
    it right after the rigctld bind.
    """

    rig = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        _handler=_BusyHandler(radio),
        allow_not_ready=True,
    )
    await rig.start()
    assert rig._server is not None
    rig_port = int(rig._server.sockets[0].getsockname()[1])
    server = WebServer(radio, _web_config())
    with patch("rigplane.web.web_startup.RadioPoller", return_value=poller):
        await server.start()
    assert server._server is not None
    server._runtime_rigctld_addr = f"127.0.0.1:{rig_port}"
    return rig, server, rig_port


@pytest.mark.asyncio
async def test_web_serves_session_busy_during_startup_busy_hold() -> None:
    """MOR-3391 part 2: HTTP is up while the first connect waits out a busy.

    The bundled rigctld binds first (refusing commands while the radio is
    not ready), the web gate releases through the busy branch — without
    the silent-link mark, so health must not blame a powered-off radio —
    and the wire reports the busy session: /readyz 503 with the exact
    busy body, connection.session busy with a retry number, /api/v1/runtime
    with rigctld enabled at its bound address.
    """

    radio = _BusyRadio()
    poller = _busy_poller()
    rig, server, rig_port = await _start_busy_stack(radio, poller)
    try:
        assert server._served_with_silent_link is False
        assert poller.start.called

        assert await _rigctld_line(rig, b"f\n") == b"RPRT -6\n"

        status, payload = await _get_json(server, "/readyz")
        assert status == 503
        assert payload == {
            "status": "not_ready",
            "radioReady": False,
            "reason": "session_busy",
            "retryInSeconds": 30.0,
        }

        status, payload = await _get_json(server, "/api/v1/info")
        assert status == 200
        assert payload["proto"] == 1
        assert payload["connection"]["rigConnected"] is False
        assert payload["connection"]["session"] == {
            "busy": True,
            "retryInSeconds": 30.0,
        }

        status, payload = await _get_json(server, "/api/v1/runtime")
        assert status == 200
        assert payload["rigctld"] == {
            "enabled": True,
            "address": f"127.0.0.1:{rig_port}",
        }

        status, payload = await _get_json(server, "/api/v1/state")
        assert status == 200
        assert payload["radioHealth"]["likelyCause"] != "radio_powered_off_likely"
    finally:
        await server.stop()
        await rig.stop()


@pytest.mark.asyncio
async def test_granted_busy_hold_becomes_ready_and_marks_transport_once() -> None:
    """After the grant the hold clears and the late completion runs.

    The background managed-session enter runs ``transport_ready`` exactly
    once, the busy signal clears, the late-connect completion refetches
    state and selects VFO A, and /readyz reports ready honestly with
    ``connection.session`` back to not-busy.
    """

    from rigplane.cli import _ManagedTxRadioSession

    radio = _BusyRadio()
    poller = _busy_poller()
    rig, server, _rig_port = await _start_busy_stack(radio, poller)
    poller.select_vfo_a_on_connect.reset_mock()
    composition = create_autospec(ManagedTxComposition, instance=True)
    session = _ManagedTxRadioSession(radio, composition)
    enter_task = asyncio.ensure_future(session.__aenter__())
    try:
        for _ in range(100):
            if radio.enter_calls:
                break
            await asyncio.sleep(0.01)
        assert radio.enter_calls == 1

        radio.grant()
        entered = await asyncio.wait_for(enter_task, timeout=5.0)
        assert entered is radio
        composition.transport_ready.assert_awaited_once_with(radio)
        assert radio.startup_busy_hold_event.is_set() is False

        await server.finish_startup_busy_hold()
        assert radio.fetch_initial_state_calls == 1
        poller.select_vfo_a_on_connect.assert_awaited_once()

        status, payload = await _get_json(server, "/readyz")
        assert status == 200
        assert payload == {"status": "ready", "radioReady": True}

        status, payload = await _get_json(server, "/api/v1/info")
        assert status == 200
        assert payload["connection"]["rigConnected"] is True
        assert payload["connection"]["session"] == {
            "busy": False,
            "retryInSeconds": None,
        }

        assert await _rigctld_line(rig, b"f\n") == b"14074000\n"
    finally:
        if not enter_task.done():
            enter_task.cancel()
            try:
                await enter_task
            except asyncio.CancelledError:
                pass
        await session.shutdown()
        await radio.__aexit__(None, None, None)
        await server.stop()
        await rig.stop()
    assert radio.exit_calls == 1


@pytest.mark.asyncio
async def test_busy_grant_identity_hold_and_answer_walk() -> None:
    """Busy and identity holds stay distinguishable across both documents.

    One walk asserting ``/readyz`` and ``/api/v1/info`` at every step:
    busy → the busy body with a busy session and ``checking`` identity;
    granted → ready with a quiet session; identity hold (``no_response``)
    → exactly the reason-less not-ready body with a quiet session and
    ``no_response`` identity; answered → ready again.
    """

    from rigplane.cli import _ManagedTxRadioSession

    radio = _BusyRadio()
    poller = _busy_poller()
    rig, server, _rig_port = await _start_busy_stack(radio, poller)
    composition = create_autospec(ManagedTxComposition, instance=True)
    session = _ManagedTxRadioSession(radio, composition)
    enter_task = asyncio.ensure_future(session.__aenter__())
    try:
        for _ in range(100):
            if radio.enter_calls:
                break
            await asyncio.sleep(0.01)
        assert radio.enter_calls == 1

        status, payload = await _get_json(server, "/readyz")
        assert status == 503
        assert payload == {
            "status": "not_ready",
            "radioReady": False,
            "reason": "session_busy",
            "retryInSeconds": 30.0,
        }
        status, payload = await _get_json(server, "/api/v1/info")
        assert status == 200
        assert payload["connection"]["session"] == {
            "busy": True,
            "retryInSeconds": 30.0,
        }
        assert payload["connection"]["identity"]["status"] == "checking"

        radio.grant()
        entered = await asyncio.wait_for(enter_task, timeout=5.0)
        assert entered is radio
        composition.transport_ready.assert_awaited_once_with(radio)
        await server.finish_startup_busy_hold()
        status, payload = await _get_json(server, "/readyz")
        assert status == 200
        assert payload == {"status": "ready", "radioReady": True}
        status, payload = await _get_json(server, "/api/v1/info")
        assert status == 200
        assert payload["connection"]["session"] == {
            "busy": False,
            "retryInSeconds": None,
        }

        radio.connected = False
        radio.radio_ready = False
        radio.connection_identity = RadioIdentity(
            status=RadioIdentityStatus.NO_RESPONSE,
            expected_model="IC-7610",
        )
        status, payload = await _get_json(server, "/readyz")
        assert status == 503
        assert payload == {"status": "not_ready", "radioReady": False}
        status, payload = await _get_json(server, "/api/v1/info")
        assert status == 200
        assert payload["connection"]["session"] == {
            "busy": False,
            "retryInSeconds": None,
        }
        assert payload["connection"]["identity"]["status"] == "no_response"

        radio.connected = True
        radio.radio_ready = True
        radio.connection_identity = RadioIdentity(
            status=RadioIdentityStatus.VERIFIED,
            expected_model="IC-7610",
            answered_model="IC-7610",
        )
        status, payload = await _get_json(server, "/readyz")
        assert status == 200
        assert payload == {"status": "ready", "radioReady": True}
        status, payload = await _get_json(server, "/api/v1/info")
        assert status == 200
        assert payload["connection"]["identity"]["status"] == "verified"
    finally:
        if not enter_task.done():
            enter_task.cancel()
            try:
                await enter_task
            except asyncio.CancelledError:
                pass
        await session.shutdown()
        await radio.__aexit__(None, None, None)
        await server.stop()
        await rig.stop()
    assert radio.exit_calls == 1


@pytest.mark.asyncio
async def test_busy_hold_with_managed_tx_composition_starts_without_provider(
    tmp_path: Path,
) -> None:
    """A busy-held radio with managed TX attached starts through the hold.

    The composition binds the web store but has no current provider — no
    transport exists for one to stand on yet — so ``web_startup:
    _validate_managed_tx`` must take the transport-pending path (the busy
    predicate names the hold the way the serial predicates name theirs)
    instead of failing start-up with "managed TX provider is not
    current". The negative probe below proves the row is not vacuous:
    without the pending path the same composition raises.
    """

    radio = _BusyRadio()
    poller = _busy_poller()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    server = WebServer(radio, _web_config())
    prepare_managed_tx_observation_generation(server)
    await composition.bind_state_store(server.command_state_store)
    attach_managed_tx_composition(server, composition)
    with pytest.raises(RuntimeError, match="managed TX provider is not current"):
        composition.validate_state_store(server.command_state_store)
    try:
        with patch("rigplane.web.web_startup.RadioPoller", return_value=poller):
            await server.start()
        assert server._served_with_silent_link is False
        status, payload = await _get_json(server, "/readyz")
        assert status == 503
        assert payload["reason"] == "session_busy"
    finally:
        await server.stop()
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_late_busy_completion_runs_the_one_time_fetches_after_grant() -> None:
    """The granted hold reaches the same ready state as a normal start.

    The poller's one-time start-up fetches ran against the disconnected
    radio when the server started during the hold; the late completion
    runs them once after the grant, against the connected radio.
    """

    from rigplane.cli import _ManagedTxRadioSession

    radio = _BusyRadio()
    poller = _busy_poller()
    rig, server, _rig_port = await _start_busy_stack(radio, poller)
    composition = create_autospec(ManagedTxComposition, instance=True)
    session = _ManagedTxRadioSession(radio, composition)
    enter_task = asyncio.ensure_future(session.__aenter__())
    try:
        for _ in range(100):
            if radio.enter_calls:
                break
            await asyncio.sleep(0.01)
        assert radio.enter_calls == 1

        radio.grant()
        entered = await asyncio.wait_for(enter_task, timeout=5.0)
        assert entered is radio
        assert radio.connected is True
        await server.finish_startup_busy_hold()
        assert radio.connected is True
        poller._fetch_nb_controls.assert_awaited_once()
        poller._fetch_mod_inputs.assert_awaited_once()
        status, payload = await _get_json(server, "/readyz")
        assert status == 200
        assert payload == {"status": "ready", "radioReady": True}
    finally:
        if not enter_task.done():
            enter_task.cancel()
            try:
                await enter_task
            except asyncio.CancelledError:
                pass
        await session.shutdown()
        await radio.__aexit__(None, None, None)
        await server.stop()
        await rig.stop()
    assert radio.exit_calls == 1


@pytest.mark.asyncio
async def test_standalone_rigctld_still_aborts_against_identity_hold() -> None:
    """The default seat keeps the fail-hard check; only web mode opts out."""

    radio = _HoldRadio()
    rig = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        _handler=_HoldHandler(radio),
    )
    with pytest.raises(RuntimeError, match="rigctld startup aborted"):
        await rig.start()
