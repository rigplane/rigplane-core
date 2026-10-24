import copy
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
        (r"ABC\ABC#ABC:ABC&ABC", r"{token}\{token}#{token}:{token}&{token}"),
        ("xABC ABCx ABC123 0ABC", "xABC ABCx ABC123 0ABC"),
        ("X_ABC_Y", "X_ABC_Y"),
        ("(ABC)", "(ABC)"),
        ("ABC.1", "ABC.1"),
    ],
)
def test_id_replacement_requires_whole_segment(text: str, expected: str) -> None:
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
    token = row["instance_id"].rsplit("\\", 1)[1]
    assert row["hardware_ids"] == [expected.format(token=token)]
    assert row["compatible_ids"] == [expected.format(token=token)]


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
    assert result["other"] == "ABC_DEF"
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


@pytest.mark.parametrize("serial", ["abc 123", "ABC_123"])
def test_case_variants_and_space_twins_share_token(serial: str) -> None:
    parent = r"USB\VID_1234&PID_5678\ABC123"
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
            (r"USB\VID_1234&PID_5678&MI_00\CHILD", parent.lower()),
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
                            "hwid": "USB SER=AbC_123 LOCATION=1",
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
    assert port["description"] == port["serial_number"]
    token = port["serial_number"]
    assert rows[0]["hardware_ids"] == [f"USB\\{token}", f"USB\\{token}"]
    assert rows[0]["friendly_name"].split()[:2] == [token, token]
    assert port["hwid"] == f"USB SER={port['serial_number']} LOCATION=1"
    text = json.dumps(raw).casefold()
    assert "abc123" not in text
    assert "abc_123" not in text
    assert "abc 123" not in text


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
        "hwid": "USB VID:PID=0001:0001 SER=1 LOCATION=1",
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
        assert raw["serial_ports"][0][name] == token
    assert (
        raw["serial_ports"][0]["hwid"]
        == f"USB VID:PID=0001:0001 SER={token} LOCATION=1"
    )
    rows = raw["windows"]["devnodes"]
    assert rows[0]["instance_id"].endswith("\\" + token)
    assert rows[1]["instance_id"] == endpoint
    assert rows[0]["hardware_ids"] == [
        f"USB\\{token}&{token}#{token}:{token}",
        "1.00000000",
        "X_1_Y",
    ]
    assert rows[0]["compatible_ids"] == [token]
    assert rows[0]["friendly_name"] == token
    assert rows[0]["device_desc"] == token


@pytest.mark.parametrize("text", ["xABC", "ABCx", "(ABC)", "ABC DEF", "X_ABC_Y"])
def test_serial_port_non_hwid_fields_require_whole_field(text: str) -> None:
    bundle = {
        "sessions": [
            {
                "raw": {
                    "serial_ports": [
                        {
                            "serial_number": "ABC",
                            "description": text,
                            "hwid": "USB SER=ABCx LOCATION=ABC",
                        }
                    ]
                }
            }
        ]
    }
    port = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"][
        "serial_ports"
    ][0]
    assert port["description"] == text
    assert port["hwid"] == "USB SER=ABCx LOCATION=ABC"


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
                            "hwid": "USB SER=ab_c_d",
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
    assert port["hwid"] == f"USB SER={token}"
    row = raw["windows"]["devnodes"][0]
    assert row["instance_id"] == "USB\\VID_1234&PID_5678\\" + token
    assert row["friendly_name"] == token
    assert row["device_desc"] == token
    assert row["hardware_ids"] == ["USB\\" + token]
    assert row["compatible_ids"] == ["USB\\" + token]


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
def test_observed_casefold_expansion_spelling_is_redacted(serial: str) -> None:
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
                            "hwid": "USB SER=" + serial,
                        }
                    ],
                }
            }
        ]
    }
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    token = raw["serial_ports"][0]["serial_number"]
    assert re.fullmatch(r"R[A-Z2-7]{10}", token)
    assert raw["serial_ports"][0]["description"] == token
    assert raw["serial_ports"][0]["hwid"] == "USB SER=" + token
    row = raw["windows"]["devnodes"][0]
    assert row["instance_id"] == "USB\\VID_1234&PID_5678\\" + token
    assert row["friendly_name"] == token
    assert row["device_desc"] == token
    assert row["hardware_ids"] == ["USB\\" + token]
    assert row["compatible_ids"] == [token]


@pytest.mark.parametrize("serial", ["ABC123", "abc123"])
def test_same_free_text_across_sources_and_sessions(serial: str) -> None:
    text = "Radio:ABC123"
    session = {
        "raw": {
            "windows": {
                "devnodes": [
                    {
                        "instance_id": r"USB\VID_1234&PID_5678\ABC123",
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
    assert len(set(outputs)) == 1
    assert outputs[0].startswith("Radio:R")
    assert "abc123" not in json.dumps(result).casefold()


@pytest.mark.parametrize(
    "serial, spelling",
    [
        ("AB_CD", "AB CD"),
        ("AB_CD_EF", "AB CD EF"),
        ("AB_CD_EF", "ab_cd ef"),
        ("AB_CD_EF", "Ab Cd_eF"),
    ],
)
def test_hwid_longest_serial_with_spaces_preserves_next_field(
    serial: str, spelling: str
) -> None:
    ports = [
        {"serial_number": "AB", "hwid": "USB SER=AB LOCATION=1-2"},
        {"serial_number": serial, "hwid": f"USB SER={spelling} LOCATION=1-2"},
    ]
    bundle = {"sessions": [{"raw": {"serial_ports": ports}}]}
    result = _usb_redact.redact_bundle(bundle, key=_KEY)
    redacted = result["sessions"][0]["raw"]["serial_ports"]
    for port in redacted:
        assert port["hwid"] == f"USB SER={port['serial_number']} LOCATION=1-2"
    assert redacted[0]["serial_number"] != redacted[1]["serial_number"]
    assert spelling.casefold() not in json.dumps(redacted).casefold()


@pytest.mark.parametrize(
    "text, changed",
    [
        ("(ABC123)", True),
        ("X_ABC123_Y", True),
        ("éABC123", False),
        ("ABC123é", False),
        ("xABC123", False),
        ("ABC123x", False),
    ],
)
def test_free_text_uses_nonalphanumeric_boundaries(text: str, changed: bool) -> None:
    port = {"serial_number": "ABC123", "description": text, "device": text}
    bundle = {"sessions": [{"raw": {"serial_ports": [port]}}]}
    raw = _usb_redact.redact_bundle(bundle, key=_KEY)["sessions"][0]["raw"]
    result = raw["serial_ports"][0]
    assert (result["description"] != text) is changed
    assert result["device"] == text


def test_missing_devnode_fields_are_empty() -> None:
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
    for field in ("parent_id", "friendly_name", "device_desc"):
        assert rows[0][field] == ""
    assert rows[0]["hardware_ids"] == []
    assert rows[0]["compatible_ids"] == []


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
