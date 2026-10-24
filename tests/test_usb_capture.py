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

_RADIOS = [{"profile": "radio-a", "serial_port": "COM3"}]


@pytest.fixture
def readers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        platform, "node", Mock(side_effect=AssertionError("no host name"))
    )
    device = dict(
        name="USB Audio CODEC",
        hostapi=0,
        max_input_channels=2,
        max_output_channels=0,
        default_samplerate=48000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.02,
        default_high_input_latency=0.1,
        default_high_output_latency=0.2,
    )
    sound = SimpleNamespace(
        query_hostapis=Mock(
            return_value=[
                dict(
                    name="MME",
                    devices=[0],
                    default_input_device=0,
                    default_output_device=-1,
                )
            ]
        ),
        query_devices=Mock(return_value=[device]),
    )
    ports = Mock(
        return_value=[
            SimpleNamespace(
                device="COM3",
                name="COM3",
                description="Serial",
                hwid="USB SER=CAPTURE_SERIAL",
                vid=0x10C4,
                pid=0xEA60,
                serial_number="CAPTURE_SERIAL",
                location="1-2",
                manufacturer="Vendor",
                product="Serial",
                interface=None,
            )
        ]
    )
    raw = Mock(
        return_value=RawWindows(
            "cfgmgr32",
            (
                DevNodeRecord(
                    r"USB\VID_10C4&PID_EA60\CAPTURE_SERIAL",
                    "",
                    "Ports",
                    "Serial",
                    "Device",
                    (),
                    (),
                ),
            ),
        )
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sound)
    monkeypatch.setitem(
        sys.modules, "serial.tools.list_ports", SimpleNamespace(comports=ports)
    )
    monkeypatch.setattr(capture, "read_raw_windows", raw)
    return {"raw": raw, "ports": ports, "devices": sound.query_devices}


def test_write_session_records_metadata_fields_and_private_modes(
    tmp_path: Path, readers: dict
) -> None:
    stage = tmp_path / "stage"
    path = capture.write_session(stage, session="connected", radios=_RADIOS)
    assert path.name == "01-connected.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(stage.stat().st_mode) == 0o700
    session = json.loads(path.read_text(encoding="utf-8"))
    assert session["schema"] == "rigplane.usb_audio_session/1"
    assert session["session"] == "connected" and session["target"] is None
    assert session["radios"] == _RADIOS
    assert session["os"] == "windows"
    assert session["os_version"] == " ".join(
        [
            platform.system(),
            platform.release(),
            platform.version(),
            platform.machine(),
        ]
    )
    assert "ui_locale" in session
    assert session["capture_tool"] == f"rigplane {__version__} audio usb-capture"
    assert session["unavailable"] == []
    port = session["raw"]["serial_ports"][0]
    assert set(port) == {
        "device",
        "name",
        "description",
        "hwid",
        "vid",
        "pid",
        "serial_number",
        "location",
        "manufacturer",
        "product",
        "interface",
    }
    assert port["serial_number"] == "CAPTURE_SERIAL"
    assert (
        session["raw"]["portaudio"]["devices"][0]["default_high_output_latency"] == 0.2
    )
    assert all(reader.call_count == 1 for reader in readers.values())


@pytest.mark.parametrize("git_entry", ["file", "directory"])
def test_stage_inside_git_tree_is_refused(
    tmp_path: Path, readers: dict, git_entry: str
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    if git_entry == "file":
        (checkout / ".git").write_text("gitdir: private-git-dir", encoding="utf-8")
    else:
        (checkout / ".git").mkdir()
    with pytest.raises(ValueError, match="git work tree"):
        capture.write_session(checkout / "stage", session="connected", radios=_RADIOS)
    assert all(reader.call_count == 0 for reader in readers.values())


def test_finalize_one_bundle_validates_and_removes_stage(
    tmp_path: Path, readers: dict
) -> None:
    stage = tmp_path / "stage"
    capture.write_session(stage, session="connected", radios=_RADIOS)
    unplug = capture.write_session(stage, session="unplugged", target="radio-a")
    assert unplug.name == "02-unplugged-radio-a.json"
    capture.write_session(stage, session="replugged", target="radio-a")
    out = tmp_path / "capture.json"
    assert capture.finalize(stage, out) == out
    bundle = json.loads(out.read_text(encoding="utf-8"))
    assert bundle["schema"] == "rigplane.usb_audio_capture/1"
    assert [s["session"] for s in bundle["sessions"]] == [
        "connected",
        "unplugged",
        "replugged",
    ]
    assert (
        len({s["raw"]["serial_ports"][0]["serial_number"] for s in bundle["sessions"]})
        == 1
    )
    assert "CAPTURE_SERIAL" not in out.read_text(encoding="utf-8")
    assert not stage.exists()


@pytest.mark.parametrize("connected,target", [(False, "radio-a"), (True, "unknown")])
def test_finalize_invalid_bundle_keeps_stage(
    tmp_path: Path, readers: dict, connected: bool, target: str
) -> None:
    stage = tmp_path / "stage"
    if connected:
        capture.write_session(stage, session="connected", radios=_RADIOS)
    capture.write_session(stage, session="unplugged", target=target)
    out = tmp_path / "capture.json"
    with pytest.raises(ValueError):
        capture.finalize(stage, out)
    assert list(stage.glob("*.json")) and not out.exists()


def test_finalize_does_not_lose_output_or_unrelated_files(
    tmp_path: Path, readers: dict
) -> None:
    stage = tmp_path / "stage"
    capture.write_session(stage, session="connected", radios=_RADIOS)
    with pytest.raises(ValueError):
        capture.finalize(stage, stage / "capture.json")
    out = tmp_path / "capture.json"
    out.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        capture.finalize(stage, out)
    assert out.read_text(encoding="utf-8") == "existing"
    assert list(stage.glob("*.json"))
    extra = stage / "keep.txt"
    extra.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        capture.finalize(stage, tmp_path / "other.json")
    assert extra.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("preexisting_file", [None, "shared.txt"])
def test_first_session_refuses_a_preexisting_stage(
    tmp_path: Path, readers: dict, preexisting_file: str | None
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o755)
    if preexisting_file is not None:
        (stage / preexisting_file).write_text("keep", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in stage.iterdir()}
    mode = stage.stat().st_mode
    with pytest.raises(ValueError, match="stage directory already exists"):
        capture.write_session(stage, session="connected", radios=_RADIOS)
    assert {path.name: path.read_bytes() for path in stage.iterdir()} == before
    assert stage.stat().st_mode == mode
    assert all(reader.call_count == 0 for reader in readers.values())


@pytest.mark.parametrize("operation", ["write_session", "finalize"])
def test_existing_stage_lock_refuses_capture_without_changes(
    tmp_path: Path, readers: dict, operation: str
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o755)
    lock = stage / ".lock"
    lock.write_text("another process", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in stage.iterdir()}
    mode = stage.stat().st_mode
    out = tmp_path / "capture.json"
    with pytest.raises(
        ValueError, match="another usb-capture is using this stage directory"
    ):
        if operation == "write_session":
            capture.write_session(stage, session="connected", radios=_RADIOS)
        else:
            capture.finalize(stage, out)
    assert {path.name: path.read_bytes() for path in stage.iterdir()} == before
    assert stage.stat().st_mode == mode
    assert not out.exists()
    assert all(reader.call_count == 0 for reader in readers.values())


@pytest.mark.asyncio
async def test_cli_finalize_attempts_all_cleanup_and_reports_only_leftovers(
    tmp_path: Path,
    readers: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stage = tmp_path / "stage"
    paths = [
        capture.write_session(stage, session="connected", radios=_RADIOS),
        capture.write_session(stage, session="unplugged", target="radio-a"),
        capture.write_session(stage, session="replugged", target="radio-a"),
    ]
    leftover = paths[0]
    unlink = Path.unlink
    rmdir = Path.rmdir
    unlink_attempts = []
    rmdir_attempts = []

    def remove_file(path: Path, *args, **kwargs) -> None:
        unlink_attempts.append(path)
        if path == leftover:
            raise PermissionError("file is in use")
        unlink(path, *args, **kwargs)

    def remove_directory(path: Path) -> None:
        rmdir_attempts.append(path)
        rmdir(path)

    monkeypatch.setattr(Path, "unlink", remove_file)
    monkeypatch.setattr(Path, "rmdir", remove_directory)
    out = tmp_path / "capture.json"
    args = _build_parser().parse_args(
        [
            "audio",
            "usb-capture",
            "--stage-dir",
            str(stage),
            "--finalize",
            "--out",
            str(out),
        ]
    )
    assert await _run(args) == 1
    assert capsys.readouterr().err == (
        f"Error: bundle written; delete these files by hand: {leftover}\n"
    )
    assert unlink_attempts == [*paths, stage / ".lock"]
    assert rmdir_attempts == [stage]
    assert list(stage.iterdir()) == [leftover]
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == (
        "rigplane.usb_audio_capture/1"
    )


@pytest.mark.parametrize(
    "part,module",
    [("portaudio", "sounddevice"), ("serial_ports", "serial.tools.list_ports")],
)
def test_missing_dependency_becomes_unavailable(
    tmp_path: Path,
    readers: dict,
    monkeypatch: pytest.MonkeyPatch,
    part: str,
    module: str,
) -> None:
    monkeypatch.setitem(sys.modules, module, None)
    path = capture.write_session(
        tmp_path / "stage", session="connected", radios=_RADIOS
    )
    session = json.loads(path.read_text(encoding="utf-8"))
    assert part not in session["raw"]
    assert [entry["part"] for entry in session["unavailable"]] == [part]
    assert session["unavailable"][0]["reason"]


def test_windows_read_failure_does_not_discard_other_parts(
    tmp_path: Path, readers: dict
) -> None:
    readers["raw"].side_effect = OSError("both readers failed")
    path = capture.write_session(
        tmp_path / "stage", session="connected", radios=_RADIOS
    )
    session = json.loads(path.read_text(encoding="utf-8"))
    assert set(session["raw"]) == {"portaudio", "serial_ports"}
    assert [entry["part"] for entry in session["unavailable"]] == ["windows"]


@pytest.mark.parametrize(
    "options",
    [
        [
            "--session",
            "connected",
            "--radio",
            "radio-a=COM3",
            "--radio",
            "radio-b=COM4",
        ],
        ["--session", "unplugged", "--target", "radio-a"],
        ["--session", "replugged", "--target", "radio-a"],
        ["--finalize", "--out", "capture.json"],
    ],
)
def test_parser_accepts_documented_combinations(options: list[str]) -> None:
    args = _build_parser().parse_args(
        ["audio", "usb-capture", "--stage-dir", "stage", *options]
    )
    assert args.audio_command == "usb-capture"


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--session", "connected"],
        ["--session", "unplugged"],
        ["--session", "replugged"],
        ["--finalize"],
        ["--out", "capture.json"],
        ["--session", "connected", "--radio", "radio-a=COM3", "--target", "radio-a"],
        ["--session", "connected", "--radio", "invalid"],
        ["--session", "connected", "--radio", "=COM3"],
        ["--session", "connected", "--radio", "radio-a="],
        [
            "--session",
            "connected",
            "--radio",
            "radio-a=COM3",
            "--radio",
            "radio-a=COM4",
        ],
        ["--session", "unplugged", "--target", "radio-a", "--radio", "radio-a=COM3"],
        ["--session", "replugged", "--target", "radio-a", "--out", "capture.json"],
        ["--finalize", "--out", "capture.json", "--radio", "radio-a=COM3"],
        ["--finalize", "--out", "capture.json", "--target", "radio-a"],
        ["--finalize", "--out", "capture.json", "--session", "connected"],
    ],
)
def test_parser_rejects_invalid_combinations(options: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _build_parser().parse_args(
            ["audio", "usb-capture", "--stage-dir", "stage", *options]
        )
    assert exc.value.code == 2


def test_parser_requires_stage_dir() -> None:
    with pytest.raises(SystemExit) as exc:
        _build_parser().parse_args(
            ["audio", "usb-capture", "--finalize", "--out", "capture.json"]
        )
    assert exc.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("system", ["Darwin", "Linux"])
async def test_command_non_windows_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], system: str
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    args = _build_parser().parse_args(
        [
            "audio",
            "usb-capture",
            "--stage-dir",
            "stage",
            "--session",
            "connected",
            "--radio",
            "radio-a=COM3",
        ]
    )
    assert await _run(args) == 2
    assert "usb-capture currently records Windows only" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cli_stages_and_finalizes_without_radio(
    tmp_path: Path,
    readers: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serials = ["RADIO_A_SERIAL", "RADIO_B_SERIAL"]
    port = readers["ports"].return_value[0]
    ports = [
        SimpleNamespace(
            **{
                **vars(port),
                "device": f"COM{index + 3}",
                "name": f"COM{index + 3}",
                "serial_number": serial,
                "hwid": f"USB SER={serial}",
            }
        )
        for index, serial in enumerate(serials)
    ]
    radio_a = DevNodeRecord(
        rf"USB\VID_10C4&PID_EA60\{serials[0]}",
        "",
        "Ports",
        "Serial A",
        "Device A",
        (),
        (),
    )
    radio_b = DevNodeRecord(
        rf"USB\VID_1234&PID_5678\{serials[1]}",
        "",
        "USB",
        "Radio B",
        "Device B",
        (),
        (),
    )
    radio_b_child = DevNodeRecord(
        rf"USB\VID_1234&PID_5678&MI_00\{serials[1]}",
        radio_b.instance_id,
        "Ports",
        "Serial B",
        "Device B",
        (),
        (),
    )
    readers["raw"].side_effect = [
        RawWindows("cfgmgr32", nodes)
        for nodes in (
            (radio_a, radio_b, radio_b_child),
            (radio_b, radio_b_child),
            (radio_a, radio_b, radio_b_child),
        )
    ]
    readers["ports"].side_effect = [ports, [ports[1]], ports]
    stage = tmp_path / "stage"
    base = ["audio", "usb-capture", "--stage-dir", str(stage)]
    args = _build_parser().parse_args(
        [
            *base,
            "--session",
            "connected",
            "--radio",
            "radio-a=COM3",
            "--radio",
            "radio-b=COM4",
        ]
    )
    assert await _run(args) == 0
    output = capsys.readouterr().out
    assert (
        "01-connected.json" in output
        and "--session unplugged --target radio-a" in output
    )
    for session in ("unplugged", "replugged"):
        args = _build_parser().parse_args(
            [*base, "--session", session, "--target", "radio-a"]
        )
        assert await _run(args) == 0
    out = tmp_path / "capture.json"
    args = _build_parser().parse_args([*base, "--finalize", "--out", str(out)])
    assert await _run(args) == 0
    assert str(out) in capsys.readouterr().out
    text = out.read_text(encoding="utf-8")
    bundle = json.loads(text)
    assert bundle["schema"] == "rigplane.usb_audio_capture/1"
    sessions = bundle["sessions"]
    assert [(session["session"], session["target"]) for session in sessions] == [
        ("connected", None),
        ("unplugged", "radio-a"),
        ("replugged", "radio-a"),
    ]
    assert sessions[0]["radios"] == [
        {"profile": "radio-a", "serial_port": "COM3"},
        {"profile": "radio-b", "serial_port": "COM4"},
    ]
    assert all(session["radios"] == [] for session in sessions[1:])
    assert all(serial not in text for serial in serials)
    radio_b_ids = [
        tuple(
            node["instance_id"]
            for node in session["raw"]["windows"]["devnodes"]
            if node["instance_id"].startswith(r"USB\VID_1234&PID_5678")
        )
        for session in sessions
    ]
    assert len(radio_b_ids[0]) == 2
    assert radio_b_ids[0] == radio_b_ids[1] == radio_b_ids[2]
    assert not stage.exists()
