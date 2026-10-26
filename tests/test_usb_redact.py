import copy
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pytest

from rigplane.audio import _usb_redact
from rigplane.audio._usb_capture import _write_private_json

_FIXTURE = Path(__file__).parent / "fixtures" / "windows_pnp_gtr7_ic7300_ids.json"
_SERIAL = "IC-7300_00000001"
_SERIAL_B = "SECOND_SERIAL"
_KEY = b"k" * 32


@pytest.mark.parametrize("part", ["macos", "linux"])
def test_unix_serial_fields_and_child_references_share_bundle_token(part):
    serial = "SECRET_123"
    device = f"/dev/cu.usbserial-{serial}0" if part == "macos" else "/dev/ttyUSB0"
    topology = {
        "usb_devices": [
            {
                "registry_id": 3,
                "location_id": 0x3121300,
                "serial_number": serial,
                "registry_path": f"/USB/{serial}",
                "path": "/sys/devices/usb1/1-2",
                "product": serial,
            }
        ],
        "serial_devices": [
            {
                "device": device,
                "usb_registry_id": 3,
                "registry_path": f"/USB/{serial}/child",
                "usb_device": "/sys/devices/usb1/1-2",
            }
        ],
        "audio_devices": [
            {
                "uid": f"AppleUSBAudioEngine:Vendor:Codec:{serial}:2",
                "usb_device": "/sys/devices/usb1/1-2",
                "name": serial,
            }
        ],
    }
    session = {
        "radios": [{"profile": "radio-a", "serial_port": device}],
        "raw": {
            part: topology,
            "serial_ports": [
                {
                    "serial_number": serial,
                    "device": device,
                    "description": serial,
                }
            ],
        },
    }
    bundle = {"sessions": [session, copy.deepcopy(session)]}
    before = copy.deepcopy(bundle)
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    assert bundle == before
    for session in result["sessions"]:
        raw = session["raw"]
        topology = raw[part]
        token = raw["serial_ports"][0]["serial_number"]
        assert re.fullmatch(r"R[A-Z2-7]{10}", token)
        assert topology["usb_devices"][0]["serial_number"] == token
        assert topology["serial_devices"][0]["usb_device"] == "/sys/devices/usb1/1-2"
        expected_device = device
        if part == "macos":
            assert (
                topology["serial_devices"][0]["registry_path"] == f"/USB/{token}/child"
            )
            assert (
                topology["audio_devices"][0]["uid"]
                == f"AppleUSBAudioEngine:Vendor:Codec:{token}:2"
            )
            expected_device = f"/dev/cu.usbserial-{token}0"
        assert session["radios"][0]["serial_port"] == expected_device
        assert raw["serial_ports"][0]["device"] == expected_device
        assert topology["usb_devices"][0]["product"] == token
        assert topology["audio_devices"][0]["name"] == token
        assert raw["serial_ports"][0]["description"] == token
    assert result["sessions"][0] == result["sessions"][1]


@pytest.mark.parametrize("part", ["macos", "linux"])
@pytest.mark.parametrize("serial", ["1", "USB"])
def test_short_unix_serial_preserves_topology_location_and_stream(part, serial):
    path = "/sys/devices/usb1/1-1/1-1:1.0"
    uid = "AppleUSBAudioEngine:Vendor:Codec:3121400:1"
    device = "/dev/cu.usbmodem3121401" if part == "macos" else "/dev/ttyUSB1"
    raw = {
        part: {
            "usb_devices": [
                {
                    "registry_id": 3,
                    "location_id": 0x3121300,
                    "serial_number": serial,
                    "path": path,
                    "registry_path": "IOUSB:/Codec@03121400",
                }
            ],
            "serial_devices": [
                {"device": device, "usb_registry_id": 3, "usb_device": path}
            ],
            "audio_devices": [
                {"uid": uid},
                {"uid": f"AppleUSBAudioEngine:Vendor:Codec:{serial}:1"},
            ],
        },
        "serial_ports": [{"device": device, "serial_number": serial}],
    }
    result = _usb_redact.redact_bundle({"sessions": [{"raw": raw}]}, key=_KEY)[
        "sessions"
    ][0]["raw"]
    assert result[part]["usb_devices"][0]["path"] == path
    assert result[part]["usb_devices"][0]["registry_path"] == "IOUSB:/Codec@03121400"
    assert result[part]["serial_devices"][0]["device"] == device
    assert result[part]["serial_devices"][0]["usb_device"] == path
    assert result[part]["audio_devices"][0]["uid"] == uid
    assert result["serial_ports"][0]["device"] == device
    assert result["serial_ports"][0]["serial_number"] != serial
    if part == "macos":
        token = result["serial_ports"][0]["serial_number"]
        assert (
            result[part]["audio_devices"][1]["uid"]
            == f"AppleUSBAudioEngine:Vendor:Codec:{token}:1"
        )


def test_bundle_wide_serial_evidence_redacts_partial_capture_sessions() -> None:
    serial = "01AE340D"
    callout = f"/dev/cu.usbserial-{serial}0"
    dialin = f"/dev/tty.usbserial-{serial}1"
    uid = f"AppleUSBAudioEngine:Generic:USB Audio Device:{serial}:2"
    connected = {
        "radios": [{"profile": "radio-a", "serial_port": callout}],
        "raw": {
            # IOUSB unavailable: registry rows are empty, CoreAudio still enumerated.
            "macos": {
                "usb_devices": [],
                "serial_devices": [],
                "audio_devices": [{"uid": uid}],
                "unavailable": [{"part": "macos:usb", "reason": "ioreg failed"}],
            },
            "serial_ports": [
                {"serial_number": serial, "device": callout, "name": "radio-a"}
            ],
        },
    }
    replugged = {
        "radios": [{"profile": "radio-a", "serial_port": callout}],
        "raw": {
            # pyserial unavailable: the serial_ports part is absent altogether.
            "macos": {
                "usb_devices": [
                    {
                        "registry_id": 3,
                        "location_id": 0x3121300,
                        "serial_number": serial,
                    }
                ],
                "serial_devices": [{"device": dialin, "usb_registry_id": 3}],
                "audio_devices": [{"uid": uid}],
                "unavailable": [{"part": "serial_ports", "reason": "comports failed"}],
            },
        },
    }
    result = _usb_redact.redact_bundle({"sessions": [connected, replugged]}, key=_KEY)
    first, second = (session["raw"] for session in result["sessions"])
    token = first["serial_ports"][0]["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    expected_callout = f"/dev/cu.usbserial-{token}0"
    expected_dialin = f"/dev/tty.usbserial-{token}1"
    expected_uid = f"AppleUSBAudioEngine:Generic:USB Audio Device:{token}:2"
    assert first["serial_ports"][0]["device"] == expected_callout
    assert result["sessions"][0]["radios"][0]["serial_port"] == expected_callout
    assert first["macos"]["audio_devices"][0]["uid"] == expected_uid
    assert second["macos"]["usb_devices"][0]["serial_number"] == token
    assert second["macos"]["serial_devices"][0]["device"] == expected_dialin
    assert second["macos"]["audio_devices"][0]["uid"] == expected_uid
    assert result["sessions"][1]["radios"][0]["serial_port"] == expected_callout
    for raw in (first, second):
        assert serial not in json.dumps(raw)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("pyserial_missing", [False, True])
@pytest.mark.parametrize("identity", ["3121400", "1-2.3"])
def test_partial_bundle_shares_path_aliases_and_location_evidence(
    tmp_path: Path, reverse: bool, pyserial_missing: bool, identity: str
) -> None:
    serial = "01AE340D"
    callout = f"/dev/cu.usbserial-{serial}0"
    dialin = f"/dev/tty.usbserial-{serial}1"
    uid = f"AppleUSBAudioEngine:Vendor:Codec:{identity}:1"
    serial_uid = f"AppleUSBAudioEngine:Vendor:Codec:{serial}:2"
    complete = {
        "radios": [{"profile": "radio-a", "serial_port": callout}],
        "raw": {
            "macos": {
                "usb_devices": [
                    {"registry_id": 1, "serial_number": serial},
                    {"registry_id": 2, "location_id": 0x3121400},
                    {"registry_id": 3, "serial_number": identity},
                ],
                "serial_devices": [{"device": callout, "usb_registry_id": 1}],
                "audio_devices": [{"uid": uid}, {"uid": serial_uid}],
            },
            "linux": {"usb_devices": [{"devpath": "1-2.3", "serial_number": serial}]},
            "serial_ports": [
                {"serial_number": serial, "device": callout, "name": callout[5:]}
            ],
        },
    }
    partial = {
        "radios": [{"profile": "radio-a", "serial_port": dialin}],
        "raw": {
            "macos": {
                "usb_devices": [],
                "serial_devices": [{"device": dialin, "usb_registry_id": None}],
                "audio_devices": [{"uid": uid}, {"uid": serial_uid}],
            },
        },
        "unavailable": [
            {"part": "macos:usb", "reason": "ioreg failed"},
            {"part": "serial_ports", "reason": "comports failed"},
        ],
    }
    if not pyserial_missing:
        partial["raw"]["serial_ports"] = [
            {"device": dialin, "name": dialin[5:], "serial_number": None}
        ]
    sessions = [complete, partial]
    if reverse:
        sessions.reverse()
    bundle = {"sessions": sessions}
    before = copy.deepcopy(bundle)
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    assert bundle == before
    out = tmp_path / "capture.json"
    _write_private_json(out, result)
    saved = out.read_text(encoding="utf-8")
    assert json.loads(saved) == result
    token = result["sessions"][int(reverse)]["raw"]["serial_ports"][0]["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    for original, session in zip(sessions, result["sessions"]):
        device = original["radios"][0]["serial_port"].replace(serial, token)
        raw = session["raw"]
        assert {
            "radio": session["radios"][0]["serial_port"],
            "topology": raw["macos"]["serial_devices"][0]["device"],
            "uids": [row["uid"] for row in raw["macos"]["audio_devices"]],
            "ports": raw.get("serial_ports"),
        } == {
            "radio": device,
            "topology": device,
            "uids": [uid, serial_uid.replace(serial, token)],
            "ports": [
                {
                    "device": device,
                    "name": device[5:],
                    "serial_number": token if original is complete else None,
                }
            ]
            if "serial_ports" in original["raw"]
            else None,
        }
    complete_raw = result["sessions"][int(reverse)]["raw"]
    assert complete_raw["macos"]["usb_devices"][0]["serial_number"] == token
    assert complete_raw["linux"]["usb_devices"][0]["serial_number"] == token
    assert complete_raw["macos"]["usb_devices"][2]["serial_number"] != identity
    assert serial not in saved


def test_bsd_path_suffix_scrubs_embedded_serial_beyond_one_interface_digit() -> None:
    serial = "01AE340D"
    ports = [
        {"serial_number": "1", "device": "/dev/cu.usbmodem14201"},
        {"serial_number": "1", "device": "/dev/tty.usbmodem14201"},
        {"serial_number": serial, "device": f"/dev/cu.usbserial-{serial}0"},
        {"serial_number": serial, "device": f"/dev/tty.usbserial-{serial}"},
        {"serial_number": serial, "device": f"/dev/cu.usbserial-{serial}01"},
    ]
    session = {
        "radios": [
            {"profile": f"radio-{index}", "serial_port": port["device"]}
            for index, port in enumerate(ports)
        ],
        "raw": {
            "macos": {
                "usb_devices": [],
                "serial_devices": [],
                "audio_devices": [],
                "unavailable": [{"part": "macos:usb", "reason": "ioreg failed"}],
            },
            "serial_ports": ports,
        },
    }
    result = _usb_redact.redact_bundle({"sessions": [session]}, key=_KEY)["sessions"][0]
    token = result["raw"]["serial_ports"][2]["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert result["raw"]["serial_ports"][0]["serial_number"] != token
    paths = [radio["serial_port"] for radio in result["radios"]]
    assert paths[0] == "/dev/cu.usbmodem14201"
    assert paths[1] == "/dev/tty.usbmodem14201"
    assert paths[2] == f"/dev/cu.usbserial-{token}0"
    assert paths[3] == f"/dev/tty.usbserial-{token}"
    assert paths[4] == f"/dev/cu.usbserial-{token}01"


def test_complete_capture_keeps_recorded_serial_for_overlapping_bsd_suffixes() -> None:
    serial = "01AE340D"
    device = f"/dev/cu.usbserial-{serial}1"
    bundle = {
        "sessions": [
            {
                "raw": {
                    "macos": {
                        "usb_devices": [
                            {"registry_id": 1, "serial_number": serial},
                            {"registry_id": 2, "serial_number": serial + "1"},
                        ],
                        "serial_devices": [{"device": device, "usb_registry_id": 1}],
                    },
                    "serial_ports": [{"device": device, "serial_number": serial}],
                },
                "radios": [{"profile": "radio-a", "serial_port": device}],
            }
        ]
    }
    result = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]
    assert result["raw"]["serial_ports"][0] == {
        "device": "/dev/cu.usbserial-RHE7PFHYDOF1",
        "serial_number": "RHE7PFHYDOF",
    }
    assert result["raw"]["macos"]["serial_devices"][0]["device"] == (
        "/dev/cu.usbserial-RHE7PFHYDOF1"
    )
    assert result["radios"][0]["serial_port"] == "/dev/cu.usbserial-RHE7PFHYDOF1"


@pytest.mark.parametrize("collision", ["RHE7", "rhe7"])
def test_known_serial_inside_token_keeps_bundle_mapping_consistent(
    collision: str,
) -> None:
    serial = "01AE340D"
    device = f"/dev/cu.usbserial-{serial}0"
    instance = rf"USB\VID_1234&PID_5678\{serial}"
    bundle = {
        "sessions": [
            {
                "radios": [{"serial_port": device}],
                "raw": {
                    "serial_ports": [
                        {
                            "serial_number": serial,
                            "device": device,
                            "description": f"Device {serial}",
                        },
                        {"serial_number": collision, "description": collision},
                    ],
                    "macos": {"usb_devices": [{"serial_number": serial}]},
                    "linux": {"usb_devices": [{"serial_number": serial}]},
                    "windows": {
                        "devnodes": [
                            {
                                "instance_id": serial,
                                "parent_id": "",
                                "class_name": "System",
                            },
                            {
                                "instance_id": instance,
                                "parent_id": serial,
                                "class_name": "USB",
                            },
                        ]
                    },
                },
            }
        ]
    }
    before = copy.deepcopy(bundle)
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    assert bundle == before
    assert _usb_redact.redact_bundle(bundle, key=_KEY) == result
    session = result["sessions"][0]
    raw = session["raw"]
    token = raw["serial_ports"][0]["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert token != "RHE7PFHYDOF"
    assert raw["serial_ports"][0]["description"] == f"Device {token}"
    assert raw["serial_ports"][0]["device"] == f"/dev/cu.usbserial-{token}0"
    assert session["radios"][0]["serial_port"] == f"/dev/cu.usbserial-{token}0"
    for part in ("macos", "linux"):
        assert raw[part]["usb_devices"][0]["serial_number"] == token
    other = raw["serial_ports"][1]
    assert re.fullmatch(r"R[A-Z2-7]{10}", other["serial_number"])
    assert other["description"] == other["serial_number"]
    ancestor, usb = raw["windows"]["devnodes"]
    assert ancestor["instance_id"] == usb["parent_id"] == f"opaque:{token}"
    assert usb["instance_id"] == rf"USB\VID_1234&PID_5678\{token}"
    for known in (serial, collision):
        assert known.lower() not in json.dumps(result).lower()


def test_mini_capture_shape_keeps_location_derived_bsd_names() -> None:
    ic705 = "R5RVE6IAXAX"
    yaesu = "RMTN4JEWAJW"
    cp2105 = "RDFXHT5X7PT"
    webcam = "RFXGLS5P2O6"
    usb_devices = [
        {"registry_id": 1, "location_id": 0x3121200, "serial_number": ic705},
        {"registry_id": 2, "location_id": 0x3121400, "serial_number": None},
        {"registry_id": 3, "location_id": 0x3122100, "serial_number": yaesu},
        {"registry_id": 4, "location_id": 0x3122200, "serial_number": cp2105},
        {"registry_id": 5, "location_id": 0x03200000, "serial_number": webcam},
    ]
    serial_devices = [
        {"device": f"/dev/{kind}.usbmodem3121201", "usb_registry_id": 1}
        for kind in ("cu", "tty")
    ]
    serial_devices += [
        {"device": f"/dev/{kind}.usbmodem3121203", "usb_registry_id": 1}
        for kind in ("cu", "tty")
    ]
    serial_devices += [
        {"device": f"/dev/{kind}.usbserial-{cp2105}{index}", "usb_registry_id": 4}
        for kind in ("cu", "tty")
        for index in (0, 1)
    ]
    serial_devices += [
        {"device": f"/dev/{kind}.usbmodem{yaesu}1", "usb_registry_id": 3}
        for kind in ("cu", "tty")
    ]
    location_uid = "AppleUSBAudioEngine:Burr-Brown from TI:USB Audio CODEC:3121400:1"
    webcam_uid = (
        f"AppleUSBAudioEngine:Unknown Manufacturer:HD Pro Webcam C920:{webcam}:3"
    )
    session = {
        "radios": [
            {"profile": "radio-a", "serial_port": "/dev/cu.usbmodem3121201"},
            {"profile": "radio-b", "serial_port": f"/dev/cu.usbserial-{cp2105}1"},
        ],
        "raw": {
            "macos": {
                "usb_devices": usb_devices,
                "serial_devices": serial_devices,
                "audio_devices": [{"uid": location_uid}, {"uid": webcam_uid}],
                "unavailable": [],
            },
            "serial_ports": [
                {"serial_number": yaesu, "device": f"/dev/cu.usbmodem{yaesu}1"}
            ],
        },
    }
    result = _usb_redact.redact_bundle({"sessions": [session]}, key=_KEY)["sessions"][0]
    raw = result["raw"]
    token_yaesu = raw["serial_ports"][0]["serial_number"]
    token_cp2105 = raw["macos"]["usb_devices"][3]["serial_number"]
    token_webcam = raw["macos"]["usb_devices"][4]["serial_number"]
    for token in (token_yaesu, token_cp2105, token_webcam):
        assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert len({token_yaesu, token_cp2105, token_webcam}) == 3
    # Location-derived modem names stay exactly as recorded.
    assert result["radios"][0]["serial_port"] == "/dev/cu.usbmodem3121201"
    assert raw["macos"]["audio_devices"][0]["uid"] == location_uid
    devices = [row["device"] for row in raw["macos"]["serial_devices"]]
    assert devices[0:4] == [
        "/dev/cu.usbmodem3121201",
        "/dev/tty.usbmodem3121201",
        "/dev/cu.usbmodem3121203",
        "/dev/tty.usbmodem3121203",
    ]
    # Serial-derived names share the bundle token plus the interface digit.
    assert devices[4:8] == [
        f"/dev/cu.usbserial-{token_cp2105}0",
        f"/dev/cu.usbserial-{token_cp2105}1",
        f"/dev/tty.usbserial-{token_cp2105}0",
        f"/dev/tty.usbserial-{token_cp2105}1",
    ]
    assert devices[8:10] == [
        f"/dev/cu.usbmodem{token_yaesu}1",
        f"/dev/tty.usbmodem{token_yaesu}1",
    ]
    assert result["radios"][1]["serial_port"] == f"/dev/cu.usbserial-{token_cp2105}1"
    assert raw["macos"]["audio_devices"][1]["uid"] == (
        f"AppleUSBAudioEngine:Unknown Manufacturer:HD Pro Webcam C920:{token_webcam}:3"
    )
    for serial in (ic705, yaesu, cp2105, webcam):
        assert serial not in json.dumps(result)
    expected = copy.deepcopy(session)
    expected["radios"][1]["serial_port"] = f"/dev/cu.usbserial-{token_cp2105}1"
    expected_raw = expected["raw"]
    expected_raw["serial_ports"][0].update(
        serial_number=token_yaesu, device=f"/dev/cu.usbmodem{token_yaesu}1"
    )
    for row in expected_raw["macos"]["usb_devices"]:
        if serial := row.get("serial_number"):
            row["serial_number"] = _usb_redact._token(_KEY, serial.lower())
    for row, device in zip(expected_raw["macos"]["serial_devices"], devices):
        row["device"] = device
    expected_raw["macos"]["audio_devices"][1]["uid"] = webcam_uid.replace(
        webcam, token_webcam
    )
    assert result == expected


@pytest.mark.parametrize("identity", ["3121400", "03121400", "03ABCd00"])
def test_macos_uid_location_wins_over_another_devices_serial(identity):
    uid = f"AppleUSBAudioEngine:Vendor:Codec:{identity}:1"
    raw = {
        "macos": {
            "usb_devices": [
                {"registry_id": 1, "location_id": 0x3121300, "serial_number": identity},
                {
                    "registry_id": 2,
                    "location_id": int(identity, 16),
                    "serial_number": None,
                },
            ],
            "audio_devices": [{"uid": uid}],
        }
    }
    result = _usb_redact.redact_bundle({"sessions": [{"raw": raw}]}, key=_KEY)
    topology = result["sessions"][0]["raw"]["macos"]
    assert topology["audio_devices"][0]["uid"] == uid
    assert topology["usb_devices"][0]["serial_number"] != identity


def test_malformed_macos_uid_is_preserved_without_crashing_redaction():
    uid = "AppleUSBAudioEngine:incomplete"
    result = _usb_redact.redact_bundle(
        {"sessions": [{"raw": {"macos": {"audio_devices": [{"uid": uid}]}}}]}, key=_KEY
    )
    assert result["sessions"][0]["raw"]["macos"]["audio_devices"][0]["uid"] == uid


def _bundle() -> dict:
    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))["rows"]
    original = json.loads(
        (_FIXTURE.parent / "windows_pnp_gtr7_ic7300.json").read_text(encoding="utf-8")
    )["tree"]
    for row in rows:
        for name in ("parent_id", "class_name", "friendly_name", "device_desc"):
            row.setdefault(name, "")
        for name in ("hardware_ids", "compatible_ids"):
            row.setdefault(name, [])
    ids = {row["instance_id"].casefold() for row in rows}
    rows += [
        dict(
            instance_id=i,
            parent_id=p,
            class_name=c,
            friendly_name=n,
            device_desc="",
            hardware_ids=[],
            compatible_ids=[],
        )
        for i, p, c, n in original
        if i.casefold() not in ids
    ]
    serial = next(row for row in rows if _SERIAL in row["instance_id"])
    second = copy.deepcopy(serial)
    second["instance_id"] = second["instance_id"].replace(_SERIAL, _SERIAL_B)
    rows.append(second)
    rows.append(
        dict(
            instance_id=r"USB\VID_1234&PID_5678\NETWORK_SERIAL",
            parent_id=serial["parent_id"],
            class_name="Net",
            friendly_name="Network adapter",
            device_desc="Network adapter",
            hardware_ids=[],
            compatible_ids=[],
        )
    )
    sessions = []
    for name in ("connected", "unplugged", "replugged"):
        sessions.append(
            {
                "session": name,
                "target": None if name == "connected" else "radio-a",
                "radios": [
                    {"profile": "radio-a", "serial_port": "COM3"},
                    {"profile": "radio-b", "serial_port": "COM4"},
                ]
                if name == "connected"
                else [],
                "raw": {
                    "windows": {"source": "cfgmgr32", "devnodes": copy.deepcopy(rows)},
                    "serial_ports": [
                        {
                            "serial_number": _SERIAL.replace("_", " "),
                            "location": _SERIAL,
                        },
                        {"serial_number": _SERIAL_B},
                    ],
                },
            }
        )
    # Only A disappears; B is the cross-session identity control.
    sessions[1]["raw"]["windows"]["devnodes"] = [
        row
        for row in sessions[1]["raw"]["windows"]["devnodes"]
        if _SERIAL not in row["instance_id"]
    ]
    sessions[1]["raw"]["serial_ports"] = [{"serial_number": _SERIAL_B}]
    return {"schema": "rigplane.usb_audio_capture/1", "sessions": sessions}


def test_serial_segments_twins_and_equal_fields_keep_one_token() -> None:
    bundle = _bundle()
    before = copy.deepcopy(bundle)
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    session = result["sessions"][0]
    port = session["raw"]["serial_ports"][0]
    token = port["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert port["location"] == token
    assert any(
        row["instance_id"].endswith("\\" + token)
        for row in session["raw"]["windows"]["devnodes"]
    )
    assert bundle == before


def test_one_key_for_bundle_preserves_b_and_per_session_keys_do_not() -> None:
    bundle = _bundle()

    def b_ids(result: dict) -> list[str]:
        return [
            session["raw"]["serial_ports"][-1]["serial_number"]
            for session in result["sessions"]
        ]

    redacted = _usb_redact.redact_bundle(bundle, key=_KEY)
    tokens = b_ids(redacted)
    assert len(set(tokens)) == 1
    for session, token in zip(redacted["sessions"], tokens):
        assert any(
            row["instance_id"].endswith("\\" + token)
            for row in session["raw"]["windows"]["devnodes"]
        )
    separate = [
        _usb_redact.redact_bundle({"sessions": [session]}, key=bytes([index]) * 32)
        for index, session in enumerate(bundle["sessions"])
    ]
    different = [b_ids(result)[0] for result in separate]
    assert len(set(different)) == 3
    for result, token in zip(separate, different):
        assert any(
            row["instance_id"].endswith("\\" + token)
            for row in result["sessions"][0]["raw"]["windows"]["devnodes"]
        )


def test_network_nodes_gone_endpoint_owners_and_ancestors_opaque() -> None:
    result = _usb_redact.redact_bundle(_bundle(), key=_KEY)
    owners = []
    for session in result["sessions"]:
        rows = session["raw"]["windows"]["devnodes"]
        assert not any(row["class_name"].casefold() == "net" for row in rows)
        assert "Network adapter" not in json.dumps(rows)
        virtual = [
            row
            for row in rows
            if row["friendly_name"].startswith("RigPlane Virtual Cable")
        ]
        assert len(virtual) == 2
        assert all(row["instance_id"].startswith("SWD\\MMDEVAPI\\") for row in virtual)
        assert all(
            re.fullmatch(r"opaque:R[A-Z2-7]{10}", row["parent_id"]) for row in virtual
        )
        assert virtual[0]["parent_id"] == virtual[1]["parent_id"]
        owners.append(virtual[0]["parent_id"])
        assert any(row["instance_id"] == owners[-1] for row in rows)
        assert all(
            row["instance_id"].startswith("opaque:")
            for row in rows
            if row["class_name"] == "System"
        )
        assert any(row["instance_id"].startswith("USB\\ROOT_HUB30\\") for row in rows)
    assert len(set(owners)) == 1


@pytest.mark.parametrize(
    "text",
    [
        "ABC",
        r"ABC\ABC#ABC:ABC&ABC",
        "xABC ABCx ABC123 0ABC",
        "X_ABC_Y",
        "(ABC)",
        "ABC.1",
    ],
)
def test_hardware_and_compatible_ids_are_preserved(text: str) -> None:
    node = dict(
        instance_id=r"USB\VID_1234&PID_5678\ABC",
        parent_id="",
        class_name="USB",
        friendly_name="",
        device_desc="",
        hardware_ids=[text],
        compatible_ids=[text],
    )
    bundle = {"sessions": [{"raw": {"windows": {"devnodes": [node]}}}]}
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    row = result["sessions"][0]["raw"]["windows"]["devnodes"][0]
    assert row["hardware_ids"] == [text]
    assert row["compatible_ids"] == [text]


def test_mi_and_generated_segments_are_not_secrets() -> None:
    nodes = [
        dict(
            instance_id=i,
            parent_id="",
            class_name="USB",
            friendly_name="",
            device_desc="",
            hardware_ids=[],
            compatible_ids=[],
        )
        for i in (
            r"USB\VID_1234&PID_5678&MI_00\INTERFACE",
            r"USB\VID_1234&PID_5678\7&GENERATED&0",
        )
    ]
    bundle = {
        "sessions": [
            {
                "raw": {
                    "windows": {"devnodes": nodes},
                    "serial_ports": [
                        {"serial_number": "ABC"},
                        {"serial_number": "ABC_DEF"},
                    ],
                },
                "other": "ABC_DEF",
            }
        ]
    }
    result = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]
    assert result["other"] == result["raw"]["serial_ports"][1]["serial_number"]
    assert (
        result["raw"]["serial_ports"][0]["serial_number"]
        != result["raw"]["serial_ports"][1]["serial_number"]
    )
    assert result["raw"]["windows"]["devnodes"] == nodes


def test_default_draws_exactly_one_key(monkeypatch: pytest.MonkeyPatch) -> None:
    draw = Mock(return_value=_KEY)
    monkeypatch.setattr(_usb_redact.secrets, "token_bytes", draw)
    _usb_redact.redact_bundle(_bundle())
    draw.assert_called_once_with(32)
    assert draw.call_count == 1
    _usb_redact.redact_bundle(_bundle())
    assert draw.call_count == 2


def test_usb_serial_changes_only_its_known_id_segments() -> None:
    parent = r"USB\VID_1234&PID_5678\USB"
    child = r"USB\VID_1234&PID_5678&MI_00\7&CHILD&0"
    ids = [
        parent,
        child,
        r"USB\ROOT_HUB30\4&ROOT&0",
        r"USB\VID_ABCD&PID_EF01\7&GENERATED&0",
        r"USB\VID_ABCD&PID_EF01&MI_01\USB",
        r"USB\VID_ABCD&PID_EF01&REV_0100\USB",
    ]
    nodes = [
        {
            "instance_id": instance_id,
            "parent_id": parent if instance_id == child else "",
            "hardware_ids": [r"USB\VID_1234&PID_5678", r"USB\USB"],
            "compatible_ids": [r"USB\Class_01", "USB"],
            "friendly_name": "USB",
            "device_desc": "USB",
        }
        for instance_id in ids
    ]
    bundle = {"sessions": [{"raw": {"windows": {"devnodes": nodes}}}]}
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    rows = result["sessions"][0]["raw"]["windows"]["devnodes"]
    token = rows[0]["instance_id"].rsplit("\\", 1)[1]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    expected = copy.deepcopy(nodes)
    expected[0]["instance_id"] = "USB\\VID_1234&PID_5678\\" + token
    expected[1]["parent_id"] = expected[0]["instance_id"]
    assert rows == expected


def test_serial_scrubs_location_text_without_recorded_usb_location_evidence() -> None:
    port = {
        "serial_number": "1-2.3",
        "location": "1-2.3",
    }
    bundle = {"sessions": [{"raw": {"serial_ports": [port]}}]}
    redacted = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"][
        "serial_ports"
    ][0]
    token = redacted["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert redacted == {
        **port,
        "serial_number": token,
        "location": token,
    }


def test_mme_truncation_and_endpoint_names_scrub_embedded_serial() -> None:
    name = "Radio:ABC123".ljust(40, "x")
    endpoint = {
        "instance_id": r"SWD\MMDEVAPI\{0.0.1.00000000}.{abcdef}",
        "parent_id": "",
        "friendly_name": name,
    }
    portaudio = {
        "devices": [{"name": name[:31], "hostapi": 0}],
        "hostapis": [{"name": "MME"}],
    }
    bundle = {
        "sessions": [
            {
                "raw": {
                    "windows": {"devnodes": [endpoint]},
                    "portaudio": portaudio,
                    "serial_ports": [{"serial_number": "ABC123"}],
                }
            }
        ]
    }
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    assert len(name) == 40 and len(name[:31]) == 31
    token = raw["serial_ports"][0]["serial_number"]
    endpoint["friendly_name"] = name.replace("ABC123", token)
    portaudio["devices"][0]["name"] = name[:31].replace("ABC123", token)
    assert raw["windows"]["devnodes"] == [endpoint]
    assert raw["portaudio"] == portaudio


def test_serial_is_shared_with_earlier_session_parent_reference() -> None:
    parent = r"USB\VID_1234&PID_5678\Ab C"
    child = {
        "instance_id": r"USB\VID_1234&PID_5678&MI_00\7&CHILD&0",
        "parent_id": parent,
    }
    bundle = {
        "sessions": [
            {"raw": {"windows": {"devnodes": [child]}}},
            {"raw": {"serial_ports": [{"serial_number": "aB_c"}]}},
        ]
    }
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    token = result["sessions"][0]["raw"]["windows"]["devnodes"][0]["parent_id"].rsplit(
        "\\", 1
    )[1]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert result["sessions"][1]["raw"]["serial_ports"] == [{"serial_number": token}]


@pytest.mark.parametrize("serial", ["abc 123", "ABC_123"])
def test_case_variants_and_space_twins_share_token(serial: str) -> None:
    parent = r"USB\VID_1234&PID_5678\AbC_123"
    nodes = [
        dict(
            instance_id=instance_id,
            parent_id=parent_id,
            class_name="USB",
            friendly_name="abc_123 ABC 123 abc123",
            device_desc="ABC_123 abc 123 ABC123",
            hardware_ids=[r"USB\ABC_123", r"USB\abc 123"],
            compatible_ids=[r"USB\abc123"],
        )
        for instance_id, parent_id in (
            (parent, ""),
            (
                r"USB\VID_1234&PID_5678&MI_00\CHILD",
                parent.replace("AbC_123", "aBc 123"),
            ),
        )
    ]
    bundle = {
        "sessions": [
            {
                "raw": {
                    "windows": {"devnodes": nodes},
                    "serial_ports": [
                        {
                            "serial_number": serial,
                            "description": "aBc_123",
                        }
                    ],
                }
            }
        ]
    }
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    rows = raw["windows"]["devnodes"]
    assert rows[1]["parent_id"] == rows[0]["instance_id"]
    port = raw["serial_ports"][0]
    token = port["serial_number"]
    assert port["description"] == token
    assert rows[0]["instance_id"] == "USB\\VID_1234&PID_5678\\" + token
    for row in rows:
        assert row["hardware_ids"] == ["USB\\" + token, "USB\\" + token]
        assert row["compatible_ids"] == [r"USB\abc123"]
        assert row["friendly_name"] == f"{token} {token} abc123"
        assert row["device_desc"] == f"{token} {token} ABC123"


def test_short_serial_only_changes_allowed_data_fields() -> None:
    endpoint = r"SWD\MMDEVAPI\{0.0.1.00000000}.{abcdef}"
    nodes = [
        dict(
            instance_id=instance_id,
            parent_id="",
            class_name="USB" if instance_id.startswith("USB") else "AudioEndpoint",
            friendly_name="1",
            device_desc="1",
            hardware_ids=[r"USB\1&1#1:1", "1.00000000", "X_1_Y"],
            compatible_ids=["1"],
        )
        for instance_id in (r"USB\VID_1234&PID_5678\1", endpoint)
    ]
    port = {
        "serial_number": "1",
        "device": "1",
        "name": "1",
        "description": "1",
        "manufacturer": "1",
        "product": "1",
        "interface": "1",
        "location": "1",
        "vid": 1,
        "pid": 1,
    }
    preserved = {
        "schema": "rigplane.usb_audio_capture/1",
        "session": "1",
        "target": "1",
        "radios": [{"profile": "1", "serial_port": "1"}],
        "os": "1",
        "os_version": "1",
        "capture_tool": "1",
        "unavailable": ["1"],
    }
    session = {
        **copy.deepcopy(preserved),
        "raw": {
            "windows": {"devnodes": nodes},
            "serial_ports": [port],
            "portaudio": {"name": "1"},
            "unavailable": ["1"],
        },
    }
    bundle = {**copy.deepcopy(preserved), "sessions": [session]}
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    assert {name: result[name] for name in preserved} == preserved
    redacted = result["sessions"][0]
    assert {name: redacted[name] for name in preserved} == preserved
    raw = redacted["raw"]
    assert raw["portaudio"] == {"name": "1"}
    assert raw["unavailable"] == ["1"]
    token = raw["serial_ports"][0]["serial_number"]
    assert token != "1"
    for name in (
        "device",
        "name",
        "description",
        "manufacturer",
        "product",
        "interface",
        "location",
    ):
        assert raw["serial_ports"][0][name] == "1"
    rows = raw["windows"]["devnodes"]
    assert rows[0]["instance_id"].endswith("\\" + token)
    assert rows[1]["instance_id"] == endpoint
    for row, original in zip(rows, nodes):
        for field in ("hardware_ids", "compatible_ids", "friendly_name", "device_desc"):
            assert row[field] == original[field]


@pytest.mark.parametrize("serial", ["AB_C D", "ab c_d", "AB C D", "ab_c_d"])
def test_mixed_space_underscore_variants_share_token(serial: str) -> None:
    node = dict(
        instance_id=r"USB\VID_1234&PID_5678\AB_C D",
        parent_id="",
        class_name="USB",
        friendly_name="AB C_D",
        device_desc="ab_c d",
        hardware_ids=[r"USB\ab c_d"],
        compatible_ids=[r"USB\AB_C_D"],
    )
    bundle = {
        "sessions": [
            {
                "raw": {
                    "windows": {"devnodes": [node]},
                    "serial_ports": [
                        {
                            "serial_number": serial,
                            "description": "aB_c D",
                        }
                    ],
                }
            }
        ]
    }
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    port = raw["serial_ports"][0]
    token = port["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert port["description"] == token
    row = raw["windows"]["devnodes"][0]
    assert row["instance_id"] == "USB\\VID_1234&PID_5678\\" + token
    assert row["friendly_name"] == row["device_desc"] == token
    assert row["hardware_ids"] == row["compatible_ids"] == ["USB\\" + token]


def test_unicode_ignorecase_extra_match_does_not_raise_or_replace_unlearned_value() -> (
    None
):
    node = dict(
        instance_id=r"USB\VID_1234&PID_5678\I",
        parent_id="",
        class_name="USB",
        friendly_name="ı I i",
        device_desc="İ I i",
        hardware_ids=[r"USB\ı"],
        compatible_ids=[],
    )
    bundle = {"sessions": [{"raw": {"windows": {"devnodes": [node]}}}]}
    row = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]["windows"][
        "devnodes"
    ][0]
    token = row["instance_id"].rsplit("\\", 1)[1]
    assert token != "I"
    assert row["friendly_name"] == "ı I i"
    assert row["device_desc"] == "İ I i"
    assert row["hardware_ids"] == [r"USB\ı"]


@pytest.mark.parametrize("serial", ["Straße", "İ"])
def test_observed_unicode_spelling_is_redacted(serial: str) -> None:
    node = dict(
        instance_id="USB\\VID_1234&PID_5678\\" + serial,
        parent_id="",
        class_name="USB",
        friendly_name=serial,
        device_desc=serial,
        hardware_ids=["USB\\" + serial],
        compatible_ids=[serial],
    )
    bundle = {
        "sessions": [
            {
                "raw": {
                    "windows": {"devnodes": [node]},
                    "serial_ports": [
                        {
                            "serial_number": serial,
                            "description": serial,
                        }
                    ],
                }
            }
        ]
    }
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    token = raw["serial_ports"][0]["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    expected = token if len(serial) >= 4 else serial
    assert raw["serial_ports"][0]["description"] == expected
    row = raw["windows"]["devnodes"][0]
    assert row["instance_id"] == "USB\\VID_1234&PID_5678\\" + token
    assert row["friendly_name"] == row["device_desc"] == expected
    assert row["hardware_ids"] == ["USB\\" + expected]
    assert row["compatible_ids"] == [expected]


@pytest.mark.parametrize("serial", ["ABC", "abc", "ABCD", "abcd", "ABC123", "abc123"])
def test_names_scrub_long_serials_across_sources_and_sessions(serial: str) -> None:
    text = f"Radio:{serial.upper()}"
    session = {
        "raw": {
            "windows": {
                "devnodes": [
                    {
                        "instance_id": "USB\\VID_1234&PID_5678\\" + serial.upper(),
                        "friendly_name": text,
                    }
                ]
            },
            "portaudio": {
                "devices": [{"name": text, "index": 1, "hostapi": 1}],
                "hostapis": [{"name": text, "index": 1}],
                "generation": 1,
                "fresh": True,
            },
            "serial_ports": [{"serial_number": serial, "description": text}],
        },
    }
    result = _usb_redact.redact_bundle(
        {"sessions": [session, copy.deepcopy(session)]}, key=_KEY
    )
    outputs = []
    for session in result["sessions"]:
        raw = session["raw"]
        outputs.extend(
            [
                raw["windows"]["devnodes"][0]["friendly_name"],
                raw["portaudio"]["devices"][0]["name"],
                raw["portaudio"]["hostapis"][0]["name"],
                raw["serial_ports"][0]["description"],
            ]
        )
        assert raw["portaudio"]["devices"][0]["index"] == 1
        assert raw["portaudio"]["generation"] == 1
        token = raw["serial_ports"][0]["serial_number"]
        assert re.fullmatch(r"R[A-Z2-7]{10}", token)
        assert raw["windows"]["devnodes"][0]["instance_id"].endswith("\\" + token)
    assert set(outputs) == {f"Radio:{token}" if len(serial) >= 4 else text}


@pytest.mark.parametrize(
    "first,second,same_token",
    [
        ("Straße", "STRASSE", False),
        ("İ", "i", False),
        ("ı", "I", False),
        ("ſ", "S", False),
        ("Ä", "ä", False),
        ("ab12", "AB12", True),
    ],
)
def test_only_ascii_case_variants_share_serial_tokens(
    first: str, second: str, same_token: bool
) -> None:
    ports = [{"serial_number": serial} for serial in (first, second)]
    bundle = {"sessions": [{"raw": {"serial_ports": ports}}]}
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    tokens = [port["serial_number"] for port in raw["serial_ports"]]
    assert all(re.fullmatch(r"R[A-Z2-7]{10}", token) for token in tokens)
    assert (tokens[0] == tokens[1]) is same_token


def test_unicode_serial_nodes_and_parent_references_remain_distinct() -> None:
    serials = ("Straße", "STRASSE")
    nodes = []
    for index, serial in enumerate(serials):
        parent = f"USB\\VID_1234&PID_5678\\{serial}"
        ancestor = f"ROOT\\ANCESTOR{index}"
        nodes.extend(
            [
                {"instance_id": parent, "parent_id": ancestor, "class_name": "USB"},
                {"instance_id": ancestor, "parent_id": "", "class_name": "System"},
                {
                    "instance_id": f"USB\\VID_1234&PID_5678&MI_00\\7&CHILD{index}&0",
                    "parent_id": parent,
                    "class_name": "USB",
                },
            ]
        )
    bundle = {
        "sessions": [
            {
                "raw": {
                    "windows": {"devnodes": nodes},
                    "serial_ports": [{"serial_number": serial} for serial in serials],
                }
            }
        ]
    }
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    tokens = [port["serial_number"] for port in raw["serial_ports"]]
    assert tokens[0] != tokens[1]
    rows = raw["windows"]["devnodes"]
    assert len(rows) == len(nodes)
    for index, token in enumerate(tokens):
        parent, ancestor, child = rows[index * 3 : index * 3 + 3]
        assert parent["instance_id"] == "USB\\VID_1234&PID_5678\\" + token
        assert child["parent_id"] == parent["instance_id"]
        assert parent["parent_id"] == ancestor["instance_id"]
        assert ancestor["instance_id"].startswith("opaque:")
    assert rows[1]["instance_id"] != rows[4]["instance_id"]


@pytest.mark.parametrize(
    "text",
    [
        "(ABC123)",
        "X_ABC123_Y",
        "éABC123",
        "ABC123é",
        "xABC123",
        "ABC123x",
    ],
)
def test_free_text_scrubs_embedded_serial(text: str) -> None:
    port = {"serial_number": "ABC123", "description": text, "device": text}
    bundle = {"sessions": [{"raw": {"serial_ports": [port]}}]}
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    result = raw["serial_ports"][0]
    expected = text.replace("ABC123", result["serial_number"])
    assert result["description"] == expected
    assert result["device"] == expected


def test_final_scrub_covers_root_scalars_lists_and_preserves_keys() -> None:
    serial = "PRIVATE_SERIAL"
    bundle = {
        serial: [f"prefix:{serial}:suffix", {"nested": serial.lower()}, None, 4],
        "sessions": [{"raw": {"serial_ports": [{"serial_number": serial}]}}],
    }
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    token = result["sessions"][0]["raw"]["serial_ports"][0]["serial_number"]
    assert result[serial] == [f"prefix:{token}:suffix", {"nested": token}, None, 4]


def test_final_scrub_prefers_longest_serial_and_preserves_only_location_identity() -> (
    None
):
    bundle = {
        "sessions": [
            {
                "raw": {
                    "serial_ports": [
                        {"serial_number": value}
                        for value in ("ABCD", "ABCD_EFG", "03121400")
                    ],
                    "macos": {
                        "usb_devices": [{"location_id": 0x3121400}],
                        "audio_devices": [
                            {
                                "uid": "AppleUSBAudioEngine:03121400:Codec:03121400:1",
                                "name": "Codec 03121400 ABCD_EFG ABCD",
                            }
                        ],
                    },
                }
            }
        ],
    }
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    first, second, location = [port["serial_number"] for port in raw["serial_ports"]]
    audio = raw["macos"]["audio_devices"][0]
    assert audio["uid"] == f"AppleUSBAudioEngine:{location}:Codec:03121400:1"
    assert audio["name"] == f"Codec {location} {second} {first}"


def test_missing_devnode_name_and_hardware_fields_are_not_added() -> None:
    bundle = {
        "sessions": [
            {
                "raw": {
                    "windows": {
                        "devnodes": [
                            {},
                            {"instance_id": r"USB\VID_1234&PID_5678\ABC123"},
                        ]
                    }
                }
            }
        ]
    }
    rows = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]["windows"][
        "devnodes"
    ]
    assert len(rows) == 1
    assert rows[0]["instance_id"].startswith("USB\\VID_1234&PID_5678\\R")
    assert rows[0]["parent_id"] == ""
    for field in ("friendly_name", "device_desc", "hardware_ids", "compatible_ids"):
        assert field not in rows[0]


def test_short_serial_preserves_names_and_metadata() -> None:
    pa = {
        "devices": [{"name": "Radio 1", "index": 1, "hostapi": 1}],
        "hostapis": [{"name": "API 1", "index": 1}],
        "generation": 1,
        "fresh": True,
    }
    port = {
        "serial_number": "1",
        "name": "Radio 1",
        "description": "Radio:1",
        "vid": 1,
        "pid": 1,
        "unavailable": ["1"],
        "os_version": "1",
    }
    metadata = {
        "schema": "rigplane.usb_audio_capture/1",
        "session": "1",
        "target": "1",
        "radios": [{"profile": "1"}],
        "os": "1",
        "os_version": "1",
        "capture_tool": "1",
        "unavailable": ["1"],
    }
    bundle = {
        **metadata,
        "sessions": [
            {
                **metadata,
                "raw": {
                    "serial_ports": [port],
                    "portaudio": pa,
                },
            }
        ],
    }
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    assert {key: result[key] for key in metadata} == metadata
    session = result["sessions"][0]
    assert {key: session[key] for key in metadata} == metadata
    assert session["raw"]["portaudio"] == pa
    redacted_port = session["raw"]["serial_ports"][0]
    assert redacted_port["serial_number"] != "1"
    assert {
        key: value for key, value in redacted_port.items() if key != "serial_number"
    } == {key: value for key, value in port.items() if key != "serial_number"}
