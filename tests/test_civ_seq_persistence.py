"""The recorded CI-V numbering and the fresh login that starts past it (MOR-3418).

The record lives in ``runtime/_civ_seq_persistence.py``; the control phase
writes it while the CI-V transport exists and reads it on a fresh login
(``ControlPhaseRuntime._connect_once``). The CI-V transport under test is the
real ``IcomTransport`` send path over an offline socket, as in
``tests/test_civ_seq_continuity.py``, so the number asserted on a fresh login
is read from the header of the OpenClose packet it sent.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import platformdirs
import pytest

from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.radio import IcomRadio
from rigplane.runtime import _civ_seq_persistence as civ_seq
from rigplane.runtime._civ_seq_persistence import _state_dir as real_state_dir
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.web.runtime_helpers import classify_radio_health

from test_civ_seq_continuity import (
    _header_seq,
    _kept_session_radio,
    _OfflineCivTransport,
    _rebuild_stubs,
)
from test_radio_connect import ConnectMockTransport, _build_conninfo, _ConnectOnceStubs

# The GUID area of ``_build_conninfo``.
_GUID = bytes(range(0x01, 0x11))
_OTHER_GUID = bytes(range(0x11, 0x21))
_NOW = 1_000_000.0
_HELD_LINE = "radio still holds the previous session; waiting"
_STEP = 90
_WAIT_S = 9


def _radio() -> IcomRadio:
    return IcomRadio(
        "127.0.0.1", username="u", password="p", model="IC-7610", timeout=0.5
    )


def _record(radio: IcomRadio, send_seq: int, *, guid: bytes = _GUID) -> None:
    civ_seq.write_record(radio._host, radio._port, guid, send_seq, time.time())


async def _fresh_login(
    radio: IcomRadio, *, with_guid: bool = True
) -> _OfflineCivTransport:
    """Run ``_connect_once`` on ``radio``; return the CI-V transport it built."""
    ctrl = ConnectMockTransport()
    if with_guid:
        ctrl.queue_response(_build_conninfo())
    radio._ctrl_transport = ctrl
    with (
        _ConnectOnceStubs(radio),
        patch("rigplane.transport.IcomTransport", _OfflineCivTransport),
        patch.object(radio, "_gate_lan_identity", new=AsyncMock(return_value=True)),
    ):
        await radio._control_phase._connect_once()
    transport = radio._civ_transport
    assert isinstance(transport, _OfflineCivTransport)
    return transport


@pytest.fixture
async def login_cleanup() -> AsyncIterator[list[IcomRadio]]:
    """Release the sockets and the record timer of every radio the test logged in."""
    radios: list[IcomRadio] = []
    yield radios
    for radio in radios:
        radio._control_phase._stop_civ_seq_persistence()
        if radio._civ_transport is not None:
            await radio._civ_transport.disconnect()
        radio._control_phase._close_pending_sockets()


async def _first_seq(
    radio: IcomRadio, cleanup: list[IcomRadio], *, with_guid: bool = True
) -> int:
    cleanup.append(radio)
    transport = await _fresh_login(radio, with_guid=with_guid)
    assert len(transport.wire) == 1
    return _header_seq(transport.wire[0])


# --- the formula and its bound -------------------------------------------------


def test_the_step_formula_and_its_bound() -> None:
    assert civ_seq.WRITE_INTERVAL_S == 5.0
    assert civ_seq.ADVANCE_RATE_PER_S == 12
    assert civ_seq.ADVANCE_MARGIN == 30
    assert civ_seq.MAX_ADVANCE == 150
    assert civ_seq.MAX_RECORD_AGE_S == 3600.0
    # The step is the write interval at the assumed rate, plus the margin.
    assert civ_seq.advance() == 5 * 12 + 30 == _STEP
    assert civ_seq.advance(0.2) == 3 + 30
    assert civ_seq.start_seq(100) == 100 + _STEP
    assert civ_seq.start_seq(0xFFF0) == (0xFFF0 + _STEP) % 0x10000 == 0x4A
    assert civ_seq.wait_seconds() == _WAIT_S
    # The bound holds however long the interval is made.
    assert civ_seq.advance(11.0) == civ_seq.MAX_ADVANCE
    assert civ_seq.advance(3600.0) == civ_seq.MAX_ADVANCE
    for interval in (0.0, 0.2, 5.0, 10.0, 11.0, 60.0, 3600.0):
        assert civ_seq.advance(interval) <= 150


def test_the_status_line_omits_the_wait_when_there_is_none() -> None:
    assert civ_seq.status_line(None) == _HELD_LINE
    assert "~" not in civ_seq.status_line(None)
    assert civ_seq.status_line(_WAIT_S) == f"{_HELD_LINE} ~{_WAIT_S} s"


# --- the record -----------------------------------------------------------------


def test_the_record_lives_under_the_rigplane_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platformdirs, "user_state_path", lambda app: tmp_path / app)
    assert real_state_dir() == tmp_path / "rigplane" / "civ-seq"


def test_a_record_reads_back() -> None:
    civ_seq.write_record("192.0.2.1", 50001, _GUID, 1234, _NOW)
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW + 5) == 1234


def test_a_record_is_keyed_by_host_and_port() -> None:
    civ_seq.write_record("192.0.2.1", 50001, _GUID, 1234, _NOW)
    assert civ_seq.read_record("192.0.2.1", 50002, _GUID, _NOW) is None
    assert civ_seq.read_record("192.0.2.2", 50001, _GUID, _NOW) is None


def test_a_record_of_another_guid_is_not_used() -> None:
    civ_seq.write_record("192.0.2.1", 50001, _OTHER_GUID, 1234, _NOW)
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW) is None


def test_a_missing_record_is_not_used() -> None:
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW) is None


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param('{"version": 1, "guid": "0102', id="truncated"),
        pytest.param("[1, 2, 3]", id="not-an-object"),
        pytest.param(
            f'{{"version": 2, "guid": "{_GUID.hex()}", "send_seq": 5, "written_at": {_NOW}}}',
            id="other-version",
        ),
        pytest.param(
            f'{{"version": 1, "guid": "{_GUID.hex()}", "send_seq": 70000, "written_at": {_NOW}}}',
            id="seq-out-of-range",
        ),
        pytest.param(
            f'{{"version": 1, "guid": "{_GUID.hex()}", "send_seq": -1, "written_at": {_NOW}}}',
            id="negative-seq",
        ),
        pytest.param(
            f'{{"version": 1, "guid": "{_GUID.hex()}", "send_seq": true, "written_at": {_NOW}}}',
            id="bool-seq",
        ),
        pytest.param(
            f'{{"version": 1, "guid": "{_GUID.hex()}", "send_seq": 5, "written_at": "now"}}',
            id="written-at-not-a-number",
        ),
        pytest.param(
            f'{{"version": 1, "send_seq": 5, "written_at": {_NOW}}}', id="no-guid"
        ),
    ],
)
def test_a_corrupt_record_is_not_used(content: str) -> None:
    path = civ_seq._record_path("192.0.2.1", 50001)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW) is None


def test_a_record_older_than_the_age_limit_is_not_used() -> None:
    civ_seq.write_record("192.0.2.1", 50001, _GUID, 1234, _NOW)
    limit = civ_seq.MAX_RECORD_AGE_S
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW + limit) == 1234
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW + limit + 1) is None


def test_a_record_dated_after_now_is_not_used() -> None:
    civ_seq.write_record("192.0.2.1", 50001, _GUID, 1234, _NOW)
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW - 1) is None


def test_a_failed_rename_leaves_the_old_record_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    civ_seq.write_record("192.0.2.1", 50001, _GUID, 100, _NOW)

    def refuse(src: object, dst: object) -> None:
        raise OSError("rename refused")

    monkeypatch.setattr(civ_seq, "os", SimpleNamespace(replace=refuse))
    with pytest.raises(OSError, match="rename refused"):
        civ_seq.write_record("192.0.2.1", 50001, _GUID, 200, _NOW + 1)
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW + 2) == 100
    leftovers = [p.name for p in civ_seq._state_dir().iterdir()]
    assert leftovers == [civ_seq._record_path("192.0.2.1", 50001).name]


# --- a fresh login ----------------------------------------------------------------


async def test_a_fresh_login_starts_just_past_the_record(
    login_cleanup: list[IcomRadio],
) -> None:
    radio = _radio()
    _record(radio, 1000)
    assert await _first_seq(radio, login_cleanup) == 1000 + _STEP
    assert radio._control_phase.fresh_login_status == f"{_HELD_LINE} ~{_WAIT_S} s"


async def test_a_fresh_login_wraps_the_step_past_the_end_of_the_range(
    login_cleanup: list[IcomRadio],
) -> None:
    radio = _radio()
    _record(radio, 0xFFF0)
    assert await _first_seq(radio, login_cleanup) == (0xFFF0 + _STEP) % 0x10000


async def test_a_fresh_login_without_a_record_starts_at_zero(
    login_cleanup: list[IcomRadio],
) -> None:
    radio = _radio()
    assert await _first_seq(radio, login_cleanup) == 0
    assert radio._control_phase.fresh_login_status == _HELD_LINE


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param("corrupt", id="corrupt"),
        pytest.param("stale", id="stale"),
        pytest.param("other-guid", id="other-guid"),
    ],
)
async def test_a_fresh_login_with_an_unusable_record_starts_at_zero(
    spoil: str, login_cleanup: list[IcomRadio]
) -> None:
    radio = _radio()
    if spoil == "corrupt":
        path = civ_seq._record_path(radio._host, radio._port)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
    elif spoil == "stale":
        stale_at = time.time() - civ_seq.MAX_RECORD_AGE_S - 60
        civ_seq.write_record(radio._host, radio._port, _GUID, 1000, stale_at)
    else:
        _record(radio, 1000, guid=_OTHER_GUID)
    assert await _first_seq(radio, login_cleanup) == 0
    status = radio._control_phase.fresh_login_status
    assert status == _HELD_LINE
    assert "~" not in status


async def test_a_login_without_a_guid_reads_and_writes_no_record(
    login_cleanup: list[IcomRadio],
) -> None:
    radio = _radio()
    _record(radio, 1000)
    before = {p.name: p.read_bytes() for p in civ_seq._state_dir().iterdir()}
    with patch.object(civ_seq, "read_record", side_effect=AssertionError("read")):
        assert await _first_seq(radio, login_cleanup, with_guid=False) == 0
    cp = radio._control_phase
    assert cp.fresh_login_status == _HELD_LINE
    with patch.object(civ_seq, "write_record", side_effect=AssertionError("write")):
        cp._record_civ_seq()
        await cp._record_civ_seq_on_exit(7)
    assert cp._civ_seq_write is None
    assert {p.name: p.read_bytes() for p in civ_seq._state_dir().iterdir()} == before


# --- a rebuild on the kept session ---------------------------------------------------


@pytest.mark.parametrize(
    ("resume_seq", "expected_first_seq"),
    [
        pytest.param(1015, 1015, id="carries-the-torn-down-numbering"),
        pytest.param(None, 0, id="nothing-carried-starts-at-zero"),
    ],
)
async def test_a_rebuild_on_the_kept_session_ignores_the_record(
    resume_seq: int | None, expected_first_seq: int
) -> None:
    radio = _kept_session_radio()
    cp = radio._control_phase
    _record(radio, 5000)
    cp._civ_seq_guid = _GUID
    cp._fresh_login_status = f"{_HELD_LINE} ~{_WAIT_S} s"
    radio._civ_resume_seq = resume_seq
    with _rebuild_stubs(radio):
        await cp.soft_reconnect()
        new = radio._civ_transport
        assert isinstance(new, _OfflineCivTransport)
        try:
            assert len(new.wire) == 1
            assert _header_seq(new.wire[0]) == expected_first_seq
        finally:
            await new.disconnect()
    assert cp.fresh_login_status is None


# --- the status line ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "state", "line", "expected"),
    [
        pytest.param(
            RadioIdentityStatus.NO_RESPONSE,
            RadioConnectionState.CONNECTING,
            _HELD_LINE,
            _HELD_LINE,
            id="unanswered-fresh-login",
        ),
        pytest.param(
            RadioIdentityStatus.VERIFIED,
            RadioConnectionState.CONNECTING,
            _HELD_LINE,
            None,
            id="cleared-when-the-identity-answers",
        ),
        pytest.param(
            RadioIdentityStatus.UNVERIFIED,
            RadioConnectionState.CONNECTING,
            _HELD_LINE,
            None,
            id="cleared-when-the-answer-is-unverified",
        ),
        pytest.param(
            RadioIdentityStatus.CHECKING,
            RadioConnectionState.CONNECTING,
            _HELD_LINE,
            None,
            id="not-while-checking",
        ),
        pytest.param(
            RadioIdentityStatus.NO_RESPONSE,
            RadioConnectionState.CONNECTING,
            None,
            None,
            id="not-a-fresh-login",
        ),
        pytest.param(
            RadioIdentityStatus.NO_RESPONSE,
            RadioConnectionState.RECONNECTING,
            _HELD_LINE,
            None,
            id="never-while-reconnecting",
        ),
    ],
)
def test_the_status_line_is_the_radios_last_error(
    status: RadioIdentityStatus,
    state: RadioConnectionState,
    line: str | None,
    expected: str | None,
) -> None:
    radio = _radio()
    radio._control_phase._fresh_login_status = line
    radio._connection_identity = RadioIdentity(status=status, expected_model="IC-7610")
    radio._conn_state = state
    assert radio.last_error == expected


def test_the_status_line_reaches_the_served_radio_health() -> None:
    radio = _radio()
    radio._control_phase._fresh_login_status = f"{_HELD_LINE} ~{_WAIT_S} s"
    radio._conn_state = RadioConnectionState.CONNECTING
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.NO_RESPONSE, expected_model="IC-7610"
    )
    assert classify_radio_health(radio)["lastError"] == f"{_HELD_LINE} ~{_WAIT_S} s"
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.VERIFIED, expected_model="IC-7610"
    )
    assert classify_radio_health(radio)["lastError"] is None


def test_no_identity_has_no_status_line() -> None:
    radio = _radio()
    radio._control_phase._fresh_login_status = _HELD_LINE
    radio._connection_identity = None
    radio._conn_state = RadioConnectionState.CONNECTING
    assert radio.last_error is None


# --- recording while connected and on exit -----------------------------------------------


def _recording_radio(send_seq: int) -> tuple[IcomRadio, _OfflineCivTransport]:
    radio = _kept_session_radio()
    transport = _OfflineCivTransport()
    transport.send_seq = send_seq
    radio._civ_transport = transport
    radio._control_phase._civ_seq_guid = _GUID
    return radio, transport


async def _wait_for(condition: Callable[[], bool], budget_s: float = 3.0) -> bool:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.01)
    return condition()


async def test_the_numbering_is_recorded_every_interval_until_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 0.05)
    radio, transport = _recording_radio(4242)
    cp = radio._control_phase

    def recorded() -> int | None:
        return civ_seq.read_record(radio._host, radio._port, _GUID, time.time())

    cp._start_civ_seq_persistence()
    try:
        assert await _wait_for(lambda: recorded() == 4242)
        transport.send_seq = 4300
        assert await _wait_for(lambda: recorded() == 4300)
    finally:
        cp._stop_civ_seq_persistence()
    assert cp._civ_seq_timer is None
    await _wait_for(lambda: cp._civ_seq_write is None or cp._civ_seq_write.done())
    transport.send_seq = 9999
    await asyncio.sleep(0.3)
    assert recorded() == 4300


async def test_the_record_is_written_off_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 0.05)
    writer_threads: list[int] = []
    real_write = civ_seq.write_record

    def spy(*args: object) -> None:
        writer_threads.append(threading.get_ident())
        real_write(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(civ_seq, "write_record", spy)
    radio, _ = _recording_radio(4242)
    cp = radio._control_phase
    cp._start_civ_seq_persistence()
    try:
        assert await _wait_for(lambda: bool(writer_threads))
        await cp._record_civ_seq_on_exit(4243)
    finally:
        cp._stop_civ_seq_persistence()
    assert len(writer_threads) >= 2
    assert threading.get_ident() not in writer_threads


async def test_a_failing_record_does_not_stop_the_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 0.05)
    attempts: list[int] = []

    def refuse(*args: object) -> None:
        attempts.append(1)
        raise OSError("read-only file system")

    monkeypatch.setattr(civ_seq, "write_record", refuse)
    radio, _ = _recording_radio(4242)
    cp = radio._control_phase
    cp._start_civ_seq_persistence()
    try:
        assert await _wait_for(lambda: len(attempts) >= 3)
    finally:
        cp._stop_civ_seq_persistence()


@pytest.mark.parametrize("teardown", ["disconnect", "release"])
async def test_a_clean_exit_records_the_final_numbering_and_still_removes_the_token(
    teardown: str,
) -> None:
    radio, transport = _recording_radio(777)
    cp = radio._control_phase
    radio._token = 0x1234
    send_token = AsyncMock()
    with patch.object(cp, "_send_token", send_token):
        await getattr(cp, teardown)()
    send_token.assert_any_call(0x01)
    # The OpenClose(close) it sent before the teardown took the next number.
    assert transport.send_seq == 778
    assert civ_seq.read_record(radio._host, radio._port, _GUID, time.time()) == 778
