"""MOR-2469 — rigctld routes level sets through the control-domain surface.

``YaesuRouting.set_level`` used to forward ``round(value)`` (IFSHIFT),
a hardcoded 300-1050 clamp (CWPITCH) or raw hamlib Hz codes (NOTCHF),
while the Yaesu backend validates against the profile's normalized
control domain. These tests pin the dispatch through the
:class:`~rigplane.core.radio_protocol.ControlDomainCapable` surface:
when the radio implements it, the routing snaps the requested hamlib
value onto the domain's display lattice (nearest, ties up) and hands
the backend the raw code; out-of-range values return ``EINVAL``
without calling the setter. Radios that do not implement the protocol
keep the legacy rounded passthrough, with the old CWPITCH clamp gone.
"""

from __future__ import annotations

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.radio_protocol import ControlDomainCapable
from rigplane.rigctld.contract import HamlibError
from rigplane.rigctld.routing import YaesuRouting


class _DomainRadio:
    """Radio double implementing ControlDomainCapable on the real FTX-1 math.

    ``snap_control_display`` / ``decode_control_raw`` delegate to a real
    :class:`YaesuCatRadio` built from the shipping ``ftx1.toml`` so the
    routing is exercised against the actual backend implementation; the
    setters only record.
    """

    def __init__(self) -> None:
        self._ftx1 = YaesuCatRadio("/dev/null", profile="ftx1")
        self.notch_calls: list[int] = []
        self.if_shift_calls: list[int] = []
        self.cw_pitch_calls: list[int] = []
        self.notch_state: tuple[bool, int] = (True, 1)

    def snap_control_display(self, control: str, display: str) -> int | None:
        return self._ftx1.snap_control_display(control, display)

    def decode_control_raw(self, control: str, raw: int) -> str | None:
        return self._ftx1.decode_control_raw(control, raw)

    async def get_manual_notch(self, receiver: int = 0) -> tuple[bool, int]:
        return self.notch_state

    async def set_notch_filter(self, level: int, receiver: int = 0) -> None:
        self.notch_calls.append(level)

    async def get_if_shift(self, receiver: int = 0) -> int:
        return 0

    async def set_if_shift(self, offset: int, receiver: int = 0) -> None:
        self.if_shift_calls.append(offset)

    async def get_cw_pitch(self) -> int:
        return 600

    async def set_cw_pitch(self, freq: int) -> None:
        self.cw_pitch_calls.append(freq)


class _NoDomainRadio:
    """Radio double without the ControlDomainCapable surface."""

    def __init__(self) -> None:
        self.notch_calls: list[int] = []
        self.if_shift_calls: list[int] = []
        self.cw_pitch_calls: list[int] = []
        self.notch_state: tuple[bool, int] = (True, 1)

    async def get_manual_notch(self, receiver: int = 0) -> tuple[bool, int]:
        return self.notch_state

    async def set_notch_filter(self, level: int, receiver: int = 0) -> None:
        self.notch_calls.append(level)

    async def get_if_shift(self, receiver: int = 0) -> int:
        return 0

    async def set_if_shift(self, offset: int, receiver: int = 0) -> None:
        self.if_shift_calls.append(offset)

    async def get_cw_pitch(self) -> int:
        return 600

    async def set_cw_pitch(self, freq: int) -> None:
        self.cw_pitch_calls.append(freq)


def _routing(radio: object) -> YaesuRouting:
    return YaesuRouting(radio, max_power_w=100.0)


def test_domain_double_satisfies_protocol() -> None:
    assert isinstance(_DomainRadio(), ControlDomainCapable)


def test_no_domain_double_does_not_satisfy_protocol() -> None:
    assert not isinstance(_NoDomainRadio(), ControlDomainCapable)


# ---------------------------------------------------------------------------
# NOTCHF — hamlib Hz → raw code through the linear notch domain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notchf_set_hz_snaps_to_raw_code() -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("NOTCHF", 1500.0)
    assert resp.ok
    assert radio.notch_calls == [150]


@pytest.mark.asyncio
async def test_notchf_set_tie_rounds_up() -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("NOTCHF", 1505.0)
    assert resp.ok
    assert radio.notch_calls == [151]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [5.0, 3300.0, -10.0])
async def test_notchf_set_out_of_range_einval_no_call(value: float) -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("NOTCHF", value)
    assert resp.error == int(HamlibError.EINVAL)
    assert not resp.ok
    assert radio.notch_calls == []


@pytest.mark.asyncio
async def test_notchf_get_decodes_raw_index_to_hz() -> None:
    radio = _DomainRadio()
    radio.notch_state = (True, 150)
    resp = await _routing(radio).get_level("NOTCHF")
    assert resp.ok
    assert resp.values == ["1500"]


@pytest.mark.asyncio
async def test_notchf_get_undecodable_index_falls_back_to_raw() -> None:
    """Index 0 is outside the raw domain — decode returns None → legacy."""
    radio = _DomainRadio()
    radio.notch_state = (True, 0)
    resp = await _routing(radio).get_level("NOTCHF")
    assert resp.ok
    assert resp.values == ["0"]


# ---------------------------------------------------------------------------
# IFSHIFT — off-lattice Hz snaps onto the ±1200 step-20 lattice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "applied"),
    [(15.0, 20), (-10.0, 0), (-15.0, -20), (0.0, 0), (-200.0, -200), (1200.0, 1200)],
)
async def test_ifshift_set_snaps_onto_lattice(value: float, applied: int) -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("IFSHIFT", value)
    assert resp.ok
    assert radio.if_shift_calls == [applied]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1210.0, -1210.0, 1201.0])
async def test_ifshift_set_out_of_range_einval_no_call(value: float) -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("IFSHIFT", value)
    assert resp.error == int(HamlibError.EINVAL)
    assert radio.if_shift_calls == []


# ---------------------------------------------------------------------------
# CWPITCH — off-lattice Hz snaps; out of range is EINVAL, not a clamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "applied"),
    [(301.0, 300), (305.0, 310), (700.0, 700), (300.0, 300), (1050.0, 1050)],
)
async def test_cwpitch_set_snaps_onto_lattice(value: float, applied: int) -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("CWPITCH", value)
    assert resp.ok
    assert radio.cw_pitch_calls == [applied]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1060.0, 299.0, 200.0, 5000.0])
async def test_cwpitch_set_out_of_range_einval_no_call(value: float) -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("CWPITCH", value)
    assert resp.error == int(HamlibError.EINVAL)
    assert radio.cw_pitch_calls == []


# ---------------------------------------------------------------------------
# No protocol implementation — legacy rounded passthrough, clamp deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_domain_notchf_keeps_round_passthrough() -> None:
    """Today's raw-code passthrough: hamlib Hz forwarded as the raw code."""
    radio = _NoDomainRadio()
    resp = await _routing(radio).set_level("NOTCHF", 1500.0)
    assert resp.ok
    assert radio.notch_calls == [1500]


@pytest.mark.asyncio
async def test_no_domain_ifshift_keeps_round_passthrough() -> None:
    radio = _NoDomainRadio()
    resp = await _routing(radio).set_level("IFSHIFT", 15.7)
    assert resp.ok
    assert radio.if_shift_calls == [16]


@pytest.mark.asyncio
async def test_no_domain_cwpitch_no_longer_clamps() -> None:
    """The 300-1050 clamp is gone: plain round() passthrough (MOR-2469)."""
    radio = _NoDomainRadio()
    resp = await _routing(radio).set_level("CWPITCH", 200.0)
    assert resp.ok
    assert radio.cw_pitch_calls == [200]
    resp = await _routing(radio).set_level("CWPITCH", 5000.0)
    assert resp.ok
    assert radio.cw_pitch_calls == [200, 5000]


@pytest.mark.asyncio
async def test_no_domain_notchf_get_keeps_raw_index() -> None:
    radio = _NoDomainRadio()
    radio.notch_state = (True, 150)
    resp = await _routing(radio).get_level("NOTCHF")
    assert resp.ok
    assert resp.values == ["150"]
