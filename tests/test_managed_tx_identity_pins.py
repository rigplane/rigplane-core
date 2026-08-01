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
"""

from __future__ import annotations

import pytest

from rigplane import IC_7610_ADDR
from rigplane.exceptions import ConnectionError as RigConnectionError
from rigplane.radio import IcomRadio
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
