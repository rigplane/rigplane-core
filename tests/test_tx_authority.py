"""Unit tests for the transmit-authority vocabulary and pure engine.

Row 1 of the transmit-authority migration: the engine is consumed by nothing,
so every test here drives it directly with real fakes and a fake clock. No
MagicMock anywhere near the authority (repo hard rule).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import MISSING, FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from rigplane.core.tx_observation import (
    RADIO_READBACK_SOURCES,
    TX_READ_DEADLINE_SECONDS,
    TxStateReading,
)
from rigplane.core import tx_authority
from rigplane.core.tx_authority import (
    FAMILY_WRITE_CLASS,
    TransmitAuthority,
    TxFamily,
    TxMethodEntry,
    TxRefusal,
    TxRefusalCode,
    TxWriteClass,
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


METHOD_MAP: Mapping[str, TxMethodEntry] = {
    "set_ptt": TxMethodEntry(family=TxFamily.PTT_ON),
    "set_powerstat": TxMethodEntry(family=TxFamily.POWER_ON),
    "set_freq": TxMethodEntry(TxFamily.FREQUENCY),
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
    method_map: Mapping[str, TxMethodEntry] | None = None,
) -> TransmitAuthority:
    return TransmitAuthority(
        read_transmit_state=link.read,
        method_map=METHOD_MAP if method_map is None else method_map,
        clock=clock,
    )


# --------------------------------------------------------------------------
# Pinned literals (INV-8)
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
    assert FAMILY_WRITE_CLASS[TxFamily.FREQUENCY] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.MODE] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.VFO_TOPOLOGY] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.POWER_ON] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.CW_STOP] is TxWriteClass.PASS


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


def test_dormant_authority_watchdog_surface_is_absent() -> None:
    """The authority retains admission records, not a second TX watchdog."""
    for name in ("_Hold", "TxDeadlineExpiry"):
        assert not hasattr(tx_authority, name)
    assert not hasattr(TransmitAuthority, "poll")
    assert not hasattr(TransmitAuthority, "fire_last_resort_unkey")

    parameters = inspect.signature(TransmitAuthority).parameters
    for name in (
        "last_resort_unkey",
        "lease_active",
        "cw_hold_duration",
        "max_key_down_seconds",
    ):
        assert name not in parameters

    authority = TransmitAuthority(
        read_transmit_state=PoisonedLink().read,
        method_map=METHOD_MAP,
        clock=Clock(),
    )
    for name in ("_last_resort_unkey", "_holds", "_deadline", "_lease_active"):
        assert not hasattr(authority, name)
    assert [field.name for field in fields(tx_authority.TxAdmission)] == [
        "family",
        "write_class",
        "evidence",
    ]
    assert "own_transmit_hold" not in get_type_hints(tx_authority.TxEvidence)


def test_dormant_decision_surface_is_absent() -> None:
    """MOR-2179 leaves the authority as an admission gate, not an audit ring."""
    for name in (
        "DECISION_LOG_CAPACITY",
        "TX_ENGINE_FAILURE_TAGS",
        "RAW_EXCLUDED",
        "TxDecisionRecord",
        "TxAuthorityView",
    ):
        assert not hasattr(tx_authority, name)

    assert "provider_generation" not in inspect.signature(TransmitAuthority).parameters
    assert not hasattr(TransmitAuthority, "view")
    assert not hasattr(TransmitAuthority, "_commit")
    assert not hasattr(TransmitAuthority, "_record")

    authority = TransmitAuthority(
        read_transmit_state=PoisonedLink().read,
        method_map=METHOD_MAP,
        clock=Clock(),
    )
    for name in ("_provider_generation", "_records"):
        assert not hasattr(authority, name)

    assert get_type_hints(tx_authority.TxAdmission)["family"] is TxFamily


def test_dormant_argument_resolution_surface_is_absent() -> None:
    for name in (
        "_UnresolvedArgument",
        "UNRESOLVED_ARGUMENT",
        "SIGNATURE_CACHE_SIZE",
        "_first_parameter_name",
        "first_parameter_name",
        "TxArgumentContext",
        "ArgumentPredicate",
        "ptt_family",
        "powerstat_family",
        "TX_ARGUMENT_PREDICATES",
        "ARGUMENT_SHORT_CIRCUIT_METHODS",
        "short_circuit_family",
    ):
        assert not hasattr(tx_authority, name)

    assert [field.name for field in fields(TxMethodEntry)] == ["family"]
    assert "target" not in inspect.signature(TransmitAuthority.admit).parameters
    assert "UNKEY" not in TxWriteClass.__members__
    assert "PTT_OFF" not in TxFamily.__members__
    assert "POWER_OFF" not in TxFamily.__members__


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "family", "write_class"),
    (
        ("set_ptt", (False,), {}, TxFamily.PTT_ON, TxWriteClass.KEYING),
        ("set_ptt", (), {"on": False}, TxFamily.PTT_ON, TxWriteClass.KEYING),
        ("set_powerstat", (False,), {}, TxFamily.POWER_ON, TxWriteClass.PASS),
        ("set_powerstat", (), {"on": False}, TxFamily.POWER_ON, TxWriteClass.PASS),
    ),
)
async def test_fixed_ptt_and_powerstat_families_ignore_arguments(
    method: str,
    args: tuple[object, ...],
    kwargs: Mapping[str, object],
    family: TxFamily,
    write_class: TxWriteClass,
) -> None:
    authority = build_authority(Clock(), PoisonedLink())

    async with authority.admit(method, args, kwargs) as admission:
        assert admission.family is family
        assert admission.write_class is write_class


def test_dormant_band_classification_surface_is_absent() -> None:
    """Frequency remains a PASS family without a second band classifier."""
    for name in (
        "BandRelation",
        "resolve_band",
        "band_relation",
        "frequency_family",
    ):
        assert not hasattr(tx_authority, name)

    parameters = inspect.signature(TransmitAuthority).parameters
    assert "bands" not in parameters
    assert "current_frequency_hz" not in parameters

    authority = TransmitAuthority(
        read_transmit_state=PoisonedLink().read,
        method_map=METHOD_MAP,
        clock=Clock(),
    )
    assert not hasattr(authority, "_bands")
    assert not hasattr(authority, "_current_frequency_hz")


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


async def test_hazard_at_confirmed_rx_reads_before_the_write() -> None:
    clock = Clock()
    link = FakeTransmitStateLink(clock)
    link.script(RX)
    authority = build_authority(clock, link)
    wire: list[str] = []
    link.on_read.append(lambda: wire.append("read"))

    async with authority.admit("set_antenna_1", ()) as admission:
        wire.append("write")

    assert wire == ["read", "write"]
    assert admission.family is TxFamily.ANTENNA
    assert admission.write_class is TxWriteClass.HAZARD
    assert admission.evidence is not None
    assert admission.evidence.solicited is True
    assert admission.evidence.value is False
    assert admission.evidence.age_seconds == pytest.approx(link.read_latency)


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
    """§3.7: an unverified readback cannot admit a hazard write."""
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


# --------------------------------------------------------------------------
# KEYING
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

    # MUTATION: in `src/rigplane/core/tx_authority.py`, dedent `yield ticket`
    # so it sits after the `async with self._lock:` body rather than inside it
    # -> this row goes red with
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


@pytest.mark.parametrize(
    "method",
    ("send_civ", "send_civ_transaction", "send_civ_raw_fire_and_forget"),
)
async def test_former_raw_methods_fail_closed_as_unclassified(method: str) -> None:
    """Former raw exclusions are unmapped writes and therefore typed refusals."""
    authority = build_authority(Clock(), PoisonedLink())

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit(method, (b"\xfe\xfe",)):
            pytest.fail("an unmapped raw write reached the wire")

    assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert excinfo.value.evidence.failure == "unclassified"
