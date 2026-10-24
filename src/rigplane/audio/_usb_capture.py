"""Collect Windows USB sessions in memory and write one redacted bundle."""

from __future__ import annotations

from collections.abc import Callable
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
            raise ValueError("connected requires radios and forbids a target")
    elif session in {"unplugged", "replugged"}:
        if not isinstance(target, str) or not _PROFILE.fullmatch(target) or radios:
            raise ValueError("unplugged/replugged require a target and forbid radios")
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


def _validate_bundle(bundle: dict[str, Any]) -> None:
    if bundle.get("schema") != "rigplane.usb_audio_capture/1" or not isinstance(
        bundle.get("sessions"), list
    ):
        raise ValueError("invalid USB capture bundle schema")
    connected_profiles: set[str] = set()
    for document in bundle["sessions"]:
        if (
            not isinstance(document, dict)
            or document.get("schema") != "rigplane.usb_audio_session/1"
        ):
            raise ValueError("invalid USB capture session schema")
        session = document.get("session")
        radios = document.get("radios")
        if not isinstance(session, str) or not isinstance(radios, list):
            raise ValueError("invalid USB capture session metadata")
        _validate_session(session, document.get("target"), radios)
        if session == "connected":
            connected_profiles.update(radio["profile"] for radio in radios)
    if not connected_profiles:
        raise ValueError("capture requires at least one connected session")
    if any(
        document["session"] != "connected"
        and document["target"] not in connected_profiles
        for document in bundle["sessions"]
    ):
        raise ValueError("capture target is absent from connected radios")


def _read_session(
    session: str,
    *,
    target: str | None = None,
    radios: list[dict[str, str]] | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    radios = radios if radios is not None else []
    _validate_session(session, target, radios)
    raw: dict[str, Any] = {}
    unavailable: list[dict[str, str]] = []
    for part in ("windows", "portaudio", "serial_ports"):
        try:
            if part == "windows":
                raw[part] = read_raw_windows(timeout_s).to_json_dict()
            elif part == "portaudio":
                sound = import_module("sounddevice")
                if session != "connected":
                    terminate = getattr(sound, "_terminate", None)
                    initialize = getattr(sound, "_initialize", None)
                    if not callable(terminate) or not callable(initialize):
                        raise RuntimeError("cannot re-initialize PortAudio")
                    terminate()
                    initialize()
                raw[part] = read_portaudio_enumeration(sound).to_json_dict()
            else:
                raw[part] = [
                    {field: getattr(port, field, None) for field in _SERIAL_FIELDS}
                    for port in import_module("serial.tools.list_ports").comports()
                ]
        except Exception as exc:
            unavailable.append({"part": part, "reason": str(exc) or type(exc).__name__})
    return {
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


def capture_bundle(
    out_path: Path | str,
    *,
    radios: list[dict[str, str]],
    no_unplug: bool = False,
    input_fn: Callable[[str], str] | None = None,
    timeout_s: float = 5.0,
) -> Path:
    out = Path(out_path)
    if out.exists() or out.is_symlink():
        raise FileExistsError("capture output already exists")
    _validate_session("connected", None, radios)
    if platform.system() != "Windows":
        raise OSError("usb-capture currently records Windows only")
    prompt = input if input_fn is None else input_fn
    sessions = [_read_session("connected", radios=radios, timeout_s=timeout_s)]
    if not no_unplug:
        for radio in radios:
            profile, port = radio["profile"], radio["serial_port"]
            prompt(
                f"Unplug the USB cable of {profile} ({port}), wait 5 seconds, then press Enter."
            )
            sessions.append(
                _read_session("unplugged", target=profile, timeout_s=timeout_s)
            )
            prompt(
                f"Plug {profile} back in (any USB port), wait 10 seconds, then press Enter."
            )
            sessions.append(
                _read_session("replugged", target=profile, timeout_s=timeout_s)
            )
    bundle = {"schema": "rigplane.usb_audio_capture/1", "sessions": sessions}
    _validate_bundle(bundle)
    _write_private_json(out, redact_bundle(bundle))
    return out
