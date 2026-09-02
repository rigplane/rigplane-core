"""Unit tests for the transmit-authority vocabulary and pure engine.

Row 1 of the transmit-authority migration: the engine is consumed by nothing,
so every test here drives it directly with real fakes and a fake clock. No
MagicMock anywhere near the authority (repo hard rule).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import MISSING, FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from rigplane.core.tx_safety import BACKEND_MAX_KEY_DOWN_SECONDS
from rigplane.core.tx_observation import (
    RADIO_READBACK_SOURCES,
    TX_READ_DEADLINE_SECONDS,
    TxStateReading,
)
from rigplane.core import tx_authority
from rigplane.core.tx_authority import (
    DECISION_LOG_CAPACITY,
    FAMILY_WRITE_CLASS,
    RAW_EXCLUDED,
    TX_ARGUMENT_PREDICATES,
    TX_ENGINE_FAILURE_TAGS,
    UNRESOLVED_ARGUMENT,
    BandRelation,
    TransmitAuthority,
    TxArgumentContext,
    TxFamily,
    TxMethodEntry,
    TxRefusal,
    TxRefusalCode,
    TxWriteClass,
    band_relation,
    first_parameter_name,
    resolve_band,
    short_circuit_family,
)


def test_tx_observation_contract_is_canonical_and_authority_reexports_it() -> None:
    """The observation value has one canonical definition during migration."""
    import rigplane.core.tx_observation as tx_observation

    reading_type = tx_observation.TxStateReading
    reading_fields = fields(reading_type)
    assert [field.name for field in reading_fields] == [
        "value",
        "attributed",
        "source",
        "verified_readback",
        "failure",
    ]
    assert get_type_hints(reading_type) == {
        "value": bool | None,
        "attributed": str | None,
        "source": str | None,
        "verified_readback": bool,
        "failure": str | None,
    }
    assert reading_fields[0].default is MISSING
    assert [field.default for field in reading_fields[1:]] == [None, None, False, None]

    reading = reading_type(value=False)
    assert not hasattr(reading, "__dict__")
    with pytest.raises(FrozenInstanceError):
        reading.value = True  # type: ignore[misc]

    assert tx_observation.TX_READ_DEADLINE_SECONDS == 0.3
    assert tx_observation.RADIO_READBACK_SOURCES == frozenset(
        {"poll_response", "civ_unsolicited", "hamlib_response", "yaesu_poll_response"}
    )
    assert tx_authority.TxStateReading is reading_type
    assert (
        tx_authority.TX_READ_DEADLINE_SECONDS is tx_observation.TX_READ_DEADLINE_SECONDS
    )
    assert tx_authority.RADIO_READBACK_SOURCES is tx_observation.RADIO_READBACK_SOURCES


BANDS: tuple[tuple[int, int], ...] = (
    (14_000_000, 14_350_000),
    (21_000_000, 21_450_000),
    (28_000_000, 29_700_000),
)

METHOD_MAP: Mapping[str, TxMethodEntry] = {
    "set_ptt": TxMethodEntry(TxFamily.PTT_ON, predicate="ptt"),
    "set_powerstat": TxMethodEntry(TxFamily.POWER_ON, predicate="powerstat"),
    "set_freq": TxMethodEntry(TxFamily.FREQUENCY, predicate="frequency"),
    "set_mode": TxMethodEntry(TxFamily.MODE),
    "set_split": TxMethodEntry(TxFamily.VFO_TOPOLOGY),
    "set_power": TxMethodEntry(TxFamily.LEVELS),
    "stop_cw_text": TxMethodEntry(TxFamily.CW_STOP),
    "send_cw_text": TxMethodEntry(TxFamily.CW_TEXT),
    "set_tuner_status": TxMethodEntry(TxFamily.TUNER),
    "set_antenna_1": TxMethodEntry(TxFamily.ANTENNA),
    "set_band": TxMethodEntry(TxFamily.BAND),
    "set_vfo_slot": TxMethodEntry(TxFamily.VFO_SELECT),
    "memory_to_vfo": TxMethodEntry(TxFamily.BAND),
}


class Clock:
    """Deterministic monotonic clock."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTransmitStateLink:
    """Scripted transmit-state answers plus a wire log."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.reads: list[float] = []
        self.answers: list[TxStateReading | str] = []
        self.read_started: asyncio.Event | None = None
        self.release_read: asyncio.Event | None = None
        self.read_latency = 0.01
        self.default = TxStateReading(
            value=False,
            attributed="rx",
            source="poll_response",
            verified_readback=True,
            failure=None,
        )
        self.on_read: list[object] = []

    def script(self, *answers: TxStateReading | str) -> None:
        self.answers.extend(answers)

    async def read(self) -> TxStateReading:
        self.reads.append(self._clock())
        if self.read_started is not None:
            self.read_started.set()
        if self.release_read is not None:
            await self.release_read.wait()
        for hook in self.on_read:
            hook()  # type: ignore[operator]
        answer = self.answers.pop(0) if self.answers else self.default
        if answer == "hang":
            await asyncio.sleep(3600)
        if answer == "boom":
            raise OSError("transport is down")
        if answer == "garbage":
            raise ValueError("the row-5 parser could not decode the reply")
        self._clock.advance(self.read_latency)
        assert isinstance(answer, TxStateReading)
        return answer


class PoisonedLink:
    """A read callable that must never be invoked."""

    async def read(self) -> TxStateReading:  # pragma: no cover - must not run
        raise AssertionError("transmit truth was consulted on a PASS write")


class FakeUnkey:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1


RX = TxStateReading(
    value=False,
    attributed="rx",
    source="poll_response",
    verified_readback=True,
    failure=None,
)
TX = TxStateReading(
    value=True,
    attributed="tx_other",
    source="poll_response",
    verified_readback=True,
    failure=None,
)
UNVERIFIED_RX = TxStateReading(
    value=False,
    attributed="rx",
    source="hamlib_response",
    verified_readback=False,
    failure=None,
)
NO_CAPABILITY = TxStateReading(
    value=None,
    attributed=None,
    source=None,
    verified_readback=False,
    failure="no-capability",
)


def build_authority(
    clock: Clock,
    link: FakeTransmitStateLink | PoisonedLink,
    *,
    unkey: FakeUnkey | None = None,
    method_map: Mapping[str, TxMethodEntry] | None = None,
    bands: Sequence[tuple[int, int]] = BANDS,
    current_frequency_hz: float | None = 14_200_000,
    lease_active: bool = False,
    cw_hold_seconds: float | None = 5.0,
) -> TransmitAuthority:
    return TransmitAuthority(
        read_transmit_state=link.read,
        last_resort_unkey=unkey or FakeUnkey(),
        method_map=METHOD_MAP if method_map is None else method_map,
        clock=clock,
        bands=tuple(bands),
        current_frequency_hz=lambda: current_frequency_hz,
        lease_active=lambda: lease_active,
        cw_hold_duration=(
            None if cw_hold_seconds is None else (lambda _ctx: cw_hold_seconds)
        ),
    )


# --------------------------------------------------------------------------
# Pinned literals (INV-8 and the raw exclusion)
# --------------------------------------------------------------------------


def test_radio_readback_sources_pin() -> None:
    """Only radio-readback provenance feeds transmit truth (INV-8).

    Mutation: adding ``"state_poller"`` (or any producer-side tag) to the
    frozenset must make this test go red.
    """
    assert RADIO_READBACK_SOURCES == frozenset(
        {
            "poll_response",
            "civ_unsolicited",
            "hamlib_response",
            "yaesu_poll_response",
        }
    )
    for excluded in ("command_response", "state_poller", "local_reconcile", "test"):
        assert excluded not in RADIO_READBACK_SOURCES


def test_raw_excluded_pin() -> None:
    """Raw byte writes are excluded by name, never classified."""
    assert RAW_EXCLUDED == frozenset(
        {
            "send_civ",
            "send_civ_transaction",
            "send_civ_raw_fire_and_forget",
        }
    )


def test_family_class_table_is_total_and_pinned() -> None:
    assert set(FAMILY_WRITE_CLASS) == set(TxFamily)
    hazard = {f for f, c in FAMILY_WRITE_CLASS.items() if c is TxWriteClass.HAZARD}
    assert hazard == {
        TxFamily.BAND,
        TxFamily.TUNER,
        TxFamily.ANTENNA,
        TxFamily.VFO_SELECT,
    }
    keying = {f for f, c in FAMILY_WRITE_CLASS.items() if c is TxWriteClass.KEYING}
    assert keying == {TxFamily.PTT_ON, TxFamily.CW_TEXT}
    unkey = {f for f, c in FAMILY_WRITE_CLASS.items() if c is TxWriteClass.UNKEY}
    assert unkey == {TxFamily.PTT_OFF}
    assert FAMILY_WRITE_CLASS[TxFamily.FREQUENCY] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.MODE] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.VFO_TOPOLOGY] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.POWER_OFF] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.CW_STOP] is TxWriteClass.PASS


def test_key_down_bound_is_imported_not_respelled() -> None:
    from rigplane.core import tx_authority

    assert tx_authority.BACKEND_MAX_KEY_DOWN_SECONDS is BACKEND_MAX_KEY_DOWN_SECONDS
    source = tx_authority.__file__ or ""
    assert source.endswith("tx_authority.py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "180" not in text


def test_read_deadline_is_its_own_named_bound() -> None:
    assert TX_READ_DEADLINE_SECONDS == 0.3


def test_transmit_truth_projection_is_absent() -> None:
    from rigplane.core import tx_authority

    for name in (
        "TransmitTruth",
        "EMPTY_TRANSMIT_TRUTH",
        "build_transmit_truth",
    ):
        assert not hasattr(tx_authority, name)


def test_argument_predicate_registry_is_named_and_pure() -> None:
    assert set(TX_ARGUMENT_PREDICATES) == {"ptt", "powerstat", "frequency"}
    ctx = TxArgumentContext(
        args=(True,), kwargs={}, current_frequency_hz=None, bands=()
    )
    assert TX_ARGUMENT_PREDICATES["ptt"](ctx) is TxFamily.PTT_ON
    off = TxArgumentContext(
        args=(False,), kwargs={}, current_frequency_hz=None, bands=()
    )
    assert TX_ARGUMENT_PREDICATES["ptt"](off) is TxFamily.PTT_OFF
    assert TX_ARGUMENT_PREDICATES["powerstat"](off) is TxFamily.POWER_OFF


# --------------------------------------------------------------------------
# Band relation arithmetic (§3.3)
# --------------------------------------------------------------------------


def test_resolve_band_hits_gap_and_missing_data() -> None:
    assert resolve_band(14_200_000, BANDS) == 0
    assert resolve_band(21_100_000, BANDS) == 1
    assert resolve_band(18_100_000, BANDS) is None  # gap between declared bands
    assert resolve_band(None, BANDS) is None
    assert resolve_band(14_200_000, ()) is None


@pytest.mark.parametrize(
    ("current", "target", "bands", "expected"),
    [
        (14_200_000, 14_300_000, BANDS, BandRelation.SAME_BAND),
        (14_200_000, 21_100_000, BANDS, BandRelation.CROSS_BAND),
        (14_200_000, 18_100_000, BANDS, BandRelation.UNRESOLVED),
        (18_100_000, 21_100_000, BANDS, BandRelation.UNRESOLVED),
        (None, 21_100_000, BANDS, BandRelation.UNRESOLVED),
        (14_200_000, 21_100_000, (), BandRelation.UNRESOLVED),
    ],
)
def test_band_relation(
    current: float | None,
    target: float,
    bands: tuple[tuple[int, int], ...],
    expected: BandRelation,
) -> None:
    assert band_relation(current, target, bands) is expected


@pytest.mark.parametrize(
    ("current", "target", "bands", "family"),
    [
        (14_200_000, 14_300_000, BANDS, TxFamily.FREQUENCY),
        (14_200_000, 21_100_000, BANDS, TxFamily.BAND),
        (14_200_000, 18_100_000, BANDS, TxFamily.FREQUENCY),
        (None, 21_100_000, BANDS, TxFamily.FREQUENCY),
        (14_200_000, 21_100_000, (), TxFamily.FREQUENCY),
    ],
)
def test_frequency_predicate_only_gates_a_resolved_crossing(
    current: float | None,
    target: float,
    bands: tuple[tuple[int, int], ...],
    family: TxFamily,
) -> None:
    ctx = TxArgumentContext(
        args=(target,), kwargs={}, current_frequency_hz=current, bands=bands
    )
    assert TX_ARGUMENT_PREDICATES["frequency"](ctx) is family


async def test_cross_band_set_freq_is_gated_and_in_band_is_not() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(TX)
    authority = build_authority(clock, link)

    async with authority.admit("set_freq", (14_250_000,)):
        pass
    assert link.reads == []  # in-band frequency never consults truth

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_freq", (21_100_000,)):
            pytest.fail("cross-band write must not reach the wire")
    assert excinfo.value.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    assert len(link.reads) == 1


# --------------------------------------------------------------------------
# Fail-direction per class × per truth answer (§3.3)
# --------------------------------------------------------------------------


async def test_pass_class_never_consults_truth() -> None:
    """INV-3: a poisoned read callable must not be reachable from PASS."""
    clock = Clock()
    authority = build_authority(clock, PoisonedLink())
    sent: list[str] = []
    for method, args in (
        ("set_mode", ("USB",)),
        ("set_split", (True,)),
        ("set_power", (50,)),
        ("set_powerstat", (True,)),
        ("stop_cw_text", ()),
    ):
        async with authority.admit(method, args):
            sent.append(method)
    assert len(sent) == 5
    assert authority.view().records == ()


async def test_hazard_at_confirmed_rx_reads_before_the_write() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link)
    wire: list[str] = []
    link.on_read.append(lambda: wire.append("read"))

    async with authority.admit("set_antenna_1", ()):
        wire.append("write")

    assert wire == ["read", "write"]
    record = authority.view().records[-1]
    assert record.action == "sent"
    assert record.family == TxFamily.ANTENNA
    assert record.write_class is TxWriteClass.HAZARD
    assert record.code is None
    assert record.evidence is not None
    assert record.evidence.solicited is True
    assert record.evidence.value is False
    assert record.evidence.age_seconds == pytest.approx(link.read_latency)


async def test_hazard_at_transmit_is_refused_with_evidence() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(TX)
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_tuner_status", (1,)):
            pytest.fail("hazard write reached the wire while transmitting")

    refusal = excinfo.value
    assert refusal.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    assert refusal.evidence.value is True
    assert refusal.evidence.attributed == "tx_other"
    assert refusal.evidence.source == "poll_response"
    assert refusal.evidence.solicited is True
    assert refusal.evidence.own_transmit_hold is None
    record = authority.view().records[-1]
    assert record.action == "refused"
    assert record.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING


async def test_hazard_read_timeout_fails_closed() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script("hang")
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_band", (20,)):
            pytest.fail("hazard write reached the wire on an unanswered read")

    refusal = excinfo.value
    assert refusal.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert refusal.evidence.failure == "timeout"
    assert refusal.evidence.value is None
    assert refusal.evidence.solicited is True


async def test_hazard_transport_error_fails_closed() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script("boom")
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_vfo_slot", ("B",)):
            pytest.fail("hazard write reached the wire on a failed read")
    assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert excinfo.value.evidence.failure == "transport"


async def test_an_unexpected_read_error_is_still_a_typed_refusal() -> None:
    """§3.4: ``TxRefusal`` is the one exception consumers of the gate handle."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script("garbage")
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_antenna_1", ()):
            pytest.fail("hazard write reached the wire on an undecodable reply")
    assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert excinfo.value.evidence.failure == "read-error"
    assert authority.view().records[-1].action == "refused"


def test_engine_failure_tag_set_is_pinned() -> None:
    """A sixth tag must not appear unnoticed: row 9b widens the web envelope."""
    assert TX_ENGINE_FAILURE_TAGS == frozenset(
        {
            "timeout",
            "transport",
            "read-error",
            "unverifiable-provenance",
            "unclassified",
        }
    )


async def test_hazard_without_capability_fails_closed() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(NO_CAPABILITY)
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("memory_to_vfo", (3,)):
            pytest.fail("hazard write reached the wire without a read primitive")
    assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert excinfo.value.evidence.failure == "no-capability"


async def test_unverifiable_readback_fails_closed() -> None:
    """§3.7: the rigctld-client answer is an upstream cache, not radio truth."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(UNVERIFIED_RX)
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_antenna_1", ()):
            pytest.fail("hazard write admitted on an unverifiable readback")
    assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert excinfo.value.evidence.failure == "unverifiable-provenance"
    assert excinfo.value.evidence.verified_readback is False


@pytest.mark.parametrize(
    "answer",
    [TX, "hang", "boom", NO_CAPABILITY, UNVERIFIED_RX],
)
async def test_every_refusal_carries_evidence(answer: TxStateReading | str) -> None:
    """INV-14: no refusal is exempt from carrying its evidence."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(answer)
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_tuner_status", (0,)):
            pytest.fail("refused write reached the wire")
    evidence = excinfo.value.evidence
    assert evidence is not None
    assert (evidence.value is not None) or (evidence.failure is not None)
    assert authority.view().records[-1].evidence is evidence


# --------------------------------------------------------------------------
# Own-transmit holds — the B6 golden (§3.5 step 1, INV-16)
# --------------------------------------------------------------------------


async def test_b6_golden_own_cw_hold_refuses_without_any_wire_read() -> None:
    """A scripted RX answer during our own CW message must not admit a hazard.

    B6, on the bench: the radio reported *receiving* while it was audibly
    still sending a CAT-issued CW message. The hold is the evidence; the wire
    is not consulted at all.
    """
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX, RX)  # the radio would answer "receiving" if asked
    authority = build_authority(clock, link, cw_hold_seconds=5.0)

    async with authority.admit("send_cw_text", ("CQ CQ DE TEST",)):
        pass
    assert link.reads == []

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_band", (15,)):
            pytest.fail("hazard write admitted during our own CW message")

    refusal = excinfo.value
    assert refusal.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    assert refusal.evidence.own_transmit_hold == "cw"
    assert refusal.evidence.solicited is False
    assert refusal.evidence.value is None
    assert link.reads == []  # no wire read at all
    assert authority.view().records[-1].evidence.own_transmit_hold == "cw"

    clock.advance(6.0)  # the computed message duration elapses
    async with authority.admit("set_band", (15,)):
        pass
    assert len(link.reads) == 1


@pytest.mark.parametrize(
    ("setup_method", "setup_args", "hold"),
    [
        ("set_ptt", (True,), "key"),
        ("set_tuner_status", (2,), "tune"),
    ],
)
async def test_own_transmit_holds_refuse_hazard_writes(
    setup_method: str, setup_args: tuple[object, ...], hold: str
) -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX, RX)
    authority = build_authority(clock, link)

    async with authority.admit(setup_method, setup_args):
        pass
    reads_after_setup = len(link.reads)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_antenna_1", ()):
            pytest.fail("hazard write admitted while we hold our own transmission")
    assert excinfo.value.evidence.own_transmit_hold == hold
    assert len(link.reads) == reads_after_setup  # step 1 makes no wire read


async def test_supervisor_lease_is_an_own_transmit_hold() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link, lease_active=True)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_antenna_1", ()):
            pytest.fail("hazard write admitted under an active managed lease")
    assert excinfo.value.evidence.own_transmit_hold == "lease"
    assert link.reads == []


async def test_an_admitted_unkey_clears_the_key_hold() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    async with authority.admit("set_ptt", (False,)):
        pass
    async with authority.admit("set_antenna_1", ()):
        pass
    assert len(link.reads) == 1


# --------------------------------------------------------------------------
# T5 / INV-5 / INV-6 — the unkey is never made harder
# --------------------------------------------------------------------------


#: A table that classifies all three T5 methods into HAZARD families. An empty
#: map is not a poisoned one — it leaves the short-circuit as the only path and
#: so cannot catch INV-6's mutation ("reorder the short-circuit after the
#: table"). This map can: with the table consulted first, every de-key path
#: becomes a hazard write and is refused at a scripted TX.
POISONED_MAP: Mapping[str, TxMethodEntry] = {
    "set_ptt": TxMethodEntry(TxFamily.TUNER),
    "set_powerstat": TxMethodEntry(TxFamily.ANTENNA),
    "stop_cw_text": TxMethodEntry(TxFamily.BAND),
}


async def test_unkey_is_never_refused_even_with_a_poisoned_table() -> None:
    """INV-5/INV-6: the T5 short-circuit precedes the table and the wire."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(TX, TX, TX)
    authority = build_authority(clock, link, method_map=POISONED_MAP)

    for method, args in (
        ("set_ptt", (False,)),
        ("set_powerstat", (False,)),
        ("stop_cw_text", ()),
    ):
        async with authority.admit(method, args):
            pass

    assert link.reads == []  # not one of the three consulted the radio
    assert authority.view().deadline_monotonic is None
    assert authority.view().records[-1].write_class is TxWriteClass.UNKEY


async def test_unkey_clears_the_deadline_and_the_holds() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    assert authority.view().deadline_monotonic == pytest.approx(
        clock.now + BACKEND_MAX_KEY_DOWN_SECONDS
    )
    assert authority.view().own_transmit_holds == ("key",)

    async with authority.admit("set_ptt", (False,)):
        pass
    assert authority.view().deadline_monotonic is None
    assert authority.view().own_transmit_holds == ()


# --------------------------------------------------------------------------
# KEYING and the one deadline (§3.6, INV-7)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [RX, TX, "boom"])
async def test_keying_is_admitted_on_every_truth_answer(
    answer: TxStateReading | str,
) -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(answer)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    assert link.reads == []  # a key is explicit operator intent, never truth-gated
    record = authority.view().records[-1]
    assert record.action == "sent"
    assert record.write_class is TxWriteClass.KEYING
    assert record.evidence is None


async def test_deadline_fires_once_and_is_disarmed() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    assert authority.poll(clock.now) == ()

    clock.advance(BACKEND_MAX_KEY_DOWN_SECONDS - 0.001)
    assert authority.poll(clock.now) == ()

    clock.advance(0.002)
    effects = authority.poll(clock.now)
    assert len(effects) == 1
    assert effects[0].reason == "backend_max_key_down"
    assert authority.poll(clock.now) == ()
    assert authority.view().deadline_monotonic is None
    assert authority.view().own_transmit_holds == ()


async def test_a_newer_key_rearms_the_deadline() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    clock.advance(60.0)
    async with authority.admit("set_ptt", (True,)):
        pass
    assert authority.view().deadline_monotonic == pytest.approx(
        clock.now + BACKEND_MAX_KEY_DOWN_SECONDS
    )


async def test_admitted_tune_start_holds_and_arms_but_a_bypass_throw_does_not() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX, RX)
    authority = build_authority(clock, link)

    async with authority.admit("set_tuner_status", (0,)):
        pass
    assert authority.view().deadline_monotonic is None
    assert authority.view().own_transmit_holds == ()

    async with authority.admit("set_tuner_status", (2,)):
        pass
    assert authority.view().own_transmit_holds == ("tune",)
    assert authority.view().deadline_monotonic == pytest.approx(
        clock.now + BACKEND_MAX_KEY_DOWN_SECONDS
    )


async def test_a_refused_tune_start_neither_holds_nor_arms() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(TX)
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal):
        async with authority.admit("set_tuner_status", (2,)):
            pytest.fail("tune start admitted while transmitting")
    assert authority.view().deadline_monotonic is None
    assert authority.view().own_transmit_holds == ()


@pytest.mark.parametrize(
    ("method", "args", "hold"),
    [("set_ptt", (True,), "key"), ("set_tuner_status", (2,), "tune")],
)
async def test_own_transmit_holds_do_not_outlive_the_deadline_without_a_driver(
    method: str, args: tuple[object, ...], hold: str
) -> None:
    """The bound the ADR already specifies, evaluated without a ``poll()`` caller.

    Rows 7-11 arm no deadline driver, and a tune start has no natural unkey at
    all, so a hold that only ``poll()`` could clear would refuse the whole
    hazard set for the life of the connection. An expired hold does not go
    blind: it falls through to the solicited read.
    """
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX, RX)
    authority = build_authority(clock, link)

    async with authority.admit(method, args):
        pass
    assert authority.view().own_transmit_holds == (hold,)

    clock.advance(BACKEND_MAX_KEY_DOWN_SECONDS + 1.0)
    assert authority.view().own_transmit_holds == ()  # poll() was never called

    reads_before = len(link.reads)
    async with authority.admit("set_antenna_1", ()):
        pass
    assert len(link.reads) == reads_before + 1


async def test_a_failed_key_write_still_records_its_hold() -> None:
    """A write that raised may already have reached the radio."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)

    with pytest.raises(OSError):
        async with authority.admit("set_ptt", (True,)):
            raise OSError("the transport died after the frame went out")

    assert authority.view().own_transmit_holds == ("key",)
    assert authority.view().deadline_monotonic is not None


async def test_last_resort_unkey_is_the_only_other_injected_effect() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    unkey = FakeUnkey()
    authority = build_authority(clock, link, unkey=unkey)

    await authority.fire_last_resort_unkey()
    assert unkey.calls == 1


# --------------------------------------------------------------------------
# INV-4 — the read is not shared and is invalidated by a transmit event
# --------------------------------------------------------------------------


async def test_a_transmit_observation_between_read_and_write_invalidates_it() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link)
    link.on_read.append(lambda: authority.note_transmit_observation(True))

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_antenna_1", ()):
            pytest.fail("hazard write used a read a transmit event invalidated")
    assert excinfo.value.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING


async def test_a_key_cannot_slip_between_a_hazard_read_and_its_write() -> None:
    """INV-4: the admission lock spans the read and the write it authorised.

    Counts cannot see this — both writes happen either way. Only the *order*
    of the effects distinguishes a held lock from an open window, and only if
    the hazard body *awaits*: every real transport write does, and a purely
    synchronous body reaches its append before the loop can schedule anybody
    else, so it cannot tell a lock held through the write handoff from one
    released at the verdict.

    # MUTATION: in `src/rigplane/core/tx_authority.py`, dedent the
    # `try:/yield ticket/finally:/self._commit(...)` block at :538-543 by one
    # level so it sits after the `async with self._lock:` body rather than
    # inside it -> this row goes red with
    # `["key-write", "hazard-relay-throw"]`: a key completed inside the
    # relay-throw window.
    """
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    link.read_started = asyncio.Event()
    link.release_read = asyncio.Event()
    authority = build_authority(clock, link)
    order: list[str] = []

    async def hazard() -> None:
        async with authority.admit("set_antenna_1", ()):
            await asyncio.sleep(0)  # the write handoff every transport makes
            order.append("hazard-relay-throw")

    async def key() -> None:
        async with authority.admit("set_ptt", (True,)):
            order.append("key-write")

    hazard_task = asyncio.create_task(hazard())
    await asyncio.wait_for(link.read_started.wait(), 1.0)

    key_task = asyncio.create_task(key())
    for _ in range(20):
        await asyncio.sleep(0)  # the key gets every chance to slip in
    assert order == [], "the key ran while a hazard admission held the lock"

    link.release_read.set()
    await asyncio.gather(hazard_task, key_task)
    assert order == ["hazard-relay-throw", "key-write"]


async def test_a_receive_observation_never_clears_anything() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    authority.note_transmit_observation(False)
    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_antenna_1", ()):
            pytest.fail("a radio RX report cleared our own key hold")
    assert excinfo.value.evidence.own_transmit_hold == "key"


async def test_concurrent_hazard_admissions_do_not_share_a_read() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX, RX)
    authority = build_authority(clock, link)
    order: list[str] = []

    async def hazard(tag: str) -> None:
        async with authority.admit("set_antenna_1", ()):
            order.append(f"write:{tag}")

    await asyncio.gather(hazard("a"), hazard("b"))
    assert len(link.reads) == 2
    assert len(order) == 2


# --------------------------------------------------------------------------
# Decision ring and view (§3.4)
# --------------------------------------------------------------------------


async def test_decision_ring_is_bounded() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)
    assert DECISION_LOG_CAPACITY == 256

    for _ in range(DECISION_LOG_CAPACITY + 20):
        async with authority.admit("set_ptt", (True,)):
            pass
    assert len(authority.view().records) == DECISION_LOG_CAPACITY


async def test_view_reports_holds_and_deadline() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = TransmitAuthority(
        read_transmit_state=link.read,
        last_resort_unkey=FakeUnkey(),
        method_map=METHOD_MAP,
        clock=clock,
        bands=BANDS,
    )
    async with authority.admit("set_ptt", (True,)):
        pass
    view = authority.view()
    assert view.own_transmit_holds == ("key",)
    assert view.deadline_monotonic is not None
    assert view.records[-1].method == "set_ptt"


async def test_an_unmapped_method_fails_closed() -> None:
    """INV-1's fail direction: nothing defaults to PASS by omission."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit("set_something_new", (1,)):
            pytest.fail("an unmapped write reached the wire")
    assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert excinfo.value.evidence.failure == "unclassified"


async def test_raw_excluded_methods_are_not_classified() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, PoisonedLink())
    for method in sorted(RAW_EXCLUDED):
        async with authority.admit(method, (b"\xfe\xfe",)) as admission:
            assert admission.family is None  # bytes are not classified
    assert authority.view().records == ()
    assert link.reads == []


# --------------------------------------------------------------------------
# MOR-1954 — classification never depends on how the call spelled the argument
# --------------------------------------------------------------------------


class SignatureMirror:
    """The parameter names the shipping Icom bodies actually declare.

    Not a radio stand-in: these pins need the *names* `runtime/radio.py`
    declares (`on`, `freq_hz`, `value`), because the defect being closed was
    the engine guessing them (`"value"`, `"frequency"`) in the predicate table.
    ``test_first_parameter_names_mirror_the_shipping_bodies`` keeps this class
    honest against the real methods.
    """

    async def set_ptt(self, on: bool) -> None: ...

    async def set_powerstat(self, on: bool) -> None: ...

    async def set_freq(self, freq_hz: int, receiver: int = 0) -> None: ...

    async def set_tuner_status(self, value: int) -> None: ...


def test_first_parameter_names_mirror_the_shipping_bodies() -> None:
    """The resolver reads real signatures, and the mirror matches them."""
    from rigplane.runtime.radio import IcomRadio

    assert first_parameter_name(IcomRadio.set_ptt) == "on"
    assert first_parameter_name(IcomRadio.set_powerstat) == "on"
    assert first_parameter_name(IcomRadio.set_freq) == "freq_hz"
    assert first_parameter_name(IcomRadio.set_tuner_status) == "value"

    for method in ("set_ptt", "set_powerstat", "set_freq", "set_tuner_status"):
        assert first_parameter_name(getattr(SignatureMirror, method)) == (
            first_parameter_name(getattr(IcomRadio, method))
        )
    assert first_parameter_name(None) is None


async def test_keyword_key_down_is_never_read_as_an_unkey() -> None:
    """The load-bearing pin: ``set_ptt(on=True)`` keys, however it is spelled.

    Before this fix the predicate read ``kwargs["value"]``; the Icom body
    declares ``on``, so a keyword key-down resolved to ``None``, classified
    PTT_OFF and was short-circuited past the gate built to arm the watchdog.
    No signature is supplied here on purpose — even with nothing to resolve
    from, the engine must fail towards the key, never towards the unkey.
    """
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (), {"on": True}) as admission:
        assert admission.family is TxFamily.PTT_ON
        assert admission.write_class is TxWriteClass.KEYING

    view = authority.view()
    assert view.own_transmit_holds == ("key",)
    assert view.deadline_monotonic == pytest.approx(
        clock.now + BACKEND_MAX_KEY_DOWN_SECONDS
    )


@pytest.mark.parametrize(
    ("method", "value", "expected"),
    [
        ("set_ptt", True, TxFamily.PTT_ON),
        ("set_ptt", False, TxFamily.PTT_OFF),
        ("set_powerstat", False, TxFamily.POWER_OFF),
        ("set_freq", 14_250_000, TxFamily.FREQUENCY),
        ("set_freq", 21_100_000, TxFamily.BAND),
    ],
)
async def test_classification_is_independent_of_argument_spelling(
    method: str, value: object, expected: TxFamily
) -> None:
    """Positional and keyword admissions of the same write agree, always."""
    target = getattr(SignatureMirror, method)
    name = first_parameter_name(target)
    assert name is not None

    families: list[TxFamily | None] = []
    classes: list[TxWriteClass] = []
    for args, kwargs in (((value,), {}), ((), {name: value})):
        clock = Clock()
        link = FakeTransmitStateLink(clock)
        link.script(RX, RX)
        authority = build_authority(clock, link)
        async with authority.admit(method, args, kwargs, target=target) as admission:
            families.append(admission.family)
            classes.append(admission.write_class)

    assert families == [expected, expected]
    assert classes[0] is classes[1]


async def test_keyword_retune_reads_the_real_frequency_argument() -> None:
    """A same-band keyword retune stays PASS only if ``freq_hz`` resolved."""
    clock = Clock()
    link = PoisonedLink()
    authority = build_authority(clock, link)

    async with authority.admit(
        "set_freq",
        (),
        {"freq_hz": 14_250_000, "receiver": 1},
        target=SignatureMirror.set_freq,
    ) as admission:
        assert admission.write_class is TxWriteClass.PASS
        assert admission.family is TxFamily.FREQUENCY


async def test_an_unresolvable_keyword_argument_fails_closed_and_is_loud(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No signature to resolve from must never mean a silent permissive PASS."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(TX)
    authority = build_authority(clock, link)

    with caplog.at_level(logging.WARNING, logger="rigplane.core.tx_authority"):
        with pytest.raises(TxRefusal) as refusal:
            async with authority.admit("set_freq", (), {"freq_hz": 14_250_000}):
                pass  # pragma: no cover - the admission refuses first

    assert refusal.value.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    assert authority.view().records[-1].family == str(TxFamily.BAND)
    assert any("could not resolve" in record.getMessage() for record in caplog.records)


async def test_an_unresolvable_tuner_argument_still_holds_the_tune() -> None:
    """``set_tuner_status`` with nothing to resolve holds as if a tune started."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link)

    async with authority.admit("set_tuner_status", (), {"value": 2}):
        pass
    assert authority.view().own_transmit_holds == ("tune",)


@pytest.mark.parametrize("spelling", ["positional", "keyword", "unresolvable"])
async def test_the_unkey_is_still_never_refused_in_any_spelling(
    spelling: str,
) -> None:
    """INV-5 survives the fix: no spelling makes an unkey harder.

    The poisoned table would refuse every de-key as a hazard at the scripted
    TX if the T5 short-circuit stopped reaching it. The keyword unkey resolves
    through the real signature and short-circuits; the unresolvable one may
    not be *called* an unkey, but it must still not be refused — it lands on
    the keying branch, which has no refusal path and consults no truth.
    """
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(TX, TX)
    authority = build_authority(clock, link, method_map=POISONED_MAP)

    if spelling == "positional":
        call = authority.admit("set_ptt", (False,))
    elif spelling == "keyword":
        call = authority.admit(
            "set_ptt", (), {"on": False}, target=SignatureMirror.set_ptt
        )
    else:
        call = authority.admit("set_ptt", (), {"on": False})

    async with call as admission:
        pass

    assert link.reads == []  # no spelling made the unkey consult the radio
    if spelling == "unresolvable":
        assert admission.write_class is TxWriteClass.KEYING
    else:
        assert admission.family is TxFamily.PTT_OFF
        assert admission.write_class is TxWriteClass.UNKEY


async def test_a_keyword_unkey_clears_the_deadline_and_the_holds() -> None:
    """The UNKEY branch's whole effect, reached by the keyword spelling."""
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    assert authority.view().own_transmit_holds == ("key",)

    async with authority.admit(
        "set_ptt", (), {"on": False}, target=SignatureMirror.set_ptt
    ):
        pass
    assert authority.view().deadline_monotonic is None
    assert authority.view().own_transmit_holds == ()


def test_predicates_resolve_the_argument_by_signature_not_by_guess() -> None:
    """Predicate-level twin of the admission pins, including the sentinel."""
    keyed = TxArgumentContext(
        args=(),
        kwargs={"on": True},
        current_frequency_hz=None,
        bands=(),
        target=SignatureMirror.set_ptt,
    )
    assert TX_ARGUMENT_PREDICATES["ptt"](keyed) is TxFamily.PTT_ON

    unkeyed = TxArgumentContext(
        args=(),
        kwargs={"on": False},
        current_frequency_hz=None,
        bands=(),
        target=SignatureMirror.set_ptt,
    )
    assert TX_ARGUMENT_PREDICATES["ptt"](unkeyed) is TxFamily.PTT_OFF
    assert unkeyed.first() is False

    blind = TxArgumentContext(
        args=(), kwargs={"on": False}, current_frequency_hz=None, bands=()
    )
    assert blind.first() is UNRESOLVED_ARGUMENT
    assert TX_ARGUMENT_PREDICATES["ptt"](blind) is TxFamily.PTT_ON
    assert TX_ARGUMENT_PREDICATES["powerstat"](blind) is TxFamily.POWER_ON
    assert TX_ARGUMENT_PREDICATES["frequency"](blind) is TxFamily.BAND
    assert short_circuit_family("set_ptt", blind) is TxFamily.PTT_ON
    assert short_circuit_family("set_powerstat", blind) is TxFamily.POWER_ON
    assert short_circuit_family("stop_cw_text", blind) is TxFamily.CW_STOP


async def test_an_unresolvable_unkey_keeps_the_hold_it_could_not_read() -> None:
    """The price of the fail-closed path, pinned so the comment cannot drift.

    An unreadable de-key is admitted (never refused) but is not *believed*: it
    takes the KEYING branch, so the live hold and deadline survive and a second
    hold stacks, and hazard writes stay refused for the key-down bound. That is
    the documented cost of :data:`UNRESOLVED_ARGUMENT`, and it is the direction
    worth being expensive in.
    """
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link)

    async with authority.admit("set_ptt", (True,)):
        pass
    async with authority.admit("set_ptt", (), {"on": False}) as admission:
        assert admission.write_class is TxWriteClass.KEYING  # admitted, not refused

    view = authority.view()
    assert view.own_transmit_holds == ("key", "key")
    assert view.deadline_monotonic == pytest.approx(
        clock.now + BACKEND_MAX_KEY_DOWN_SECONDS
    )

    with pytest.raises(TxRefusal) as refusal:
        async with authority.admit("set_antenna_1", ()):
            pass  # pragma: no cover - the admission refuses first
    assert refusal.value.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    assert refusal.value.evidence.own_transmit_hold == "key"
    assert link.reads == []  # refused on our own hold, with no wire read at all

    clock.advance(BACKEND_MAX_KEY_DOWN_SECONDS + 0.1)
    async with authority.admit("set_antenna_1", ()):
        pass  # the hold expires on the key-down bound; it is not permanent
