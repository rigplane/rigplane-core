"""WSJT-X DATA-mode / MOD-input glue shared by every keying ingress.

``rigplane.audio.route.apply_wsjtx_data_mod_input`` is the one extracted
implementation of the rule that used to live in ``rigctld`` only
(MOR-3821 P0d): for an explicit numeric DATA sub-mode, select the
profile's MOD input for that sub-mode; anything else (a plain boolean
DATA flag, no input source, a radio without that setter) is a no-op.
``profile_data_mode_count`` is the canonical bound both call sites use.
"""

from __future__ import annotations

from types import SimpleNamespace

from rigplane.audio.route import (
    apply_wsjtx_data_mod_input,
    profile_data_mode_count,
)


class _ModInputRadio:
    """Hand fake: records DATA MOD-input selections, nothing else."""

    def __init__(self, *, data_mode_count: int = 3) -> None:
        self.calls: list[tuple[str, int]] = []
        self.profile = SimpleNamespace(data_mode_count=data_mode_count)

    async def set_data2_mod_input(self, source: int) -> None:
        self.calls.append(("set_data2_mod_input", source))


async def test_numeric_data_mode_selects_the_matching_mod_input() -> None:
    radio = _ModInputRadio()
    await apply_wsjtx_data_mod_input(radio, data_mode=2, source=5)
    assert radio.calls == [("set_data2_mod_input", 5)]


async def test_boolean_data_mode_selects_no_mod_input() -> None:
    radio = _ModInputRadio()
    await apply_wsjtx_data_mod_input(radio, data_mode=True, source=5)
    assert radio.calls == []


async def test_missing_source_selects_no_mod_input() -> None:
    radio = _ModInputRadio()
    await apply_wsjtx_data_mod_input(radio, data_mode=2, source=None)
    assert radio.calls == []


async def test_missing_setter_is_a_noop() -> None:
    radio = _ModInputRadio()
    await apply_wsjtx_data_mod_input(radio, data_mode=3, source=5)
    assert radio.calls == []


async def test_plain_radio_without_a_setter_is_a_noop() -> None:
    finished: list[str] = []
    await apply_wsjtx_data_mod_input(
        SimpleNamespace(profile=SimpleNamespace(data_mode_count=1)),
        data_mode=1,
        source=5,
    )
    finished.append("returned")
    assert finished == ["returned"]


def test_profile_data_mode_count_bounds_the_sub_mode() -> None:
    assert profile_data_mode_count(_ModInputRadio(data_mode_count=3)) == 3
    assert profile_data_mode_count(_ModInputRadio(data_mode_count=1)) == 1
    assert profile_data_mode_count(SimpleNamespace(profile=None)) == 1
    assert profile_data_mode_count(SimpleNamespace()) == 1
