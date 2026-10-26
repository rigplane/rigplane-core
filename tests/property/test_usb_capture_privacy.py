import copy
import json
import re
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from rigplane.audio._usb_capture import _validate_bundle
from rigplane.audio._usb_redact import _USB_SERIAL_ID, redact_bundle


def _replace_serials(value: Any, tokens: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            field: _replace_serials(child, tokens) for field, child in value.items()
        }
    if isinstance(value, list):
        return [_replace_serials(child, tokens) for child in value]
    if isinstance(value, str):
        spellings = {
            spelling.lower(): token
            for serial, token in tokens.items()
            for spelling in (serial, serial.replace(" ", "_"))
        }
        pattern = re.compile(
            "|".join(
                re.escape(serial) for serial in sorted(spellings, key=len, reverse=True)
            ),
            re.IGNORECASE | re.ASCII,
        )
        return pattern.sub(lambda match: spellings[match[0].lower()], value)
    return value


@st.composite
def _capture_bundles(draw: st.DrawFn, part: str) -> tuple[dict[str, Any], list[str]]:
    serials = [
        "PRIVATE_SERIAL_" + value + "_END"
        for value in draw(
            st.lists(
                st.text(
                    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ -",
                    min_size=6,
                    max_size=20,
                ),
                min_size=2,
                max_size=3,
                unique_by=lambda value: value.replace(" ", "_").lower(),
            )
        )
    ]
    session_count = draw(st.integers(1, 3))
    evidence_session = draw(st.integers(0, session_count - 1))
    sessions = []
    for session_index in range(session_count):
        ports, radios, usb, children, audio, nodes = [], [], [], [], [], []
        for index, serial in enumerate(serials):
            path_serial = serial.replace(" ", "_")
            if part == "windows":
                devices = [f"COM{index + 3}"]
                instance = rf"USB\VID_1234&PID_5678\{serial}"
                if draw(st.booleans()):
                    nodes.append(
                        {"instance_id": instance, "parent_id": "", "class_name": "USB"}
                    )
                nodes += [
                    {
                        "instance_id": rf"USB\VID_1234&PID_5678&MI_00\7&{index}&0&0000",
                        "parent_id": instance,
                        "class_name": "Ports",
                    },
                    {
                        "instance_id": rf"SWD\MMDEVAPI\endpoint-{index}",
                        "parent_id": instance,
                        "class_name": "AudioEndpoint",
                    },
                ]
            elif part == "macos":
                interface = draw(st.integers(0, 9))
                devices = [
                    f"/dev/{kind}.{stem}{path_serial}{interface}"
                    for kind in ("cu", "tty")
                    for stem in ("usbserial-", "usbmodem")
                ]
                path = f"IOUSB:/Hub@03000000/{serial}@{index + 4}"
                usb.append(
                    {
                        "registry_id": index,
                        "registry_path": path,
                        "parent_path": "IOUSB:/Hub@03000000",
                        "location_id": 0x3120000 + index * 0x100,
                        "serial_number": serial,
                    }
                )
                children.extend(
                    {
                        "device": device,
                        "usb_registry_id": index,
                        "registry_path": path + "/Serial@0",
                        "parent_path": path,
                    }
                    for device in devices
                )
                audio.append(
                    {"uid": f"AppleUSBAudioEngine:Vendor:Codec:{path_serial}:2,1"}
                )
            else:
                devices = [
                    f"/dev/serial/by-id/usb-Vendor_Codec_{path_serial}-if00-port0"
                ]
                path = f"/sys/devices/usb1/1-{index + 2}"
                usb.append(
                    {"path": path, "devpath": str(index + 2), "serial_number": serial}
                )
                children.append({"device": devices[0], "usb_device": path})
                audio.append({"path": path + "/sound/card0", "usb_device": path})
            radios.append({"profile": f"radio-{index}", "serial_port": devices[0]})
            ports.extend(
                {
                    "device": device,
                    "name": device.removeprefix("/dev/"),
                    "serial_number": serial,
                }
                for device in devices
            )
        topology = (
            {"source": "cfgmgr32", "devnodes": nodes}
            if part == "windows"
            else {
                "usb_devices": usb,
                "serial_devices": children,
                "audio_devices": audio,
                "unavailable": [],
            }
        )
        raw = {part: topology, "serial_ports": ports, "portaudio": {"devices": []}}
        unavailable = []
        private_path = (
            rf"USB\VID_1234&PID_5678\{serials[0]}"
            if part == "windows"
            else radios[0]["serial_port"]
        )
        missing = draw(st.sets(st.sampled_from([part, "serial_ports", "portaudio"])))
        if session_index == evidence_session:
            missing.discard(part)
        for source in sorted(missing):
            raw.pop(source)
            reason = draw(
                st.sampled_from(
                    [
                        f"cannot read {private_path}",
                        f"{private_path}: unavailable",
                        f"cannot read '{private_path}'",
                        f"cannot read {private_path}.",
                    ]
                )
            )
            unavailable.append({"part": source, "reason": reason})
        if part != "windows" and part in raw:
            missing_sections = draw(
                st.sets(st.sampled_from(["usb", "serial", "audio"]))
            )
            if session_index == evidence_session:
                missing_sections.discard("usb")
            for source in sorted(missing_sections):
                topology[source + "_devices"] = []
                failure = {
                    "part": part + ":" + source,
                    "reason": f"cannot read {private_path}",
                }
                unavailable.append(failure)
                topology["unavailable"].append(copy.deepcopy(failure))
        sessions.append(
            {
                "schema": "rigplane.usb_audio_session/1",
                "session": ("connected", "unplugged", "replugged")[session_index],
                "target": None if session_index == 0 else "radio-0",
                "radios": radios if session_index == 0 else [],
                "os": part,
                "capture_tool": "rigplane test audio usb-capture",
                "raw": raw,
                "unavailable": unavailable,
            }
        )
    return {"schema": "rigplane.usb_audio_capture/1", "sessions": sessions}, serials


def _serial_tokens(
    bundle: dict[str, Any], result: dict[str, Any], part: str
) -> dict[str, str]:
    tokens = {}
    for original, redacted in zip(bundle["sessions"], result["sessions"], strict=True):
        raw = original["raw"].get(part, {})
        rows = raw.get("devnodes" if part == "windows" else "usb_devices", [])
        redacted_rows = (
            redacted["raw"]
            .get(part, {})
            .get("devnodes" if part == "windows" else "usb_devices", [])
        )
        for row, redacted_row in zip(rows, redacted_rows, strict=True):
            if part == "windows":
                for field in ("instance_id", "parent_id"):
                    if match := _USB_SERIAL_ID.fullmatch(row[field]):
                        serial = match[1]
                        token = redacted_row[field].rsplit("\\", 1)[1]
                        assert re.fullmatch(r"R[A-Z2-7]{10}", token)
                        assert tokens.setdefault(serial, token) == token
                continue
            else:
                serial = row["serial_number"]
                if serial is None:
                    continue
                token = redacted_row["serial_number"]
            assert re.fullmatch(r"R[A-Z2-7]{10}", token)
            assert tokens.setdefault(serial, token) == token
    return tokens


@pytest.mark.parametrize("part", ["windows", "macos", "linux"])
@settings(max_examples=200, deadline=None)
@given(data=st.data())
def test_generated_capture_has_no_serial_anywhere(
    part: str, data: st.DataObject
) -> None:
    bundle, serials = data.draw(_capture_bundles(part))
    _validate_bundle(bundle)
    before = copy.deepcopy(bundle)
    result = redact_bundle(bundle, key=b"generated-capture-privacy")
    # Whole-document scans use long serials; short serials are checked by position below.
    document = json.dumps(result)
    for serial in serials:
        for spelling in {serial, serial.replace(" ", "_")}:
            assert spelling.lower() not in document.lower(), (
                f"raw serial survived: {spelling}"
            )
    assert bundle == before
    tokens = _serial_tokens(bundle, result, part)
    assert set(tokens) == set(serials)
    assert len(set(tokens.values())) == len(serials)
    assert result == _replace_serials(bundle, tokens)


@pytest.mark.parametrize("part", ["windows", "macos", "linux"])
@pytest.mark.parametrize("serial", ["1", "A1", "3121400", "03121400", "1-2.3"])
@settings(max_examples=20, deadline=None)
@given(data=st.data())
def test_short_and_colliding_serials_keep_exact_path_rule(
    part: str, serial: str, data: st.DataObject
) -> None:
    template, serials = data.draw(_capture_bundles(part))
    substitutions = {
        original: serial if index == 0 else original
        for index, original in enumerate(serials)
    }
    collision = (part == "macos" and serial in {"3121400", "03121400"}) or (
        part == "linux" and serial == "1-2.3"
    )
    for session in template["sessions"]:
        topology = session["raw"].get(part)
        if part == "windows" or topology is None:
            continue
        if collision:
            location = (
                {"registry_id": 100, "location_id": 0x3121400}
                if part == "macos"
                else {"path": "/sys/devices/usb1/1-2.3", "devpath": "1-2.3"}
            )
            topology["usb_devices"].append({**location, "serial_number": None})
            if part == "macos":
                for row in topology["audio_devices"]:
                    row["uid"] = _replace_serials(row["uid"], substitutions)
        if part == "macos":
            topology["serial_devices"].append(
                {"device": "/dev/cu.usbmodem14201", "usb_registry_id": None}
            )
            topology["audio_devices"].append(
                {"uid": "AppleUSBAudioEngine:Vendor:Codec:3121400:1"}
            )
    bundle = _replace_serials(template, substitutions)
    _validate_bundle(bundle)
    before = copy.deepcopy(bundle)
    result = redact_bundle(bundle, key=b"generated-capture-privacy")
    assert bundle == before
    tokens = _serial_tokens(bundle, result, part)
    assert set(tokens) == {serial, *serials[1:]}
    assert result == _replace_serials(
        template,
        {
            original: tokens[serial if index == 0 else original]
            for index, original in enumerate(serials)
        },
    )


def test_windows_parent_reference_is_bundle_serial_evidence() -> None:
    serial = "PRIVATE_SERIAL_CHILD_ONLY_END"
    parent = rf"USB\VID_1234&PID_5678\{serial}"
    bundle = {
        "schema": "rigplane.usb_audio_capture/1",
        "sessions": [
            {
                "schema": "rigplane.usb_audio_session/1",
                "session": "connected",
                "target": None,
                "radios": [{"profile": "radio-0", "serial_port": "COM3"}],
                "raw": {
                    "windows": {
                        "devnodes": [
                            {
                                "instance_id": r"USB\VID_1234&PID_5678&MI_00\7&0&0&0000",
                                "parent_id": parent,
                                "class_name": "Ports",
                            }
                        ]
                    }
                },
                "unavailable": [{"part": "serial_ports", "reason": "OSError"}],
            }
        ],
    }
    _validate_bundle(bundle)
    result = redact_bundle(bundle, key=b"generated-capture-privacy")
    assert serial not in json.dumps(result), f"raw serial survived: {serial}"
    token = _serial_tokens(bundle, result, "windows")[serial]
    assert result == _replace_serials(bundle, {serial: token})


def test_serial_replacement_oracle_keeps_overlapping_serials_distinct() -> None:
    first = "PRIVATE_SERIAL_AAAAAA_END"
    second = first + "_END"
    assert _replace_serials(
        [first, second, second.lower()], {first: "RAAAAAAAAAA", second: "RBBBBBBBBBB"}
    ) == ["RAAAAAAAAAA", "RBBBBBBBBBB", "RBBBBBBBBBB"]


@pytest.mark.parametrize("case", [str.upper, str.lower])
def test_windows_diagnostic_uses_serial_port_only_evidence(case: Any) -> None:
    serial = "PRIVATE_SERIAL_DIAGNOSTIC_END"
    reference = case(rf"USB\VID_1234&PID_5678\{serial}")
    bundle = {
        "sessions": [
            {
                "raw": {
                    "serial_ports": [{"serial_number": serial}, {"serial_number": "1"}]
                },
                "unavailable": [
                    {"part": "windows", "reason": f"cannot read {reference}."},
                    {
                        "part": "windows",
                        "reason": case(r"cannot read USB\VID_1234&PID_5678\1&LOC&0"),
                    },
                ],
            }
        ]
    }
    result = redact_bundle(bundle, key=b"generated-capture-privacy")
    assert serial.lower() not in json.dumps(result).lower()
    token = result["sessions"][0]["raw"]["serial_ports"][0]["serial_number"]
    short_token = result["sessions"][0]["raw"]["serial_ports"][1]["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", short_token)
    expected = _replace_serials(bundle, {serial: token})
    expected["sessions"][0]["raw"]["serial_ports"][1]["serial_number"] = short_token
    assert result == expected
