"""MOR-3678 (#4317 gate review): CW text support follows what the backend can send.

Each protocol's ``send_cw_text`` needs different profile facts:

* CI-V (Icom) sends the text itself with ``send_cw`` (0x17). Without a
  ``[cw_text]`` section the profile follows its ``send_cw`` command, as
  before.
* Yaesu CAT writes the text into a keyer memory (``write_keyer_memory``,
  KM, FTX-1 CAT 2508-C printed p.17) and plays it (``send_cw``, KY,
  printed p.18). Without an explicit ``[cw_text] supported = true`` it is
  unsupported; an explicit enable needs ``memory_channel`` 1-5 and both
  commands writable, or the profile does not load.
* Kenwood CAT has no ``send_cw_text`` in core, so it is never supported and
  an explicit enable does not load.

The invariant test checks every shipped profile: what the API publishes,
what ``supports_callable`` answers and what the backend does agree.
Transports are mocked; nothing reaches a port and nothing is keyed.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.profiles import CwTextSpec, CwTextUnsupportedError
from rigplane.rig_loader import RigConfig, RigLoadError, load_rig
from rigplane.runtime.callable_support import supports_callable
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import _serialize_cw_text

RIGS = Path(__file__).resolve().parents[1] / "rigs"
SHIPPED = sorted(p.name for p in RIGS.glob("*.toml") if not p.name.startswith("_"))

_FTX1_CW_TEXT = re.compile(r"^\[cw_text\]\n(?:(?!\[).*\n)*", re.MULTILINE)


def _variant(tmp_path: Path, name: str, text: str) -> RigConfig:
    for shared in RIGS.glob("_*.toml"):
        shutil.copy(shared, tmp_path / shared.name)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return load_rig(path)


def _ftx1_text(cw_text: str | None) -> str:
    text = (RIGS / "ftx1.toml").read_text(encoding="utf-8")
    assert _FTX1_CW_TEXT.search(text), "ftx1.toml lost its [cw_text] section"
    return _FTX1_CW_TEXT.sub("" if cw_text is None else cw_text + "\n", text)


def _drop_command(text: str, name: str) -> str:
    pattern = re.compile(rf"^{name}\s*=.*\n", re.MULTILINE)
    assert pattern.search(text), name
    return pattern.sub("", text)


# ── Yaesu ────────────────────────────────────────────────────────


def test_yaesu_profile_without_cw_text_section_is_unsupported(tmp_path: Path) -> None:
    rig = _variant(tmp_path, "yaesu_no_section.toml", _ftx1_text(None))
    assert rig.cw_text == CwTextSpec(False, 512, None, None)
    assert supports_callable(rig.to_profile(), "send_cw_text") is False


def test_yaesu_section_without_supported_stays_unsupported(tmp_path: Path) -> None:
    rig = _variant(
        tmp_path,
        "yaesu_no_supported.toml",
        _ftx1_text("[cw_text]\nmax_length = 50\nmemory_channel = 5"),
    )
    assert rig.cw_text == CwTextSpec(False, 50, 5, None)


def test_complete_yaesu_enable_loads(tmp_path: Path) -> None:
    rig = _variant(
        tmp_path,
        "yaesu_enabled.toml",
        _ftx1_text("[cw_text]\nsupported = true\nmax_length = 50\nmemory_channel = 1"),
    )
    assert rig.cw_text == CwTextSpec(True, 50, 1, None)


@pytest.mark.parametrize(
    ("section", "dropped", "key"),
    [
        ("[cw_text]\nsupported = true", None, "[cw_text].memory_channel"),
        (
            "[cw_text]\nsupported = true\nmemory_channel = 6",
            None,
            "[cw_text].memory_channel",
        ),
        (
            "[cw_text]\nsupported = true\nmemory_channel = 5",
            "write_keyer_memory",
            "write_keyer_memory",
        ),
        ("[cw_text]\nsupported = true\nmemory_channel = 5", "send_cw", "send_cw"),
    ],
    ids=["no-channel", "channel-6", "no-km-command", "no-ky-command"],
)
def test_incomplete_yaesu_enable_does_not_load(
    tmp_path: Path, section: str, dropped: str | None, key: str
) -> None:
    text = _ftx1_text(section)
    if dropped is not None:
        text = _drop_command(text, dropped)
    with pytest.raises(RigLoadError) as excinfo:
        _variant(tmp_path, "yaesu_incomplete.toml", text)
    message = str(excinfo.value)
    assert "yaesu_incomplete.toml" in message
    assert key in message


def test_yaesu_memory_channel_outside_keyer_memories_does_not_load(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        RigLoadError, match=r"yaesu_ch\.toml.*\[cw_text\]\.memory_channel"
    ):
        _variant(
            tmp_path,
            "yaesu_ch.toml",
            _ftx1_text("[cw_text]\nsupported = false\nmemory_channel = 9"),
        )


def test_read_only_keyer_memory_command_does_not_enable(tmp_path: Path) -> None:
    text = _ftx1_text("[cw_text]\nsupported = true\nmemory_channel = 5")
    text = re.sub(
        r"^write_keyer_memory\s*=.*$",
        'write_keyer_memory = { cat = { read = "KM5;" } }',
        text,
        flags=re.MULTILINE,
    )
    with pytest.raises(RigLoadError, match=r"yaesu_ro\.toml.*write_keyer_memory"):
        _variant(tmp_path, "yaesu_ro.toml", text)


# ── Kenwood ──────────────────────────────────────────────────────


def _tx500_with_send_cw(cw_text: str = "") -> str:
    text = (RIGS / "tx500.toml").read_text(encoding="utf-8")
    text = text.replace(
        "[commands]\n",
        '[commands]\nsend_cw = { cat = { write = "KY {text};" } }\n',
        1,
    )
    return text + ("\n" + cw_text + "\n" if cw_text else "")


def test_kenwood_profile_with_send_cw_is_still_unsupported(tmp_path: Path) -> None:
    rig = _variant(tmp_path, "kenwood_send_cw.toml", _tx500_with_send_cw())
    assert rig.cw_text.supported is False


def test_kenwood_explicit_enable_does_not_load(tmp_path: Path) -> None:
    with pytest.raises(RigLoadError) as excinfo:
        _variant(
            tmp_path,
            "kenwood_enabled.toml",
            _tx500_with_send_cw("[cw_text]\nsupported = true"),
        )
    message = str(excinfo.value)
    assert "kenwood_enabled.toml" in message
    assert "[cw_text].supported" in message
    assert "kenwood_cat" in message


# ── Invariant over every shipped profile ─────────────────────────


async def _backend_sends(rig: RigConfig) -> bool:
    """Whether the profile's backend gets past admission for ``CQ``."""
    if rig.protocol_type == "yaesu_cat":
        radio = YaesuCatRadio("/dev/null", profile=rig)
        radio._transport._connected = True  # noqa: SLF001
        radio._transport.write = AsyncMock()  # noqa: SLF001
        radio._transport.query = AsyncMock(return_value="")  # noqa: SLF001
        sent = lambda: radio._transport.write.await_count > 0  # noqa: E731, SLF001
        send = radio.send_cw_text
    elif rig.protocol_type == "civ":
        icom = IcomRadio("192.0.2.1", profile=rig.to_profile())
        icom._check_connected = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
        icom._send_civ_expect = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
            return_value=SimpleNamespace(command=0xFB)
        )
        sent = lambda: icom._send_civ_expect.await_count > 0  # noqa: E731, SLF001
        send = icom.send_cw_text
    else:
        # Kenwood CAT: core has no backend that sends CW text.
        assert rig.protocol_type == "kenwood_cat", rig.protocol_type
        return False
    try:
        await send("CQ")
    except CwTextUnsupportedError:
        assert not sent()
        return False
    assert sent()
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SHIPPED)
async def test_published_support_matches_callable_support_and_execution(
    name: str,
) -> None:
    rig = load_rig(RIGS / name)
    profile = rig.to_profile()
    published = _serialize_cw_text(profile)["supported"]
    assert published is supports_callable(profile, "send_cw_text")
    assert published is await _backend_sends(rig)


def test_declared_absent_keyer_memory_command_does_not_enable(tmp_path: Path) -> None:
    text = _ftx1_text("[cw_text]\nsupported = true\nmemory_channel = 5")
    text = re.sub(
        r"^write_keyer_memory\s*=.*$",
        'write_keyer_memory = { absent = "test: declared absent" }',
        text,
        flags=re.MULTILINE,
    )
    with pytest.raises(RigLoadError, match=r"yaesu_absent\.toml.*write_keyer_memory"):
        _variant(tmp_path, "yaesu_absent.toml", text)


# ── Commands the protocol's backend can execute ──────────────────


@pytest.mark.parametrize(
    ("command", "civ_bytes"),
    [("send_cw", "[0x17]"), ("write_keyer_memory", "[0x1A, 0x02]")],
)
def test_yaesu_enable_needs_cat_commands_not_civ_bytes(
    tmp_path: Path, command: str, civ_bytes: str
) -> None:
    # A CI-V spec in a Yaesu profile is not something YaesuCatRadio can
    # write: KM would land and KY would never play.
    text = _ftx1_text("[cw_text]\nsupported = true\nmemory_channel = 5")
    text = re.sub(
        rf"^{command}\s*=.*$", f"{command} = {civ_bytes}", text, flags=re.MULTILINE
    )
    with pytest.raises(RigLoadError) as excinfo:
        _variant(tmp_path, "yaesu_civ_spec.toml", text)
    message = str(excinfo.value)
    assert "yaesu_civ_spec.toml" in message
    assert command in message


def _civ_with_cat_send_cw(tmp_path: Path, cw_text: str) -> RigConfig:
    from test_cw_text_capability import _MINIMAL_TOML

    path = tmp_path / "civ_cat_send_cw.toml"
    path.write_text(
        _MINIMAL_TOML.format(
            commands_extra='send_cw = { cat = { write = "KY {text};" } }',
            cw_text=cw_text,
        ),
        encoding="utf-8",
    )
    return load_rig(path)


def test_civ_profile_with_a_cat_send_cw_is_unsupported(tmp_path: Path) -> None:
    rig = _civ_with_cat_send_cw(tmp_path, "")
    assert rig.cw_text.supported is False
    assert supports_callable(rig.to_profile(), "send_cw_text") is False


def test_civ_enable_with_a_cat_send_cw_does_not_load(tmp_path: Path) -> None:
    with pytest.raises(RigLoadError) as excinfo:
        _civ_with_cat_send_cw(tmp_path, "[cw_text]\nsupported = true")
    message = str(excinfo.value)
    assert "civ_cat_send_cw.toml" in message
    assert "[cw_text].supported" in message
    assert "send_cw" in message
