"""Transmit-authority conformance matrix over every shipping backend.

ADR row 4 (``docs/plans/2026-08-20-transmit-authority.md`` §4 row 4, §3.10
item 3), following the audio precedent
(``tests/contracts/test_audio_lifecycle_conformance.py``): one scenario matrix,
run for every shipping backend class against its fake link, with the scripted
transmit-state answers this row also lands
(``tests/tx_authority_fakes.py``, §3.10 item 1).

**What this file proves today, and what it merely reserves.**

No backend constructs a :class:`TransmitAuthority` yet — that is rows 7 and 8 —
so the matrix runs each ADR-named row twice:

*Composed rows (live).* The real engine, the real backend class and the real
fake wire, with the admission driven from the test. These prove the component
composes: a hazard write at scripted TX is refused and nothing reaches the
wire; at scripted RX the solicited read precedes the write; an unmapped value
is never receiving; an unkey is never refused; a key arms the one deadline and
the last-resort rail fires the OFF on a fake clock.

*Cutover rows (``xfail(strict=True)``).* The same behaviours driven the way a
consumer will drive them — a plain method call, or a command through
``create_observation_poller``'s queue drain — with **no** test-side admission.
They fail today because the admission is not in the backend. Each names the
migration row that turns it green. ``strict=True`` is the point: the day a
cutover lands, the row goes red as an XPASS, which is the signal rows 7/8 need
that their conformance obligation is met. A row is never weakened to pass
today: an honest xfail says more than an assertion-free green.

**The queue path is not optional.** §3.2's refutation of the wrapping facade
turns on item 2: on FTX-1 and hamlib-provider radios the web write path never
touches the radio object from outside — ``web_startup.py:126-128`` hands the
``CommandQueue`` into ``create_observation_poller`` and the backend drains it
against raw ``self``. A matrix that only called backend methods directly would
prove less than it appears to, so every queue-capable column carries the same
rows again through the drain, plus a live row proving the drain really does
reach the backend write method — so the xfails above it rest on working
plumbing rather than on broken test wiring.

No MagicMock anywhere near the authority (CLAUDE.md hard rule, restated by
§3.10 item 1); the fakes are plain objects and the clock is injected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest
from tx_authority_fakes import (
    CIV_NON_ANSWERS,
    CONFORMANCE_BACKENDS,
    NON_RECEIVING_ANSWERS,
    QUEUE_PATH_BACKENDS,
    TRANSMITTING_ANSWERS,
    TX_ANSWER_VOCABULARY,
    TxConformanceHarness,
    build_harness,
    civ_transmit_state_reply,
)

from rigplane.core.tx_authority import (
    RADIO_READBACK_SOURCES,
    TransmitAuthority,
    TxFamily,
    TxMethodEntry,
    TxRefusal,
    TxRefusalCode,
    TxStateReading,
    build_transmit_truth,
)
from rigplane.core.tx_safety import BACKEND_MAX_KEY_DOWN_SECONDS

# ---------------------------------------------------------------------------
# Pinned literals
# ---------------------------------------------------------------------------

#: The backend columns, as an explicit literal. Never computed from a registry,
#: a factory table or an enum — the ``test_audio_transport_conformance.py:65-81``
#: rule, so a backend that quietly stops shipping fails this file instead of
#: silently shrinking the matrix.
EXPECTED_BACKENDS: tuple[str, ...] = (
    "lan-icom",
    "icom7610-serial",
    "ic705-serial",
    "ic7300-serial",
    "ic9700-serial",
    "yaesu-ftx1",
    "rigctld-client",
)

#: The Icom columns, whose read is a CI-V round trip under the shipped
#: directed-exact-reply discipline. Explicit literal.
CIV_BACKENDS: tuple[str, ...] = (
    "lan-icom",
    "icom7610-serial",
    "ic705-serial",
    "ic7300-serial",
    "ic9700-serial",
)

#: The matrix's own method map. The shipped per-backend maps land at rows 7/8
#: beside the methods they pin (§3.3); this literal covers exactly the methods
#: the matrix drives, and like every other classification literal it is
#: written out, never derived.
CONFORMANCE_METHOD_MAP: Mapping[str, TxMethodEntry] = {
    "set_tuner_status": TxMethodEntry(TxFamily.TUNER),
    "set_tuner": TxMethodEntry(TxFamily.TUNER),
    "set_vfo_slot": TxMethodEntry(TxFamily.VFO_SELECT),
    "set_ptt": TxMethodEntry(TxFamily.PTT_ON, predicate="ptt"),
}


class FakeClock:
    """Deterministic monotonic clock — the same shape ``TxSafetySupervisor``'s
    own tests inject, and the reason no row here sleeps."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingUnkey:
    """The authority's injected last-resort OFF rail, wired to a real backend."""

    def __init__(self, harness: TxConformanceHarness) -> None:
        self._harness = harness
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        await self._harness.unkey()


def build_authority(
    harness: TxConformanceHarness,
    *,
    clock: FakeClock | None = None,
    unkey: RecordingUnkey | None = None,
) -> TransmitAuthority:
    """The real engine over a real backend's real fake wire."""
    return TransmitAuthority(
        read_transmit_state=harness.read_transmit_state,
        last_resort_unkey=unkey or RecordingUnkey(harness),
        method_map=CONFORMANCE_METHOD_MAP,
        clock=clock or FakeClock(),
    )


@pytest.fixture
async def harness(
    request: pytest.FixtureRequest,
) -> AsyncIterator[TxConformanceHarness]:
    built = await build_harness(request.param)
    try:
        yield built
    finally:
        await built.close()


def every_backend(*names: str):
    return pytest.mark.parametrize("harness", names or EXPECTED_BACKENDS, indirect=True)


# ---------------------------------------------------------------------------
# 0. The matrix declares its own shape
# ---------------------------------------------------------------------------


def test_backend_columns_are_an_explicit_literal() -> None:
    """The fakes module and this file agree, and neither computes the set."""
    assert CONFORMANCE_BACKENDS == EXPECTED_BACKENDS
    assert set(CIV_BACKENDS) | {"yaesu-ftx1", "rigctld-client"} == set(
        EXPECTED_BACKENDS
    )


def test_the_matrix_covers_the_audio_matrix_plus_the_rigctld_client() -> None:
    """Every class the audio conformance gate ships, plus the one it excludes.

    ``rigctld_client`` carries no audio surface, so it is absent there; it
    very much ships writes, so it is present here (ADR row 8b).
    """
    from contracts.test_audio_transport_conformance import SHIPPING_BACKENDS

    assert len(SHIPPING_BACKENDS) == 6
    assert len(EXPECTED_BACKENDS) == len(SHIPPING_BACKENDS) + 1


def test_queue_path_columns_are_the_observation_pollable_backends() -> None:
    """``web_startup.py:105-193`` branch 1 — the ingress §3.2 item 2 names."""
    assert QUEUE_PATH_BACKENDS == ("yaesu-ftx1", "rigctld-client")


def test_scripted_answer_vocabulary_pin() -> None:
    assert TX_ANSWER_VOCABULARY == (
        "rx",
        "tx_cat",
        "tx_other",
        "silence",
        "refusal",
        "unmapped",
    )
    assert CIV_NON_ANSWERS == ("ack", "setter_echo", "misaddressed")


# ---------------------------------------------------------------------------
# 1. Fake extensions (§3.10 item 1) — live
# ---------------------------------------------------------------------------


@every_backend()
@pytest.mark.parametrize("answer", TX_ANSWER_VOCABULARY)
async def test_every_backend_answers_every_scripted_answer(
    harness: TxConformanceHarness, answer: str
) -> None:
    """The gate's solicited read runs the backend's real read code for each
    scripted answer, and only a confirmed RX admits a hazard write.

    This is the row that makes every other row in the file meaningful: if a
    column could not produce the answer, its conformance rows would be
    vacuous.

    # MUTATION: in `src/rigplane/core/tx_authority.py`, in `_admit_hazard`,
    # replace `if reading.value or epoch != self._transmit_epoch:` at :619
    # with `if epoch != self._transmit_epoch:` -> this row goes red for both
    # transmitting answers on every column: the gate admits a hazard write
    # while the radio answers "transmitting".
    """
    harness.script(answer)
    authority = build_authority(harness)

    if answer == "rx" and harness.verified_readback:
        async with authority.admit(harness.hazard_method, harness.hazard_args):
            pass  # admitted: the one answer that may proceed
        return

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit(harness.hazard_method, harness.hazard_args):
            pytest.fail(f"{harness.name}: a hazard write was admitted at {answer!r}")

    if answer in TRANSMITTING_ANSWERS and harness.verified_readback:
        assert excinfo.value.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    elif answer == "unmapped":
        # Either fail-closed direction is correct and the columns differ:
        # Yaesu's positive map has no entry for `TX9`, so `read_ptt` answers
        # *transmitting*; the CI-V and hamlib-provider columns produce no
        # value at all. What must never happen is admission.
        assert excinfo.value.code in tuple(TxRefusalCode)
    else:
        assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
    assert excinfo.value.evidence is not None  # INV-14


@every_backend()
async def test_a_confirmed_rx_and_a_keyed_radio_read_their_values(
    harness: TxConformanceHarness,
) -> None:
    """The scripted answers are distinguishable on the wire, not merely named."""
    harness.script("rx")
    assert (await harness.read_transmit_state()).value is False
    harness.script("tx_cat")
    assert (await harness.read_transmit_state()).value is True
    harness.script("tx_other")
    assert (await harness.read_transmit_state()).value is True


@pytest.mark.parametrize("harness", CIV_BACKENDS, indirect=True)
@pytest.mark.parametrize("shape", CIV_NON_ANSWERS)
async def test_a_civ_read_is_not_satisfied_by_an_ack_echo_or_misaddressed_frame(
    harness: TxConformanceHarness, shape: str
) -> None:
    """INV-13 on the CI-V family: the read is directed and exact.

    An ACK, our own setter echo and a frame from a foreign bus address all go
    onto the wire; none of them may answer the read. The discrimination is the
    product's (``_civ_rx.py:1489-1492`` routing guards, ``:2636-2650``
    provenance narrowing), not the fake's — the fake queues the bytes and asks
    what the shipped code does with them.

    # MUTATION (two edits, because two shipped checks stand in series): in
    # `src/rigplane/runtime/_civ_rx.py`, (a) delete both routing guards at
    # :1489-1492 (`from_addr != radio_addr` and `to_addr not in
    # (CONTROLLER_ADDR, 0x00)`), and (b) replace the six conjuncts at
    # :2639-2645 with `frame.command == 0x1C\n and frame.sub == 0x00`
    # -> the `setter_echo` and `misaddressed` cases go red on all five CI-V
    # columns (10 rows): both carry a `00` data byte, so a widened
    # classification makes each of them answer "receiving".
    # Deleting only guard (a) kills nothing — verified — because the
    # provenance narrowing at (b) re-checks `from_addr` itself; the `ack`
    # case is held by a third mechanism again, the decode ladder having no
    # PTT branch for `0xFB` at all, and survives this mutation.
    """
    harness.script(shape)
    reading = await harness.read_transmit_state()
    assert reading.value is None, f"{harness.name}: {shape} satisfied the read"

    authority = build_authority(harness)
    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit(harness.hazard_method, harness.hazard_args):
            pytest.fail(f"{harness.name}: {shape} admitted a hazard write")
    assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE


@pytest.mark.parametrize("harness", CIV_BACKENDS, indirect=True)
async def test_the_civ_unmapped_shape_would_read_receiving_if_the_check_lapsed(
    harness: TxConformanceHarness,
) -> None:
    """The unmapped CI-V answer is a fail-open canary, not junk.

    ``1C 00`` + ``00 00`` carries a leading ``0x00``: if the exact-shape check
    were loosened it would decode as *receiving* and admit a relay throw. A
    junk byte would decode as transmitting and could never catch that.

    # MUTATION: in `src/rigplane/runtime/_civ_rx.py`, delete the
    # `and len(frame.data) == 1` conjunct at :2643 -> this row goes red: the
    # two-byte reply earns `poll_response` and reads as receiving.
    """
    frame = civ_transmit_state_reply("unmapped", harness.radio._radio_addr)
    assert frame is not None and frame[-2:-1] == b"\x00"

    harness.script("unmapped")
    reading = await harness.read_transmit_state()
    assert reading.value is not False, "an unmapped CI-V shape read as receiving"
    assert reading.value is None
    assert reading.failure is not None  # INV-14: the cause travels


@every_backend()
async def test_an_unmapped_transmit_state_value_is_never_receiving(
    harness: TxConformanceHarness,
) -> None:
    """INV-9 / §3.7: RX is only ever produced by a positive mapping.

    On Yaesu the unmapped ``TX9`` fails closed to *transmitting*; on the CI-V
    and hamlib-provider columns it produces no value at all. Both are "not
    receiving"; neither may admit a hazard write.

    # MUTATION: in `src/rigplane/backends/yaesu_cat/radio.py`, change
    # `return bool(state != "0")` at :1027 to `return bool(state == "1")`
    # -> this row goes red on the `yaesu-ftx1` column (MOR-1905's own
    # mutation): the unmapped `TX9` reads as receiving.
    """
    harness.script("unmapped")
    reading = await harness.read_transmit_state()
    assert reading.value is not False, f"{harness.name}: unmapped value read as RX"

    authority = build_authority(harness)
    with pytest.raises(TxRefusal):
        async with authority.admit(harness.hazard_method, harness.hazard_args):
            pytest.fail(f"{harness.name}: an unmapped value admitted a hazard write")


@every_backend()
@pytest.mark.parametrize("answer", NON_RECEIVING_ANSWERS)
async def test_no_failing_answer_ever_resolves_to_receiving(
    harness: TxConformanceHarness, answer: str
) -> None:
    """Silence, a refusal and an unmapped value all fail closed (§3.3 table)."""
    harness.script(answer)
    assert (await harness.read_transmit_state()).value is not False


# ---------------------------------------------------------------------------
# 2. Composed conformance — the real engine over the real backend, live
# ---------------------------------------------------------------------------


@every_backend()
async def test_a_hazard_write_at_scripted_tx_is_refused_and_no_wire_write_occurs(
    harness: TxConformanceHarness,
) -> None:
    """§3.10 item 3, row 1. The refusal is worthless if the bytes went anyway.

    # MUTATION: in `src/rigplane/core/tx_authority.py`, in `admit()`, change
    # `if write_class is TxWriteClass.PASS:` at :522 to
    # `if write_class in (TxWriteClass.PASS, TxWriteClass.HAZARD):` -> this
    # row goes red: the hazard write is yielded with no truth consulted and
    # the tuner frame reaches the wire during transmit.
    """
    harness.script("tx_cat")
    authority = build_authority(harness)
    writes_before = len(harness.writes())

    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit(harness.hazard_method, harness.hazard_args):
            await harness.hazard()

    assert harness.writes()[writes_before:] == [], (
        f"{harness.name}: a refused hazard write still reached the wire"
    )
    if harness.verified_readback:
        assert excinfo.value.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    else:
        # §3.7: the hamlib-provider answer is an upstream cache, never radio
        # truth, so its hazard families are fail-closed by provenance.
        assert excinfo.value.code is TxRefusalCode.TX_TRUTH_UNAVAILABLE
        assert excinfo.value.evidence.failure == "unverifiable-provenance"


@every_backend(*CIV_BACKENDS, "yaesu-ftx1")
async def test_a_hazard_write_at_scripted_rx_reads_before_it_writes(
    harness: TxConformanceHarness,
) -> None:
    """§3.10 item 3, row 2: on the wire, the read precedes the write.

    Counts cannot see this — both happen either way. Only the order on the
    wire distinguishes a solicited read from a cached one (T3/INV-4).

    The ``rigctld-client`` column is absent by design, not by omission: its
    readback is permanently unverifiable (§3.7), so it never reaches a write
    to order the read against. ``test_..._refused_and_no_wire_write_occurs``
    covers it instead.

    # MUTATION: in `src/rigplane/core/tx_authority.py`, in `admit()`, change
    # `if write_class is TxWriteClass.PASS:` at :522 to
    # `if write_class in (TxWriteClass.PASS, TxWriteClass.HAZARD):` -> this
    # row goes red: no read frame precedes the write on any column.
    """
    harness.script("rx")
    authority = build_authority(harness)
    baseline = len(harness.wire())

    async with authority.admit(harness.hazard_method, harness.hazard_args):
        await harness.hazard()

    wire = list(harness.wire())[baseline:]
    reads = [i for i, entry in enumerate(wire) if harness.is_read(entry)]
    writes = [i for i, entry in enumerate(wire) if not harness.is_read(entry)]
    assert reads, f"{harness.name}: the hazard admission performed no read"
    assert writes, f"{harness.name}: the admitted hazard write never happened"
    assert reads[-1] < writes[0], (
        f"{harness.name}: the write preceded the read it was authorised by: {wire}"
    )


@every_backend()
@pytest.mark.parametrize("answer", ["tx_cat", "silence", "unmapped"])
async def test_an_unkey_is_never_refused(
    harness: TxConformanceHarness, answer: str
) -> None:
    """§3.10 item 3, row 6 / INV-5: the one-sided unkey, on every column.

    Driven under the most hostile truth each wire can produce — keyed, silent,
    unmapped — because an unkey that only works when truth is healthy is not
    the doctrine (``managed_tx_ingress.py:1-21``).

    # MUTATION: in `src/rigplane/core/tx_authority.py`, in `admit()`, insert
    # `raise TxRefusal(TxRefusalCode.TX_TRUTH_UNAVAILABLE, TxEvidence())` as
    # the first statement of the `if write_class is TxWriteClass.UNKEY:`
    # branch at :526 -> this row goes red on every column.
    """
    harness.script(answer)
    authority = build_authority(harness)
    writes_before = len(harness.writes())

    async with authority.admit("set_ptt", (False,)):
        await harness.unkey()

    assert harness.writes()[writes_before:], f"{harness.name}: the unkey never landed"


@every_backend()
async def test_our_own_key_makes_the_gate_stricter_never_looser(
    harness: TxConformanceHarness,
) -> None:
    """INV-16 / §3.7's own-commands rule, composed over a real backend.

    The wire is scripted to answer *receiving* throughout — the B6 blind spot,
    where the IC-7300 reported RX while audibly still sending. Our own key
    must refuse the hazard write anyway, with no wire read at all.

    # MUTATION: in `src/rigplane/core/tx_authority.py`, in `_admit_hazard`,
    # delete the step-1 `if held is not None:` refusal at :580-584 -> this row
    # goes red on every column: the scripted RX answer clears our own key.
    """
    harness.script("rx")
    authority = build_authority(harness)

    async with authority.admit("set_ptt", (True,)):
        await harness.key()

    reads_before = len(harness.reads())
    with pytest.raises(TxRefusal) as excinfo:
        async with authority.admit(harness.hazard_method, harness.hazard_args):
            pytest.fail(f"{harness.name}: a hazard write ran during our own key")

    assert excinfo.value.code is TxRefusalCode.REFUSED_WHILE_TRANSMITTING
    assert excinfo.value.evidence.own_transmit_hold == "key"
    assert harness.reads()[reads_before:] == [], (
        f"{harness.name}: the own-transmit refusal touched the wire"
    )


@every_backend()
async def test_a_key_arms_the_one_deadline_and_the_off_fires_on_a_fake_clock(
    harness: TxConformanceHarness,
) -> None:
    """§3.10 item 3, row 5, backend half: the arm and the last-resort rail.

    The three *shipped* drivers (web ``call_later``, the observation poller's
    drain, the rigctld drain) arrive at rows 12a/12b and are reserved below;
    what is live here is that a key through the authority arms the one
    deadline and that the OFF, once due, reaches the real backend's real
    unkey on the real wire.

    # MUTATION: in `src/rigplane/core/tx_authority.py`, in `_commit`, change
    # `self._deadline = deadline` at :651 to `self._deadline = None` -> this
    # row goes red on every column: the key arms nothing and no OFF is due.
    """
    harness.script("rx")
    clock = FakeClock()
    unkey = RecordingUnkey(harness)
    authority = build_authority(harness, clock=clock, unkey=unkey)

    async with authority.admit("set_ptt", (True,)):
        await harness.key()

    assert authority.view().deadline_monotonic == pytest.approx(
        clock.now + BACKEND_MAX_KEY_DOWN_SECONDS
    )
    assert authority.poll(clock.now) == (), "the deadline fired early"

    clock.advance(BACKEND_MAX_KEY_DOWN_SECONDS + 0.001)
    due = authority.poll(clock.now)
    assert len(due) == 1

    writes_before = len(harness.writes())
    await authority.fire_last_resort_unkey()
    assert unkey.calls == 1
    assert harness.writes()[writes_before:], (
        f"{harness.name}: the due OFF never reached the wire"
    )


@every_backend()
async def test_a_pass_class_write_never_consults_transmit_truth(
    harness: TxConformanceHarness,
) -> None:
    """INV-3, composed: a poisoned read must not be reachable from PASS.

    ``set_ptt(False)`` resolves through the T5 short circuit and ``set_freq``
    is absent from the matrix map, so the row uses the unkey — the one write
    that is structurally ahead of every table.
    """

    async def poisoned() -> TxStateReading:  # pragma: no cover - must not run
        raise AssertionError("transmit truth was consulted on a non-hazard write")

    authority = TransmitAuthority(
        read_transmit_state=poisoned,
        last_resort_unkey=RecordingUnkey(harness),
        method_map=CONFORMANCE_METHOD_MAP,
        clock=FakeClock(),
    )
    async with authority.admit("set_ptt", (False,)):
        await harness.unkey()


@pytest.mark.parametrize("harness", QUEUE_PATH_BACKENDS, indirect=True)
async def test_the_queue_drain_reaches_the_backend_write_method(
    harness: TxConformanceHarness,
) -> None:
    """The D1 ingress is real, and this file can drive it.

    Live on purpose: the queue-path cutover rows below are ``xfail``, and an
    xfail proves nothing if the plumbing under it never worked. ``PttOff`` is
    the probe because it is ``ALWAYS_PASS`` at the old seat still standing
    above the authority (row 11 deletes that seat), so this row measures the
    drain rather than the seat.
    """
    harness.script("rx")
    queue = harness.extras["queue"]
    assert harness.drain is not None and harness.queue_unkey is not None

    writes_before = len(harness.writes())
    queue.put_ordered(harness.queue_unkey)
    await harness.drain()

    assert harness.writes()[writes_before:], (
        f"{harness.name}: a queued command never reached the backend write method"
    )


@pytest.mark.parametrize("harness", QUEUE_PATH_BACKENDS, indirect=True)
async def test_a_set_ptt_write_alone_produces_no_observation(
    harness: TxConformanceHarness,
) -> None:
    """§3.10 item 3, row 7 / INV-8: a write outcome is never evidence of RF.

    A bare ``set_ptt`` — the harness's direct call on the radio object, not
    the queue-drained write ``test_the_queue_drain_reaches_the_backend_write_method``
    already proves reaches the backend — only writes to the wire and updates
    the legacy ``radio_state`` cache (``yaesu_cat/radio.py:987-994`` and the
    rigctld-client ``set_ptt`` equivalent); neither touches
    ``self._observation_callback`` at all. So ``observations == []`` measured
    right after the bare write, with no poll cycle ever run, was unreachable
    under today's code and under every mutation this file declares — the two
    paths only meet once something drives a poll. It was not unfalsifiable:
    a write path patched to publish its own observation does redden even the
    old row. It was unreached, which is enough to make a green meaningless.

    What the row needs only shows up once a poll cycle actually runs, so this
    drives one for real — the same method the production polling loop calls
    (``YaesuCatPoller._emit_medium_observations`` /
    ``RigctldClientObservationPoller._poll_medium``), over the same scripted
    wire the bare write just used, no sleep and no mock. A genuine
    ``yaesu_poll_response`` / ``hamlib_response`` readback of the current PTT
    state is expected and correct — §3.7's ``verified_readback`` is what
    decides whether that readback may be trusted, not this row. What must
    never appear is a ``global.tx_state.ptt`` observation under any other
    source: that would be a write outcome wearing a readback's clothes.

    # MUTATION: in `src/rigplane/backends/yaesu_cat/observations.py`, in
    # `YaesuObservationAdapter._adapter` at :1141, change
    # `source="yaesu_poll_response"` to `source="command_response"` -> this
    # row goes red on the `yaesu-ftx1` column: the real PTT readback the
    # driven poll cycle takes now carries a producer-side source, and
    # `laundered` stops being empty.
    #
    # MUTATION: in `src/rigplane/backends/yaesu_cat/observations.py`, remove
    # the `_PTT` branch at :328-333 so the poll cycle stops reading PTT at
    # all -> the liveness guard below goes red on the `yaesu-ftx1` column.
    # Without that guard the row would pass on an empty `laundered` list
    # again, which is the same silent vacuity in a narrower disguise: a
    # non-empty `observations` proves a cycle ran, not that it read the one
    # field this row is about.
    """
    observations = harness.extras["observations"]
    observations.clear()
    harness.script("rx")

    await harness.key()
    await harness.unkey()

    poller = harness.extras["poller"]
    if harness.name == "yaesu-ftx1":
        await poller._emit_medium_observations()
    else:
        await poller._poll_medium()

    assert any(str(obs.path) == "global.tx_state.ptt" for obs in observations), (
        f"{harness.name}: the driven poll cycle read no transmit state — "
        "this row proves nothing without the field under test to inspect"
    )
    laundered = [
        obs
        for obs in observations
        if str(obs.path) == "global.tx_state.ptt"
        and str(obs.source.source) not in RADIO_READBACK_SOURCES
    ]
    assert laundered == [], (
        f"{harness.name}: a self-write produced a transmit-truth observation"
    )


@pytest.mark.parametrize("harness", CIV_BACKENDS, indirect=True)
async def test_an_icom_self_write_leaves_the_store_silent_on_transmit_truth(
    harness: TxConformanceHarness,
) -> None:
    """The same row on the CI-V columns, read off the canonical store."""
    await harness.key()
    await harness.unkey()
    await asyncio.sleep(0)

    store = harness.radio._state_store
    truth = build_transmit_truth(
        store.snapshot(), provider_generation=store.provider_generation
    )
    assert truth.value is None, f"{harness.name}: our own set_ptt became transmit truth"


# ---------------------------------------------------------------------------
# 3. Cutover rows — reserved, individually reasoned, strict
# ---------------------------------------------------------------------------


@every_backend()
@pytest.mark.xfail(
    strict=True,
    reason="row 7 (Icom) / row 8 (Yaesu, rigctld-client): no backend "
    "constructs a TransmitAuthority yet, so INV-15 cannot hold. Turns green "
    "when the admission is constructed in every connect path.",
)
async def test_a_backend_constructs_its_own_transmit_authority(
    harness: TxConformanceHarness,
) -> None:
    """INV-15: no gated write executes without a constructed authority."""
    authority = getattr(harness.radio, "_tx_authority", None)
    assert isinstance(authority, TransmitAuthority)


@every_backend()
@pytest.mark.xfail(
    strict=True,
    reason="row 7 (Icom) / row 8 (Yaesu, rigctld-client): the admission call "
    "is not yet at the top of the gated write methods, so a plain method "
    "call is ungated. The composed row above already proves the engine "
    "refuses; this reserves the seat inside the backend.",
)
async def test_a_backend_method_call_is_refused_at_scripted_tx(
    harness: TxConformanceHarness,
) -> None:
    """The ADR's row 1, driven the way every consumer drives it."""
    harness.script("tx_cat")
    writes_before = len(harness.writes())
    with pytest.raises(TxRefusal):
        await harness.hazard()
    assert harness.writes()[writes_before:] == []


@every_backend()
@pytest.mark.xfail(
    strict=True,
    reason="row 5 (the read primitive) + row 7/8 (the admission): a plain "
    "method call performs no solicited read, so nothing precedes the write.",
)
async def test_a_backend_method_call_reads_before_it_writes_at_scripted_rx(
    harness: TxConformanceHarness,
) -> None:
    """The ADR's row 2, driven the way every consumer drives it."""
    harness.script("rx")
    baseline = len(harness.wire())
    await harness.hazard()
    wire = list(harness.wire())[baseline:]
    reads = [i for i, entry in enumerate(wire) if harness.is_read(entry)]
    writes = [i for i, entry in enumerate(wire) if not harness.is_read(entry)]
    assert reads and writes and reads[-1] < writes[0]


@pytest.mark.parametrize("harness", QUEUE_PATH_BACKENDS, indirect=True)
@pytest.mark.xfail(
    strict=True,
    reason="row 8 (the admission at each backend's real write surface) and, "
    "on Yaesu, row 11 (deleting the poller's own seat above it): the queue "
    "drain reaches an ungated backend body. This is the D1 ingress §3.2 "
    "item 2 names — the one a wrapping facade could never have seen.",
)
async def test_a_queued_hazard_write_is_refused_at_scripted_tx(
    harness: TxConformanceHarness,
) -> None:
    """The ADR's row 1 again, through ``create_observation_poller``'s drain."""
    harness.script("tx_cat")
    queue = harness.extras["queue"]
    assert harness.drain is not None

    writes_before = len(harness.writes())
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    queue.put_ordered(harness.queue_command, future=future)
    await harness.drain()

    assert future.done()
    assert isinstance(future.exception(), TxRefusal)
    assert harness.writes()[writes_before:] == []


@pytest.mark.parametrize("harness", QUEUE_PATH_BACKENDS, indirect=True)
@pytest.mark.xfail(
    strict=True,
    reason="row 5 (the read primitive) + row 8 (the admission) and, on "
    "Yaesu, row 11: the drain reaches the backend body with no solicited "
    "read in front of it.",
)
async def test_a_queued_hazard_write_reads_before_it_writes_at_scripted_rx(
    harness: TxConformanceHarness,
) -> None:
    """The ADR's row 2 again, through the queue ingress."""
    harness.script("rx")
    queue = harness.extras["queue"]
    assert harness.drain is not None

    baseline = len(harness.wire())
    queue.put_ordered(harness.queue_command)
    await harness.drain()

    wire = list(harness.wire())[baseline:]
    reads = [i for i, entry in enumerate(wire) if harness.is_read(entry)]
    writes = [i for i, entry in enumerate(wire) if not harness.is_read(entry)]
    assert reads and writes and reads[-1] < writes[0]


@every_backend()
@pytest.mark.xfail(
    strict=True,
    reason="row 5: `read_transmit_state()` lands on the new capability "
    "protocol `TransmitStateReadable` (5a Icom, 5b Yaesu + rigctld-client). "
    "The harness assembles the equivalent from shipped parts meanwhile.",
)
async def test_a_backend_exposes_the_row_five_read_primitive(
    harness: TxConformanceHarness,
) -> None:
    """INV-13's home: one primitive, per backend, returning typed evidence."""
    reading = await harness.radio.read_transmit_state()
    assert isinstance(reading, TxStateReading)


@pytest.mark.parametrize("harness", ["rigctld-client"], indirect=True)
@pytest.mark.xfail(
    strict=True,
    reason="row 5b: the backend's own primitive must carry "
    "`verified_readback=False` permanently (§3.7). The harness already marks "
    "it, and the composed refusal row above proves the gate honours the "
    "marking; this reserves the marking on the shipped primitive.",
)
async def test_the_rigctld_client_primitive_marks_its_readback_unverified(
    harness: TxConformanceHarness,
) -> None:
    harness.script("rx")
    reading = await harness.radio.read_transmit_state()
    assert reading.verified_readback is False


@pytest.mark.parametrize("harness", ["yaesu-ftx1"], indirect=True)
@pytest.mark.xfail(
    strict=True,
    reason="row 6 (Yaesu truth honesty): `tx_state_map` turns the "
    "three-valued `TX;` answer into an attribution. Today `read_ptt` "
    "collapses it to a bool, so `tx_other` (the front-panel key) is "
    "indistinguishable from `tx_cat` in the evidence.",
)
async def test_yaesu_attribution_reaches_the_evidence(
    harness: TxConformanceHarness,
) -> None:
    """§3.7: attribution is a per-vendor capability, carried not discarded."""
    harness.script("tx_other")
    assert (await harness.read_transmit_state()).attributed == "tx_other"
    harness.script("tx_cat")
    assert (await harness.read_transmit_state()).attributed == "tx_cat"


@pytest.mark.parametrize("harness", ["yaesu-ftx1"], indirect=True)
@pytest.mark.xfail(
    strict=True,
    reason="row 6: `set_ptt` self-mutates the legacy mirror "
    "(`backends/yaesu_cat/radio.py:994`). The row deletes that write; until "
    "then our own command is still a claim about RF somewhere in the tree.",
)
async def test_a_yaesu_self_write_leaves_no_transmit_truth_claim_anywhere(
    harness: TxConformanceHarness,
) -> None:
    """The self-write launder, at its last surviving Yaesu address."""
    before = harness.radio._state.ptt
    await harness.key()
    assert harness.radio._state.ptt == before


@every_backend()
@pytest.mark.xfail(
    strict=True,
    reason="rows 12a/12b: the shipped deadline drivers. 12a wires the web "
    "`call_later` (Icom branch) and the observation poller's drain to the "
    "authority's deadline, both enqueuing `PttOff`; 12b wires the rigctld "
    "drain and the Icom backend loops. Until then the only rail is the "
    "authority's own last resort, which the live row above already exercises.",
)
async def test_a_shipped_driver_polls_the_authority_deadline(
    harness: TxConformanceHarness,
) -> None:
    """INV-7: every delivery carries a driver for the one deadline."""
    driver = getattr(harness.radio, "_tx_authority_deadline_driver", None)
    assert driver is not None
