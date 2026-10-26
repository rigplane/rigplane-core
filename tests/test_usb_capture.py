import builtins
import json
from pathlib import Path
import platform
import stat
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rigplane import __version__
from rigplane.audio import _usb_capture as capture
from rigplane.audio._usb_raw_windows import RawWindows
from rigplane.audio._windows_devnode import DevNodeRecord
from rigplane.cli import _build_parser, _run

_RADIOS = [
    {"profile": "radio-a", "serial_port": "COM3"},
    {"profile": "radio-b", "serial_port": "COM4"},
]
_SERIALS = ["RADIO_A_SERIAL", "RADIO_B_SERIAL"]


def _args(out: Path, *options: str):
    return _build_parser().parse_args(
        [
            "audio",
            "usb-capture",
            "--radio",
            "radio-a=COM3",
            "--radio",
            "radio-b=COM4",
            "--out",
            str(out),
            *options,
        ]
    )


@pytest.fixture
def readers(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    events = []

    def event(name, result=None):
        def record(*args):
            events.append(name)
            return result

        return Mock(side_effect=record)

    nodes = tuple(
        DevNodeRecord(
            rf"USB\VID_10C4&PID_EA60\{serial}",
            "",
            "Ports",
            f"Radio:{serial}",
            f"Device:{serial}",
            (),
            (),
        )
        for serial in _SERIALS
    )
    ports = [
        SimpleNamespace(
            device=f"COM{index + 3}",
            name=f"COM{index + 3}",
            description=f"Radio:{serial}",
            hwid=f"USB SER={serial} LOCATION=1-2",
            vid=0x10C4,
            pid=0xEA60,
            serial_number=serial,
            location="1-2",
            manufacturer="Vendor",
            product="Serial",
            interface=None,
        )
        for index, serial in enumerate(_SERIALS)
    ]
    sound = SimpleNamespace(
        _terminate=event("terminate"),
        _initialize=event("initialize"),
        query_hostapis=event(
            "hostapis",
            [
                dict(
                    name="MME",
                    devices=[0],
                    default_input_device=0,
                    default_output_device=-1,
                )
            ],
        ),
        query_devices=event(
            "devices",
            [
                dict(
                    name=f"Radio:{_SERIALS[0]}",
                    hostapi=0,
                    max_input_channels=2,
                    max_output_channels=0,
                    default_samplerate=48000.0,
                    default_low_input_latency=0.01,
                    default_low_output_latency=0.02,
                    default_high_input_latency=0.1,
                    default_high_output_latency=0.2,
                )
            ],
        ),
    )
    raw = event("windows", RawWindows("cfgmgr32", nodes))
    comports = event("serial_ports", ports)
    prompt = event("prompt", "")
    monkeypatch.setitem(sys.modules, "sounddevice", sound)
    monkeypatch.setitem(
        sys.modules, "serial.tools.list_ports", SimpleNamespace(comports=comports)
    )
    monkeypatch.setattr(capture, "read_raw_windows", raw)
    monkeypatch.setattr(builtins, "input", prompt)
    return SimpleNamespace(
        raw=raw,
        comports=comports,
        sound=sound,
        prompt=prompt,
        events=events,
        nodes=nodes,
        ports=ports,
    )


def test_capture_bundle_records_supplied_tool_version(
    tmp_path: Path, readers: SimpleNamespace
) -> None:
    out = tmp_path / "capture.json"
    capture.capture_bundle(out, radios=_RADIOS, tool_version="9.9.9")
    sessions = json.loads(out.read_text(encoding="utf-8"))["sessions"]
    assert [session["capture_tool"] for session in sessions] == [
        "rigplane 9.9.9 audio usb-capture"
    ] * 5


@pytest.mark.parametrize(
    "serial",
    [
        "AUDIO",
        "connected",
        "unplugged",
        "replugged",
        "windows",
        "radio-a",
        "radio-b",
        "cfgmgr32",
        "powershell",
        "Ports",
        "AudioEndpoint",
    ],
)
def test_capture_serial_collision_preserves_discriminators_and_scrubs_data(
    tmp_path: Path, readers: SimpleNamespace, serial: str
) -> None:
    readers.ports[0].serial_number = serial
    readers.ports[0].description = f"Device ({serial.lower()}) / {serial.upper()}"
    readers.sound.query_hostapis.side_effect = RuntimeError(
        f"missing {serial.swapcase()}"
    )
    source = "powershell" if serial == "powershell" else "cfgmgr32"
    readers.raw.side_effect = None
    readers.raw.return_value = RawWindows(
        source,
        (
            DevNodeRecord(
                rf"USB\VID_10C4&PID_EA60\{serial.upper()}",
                "",
                "Ports",
                f"Device {serial.lower()}",
                f"Device {serial.upper()}",
                (),
                (),
            ),
            DevNodeRecord(
                r"SWD\MMDEVAPI\ENDPOINT",
                rf"USB\VID_10C4&PID_EA60\{serial.upper()}",
                "AudioEndpoint",
                f"Endpoint {serial.lower()}",
                f"Endpoint {serial.upper()}",
                (),
                (),
            ),
        ),
    )
    out = tmp_path / "capture.json"
    capture.capture_bundle(out, radios=_RADIOS, tool_version="test")
    bundle = json.loads(out.read_text(encoding="utf-8"))
    capture._validate_bundle(bundle)
    assert bundle["schema"] == "rigplane.usb_audio_capture/1"
    assert [(s["session"], s["target"]) for s in bundle["sessions"]] == [
        ("connected", None),
        ("unplugged", "radio-a"),
        ("replugged", "radio-a"),
        ("unplugged", "radio-b"),
        ("replugged", "radio-b"),
    ]
    for session in bundle["sessions"]:
        assert session["schema"] == "rigplane.usb_audio_session/1"
        assert session["os"] == "windows"
        assert session["radios"] == (
            _RADIOS if session["session"] == "connected" else []
        )
        raw = session["raw"]
        assert raw["windows"]["source"] == source
        assert [node["class_name"] for node in raw["windows"]["devnodes"]] == [
            "Ports",
            "AudioEndpoint",
        ]
        token = raw["serial_ports"][0]["serial_number"]
        assert raw["serial_ports"][0]["description"] == f"Device ({token}) / {token}"
        assert session["unavailable"] == [
            {"part": "portaudio", "reason": f"missing {token}"}
        ]
    pending = [bundle]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(
                child
                for field, child in value.items()
                if field
                not in {
                    "schema",
                    "session",
                    "target",
                    "os",
                    "profile",
                    "source",
                    "part",
                    "class_name",
                }
            )
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            assert serial.lower() not in value.lower(), value


@pytest.mark.parametrize("schema_field", ["bundle", "session"])
def test_capture_rejects_invalid_redacted_bundle_before_writing(
    tmp_path: Path,
    readers: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    schema_field: str,
) -> None:
    redact = capture.redact_bundle

    def corrupt_schema(bundle):
        redacted = redact(bundle)
        document = redacted if schema_field == "bundle" else redacted["sessions"][0]
        document["schema"] = "invalid"
        return redacted

    write = Mock()
    monkeypatch.setattr(capture, "redact_bundle", corrupt_schema)
    monkeypatch.setattr(capture, "_write_private_json", write)
    with pytest.raises(ValueError, match=f"invalid USB capture {schema_field} schema"):
        capture.capture_bundle(
            tmp_path / "capture.json",
            radios=_RADIOS,
            tool_version="test",
            no_unplug=True,
        )
    write.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_captured_session_omits_hwid_even_when_port_has_it(
    readers: SimpleNamespace,
) -> None:
    assert all(hasattr(port, "hwid") for port in readers.ports)
    session = capture._read_session(
        "connected", radios=_RADIOS, tool_version=__version__
    )
    ports = session["raw"]["serial_ports"]
    assert len(ports) == len(readers.ports)
    assert all("hwid" not in port for port in ports)


@pytest.mark.asyncio
async def test_cli_captures_two_radios_in_one_process(
    tmp_path: Path, readers: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = readers.nodes
    readers.raw.side_effect = [
        RawWindows("cfgmgr32", nodes) for nodes in ((a, b), (b,), (a, b), (a,), (a, b))
    ]
    readers.comports.side_effect = [
        readers.ports,
        [readers.ports[1]],
        readers.ports,
        [readers.ports[0]],
        readers.ports,
    ]
    out = tmp_path / "capture.json"
    assert await _run(_args(out)) == 0
    text = out.read_text(encoding="utf-8")
    bundle = json.loads(text)
    assert bundle["schema"] == "rigplane.usb_audio_capture/1"
    sessions = bundle["sessions"]
    assert [(s["session"], s["target"]) for s in sessions] == [
        ("connected", None),
        ("unplugged", "radio-a"),
        ("replugged", "radio-a"),
        ("unplugged", "radio-b"),
        ("replugged", "radio-b"),
    ]
    assert sessions[0]["radios"] == _RADIOS
    assert all(s["radios"] == [] for s in sessions[1:])
    for session in sessions:
        raw = session["raw"]
        for node in raw["windows"]["devnodes"]:
            assert all(serial not in node["instance_id"] for serial in _SERIALS)
        for port in raw["serial_ports"]:
            assert port["serial_number"] not in _SERIALS
            assert "hwid" not in port
            assert port["description"] == f"Radio:{port['serial_number']}"
        assert raw["portaudio"]["devices"][0]["name"].startswith("Radio:R")
        assert all(serial not in json.dumps(session) for serial in _SERIALS)
    b_ids = [s["raw"]["windows"]["devnodes"][-1]["instance_id"] for s in sessions[:3]]
    assert b_ids[0] == b_ids[1] == b_ids[2]
    assert readers.raw.call_count == readers.comports.call_count == 5
    assert (
        readers.sound._terminate.call_count == readers.sound._initialize.call_count == 4
    )
    audio_events = [
        event
        for event in readers.events
        if event in {"terminate", "initialize", "hostapis", "devices"}
    ]
    enumeration = audio_events[:2]
    assert set(enumeration) == {"hostapis", "devices"}
    assert audio_events == enumeration + ["terminate", "initialize", *enumeration] * 4
    assert [call.args[0] for call in readers.prompt.call_args_list] == [
        "Unplug the USB cable of radio-a (COM3), wait 5 seconds, then press Enter.",
        "Plug radio-a back in (any USB port), wait 10 seconds, then press Enter.",
        "Unplug the USB cable of radio-b (COM4), wait 5 seconds, then press Enter.",
        "Plug radio-b back in (any USB port), wait 10 seconds, then press Enter.",
    ]
    output = capsys.readouterr().out
    assert "names are kept" not in output
    assert str(out) in output and "5 sessions" in output and "2 radios" in output
    assert list(tmp_path.iterdir()) == [out]


@pytest.mark.parametrize("failure", ["serialization", "open", "write", "interrupt"])
def test_atomic_write_cleans_partial_temp_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    out = tmp_path / "capture.json"
    bundle = {"sessions": []}

    def fail_serialization(value, **kwargs):
        assert not out.exists()
        assert len(list(tmp_path.iterdir())) == 1
        raise ValueError("serialization failed")

    def fail_open(*args, **kwargs):
        assert not out.exists()
        assert len(list(tmp_path.iterdir())) == 1
        raise OSError("open failed")

    def fail_write(*args, **kwargs):
        stream = open(*args, **kwargs)

        class FailingStream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                stream.close()

            def write(self, data):
                stream.write(data[:1])
                raise OSError("write failed")

        return FailingStream()

    def interrupt_after_creation(descriptor):
        assert not out.exists()
        assert len(list(tmp_path.iterdir())) == 1
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        if failure == "serialization":
            patch.setattr(capture.json, "dumps", fail_serialization)
            error = ValueError
        elif failure == "open":
            patch.setattr(capture, "open", fail_open, raising=False)
            error = OSError
        elif failure == "write":
            patch.setattr(capture, "open", fail_write, raising=False)
            error = OSError
        else:
            patch.setattr(capture.os, "fsync", interrupt_after_creation)
            error = KeyboardInterrupt
        with pytest.raises(error):
            capture._write_private_json(out, bundle)
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
    capture._write_private_json(out, bundle)
    assert json.loads(out.read_text(encoding="utf-8")) == bundle
    assert list(tmp_path.iterdir()) == [out]


def test_atomic_write_cleans_temp_when_link_reports_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "capture.json"
    monkeypatch.setattr(capture.os, "link", Mock(side_effect=FileExistsError))
    with pytest.raises(FileExistsError) as exc:
        capture._write_private_json(out, {"sessions": []})
    assert str(exc.value) == f"output already exists: {out}"
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_refuses_output_created_at_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "capture.json"
    link = capture.os.link

    def create_competing_output(temporary, path):
        assert not out.exists()
        out.write_text("someone else's output", encoding="utf-8")
        link(temporary, path)

    monkeypatch.setattr(capture.os, "link", create_competing_output)
    with pytest.raises(FileExistsError) as exc:
        capture._write_private_json(out, {"sessions": []})
    assert str(exc.value) == f"output already exists: {out}"
    assert out.read_text(encoding="utf-8") == "someone else's output"
    assert list(tmp_path.iterdir()) == [out]


def test_atomic_write_refuses_existing_output_without_creating_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "capture.json"
    out.write_text("existing", encoding="utf-8")
    create = Mock(side_effect=AssertionError("must not create a temporary file"))
    monkeypatch.setattr(capture.tempfile, "mkstemp", create)
    with pytest.raises(FileExistsError) as exc:
        capture._write_private_json(out, {"sessions": []})
    assert str(exc.value) == f"output already exists: {out}"
    assert out.read_text(encoding="utf-8") == "existing"
    create.assert_not_called()
    assert list(tmp_path.iterdir()) == [out]


def test_atomic_write_refuses_unsupported_safe_write_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "capture.json"
    bundle = {"sessions": []}

    with monkeypatch.context() as patch:
        patch.setattr(capture.os, "link", Mock(side_effect=OSError("unsupported")))
        with pytest.raises(OSError) as exc:
            capture._write_private_json(out, bundle)
    assert str(exc.value) == (
        f"cannot write {tmp_path}: this folder's filesystem does not support the safe "
        "write used by usb-capture; choose a folder on an NTFS (Windows) or APFS "
        "(macOS) drive, e.g. your Documents folder"
    )
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
    capture._write_private_json(out, bundle)
    assert json.loads(out.read_text(encoding="utf-8")) == bundle
    assert list(tmp_path.iterdir()) == [out]


@pytest.mark.asyncio
async def test_cli_writes_scrubbed_document_without_obsolete_names_note(
    tmp_path: Path,
    readers: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write = capture._write_private_json

    def check_note(path, value):
        assert capsys.readouterr().out == ""
        assert all(serial not in json.dumps(value) for serial in _SERIALS)
        write(path, value)

    monkeypatch.setattr(capture, "_write_private_json", check_note)
    assert await _run(_args(tmp_path / "capture.json", "--no-unplug")) == 0


@pytest.mark.asyncio
async def test_cli_no_unplug_records_metadata_and_private_output(
    tmp_path: Path, readers: SimpleNamespace
) -> None:
    out = tmp_path / "capture.json"
    assert await _run(_args(out, "--no-unplug")) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    sessions = json.loads(out.read_text(encoding="utf-8"))["sessions"]
    assert len(sessions) == 1
    session = sessions[0]
    assert session["schema"] == "rigplane.usb_audio_session/1"
    assert session["session"] == "connected" and session["target"] is None
    assert session["radios"] == _RADIOS
    assert session["os"] == "windows"
    assert session["os_version"] == " ".join(
        [platform.system(), platform.release(), platform.version(), platform.machine()]
    )
    assert "ui_locale" in session
    assert session["capture_tool"] == f"rigplane {__version__} audio usb-capture"
    assert session["unavailable"] == []
    assert set(session["raw"]["serial_ports"][0]) == set(capture._SERIAL_FIELDS)
    assert (
        session["raw"]["portaudio"]["devices"][0]["default_high_output_latency"] == 0.2
    )
    assert readers.raw.call_count == readers.comports.call_count == 1
    readers.prompt.assert_not_called()
    readers.sound._terminate.assert_not_called()
    readers.sound._initialize.assert_not_called()


@pytest.mark.asyncio
async def test_cli_existing_output_refused_before_reading(
    tmp_path: Path, readers: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "capture.json"
    out.write_text("existing", encoding="utf-8")
    assert await _run(_args(out)) == 1
    assert out.read_text(encoding="utf-8") == "existing"
    assert readers.events == []
    assert f"output already exists: {out}" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("error,exit_code", [(EOFError, 1), (KeyboardInterrupt, 130)])
async def test_cli_interrupted_prompt_writes_nothing(
    tmp_path: Path,
    readers: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    error: type[BaseException],
    exit_code: int,
) -> None:
    readers.prompt.side_effect = error
    out = tmp_path / "capture.json"
    assert await _run(_args(out)) == exit_code
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
    assert readers.raw.call_count == 1
    assert "no output written" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cli_interrupted_descriptor_close_cleans_temp_and_allows_retry(
    tmp_path: Path, readers: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "capture.json"
    close = capture.os.close

    def interrupt_after_close(fd):
        close(fd)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(capture.os, "close", interrupt_after_close)
        assert await _run(_args(out, "--no-unplug")) == 130
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []

    assert await _run(_args(out, "--no-unplug")) == 0
    assert out.is_file()
    assert list(tmp_path.iterdir()) == [out]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["_terminate", "_initialize"])
async def test_cli_missing_portaudio_refresh_marks_later_sessions_unavailable(
    tmp_path: Path, readers: SimpleNamespace, missing: str
) -> None:
    delattr(readers.sound, missing)
    out = tmp_path / "capture.json"
    assert await _run(_args(out)) == 0
    sessions = json.loads(out.read_text(encoding="utf-8"))["sessions"]
    assert "portaudio" in sessions[0]["raw"]
    for session in sessions[1:]:
        assert "portaudio" not in session["raw"]
        assert session["unavailable"] == [
            {"part": "portaudio", "reason": "cannot re-initialize PortAudio"}
        ]
        assert set(session["raw"]) == {"windows", "serial_ports"}
    assert readers.sound.query_devices.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "part,module",
    [("portaudio", "sounddevice"), ("serial_ports", "serial.tools.list_ports")],
)
async def test_cli_missing_dependency_becomes_unavailable(
    tmp_path: Path,
    readers: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    part: str,
    module: str,
) -> None:
    monkeypatch.setitem(sys.modules, module, None)
    out = tmp_path / "capture.json"
    assert await _run(_args(out, "--no-unplug")) == 0
    session = json.loads(out.read_text(encoding="utf-8"))["sessions"][0]
    assert part not in session["raw"]
    assert [entry["part"] for entry in session["unavailable"]] == [part]
    assert session["unavailable"][0]["reason"]


@pytest.mark.asyncio
async def test_cli_windows_read_failure_preserves_other_parts(
    tmp_path: Path, readers: SimpleNamespace
) -> None:
    readers.raw.side_effect = OSError("both readers failed")
    out = tmp_path / "capture.json"
    assert await _run(_args(out, "--no-unplug")) == 0
    session = json.loads(out.read_text(encoding="utf-8"))["sessions"][0]
    assert set(session["raw"]) == {"portaudio", "serial_ports"}
    assert session["unavailable"] == [
        {"part": "windows", "reason": "both readers failed"}
    ]


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--out", "capture.json"],
        ["--radio", "radio-a=COM3"],
        ["--out", "capture.json", "--radio", "invalid"],
        ["--out", "capture.json", "--radio", "=COM3"],
        ["--out", "capture.json", "--radio", "radio-a="],
        ["--out", "capture.json", "--radio", "radio/a=COM3"],
        ["--out", "capture.json", "--radio", "radio-a=COM3", "--radio", "radio-a=COM4"],
    ],
)
def test_parser_rejects_invalid_combinations(options: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _build_parser().parse_args(["audio", "usb-capture", *options])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "session,target,radios",
    [
        ("unknown", None, []),
        ("connected", None, []),
        ("connected", "radio-a", _RADIOS),
        ("unplugged", None, []),
        ("replugged", "radio-a", _RADIOS),
    ],
)
def test_session_validation(session: str, target: str | None, radios: list) -> None:
    with pytest.raises(ValueError):
        capture._validate_session(session, target, radios)


@pytest.mark.parametrize(
    "sessions",
    [
        [],
        [
            {
                "schema": "wrong",
                "session": "connected",
                "target": None,
                "radios": _RADIOS,
            }
        ],
        [
            {
                "schema": "rigplane.usb_audio_session/1",
                "session": "connected",
                "radios": None,
            }
        ],
        [
            {
                "schema": "rigplane.usb_audio_session/1",
                "session": "unplugged",
                "target": "radio-a",
                "radios": [],
            }
        ],
        [
            {
                "schema": "rigplane.usb_audio_session/1",
                "session": "connected",
                "target": None,
                "radios": _RADIOS,
            },
            {
                "schema": "rigplane.usb_audio_session/1",
                "session": "unplugged",
                "target": "other",
                "radios": [],
            },
        ],
    ],
)
def test_bundle_validation(sessions: list) -> None:
    with pytest.raises(ValueError):
        capture._validate_bundle(
            {"schema": "rigplane.usb_audio_capture/1", "sessions": sessions}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("system", ["FreeBSD", "Unknown"])
async def test_command_non_windows_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    system: str,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    assert await _run(_args(tmp_path / "capture.json", "--no-unplug")) == 2
    assert "usb-capture supports Windows, macOS and Linux" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("system,part", [("Darwin", "macos"), ("Linux", "linux")])
async def test_unix_cli_capture_validates_and_redacts(
    tmp_path, readers, monkeypatch, system, part
):
    monkeypatch.setattr(platform, "system", lambda: system)
    serial = _SERIALS[0]
    document = {
        "usb_devices": [
            {
                "registry_id": 3,
                "serial_number": serial,
                "registry_path": "IOUSB:/Serial@03121300",
                "path": "/sys/devices/usb1/1-2",
            }
        ],
        "serial_devices": [
            {
                "device": f"/dev/cu.usbserial-{serial}0"
                if part == "macos"
                else "/dev/ttyUSB0",
                "usb_registry_id": 3,
                "registry_path": "IOService:/Serial@03121300/child",
                "usb_device": "/sys/devices/usb1/1-2",
            }
        ],
        "audio_devices": [
            {
                "uid": f"AppleUSBAudioEngine:Vendor:Codec:{serial}:2"
                if part == "macos"
                else "alsa:card0"
            }
        ],
        "unavailable": [{"part": f"{part}:optional", "reason": "missing"}],
    }
    reader = Mock(return_value=SimpleNamespace(to_json_dict=lambda: document))
    monkeypatch.setattr(capture, f"read_raw_{part}", reader, raising=False)
    readers.raw.side_effect = AssertionError("Windows reader must not run")
    out = tmp_path / "capture.json"
    assert await _run(_args(out)) == 0
    bundle = json.loads(out.read_text())
    capture._validate_bundle(bundle)
    assert len(bundle["sessions"]) == 5
    assert reader.call_count == 5
    for session in bundle["sessions"]:
        assert session["os"] == part
        assert "windows" not in session["raw"]
        assert session["unavailable"] == document["unavailable"]
        assert serial not in json.dumps(session["raw"][part])
        assert all("hwid" not in p for p in session["raw"]["serial_ports"])
        assert session["raw"]["portaudio"]["devices"]


def test_capture_help_names_all_supported_platforms():
    parser = _build_parser()
    audio = parser._subparsers._group_actions[0].choices["audio"]
    help_text = audio.format_help()
    assert all(name in help_text for name in ("Windows", "macOS", "Linux"))
