"""Stage Windows USB sessions and finalize a redacted capture bundle."""

from __future__ import annotations

from importlib import import_module
import json
import locale
import os
from pathlib import Path
import platform
import re
from typing import Any

from rigplane import __version__
from rigplane.audio._portaudio_enum import read_portaudio_enumeration
from rigplane.audio._usb_raw_windows import read_raw_windows
from rigplane.audio._usb_redact import redact_bundle

_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_SESSION_FILE = re.compile(
    r"([0-9]{2,})-(connected|unplugged|replugged)"
    r"(?:-([A-Za-z0-9][A-Za-z0-9_.-]*))?\.json"
)
_SERIAL_FIELDS = (
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
)


def _validate_session(
    session: str, target: str | None, radios: list[dict[str, str]]
) -> None:
    if session == "connected":
        if target is not None or not radios:
            raise ValueError("connected requires --radio and forbids --target")
    elif session in {"unplugged", "replugged"}:
        if not isinstance(target, str) or not _PROFILE.fullmatch(target) or radios:
            raise ValueError("unplugged/replugged require --target and forbid --radio")
    else:
        raise ValueError("unknown capture session")
    profiles: set[str] = set()
    for radio in radios:
        if not isinstance(radio, dict):
            raise ValueError("each radio must name a profile and serial port")
        profile = radio.get("profile")
        port = radio.get("serial_port")
        if (
            not isinstance(profile, str)
            or not _PROFILE.fullmatch(profile)
            or not isinstance(port, str)
            or not port.strip()
        ):
            raise ValueError("--radio requires PROFILE=PORT")
        if profile in profiles:
            raise ValueError("each radio profile must be unique")
        profiles.add(profile)


def _stage_path(stage_dir: Path | str) -> Path:
    path = Path(stage_dir)
    if path.is_symlink():
        raise ValueError("stage directory must not be a symbolic link")
    for candidate in (path.absolute(), path.resolve()):
        for parent in (candidate, *candidate.parents):
            git = parent / ".git"
            if git.exists() or git.is_symlink():
                raise ValueError("stage directory must be outside a git work tree")
    return path.resolve()


def _session_paths(stage: Path) -> list[Path]:
    paths = [path for path in stage.iterdir() if path.name != ".lock"]
    if any(
        path.is_symlink()
        or not path.is_file()
        or not _SESSION_FILE.fullmatch(path.name)
        for path in paths
    ):
        raise ValueError("stage directory contains files other than capture sessions")
    return sorted(paths, key=lambda path: int(path.name.split("-", 1)[0]))


def _take_stage_lock(stage: Path) -> Path:
    lock = stage / ".lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("another usb-capture is using this stage directory") from None
    os.close(descriptor)
    return lock


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def write_session(
    stage_dir: Path | str,
    *,
    session: str,
    target: str | None = None,
    radios: list[dict[str, str]] | None = None,
    timeout_s: float = 5.0,
) -> Path:
    radios = radios if radios is not None else []
    _validate_session(session, target, radios)
    stage = _stage_path(stage_dir)
    if platform.system() != "Windows":
        raise OSError("usb-capture currently records Windows only")
    try:
        stage.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        if not any(
            path.name == ".lock"
            or (
                _SESSION_FILE.fullmatch(path.name)
                and path.is_file()
                and not path.is_symlink()
            )
            for path in stage.iterdir()
        ):
            raise ValueError(
                "stage directory already exists without capture sessions"
            ) from None
    lock = _take_stage_lock(stage)
    try:
        stage.chmod(0o700)
        paths = _session_paths(stage)
        sequence = (
            max((int(path.name.split("-", 1)[0]) for path in paths), default=0) + 1
        )
        raw: dict[str, Any] = {}
        unavailable: list[dict[str, str]] = []
        for part in ("windows", "portaudio", "serial_ports"):
            try:
                if part == "windows":
                    raw[part] = read_raw_windows(timeout_s).to_json_dict()
                elif part == "portaudio":
                    raw[part] = read_portaudio_enumeration(
                        import_module("sounddevice")
                    ).to_json_dict()
                else:
                    raw[part] = [
                        {field: getattr(port, field, None) for field in _SERIAL_FIELDS}
                        for port in import_module("serial.tools.list_ports").comports()
                    ]
            except Exception as exc:
                unavailable.append(
                    {"part": part, "reason": str(exc) or type(exc).__name__}
                )
        document = {
            "schema": "rigplane.usb_audio_session/1",
            "session": session,
            "target": target,
            "radios": radios,
            "os": "windows",
            "os_version": " ".join(
                [
                    platform.system(),
                    platform.release(),
                    platform.version(),
                    platform.machine(),
                ]
            ),
            "ui_locale": locale.getlocale()[0],
            "capture_tool": f"rigplane {__version__} audio usb-capture",
            "raw": raw,
            "unavailable": unavailable,
        }
        suffix = f"-{target}" if target is not None else ""
        path = stage / f"{sequence:02d}-{session}{suffix}.json"
        _write_private_json(path, document)
        return path
    finally:
        lock.unlink()


def finalize(stage_dir: Path | str, out_path: Path | str) -> Path:
    stage = _stage_path(stage_dir)
    lock = _take_stage_lock(stage)
    written = False
    paths: list[Path] = []
    try:
        out = Path(out_path)
        if out.resolve().is_relative_to(stage):
            raise ValueError("output must be outside the stage directory")
        if out.exists() or out.is_symlink():
            raise FileExistsError("capture output already exists")
        paths = _session_paths(stage)
        sessions = []
        connected_profiles: set[str] = set()
        connected = False
        for path in paths:
            document = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(document, dict)
                or document.get("schema") != "rigplane.usb_audio_session/1"
            ):
                raise ValueError("invalid USB capture session schema")
            session = document.get("session")
            target = document.get("target")
            radios = document.get("radios")
            if not isinstance(session, str) or not isinstance(radios, list):
                raise ValueError("invalid USB capture session metadata")
            _validate_session(session, target, radios)
            if session == "connected":
                connected = True
                connected_profiles.update(radio["profile"] for radio in radios)
            sessions.append(document)
        if not connected:
            raise ValueError("capture requires at least one connected session")
        if any(
            document["session"] != "connected"
            and document["target"] not in connected_profiles
            for document in sessions
        ):
            raise ValueError("capture target is absent from connected radios")
        bundle = redact_bundle(
            {"schema": "rigplane.usb_audio_capture/1", "sessions": sessions}
        )
        _write_private_json(out, bundle)
        written = True
    finally:
        if written:
            for path in [*paths, lock]:
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                stage.rmdir()
            except OSError:
                pass
            if stage.exists():
                leftovers = sorted(stage.iterdir()) or [stage]
                raise OSError(
                    "bundle written; delete these files by hand: "
                    + ", ".join(str(path) for path in leftovers)
                )
        else:
            lock.unlink()
    return out
