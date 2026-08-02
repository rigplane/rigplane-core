"""MOR-1221 (MOR-1165 audit remediation R1): pin the managed-TX identity
invariants Auditor A's mutation battery found unpinned at base ``65271180``
(see ``mor1165-audit-plan/raw/auditor-A-report.md`` §A3/§A4).

* **A3-1 epoch (priority).** ``_managed_tx_binding_is_live``
  (``runtime/radio.py:1207-1211``) drops "live" the instant any of three
  captured facts moves. Deleting the epoch comparison alone (M5d) survived
  the suite: nothing drove the ``soft_disconnect`` gap where the CI-V epoch
  advances (``radio.py:1720``) while the transport object stays put.
* **A3-1 generation (defence-in-depth).** Deleting the provider-generation
  comparison (M5) also survived; every reachable ``CoreRadio`` path moves
  epoch/transport/generation together via ``retire_managed_tx_port``, so
  the auditor judged it a likely equivalent mutant there. Pinned anyway via
  the override seam ``radio.py:1348-1351`` names ("a backend that overrides
  that lifecycle hook cannot silently lose it"): ``_NoOpRetireBackend``
  is that backend, synthetic by design -- no shipped backend behaves this
  way.
* **A4-1 two token fields.** ``_managed_tx_port_is_current``
  (``_civ_rx.py:694-703``) guards every managed PTT write. Deleting either
  the exact-transport check (:702, M7) or the binding-epoch check (:699,
  M7b) survived: the existing ``tests/test_radio.py::TestPtt`` safety
  parametrization only invalidates through the *observer*-binding path,
  never these two fields in isolation.

A4-2 (the ``_send_civ_frame_now`` min-interval pacing regression) is not in
this file -- it pushed this slice over its LOC cap and ships separately
under its own non-audit ticket; its kill (M8a) was verified before
extraction (see ``mor1221-a42-pacing-test.patch`` in the build scratchpad).

No ``MagicMock`` stands in for a protocol-shaped object: ``_ManagedRadio``/
``_Provider`` (from ``test_web_recovery_durable_off``) and the real
``IcomRadio``/``MockTransport`` pair (from ``test_radio``) drive every case.

---

MOR-1226 (MOR-1165 audit remediation R2): pin the rest of the same guard,
per Auditor A's Round-2 report §A4/§A8 (and §A3 for the binding triple):

* **A4-1, the four remaining ``_managed_tx_port_is_current`` fields.** R1
  pinned :699 (binding epoch) and :702 (exact transport, as a *deletion*).
  The other four comparisons at ``_civ_rx.py:696-701`` -- registry
  identity, observer provider-generation, observer CI-V generation, host
  CI-V epoch -- were each individually deletable with the full 9020-test
  suite green. Pinned below by direct manipulation of the one field under
  test, exactly the style ``test_binding_epoch_divergence_makes_the_managed_port_stale``
  already uses, so each mutation is isolated from the other six.
* **A4-1, the post-response READ revalidation (:670).** Whole-block
  deletion survived the full suite too -- because ``_emit_authoritative_ptt``
  happens to re-derive an equivalent check through its own ``expected``
  argument for this one call site, so the *return value* and the *observer*
  side effect are unaffected either way. Pinned as an architectural
  property instead of a value: a spy stands in for
  ``_emit_authoritative_ptt`` and proves :670 -- not the coincidence
  downstream -- is what stops it from being reached once the binding moved
  underneath an in-flight read.
* **A3-1 / A4-1, the two ``is``-vs-``==`` weakenings.** Confirmed still
  unpinned at both the binding triple (``radio.py:1208``, Low) and the
  managed-port guard's own transport check (``_civ_rx.py:702``,
  Medium-High) because no shipped transport type
  (``IcomTransport``/serial's ``SerialCivTransport``/``MockTransport``)
  overrides ``__eq__`` -- ``is`` and ``==`` agree on every transport object
  the suite ever constructs, so the weakening is invisible to every
  existing test by construction, not because the guard doesn't need it.
  ``_EqTransport`` below is the "obvious route" attempted before any
  documented-skip: an equal-but-distinct transport double, on the same
  synthetic footing as R1's ``_NoOpRetireBackend`` -- no shipped transport
  behaves this way, but the guard must not depend on that being true.

No ``MagicMock`` stands in for a protocol-shaped object here either: the
same real ``IcomRadio``/``MockTransport`` pair, and ``_ManagedRadio``/
``_Provider``, drive every new case; ``_EqTransport`` is a minimal value
double, not a mock.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR
from rigplane.exceptions import ConnectionError as RigConnectionError
from rigplane.radio import IcomRadio
from rigplane.runtime._civ_rx import (
    RawCivTransactionResult,
    _AuthoritativePttRead,
    _ManagedTxPortToken,
)
from rigplane.types import CivFrame
from test_radio import MockTransport
from test_web_recovery_durable_off import _armed, _ManagedRadio, _Provider

# ---------------------------------------------------------------------------
# Shared fixtures -- same shape as tests/test_civ_transaction_ownership.py,
# reproduced locally per the ticket's "do not modify existing test files"
# scope rather than imported as pytest fixture objects (no precedent for
# cross-module fixture import in this suite; every sibling file that reuses
# ``test_radio``'s wiring redeclares this exact pair).
# ---------------------------------------------------------------------------


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(transport: MockTransport):
    r = IcomRadio("192.168.1.100", timeout=0.05)
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    r._radio_addr = IC_7610_ADDR
    r._civ_ack_sink_grace = 0.001
    yield r
    r._connected = False
    r._civ_transport = None
    r._ctrl_transport = None


# ===========================================================================
# A3-1 -- epoch element of _managed_tx_binding_is_live (the priority)
# ===========================================================================


async def test_epoch_only_advance_kills_the_binding_and_forces_one_rearm() -> None:
    """The exact ABA gap ``soft_disconnect`` opens: epoch moves, transport
    does not. Without the epoch comparison, ``_managed_tx_binding_is_live``
    would still see a matching transport and a matching provider generation
    and answer "live" -- so a re-arm landing here would wrongly no-op,
    leaving the durable OFF armed against a port CI-V recovery already moved
    past. Production must answer "not live", and the one re-arm that follows
    must reach the runtime exactly once.
    """
    radio, _runtime, rebinds = await _armed()
    epoch_before = radio._civ_epoch
    transport_before = radio._civ_transport

    # ``soft_disconnect``'s own move: advance the CI-V epoch, touch nothing
    # else. See radio.py:1720 / CivRuntime.advance_generation.
    radio._advance_civ_generation("test: soft_disconnect epoch-only advance")

    assert radio._civ_epoch != epoch_before
    assert radio._civ_transport is transport_before  # untouched -- the gap

    assert radio._managed_tx_binding_is_live() is False

    await radio.rearm_managed_tx()

    assert rebinds == [True]  # exactly one replace_provider reached the runtime


# ===========================================================================
# A3-1 -- generation element, pinned only via the override seam
# radio.py:1348-1351 anticipates ("a backend that overrides that lifecycle
# hook cannot silently lose it"). Every reachable path inside CoreRadio moves
# epoch/transport/generation together (retire_managed_tx_port), so this
# element cannot be isolated without a backend that breaks that coupling on
# purpose -- which is what makes it defence-in-depth rather than reachable
# production behaviour.
# ===========================================================================


class _NoOpRetireBackend(_ManagedRadio):
    """Overrides the lifecycle hook without touching epoch or transport.

    Real backends built on ``CoreRadio`` never do this -- ``_ManagedRadio``
    (the base class here) mirrors production's conditional epoch/transport
    clearing exactly. This override is deliberately the seam's worst case:
    a hook that drops the coupling entirely, so the supervisor's provider
    generation can move while the recorded epoch and transport stay put.
    """

    async def _retire_managed_tx_port(self, generation: int) -> None:
        await self.provider._retire_managed_tx_port(generation)
        # Deliberately no epoch advance, no transport clear.


async def test_generation_only_divergence_kills_the_binding() -> None:
    radio = _NoOpRetireBackend(_Provider([]))
    await radio.rearm_managed_tx()
    runtime = radio._managed_tx_runtime
    assert runtime is not None
    bound_generation, epoch, transport = radio._managed_tx_bound_port
    assert radio._managed_tx_binding_is_live() is True

    # Move the supervisor's provider generation directly -- bypassing
    # ``rearm_managed_tx``'s own no-op guard, which would refuse a second
    # call while the binding still reads live -- so only the generation
    # moves underneath the recorded binding.
    await runtime.replace_provider(ready=True)

    assert runtime.tx_snapshot.provider_generation != bound_generation
    assert radio._civ_epoch == epoch
    assert radio._civ_transport is transport

    assert radio._managed_tx_binding_is_live() is False


# ===========================================================================
# A4-1 -- two token fields of _managed_tx_port_is_current
# ===========================================================================


async def test_transport_divergence_makes_the_managed_port_stale(
    radio: IcomRadio,
) -> None:
    """A token whose transport is not the live transport must read stale,
    and the write it guards must refuse. This is the reachable case the
    report calls out by name: ``soft_reconnect`` replaces the transport
    before it advances the epoch, and a managed write landing in that window
    must not reach the dead socket.
    """
    observations: list[object] = []
    assert radio._capture_managed_tx_port(11, observations.append)
    civ_runtime = radio._civ_runtime
    token = civ_runtime._managed_tx_ports[11]
    assert civ_runtime._managed_tx_port_is_current(token) is True

    radio._civ_transport = MockTransport()  # fresh transport, epoch untouched

    assert civ_runtime._managed_tx_port_is_current(token) is False
    with pytest.raises(RigConnectionError, match="managed TX physical port is stale"):
        await radio._write_managed_ptt(11, True)


async def test_binding_epoch_divergence_makes_the_managed_port_stale(
    radio: IcomRadio,
) -> None:
    """A token whose binding epoch is stale (the observer was rebound to a
    later generation) must read stale on its own, independent of the
    transport check next to it.
    """
    observations: list[object] = []
    assert radio._capture_managed_tx_port(11, observations.append)
    civ_runtime = radio._civ_runtime
    token = civ_runtime._managed_tx_ports[11]
    assert civ_runtime._managed_tx_port_is_current(token) is True

    civ_runtime._ptt_observer_binding_epoch += 1  # bump the epoch alone

    assert civ_runtime._managed_tx_port_is_current(token) is False
    with pytest.raises(RigConnectionError, match="managed TX physical port is stale"):
        await radio._write_managed_ptt(11, True)


# ===========================================================================
# MOR-1226 (R2) -- the four remaining _managed_tx_port_is_current fields
# (:696, :698, :700, :701), each isolated by direct manipulation of the one
# field under test -- the same style as the binding-epoch pin above.
# ===========================================================================


async def test_registry_identity_divergence_makes_the_managed_port_stale(
    radio: IcomRadio,
) -> None:
    """A token whose registry slot (``_civ_rx.py:696``) now holds a
    *different* token object -- same generation, same field values, but not
    the one this caller is holding -- must read stale on its own. This is
    the only one of the seven comparisons that distinguishes a freshly
    recaptured port (``reset_managed_tx_generations`` clears the registry
    and a later capture refills the same-numbered slot) from a stale
    in-flight write still holding the old token: the replacement token is
    field-identical by construction, so nothing else in the guard would
    catch it.
    """
    observations: list[object] = []
    assert radio._capture_managed_tx_port(11, observations.append)
    civ_runtime = radio._civ_runtime
    token = civ_runtime._managed_tx_ports[11]
    assert civ_runtime._managed_tx_port_is_current(token) is True

    civ_runtime._managed_tx_ports[11] = _ManagedTxPortToken(
        token.provider_generation,
        token.binding_epoch,
        token.civ_source_generation,
        token.transport,
    )

    assert civ_runtime._managed_tx_port_is_current(token) is False
    # ``write_managed_ptt`` re-fetches its token by generation on every call,
    # so it can never hold a registry-stale one -- the caller that matters
    # here is the dispatch-time re-validation, which *does* hold a token
    # across an await and is exactly what :696 exists to catch.
    with pytest.raises(
        RigConnectionError, match="managed TX physical port changed before dispatch"
    ):
        await civ_runtime._send_civ_frame_now(b"\x00", owner=token)


async def test_observer_provider_generation_divergence_makes_the_managed_port_stale(
    radio: IcomRadio,
) -> None:
    """``_civ_rx.py:698`` in isolation: the observer's own recorded provider
    generation moves away from the token's, independent of the registry
    slot, the binding epoch and the transport -- e.g. a second
    ``bind_ptt_observer`` for a different generation lands between capture
    and write.
    """
    observations: list[object] = []
    assert radio._capture_managed_tx_port(11, observations.append)
    civ_runtime = radio._civ_runtime
    token = civ_runtime._managed_tx_ports[11]
    assert civ_runtime._managed_tx_port_is_current(token) is True

    civ_runtime._ptt_observer_provider_generation = 999

    assert civ_runtime._managed_tx_port_is_current(token) is False
    with pytest.raises(RigConnectionError, match="managed TX physical port is stale"):
        await radio._write_managed_ptt(11, True)


async def test_observer_civ_generation_divergence_makes_the_managed_port_stale(
    radio: IcomRadio,
) -> None:
    """``_civ_rx.py:700`` in isolation: the observer's own recorded CI-V
    generation moves away from the token's, independent of the *host's*
    live ``_civ_epoch`` (:701, pinned separately below) and everything
    else.
    """
    observations: list[object] = []
    assert radio._capture_managed_tx_port(11, observations.append)
    civ_runtime = radio._civ_runtime
    token = civ_runtime._managed_tx_ports[11]
    assert civ_runtime._managed_tx_port_is_current(token) is True

    civ_runtime._ptt_observer_civ_generation += 1

    assert civ_runtime._managed_tx_port_is_current(token) is False
    with pytest.raises(RigConnectionError, match="managed TX physical port is stale"):
        await radio._write_managed_ptt(11, True)


async def test_host_civ_epoch_divergence_makes_the_managed_port_stale(
    radio: IcomRadio,
) -> None:
    """``_civ_rx.py:701`` in isolation: the *host's* live CI-V epoch moves
    away from the token's recorded generation without going through
    ``advance_generation`` (which would also poison the token via :697,
    already pinned) -- proving :701 catches a bare epoch bump entirely on
    its own.
    """
    observations: list[object] = []
    assert radio._capture_managed_tx_port(11, observations.append)
    civ_runtime = radio._civ_runtime
    token = civ_runtime._managed_tx_ports[11]
    assert civ_runtime._managed_tx_port_is_current(token) is True

    radio._civ_epoch += 1

    assert civ_runtime._managed_tx_port_is_current(token) is False
    with pytest.raises(RigConnectionError, match="managed TX physical port is stale"):
        await radio._write_managed_ptt(11, True)


# ===========================================================================
# MOR-1226 (R2) -- the post-response READ revalidation (:670): whole-block
# deletion survived the full suite because _emit_authoritative_ptt happens
# to re-derive an equivalent predicate through its own ``expected`` kwarg
# for this call site. Pin the guard itself with a spy that skips that
# downstream re-check, proving :670 -- not the coincidence -- is what stops
# a stale response from reaching it.
# ===========================================================================


async def test_the_post_response_revalidation_stops_a_response_the_binding_moved_past(
    radio: IcomRadio,
) -> None:
    """A genuine CI-V response arrives (``result.status == "response"``,
    a real frame), but the observer was rebound to a different generation
    *while the round trip was in flight* -- the exact race a reconnect or a
    second ``bind_ptt_observer`` opens between a read's dispatch and its
    reply. ``_emit_authoritative_ptt`` must never even be reached in that
    case; a spy in its place proves the direct check at ``_civ_rx.py:670``
    is what stops it, independent of whatever the callee would itself have
    decided.
    """
    civ_runtime = radio._civ_runtime
    observations: list[object] = []
    observer = observations.append
    assert radio._capture_managed_tx_port(11, observer)

    emit_calls: list[object] = []

    def spying_emit(*args: object, **kwargs: object) -> bool:
        # Deliberately does not re-derive currency the way the real
        # _emit_authoritative_ptt (:1499) happens to for this call site
        # today -- that is the coincidence :670 must not depend on.
        emit_calls.append(kwargs.get("expected"))
        return True

    async def race_then_respond(
        *args: object, **kwargs: object
    ) -> RawCivTransactionResult:
        # The binding moves while the round trip is in flight.
        civ_runtime.bind_ptt_observer(
            provider_generation=12, observer=lambda _obs: None
        )
        return RawCivTransactionResult(
            status="response",
            frame=CivFrame(
                to_addr=CONTROLLER_ADDR,
                from_addr=IC_7610_ADDR,
                command=0x1C,
                sub=0x00,
                data=bytes([0x01]),
            ),
        )

    with (
        patch.object(civ_runtime, "_emit_authoritative_ptt", spying_emit),
        patch.object(civ_runtime, "execute_civ_transaction", race_then_respond),
    ):
        result = await civ_runtime.request_authoritative_ptt_read(
            b"\x00", provider_generation=11, observer=observer
        )

    assert result is False
    assert emit_calls == []  # :670 must stop this before _emit_authoritative_ptt runs
    assert observations == []


# ===========================================================================
# MOR-1226 (R2) -- the two ``is``-vs-``==`` weakenings, both confirmed still
# unpinned because no shipped transport overrides __eq__. _EqTransport is
# the equal-but-distinct double: same synthetic footing as R1's
# _NoOpRetireBackend above.
# ===========================================================================


class _EqTransport:
    """A transport double whose equality is defined by a shared marker, not
    identity. No shipped transport (``IcomTransport``, serial's
    ``SerialCivTransport``, ``MockTransport``) overrides ``__eq__`` -- every
    one of them compares by identity by default -- so nothing in the
    existing suite can tell an ``is`` check apart from an ``==`` check on a
    real transport object. This double breaks that coincidence: two
    distinct instances that compare equal must still be told apart, because
    ``==`` on a future or third-party transport type could otherwise let a
    write reach a different physical connection than the one authorised.
    """

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _EqTransport) and other.marker == self.marker

    def __hash__(self) -> int:
        return hash(self.marker)


async def test_transport_equality_is_not_enough_the_managed_port_check_is_identity(
    radio: IcomRadio,
) -> None:
    """``_civ_rx.py:702`` weakened from ``is`` to ``==`` survives the full
    9020-test suite (Auditor A, A4-M8) purely because no shipped transport
    type defines value equality. Prove the check is genuinely identity by
    handing it two distinct, but equal, transport objects.
    """
    transport_a = _EqTransport("port-x")
    radio._civ_transport = transport_a
    observations: list[object] = []
    assert radio._capture_managed_tx_port(11, observations.append)
    civ_runtime = radio._civ_runtime
    token = civ_runtime._managed_tx_ports[11]
    assert civ_runtime._managed_tx_port_is_current(token) is True

    transport_b = _EqTransport("port-x")
    assert transport_b == transport_a
    assert transport_b is not transport_a
    radio._civ_transport = transport_b

    assert civ_runtime._managed_tx_port_is_current(token) is False
    with pytest.raises(RigConnectionError, match="managed TX physical port is stale"):
        await radio._write_managed_ptt(11, True)


async def test_transport_equality_is_not_enough_for_binding_liveness() -> None:
    """``_managed_tx_binding_is_live``'s transport comparison
    (``runtime/radio.py:1208``) weakened from ``is`` to ``==`` also
    survives the full 828-test identity-pin superset (Auditor A, A3-M3,
    recorded at Low as "protected by review only"). Same double, same
    reasoning as the ``_civ_rx.py:702`` pin above: a same-generation,
    same-epoch rebind that nonetheless lands on a *different* transport
    object must not read as still live merely because the two compare
    equal.
    """
    radio = _ManagedRadio(_Provider([]))
    radio._civ_transport = _EqTransport("port-x")
    await radio.rearm_managed_tx()
    runtime = radio._managed_tx_runtime
    assert runtime is not None
    _generation, _epoch, bound_transport = radio._managed_tx_bound_port
    assert bound_transport is radio._civ_transport

    twin = _EqTransport("port-x")
    assert twin == bound_transport
    assert twin is not bound_transport
    radio._civ_transport = twin

    assert radio._managed_tx_binding_is_live() is False


# ===========================================================================
# MOR-1226 addendum (R6 verifier finding) -- _emit_authoritative_ptt's own
# ``expected`` re-check (:1499) is a second, independent belt to :670's
# braces, not implied by it. The verifier's combination probe found that
# with :670 intact, disabling *only* :1499's ``expected`` term still
# survives the full 8764-test suite: the sole call site that passes
# ``expected`` (:671) is already gated by :670 immediately before it,
# synchronously, so :1499's own check is never independently exercised
# through either of its two call sites (the other, :1479, never passes
# ``expected`` at all). Pin :1499 directly, bypassing both callers.
# ===========================================================================


async def test_emit_authoritative_ptt_refuses_a_stale_expected_read_on_its_own(
    radio: IcomRadio,
) -> None:
    """Call ``_emit_authoritative_ptt`` itself with an ``expected`` whose
    currency has already lapsed -- skipping :670 entirely -- and assert
    :1499 refuses on its own: no observation reaches the real registered
    observer, and the sequence counter does not advance.
    """
    civ_runtime = radio._civ_runtime
    observations: list[object] = []
    observer = observations.append
    assert radio._capture_managed_tx_port(11, observer)
    token = civ_runtime._managed_tx_ports[11]
    stale_read = _AuthoritativePttRead(observer, token, True)
    seq_before = civ_runtime._ptt_observation_seq

    civ_runtime._ptt_observer_binding_epoch += 1  # the binding moved
    assert civ_runtime._ptt_read_is_current(stale_read) is False

    frame = CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=0x1C,
        sub=0x00,
        data=bytes([0x01]),
    )
    result = civ_runtime._emit_authoritative_ptt(
        frame, source_generation=token.civ_source_generation, expected=stale_read
    )

    assert result is False
    assert observations == []
    assert civ_runtime._ptt_observation_seq == seq_before
