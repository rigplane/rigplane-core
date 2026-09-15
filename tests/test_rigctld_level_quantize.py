"""MOR-2469 / MOR-2479 — rigctld routes levels through the control-domain surface.

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

MOR-2479 extends the same dispatch to NR: hamlib carries NR as a
0.0-1.0 fraction and the routing used to scale it by a hardcoded 15,
while the FTX-1 profile publishes an identity 0-10 ``nr_level`` domain
(CAT manual rev 2508-C, ``RL``: P2 = 00 OFF, 01-10). With a published
domain the fraction is mapped onto the display band and written as its
raw code; reading decodes the raw code and divides by the band maximum
so raw 10 answers ``1.000000``. No domain — or a double that cannot
supply usable bounds — keeps today's /15 numbers exactly.
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
        self.nr_calls: list[int] = []
        self.notch_state: tuple[bool, int] = (True, 1)
        self.nr_level: int = 0

    def snap_control_display(self, control: str, display: str) -> int | None:
        return self._ftx1.snap_control_display(control, display)

    def decode_control_raw(self, control: str, raw: int) -> str | None:
        return self._ftx1.decode_control_raw(control, raw)

    def control_display_bounds(self, control: str) -> tuple[str, str] | None:
        return self._ftx1.control_display_bounds(control)

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

    async def get_nr_level(self, receiver: int = 0) -> int:
        return self.nr_level

    async def set_nr_level(self, level: int, receiver: int = 0) -> None:
        self.nr_calls.append(level)


class _NoNrDomainRadio(_DomainRadio):
    """Domain double whose profile publishes no ``nr_level`` domain.

    ``control_display_bounds`` answers ``None`` for NR — the contract
    for "no normalized domain published" — so both NR arms must take
    the legacy /15 path while the other controls keep domain routing.
    """

    def control_display_bounds(self, control: str) -> tuple[str, str] | None:
        if control == "nr_level":
            return None
        return self._ftx1.control_display_bounds(control)


class _NoDomainRadio:
    """Radio double without the ControlDomainCapable surface."""

    def __init__(self) -> None:
        self.notch_calls: list[int] = []
        self.if_shift_calls: list[int] = []
        self.cw_pitch_calls: list[int] = []
        self.nr_calls: list[int] = []
        self.notch_state: tuple[bool, int] = (True, 1)
        self.nr_level: int = 0

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

    async def get_nr_level(self, receiver: int = 0) -> int:
        return self.nr_level

    async def set_nr_level(self, level: int, receiver: int = 0) -> None:
        self.nr_calls.append(level)


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


# ---------------------------------------------------------------------------
# NR — hamlib 0.0-1.0 fraction mapped onto the identity 0-10 domain
# (MOR-2479: FTX-1 CAT manual rev 2508-C, RL P2 = 00 OFF, 01-10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "applied"), [(1.0, 10), (0.5, 5), (0.0, 0), (0.3, 3), (0.35, 4)]
)
async def test_nr_set_maps_fraction_onto_domain(value: float, applied: int) -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("NR", value)
    assert resp.ok
    assert radio.nr_calls == [applied]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1.5, -0.1, 12.0])
async def test_nr_set_out_of_range_einval_no_call(value: float) -> None:
    radio = _DomainRadio()
    resp = await _routing(radio).set_level("NR", value)
    assert resp.error == int(HamlibError.EINVAL)
    assert not resp.ok
    assert radio.nr_calls == []


@pytest.mark.asyncio
async def test_nr_get_decodes_raw_max_to_one() -> None:
    radio = _DomainRadio()
    radio.nr_level = 10
    resp = await _routing(radio).get_level("NR")
    assert resp.ok
    assert resp.values == ["1.000000"]


@pytest.mark.asyncio
async def test_nr_get_decodes_mid_raw_to_half() -> None:
    radio = _DomainRadio()
    radio.nr_level = 5
    resp = await _routing(radio).get_level("NR")
    assert resp.ok
    assert resp.values == ["0.500000"]


# ---------------------------------------------------------------------------
# NR with no usable domain — today's /15 numbers, pinned exactly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "applied"), [(1.0, 15), (0.5, 8), (0.0, 0)])
async def test_nr_set_no_domain_keeps_legacy_scale(value: float, applied: int) -> None:
    """No protocol implementation → max(0, min(15, round(value * 15)))."""
    radio = _NoDomainRadio()
    resp = await _routing(radio).set_level("NR", value)
    assert resp.ok
    assert radio.nr_calls == [applied]


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "applied"), [(1.0, 15), (0.5, 8), (0.0, 0)])
async def test_nr_set_unpublished_domain_keeps_legacy_scale(
    value: float, applied: int
) -> None:
    """Protocol implemented but no nr_level domain published → legacy /15."""
    radio = _NoNrDomainRadio()
    resp = await _routing(radio).set_level("NR", value)
    assert resp.ok
    assert radio.nr_calls == [applied]


@pytest.mark.asyncio
async def test_nr_get_no_domain_keeps_legacy_scale() -> None:
    radio = _NoDomainRadio()
    radio.nr_level = 8
    resp = await _routing(radio).get_level("NR")
    assert resp.ok
    assert resp.values == [f"{8 / 15.0:.6f}"]


@pytest.mark.asyncio
async def test_nr_get_unpublished_domain_keeps_legacy_scale() -> None:
    radio = _NoNrDomainRadio()
    radio.nr_level = 8
    resp = await _routing(radio).get_level("NR")
    assert resp.ok
    assert resp.values == [f"{8 / 15.0:.6f}"]


# ---------------------------------------------------------------------------
# NR state path — format_state_level must agree with the live read (MOR-2479)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nr_state_path_agrees_with_live_read() -> None:
    """Same raw value, both answering paths, same fraction.

    ``get_level`` reads the wire; ``format_state_level`` formats the
    StateStore projection. Both must consult the published domain, so
    raw 10 answers ``1.000000`` either way — not ``1.000000`` live and
    ``0.666667`` from state.
    """
    radio = _DomainRadio()
    radio.nr_level = 10
    routing = _routing(radio)
    live = await routing.get_level("NR")
    state = routing.format_state_level("NR", 10)
    assert live.ok and state is not None
    assert state.values == live.values == ["1.000000"]


def test_nr_state_path_agrees_with_live_read_off_max() -> None:
    radio = _DomainRadio()
    routing = _routing(radio)
    state = routing.format_state_level("NR", 5)
    assert state is not None
    assert state.values == ["0.500000"]


def test_nr_state_path_no_domain_keeps_legacy_scale() -> None:
    radio = _NoDomainRadio()
    routing = _routing(radio)
    state = routing.format_state_level("NR", 8)
    assert state is not None
    assert state.values == [f"{8 / 15.0:.6f}"]


def test_nr_state_path_unpublished_domain_keeps_legacy_scale() -> None:
    radio = _NoNrDomainRadio()
    routing = _routing(radio)
    state = routing.format_state_level("NR", 8)
    assert state is not None
    assert state.values == [f"{8 / 15.0:.6f}"]
