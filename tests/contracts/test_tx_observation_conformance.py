"""Transmit-observation conformance contracts over every shipping backend.

The rows pin observation primitives, parser discrimination, provenance, and
queue liveness against every backend's real fake wire.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import fields
from pathlib import Path

import pytest
from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer
from tx_observation_fakes import (
    CIV_NON_ANSWERS,
    CONFORMANCE_BACKENDS,
    NON_RECEIVING_ANSWERS,
    QUEUE_PATH_BACKENDS,
    TRANSMITTING_ANSWERS,
    TX_ANSWER_VOCABULARY,
    TxObservationHarness,
    build_harness,
    civ_transmit_state_reply,
)

from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat import YaesuCatRadio
from rigplane.core.tx_observation import RADIO_READBACK_SOURCES, TxStateReading

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


@pytest.fixture
async def harness(
    request: pytest.FixtureRequest,
) -> AsyncIterator[TxObservationHarness]:
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


def test_observation_harness_surface_is_named_for_its_surviving_contract() -> None:
    assert (
        Path(__file__).name,
        tuple(field.name for field in fields(TxObservationHarness)),
    ) == (
        "test_tx_observation_conformance.py",
        (
            "name",
            "radio",
            "script",
            "read_transmit_state",
            "wire",
            "is_read",
            "key",
            "unkey",
            "close",
            "queue_unkey",
            "drain",
            "extras",
        ),
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
    harness: TxObservationHarness, answer: str
) -> None:
    """Every column produces each declared answer through its real read path."""
    harness.script(answer)
    reading = await harness.read_transmit_state()

    if answer == "rx":
        assert reading.value is False
    elif answer in TRANSMITTING_ANSWERS:
        assert reading.value is True
    else:
        assert reading.value is not False


@every_backend()
async def test_a_confirmed_rx_and_a_keyed_radio_read_their_values(
    harness: TxObservationHarness,
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
    harness: TxObservationHarness, shape: str
) -> None:
    """INV-13 on the CI-V family: the read is directed and exact.

    An ACK, our own setter echo and a frame from a foreign bus address all go
    onto the wire; none of them may answer the read. The discrimination is the
    product's (``_civ_rx.py:1489-1492`` routing guards, ``:2636-2650``
    provenance narrowing), not the fake's — the fake queues the bytes and asks
    what the shipped code does with them.

    What these three rows actually measure, so nobody cites them as proof of
    the primitive's own shape check: empirically all three come back
    ``failure='timeout'`` on every CI-V column, because the RX pump's
    routing guards (``_civ_rx.py:1489-1492``) drop ``setter_echo`` and
    ``misaddressed`` before either ever reaches the primitive's own
    narrowing, and the decode ladder has no PTT branch for ``ack``'s
    ``0xFB`` at all — none of the three ever reach the primitive's shape
    check (``:2636-2650``) to exercise it. That check *is* load-bearing —
    see ``test_the_civ_unmapped_shape_would_read_receiving_if_the_check_lapsed``
    below, which reaches it with a correctly-addressed but wrong-shaped
    reply and is the row that actually proves it.

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


@pytest.mark.parametrize("harness", CIV_BACKENDS, indirect=True)
async def test_the_civ_unmapped_shape_would_read_receiving_if_the_check_lapsed(
    harness: TxObservationHarness,
) -> None:
    """The unmapped CI-V answer is a fail-open canary, not junk.

    ``1C 00`` + ``00 00`` carries a leading ``0x00``: if the exact-shape check
    were loosened it would decode as *receiving*. A junk byte would decode as
    transmitting and could never catch that.

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
    harness: TxObservationHarness,
) -> None:
    """INV-9 / §3.7: RX is only ever produced by a positive mapping.

    On Yaesu the unmapped ``TX9`` resolves to *transmitting*; on the CI-V and
    hamlib-provider columns it produces no value at all. Both are "not
    receiving".

    # MUTATION (MOR-1914, restated -- the predicate moved out of `read_ptt`
    # into a shared helper both `read_ptt` and `read_transmit_state` call):
    # in `src/rigplane/backends/yaesu_cat/radio.py`, in
    # `_interpret_ptt_token`, change `return not policy.is_receiving(state)`
    # at :1090 to `return policy.is_receiving(state)` -> this row goes red on
    # the `yaesu-ftx1` column (MOR-1905's own inversion direction): the
    # unmapped `TX9` reads as receiving. The decoy for this row is :1087
    # (the empty-`tx_state_map` fallback branch, `_interpret_ptt_token`'s
    # other `return`) -- `yaesu-ftx1` ships a populated `tx_state_map`, so
    # that branch never executes here; mutating it leaves this row green.
    """
    harness.script("unmapped")
    reading = await harness.read_transmit_state()
    assert reading.value is not False, f"{harness.name}: unmapped value read as RX"


@every_backend()
@pytest.mark.parametrize("answer", NON_RECEIVING_ANSWERS)
async def test_no_failing_answer_ever_resolves_to_receiving(
    harness: TxObservationHarness, answer: str
) -> None:
    """Silence, a refusal and an unmapped value all fail closed (§3.3 table)."""
    harness.script(answer)
    assert (await harness.read_transmit_state()).value is not False


# ---------------------------------------------------------------------------
# 2. Queue liveness and self-write provenance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("harness", QUEUE_PATH_BACKENDS, indirect=True)
async def test_the_queue_drain_reaches_the_backend_write_method(
    harness: TxObservationHarness,
) -> None:
    """The D1 ingress is real, and this file can drive it.

    The queued command is the probe, so this row measures the drain itself.
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
    harness: TxObservationHarness,
) -> None:
    """§3.10 item 3, row 7 / INV-8: a write outcome is never evidence of RF.

    A bare ``set_ptt`` — the harness's direct call on the radio object, not
    the queue-drained write ``test_the_queue_drain_reaches_the_backend_write_method``
    already proves reaches the backend — only writes to the wire and updates
    the legacy ``radio_state`` cache (``yaesu_cat/radio.py:993-1000`` -- moved
    +6 by MOR-1914's imports -- and the rigctld-client ``set_ptt``
    equivalent); neither touches
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
    wire the bare write just used, no sleep and no mock. A
    ``yaesu_poll_response`` / ``hamlib_response`` observation is expected.
    What must never appear is a ``global.tx_state.ptt`` observation under any
    other source: that would be a write outcome wearing a readback's clothes.

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
    harness: TxObservationHarness,
) -> None:
    """The same row on the CI-V columns, read off the canonical store."""
    await harness.key()
    await harness.unkey()
    await asyncio.sleep(0)

    store = harness.radio._state_store
    assert "global.tx_state.ptt" not in {
        str(field.path) for field in store.snapshot().fields
    }, f"{harness.name}: our own set_ptt became a store observation"


@every_backend()
async def test_a_backend_exposes_the_row_five_read_primitive(
    harness: TxObservationHarness,
) -> None:
    """INV-13's home: one primitive, per backend, returning typed evidence.

    Row 5: ``read_transmit_state()`` lands on the new capability protocol
    ``TransmitStateReadable`` (5a Icom, 5b Yaesu + rigctld-client) -- the
    harness now points ``read_transmit_state`` at this same method directly
    rather than assembling the equivalent from shipped parts.
    """
    reading = await harness.radio.read_transmit_state()
    assert isinstance(reading, TxStateReading)


@pytest.mark.parametrize("harness", ["rigctld-client"], indirect=True)
async def test_the_rigctld_client_primitive_marks_its_readback_unverified(
    harness: TxObservationHarness,
) -> None:
    harness.script("rx")
    reading = await harness.radio.read_transmit_state()
    assert reading.verified_readback is False


async def test_the_rigctld_client_primitive_reports_a_real_timeout_as_timeout() -> None:
    """A genuine wire-level read timeout must report ``failure="timeout"``,
    not ``"read-error"``.

    Deliberately not driven through the shared ``rigctld-client`` harness:
    that harness's ``"silence"`` answer is realised as the upstream
    dropping the connection (``server.behavior.disconnect_commands``, by
    design -- see its own comment in ``tx_observation_fakes.py`` -- to avoid a
    late line answering the *next* read on the shared socket), which the
    transport reports as ``RadioConnectionError``, not a timeout. That
    exercises a different exception path than the one this pin is about.

    This test scripts an actual delay past the client's read deadline
    (``FakeRigctldBehavior.command_delays``) instead, which is what drives
    ``asyncio.wait_for(reader.readline(), timeout=self.timeout)`` in
    ``transport.py:232`` to genuinely expire and raise
    ``rigplane.core.exceptions.TimeoutError`` (aliased ``RadioTimeoutError``)
    -- which does **not** subclass the builtin/``asyncio.TimeoutError`` (its
    MRO is ``TimeoutError -> RigplaneError -> Exception``). A catch narrowed
    to ``except asyncio.TimeoutError`` alone silently reroutes this into the
    generic ``except Exception`` branch, which
    ``test_no_failing_answer_ever_resolves_to_receiving`` could not see: it
    only asserts ``value is not False``, true either way.

    # MUTATION: in `src/rigplane/backends/rigctld_client/radio.py`, in
    # `read_transmit_state`, narrow `except (asyncio.TimeoutError,
    # RadioTimeoutError):` back to `except asyncio.TimeoutError:` -> this
    # row goes red: the real `RadioTimeoutError` falls through to the
    # generic `except Exception:` branch and reports `failure="read-error"`
    # instead of `failure="timeout"`.
    """
    server = FakeRigctldServer(behavior=FakeRigctldBehavior(command_delays={"t": 0.2}))
    await server.start()
    radio = RigctldClientRadio(host=server.host, port=server.port, timeout=0.05)
    await radio.connect()
    try:
        reading = await radio.read_transmit_state()
    finally:
        await radio.disconnect()
        await server.stop()

    assert reading.failure == "timeout", (
        f"a real read timeout reported failure={reading.failure!r}, not "
        "'timeout' -- the RadioTimeoutError catch narrowed back to only "
        "asyncio.TimeoutError"
    )


@pytest.mark.parametrize("harness", ["rigctld-client"], indirect=True)
async def test_the_vocabulary_table_does_not_claim_a_delay_it_does_not_deliver(
    harness: TxObservationHarness,
) -> None:
    """MOR-1953: the vocabulary table's own words must match what its
    ``script`` function does, not what would be convenient to believe.

    A prior version of the module docstring's table (``tx_observation_fakes.py``
    top) described the rigctld-client ``silence`` answer as "delayed past
    the deadline". ``_build_rigctld_client``'s ``script`` does something
    else: it makes the upstream *drop the connection*
    (``server.behavior.disconnect_commands``), which the transport reports
    as ``RadioConnectionError``, not a timeout -- the implementation's own
    comment already explains why (a genuinely delayed line would arrive in
    time to answer the *next* read on the shared socket, not this one), and
    that implementation is correct and unchanged by this row.

    The table was the defect. During MOR-1914 an agent trusted the old
    wording, wrote a pin scripting ``silence`` and asserting
    ``failure == "timeout"``, and it failed *with the fix applied* -- red
    for a reason unrelated to any defect, and it would have gone green for
    a reason unrelated to any fix. This test pins the table's text against
    the real behaviour it describes, so the two cannot drift apart again
    silently.

    The genuine wire-level timeout path is a different scenario entirely,
    covered separately by
    ``test_the_rigctld_client_primitive_reports_a_real_timeout_as_timeout``
    above -- deliberately not driven through this harness; see that test's
    own docstring for why.

    # MUTATION: in `tests/tx_observation_fakes.py`, revert the rigctld-client
    # `silence` cell in the module docstring's table back to "delayed past
    # the deadline" -> this row goes red on the docstring assertion below.
    """
    import tx_observation_fakes

    doc = tx_observation_fakes.__doc__ or ""
    assert "delayed past" not in doc and "the deadline" not in doc, (
        "the vocabulary table still claims the rigctld-client 'silence' "
        "answer is a delay past the deadline -- it is not; "
        "_build_rigctld_client's script() drops the connection instead "
        "(see that function's own comment)"
    )

    harness.script("silence")
    reading = await harness.radio.read_transmit_state()

    assert reading.failure == "read-error", (
        f"scripted 'silence' on rigctld-client reported failure="
        f"{reading.failure!r}, not 'read-error' -- if this is now "
        "'timeout' the harness's disconnect-based realisation of "
        "'silence' has changed and the table needs to change with it"
    )


async def test_the_yaesu_primitive_reports_a_malformed_reply_as_read_error() -> None:
    """A malformed-but-delivered CAT reply must come back as a
    :class:`TxStateReading` with ``failure="read-error"``, never escape
    ``read_transmit_state`` as a raised ``CatParseError``.

    Deliberately not driven through the shared ``yaesu-ftx1`` harness:
    ``ScriptedCatTransport`` only ever returns the vocabulary's six
    answers, each of which either parses cleanly against the ``ftx1``
    profile's ``TX{state};`` template or raises one of the transport's own
    typed exceptions. A noisy serial line can also deliver a line that gets
    past ``query()``'s ``?``-prefix rejection but fails the response
    *template* once ``_query`` (``radio.py:764-781``) hands it to
    ``CatCommandParser.parse``. That raises ``CatParseError``
    (``parser.py:157``) -- a ``ValueError`` subclass, not one of the
    ``transport.py`` ``Cat*Error`` family -- which was not in
    ``read_transmit_state``'s catch list.

    Three shapes reproduce it against the real ``TX{state};`` template: a
    ``TX`` answer with no state digit, an answer that isn't a ``TX``
    answer at all, and an empty line.

    # MUTATION: in `src/rigplane/backends/yaesu_cat/radio.py`, in
    # `read_transmit_state`, narrow `except (CatCommandRejected,
    # CatParseError):` back to `except CatCommandRejected:` -> this row
    # goes red: `CatParseError` escapes the primitive instead of coming
    # back as `TxStateReading(value=None, failure="read-error")`.
    """
    radio = YaesuCatRadio("/dev/null", profile="ftx1")
    radio._transport._connected = True

    for malformed_reply in ("TX", "FA014074000", ""):

        async def query(
            cmd: str, *args: object, _reply: str = malformed_reply, **kwargs: object
        ) -> str:
            return _reply

        radio._transport.query = query  # type: ignore[method-assign]

        reading = await radio.read_transmit_state()

        assert reading == TxStateReading(value=None, failure="read-error"), (
            f"a malformed-but-delivered reply {malformed_reply!r} produced "
            f"{reading!r}, not a clean "
            "TxStateReading(value=None, failure='read-error')"
        )


@pytest.mark.parametrize("harness", ["yaesu-ftx1"], indirect=True)
async def test_yaesu_attribution_reaches_the_evidence(
    harness: TxObservationHarness,
) -> None:
    """§3.7: attribution is a per-vendor capability, carried not discarded.

    MOR-1941 (row 6): ``tx_state_map`` turns the three-valued ``TX;``
    answer into an attribution, so ``tx_other`` (the front-panel key) is
    now distinguishable from ``tx_cat`` in the evidence.
    """
    harness.script("tx_other")
    assert (await harness.read_transmit_state()).attributed == "tx_other"
    harness.script("tx_cat")
    assert (await harness.read_transmit_state()).attributed == "tx_cat"


@pytest.mark.parametrize("harness", ["yaesu-ftx1"], indirect=True)
async def test_a_yaesu_self_write_leaves_no_transmit_truth_claim_anywhere(
    harness: TxObservationHarness,
) -> None:
    """The self-write launder, at its last surviving Yaesu address.

    MOR-1941 (row 6): ``set_ptt`` no longer self-mutates the legacy
    mirror (``backends/yaesu_cat/radio.py:1000`` -- moved +6 by MOR-1914's
    imports) -- our own command is no longer a claim about RF anywhere in
    the tree.
    """
    before = harness.radio._state.ptt
    await harness.key()
    assert harness.radio._state.ptt == before
