"""Declared-absent reads are asked via borrowed sibling bytes (MOR-4464).

A read the profile marks ``absent`` is a belief, and the probe tests
beliefs against the real radio. Every declared read of every shipped
profile is either asked or recorded in the plan's ``skipped`` with a
reason from the closed enum; nothing is in both, nothing is missing.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from rigplane.commands.command_spec import (
    AbsentCommandSpec,
    CatCommandSpec,
    CivCommandSpec,
)
from rigplane.probe.model import SKIP_REASONS
from rigplane.probe.readlist import (
    _BORROW_READ_SAFE_COMMANDS,
    _REFUSED_COMMANDS,
    build_allowed_bodies,
    build_read_plan,
    is_forbidden,
)
from rigplane.profiles.rig_loader import RigConfig, load_rig

REPO_ROOT = Path(__file__).resolve().parents[2]
RIGS_DIR = REPO_ROOT / "rigs"


def _load(name: str) -> RigConfig:
    return load_rig(RIGS_DIR / f"{name}.toml")


def _siblings() -> list[RigConfig]:
    return [
        load_rig(path)
        for path in sorted(RIGS_DIR.glob("*.toml"))
        if not path.name.startswith("_") and not path.name.endswith(".draft.toml")
    ]


def _probe_toml_names() -> list[str]:
    return [
        path.stem
        for path in sorted(RIGS_DIR.glob("*.toml"))
        if not path.name.startswith("_")
        and not path.name.endswith(".draft.toml")
        and load_rig(path).protocol_type in ("civ", "yaesu_cat")
    ]


def _declared_reads(config: RigConfig) -> set[str]:
    """Every name the profile declares as a read, present or absent."""
    reads: set[str] = set()
    for name, spec in config.commands.items():
        if isinstance(spec, CivCommandSpec) and name.startswith("get_"):
            reads.add(name)
        elif isinstance(spec, CatCommandSpec) and spec.read:
            reads.add(name)
        elif isinstance(spec, AbsentCommandSpec) and name.startswith("get_"):
            reads.add(name)
    return reads


@pytest.mark.parametrize("toml_name", _probe_toml_names())
@pytest.mark.parametrize("include_private", [False, True])
def test_every_declared_read_is_asked_or_skipped_with_reason(
    toml_name: str, include_private: bool
) -> None:
    """No declared read silently disappears when a profile is edited (MOR-4464)."""
    config = _load(toml_name)
    plan = build_read_plan(
        config, include_private=include_private, siblings=_siblings()
    )
    asked = {item.name for item in plan.asked}
    skipped = {entry.name: entry for entry in plan.skipped}
    assert not (asked & set(skipped)), toml_name
    for name in _declared_reads(config):
        assert (name in asked) != (name in skipped), (toml_name, name)
    for entry in plan.skipped:
        assert entry.reason in SKIP_REASONS, (toml_name, entry)
        assert entry.request_hex is None or bytes.fromhex(entry.request_hex), (
            toml_name,
            entry,
        )


def test_g7250_absent_data_mode_is_asked_as_absent() -> None:
    plan = build_read_plan(_load("g7250"), siblings=_siblings())
    by_name = {item.name: item for item in plan.asked}
    assert "get_data_mode" in by_name
    assert by_name["get_data_mode"].expected == "absent"
    assert by_name["get_data_mode"].body == bytes((0x1A, 0x06))
    assert "bytes from profile(s)" in by_name["get_data_mode"].note


def test_g7250_native_present_read_is_asked_as_present() -> None:
    plan = build_read_plan(_load("g7250"), siblings=_siblings())
    by_name = {item.name: item for item in plan.asked}
    assert by_name["get_freq"].expected == "present"
    assert by_name["get_freq"].note == ""


def test_g7250_skipped_reasons() -> None:
    plan = build_read_plan(_load("g7250"), siblings=_siblings())
    skipped = {entry.name: entry for entry in plan.skipped}
    assert skipped["get_transceiver_status"].reason == "unsafe_family"
    assert skipped["get_command_error_fa"].reason == "response_code"
    assert skipped["get_command_ok_fb"].reason == "response_code"
    assert skipped["get_memory_contents"].reason == "private"


def test_g7250_memory_contents_asked_with_private_flag() -> None:
    plan = build_read_plan(
        _load("g7250"), include_private=True, siblings=_siblings()
    )
    by_name = {item.name: item for item in plan.asked}
    assert by_name["get_memory_contents"].expected == "absent"


def test_ic7300_memory_contents_asked_with_private_flag() -> None:
    plan = build_read_plan(
        _load("ic7300"), include_private=True, siblings=_siblings()
    )
    by_name = {item.name: item for item in plan.asked}
    assert by_name["get_memory_contents"].expected == "present"


def test_ic705_disagreeing_siblings_are_skipped_without_bytes() -> None:
    plan = build_read_plan(_load("ic705"), siblings=_siblings())
    skipped = {entry.name: entry for entry in plan.skipped}
    assert skipped["get_dual_watch"].reason == "no_unambiguous_bytes"
    assert skipped["get_dual_watch"].request_hex is None


def _synthetic_target(base: RigConfig, commands: dict) -> RigConfig:
    return dataclasses.replace(base, id="syn-target", model="SYN-TARGET", commands=commands)


def _synthetic_lender(base: RigConfig, commands: dict) -> RigConfig:
    return dataclasses.replace(base, id="syn-lender", model="SYN-LENDER", commands=commands)


def test_borrowed_action_shaped_entry_is_skipped_unsafe_family() -> None:
    """A borrowed speech byte (0x13) is an action, never a read (MOR-4464)."""
    base = _load("g7250")
    target = _synthetic_target(
        base, {"get_x": AbsentCommandSpec("synthetic: no X entry")}
    )
    lender = _synthetic_lender(base, {"get_x": CivCommandSpec((0x13, 0x01))})
    plan = build_read_plan(target, siblings=[lender])
    assert [item.name for item in plan.asked if item.name == "get_x"] == []
    skipped = {entry.name: entry for entry in plan.skipped}
    assert skipped["get_x"].reason == "unsafe_family"
    allowed = build_allowed_bodies(
        target.to_profile(),
        borrowed_bodies=[item.body for item in plan.asked],
    )
    assert is_forbidden(bytes((0x13, 0x01)), allowed)


def test_borrowed_split_family_entry_is_skipped_unsafe_family() -> None:
    """A borrowed split byte (0x0F) is an action, never a read (MOR-4464)."""
    base = _load("g7250")
    target = _synthetic_target(
        base, {"get_x": AbsentCommandSpec("synthetic: no X entry")}
    )
    lender = _synthetic_lender(base, {"get_x": CivCommandSpec((0x0F, 0x00))})
    plan = build_read_plan(target, siblings=[lender])
    skipped = {entry.name: entry for entry in plan.skipped}
    assert skipped["get_x"].reason == "unsafe_family"


def test_set_only_lender_lends_nothing() -> None:
    """Borrowing is same-name only: a lender's set_x never feeds get_x."""
    base = _load("g7250")
    target = _synthetic_target(
        base, {"get_x": AbsentCommandSpec("synthetic: no X entry")}
    )
    lender = _synthetic_lender(base, {"set_x": CivCommandSpec((0x13, 0x01))})
    plan = build_read_plan(target, siblings=[lender])
    skipped = {entry.name: entry for entry in plan.skipped}
    assert skipped["get_x"].reason == "no_unambiguous_bytes"


def test_borrow_safe_list_holds_no_refused_command() -> None:
    """The positive borrow list can never admit a refused family (MOR-4464)."""
    assert not (set(_BORROW_READ_SAFE_COMMANDS) & set(_REFUSED_COMMANDS))
