"""Browser audio start; Linux uses synthetic sysfs, not hardware-verified."""

import asyncio
import json
import sys
from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_usb_pairing_windows import (
    W3_ENDPOINT_RENAMES,
    _inject_windows,
    _w1_session,
    _w3_session,
    rename_w3_endpoints,
)
from test_usb_pairing_macos import (
    FTX_PORT,
    MAC_PORT,
    MAC_RX_ID,
    MAC_TX_ID,
    _inject_macos,
    _macos_session,
    _second_same_named_burr_brown,
    _second_same_named_c_media,
    _synthetic_macos,
)
from test_usb_pairing_linux import LINUX_ID, LINUX_NAME, LINUX_PORT, _inject_linux
from test_web_server import (
    _close_ws,
    _http_get,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)
from test_yaesu_cat_identity_gate import FakeCatTransport

from rigplane.audio import usb_driver
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.types import AudioCodec
from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId
from rigplane.profiles import get_radio_profile
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_RX,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT


W1_RX_ID = r"MME|SWD\MMDEVAPI\{0.0.1.00000000}.{963D478C-4D8C-412E-A32C-387169AFB3A0}"
W1_TX_ID = r"MME|SWD\MMDEVAPI\{0.0.0.00000000}.{9082644E-6A88-4638-B416-E1788AAC0FDF}"


@pytest.mark.parametrize("refusal", ["timeout", "unmatched-names"])
async def test_generated_picker_candidates_reach_audio_start_api(monkeypatch, refusal):
    session = _w3_padded_session()
    if refusal == "unmatched-names":
        for row in session["raw"]["windows"]["devnodes"]:
            if row["class_name"] == "AudioEndpoint":
                row["friendly_name"] = "Unmatched " + row["friendly_name"]
    backend, _ = _inject_windows(monkeypatch, session)
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    if refusal == "timeout":
        original_run = usb_driver.bounded_portaudio_pool.run_bounded

        async def timed_out(fn, *, what, direction, timeout, **kwargs):
            if what == "optional USB topology":
                assert timeout == 5.0
                raise usb_driver.AudioCaptureOpenTimeoutError("topology timed out")
            return await original_run(
                fn, what=what, direction=direction, timeout=timeout, **kwargs
            )

        monkeypatch.setattr(usb_driver.bounded_portaudio_pool, "run_bounded", timed_out)
    radio = Ic7300SerialRadio(
        device="COM3",
        profile=get_radio_profile("IC-7300"),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        civ_link=_FakeSerialCivLink(model_id=0x94),
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        messages = []
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                messages.append(json.loads(payload))
                if messages[-1]["type"] == "audio_format":
                    break
        assert any(message["type"] == "error" for message in messages)
        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        failure = json.loads(body)["audioReceive"]["failure"]
        assert failure["reason"] == (
            "usb_topology_unavailable" if refusal == "timeout" else "usb_topology_names"
        )
        candidates = failure["candidates"]
        expected = {(1, "capture"), (2, "capture"), (5, "render"), (6, "render")}
        assert {(c["portAudioIndex"], c["direction"]) for c in candidates} == expected
        devices = {d["index"]: d for d in session["raw"]["portaudio"]["devices"]}
        for candidate in candidates:
            assert (
                candidate["displayName"] == devices[candidate["portAudioIndex"]]["name"]
            )
            assert candidate["stableId"] == "MME|name:" + candidate["displayName"]
            assert candidate["hostApi"] == "MME"
            assert candidate["nearRadio"] is False
        assert radio.audio_session.rx_demand == 0
        assert not (backend.rx_streams or backend.tx_streams or backend.duplex_streams)
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()


@pytest.mark.parametrize(
    "os_name", ["windows", "macos", "macos-frozen", "linux", "linux-frozen"]
)
@pytest.mark.parametrize(
    "codec_matches,explicit_choice",
    [
        (True, False),
        (False, False),
        (False, True),
    ],
    ids=["w1-mme", "wrong-codec", "stable-choice-overrides-wrong-codec"],
)
async def test_w1_owner_browser_audio_start_replays_windows_usb_pairing(
    tmp_path, monkeypatch, codec_matches, explicit_choice, os_name
):
    """Real web/driver seam; Linux fixtures are not hardware-verified."""
    from rigplane.audio import _usb_pairing

    if os_name.startswith("linux"):
        if os_name == "linux-frozen":
            monkeypatch.setattr(sys, "frozen", True, raising=False)
            monkeypatch.setattr(sys, "executable", "frozen-sidecar-must-not-relaunch")
            monkeypatch.setattr(
                "subprocess.Popen",
                Mock(side_effect=AssertionError("Linux pairing must stay in process")),
            )
        backend, _, _, _ = _inject_linux(tmp_path, monkeypatch)
        rx_id = tx_id = LINUX_ID
        indices = (0, 0)
        radio_class, port, model, address = Ic705SerialRadio, LINUX_PORT, "IC-705", 0xA4
        expected_candidates = (
            (rx_id, 0, LINUX_NAME, "capture"),
            (tx_id, 0, LINUX_NAME, "render"),
        )
    elif os_name.startswith("macos"):
        frozen = os_name == "macos-frozen"
        if frozen:
            monkeypatch.setattr(sys, "frozen", True, raising=False)
            monkeypatch.setattr(sys, "executable", "frozen-sidecar-must-not-relaunch")
        backend, _ = _inject_macos(monkeypatch, _macos_session(), real_reader=frozen)
        rx_id, tx_id, indices = MAC_RX_ID, MAC_TX_ID, (2, 1)
        radio_class, port, model, address = Ic705SerialRadio, MAC_PORT, "IC-705", 0xA4
        expected_candidates = (
            (rx_id, 2, "USB Audio CODEC", "capture"),
            (tx_id, 1, "USB Audio CODEC", "render"),
        )
    else:
        backend, _ = _inject_windows(monkeypatch, _w1_session())
        rx_id, tx_id, indices = W1_RX_ID, W1_TX_ID, (1, 5)
        radio_class, port, model, address = Ic7300SerialRadio, "COM3", "IC-7300", 0x94
        expected_candidates = (
            (rx_id, 1, "Microphone (USB Audio CODEC )", "capture"),
            (tx_id, 5, "Speakers (USB Audio CODEC )", "render"),
        )
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    pairing = Mock(wraps=_usb_pairing.pair)
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    profile = get_radio_profile(model)
    if not codec_matches:
        profile = replace(
            profile,
            usb_identity=UsbAudioIdentity(frozenset({UsbFunctionId("0D8C", "0016")})),
        )
    radio = radio_class(
        device=port,
        profile=profile,
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        rx_device=rx_id if explicit_choice else None,
        tx_device=tx_id if explicit_choice else None,
        civ_link=_FakeSerialCivLink(model_id=address),
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        # Bind the real HTTP/WS dispatcher without the unrelated CAT poller.
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        messages = []
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                messages.append(message)
                if message["type"] == "audio_format":
                    break

        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        audio_receive = json.loads(body)["audioReceive"]
        if not codec_matches and not explicit_choice:
            assert audio_receive["schemaVersion"] == 1
            failure = audio_receive["failure"]
            assert {
                key: value for key, value in failure.items() if key != "candidates"
            } == {
                "stage": "rx_start",
                "code": "core_rx_start_failed",
                "reason": "usb_topology_unavailable",
            }
            candidates = failure["candidates"]
            for stable_id, index, name, direction in expected_candidates:
                assert {
                    "stableId": stable_id,
                    "displayName": name,
                    "direction": direction,
                    "hostApi": "ALSA"
                    if os_name.startswith("linux")
                    else "Core Audio"
                    if os_name.startswith("macos")
                    else "MME",
                    "portAudioIndex": index,
                    "nearRadio": True,
                } in candidates
            assert all(
                set(candidate)
                == {
                    "stableId",
                    "displayName",
                    "direction",
                    "hostApi",
                    "portAudioIndex",
                    "nearRadio",
                }
                for candidate in candidates
            )
            assert any(message["type"] == "error" for message in messages)
            assert radio.audio_session.rx_demand == 0
            assert not (
                backend.rx_streams or backend.tx_streams or backend.duplex_streams
            )
            return

        assert audio_receive == {"schemaVersion": 1, "failure": None}
        if explicit_choice:
            pairing.assert_not_called()
        assert not any(message["type"] == "error" for message in messages)
        assert radio.audio_session.rx_demand == 1
        driver = radio._serial_audio_driver
        assert (
            driver.selected_rx_device.index,
            driver.selected_tx_device.index,
        ) == indices
        assert len(backend.rx_streams) == 1
        assert backend.rx_streams[0].running
        pcm = b"\x01\x00" * 960
        backend.rx_streams[0].inject_frame(pcm)
        opcode, frame = await _ws_recv_frame(reader, timeout=5)
        assert opcode == WS_OP_BINARY
        assert frame[:2] == bytes([MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16])
        assert frame[AUDIO_HEADER_SIZE:] == pcm
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()


async def test_w1_serial_ambiguous_refusal_reaches_ws_client_as_category_only(
    tmp_path, monkeypatch
):
    """MOR-3823 seam test: a REAL S3 ``serial_ambiguous`` pairing refusal —
    the W1 recording plus a second UART on the same hub — travels pairing ->
    driver -> broadcaster to the WebSocket client as the category token only.

    The client frame must never carry the exception text (host-side
    instructions such as "select explicit RX/TX devices" or the
    ``serial_ambiguous`` stage name); the same category is what
    ``/api/v1/info`` publishes under ``audioReceive.failure.reason``.
    """
    session = _w1_session()
    hub = r"USB\VID_0451&PID_2046\6&3878EF1F&0&2"
    serial_row = next(
        row
        for row in session["raw"]["windows"]["devnodes"]
        if row["class_name"] == "Ports"
    )
    session["raw"]["windows"]["devnodes"].append(
        {
            **serial_row,
            "instance_id": r"USB\VID_10C4&PID_EA70\EXTRAUART",
            "parent_id": hub,
            "friendly_name": "Second UART (COM9)",
        }
    )
    backend, _ = _inject_windows(monkeypatch, session)
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    radio = Ic7300SerialRadio(
        device="COM3",
        profile=get_radio_profile("IC-7300"),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        civ_link=_FakeSerialCivLink(model_id=0x94),
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        errors = []
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                if message["type"] == "error":
                    # No part of the exception text may reach the wire.
                    assert b"serial_ambiguous" not in payload
                    assert b"USB audio parents" not in payload
                    assert b"select explicit RX/TX devices" not in payload
                    errors.append(message)
                if message["type"] == "audio_format":
                    break

        assert errors == [
            {
                "type": "error",
                "message": "audio_start: RX audio failed to start: usb_ambiguous",
                "reason": "usb_ambiguous",
            }
        ]
        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        failure = json.loads(body)["audioReceive"]["failure"]
        assert failure["reason"] == "usb_ambiguous"
        assert radio.audio_session.rx_demand == 0
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()


class _FakePortAudioError(Exception):
    """The shape sounddevice's PortAudioError has: (message, code[, host error])."""


def _fake_portaudio(session, *, exclusive_error, shared_error):
    """A fake ``sounddevice`` over the W1 enumeration whose WASAPI opens raise.

    It is the lowest seam: everything above it (PortAudioBackend, the capture
    policy, the driver, the radio, the web server and its WebSocket) is real.
    """
    from types import SimpleNamespace

    enumeration = session["raw"]["portaudio"]
    devices = enumeration["devices"]
    opened = []

    class WasapiSettings:
        def __init__(self, exclusive=False, auto_convert=False):
            self.exclusive = exclusive
            self.auto_convert = auto_convert

    class InputStream:
        def __init__(self, **kwargs):
            extra = kwargs.get("extra_settings")
            opened.append(extra.exclusive if extra is not None else None)
            error = exclusive_error if extra and extra.exclusive else shared_error
            if error is not None:
                raise error

        def start(self):
            raise AssertionError("an open that was refused must not start")

        def close(self):
            pass

    def hostapis():
        return [
            {
                "name": api["name"],
                "devices": [
                    d["index"] for d in devices if d["hostapi"] == api["index"]
                ],
                "default_input_device": api["default_input"],
                "default_output_device": api["default_output"],
            }
            for api in enumeration["hostapis"]
        ]

    sd = SimpleNamespace(
        default=SimpleNamespace(device=(19, 17)),
        WasapiSettings=WasapiSettings,
        InputStream=InputStream,
        query_devices=lambda index=None: devices if index is None else devices[index],
        query_hostapis=lambda index=None: (
            hostapis() if index is None else hostapis()[index]
        ),
        check_input_settings=lambda **kwargs: None,
        check_output_settings=lambda **kwargs: None,
    )
    return sd, opened


REAL_INVALID_DEVICE = "Error opening InputStream: Invalid device [PaErrorCode -9996]"
ACCESS_DENIED_ERROR = (
    "Error opening InputStream: Unanticipated host error [PaErrorCode -9999]"
)


@pytest.mark.parametrize(
    "shared_error,reason,lookup_result,holder",
    [
        (
            _FakePortAudioError(REAL_INVALID_DEVICE, -9996),
            "device_in_use",
            "python.exe",
            "python.exe",
        ),
        (
            _FakePortAudioError(REAL_INVALID_DEVICE, -9996),
            "device_in_use",
            r"C:\Users\someone\AppData\Local\Programs\Python\python.exe",
            "python.exe",
        ),
        (_FakePortAudioError(REAL_INVALID_DEVICE, -9996), "device_in_use", None, None),
        (
            _FakePortAudioError(
                ACCESS_DENIED_ERROR,
                -9999,
                ("Windows WASAPI", -2147024891, "Access is denied. (0x80070005)"),
            ),
            "other",
            "python.exe",
            None,
        ),
    ],
    ids=[
        "codec-held-by-a-named-program",
        "holder-path-reduced-to-the-file-name",
        "codec-held-by-an-unnamed-program",
        "access-denied-never-names-a-holder",
    ],
)
async def test_w1_exclusive_preferred_in_use_refusal_reaches_ws_client(
    tmp_path, monkeypatch, shared_error, reason, lookup_result, holder
):
    """MOR-4030 seam test: browser ``audio_start`` over the REAL ``/api/v1/audio``
    WebSocket against a W1 Windows pairing in ``exclusive-preferred`` mode.

    Only ``sounddevice`` is faked. The exclusive open and the shared fallback
    both raise the real run's ``Invalid device [PaErrorCode -9996]`` (the codec
    is held by another program), so the refusal is ``device_in_use``; a shared
    failure that says access denied keeps the old ``other`` category. A fake
    holder lookup (the only other fake) names the program: the frame and
    ``/api/v1/info`` carry ``holder`` (the bare file name) for ``device_in_use``
    only, and no ``holder`` key when the lookup finds nothing. The frame below is
    the exact frame the frontend routes to the in-use toast
    (``audio-manager-rx-refusal.isolated.test.ts``).
    """
    session = _w1_session()
    backend, _ = _inject_windows(monkeypatch, session)
    sd, opened = _fake_portaudio(
        session,
        exclusive_error=_FakePortAudioError(REAL_INVALID_DEVICE, -9996),
        shared_error=shared_error,
    )
    real_backend = usb_driver.PortAudioBackend
    lookups = []

    def make_backend(**kwargs):
        made = real_backend(dependency_loader=lambda: (sd, object()), **kwargs)
        made._capture_policy.holder_lookup = lambda name: (
            lookups.append(name) or lookup_result
        )
        return made

    monkeypatch.setattr(usb_driver, "PortAudioBackend", make_backend)
    radio = Ic7300SerialRadio(
        device="COM3",
        profile=get_radio_profile("IC-7300"),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        civ_link=_FakeSerialCivLink(model_id=0x94),
        _enumerate_serial_ports_fn=lambda: [],
        capture_mode="exclusive-preferred",
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        errors = []
        async with asyncio.timeout(5):
            while not errors:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                assert message["type"] != "audio_format", "the refused RX started"
                if message["type"] == "error":
                    errors.append(message)

        assert errors == [
            {
                "type": "error",
                "message": f"audio_start: RX audio failed to start: {reason}",
                "reason": reason,
                **({"holder": holder} if holder else {}),
            }
        ]
        # The lookup is asked, with the capture endpoint's name, only for the
        # in-use refusal.
        assert lookups == (
            ["Microphone (USB Audio CODEC )"] if reason == "device_in_use" else []
        )
        # WASAPI exclusive first, then shared WASAPI with auto-convert.
        assert opened == [True, False]
        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        failure = json.loads(body)["audioReceive"]["failure"]
        assert failure == {
            "stage": "rx_start",
            "code": "core_rx_start_failed",
            "reason": reason,
            **({"holder": holder} if holder else {}),
        }
        assert radio.audio_session.rx_demand == 0
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()


def _w3_padded_session():
    """W3 with PortAudio list positions matching its indices.

    The runtime enumeration path (``read_portaudio_enumeration``) numbers
    devices by list position, so the four-entry W3 fixture (indices 1, 2, 5,
    6) is padded here with silent entries until position ``i`` carries index
    ``i`` — the same shape the W1 recording already has.
    """
    session = _w3_session()
    by_index = {
        device["index"]: dict(device)
        for device in session["raw"]["portaudio"]["devices"]
    }
    devices = []
    for index in range(7):
        if index in by_index:
            devices.append({**by_index[index], "index": index})
        else:
            devices.append(
                {
                    "index": index,
                    "name": f"Unrelated device {index}",
                    "hostapi": 0,
                    "max_input_channels": 0,
                    "max_output_channels": 0,
                    "default_samplerate": 44100.0,
                    "default_low_input_latency": 0.09,
                    "default_low_output_latency": 0.09,
                    "default_high_input_latency": 0.18,
                    "default_high_output_latency": 0.18,
                }
            )
    session["raw"]["portaudio"]["devices"] = devices
    return session


@pytest.mark.parametrize("variant", W3_ENDPOINT_RENAMES)
async def test_w3_dual_cp210x_browser_audio_start_selects_ic7610_codec(
    monkeypatch, variant
):
    """MOR-3885 seam test: the dual-CP210x topology plus the real IC-7610
    serial backend starts RX through the product ``audio_start`` path.

    Profile loading, pairing, driver selection, and web handling stay real.
    With the old S3 implementation (one serial function per VID:PID) the
    second CP210x refuses pairing and this test fails at ``audio_format``.
    The renames cover the Windows "2-" duplicate prefix and user-chosen
    non-English names on the IC-7610 endpoints: selection follows topology,
    not endpoint names (MOR-3710).
    """
    from rigplane import IC_7610_ADDR

    session = rename_w3_endpoints(_w3_padded_session(), W3_ENDPOINT_RENAMES[variant])
    backend, _ = _inject_windows(monkeypatch, session)
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    radio = Icom7610SerialRadio(
        device="COM5",
        profile=get_radio_profile("IC-7610"),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        civ_link=_FakeSerialCivLink(model_id=IC_7610_ADDR),
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        # Bind the real HTTP/WS dispatcher without the unrelated CAT poller.
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        messages = []
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                messages.append(message)
                if message["type"] == "audio_format":
                    break

        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        assert json.loads(body)["audioReceive"] == {"schemaVersion": 1, "failure": None}
        assert not any(message["type"] == "error" for message in messages)
        assert radio.audio_session.rx_demand == 1
        driver = radio._serial_audio_driver
        assert (
            driver.selected_rx_device.index,
            driver.selected_tx_device.index,
        ) == (1, 5)
        assert len(backend.rx_streams) == 1
        assert backend.rx_streams[0].running
        pcm = b"\x01\x00" * 960
        backend.rx_streams[0].inject_frame(pcm)
        opcode, frame = await _ws_recv_frame(reader, timeout=5)
        assert opcode == WS_OP_BINARY
        assert frame[:2] == bytes([MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16])
        assert frame[AUDIO_HEADER_SIZE:] == pcm
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()


@pytest.mark.parametrize(
    "shape,port,indices",
    [
        ("split", MAC_PORT, (2, 1)),
        ("split", "/dev/cu.other", (18, 17)),
        ("duplex", FTX_PORT, (3, 3)),
        ("duplex", "/dev/cu.other", (17, 17)),
    ],
)
async def test_macos_same_named_codec_pair_browser_audio_start_uses_own_records(
    monkeypatch, shape, port, indices
):
    """MOR-4356 product seam: browser ``audio_start`` over the REAL
    ``/api/v1/audio`` WebSocket against two macOS radios whose CODECs share
    one CoreAudio name — the split Burr-Brown "USB Audio CODEC" pair
    (IC-705 + IC-7300 shape) and the duplex C-Media "USB Audio Device" pair
    (G7250 + FTX-1 shape).

    Only hardware is faked: topology, PortAudio and the audio backend come
    from the recorded macOS session and the existing helpers; the web
    server, pairing, driver selection and the radio stay real (the duplex
    radio is the real YaesuCatRadio over a scripted CAT transport). With
    the pre-binding name-only record matching each pair refused as
    ``contested`` and these cases fail waiting for ``audio_format``.
    """
    if shape == "split":
        session = _synthetic_macos()
        _second_same_named_burr_brown(session)
    else:
        session = _macos_session()
        _second_same_named_c_media(session)
    backend, _ = _inject_macos(monkeypatch, session)
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    if shape == "split":
        radio = Ic705SerialRadio(
            device=port,
            profile=get_radio_profile("IC-705"),
            audio_codec=AudioCodec.PCM_1CH_16BIT,
            civ_link=_FakeSerialCivLink(model_id=0xA4),
            _enumerate_serial_ports_fn=lambda: [],
        )
    else:
        radio = YaesuCatRadio(device=port)
        radio._transport = FakeCatTransport(["ID0840"])
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        # Bind the real HTTP/WS dispatcher without the unrelated CAT poller.
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        messages = []
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                messages.append(message)
                if message["type"] == "audio_format":
                    break

        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        assert json.loads(body)["audioReceive"] == {"schemaVersion": 1, "failure": None}
        assert not any(message["type"] == "error" for message in messages)
        assert radio.audio_session.rx_demand == 1
        driver = radio._serial_audio_driver if shape == "split" else radio._audio_driver
        assert (
            driver.selected_rx_device.index,
            driver.selected_tx_device.index,
        ) == indices
        assert len(backend.rx_streams) == 1
        assert backend.rx_streams[0].running
        pcm = b"\x01\x00" * 960
        backend.rx_streams[0].inject_frame(pcm)
        opcode, frame = await _ws_recv_frame(reader, timeout=5)
        assert opcode == WS_OP_BINARY
        assert frame[:2] == bytes([MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16])
        assert frame[AUDIO_HEADER_SIZE:] == pcm
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()
