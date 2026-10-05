"""Windows PortAudio work uses the calling worker's balanced COM apartment.

Fake Ole32 only: no Windows API, sound device, stream or RF is accessed.
"""

from __future__ import annotations

import asyncio
import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rigplane.audio import usb_driver


@pytest.mark.parametrize("hresult", [0, 1, -2147417850])
@pytest.mark.parametrize("operation_fails", [False, True])
async def test_worker_com_is_initialized_and_balanced_on_its_own_thread(
    monkeypatch: pytest.MonkeyPatch, hresult: int, operation_fails: bool
) -> None:
    events: list[tuple[str, int]] = []
    parent = threading.get_ident()
    failure = RuntimeError("native start refused")

    def initialize(_reserved: object, mode: int) -> int:
        assert mode == 0  # COINIT_MULTITHREADED
        events.append(("initialize", threading.get_ident()))
        return hresult

    ole32 = SimpleNamespace(
        CoInitializeEx=Mock(side_effect=initialize),
        CoUninitialize=Mock(
            side_effect=lambda: events.append(("uninitialize", threading.get_ident()))
        ),
    )
    loader = Mock(return_value=ole32)
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(usb_driver, "sys", SimpleNamespace(platform="win32"))

    def operation() -> int:
        events.append(("operation", threading.get_ident()))
        if operation_fails:
            raise failure
        return 42

    pool = usb_driver._BoundedPortAudioPool()
    try:
        future = pool.submit_tracked(operation)
        if operation_fails:
            with pytest.raises(RuntimeError) as caught:
                await future
            assert caught.value is failure
        else:
            assert await future == 42
        await asyncio.sleep(0)
        assert pool.inflight == 0
    finally:
        pool._executor.shutdown(wait=True)

    expected = ["initialize", "operation"]
    if hresult in (0, 1):  # S_OK and S_FALSE each increment this thread's count.
        expected.append("uninitialize")
    assert [name for name, _ in events] == expected
    assert all(thread != parent for _, thread in events)
    assert len({thread for _, thread in events}) == 1
    loader.assert_called_once_with("ole32")


async def test_failed_com_initialization_refuses_operation_without_uninitializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ole32 = SimpleNamespace(
        CoInitializeEx=Mock(return_value=-2147024882),  # E_OUTOFMEMORY
        CoUninitialize=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=ole32), raising=False)
    monkeypatch.setattr(usb_driver, "sys", SimpleNamespace(platform="win32"))
    operation = Mock()
    pool = usb_driver._BoundedPortAudioPool()
    try:
        with pytest.raises(RuntimeError, match="0x8007000e"):
            await pool.submit_tracked(operation)
        await asyncio.sleep(0)
        assert pool.inflight == 0
    finally:
        pool._executor.shutdown(wait=True)
    operation.assert_not_called()
    ole32.CoUninitialize.assert_not_called()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
async def test_other_platform_workers_do_not_load_windows_com(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    loader = Mock(side_effect=AssertionError("Windows API on another platform"))
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(usb_driver, "sys", SimpleNamespace(platform=platform))
    pool = usb_driver._BoundedPortAudioPool()
    try:
        assert await pool.submit_tracked(lambda: 42) == 42
    finally:
        pool._executor.shutdown(wait=True)
    loader.assert_not_called()
