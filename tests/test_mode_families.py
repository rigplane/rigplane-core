"""MOR-3678: radio profiles declare mode families in one place.

Clients (Pro's CW console among them) must not keep their own list of CW
mode names: Icom names them ``CW``/``CW-R``, the Yaesu FTX-1 ``CW-U``/``CW-L``.
The profile declares ``[modes] families`` and both capability endpoints
publish it as ``modeFamilies``. Expectations are literals, never read back
from the profile under test.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.profiles import RadioProfile
from rigplane.rig_loader import RigConfig, RigLoadError, load_rig
from rigplane.web.server import WebConfig, WebServer

_MINIMAL_TOML = """\
[radio]
id = "family_test"
model = "Family Test"
civ_addr = 0x94
receiver_count = 1
has_lan = false
has_wifi = false

[capabilities]
features = ["tx"]

[modes]
list = ["USB", "LSB", "CW", "CW-R", "FM"]
{modes_extra}

[filters]
list = ["FIL1"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 30000
end_hz = 60000000

[commands]
get_freq = [0x03]
set_freq = [0x05]
"""


def _load(tmp_path: Path, modes_extra: str):
    path = tmp_path / "family_test.toml"
    path.write_text(_MINIMAL_TOML.format(modes_extra=modes_extra), encoding="utf-8")
    return load_rig(path)


# ── Loader ───────────────────────────────────────────────────────


def test_families_load_in_declared_order(tmp_path: Path) -> None:
    rig = _load(tmp_path, 'families = { cw = ["CW", "CW-R"] }')
    assert rig.mode_families == {"cw": ("CW", "CW-R")}
    assert rig.to_profile().mode_families == {"cw": ("CW", "CW-R")}


def test_families_absent_means_empty(tmp_path: Path) -> None:
    rig = _load(tmp_path, "")
    assert rig.mode_families == {}
    assert rig.to_profile().mode_families == {}


@pytest.mark.parametrize(
    ("modes_extra", "mode"),
    [
        pytest.param('families = { cw = ["CW-U"] }', "CW-U", id="unknown-mode"),
        pytest.param('families = { cw = ["CW", "CW-R", "CW"] }', "CW", id="duplicate"),
    ],
)
def test_family_error_names_profile_and_mode(
    tmp_path: Path, modes_extra: str, mode: str
) -> None:
    with pytest.raises(RigLoadError) as excinfo:
        _load(tmp_path, modes_extra)
    message = str(excinfo.value)
    assert "family_test.toml" in message
    assert repr(mode) in message


@pytest.mark.parametrize(
    "modes_extra",
    [
        pytest.param('families = ["CW"]', id="not-a-table"),
        pytest.param("families = { cw = [] }", id="empty-family"),
        pytest.param('families = { cw = "CW" }', id="family-not-a-list"),
        pytest.param('families = { cw = ["CW", 1] }', id="member-not-a-string"),
        pytest.param('families = { cw = ["CW-U"] }', id="member-not-in-list"),
        pytest.param('families = { cw = ["CW", "CW"] }', id="duplicate-member"),
        pytest.param('families = { CW = ["CW"] }', id="unknown-family-name"),
    ],
)
def test_malformed_families_rejected(tmp_path: Path, modes_extra: str) -> None:
    with pytest.raises(RigLoadError, match=r"\[modes\]"):
        _load(tmp_path, modes_extra)


@pytest.mark.parametrize(
    "modes_extra",
    [
        pytest.param('families = { cw = ["CW", 1] }', id="member-not-a-string"),
        pytest.param('families = { cw = "CW" }', id="family-not-a-list"),
    ],
)
def test_family_must_be_a_list_of_strings(tmp_path: Path, modes_extra: str) -> None:
    # The shape check must answer, not the later membership check: ``1`` is
    # not in [modes].list, and a scalar ``"CW"`` would be iterated as "C", "W".
    with pytest.raises(
        RigLoadError,
        match=r"family_test\.toml: \[modes\]\.families\.cw must be a non-empty "
        r"list of strings",
    ):
        _load(tmp_path, modes_extra)


@pytest.mark.parametrize("cls", [RigConfig, RadioProfile])
def test_mode_families_does_not_shift_positional_arguments(cls: type) -> None:
    """Pre-review P2: the field is keyword-only, so callers passing the
    existing fields positionally keep their assignments."""
    by_name = {f.name: f for f in dataclasses.fields(cls)}
    assert by_name["mode_families"].kw_only is True
    positional = [f.name for f in dataclasses.fields(cls) if f.init and not f.kw_only]
    assert "mode_families" not in positional
    assert positional[-1] == "expected_identity_ids"


@pytest.mark.parametrize(
    "mode_list",
    [
        pytest.param('"CW-R"', id="scalar-string"),
        pytest.param('["CW", 1]', id="non-string-entry"),
        pytest.param("{ a = 1 }", id="table"),
    ],
)
def test_modes_list_must_be_a_list_of_strings(tmp_path: Path, mode_list: str) -> None:
    """Pro-gate P2: a scalar ``list = "CW-R"`` passed (substring match of
    family member ``CW``) and published modes ``["C", "W", "-", "R"]``."""
    path = tmp_path / "family_test.toml"
    text = _MINIMAL_TOML.format(modes_extra='families = { cw = ["CW"] }').replace(
        'list = ["USB", "LSB", "CW", "CW-R", "FM"]', f"list = {mode_list}"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RigLoadError, match=r"family_test\.toml: \[modes\]\.list"):
        load_rig(path)


# ── Capability endpoints ─────────────────────────────────────────


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("modes_extra", "expected"),
    [
        ('families = { cw = ["CW-R", "CW"] }', {"cw": ["CW-R", "CW"]}),
        ("", {}),
    ],
    ids=["declared", "absent"],
)
async def test_capability_endpoints_publish_mode_families(
    tmp_path: Path, modes_extra: str, expected: dict[str, list[str]]
) -> None:
    profile = _load(tmp_path, modes_extra).to_profile()
    radio = SimpleNamespace(
        model=profile.model,
        profile=profile,
        capabilities=set(profile.capabilities),
        connected=False,
        control_connected=False,
        radio_ready=False,
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    for path in ("/api/v1/info", "/api/v1/capabilities"):
        writer = _Writer()
        await server._handle_http(writer, "GET", path, headers={})  # noqa: SLF001
        text = writer.buffer.decode("ascii", errors="replace")
        assert int(text.split(" ", 2)[1]) == 200
        payload = json.loads(text[text.index("\r\n\r\n") + 4 :])
        capability_payload = (
            payload["capabilities"] if path == "/api/v1/info" else payload
        )
        assert capability_payload["modeFamilies"] == expected
