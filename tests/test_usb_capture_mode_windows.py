"""MOR-4030: the ``exclusive-preferred`` capture mode, Windows pairing and notice.

Windows' default AI noise-suppression effect pack gates non-speech audio on the
shared-mode capture endpoint of a radio's USB codec. WASAPI exclusive mode
bypasses the engine and its effects, so ``exclusive-preferred`` makes pairing
prefer the WASAPI host API for BOTH directions. The default ``shared`` mode
keeps today's choice (the first host API with one input and one output: MME).

These tests replay the recorded W1 capture (which has a real WASAPI host API)
and the recorded dual-codec W3 capture plus synthetic WASAPI clones of it; no
audio device and no Windows machine is involved.
"""

from __future__ import annotations

import copy
import logging

import pytest
from test_usb_pairing_windows import (
    _inject_windows,
    _synthetic_session,
    _w1_session,
    _w3_session,
)

from rigplane.audio._usb_adapt_windows import adapt_windows
from rigplane.audio._usb_pairing import PairOk, PairRefused, pair
from rigplane.audio.backend import (
    AudioDeviceConfig,
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
)
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.profiles import get_radio_profile

WASAPI = "Windows WASAPI"
EXCLUSIVE = "exclusive-preferred"
_LOGGER_NAME = "rigplane.audio.usb_driver"


def _add_wasapi(session, *, drop_render=False):
    """Clone every MME device into a new ``Windows WASAPI`` host API."""
    session = copy.deepcopy(session)
    audio = session["raw"]["portaudio"]
    api = len(audio["hostapis"])
    next_index = max(d["index"] for d in audio["devices"]) + 1
    clones = []
    for device in audio["devices"]:
        if device["hostapi"] != 0:
            continue
        if device["max_input_channels"] == 0 and device["max_output_channels"] == 0:
            continue
        if drop_render and device["max_output_channels"] > 0:
            continue
        clones.append(
            dict(device, index=next_index, hostapi=api, default_samplerate=48000.0)
        )
        next_index += 1
    audio["devices"].extend(clones)
    audio["hostapis"].append(
        {
            "index": api,
            "name": WASAPI,
            "device_count": len(clones),
            "default_input": -1,
            "default_output": -1,
        }
    )
    return session


def _identity(model):
    return get_radio_profile(model).usb_identity


# (session factory, serial port, profile, expected MME pair, expected WASAPI pair)
FIXTURES = {
    "w1-recorded-wasapi": (_w1_session, "COM3", None, (1, 5), (19, 17)),
    "synthetic-single-codec": (
        lambda: _add_wasapi(_synthetic_session()),
        "COM3",
        None,
        (1, 5),
        (6, 7),
    ),
    "w3-first-radio": (
        lambda: _add_wasapi(_w3_session()),
        "COM5",
        "IC-7610",
        (1, 5),
        (7, 9),
    ),
    "w3-second-radio": (
        lambda: _add_wasapi(_w3_session()),
        "COM3",
        "IC-7300",
        (2, 6),
        (8, 10),
    ),
}


@pytest.mark.parametrize("fixture", FIXTURES)
def test_exclusive_preferred_pairs_both_directions_on_wasapi(fixture):
    make, port, model, _mme, wasapi = FIXTURES[fixture]
    snapshot = adapt_windows(make())
    identity = _identity(model) if model else None
    result = pair(snapshot, port, identity, prefer_hostapi=WASAPI)
    assert isinstance(result, PairOk), result
    # One PairOk carries one host API: RX and TX can never straddle two.
    assert (result.rx_index, result.tx_index, result.hostapi) == (*wasapi, WASAPI)
    assert result.evidence[-1] == "hostapi:" + WASAPI


@pytest.mark.parametrize("fixture", FIXTURES)
def test_shared_pairing_keeps_the_first_host_api(fixture):
    make, port, model, mme, _wasapi = FIXTURES[fixture]
    snapshot = adapt_windows(make())
    identity = _identity(model) if model else None
    result = pair(snapshot, port, identity)
    assert isinstance(result, PairOk), result
    assert (result.rx_index, result.tx_index, result.hostapi) == (*mme, "MME")


@pytest.mark.parametrize(
    "make,port,model,mme",
    [
        (_w1_session, "COM3", None, (1, 5)),
        (_w3_session, "COM5", "IC-7610", (1, 5)),
        (_synthetic_session, "COM3", None, (1, 5)),
    ],
    ids=["w1-without-wasapi-render", "w3-mme-only", "synthetic-mme-only"],
)
def test_exclusive_preferred_falls_back_when_wasapi_has_no_unique_pair(
    make, port, model, mme
):
    session = make()
    if make is _w1_session:
        # WASAPI keeps the input but loses the codec's render endpoint.
        for device in session["raw"]["portaudio"]["devices"]:
            if device["hostapi"] == 3 and device["name"].startswith("Speakers"):
                device["max_output_channels"] = 0
    result = pair(
        adapt_windows(session),
        port,
        _identity(model) if model else None,
        prefer_hostapi=WASAPI,
    )
    assert isinstance(result, PairOk), result
    assert (result.rx_index, result.tx_index, result.hostapi) == (*mme, "MME")


def test_exclusive_preferred_refusal_lists_wasapi_candidates():
    refusal = pair(adapt_windows(_w1_session()), "missing", None, prefer_hostapi=WASAPI)
    assert isinstance(refusal, PairRefused)
    assert refusal.candidates
    assert {c.hostapi for c in refusal.candidates} == {WASAPI}


# --- driver selection ------------------------------------------------------


def _driver(backend, capture_mode, **kwargs):
    return UsbAudioDriver(
        AudioDeviceConfig(
            capture_mode=capture_mode,
            rx_device=kwargs.pop("rx_device", None),
            tx_device=kwargs.pop("tx_device", None),
        ),
        backend=backend,
        serial_port="COM3",
        **kwargs,
    )


async def _selected(driver):
    await driver.start_rx(lambda _frame: None)
    try:
        await driver.start_tx()
        try:
            return driver.selected_rx_device.index, driver.selected_tx_device.index
        finally:
            await driver.stop_tx()
    finally:
        await driver.stop_rx()


@pytest.mark.parametrize("fixture", ["w1-recorded-wasapi", "synthetic-single-codec"])
async def test_driver_pairs_wasapi_for_both_directions_when_exclusive_preferred(
    monkeypatch, fixture
):
    make, _port, _model, _mme, wasapi = FIXTURES[fixture]
    backend, _ = _inject_windows(monkeypatch, make())
    assert await _selected(_driver(backend, EXCLUSIVE)) == wasapi


@pytest.mark.parametrize("fixture", ["w1-recorded-wasapi", "synthetic-single-codec"])
async def test_driver_keeps_mme_by_default_even_when_wasapi_exists(
    monkeypatch, fixture
):
    make, _port, _model, mme, _wasapi = FIXTURES[fixture]
    backend, _ = _inject_windows(monkeypatch, make())
    assert await _selected(_driver(backend, "shared")) == mme
    backend, _ = _inject_windows(monkeypatch, make())
    # No config at all is the shared default.
    default = UsbAudioDriver(backend=backend, serial_port="COM3")
    assert await _selected(default) == mme


async def test_driver_exclusive_preferred_falls_back_to_mme_when_pairing_needs_it(
    monkeypatch,
):
    session = _w1_session()
    for device in session["raw"]["portaudio"]["devices"]:
        if device["hostapi"] == 3 and device["name"].startswith("Speakers"):
            device["max_output_channels"] = 0
    backend, _ = _inject_windows(monkeypatch, session)
    assert await _selected(_driver(backend, EXCLUSIVE)) == (1, 5)


# --- saved selectors resolve across host APIs ------------------------------


def _saved_mme_selectors(session, variant):
    """Build the ``MME|...`` selectors a Pro/Core release before this change saved."""
    session = copy.deepcopy(session)
    if variant == "name-id":
        rows = session["raw"]["windows"]["devnodes"]
        for row in tuple(rows):
            if row["class_name"] == "AudioEndpoint":
                rows.append(dict(row, instance_id=row["instance_id"] + "-duplicate"))
    refusal = pair(adapt_windows(session), "missing", None)
    assert isinstance(refusal, PairRefused)
    choices = {
        c.direction: c.stable_id
        for c in refusal.candidates
        if c.portaudio_index in {1, 5}
    }
    assert all(choice.startswith("MME|") for choice in choices.values())
    if variant == "name-id":
        assert choices["capture"] == "MME|name:Microphone (USB Audio CODEC )"
    else:
        assert choices["capture"].startswith("MME|SWD\\MMDEVAPI\\{0.0.1.")
    return session, choices


@pytest.mark.parametrize("variant", ["endpoint-id", "name-id"])
async def test_saved_mme_selector_resolves_to_wasapi_by_endpoint(monkeypatch, variant):
    session, saved = _saved_mme_selectors(_w1_session(), variant)
    backend, _ = _inject_windows(monkeypatch, session)
    driver = _driver(
        backend, EXCLUSIVE, rx_device=saved["capture"], tx_device=saved["render"]
    )
    assert await _selected(driver) == (19, 17)


@pytest.mark.parametrize("variant", ["endpoint-id", "name-id"])
async def test_saved_mme_selector_still_means_mme_in_shared_mode(monkeypatch, variant):
    session, saved = _saved_mme_selectors(_w1_session(), variant)
    backend, _ = _inject_windows(monkeypatch, session)
    driver = _driver(
        backend, "shared", rx_device=saved["capture"], tx_device=saved["render"]
    )
    assert await _selected(driver) == (1, 5)


async def test_saved_truncated_mme_name_resolves_to_full_wasapi_name(monkeypatch):
    session = _w1_session()
    full = "USB Audio CODEC 12345678901234 endpoint"
    truncated = full[:31]
    for row in session["raw"]["windows"]["devnodes"]:
        if row["class_name"] == "AudioEndpoint":
            row["friendly_name"] = full
    devices = session["raw"]["portaudio"]["devices"]
    for index in (1, 5):
        devices[index]["name"] = truncated
    for index in (17, 19):
        devices[index]["name"] = full
    for row in tuple(session["raw"]["windows"]["devnodes"]):
        if row["class_name"] == "AudioEndpoint":
            session["raw"]["windows"]["devnodes"].append(
                dict(row, instance_id=row["instance_id"] + "-duplicate")
            )
    backend, _ = _inject_windows(monkeypatch, session)
    saved = "MME|name:" + truncated
    driver = _driver(backend, EXCLUSIVE, rx_device=saved, tx_device=saved)
    assert await _selected(driver) == (19, 17)


async def test_saved_wasapi_selector_is_kept_when_exclusive_preferred(monkeypatch):
    session = _w1_session()
    refusal = pair(adapt_windows(session), "missing", None, prefer_hostapi=WASAPI)
    assert isinstance(refusal, PairRefused)
    saved = {
        c.direction: c.stable_id
        for c in refusal.candidates
        if c.portaudio_index in {17, 19}
    }
    backend, _ = _inject_windows(monkeypatch, session)
    driver = _driver(
        backend, EXCLUSIVE, rx_device=saved["capture"], tx_device=saved["render"]
    )
    assert await _selected(driver) == (19, 17)


async def test_saved_selector_without_a_wasapi_twin_keeps_its_own_host_api(
    monkeypatch,
):
    session, saved = _saved_mme_selectors(_w1_session(), "endpoint-id")
    for device in session["raw"]["portaudio"]["devices"]:
        if device["hostapi"] == 3 and "USB Audio CODEC" in device["name"]:
            device["max_input_channels"] = 0
            device["max_output_channels"] = 0
    backend, _ = _inject_windows(monkeypatch, session)
    driver = _driver(
        backend, EXCLUSIVE, rx_device=saved["capture"], tx_device=saved["render"]
    )
    assert await _selected(driver) == (1, 5)


# --- the digital-silence notice -------------------------------------------

WINDOWS_NOTICE = (
    "Windows is giving this app only silence from the radio's USB input. "
    'Turn off "Audio enhancements" for this input in Windows Sound settings.'
)
_SILENT = b"\x00" * 1920
_LOUD = b"\x11\x22" * 960


def _codec_backend():
    return FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="USB Audio CODEC",
                input_channels=1,
                output_channels=1,
                default_samplerate=48_000,
                is_default_input=True,
                is_default_output=True,
            )
        ]
    )


def _warnings(caplog):
    return [
        r
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.WARNING
    ]


async def _silent_run(monkeypatch, caplog, *, system, frames, config=None):
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: system)
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})
    backend = _codec_backend()
    driver = UsbAudioDriver(config, backend=backend)
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        for frame in frames:
            stream.inject_frame(frame)
    return driver, stream


async def test_windows_shared_silence_raises_one_notice_that_names_the_fix(
    monkeypatch, caplog
):
    driver, _ = await _silent_run(
        monkeypatch, caplog, system="Windows", frames=[_SILENT] * 30
    )
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert WINDOWS_NOTICE in warnings[0].getMessage()
    assert driver.rx_silent is True


async def test_windows_notice_waits_for_the_existing_ten_second_window(
    monkeypatch, caplog
):
    _, _ = await _silent_run(
        monkeypatch, caplog, system="Windows", frames=[_SILENT] * 9
    )
    assert _warnings(caplog) == []


async def test_windows_notice_fires_again_only_after_audio_returned(
    monkeypatch, caplog
):
    _, _ = await _silent_run(
        monkeypatch,
        caplog,
        system="Windows",
        frames=[_SILENT] * 10 + [_SILENT] * 10 + [_LOUD] + [_SILENT] * 10,
    )
    assert [WINDOWS_NOTICE in r.getMessage() for r in _warnings(caplog)] == [True, True]


async def test_windows_notice_does_not_fire_for_audio(monkeypatch, caplog):
    driver, _ = await _silent_run(
        monkeypatch, caplog, system="Windows", frames=[_LOUD] * 30
    )
    assert _warnings(caplog) == []
    assert driver.rx_silent is False


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
async def test_notice_does_not_fire_on_other_platforms(monkeypatch, caplog, system):
    _, _ = await _silent_run(monkeypatch, caplog, system=system, frames=[_SILENT] * 10)
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "Audio enhancements" not in warnings[0].getMessage()
    assert "digital silence" in warnings[0].getMessage()


async def test_notice_is_not_raised_when_the_capture_is_exclusive(monkeypatch, caplog):
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Windows")
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})
    backend = _codec_backend()
    driver = UsbAudioDriver(AudioDeviceConfig(capture_mode=EXCLUSIVE), backend=backend)
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]
    # The real stream says how it opened; effects cannot gate an exclusive open.
    stream.exclusive = True
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        for _ in range(10):
            stream.inject_frame(_SILENT)
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "Audio enhancements" not in warnings[0].getMessage()


async def test_notice_is_raised_when_exclusive_preferred_fell_back_to_shared(
    monkeypatch, caplog
):
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Windows")
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})
    backend = _codec_backend()
    driver = UsbAudioDriver(AudioDeviceConfig(capture_mode=EXCLUSIVE), backend=backend)
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]
    stream.exclusive = False
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        for _ in range(10):
            stream.inject_frame(_SILENT)
    assert [WINDOWS_NOTICE in r.getMessage() for r in _warnings(caplog)] == [True]
