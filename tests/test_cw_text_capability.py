"""MOR-3678: the profile says whether a radio can send CW text (``cwText``).

The ``cw`` capability tag drives the CW decoder, console and keyer
controls; sending typed text is a separate, profile-driven ability:
``[cw_text]`` -> ``RadioProfile.cw_text`` -> ``cwText`` on
``/api/v1/info`` and ``/api/v1/capabilities``. The FTX-1 writes text with
KM KEYING MEMORY (FTX-1_CAT_OM_ENG_2508-C printed p.17, "up to 50
characters") into a keyer memory channel, so it publishes
``maxLength`` 50 and ``memoryChannel`` 5; sending stays disabled until the
memory type is verified on the radio. Expectations are literals.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.profiles import CwTextSpec, RadioProfile
from rigplane.rig_loader import RigConfig, RigLoadError, load_rig
from rigplane.runtime.callable_support import supports_callable
from rigplane.web.api_contract import RESPONSE_FIELD_CONTRACTS
from rigplane.web.server import WebConfig, WebServer

ROOT = Path(__file__).resolve().parents[1]
RIGS = ROOT / "rigs"

_MINIMAL_TOML = """\
[radio]
id = "cw_text_test"
model = "CW Text Test"
civ_addr = 0x94
receiver_count = 1
has_lan = false
has_wifi = false

[capabilities]
features = ["tx", "cw"]

[modes]
list = ["USB", "LSB", "CW", "CW-R", "FM"]

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
{commands_extra}

{cw_text}
"""


def _load(tmp_path: Path, cw_text: str = "", *, send_cw: bool = True) -> RigConfig:
    path = tmp_path / "cw_text_test.toml"
    path.write_text(
        _MINIMAL_TOML.format(
            commands_extra="send_cw = [0x17]\nstop_cw = [0x17]" if send_cw else "",
            cw_text=cw_text,
        ),
        encoding="utf-8",
    )
    return load_rig(path)


# ── Shipped profiles ─────────────────────────────────────────────


def test_ftx1_publishes_cw_text_disabled_with_keyer_memory_five() -> None:
    rig = load_rig(RIGS / "ftx1.toml")
    expected = CwTextSpec(
        supported=False, max_length=50, memory_channel=5, memory_type="text"
    )
    assert rig.cw_text == expected
    assert rig.to_profile().cw_text == expected
    # The cw tag (decoder, console, keyer controls) is untouched.
    assert "cw" in rig.to_profile().capabilities


@pytest.mark.parametrize(
    "name",
    ["ic705.toml", "ic7300.toml", "ic7300mk2.toml", "ic7610.toml", "ic9700.toml"],
)
def test_icom_profiles_keep_sending_cw_text(name: str) -> None:
    # No Icom profile declares its own text limit: the 512-character default.
    assert load_rig(RIGS / name).to_profile().cw_text == CwTextSpec(
        supported=True, max_length=512, memory_channel=None, memory_type=None
    )


@pytest.mark.parametrize("name", ["tx500.toml", "x6100.toml", "x6200.toml"])
def test_profiles_without_a_send_cw_command_cannot_send_cw_text(name: str) -> None:
    profile = load_rig(RIGS / name).to_profile()
    assert profile.cw_text == CwTextSpec(
        supported=False, max_length=512, memory_channel=None, memory_type=None
    )
    assert "cw" in profile.capabilities


# ── Loader ───────────────────────────────────────────────────────


def test_absent_section_follows_the_send_cw_command(tmp_path: Path) -> None:
    assert _load(tmp_path).cw_text == CwTextSpec(True, 512, None, None)
    assert _load(tmp_path, send_cw=False).cw_text == CwTextSpec(False, 512, None, None)


def test_declared_section_loads(tmp_path: Path) -> None:
    rig = _load(
        tmp_path,
        "[cw_text]\nsupported = true\nmax_length = 50\nmemory_channel = 3\n"
        'memory_type = "message"',
    )
    assert rig.cw_text == CwTextSpec(True, 50, 3, "message")


@pytest.mark.parametrize(
    ("section", "send_cw", "fragment"),
    [
        ('[cw_text]\nsupported = "yes"', True, "supported"),
        ("[cw_text]\nmax_length = 0", True, "max_length"),
        ("[cw_text]\nmax_length = true", True, "max_length"),
        ("[cw_text]\nmemory_channel = 0", True, "memory_channel"),
        ('[cw_text]\nmemory_channel = "5"', True, "memory_channel"),
        ('[cw_text]\nmemory_type = "keyboard"', True, "memory_type"),
        ('[cw_text]\nmemory_type = ["text"]', True, "memory_type"),
        ('[cw_text]\nmemory_type = { value = "text" }', True, "memory_type"),
        ("[cw_text]\nchannel = 5", True, "channel"),
        ("[cw_text]\nsupported = true", False, "send_cw"),
    ],
    ids=[
        "supported-not-bool",
        "max-length-zero",
        "max-length-bool",
        "channel-out-of-range",
        "channel-not-int",
        "unknown-memory-type",
        "memory-type-array",
        "memory-type-table",
        "unknown-key",
        "supported-without-send-cw",
    ],
)
def test_malformed_cw_text_rejected(
    tmp_path: Path, section: str, send_cw: bool, fragment: str
) -> None:
    with pytest.raises(RigLoadError) as excinfo:
        _load(tmp_path, section, send_cw=send_cw)
    message = str(excinfo.value)
    assert "cw_text_test.toml" in message
    assert "[cw_text]" in message
    assert fragment in message


@pytest.mark.parametrize("cls", [RigConfig, RadioProfile])
def test_cw_text_does_not_shift_positional_arguments(cls: type) -> None:
    field = {f.name: f for f in dataclasses.fields(cls)}["cw_text"]
    assert field.kw_only is True


# ── Capability endpoints (handler level) ─────────────────────────


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ftx1.toml", {"supported": False, "maxLength": 50, "memoryChannel": 5}),
        ("ic7300.toml", {"supported": True, "maxLength": 512, "memoryChannel": None}),
        ("tx500.toml", {"supported": False, "maxLength": 512, "memoryChannel": None}),
    ],
)
async def test_capability_endpoints_publish_cw_text(name: str, expected: dict) -> None:
    profile = load_rig(RIGS / name).to_profile()
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
        if path == "/api/v1/info":
            caps = payload["capabilities"]
            # Disabling text sending never touches the cw tag or hasCw.
            assert caps["hasCw"] is True
            assert "cw" in caps["tags"]
        else:
            caps = payload
            assert "cw" in caps["capabilities"]
        assert caps["cwText"] == expected


_FRONTEND_FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/runtime/adapters/__tests__/fixtures"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "fixture"),
    [
        ("ftx1.toml", "ftx1-capabilities.json"),
        ("ic7300.toml", "ic7300-capabilities.json"),
    ],
)
async def test_frontend_capability_fixtures_carry_the_served_cw_text(
    name: str, fixture: str
) -> None:
    # The frontend fixtures stand in for /api/v1/capabilities in browser
    # tests; their cwText must be what the real handler serves.
    profile = load_rig(RIGS / name).to_profile()
    radio = SimpleNamespace(
        model=profile.model,
        profile=profile,
        capabilities=set(profile.capabilities),
        connected=False,
        control_connected=False,
        radio_ready=False,
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = _Writer()
    await server._handle_http(writer, "GET", "/api/v1/capabilities", headers={})  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    served = json.loads(text[text.index("\r\n\r\n") + 4 :])
    stored = json.loads((_FRONTEND_FIXTURES / fixture).read_text(encoding="utf-8"))
    assert stored["cwText"] == served["cwText"]


def test_capabilities_contract_requires_cw_text() -> None:
    assert "cwText" in RESPONSE_FIELD_CONTRACTS["/api/v1/capabilities"]["required"]


# ── Command introspection agrees with cwText ─────────────────────


@pytest.mark.parametrize(
    ("name", "supported", "expected"),
    [
        ("ftx1.toml", None, False),
        ("ftx1.toml", True, True),
        ("ic7300.toml", None, True),
        ("ic7300.toml", False, False),
    ],
    ids=["ftx1-shipped", "ftx1-enabled", "ic7300-shipped", "ic7300-disabled"],
)
def test_supports_send_cw_text_follows_cw_text_supported(
    name: str, supported: bool | None, expected: bool
) -> None:
    rig = load_rig(RIGS / name)
    if supported is not None:
        rig = dataclasses.replace(
            rig, cw_text=dataclasses.replace(rig.cw_text, supported=supported)
        )
    profile = rig.to_profile()
    assert supports_callable(profile, "send_cw_text") is expected
    # Stopping stays available wherever stop_cw exists or KY can stop.
    assert supports_callable(profile, "stop_cw_text") is supports_callable(
        load_rig(RIGS / name).to_profile(), "stop_cw_text"
    )


def test_yaesu_radio_supports_command_follows_cw_text_supported() -> None:
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

    rig = load_rig(RIGS / "ftx1.toml")
    assert (
        YaesuCatRadio("/dev/null", profile=rig).supports_command("send_cw_text")
        is False
    )
    enabled = dataclasses.replace(
        rig, cw_text=dataclasses.replace(rig.cw_text, supported=True)
    )
    assert (
        YaesuCatRadio("/dev/null", profile=enabled).supports_command("send_cw_text")
        is True
    )
