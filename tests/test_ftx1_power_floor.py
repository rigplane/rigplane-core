"""MOR-2998: the FTX-1 RF power floor clamps the bottom of the range.

The FTX-1 CAT manual (FTX-1_CAT_OM_ENG_2508-C, p.21, PC POWER CONTROL)
gives P1=2 (SPA-1) 005-100 W: the radio's minimum is 5 W, so ``PC2000;``
is refused (bench, stand .152, 2026-09-28). The profile declares the
floor in ``[power].min_watts`` and the backend clamps a lower request up
to it before it reaches the wire.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.command_service import resolve_power_level_target
from rigplane.rig_loader import RigLoadError, load_rig

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_FTX1_TOML = _RIGS_DIR / "ftx1.toml"


@pytest.fixture()
def connected_ftx1():
    """A YaesuCatRadio on the real ftx1 profile with a mocked transport."""
    radio = YaesuCatRadio("/dev/null", profile=load_rig(_FTX1_TOML))
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    return radio


@pytest.mark.asyncio
async def test_normalized_bottom_sends_the_floor(connected_ftx1):
    # The bottom of the normalized range (0.0 over max_watts=100) resolves
    # to 0 W at the backend door; the 5 W floor must raise it to PC2005,
    # not the refused PC2000.
    native, _ = resolve_power_level_target(
        0.0, power_native_unit="watts", power_max_watts=100
    )
    assert native == 0
    await connected_ftx1.set_rf_power(native)
    connected_ftx1._transport.write.assert_called_once_with("PC2005;")


@pytest.mark.asyncio
async def test_native_below_the_floor_sends_the_floor(connected_ftx1):
    await connected_ftx1.set_power(3)
    connected_ftx1._transport.write.assert_called_once_with("PC2005;")


@pytest.mark.asyncio
async def test_above_the_floor_is_untouched(connected_ftx1):
    await connected_ftx1.set_power(7)
    connected_ftx1._transport.write.assert_called_once_with("PC2007;")


@pytest.mark.asyncio
async def test_profile_without_min_watts_keeps_todays_wire(connected_ftx1):
    config = replace(connected_ftx1._config, min_watts=None)
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    await radio.set_power(0)
    radio._transport.write.assert_called_once_with("PC2000;")


def test_ftx1_profile_loads_min_watts():
    config = load_rig(_FTX1_TOML)
    assert config.min_watts == 5
    assert config.to_profile().min_watts == 5


def test_loader_refuses_min_watts_above_max_watts(tmp_path):
    text = _FTX1_TOML.read_text(encoding="utf-8")
    patched = text.replace("min_watts = 5", "min_watts = 101")
    assert patched != text
    broken = tmp_path / "ftx1.toml"
    broken.write_text(patched, encoding="utf-8")
    with pytest.raises(RigLoadError, match=r"\[power\]\.min_watts"):
        load_rig(broken)
