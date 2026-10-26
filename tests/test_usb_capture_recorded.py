import json
from pathlib import Path
import re
from typing import Any

import pytest

from rigplane.audio._usb_capture import _validate_bundle
from rigplane.audio._usb_redact import _USB_SERIAL_ID, redact_bundle

_FIXTURES = sorted((Path(__file__).parent / "fixtures/usb_audio").rglob("*.json"))
_TOKEN = re.compile(r"R[A-Z2-7]{10}")


def _assert_recording_redacted(
    value: Any, locations: set[int | str], serials: tuple[str, ...] = ()
) -> int:
    count = 0
    if isinstance(value, list):
        return sum(
            _assert_recording_redacted(child, locations, serials) for child in value
        )
    if isinstance(value, str):
        for serial in serials:
            assert serial.lower().replace(" ", "_") not in value.lower().replace(
                " ", "_"
            ), value
        for match in re.finditer(
            r"(?:/dev/)?(?:cu|tty)\.(usbserial-|usbmodem)([^\s'\"(),;:/\\]+)", value
        ):
            suffix = match[2].rstrip(".")
            if not re.fullmatch(r"R[A-Z2-7]{10}\d?", suffix):
                assert match[1] == "usbmodem", value
                assert re.fullmatch(r"[0-9a-fA-F]+", suffix), value
                assert int(suffix, 16) & ~0xF in locations, value
            count += 1
        for match in re.finditer(
            r"USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\([^\s\\&'\"(),;:]+)",
            value,
            re.IGNORECASE | re.ASCII,
        ):
            assert _TOKEN.fullmatch(match[1].rstrip(".")), value
            count += 1
        return count
    if not isinstance(value, dict):
        return count
    assert "hwid" not in value
    for field, child in value.items():
        if field == "serial_number" and child:
            assert _TOKEN.fullmatch(child), (field, child)
            count += 1
        elif field in {"instance_id", "parent_id"} and isinstance(child, str):
            if match := _USB_SERIAL_ID.fullmatch(child):
                assert _TOKEN.fullmatch(match[1]), (field, child)
                count += 1
        elif field in {"device", "name", "serial_port"} and isinstance(child, str):
            match = re.fullmatch(
                r"(?:/dev/)?(?:cu|tty)\.(usbserial-|usbmodem)(.+)", child
            )
            if match:
                suffix = match[2]
                if not re.fullmatch(r"R[A-Z2-7]{10}\d?", suffix):
                    assert match[1] == "usbmodem", child
                    assert re.fullmatch(r"[0-9a-fA-F]+", suffix), child
                    assert int(suffix, 16) & ~0xF in locations, child
                count += 1
            elif child.startswith("/dev/serial/by-id/"):
                assert re.search(r"_R[A-Z2-7]{10}(?:-if\d+.*)?$", child), child
                count += 1
        elif field == "uid" and isinstance(child, str):
            if child.startswith("AppleUSBAudioEngine:"):
                identity = child.rsplit(":", 2)[1]
                if not _TOKEN.fullmatch(identity):
                    assert identity in locations or int(identity, 16) in locations, (
                        child
                    )
                count += 1
        count += _assert_recording_redacted(child, locations, serials)
    return count


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda path: path.name)
def test_recorded_capture_valid_and_redacted(fixture: Path) -> None:
    bundle = json.loads(fixture.read_text(encoding="utf-8"))
    assert _validate_bundle(bundle) is None
    locations = set()
    for session in bundle["sessions"]:
        for part in ("macos", "linux"):
            for row in session["raw"].get(part, {}).get("usb_devices", []):
                for field in ("location_id", "devpath"):
                    if (location := row.get(field)) is not None:
                        locations.add(location)
    assert _assert_recording_redacted(bundle, locations) > 0
    serial = "PRIVATE_RECORDING_SERIAL"
    raw = bundle["sessions"][0]["raw"]
    raw.setdefault("serial_ports", []).append(
        {
            "serial_number": serial,
            "description": rf"Device USB\VID_1234&PID_5678\{serial}",
            "product": f"Product ({serial})",
            "manufacturer": f"Vendor ({serial})",
        }
    )
    bundle["sessions"][0].setdefault("unavailable", []).append(
        {"part": "optional", "reason": f"cannot read /dev/cu.usbserial-{serial}0"}
    )
    bundle["diagnostics"] = [serial.lower(), {"nested": [f"Diagnostic ({serial})"]}]
    result = redact_bundle(bundle, key=b"recording-privacy")
    assert _assert_recording_redacted(result, locations, (serial,)) > 0


@pytest.mark.parametrize(
    "value",
    [
        {"unavailable": [{"reason": "cannot read /dev/cu.usbserial-PRIVATE_SERIAL0"}]},
        {"description": r"Device USB\VID_1234&PID_5678\PRIVATE_SERIAL"},
        ["nested", [r"Device USB\VID_1234&PID_5678\PRIVATE_SERIAL"]],
    ],
)
def test_recording_check_rejects_serials_in_every_string_leaf(value: Any) -> None:
    with pytest.raises(AssertionError):
        _assert_recording_redacted(value, set())
