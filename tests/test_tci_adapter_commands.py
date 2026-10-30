"""C1 RED: TCI adapter command and mode tables (MOR-3821, spec 5.5/5.6)."""

import pytest

from rigplane.core.types import Mode
from rigplane.tci_adapter import commands as cmds


def test_protocol_identity_constants_pinned() -> None:
    assert cmds.TCI_PROTOCOL_NAME == "ExpertSDR3"
    assert cmds.TCI_PROTOCOL_VERSION == "2.1"
    assert cmds.TCI_DEFAULT_HOST == "127.0.0.1"
    assert cmds.TCI_DEFAULT_PORT == 50001
    assert cmds.TCI_TRX_COUNT == 1
    assert cmds.TCI_CHANNELS_COUNT == 2


@pytest.mark.parametrize(
    ("tci_name", "mode", "data"),
    [
        ("AM", Mode.AM, False),
        ("LSB", Mode.LSB, False),
        ("USB", Mode.USB, False),
        ("CW", Mode.CW, False),
        ("FM", Mode.FM, False),
        ("WFM", Mode.WFM, False),
        ("DIGL", Mode.LSB, True),
        ("DIGU", Mode.USB, True),
    ],
)
def test_tci_to_core_mode_table(tci_name: str, mode: Mode, data: bool) -> None:
    assert cmds.TCI_TO_CORE_MODE[tci_name] == (mode, data)


@pytest.mark.parametrize(
    ("mode", "data", "tci_name"),
    [
        (Mode.AM, False, "AM"),
        (Mode.LSB, False, "LSB"),
        (Mode.USB, False, "USB"),
        (Mode.CW, False, "CW"),
        (Mode.FM, False, "FM"),
        (Mode.WFM, False, "WFM"),
        (Mode.LSB, True, "DIGL"),
        (Mode.USB, True, "DIGU"),
    ],
)
def test_core_to_tci_mode_table(mode: Mode, data: bool, tci_name: str) -> None:
    assert cmds.CORE_TO_TCI_MODE[(mode, data)] == tci_name


def test_every_mode_member_is_classified() -> None:
    """A new Mode member fails here until the adapter classifies it (5.6)."""
    covered = set(cmds.CORE_TO_TCI_MODE) | {
        (mode, flag) for mode in cmds.UNMAPPED_CORE_MODES for flag in (False, True)
    }
    for mode in Mode:
        assert (mode, False) in covered, f"{mode} is not classified"
        assert (mode, True) in covered or mode in cmds.UNMAPPED_CORE_MODES


def test_unmapped_modes_are_exactly_the_non_tci_ones() -> None:
    assert cmds.UNMAPPED_CORE_MODES == frozenset(
        {Mode.RTTY, Mode.RTTY_R, Mode.CW_R, Mode.PSK, Mode.PSK_R, Mode.DV}
    )


def test_modulations_list_is_tci_ordered_intersection() -> None:
    assert cmds.modulations_list(("USB", "LSB", "AM", "CW", "FM"), data_capable=True) == (
        "AM",
        "LSB",
        "USB",
        "CW",
        "FM",
        "DIGL",
        "DIGU",
    )
    assert cmds.modulations_list(("USB", "LSB", "AM", "CW", "FM"), data_capable=False) == (
        "AM",
        "LSB",
        "USB",
        "CW",
        "FM",
    )


def test_modulations_list_drops_unmapped_profile_modes() -> None:
    assert cmds.modulations_list(("USB", "RTTY", "PSK", "DV"), data_capable=True) == (
        "USB",
        "DIGU",
    )


def test_modulations_list_keeps_wfm_when_profile_has_it() -> None:
    assert "WFM" in cmds.modulations_list(("WFM", "FM", "AM"), data_capable=False)
    assert "DIGL" not in cmds.modulations_list(("USB",), data_capable=True)
    assert "DIGU" not in cmds.modulations_list(("LSB",), data_capable=True)
