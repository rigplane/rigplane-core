"""MOR-4030: opening a WASAPI device in exclusive mode, with a shared fallback.

``PortAudioBackend(capture_mode="exclusive-preferred")`` asks PortAudio for a
WASAPI exclusive stream on a WASAPI device, which bypasses the Windows audio
engine and the effects (AI noise suppression) it applies to a capture
endpoint. When the exclusive open is refused (device busy, exclusive not
allowed, format not accepted) the same open is retried in shared mode with
sample-rate auto-convert, and that fallback is logged ONCE per device.

A fake ``sounddevice`` stands in for PortAudio: no audio device, no Windows.
"""

from __future__ import annotations

import logging

import pytest

from rigplane.audio.backend import AudioDeviceId, PortAudioBackend

EXCLUSIVE = "exclusive-preferred"
WASAPI_DEVICE = 19
MME_DEVICE = 1
_LOGGER_NAME = "rigplane.audio.backend"


class FakeSd:
    """The slice of ``sounddevice`` the streams touch, recording every call."""

    def __init__(
        self,
        *,
        refuse_exclusive=False,
        refuse_shared=False,
        refuse_at="open",
        exclusive_error: BaseException | None = None,
        shared_error: BaseException | None = None,
        unplugged: tuple[int, ...] = (),
    ) -> None:
        self.shared_error = shared_error or OSError(
            "Error opening stream: shared refused"
        )
        # Devices PortAudio no longer enumerates (query_devices raises).
        self.unplugged = set(unplugged)
        self.refuse_exclusive = refuse_exclusive or exclusive_error is not None
        self.exclusive_error = exclusive_error or OSError(
            "Error opening stream: exclusive-mode format not accepted"
        )
        self.refuse_shared = refuse_shared
        # "open": the constructor raises; "start": the stream builds, then
        # ``start()`` raises (PortAudio can refuse at either point).
        self.refuse_at = refuse_at
        self.opened: list[dict[str, object]] = []
        self.settings: list[object] = []
        self.queries: list[tuple[str, int]] = []
        self.probes: list[tuple[str, dict[str, object]]] = []
        self.streams: list[object] = []
        sd = self

        class WasapiSettings:
            def __init__(self, exclusive=False, auto_convert=False) -> None:
                self.exclusive = exclusive
                self.auto_convert = auto_convert
                sd.settings.append(self)

        class _Stream:
            kind = ""

            def __init__(self, **kw: object) -> None:
                sd.opened.append({"kind": self.kind, **kw})
                extra = kw.get("extra_settings")
                self.error: BaseException | None = None
                if extra is not None and extra.exclusive:
                    if sd.refuse_exclusive:
                        self.error = sd.exclusive_error
                elif sd.refuse_shared:
                    self.error = sd.shared_error
                if self.error is not None and sd.refuse_at == "open":
                    raise self.error
                self.closed = False
                self.started = False
                sd.streams.append(self)

            def start(self) -> None:
                if self.error is not None:
                    raise self.error
                self.started = True

            def stop(self) -> None:
                self.started = False

            def close(self) -> None:
                self.closed = True

        class InputStream(_Stream):
            kind = "input"

        class OutputStream(_Stream):
            kind = "output"

        self.WasapiSettings = WasapiSettings
        self.InputStream = InputStream
        self.OutputStream = OutputStream

    def query_devices(self, index):
        self.queries.append(("device", index))
        if index in self.unplugged:
            raise ValueError(f"Error querying device {index}")
        return {
            "name": "Microphone (USB Audio CODEC)",
            "hostapi": 3 if index == WASAPI_DEVICE else 0,
            "max_input_channels": 2,
            "max_output_channels": 2,
        }

    def query_hostapis(self, index):
        self.queries.append(("host", index))
        return {"name": "Windows WASAPI" if index == 3 else "MME"}

    def check_input_settings(self, **kw: object) -> None:
        self.probes.append(("input", kw))

    def check_output_settings(self, **kw: object) -> None:
        self.probes.append(("output", kw))


@pytest.fixture(autouse=True)
def _no_com(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rigplane.audio.backend._WindowsMtaLease.acquire", lambda _self: None
    )
    monkeypatch.setattr(
        "rigplane.audio.backend._WindowsMtaLease.release", lambda _self: None
    )


def _backend(sd: FakeSd, capture_mode: str = EXCLUSIVE) -> PortAudioBackend:
    return PortAudioBackend(
        dependency_loader=lambda: (sd, object()), capture_mode=capture_mode
    )


def _flags(extra) -> tuple[bool, bool]:
    return extra.exclusive, extra.auto_convert


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_wasapi_device_opens_exclusive_first(direction):
    sd = FakeSd()
    backend = _backend(sd)
    if direction == "rx":
        stream = backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start(lambda _pcm: None)
    else:
        stream = backend.open_tx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start()
    assert [_flags(o["extra_settings"]) for o in sd.opened] == [(True, False)]
    assert sd.opened[0]["kind"] == ("input" if direction == "rx" else "output")
    assert stream.exclusive is True
    assert stream.running
    await stream.stop()


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_refused_exclusive_open_retries_in_shared_with_auto_convert(direction):
    sd = FakeSd(refuse_exclusive=True)
    backend = _backend(sd)
    if direction == "rx":
        stream = backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start(lambda _pcm: None)
    else:
        stream = backend.open_tx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start()
    # Exclusive first, then shared WASAPI with sample-rate conversion allowed.
    assert [_flags(o["extra_settings"]) for o in sd.opened] == [
        (True, False),
        (False, True),
    ]
    assert stream.exclusive is False
    assert stream.running
    await stream.stop()


async def test_fallback_is_logged_once_across_repeated_opens(
    caplog: pytest.LogCaptureFixture,
):
    sd = FakeSd(refuse_exclusive=True)
    backend = _backend(sd)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        for _ in range(3):
            stream = backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
            await stream.start(lambda _pcm: None)
            await stream.stop()
    # Every open still tries exclusive first (the device may be free next time).
    assert [_flags(o["extra_settings"]) for o in sd.opened] == [
        (True, False),
        (False, True),
    ] * 3
    messages = [
        r.getMessage()
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.WARNING
    ]
    assert len(messages) == 1
    assert "exclusive" in messages[0] and "shared" in messages[0]
    assert str(WASAPI_DEVICE) in messages[0]


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_exclusive_refused_at_start_closes_the_half_open_stream(direction):
    sd = FakeSd(refuse_exclusive=True, refuse_at="start")
    backend = _backend(sd)
    if direction == "rx":
        stream = backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start(lambda _pcm: None)
    else:
        stream = backend.open_tx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start()
    assert [_flags(o["extra_settings"]) for o in sd.opened] == [
        (True, False),
        (False, True),
    ]
    refused, shared = sd.streams
    assert refused.closed and not refused.started
    assert shared.started and not shared.closed
    assert stream.exclusive is False
    await stream.stop()
    assert shared.closed


@pytest.mark.parametrize("refuse_at", ["open", "start"])
async def test_a_shared_failure_after_an_exclusive_failure_is_raised(refuse_at):
    sd = FakeSd(refuse_exclusive=True, refuse_shared=True, refuse_at=refuse_at)
    stream = _backend(sd).open_rx(AudioDeviceId(WASAPI_DEVICE))
    with pytest.raises(OSError, match="shared refused"):
        await stream.start(lambda _pcm: None)
    assert not stream.running


async def test_exclusive_that_opens_does_not_log(caplog: pytest.LogCaptureFixture):
    sd = FakeSd()
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        stream = _backend(sd).open_rx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start(lambda _pcm: None)
        await stream.stop()
    assert not [r for r in caplog.records if r.name == _LOGGER_NAME]


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_non_wasapi_device_is_opened_exactly_as_before(direction):
    sd = FakeSd()
    backend = _backend(sd)
    if direction == "rx":
        stream = backend.open_rx(AudioDeviceId(MME_DEVICE))
        await stream.start(lambda _pcm: None)
    else:
        stream = backend.open_tx(AudioDeviceId(MME_DEVICE))
        await stream.start()
    assert "extra_settings" not in sd.opened[0]
    assert sd.settings == []
    assert stream.exclusive is False
    await stream.stop()


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_shared_mode_never_touches_wasapi_settings(direction):
    sd = FakeSd()
    backend = _backend(sd, "shared")
    if direction == "rx":
        stream = backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start(lambda _pcm: None)
    else:
        stream = backend.open_tx(AudioDeviceId(WASAPI_DEVICE))
        await stream.start()
    assert "extra_settings" not in sd.opened[0]
    assert sd.settings == []
    assert sd.queries == []
    assert stream.exclusive is False
    await stream.stop()


def test_default_backend_mode_is_shared():
    sd = FakeSd()
    backend = PortAudioBackend(dependency_loader=lambda: (sd, object()))
    assert backend.check_sample_rate(AudioDeviceId(WASAPI_DEVICE), 48_000) is True
    assert sd.probes == [("input", {"device": WASAPI_DEVICE, "samplerate": 48_000})]
    assert sd.settings == []


@pytest.mark.parametrize("direction,kind", [("rx", "input"), ("tx", "output")])
def test_format_probe_allows_auto_convert_on_wasapi(direction, kind):
    sd = FakeSd()
    backend = _backend(sd)
    assert (
        backend.check_sample_rate(
            AudioDeviceId(WASAPI_DEVICE), 48_000, direction=direction
        )
        is True
    )
    [(probed, kw)] = sd.probes
    assert probed == kind
    assert (kw["device"], kw["samplerate"]) == (WASAPI_DEVICE, 48_000)
    assert _flags(kw["extra_settings"]) == (False, True)


def test_format_probe_on_other_host_apis_is_unchanged():
    sd = FakeSd()
    assert _backend(sd).check_sample_rate(AudioDeviceId(MME_DEVICE), 48_000) is True
    assert sd.probes == [("input", {"device": MME_DEVICE, "samplerate": 48_000})]


async def test_host_api_lookup_failure_degrades_to_a_plain_open():
    sd = FakeSd()

    def broken(index):
        raise RuntimeError("host metadata unavailable")

    sd.query_devices = broken  # type: ignore[method-assign]
    stream = _backend(sd).open_rx(AudioDeviceId(WASAPI_DEVICE))
    await stream.start(lambda _pcm: None)
    assert "extra_settings" not in sd.opened[0]
    assert stream.exclusive is False
    await stream.stop()


def test_unknown_capture_mode_is_refused():
    with pytest.raises(ValueError, match="capture_mode"):
        PortAudioBackend(capture_mode="exclusive")


async def test_missing_wasapi_settings_degrades_to_a_plain_open():
    sd = FakeSd()

    def unavailable(**_kw):
        raise AttributeError("no WASAPI support in this PortAudio build")

    sd.WasapiSettings = unavailable  # type: ignore[assignment]
    backend = _backend(sd)
    stream = backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
    await stream.start(lambda _pcm: None)
    assert "extra_settings" not in sd.opened[0]
    assert stream.exclusive is False
    await stream.stop()
    assert backend.check_sample_rate(AudioDeviceId(WASAPI_DEVICE), 48_000) is True
    assert "extra_settings" not in sd.probes[0][1]
