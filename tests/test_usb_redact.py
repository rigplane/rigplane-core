import base64
import copy
import hashlib
import hmac
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pytest

from rigplane.audio import _usb_redact

_FIXTURE = Path(__file__).parent / "fixtures" / "windows_pnp_gtr7_ic7300_ids.json"
_SERIAL = "IC-7300_00000001"
_SERIAL_B = "SECOND_SERIAL"
_KEY = b"k" * 32


def _bundle() -> dict:
    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))["rows"]
    original = json.loads(
        (_FIXTURE.parent / "windows_pnp_gtr7_ic7300.json").read_text(encoding="utf-8")
    )["tree"]
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
                            "hwid": f"USB SER={_SERIAL} LOCATION=1",
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
    assert port["hwid"] == f"USB SER={token} LOCATION=1"
    assert any(
        row["instance_id"].endswith("\\" + token)
        for row in session["raw"]["windows"]["devnodes"]
    )
    assert bundle == before
    text = json.dumps(result)
    for secret in (_SERIAL, _SERIAL.replace("_", " "), _SERIAL_B, "NETWORK_SERIAL"):
        assert secret not in text


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
    "text, expected",
    [
        ("ABC", "{token}"),
        ("SER=ABC END", "SER={token} END"),
        ("(ABC)\\ABC#ABC:ABC&ABC", "({token})\\{token}#{token}:{token}&{token}"),
        ("xABC ABCx ABC123 0ABC", "xABC ABCx ABC123 0ABC"),
        ("X_ABC_Y", "X_{token}_Y"),
    ],
)
def test_one_boundary_rule_in_every_string(text: str, expected: str) -> None:
    bundle = {
        "sessions": [
            {"raw": {"serial_ports": [{"serial_number": "ABC"}]}, "other": text}
        ]
    }
    token = (
        "R"
        + base64.b32encode(hmac.new(_KEY, b"ABC", hashlib.sha256).digest()).decode()[
            :10
        ]
    )
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    assert result["sessions"][0]["other"] == expected.format(token=token)


def test_longest_match_wins_and_mi_and_generated_segments_are_not_secrets() -> None:
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
    assert result["raw"]["windows"]["devnodes"] == nodes


def test_default_draws_exactly_one_key(monkeypatch: pytest.MonkeyPatch) -> None:
    draw = Mock(return_value=_KEY)
    monkeypatch.setattr(_usb_redact.secrets, "token_bytes", draw)
    _usb_redact.redact_bundle(_bundle())
    draw.assert_called_once_with(32)
