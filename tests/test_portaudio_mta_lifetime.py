"""Fake COM cookie keeps native stream resources alive between worker calls."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rigplane.audio import backend


def _stream(kind: str, sd: object) -> object:
    config = backend.AudioDeviceConfig(sample_rate=48_000, channels=2)
    if kind == "rx":
        return backend._PortAudioRxStream(sd, 19, config=config, blocksize=0)
    if kind == "tx":
        return backend._PortAudioTxStream(
            sd, object(), 19, sample_rate=48_000, channels=2, blocksize=0
        )
    return backend._PortAudioDuplexStream(sd, object(), 19, config=config, blocksize=0)


@pytest.mark.parametrize("kind", ["rx", "tx", "duplex"])
@pytest.mark.parametrize("failure_at", [None, "open", "start", "close"])
async def test_mta_cookie_spans_open_to_close_and_survives_failed_close(
    monkeypatch: pytest.MonkeyPatch, kind: str, failure_at: str | None
) -> None:
    events: list[str] = []
    failure = RuntimeError("native refusal")
    close_fails = failure_at == "close"

    def retain(pointer: object) -> int:
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 123
        events.append("retain")
        return 0

    def release(cookie: ctypes.c_void_p) -> int:
        assert cookie.value == 123
        events.append("release")
        return 0

    ole32 = SimpleNamespace(
        CoIncrementMTAUsage=Mock(side_effect=retain),
        CoDecrementMTAUsage=Mock(side_effect=release),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=ole32), raising=False)
    monkeypatch.setattr(backend, "sys", SimpleNamespace(platform="win32"))

    class NativeStream:
        def __init__(self, **_kwargs: object) -> None:
            events.append("open")
            if failure_at == "open":
                raise failure

        def start(self) -> None:
            events.append("start")
            if failure_at == "start":
                raise failure

        def stop(self) -> None:
            events.append("stop")

        def close(self) -> None:
            nonlocal close_fails
            events.append("close")
            if close_fails:
                close_fails = False
                raise failure

    stream = _stream(
        kind,
        SimpleNamespace(
            InputStream=NativeStream, OutputStream=NativeStream, Stream=NativeStream
        ),
    )
    start = stream.start() if kind == "tx" else stream.start(lambda _data: None)
    if failure_at in ("open", "start"):
        with pytest.raises(RuntimeError) as caught:
            await start
        assert caught.value is failure
        assert not stream.running
        assert events == (
            ["retain", "open", "release"]
            if failure_at == "open"
            else ["retain", "open", "start", "stop", "close", "release"]
        )
    else:
        await start
        assert stream.running
        assert events == ["retain", "open", "start"]
        if failure_at == "close":
            with pytest.raises(RuntimeError) as caught:
                await stream.stop()
            assert caught.value is failure
            assert "release" not in events
            assert not stream.running
        await stream.stop()
        assert events[-3:] == ["stop", "close", "release"]
    await stream.stop()
    assert events.count("retain") == events.count("release") == 1


@pytest.mark.parametrize("kind", ["rx", "tx", "duplex"])
async def test_mta_retain_failure_never_opens_stream(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    ole32 = SimpleNamespace(
        CoIncrementMTAUsage=Mock(return_value=-2147024882),
        CoDecrementMTAUsage=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=ole32), raising=False)
    monkeypatch.setattr(backend, "sys", SimpleNamespace(platform="win32"))
    constructor = Mock(side_effect=AssertionError("opened after COM refusal"))
    stream = _stream(
        kind,
        SimpleNamespace(
            InputStream=constructor, OutputStream=constructor, Stream=constructor
        ),
    )
    with pytest.raises(RuntimeError, match="0x8007000e"):
        if kind == "tx":
            await stream.start()
        else:
            await stream.start(lambda _data: None)
    constructor.assert_not_called()
    ole32.CoDecrementMTAUsage.assert_not_called()
