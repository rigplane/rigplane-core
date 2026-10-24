"""Replay the real W1 recording without opening audio or a serial port."""

import json
from pathlib import Path
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from unittest.mock import Mock

from rigplane.audio._usb_adapt_windows import adapt_windows
from rigplane.audio._usb_pairing import PairOk, PairRefused, pair
from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId


def _w1_session():
    return json.loads(
        Path("tests/fixtures/usb_audio/windows/w1-gtr7-ic7300.json").read_text()
    )["sessions"][0]


def _synthetic_session(*, nested=False, codec=("08BB", "2901")):
    from rigplane.audio._windows_devnode import DevNodeRecord
    from rigplane.audio._usb_raw_windows import RawWindows

    hub = r"USB\VID_1234&PID_0001\H"
    child_hub = r"USB\VID_1234&PID_0002\N"
    serial = r"USB\VID_10C4&PID_EA60\S"
    audio = rf"USB\VID_{codec[0]}&PID_{codec[1]}\C"
    rows = [
        DevNodeRecord("ROOT", "", "System", "", "", (), ()),
        DevNodeRecord(hub, "ROOT", "USB", "Hub", "", (), (r"USB\USB20_HUB",)),
        DevNodeRecord(serial, hub, "Ports", "Serial (COM3)", "", (), ()),
        DevNodeRecord(
            audio, child_hub if nested else hub, "MEDIA", "Codec", "", (), ()
        ),
        DevNodeRecord(
            r"SWD\MMDEVAPI\{0.0.1.capture}",
            audio,
            "AudioEndpoint",
            "Microphone (USB Audio CODEC )",
            "",
            (),
            (),
        ),
        DevNodeRecord(
            r"SWD\MMDEVAPI\{0.0.0.render}",
            audio,
            "AudioEndpoint",
            "Speakers (USB Audio CODEC )",
            "",
            (),
            (),
        ),
    ]
    if nested:
        rows.append(
            DevNodeRecord(
                child_hub, hub, "USB", "Nested hub", "", (), (r"USB\USB20_HUB",)
            )
        )
    from test_usb_pairing_rules import _snapshot

    enumeration = _snapshot().portaudio
    # PortAudio list positions are its indices, including non-audio entries.
    devices = [
        dict(
            asdict(enumeration.devices[0]),
            index=i,
            name="Built-in",
            max_input_channels=0,
            max_output_channels=0,
        )
        for i in range(6)
    ]
    for device in enumeration.devices:
        devices[device.index] = asdict(device)
    return {
        "raw": {
            "windows": RawWindows("cfgmgr32", tuple(rows)).to_json_dict(),
            "serial_ports": [],
            "portaudio": {**enumeration.to_json_dict(), "devices": devices},
        }
    }


def _inject_windows(monkeypatch, session):
    from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
    from rigplane.audio._usb_raw_windows import RawWindows

    enumeration = session["raw"]["portaudio"]
    sd = SimpleNamespace(
        query_devices=lambda: enumeration["devices"],
        query_hostapis=lambda: [
            {
                "name": api["name"],
                "devices": [
                    d["index"]
                    for d in enumeration["devices"]
                    if d["hostapi"] == api["index"]
                ],
                "default_input_device": api["default_input"],
                "default_output_device": api["default_output"],
            }
            for api in enumeration["hostapis"]
        ],
    )
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(d["index"]),
                name=d["name"],
                input_channels=d["max_input_channels"],
                output_channels=d["max_output_channels"],
            )
            for d in enumeration["devices"]
        ]
    )
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Windows")
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})
    monkeypatch.setattr(
        "rigplane.audio.usb_driver._extract_sounddevice_module", lambda _backend: sd
    )
    monkeypatch.setattr(
        "rigplane.audio._usb_raw_windows.read_raw_windows",
        lambda timeout_s: RawWindows.from_json_dict(session["raw"]["windows"]),
    )
    return backend, sd


@pytest.fixture
def snapshot():
    return adapt_windows(_w1_session())


@pytest.mark.parametrize(
    "identity", [None, UsbAudioIdentity(frozenset({UsbFunctionId("08BB", "2901")}))]
)
def test_s2_s5_w1_mme_pair_with_or_without_profile(snapshot, identity):
    result = pair(snapshot, "com3", identity)
    assert isinstance(result, PairOk)
    assert (result.rx_index, result.tx_index, result.hostapi) == (1, 5, "MME")
    assert result.codec_node.startswith("USB\\VID_08BB&PID_2901\\")
    assert result.rx_id.startswith("SWD\\MMDEVAPI\\{0.0.1.")
    assert result.tx_id.startswith("SWD\\MMDEVAPI\\{0.0.0.")
    assert result.evidence


def test_s2_w1_wrong_codec_identity_retains_nearby_mme_candidates(snapshot):
    result = pair(
        snapshot, "COM3", UsbAudioIdentity(frozenset({UsbFunctionId("0D8C", "0016")}))
    )
    assert isinstance(result, PairRefused)
    assert (result.reason, result.wire_reason) == (
        "codec_identity_mismatch",
        "usb_topology_unavailable",
    )
    assert {
        (candidate.portaudio_index, candidate.direction)
        for candidate in result.candidates
        if candidate.near_radio
    } == {(1, "capture"), (5, "render")}
    assert all(candidate.hostapi == "MME" for candidate in result.candidates)
    assert all(
        candidate.portaudio_index not in {0, 4} for candidate in result.candidates
    )
    assert result.candidates[0].near_radio


@pytest.mark.parametrize("direction", ["rx", "tx"])
@pytest.mark.parametrize("name_fallback", [False, True], ids=["endpoint-id", "name-id"])
async def test_stable_override_revalidated_on_every_open(
    monkeypatch, direction, name_fallback
):
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import (
        AudioDeviceSelectionError,
        UsbAudioDriver,
    )

    session = _w1_session()
    if name_fallback:
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
    if name_fallback:
        assert choices["capture"] == "MME|name:Microphone (USB Audio CODEC )"
        assert choices["render"] == "MME|name:Speakers (USB Audio CODEC )"
    backend, _ = _inject_windows(monkeypatch, session)
    pairing = Mock(side_effect=AssertionError("explicit choices must bypass pairing"))
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    driver = UsbAudioDriver(
        backend=backend,
        serial_port="COM3",
        rx_device=choices["capture"],
        tx_device=choices["render"],
    )
    start = (
        (lambda: driver.start_rx(lambda _frame: None))
        if direction == "rx"
        else driver.start_tx
    )
    stop = driver.stop_rx if direction == "rx" else driver.stop_tx
    try:
        await start()
        assert (driver.selected_rx_device.index, driver.selected_tx_device.index) == (
            1,
            5,
        )
        await stop()
        # Keep the same driver, but remove the saved endpoint from the next enumeration.
        index = 1 if direction == "rx" else 5
        devices = [d for d in backend.list_devices() if int(d.id) != index]
        monkeypatch.setattr(backend, "list_devices", lambda: devices)
        session["raw"]["portaudio"]["devices"][index]["name"] = "Removed device"
        with pytest.raises(
            AudioDeviceSelectionError,
            match=f"Unknown {direction.upper()} device override",
        ):
            await start()
        assert (
            len(backend.rx_streams)
            + len(backend.tx_streams)
            + len(backend.duplex_streams)
            == 1
        )
        pairing.assert_not_called()
    finally:
        await stop()


@pytest.mark.parametrize(
    "direction,index,flow", [("rx", 1, "capture"), ("tx", 5, "render")]
)
@pytest.mark.parametrize("condition", ["missing", "ambiguous", "empty"])
async def test_stable_override_refuses_without_pairing_or_streams(
    monkeypatch, direction, index, flow, condition
):
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import (
        AudioDeviceAmbiguousError,
        AudioDeviceSelectionError,
        UsbAudioDriver,
    )

    session = _w1_session()
    refusal = pair(adapt_windows(session), "missing", None)
    assert isinstance(refusal, PairRefused)
    selector = next(
        c.stable_id
        for c in refusal.candidates
        if c.portaudio_index == index and c.direction == flow
    )
    if condition == "ambiguous":
        devices = session["raw"]["portaudio"]["devices"]
        devices[2] = dict(devices[index], index=2)
    else:
        selector = "MME|name:missing saved device"
        # A stale id must not fall through to the legacy substring matcher.
        session["raw"]["portaudio"]["devices"][index]["name"] = (
            selector + " replacement"
        )
    backend, _ = _inject_windows(monkeypatch, session)
    if condition == "empty":
        monkeypatch.setattr(backend, "list_devices", lambda: [])
    pairing = Mock(side_effect=AssertionError("explicit choices must bypass pairing"))
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    driver = UsbAudioDriver(
        backend=backend,
        serial_port="COM3",
        **{direction + "_device": selector},
    )
    error = (
        AudioDeviceAmbiguousError
        if condition == "ambiguous"
        else AudioDeviceSelectionError
    )
    message = "Ambiguous" if condition == "ambiguous" else "Unknown"
    with pytest.raises(error, match=f"{message} {direction.upper()} device override"):
        if direction == "rx":
            await driver.start_rx(lambda _frame: None)
        else:
            await driver.start_tx()
    assert not (backend.rx_streams or backend.tx_streams or backend.duplex_streams)
    pairing.assert_not_called()


@pytest.mark.parametrize(
    "session",
    [_w1_session, lambda: _synthetic_session(nested=True)],
    ids=["w1-ic7300", "nested-ic7610"],
)
@pytest.mark.parametrize("serial_name", [None, "Radio COM3"])
async def test_driver_shared_pairing_selects_mme_indices(
    monkeypatch, session, serial_name
):
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import UsbAudioDriver

    session = session()
    if serial_name is not None:
        next(
            row
            for row in session["raw"]["windows"]["devnodes"]
            if row["class_name"] == "Ports"
        )["friendly_name"] = serial_name
    session["raw"]["serial_ports"] = []
    backend, _ = _inject_windows(monkeypatch, session)
    paired = []
    shared_pair = _usb_pairing.pair

    def record_pair(snapshot, port, identity):
        result = shared_pair(snapshot, port, identity)
        paired.append(result)
        return result

    monkeypatch.setattr(_usb_pairing, "pair", record_pair)
    driver = UsbAudioDriver(backend=backend, serial_port="COM3")
    await driver.start_rx(lambda _frame: None)
    try:
        assert (driver.selected_rx_device.index, driver.selected_tx_device.index) == (
            1,
            5,
        )
        assert driver.rx_running
        assert len(paired) == 1
        assert isinstance(paired[0], PairOk)
        assert paired[0].hostapi == "MME"
    finally:
        await driver.stop_rx()


@pytest.mark.parametrize("bounded", [False, True])
async def test_driver_refusal_carries_candidates_and_profile_identity(
    monkeypatch, bounded
):
    from rigplane.audio.usb_driver import UsbAudioDriver, audio_start_failure_reason

    backend, _ = _inject_windows(monkeypatch, _w1_session())
    driver = UsbAudioDriver(
        backend=backend,
        serial_port="COM3",
        usb_identity=UsbAudioIdentity(frozenset({UsbFunctionId("0D8C", "0016")})),
    )
    with pytest.raises(RuntimeError) as raised:
        if bounded:
            await driver.start_rx(lambda _frame: None)
        else:
            driver._ensure_selected_devices()
    error = raised.value
    assert error.reason == "codec_identity_mismatch"
    assert error.evidence[-1] == "refused:codec_identity_mismatch"
    assert {
        (c.portaudio_index, c.direction) for c in error.candidates if c.near_radio
    } == {(1, "capture"), (5, "render")}
    assert all(
        c.stable_id and c.display_name and c.hostapi == "MME" for c in error.candidates
    )
    assert audio_start_failure_reason(error) == "usb_topology_unavailable"
    assert not backend.rx_streams


@pytest.mark.parametrize("serial_name", ["Serial (COM3)", "Radio COM3"])
def test_windows_public_resolver_uses_shared_rule_for_cmedia_shape(
    monkeypatch, serial_name
):
    from rigplane.usb_audio_resolve import resolve_audio_for_serial_port

    session = _synthetic_session(codec=("0D8C", "0012"))
    # Synthetic composite device: serial and audio functions have one owner.
    rows = session["raw"]["windows"]["devnodes"]
    audio = next(row for row in rows if row["class_name"] == "MEDIA")
    serial = next(row for row in rows if row["class_name"] == "Ports")
    serial["instance_id"] = r"USB\VID_0D8C&PID_0012&MI_00\S"
    serial["parent_id"] = audio["instance_id"]
    serial["friendly_name"] = serial_name
    for row, index, name in (
        (rows[4], 1, "Microphone (C-Media USB Audio Device)"),
        (rows[5], 5, "Speakers (C-Media USB Audio Device)"),
    ):
        row["friendly_name"] = name
        session["raw"]["portaudio"]["devices"][index]["name"] = name[:31]
    _, sd = _inject_windows(monkeypatch, session)
    result = resolve_audio_for_serial_port("COM3", sounddevice_module=sd)
    assert result is not None
    assert (result.rx_device_index, result.tx_device_index) == (1, 5)


@pytest.mark.parametrize("index", [1, 5])
async def test_driver_refuses_pair_indices_missing_from_backend(monkeypatch, index):
    from rigplane.audio.usb_driver import AudioDeviceSelectionError, UsbAudioDriver

    backend, _ = _inject_windows(monkeypatch, _w1_session())
    devices = [device for device in backend.list_devices() if int(device.id) != index]
    monkeypatch.setattr(backend, "list_devices", lambda: devices)
    driver = UsbAudioDriver(backend=backend, serial_port="COM3")
    with pytest.raises(AudioDeviceSelectionError, match="enumerated audio devices"):
        await driver.start_rx(lambda _frame: None)
    assert not backend.rx_streams


@pytest.mark.parametrize(
    "reason,category",
    [
        ("unavailable", "usb_topology_unavailable"),
        ("serial_not_found", "usb_topology_unavailable"),
        ("topology_incomplete", "usb_topology_unavailable"),
        ("containment_unproven", "usb_topology_unavailable"),
        ("serial_ambiguous", "usb_ambiguous"),
        ("no_codec", "usb_topology_unavailable"),
        ("codec_ambiguous", "usb_ambiguous"),
        ("codec_identity_mismatch", "usb_topology_unavailable"),
        ("contested", "usb_ambiguous"),
        ("endpoint_names", "usb_topology_names"),
    ],
)
def test_pairing_exception_keeps_each_public_failure_category(reason, category):
    from rigplane.audio._usb_pairing import WindowsAudioTopologyError
    from rigplane.audio.usb_driver import audio_start_failure_reason

    refusal = PairRefused(reason, category, (), ("refused:" + reason,))
    error = WindowsAudioTopologyError(refusal)
    assert (error.reason, error.evidence, error.candidates) == (
        reason,
        refusal.evidence,
        refusal.candidates,
    )
    assert audio_start_failure_reason(error) == category


def test_windows_refusal_exceptions_are_public_shim_exports():
    from rigplane.audio import _usb_pairing, _usb_resolve
    from rigplane.usb_audio_resolve import (
        WindowsAudioAmbiguityError,
        WindowsAudioEndpointNameError,
        WindowsAudioTopologyError,
    )

    for error_type in (
        WindowsAudioTopologyError,
        WindowsAudioAmbiguityError,
        WindowsAudioEndpointNameError,
    ):
        assert getattr(_usb_pairing, error_type.__name__) is error_type
        assert error_type.__name__ in _usb_resolve.__all__
        assert issubclass(error_type, WindowsAudioTopologyError)


@pytest.mark.parametrize("entrypoint", ["driver", "bounded_driver", "resolver"])
@pytest.mark.parametrize(
    "reason,category,error_name",
    [
        ("serial_not_found", "usb_topology_unavailable", "WindowsAudioTopologyError"),
        ("serial_ambiguous", "usb_ambiguous", "WindowsAudioAmbiguityError"),
        ("endpoint_names", "usb_topology_names", "WindowsAudioEndpointNameError"),
    ],
)
async def test_public_refusal_subclass_matches_wire_reason(
    monkeypatch, entrypoint, reason, category, error_name
):
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import UsbAudioDriver, audio_start_failure_reason
    import rigplane.usb_audio_resolve as public

    backend, sd = _inject_windows(monkeypatch, _w1_session())
    candidates = pair(adapt_windows(_w1_session()), "missing", None).candidates
    refusal = PairRefused(reason, category, candidates, ("refused:" + reason,))
    monkeypatch.setattr(_usb_pairing, "pair", lambda *args: refusal)

    with pytest.raises(public.WindowsAudioTopologyError) as raised:
        if entrypoint == "resolver":
            public.resolve_audio_for_serial_port("COM3", sounddevice_module=sd)
        else:
            driver = UsbAudioDriver(backend=backend, serial_port="COM3")
            if entrypoint == "bounded_driver":
                await driver.start_rx(lambda _frame: None)
            else:
                driver._ensure_selected_devices()

    error = raised.value
    assert type(error) is getattr(public, error_name)
    assert error.refusal is refusal
    assert (error.reason, error.evidence, error.candidates) == (
        reason,
        refusal.evidence,
        candidates,
    )
    assert str(error) == (
        "Windows USB audio parents do not match the serial radio or "
        "cannot establish a unique same-host-API pair "
        f"({reason}); select explicit RX/TX devices."
    )
    assert audio_start_failure_reason(error) == category
    assert not backend.rx_streams
