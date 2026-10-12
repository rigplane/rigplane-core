"""The recorded CI-V numbering and the fresh login that starts past it (MOR-3418).

The CI-V transport under test is the real ``IcomTransport`` send path over an
offline socket, as in ``tests/test_civ_seq_continuity.py``: a fresh login's
number is read from the header of the OpenClose packet it sent.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from functools import partial
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

_GUID = bytes(range(0x01, 0x11))  # the GUID area of ``_build_conninfo``
_OTHER_GUID = bytes(range(0x11, 0x21))
_NOW = 1_000_000.0
_HELD = "radio still holds the previous session; waiting"
_STEP = 90  # 5 s * 12 packets/s + 30
_WAIT_S = 9  # ceil(90 / 10)
_NO_RESPONSE = RadioIdentityStatus.NO_RESPONSE
_VERIFIED = RadioIdentityStatus.VERIFIED
_CONNECTING = RadioConnectionState.CONNECTING
_RECONNECTING = RadioConnectionState.RECONNECTING


def _radio() -> IcomRadio:
    return IcomRadio(
        "127.0.0.1", username="u", password="p", model="IC-7610", timeout=0.5
    )


def _record(
    radio: IcomRadio, send_seq: int, *, guid: bytes = _GUID, at: float | None = None
) -> None:
    now = time.time() if at is None else at
    civ_seq.write_record(radio._host, radio._port, guid, send_seq, now)


async def _first_seq(radio: IcomRadio, *, with_guid: bool = True) -> int:
    """Fresh-login ``radio``, release its sockets, return its first CI-V number."""
    ctrl = ConnectMockTransport()
    if with_guid:
        ctrl.queue_response(_build_conninfo())
    radio._ctrl_transport = ctrl
    try:
        with (
            _ConnectOnceStubs(radio),
            patch("rigplane.transport.IcomTransport", _OfflineCivTransport),
            patch.object(radio, "_gate_lan_identity", new=AsyncMock(return_value=True)),
        ):
            await radio._control_phase._connect_once()
        transport = radio._civ_transport
        assert isinstance(transport, _OfflineCivTransport) and len(transport.wire) == 1
        return _header_seq(transport.wire[0])
    finally:
        radio._control_phase._stop_civ_seq_persistence()
        if radio._civ_transport is not None:
            await radio._civ_transport.disconnect()
        radio._control_phase._close_pending_sockets()


def test_the_step_formula_bound_and_status_line() -> None:
    assert civ_seq.WRITE_INTERVAL_S == 5.0
    assert civ_seq.ADVANCE_RATE_PER_S == 12
    assert civ_seq.ADVANCE_MARGIN == 30
    assert civ_seq.MAX_ADVANCE == 150
    assert civ_seq.advance() == _STEP
    assert civ_seq.advance(0.2) == 3 + 30
    assert civ_seq.start_seq(100) == 100 + _STEP
    assert civ_seq.start_seq(0xFFF0) == (0xFFF0 + _STEP) % 0x10000 == 0x4A
    assert civ_seq.wait_seconds() == _WAIT_S
    # However long the interval is made, the step stays within the bound.
    assert civ_seq.advance(3600.0) == civ_seq.MAX_ADVANCE
    assert all(civ_seq.advance(i) <= 150 for i in (0.0, 0.2, 5.0, 11.0, 60.0, 3600.0))
    assert civ_seq.status_line(None) == _HELD
    assert civ_seq.status_line(_WAIT_S) == f"{_HELD} ~{_WAIT_S} s"


def test_the_record_lives_under_the_rigplane_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platformdirs, "user_state_path", lambda app: tmp_path / app)
    assert real_state_dir() == tmp_path / "rigplane" / "civ-seq"


def test_a_record_is_used_for_its_own_host_port_guid_and_age() -> None:
    civ_seq.write_record("192.0.2.1", 50001, _GUID, 1234, _NOW)
    read = partial(
        civ_seq.read_record, host="192.0.2.1", port=50001, guid=_GUID, now=_NOW
    )
    limit = civ_seq.MAX_RECORD_AGE_S
    assert read() == read(now=_NOW + limit) == 1234
    assert read(host="192.0.2.2") is None and read(port=50002) is None
    assert read(guid=_OTHER_GUID) is None
    assert read(now=_NOW + limit + 1) is None
    assert read(now=_NOW - 1) is None


def test_a_corrupt_record_is_not_used() -> None:
    def document(**changes: object) -> str:
        fields = {"version": 1, "guid": _GUID.hex(), "send_seq": 5, "written_at": _NOW}
        return json.dumps(fields | changes)

    path = civ_seq._record_path("192.0.2.1", 50001)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document(), encoding="utf-8")
    assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW) == 5  # control
    for content in (
        "",
        document()[:20],
        "[1, 2]",
        document(version=2),
        document(send_seq=70000),
        document(send_seq=-1),
        document(send_seq=True),
        document(written_at="now"),
        document(guid=None),
    ):
        path.write_text(content, encoding="utf-8")
        assert civ_seq.read_record("192.0.2.1", 50001, _GUID, _NOW) is None, content


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
    names = [p.name for p in civ_seq._state_dir().iterdir()]
    assert names == [civ_seq._record_path("192.0.2.1", 50001).name]


def test_a_record_is_removed_only_for_its_own_radio() -> None:
    path = civ_seq._record_path("192.0.2.1", 50001)
    civ_seq.write_record("192.0.2.1", 50001, _OTHER_GUID, 5, _NOW)
    civ_seq.remove_record("192.0.2.1", 50001, _GUID)
    assert path.exists()
    civ_seq.remove_record("192.0.2.1", 50001, _OTHER_GUID)
    assert not path.exists()
    civ_seq.remove_record("192.0.2.1", 50001, _OTHER_GUID)  # absent: no error


async def test_a_fresh_login_starts_past_the_record() -> None:
    radio = _radio()
    _record(radio, 1000)
    assert await _first_seq(radio) == 1000 + _STEP
    assert radio._control_phase.fresh_login_status == f"{_HELD} ~{_WAIT_S} s"


@pytest.mark.parametrize("record", ["none", "corrupt", "stale", "other-guid"])
async def test_an_unusable_record_starts_a_fresh_login_at_zero(record: str) -> None:
    radio = _radio()
    path = civ_seq._record_path(radio._host, radio._port)
    if record == "corrupt":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
    elif record == "stale":
        _record(radio, 1000, at=time.time() - civ_seq.MAX_RECORD_AGE_S - 60)
    elif record == "other-guid":
        _record(radio, 1000, guid=_OTHER_GUID)
    assert await _first_seq(radio) == 0
    assert radio._control_phase.fresh_login_status == _HELD


async def test_a_login_without_a_guid_touches_no_record() -> None:
    radio = _radio()
    with patch.object(civ_seq, "read_record", side_effect=AssertionError("read")):
        assert await _first_seq(radio, with_guid=False) == 0
    cp = radio._control_phase
    assert cp.fresh_login_status == _HELD
    with (
        patch.object(civ_seq, "write_record", side_effect=AssertionError("write")),
        patch.object(civ_seq, "remove_record", side_effect=AssertionError("remove")),
    ):
        cp._record_civ_seq()
        await cp._forget_civ_seq()
    cp._stop_civ_seq_persistence()
    assert cp._civ_seq_write is None


@pytest.mark.parametrize(("resume_seq", "first_seq"), [(1015, 1015), (None, 0)])
async def test_a_rebuild_on_the_kept_session_ignores_the_record(
    resume_seq: int | None, first_seq: int
) -> None:
    radio = _kept_session_radio()
    cp = radio._control_phase
    _record(radio, 5000)
    cp._civ_seq_guid = _GUID
    cp.fresh_login_status = f"{_HELD} ~{_WAIT_S} s"
    radio._civ_resume_seq = resume_seq
    with _rebuild_stubs(radio):
        await cp.soft_reconnect()
        new = radio._civ_transport
        assert isinstance(new, _OfflineCivTransport)
        try:
            assert [_header_seq(p) for p in new.wire] == [first_seq]
        finally:
            await new.disconnect()
    assert cp.fresh_login_status is None


@pytest.mark.parametrize(
    ("status", "state", "line", "expected"),
    [
        pytest.param(_NO_RESPONSE, _CONNECTING, _HELD, _HELD, id="unanswered"),
        pytest.param(_VERIFIED, _CONNECTING, _HELD, None, id="answered"),
        pytest.param(_NO_RESPONSE, _CONNECTING, None, None, id="not-a-fresh-login"),
        pytest.param(_NO_RESPONSE, _RECONNECTING, _HELD, None, id="reconnecting"),
    ],
)
def test_the_status_line_is_the_radios_last_error_and_the_served_health(
    status: RadioIdentityStatus,
    state: RadioConnectionState,
    line: str | None,
    expected: str | None,
) -> None:
    radio = _radio()
    radio._control_phase.fresh_login_status = line
    radio._connection_identity = RadioIdentity(status=status, expected_model="IC-7610")
    radio._conn_state = state
    assert radio.last_error == expected
    assert classify_radio_health(radio)["lastError"] == expected


def test_a_last_error_a_backend_sets_comes_first() -> None:
    radio = _radio()
    radio._control_phase.fresh_login_status = _HELD
    radio._connection_identity = RadioIdentity(status=_NO_RESPONSE)
    radio._conn_state = _CONNECTING
    radio.last_error = "port busy"  # as the serial Icom radios do
    assert radio.last_error == "port busy"
    radio.last_error = None
    assert radio.last_error == _HELD


def _recording_radio(send_seq: int) -> tuple[IcomRadio, _OfflineCivTransport]:
    radio = _kept_session_radio()
    transport = _OfflineCivTransport()
    transport.send_seq = send_seq
    radio._civ_transport = transport
    radio._control_phase._civ_seq_guid = _GUID
    return radio, transport


async def _wait_for(condition: Callable[[], bool], budget_s: float = 3.0) -> bool:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline and not condition():
        await asyncio.sleep(0.01)
    return condition()


def _recorded(radio: IcomRadio) -> int | None:
    return civ_seq.read_record(radio._host, radio._port, _GUID, time.time())


async def test_the_numbering_is_recorded_off_the_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 0.05)
    threads: list[int] = []
    real_write = civ_seq.write_record

    def spy(*args: object) -> None:
        threads.append(threading.get_ident())
        real_write(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(civ_seq, "write_record", spy)
    radio, _ = _recording_radio(4242)
    cp = radio._control_phase
    cp._start_civ_seq_persistence()
    try:
        assert await _wait_for(lambda: _recorded(radio) == 4242)
    finally:
        cp._stop_civ_seq_persistence()
    assert threads and threading.get_ident() not in threads


async def test_a_crash_leaves_the_last_periodic_record_on_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 0.05)
    radio, transport = _recording_radio(4242)
    cp = radio._control_phase
    cp._start_civ_seq_persistence()
    try:
        assert await _wait_for(lambda: _recorded(radio) == 4242)
        transport.send_seq = 4300
        assert await _wait_for(lambda: _recorded(radio) == 4300)
    finally:
        cp._stop_civ_seq_persistence()  # a dead process runs no teardown
    await _wait_for(lambda: cp._civ_seq_write is None or cp._civ_seq_write.done())
    transport.send_seq = 9999
    await asyncio.sleep(0.3)
    assert _recorded(radio) == 4300


@pytest.mark.parametrize("teardown", ["disconnect", "release"])
async def test_a_clean_exit_deletes_the_record_and_removes_the_token(
    teardown: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 0.05)
    radio, _ = _recording_radio(777)
    cp = radio._control_phase
    radio._token = 0x1234
    path = civ_seq._record_path(radio._host, radio._port)
    cp._start_civ_seq_persistence()
    assert await _wait_for(path.exists)  # a periodic record is on disk
    send_token = AsyncMock()
    with patch.object(cp, "_send_token", send_token):
        await getattr(cp, teardown)()
    send_token.assert_any_call(0x01)
    await asyncio.sleep(0.3)  # several intervals: nothing writes it back
    assert not path.exists()
    await cp._forget_civ_seq()  # an absent record is not an error


async def test_a_late_periodic_write_cannot_recreate_a_deleted_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 0.05)
    started, release = threading.Event(), threading.Event()
    real_write = civ_seq.write_record

    def held_write(*args: object) -> None:
        started.set()
        release.wait(5)
        real_write(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(civ_seq, "write_record", held_write)
    radio, _ = _recording_radio(4242)
    cp = radio._control_phase
    path = civ_seq._record_path(radio._host, radio._port)
    cp._start_civ_seq_persistence()
    try:
        assert await _wait_for(started.is_set)  # a write is in flight, held
        forget = asyncio.create_task(cp._forget_civ_seq())
        await asyncio.sleep(0.2)  # several intervals: the delete waits for it
        assert not forget.done()
        release.set()
        await forget
        await asyncio.sleep(0.3)  # a surviving timer would write again here
        assert not path.exists()
    finally:
        release.set()
        cp._stop_civ_seq_persistence()
