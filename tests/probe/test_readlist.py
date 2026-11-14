"""Read lists derived from profiles, and the no-TX-ever contract (MOR-4464 PR A).

The read list is the allow-list: the runner sends only what
``build_read_list`` produces, and every produced line must be a declared
profile read and never a write.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from rigplane.commands import build_cmd29_frame
from rigplane.commands import CONTROLLER_ADDR
from rigplane.profiles.rig_loader import RigConfig, load_rig
from rigplane.probe.readlist import (
    build_allowed_bodies,
    build_read_list,
    compile_cat_template,
    identity_requests,
    is_forbidden,
    snapshot_names,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RIGS_DIR = REPO_ROOT / "rigs"
FTX1 = RIGS_DIR / "ftx1.toml"
IC7610 = RIGS_DIR / "ic7610.toml"
G7250 = RIGS_DIR / "g7250.toml"


def _load(name: str) -> RigConfig:
    return load_rig(RIGS_DIR / f"{name}.toml")


def test_yaesu_list_is_every_profile_read_expanded() -> None:
    config = _load("ftx1")
    bodies = [item.body for item in build_read_list(config)]
    assert len(bodies) == len(set(bodies))
    assert b"ID;" in bodies
    assert b"FA;" in bodies
    assert b"MD0;" in bodies
    assert b"CN00;" in bodies
    assert b"CN10;" in bodies
    assert b"RM1;" in bodies
    assert b"RM8;" in bodies
    assert not [body for body in bodies if b"{" in body]


def test_yaesu_private_memory_reads_need_the_flag() -> None:
    config = _load("ftx1")
    default = {item.body for item in build_read_list(config)}
    assert not [body for body in default if re.match(rb"M[RT]", body)]
    private = {item.body for item in build_read_list(config, include_private=True)}
    assert b"MR00001;" in private
    assert b"MT00001;" in private
    assert default < private


def test_yaesu_every_line_passes_an_independent_read_matcher() -> None:
    from rigplane.commands.command_spec import CatCommandSpec

    config = _load("ftx1")
    reads = [
        spec.read
        for spec in config.commands.values()
        if isinstance(spec, CatCommandSpec) and spec.read
    ]
    matchers = [compile_cat_template(template) for template in reads]
    for item in build_read_list(config, include_private=True):
        line = item.body.decode("ascii")
        assert any(pattern.fullmatch(line) for pattern in matchers), line


def test_civ_list_covers_profile_gets_and_excludes_forbidden() -> None:
    raw = tomllib.loads(IC7610.read_text(encoding="utf-8"))
    config = load_rig(IC7610)
    profile = config.to_profile()

    items = build_read_list(config)
    allowed = build_allowed_bodies(profile)

    commands = dict(raw["commands"])
    overrides = commands.pop("overrides", {})
    effective = {**commands, **overrides}
    eligible = {
        key
        for key, value in effective.items()
        if key.startswith("get_")
        and isinstance(value, list)
        and value[0] not in (0x07, 0x1C)
        and not (value[0] == 0x1A and len(value) > 1 and value[1] == 0x00)
    }
    assert {item.name for item in items if item.kind == "get"} == eligible
    assert all(not is_forbidden(item.body, allowed) for item in items)

    refused = (
        "29001c0001",
        "29001800",
        "14010050",
        "1c0001",
        "1801",
        "1a000001",
        "0e01",
        "1701",
        "07",
        "07d0",
    )
    for hex_body in refused:
        assert is_forbidden(bytes.fromhex(hex_body), allowed), hex_body
    for hex_body in ("03", "2500", "1a050092"):
        assert not is_forbidden(bytes.fromhex(hex_body), allowed), hex_body

    # The 0x07 VFO-selection family is absent everywhere.
    assert bytes.fromhex("07") not in allowed
    assert not [item for item in items if item.body[:1] == b"\x07"]

    bodies = {item.body for item in items}
    for hex_body in ("2500", "2501", "2600", "2601", "1900"):
        assert bytes.fromhex(hex_body) in bodies, hex_body
    for hex_body in ("1a050999", "7f", "147e", "277f"):
        assert bytes.fromhex(hex_body) in bodies, hex_body
    assert bytes.fromhex("290003") in bodies
    assert bytes.fromhex("290103") in bodies

    # cmd29 wrapping matches the runtime's own builder byte for byte.
    sub_wrapped = next(item for item in items if item.name == "cmd29:get_preamp:1")
    expected = build_cmd29_frame(
        profile.civ_addr, CONTROLLER_ADDR, 0x16, sub=0x02, receiver=1
    )[4:-1]
    assert sub_wrapped.body == expected

    names = {item.name for item in items}
    assert "get_transceiver_status" not in names
    assert "get_memory_contents" not in names
    assert "get_vfo" not in names


def _protocol_of(config: RigConfig) -> str:
    return config.protocol_type


def _probe_toml_names() -> list[str]:
    names: list[str] = []
    for path in sorted(RIGS_DIR.glob("*.toml")):
        if path.name.startswith("_") or path.name.endswith(".draft.toml"):
            continue
        if load_rig(path).protocol_type in ("civ", "yaesu_cat"):
            names.append(path.stem)
    return names


@pytest.mark.parametrize("toml_name", _probe_toml_names())
def test_no_probe_body_is_an_ack_nak_echo(toml_name: str) -> None:
    """No read list entry starts with 0xFA or 0xFB (MOR-4464 PR A).

    Those bare bytes are the radio's own NAK/ACK response codes, not
    requests: profiles declare them as ``get_command_error_fa`` /
    ``get_command_ok_fb`` and the probe must never send them.
    """
    config = load_rig(RIGS_DIR / f"{toml_name}.toml")
    bad = [
        item
        for item in build_read_list(config, include_private=True)
        if item.body[:1] in (b"\xfa", b"\xfb")
    ]
    assert not bad


def test_cmd29_get_freq_only_on_dual_receiver_profiles() -> None:
    """The unconditional cmd29-wrapped get_freq is dual-receiver only (MOR-4464 PR A)."""
    g7250 = build_read_list(load_rig(G7250))
    assert not [
        item for item in g7250 if item.name.startswith("cmd29:get_freq:")
    ]

    ic7610 = build_read_list(load_rig(IC7610))
    wrapped = [
        (item.name, item.body.hex())
        for item in ic7610
        if item.name.startswith("cmd29:get_freq:")
    ]
    assert wrapped == [
        ("cmd29:get_freq:0", "290003"),
        ("cmd29:get_freq:1", "290103"),
    ]


def test_ic7610_read_list_otherwise_unchanged() -> None:
    """The IC-7610 list keeps its size: gating drops nothing there (MOR-4464 PR A)."""
    items = build_read_list(load_rig(IC7610))
    assert len(items) == 177


def test_refused_command_bytes_are_refused_even_when_allowed() -> None:
    hex_bodies = ("1c00", "1c0001", "1800", "07", "07d0", "0e01")
    allowed = frozenset(bytes.fromhex(hex_body) for hex_body in hex_bodies)
    for hex_body in hex_bodies:
        assert is_forbidden(bytes.fromhex(hex_body), allowed), hex_body


def test_identity_requests_per_protocol() -> None:
    assert [item.body for item in identity_requests(_load("ftx1"))] == [b"ID;"]
    ic7300 = [item.body for item in identity_requests(_load("ic7300"))]
    assert ic7300 == [bytes((0x19, 0x00))]
    g7250 = [item.body for item in identity_requests(_load("g7250"))]
    assert g7250 == [bytes((0x19, 0x00)), bytes((0x1D, 0x19))]


def test_snapshot_names_come_from_the_profile() -> None:
    names = snapshot_names(_load("ftx1"))
    assert "get_freq" in names and "get_ptt" in names
    produced = {item.name for item in build_read_list(_load("ftx1"))}
    assert set(names) <= produced
    civ_names = snapshot_names(_load("ic7300"))
    assert "get_freq" in civ_names and "get_mode" in civ_names


@pytest.mark.parametrize("toml_name", _probe_toml_names())
def test_no_tx_ever_and_every_line_a_declared_read(toml_name: str) -> None:
    """Contract: no produced line is a write, every one is a declared read."""
    from rigplane.commands.command_spec import CatCommandSpec

    config = load_rig(RIGS_DIR / f"{toml_name}.toml")
    items = build_read_list(config, include_private=True)
    assert items
    if _protocol_of(config) == "yaesu_cat":
        reads = [
            spec.read
            for spec in config.commands.values()
            if isinstance(spec, CatCommandSpec) and spec.read
        ]
        writes = [
            spec.write
            for spec in config.commands.values()
            if isinstance(spec, CatCommandSpec) and spec.write
        ]
        read_matchers = [compile_cat_template(t) for t in reads]
        write_matchers = [compile_cat_template(t) for t in writes]
        for item in items:
            line = item.body.decode("ascii")
            assert any(p.fullmatch(line) for p in read_matchers), line
            assert not any(p.fullmatch(line) for p in write_matchers), line
    else:
        allowed = build_allowed_bodies(config.to_profile(), include_private=True)
        for item in items:
            assert not is_forbidden(item.body, allowed), item.body.hex()
