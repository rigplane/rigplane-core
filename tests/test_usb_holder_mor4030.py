"""MOR-4030: naming the program that holds the radio's audio input.

``find_holder_name`` enumerates the audio sessions of the capture endpoint
(``IAudioSessionManager2``, verified on a real Windows host for exclusive,
MME shared and WASAPI shared holders) and resolves the holding process to its
executable FILE name. These tests drive the selection logic and the guards with
fakes; the COM calls themselves only run on Windows.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from rigplane.audio import _usb_holder
from rigplane.audio._usb_holder import (
    HOLDER_LOOKUP_TIMEOUT_S,
    AudioSession,
    find_holder_name,
    holder_exe_name,
    pick_holder,
)

OWN_PID = 4242
ACTIVE = 1
INACTIVE = 0
EXPIRED = 2


def _session(pid, *, state=ACTIVE, system=False):
    return AudioSession(pid=pid, state=state, system_sounds=system)


def _resolve(table):
    def resolve(pid):
        value = table[pid]
        if isinstance(value, BaseException):
            raise value
        return value

    return resolve


def test_the_holder_is_the_exe_name_of_the_active_session():
    resolve = _resolve({100: "wsjtx.exe"})
    assert pick_holder([_session(100)], OWN_PID, resolve) == "wsjtx.exe"


def test_no_sessions_gives_no_name():
    assert pick_holder([], OWN_PID, _resolve({})) is None


def test_our_own_process_is_not_the_holder():
    resolve = _resolve({OWN_PID: "rigplane.exe", 100: "wsjtx.exe"})
    sessions = [_session(OWN_PID), _session(100)]
    assert pick_holder(sessions, OWN_PID, resolve) == "wsjtx.exe"
    assert pick_holder([_session(OWN_PID)], OWN_PID, resolve) is None


def test_the_system_sounds_session_and_pid_zero_are_not_holders():
    resolve = _resolve({0: "System", 7: "audiodg.exe", 100: "obs64.exe"})
    sessions = [_session(0), _session(7, system=True), _session(100)]
    assert pick_holder(sessions, OWN_PID, resolve) == "obs64.exe"
    assert pick_holder(sessions[:2], OWN_PID, resolve) is None


@pytest.mark.parametrize("state", [INACTIVE, EXPIRED])
def test_only_an_active_session_holds_the_device(state):
    resolve = _resolve({100: "wsjtx.exe"})
    assert pick_holder([_session(100, state=state)], OWN_PID, resolve) is None


def test_an_unresolvable_process_falls_through_to_the_next_session():
    resolve = _resolve(
        {100: None, 101: OSError("access denied"), 102: "", 103: "flrig.exe"}
    )
    sessions = [_session(pid) for pid in (100, 101, 102, 103)]
    assert pick_holder(sessions, OWN_PID, resolve) == "flrig.exe"
    assert pick_holder(sessions[:3], OWN_PID, resolve) is None


# --- find_holder_name ------------------------------------------------------


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(_usb_holder, "_is_windows", lambda: True)
    monkeypatch.setattr(os, "getpid", lambda: OWN_PID)


def test_find_holder_name_asks_for_the_capture_endpoint_of_the_device(
    monkeypatch, on_windows
):
    seen = []

    def list_sessions(device_name):
        seen.append(device_name)
        return [_session(OWN_PID), _session(100)]

    monkeypatch.setattr(_usb_holder, "_list_capture_sessions", list_sessions)
    monkeypatch.setattr(
        _usb_holder, "_process_exe_name", lambda pid: {100: "wsjtx.exe"}[pid]
    )
    assert find_holder_name("Microphone (USB Audio CODEC)") == "wsjtx.exe"
    assert seen == ["Microphone (USB Audio CODEC)"]


def test_find_holder_name_is_never_called_off_windows(monkeypatch):
    monkeypatch.setattr(_usb_holder, "_is_windows", lambda: False)

    def boom(*_args):
        raise AssertionError("the COM lookup must not run off Windows")

    monkeypatch.setattr(_usb_holder, "_list_capture_sessions", boom)
    monkeypatch.setattr(_usb_holder, "_process_exe_name", boom)
    assert find_holder_name("Microphone (USB Audio CODEC)") is None


def test_find_holder_name_swallows_every_error(monkeypatch, on_windows):
    def broken(_device_name):
        raise OSError(r"COM failed at C:\Users\someone")

    monkeypatch.setattr(_usb_holder, "_list_capture_sessions", broken)
    assert find_holder_name("Microphone (USB Audio CODEC)") is None


def test_find_holder_name_gives_none_when_the_endpoint_is_not_found(
    monkeypatch, on_windows
):
    monkeypatch.setattr(_usb_holder, "_list_capture_sessions", lambda _name: None)
    assert find_holder_name("Microphone (USB Audio CODEC)") is None


def test_the_default_bound_is_at_most_half_a_second():
    assert HOLDER_LOOKUP_TIMEOUT_S <= 0.5


def test_a_slow_find_holder_name_is_abandoned_within_its_bound(monkeypatch, on_windows):
    release = threading.Event()

    def slow(_name):
        release.wait(5)
        return [_session(100)]

    monkeypatch.setattr(_usb_holder, "_list_capture_sessions", slow)
    monkeypatch.setattr(_usb_holder, "_process_exe_name", lambda _pid: "late.exe")
    started = time.monotonic()
    try:
        assert holder_exe_name(find_holder_name, "x", 0.05) is None
        assert time.monotonic() - started < 1.0
    finally:
        release.set()


def test_the_com_layer_is_inert_off_windows():
    if _usb_holder._is_windows():
        pytest.skip("Windows runs the real COM layer")
    from rigplane.audio import _windows_audio_sessions

    with pytest.raises(OSError):
        _windows_audio_sessions.capture_sessions("Microphone (USB Audio CODEC)")
