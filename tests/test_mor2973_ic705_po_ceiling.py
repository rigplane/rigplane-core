"""MOR-2973: the IC-705 Po meter reads in watts of the current power ceiling.

The IC-705's maximum transmit power depends on the power source, 10 W on an
external 13.8 V supply and 5 W on the battery pack, lowered by that source's
Max TX Power setting, and the Po meter shows that maximum (IC-705 Basic
Manual A7560D-1EX-9 p.3-10, p.8-4). RigPlane reads the source (``1A 0B``)
and both settings (``1A 05 0036``/``0037``) (IC-705 CI-V Reference Guide
(A7560-8EX-1, Jul.2020) p.14, p.5) and scales the profile's Po table, stated
at the 10 W rating, by the ceiling over the rating.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.radio import IcomRadio
from rigplane.rig_loader import RigLoadError, load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.types import CivFrame
from test_radio import MockTransport
from test_rig_loader import _MINIMAL_TOML, _write_toml

IC705_PATH = Path(__file__).resolve().parent.parent / "rigs" / "ic705.toml"

SOURCE = "global.tx_state.power_source"
BATTERY_W = "global.operator_controls.max_tx_power_battery_w"
EXTERNAL_W = "global.operator_controls.max_tx_power_external_w"
PO = "global.meters.power"
PO_FULL = b"\x02\x13"  # 15 11 0213 = 100 %


def _radio(model: str = "IC-705") -> IcomRadio:
    radio = IcomRadio("192.168.1.100", model=model)
    radio._civ_transport = MockTransport()
    radio._ctrl_transport = radio._civ_transport
    radio._connected = True
    return radio


def _feed(radio: IcomRadio, command: int, sub: int, data: bytes) -> None:
    radio._civ_runtime._update_state_cache_from_frame(
        CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=radio._profile.civ_addr,
            command=command,
            sub=sub,
            data=data,
        )
    )


def _value(radio: IcomRadio, path: str) -> object:
    return radio._state_store.snapshot().field(path).value


@pytest.mark.parametrize(("code", "source"), [(0x00, "external"), (0x01, "battery")])
def test_power_source_reply_names_the_source(code: int, source: str) -> None:
    radio = _radio()
    _feed(radio, 0x1A, 0x0B, bytes([code]))
    assert _value(radio, SOURCE) == source
    radio._connected = False


@pytest.mark.parametrize(
    ("path", "control", "code", "watts"),
    [
        *[
            pytest.param(BATTERY_W, b"\x00\x36", code, watts, id=f"battery-{watts}")
            for code, watts in enumerate((0.5, 1.0, 2.5, 5.0))
        ],
        *[
            pytest.param(EXTERNAL_W, b"\x00\x37", code, watts, id=f"external-{watts}")
            for code, watts in enumerate((0.5, 1.0, 2.5, 5.0, 10.0))
        ],
    ],
)
def test_max_tx_power_reply_reads_in_watts(
    path: str, control: bytes, code: int, watts: float
) -> None:
    radio = _radio()
    _feed(radio, 0x1A, 0x05, control + bytes([code]))
    assert _value(radio, path) == watts
    radio._connected = False


@pytest.mark.parametrize(
    ("command", "sub", "data", "path"),
    [
        pytest.param(0x1A, 0x0B, b"\x02", SOURCE, id="source-02"),
        # The battery setting ends at 03 = 5 W (guide p.5).
        pytest.param(0x1A, 0x05, b"\x00\x36\x04", BATTERY_W, id="battery-04"),
    ],
)
def test_an_unlisted_code_reads_as_unknown(
    command: int, sub: int, data: bytes, path: str
) -> None:
    radio = _radio()
    _feed(radio, command, sub, data)
    assert _value(radio, path) is None
    radio._connected = False


def test_a_profile_without_power_sources_publishes_no_source() -> None:
    radio = _radio("IC-7300")
    _feed(radio, 0x1A, 0x0B, b"\x01")
    with pytest.raises(KeyError):
        _value(radio, SOURCE)
    radio._connected = False


@pytest.mark.parametrize(
    ("source", "control", "code", "po", "watts"),
    [
        pytest.param(0x01, b"\x00\x36", 0x03, PO_FULL, 5.0, id="battery-5w-full"),
        pytest.param(0x01, b"\x00\x36", 0x03, b"\x01\x43", 2.5, id="battery-5w-half"),
        pytest.param(0x00, b"\x00\x37", 0x04, PO_FULL, 10.0, id="external-10w-full"),
        pytest.param(0x00, b"\x00\x37", 0x02, PO_FULL, 2.5, id="external-2.5w-full"),
    ],
)
def test_po_reads_in_watts_of_the_current_ceiling(
    source: int, control: bytes, code: int, po: bytes, watts: float
) -> None:
    radio = _radio()
    _feed(radio, 0x1A, 0x0B, bytes([source]))
    _feed(radio, 0x1A, 0x05, control + bytes([code]))
    _feed(radio, 0x15, 0x11, po)
    assert _value(radio, PO) == pytest.approx(watts)
    radio._connected = False


@pytest.mark.parametrize(
    "replies",
    [
        pytest.param([], id="nothing-read"),
        pytest.param([(0x0B, b"\x01")], id="source-only"),
        pytest.param([(0x05, b"\x00\x36\x03")], id="ceiling-only"),
        pytest.param(
            [(0x0B, b"\x01"), (0x05, b"\x00\x37\x02")], id="other-source-ceiling"
        ),
        pytest.param([(0x0B, b"\x01"), (0x05, b"\x00\x36\x04")], id="unlisted-code"),
    ],
)
def test_po_stays_at_the_rating_until_the_ceiling_is_known(
    replies: list[tuple[int, bytes]],
) -> None:
    radio = _radio()
    for sub, data in replies:
        _feed(radio, 0x1A, sub, data)
    _feed(radio, 0x15, 0x11, PO_FULL)
    assert _value(radio, PO) == pytest.approx(10.0)
    radio._connected = False


@pytest.mark.parametrize(
    ("path", "wire"),
    [
        (SOURCE, (0x1A, 0x0B, b"")),
        (BATTERY_W, (0x1A, 0x05, b"\x00\x36")),
        (EXTERNAL_W, (0x1A, 0x05, b"\x00\x37")),
    ],
)
def test_the_ic705_polls_the_source_and_both_ceilings(
    path: str, wire: tuple[int, int, bytes]
) -> None:
    profile = load_rig(IC705_PATH).to_profile()
    field = FieldPath.parse(path)
    assert field in profile.state_acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(profile)(field)
    assert query is not None
    assert (query.command, query.sub, query.data) == wire


_RATED = "[power]\nmax_watts = 10\n\n"
_SOURCES = '[power.sources]\n0 = "external"\n1 = "battery"\n\n'
_CEILINGS = (
    "[power.ceilings_w.external]\n4 = 10.0\n\n[power.ceilings_w.battery]\n3 = 5.0\n\n"
)
_EXTERNAL_ONLY = (
    '[power.sources]\n0 = "external"\n\n[power.ceilings_w.external]\n4 = 10.0\n\n'
)


def _load(tmp_path: Path, power: str, commands: str = ""):
    toml = _MINIMAL_TOML.replace("[commands]\n", f"{power}[commands]\n{commands}")
    return load_rig(_write_toml(tmp_path, toml))


def test_power_tables_load_by_code(tmp_path: Path) -> None:
    profile = _load(tmp_path, _RATED + _SOURCES + _CEILINGS).to_profile()
    assert profile.power_sources == {0: "external", 1: "battery"}
    assert profile.power_ceilings_w == {"external": {4: 10.0}, "battery": {3: 5.0}}


def test_an_absent_power_source_getter_needs_no_table(tmp_path: Path) -> None:
    profile = _load(
        tmp_path, _RATED, 'get_power_source = { absent = "not on this radio" }\n'
    ).to_profile()
    assert profile.power_sources is None


@pytest.mark.parametrize(
    ("power", "commands", "match"),
    [
        pytest.param(_RATED + _SOURCES, "", "come together", id="sources-only"),
        pytest.param(_RATED + _CEILINGS, "", "come together", id="ceilings-only"),
        pytest.param(_SOURCES + _CEILINGS, "", "max_watts", id="no-rating"),
        pytest.param(
            _RATED
            + '[power.sources]\n0 = "solar"\n\n[power.ceilings_w.solar]\n0 = 1.0\n\n',
            "",
            "is not one of",
            id="unknown-source-name",
        ),
        pytest.param(
            _RATED + _EXTERNAL_ONLY + "[power.ceilings_w.battery]\n3 = 5.0\n\n",
            "",
            "non-empty table",
            id="table-for-unnamed-source",
        ),
        pytest.param(
            _RATED + _SOURCES + _CEILINGS.replace("10.0", "12.0"),
            "",
            "is not a wattage",
            id="above-the-rating",
        ),
        pytest.param(
            _RATED + _SOURCES + "[power.ceilings_w.external]\n4 = 10.0\n\n",
            "",
            "has no table for",
            id="named-source-without-table",
        ),
        pytest.param(
            _RATED + _EXTERNAL_ONLY.replace("0 = ", "x = ", 1),
            "",
            "is not a code",
            id="non-numeric-code",
        ),
        pytest.param(
            _RATED + _EXTERNAL_ONLY.replace("0 = ", "256 = ", 1),
            "",
            "is not one byte",
            id="code-above-one-byte",
        ),
        pytest.param(
            _RATED,
            "get_power_source = [0x1A, 0x0B]\n",
            "get_power_source needs",
            id="source-getter-without-table",
        ),
        pytest.param(
            _RATED + _EXTERNAL_ONLY,
            "get_max_tx_power_battery = [0x1A, 0x05, 0x00, 0x36]\n",
            "get_max_tx_power_battery needs 'battery'",
            id="ceiling-getter-without-its-source",
        ),
    ],
)
def test_power_table_errors(
    tmp_path: Path, power: str, commands: str, match: str
) -> None:
    with pytest.raises(RigLoadError, match=match):
        _load(tmp_path, power, commands)
