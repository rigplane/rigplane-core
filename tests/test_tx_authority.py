"""Unit tests for the transmit-authority vocabulary and pure engine.

Row 1 of the transmit-authority migration: the engine is consumed by nothing,
so every test here drives it directly with real fakes and a fake clock. No
MagicMock anywhere near the authority (repo hard rule).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    SourceMetadata,
)
from rigplane.core.state_store import FieldSnapshot, FreshnessState, StateSnapshot
from rigplane.core.tx_safety import BACKEND_MAX_KEY_DOWN_SECONDS
from rigplane.core.tx_authority import (
    DECISION_LOG_CAPACITY,
    FAMILY_WRITE_CLASS,
    RADIO_READBACK_SOURCES,
    RAW_EXCLUDED,
    TX_ARGUMENT_PREDICATES,
    TX_ENGINE_FAILURE_TAGS,
    TX_READ_DEADLINE_SECONDS,
    BandRelation,
    TransmitAuthority,
    TransmitTruth,
    TxArgumentContext,
    TxFamily,
    TxMethodEntry,
    TxRefusal,
    TxRefusalCode,
    TxStateReading,
    TxWriteClass,
    band_relation,
    build_transmit_truth,
    resolve_band,
)

PTT_PATH = FieldPath.global_("tx_state", "ptt")

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


async def test_view_reports_truth_holds_and_deadline() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    truth = TransmitTruth(
        value=False,
        attributed="rx",
        age_seconds=0.2,
        source="poll_response",
        generation_current=True,
    )
    authority = TransmitAuthority(
        read_transmit_state=link.read,
        last_resort_unkey=FakeUnkey(),
        method_map=METHOD_MAP,
        clock=clock,
        bands=BANDS,
        truth_provider=lambda: truth,
    )
    async with authority.admit("set_ptt", (True,)):
        pass
    view = authority.view()
    assert view.truth is truth
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
# TransmitTruth builder (§3.7)
# --------------------------------------------------------------------------


def _snapshot(
    *,
    value: object,
    source: str,
    observed: float,
    generated: float,
    provider_generation: int = 3,
) -> StateSnapshot:
    field = FieldSnapshot(
        path=PTT_PATH,
        value=value,
        freshness=FreshnessState.FRESH,
        last_observed_monotonic=observed,
        max_age=1.0,
        source=SourceMetadata(source=source, provider="fake"),  # type: ignore[arg-type]
        provider_generation=provider_generation,
    )
    return StateSnapshot(
        state_revision=1,
        freshness_revision=1,
        observation_seq=1,
        generated_at_monotonic=generated,
        fields=(field,),
        provider_generation=provider_generation,
    )


def test_transmit_truth_accepts_radio_readback_provenance() -> None:
    snapshot = _snapshot(
        value=True, source="poll_response", observed=9.5, generated=10.0
    )
    truth = build_transmit_truth(snapshot, provider_generation=3)
    assert truth.value is True
    assert truth.source == "poll_response"
    assert truth.age_seconds == pytest.approx(0.5)
    assert truth.generation_current is True


@pytest.mark.parametrize(
    "source", ["state_poller", "command_response", "local_reconcile", "test"]
)
def test_transmit_truth_rejects_non_readback_provenance(source: str) -> None:
    snapshot = _snapshot(value=True, source=source, observed=9.5, generated=10.0)
    truth = build_transmit_truth(snapshot, provider_generation=3)
    assert truth.value is None
    assert truth.source is None
    assert truth.age_seconds is None


def test_transmit_truth_requires_a_strict_bool() -> None:
    snapshot = _snapshot(value=1, source="poll_response", observed=9.5, generated=10.0)
    assert build_transmit_truth(snapshot, provider_generation=3).value is None


def test_transmit_truth_is_empty_without_the_field() -> None:
    empty = StateSnapshot.empty()
    truth = build_transmit_truth(empty, provider_generation=0)
    assert truth == TransmitTruth(
        value=None,
        attributed=None,
        age_seconds=None,
        source=None,
        generation_current=False,
    )


def test_transmit_truth_marks_a_stale_generation_without_discarding_it() -> None:
    snapshot = _snapshot(
        value=False,
        source="civ_unsolicited",
        observed=9.0,
        generated=10.0,
        provider_generation=2,
    )
    truth = build_transmit_truth(snapshot, provider_generation=3)
    assert truth.value is False
    assert truth.generation_current is False
    assert truth.age_seconds == pytest.approx(1.0)
