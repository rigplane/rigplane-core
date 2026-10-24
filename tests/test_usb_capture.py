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
            assert port["hwid"] == f"USB SER={port['serial_number']} LOCATION=1-2"
            assert port["description"] in {f"Radio:{serial}" for serial in _SERIALS}
        assert raw["portaudio"]["devices"][0]["name"] == f"Radio:{_SERIALS[0]}"
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
    assert output.splitlines()[0] == (
        "Note: device and audio endpoint names are kept as recorded; review the file before sending it."
    )
    assert str(out) in output and "5 sessions" in output and "2 radios" in output
    assert list(tmp_path.iterdir()) == [out]


@pytest.mark.parametrize(
    "failure", ["serialization", "creation_interrupt", "interrupt"]
)
def test_atomic_write_cleans_partial_temp_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    out = tmp_path / "capture.json"
    bundle = {"sessions": []}

    def fail_serialization(value, **kwargs):
        assert not out.exists()
        assert len(list(tmp_path.iterdir())) == 1
        raise ValueError("serialization failed")

    def interrupt_before_fdopen(descriptor, *args, **kwargs):
        assert not out.exists()
        assert len(list(tmp_path.iterdir())) == 1
        raise KeyboardInterrupt

    def interrupt_after_creation(descriptor):
        assert not out.exists()
        assert len(list(tmp_path.iterdir())) == 1
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        if failure == "serialization":
            patch.setattr(capture.json, "dumps", fail_serialization)
            error = ValueError
        elif failure == "creation_interrupt":
            patch.setattr(capture.os, "fdopen", interrupt_before_fdopen)
            error = KeyboardInterrupt
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


@pytest.mark.parametrize("link_supported", [True, False])
def test_atomic_write_refuses_output_created_at_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_supported: bool
) -> None:
    out = tmp_path / "capture.json"
    link = capture.os.link

    def create_competing_output(temporary, path):
        assert not out.exists()
        out.write_text("someone else's output", encoding="utf-8")
        if not link_supported:
            raise OSError("hard links unsupported")
        link(temporary, path)

    monkeypatch.setattr(capture.os, "link", create_competing_output)
    with pytest.raises(FileExistsError, match="already exists"):
        capture._write_private_json(out, {"sessions": []})
    assert out.read_text(encoding="utf-8") == "someone else's output"
    assert list(tmp_path.iterdir()) == [out]


def test_atomic_write_falls_back_to_private_exclusive_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "capture.json"
    bundle = {"label": "Радио"}
    monkeypatch.setattr(capture.os, "link", Mock(side_effect=OSError("unsupported")))
    capture._write_private_json(out, bundle)
    assert json.loads(out.read_text(encoding="utf-8")) == bundle
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [out]


def test_atomic_write_cleans_interrupted_fallback_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "capture.json"
    bundle = {"sessions": []}

    def interrupt_copy(source, destination):
        destination.write(b'{"partial":')
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(capture.os, "link", Mock(side_effect=OSError("unsupported")))
        patch.setattr(capture.shutil, "copyfileobj", interrupt_copy)
        with pytest.raises(KeyboardInterrupt):
            capture._write_private_json(out, bundle)
    assert list(tmp_path.iterdir()) == []
    capture._write_private_json(out, bundle)
    assert json.loads(out.read_text(encoding="utf-8")) == bundle
    assert list(tmp_path.iterdir()) == [out]


@pytest.mark.asyncio
async def test_cli_prints_names_note_before_writing(
    tmp_path: Path,
    readers: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write = capture._write_private_json

    def check_note(path, value):
        assert capsys.readouterr().out == (
            "Note: device and audio endpoint names are kept as recorded; review the file before sending it.\n"
        )
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
    assert "already exists" in capsys.readouterr().err


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
@pytest.mark.parametrize("system", ["Darwin", "Linux"])
async def test_command_non_windows_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    system: str,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    assert await _run(_args(tmp_path / "capture.json", "--no-unplug")) == 2
    assert "usb-capture currently records Windows only" in capsys.readouterr().err
