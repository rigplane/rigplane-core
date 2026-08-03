"""MOR-1015: the additive ``txSafety`` evidence, and the system-level proof.

Two halves of one claim. The first is that the Web runtime document tells the
truth about the managed TX supervisor: whose lease it is, which phase it is in,
whether a watchdog can actually fire, what durable OFF is owed, how many
attempts it has cost, what the last error was, and when the obligation has
stopped being able to complete at all. The second is that there is exactly one
supervisor behind every supported ingress, and no production path writes PTT
around it.

Every case here drives a **real** ``TxSafetySupervisor`` through a real
``ManagedRadioRuntime`` and the real effect service, over the hand-rolled
provider the recovery suite already uses. A scripted double would answer
whatever it was told, and what is under test is precisely the difference
between what the supervisor was *told* and what it actually *knows* — an ACK
is not RF, and a configured watchdog is not an armed one.

The three honesty properties, and the mutations they kill:

* **ACK is not RF (MOR-1177 class).** ``rfConfirmed`` follows
  ``TxPhase.KEYED`` — the phase reached only when a causally-ordered PTT
  observation confirms the key — never the lease, and never the ``ACCEPTED``
  outcome. ``test_an_acked_key_with_no_observation_is_not_confirmed_rf`` keys a
  rig whose write is acknowledged and whose read succeeds without returning an
  observation, so lease-held and outcome-ACCEPTED are both true while RF is
  unknown: any implementation deriving ``rfConfirmed`` from either one reports
  ``True`` and fails.
* **A watchdog nobody armed is not coverage (MOR-1204).** ``watchdog.armed``
  follows the live deadline, never ``TxSafetySnapshot.watchdog_enabled``, which
  stays ``True`` across a release-only lease that no deadline backs.
  ``test_a_driven_release_only_lease_advertises_no_armed_watchdog`` drives
  exactly that lease: reporting the flag as "armed" is the MOR-1204 lie and
  fails there.
* **An obligation nothing can finish is named (MOR-1014).** A twice-cancelled
  rigctld handback leaves the lease held at ``RELEASE_REQUIRED`` with an
  unsettled ``WRITE_OFF`` and no watchdog behind it; it does not time itself
  out, and ``rigctld/server.py`` says in as many words that these diagnostics
  are what surface it. ``test_a_cancelled_handback_is_an_uncertain_shutdown``
  drives the cancellation and pins the terminal — with the same state read one
  moment earlier asserting ``False``, so "always uncertain during a release"
  does not pass for an implementation.
"""

from __future__ import annotations

import ast
import asyncio
import json
from inspect import getattr_static
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.tx_safety import (
    ProviderAttemptKind,
    ProviderPttObservation,
    RadioTx,
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySnapshot,
    TxSafetySupervisor,
    TxSource,
)
from rigplane.runtime import radio as radio_module
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_ingress import bind_managed_tx
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.tx_safety_view import build_tx_safety_payload
from test_web_recovery_durable_off import _ManagedRadio, _Observer, _Provider

_WEB = TxOwner(TxSource.WEBSOCKET, "ws-1")
_RIGCTLD = TxOwner(TxSource.RIGCTLD, "rigctld-client-1")
_SDK = TxOwner(TxSource.SDK, "9f8e7d6c5b4a39281706f5e4d3c2b1a0")

_LIVE: list[ManagedRadioRuntime] = []


@pytest.fixture(autouse=True)
def _retire_tickers():
    """Stop any watchdog ticker a test left running with a lease still held."""
    yield
    while _LIVE:
        task = _LIVE.pop()._tick_task
        if task is not None:
            task.cancel()


async def _armed(provider: _Provider) -> tuple[_ManagedRadio, ManagedRadioRuntime]:
    """A real ``CoreRadio`` armed through the public path over ``provider``."""
    radio = _ManagedRadio(provider)
    await radio.rearm_managed_tx()
    runtime = radio._managed_tx_runtime
    assert runtime is not None
    assert runtime.tx_snapshot.provider_ready
    _LIVE.append(runtime)
    return radio, runtime


class _MuteableProvider(_Provider):
    """A rig that answers reads until told to stop answering them.

    Muted, the read still *succeeds* — the round trip completes and the
    supervisor settles the attempt — it simply carries no observation back.
    That is the shape of a rig whose PTT readback is unavailable while its
    command channel is fine, and the state in which an ACK is all anyone has.
    """

    def __init__(self, log: list[str]) -> None:
        super().__init__(log)
        self.mute = False

    async def _request_authoritative_ptt_read(
        self, *, provider_generation: int, observer: _Observer
    ) -> None:
        if self.mute:
            self.log.append("read_ptt")
            return
        await super()._request_authoritative_ptt_read(
            provider_generation=provider_generation, observer=observer
        )


class _GatedUnkey(_Provider):
    """A rig whose unkey write parks on a gate until the test releases it."""

    def __init__(self, log: list[str]) -> None:
        super().__init__(log)
        self.gate, self.reached = asyncio.Event(), asyncio.Event()
        self.gate_unkey = False

    async def _write_managed_ptt(self, generation: int, on: bool) -> None:
        if not on and self.gate_unkey:
            self.reached.set()
            await self.gate.wait()
        await super()._write_managed_ptt(generation, on)


async def _settle(predicate, *, what: str) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"never reached: {what}")


# ===========================================================================
# The evidence a managed supervisor actually publishes
# ===========================================================================


async def test_the_payload_carries_the_supervisors_own_lease_owner_and_watchdog() -> (
    None
):
    """Every required field, read from the supervisor rather than re-derived.

    The poller's PTT mirror knows a boolean; this block has to carry the owner
    that holds the lease, the lease id itself, the phase, the live watchdog
    deadline and the (empty) release obligation. A payload assembled from the
    legacy mirror, or from constants, cannot produce any of them.
    """
    radio, runtime = await _armed(_Provider([]))
    assert (await runtime.request_on(_WEB)).outcome is TxOutcome.ACCEPTED
    snapshot = runtime.tx_snapshot

    payload = build_tx_safety_payload(radio)

    assert payload["status"] == "managed"
    assert payload["owner"] == {"source": "websocket", "sessionId": "ws-1"}
    assert payload["lease"] == {"held": True, "id": snapshot.lease_id}
    assert snapshot.lease_id is not None
    assert payload["phase"] == "keyed"
    assert payload["keyRequested"] is True
    assert payload["rfConfirmed"] is True
    assert payload["radioTx"] == "on"
    assert payload["externalConflict"] is False
    assert payload["provider"] == {
        "generation": snapshot.provider_generation,
        "ready": True,
    }
    assert payload["watchdog"]["armed"] is True
    assert payload["watchdog"]["deadlineMonotonic"] == pytest.approx(
        snapshot.watchdog_deadline_monotonic
    )
    assert 0 < payload["watchdog"]["secondsRemaining"] <= 180.0
    assert payload["durableOff"] == {
        "owed": False,
        "requestedReason": None,
        "terminalReason": None,
        "attempts": 0,
        "lastError": None,
    }
    assert payload["uncertainShutdown"] is False
    assert payload["uncertainReason"] is None


async def test_an_acked_key_with_no_observation_is_not_confirmed_rf() -> None:
    """ACCEPTED plus an acknowledged write is still not RF.

    The kill: any ``rfConfirmed`` derived from the lease, or from the
    ``ACCEPTED`` outcome, reads ``True`` here — the lease is held, the write
    reached the wire and the provider acknowledged it, and the read round trip
    itself succeeded. What is missing is the only thing that means RF: a
    causally-ordered observation. ``radioTx`` must keep reporting the last one
    there was, which still says ``off``.
    """
    provider = _MuteableProvider([])
    radio, runtime = await _armed(provider)
    provider.mute = True

    transition = await runtime.request_on(_WEB)

    assert transition.outcome is TxOutcome.ACCEPTED
    assert "ptt(on)" in provider.log  # the write was acknowledged on the wire
    payload = build_tx_safety_payload(radio)
    assert payload["lease"]["held"] is True
    assert payload["keyRequested"] is True
    assert payload["phase"] == "key_pending"
    assert payload["rfConfirmed"] is False
    assert payload["radioTx"] == "off"
    assert payload["externalConflict"] is False

    # ...and it is not hardwired: the confirming observation flips it.
    provider.mute = False
    await runtime.request_fresh_ptt()

    confirmed = build_tx_safety_payload(radio)
    assert confirmed["phase"] == "keyed"
    assert confirmed["rfConfirmed"] is True
    assert confirmed["radioTx"] == "on"


async def test_a_driven_release_only_lease_advertises_no_armed_watchdog() -> None:
    """MOR-1204: ``watchdog_enabled`` is true here and nothing can fire.

    ``force_unkey`` adopts an unowned key as a pure release obligation, and
    ``_begin_release`` clears the max key-down deadline; ``tick`` re-arms that
    branch only while no release is pending. So the supervisor's own
    ``watchdog_enabled`` — configured *and* driven — stays ``True`` over a lease
    no deadline backs. Reporting that flag as "armed" is the MOR-1204 lie, and
    it is the mutation this kills: ``armed`` must follow the deadline.
    """
    provider = _MuteableProvider([])
    radio, runtime = await _armed(provider)
    provider.mute = True  # the release cannot confirm, so the lease persists

    transition = await runtime.force_unkey(
        _WEB, reason=TxReleaseReason.OPERATOR_FORCED_UNKEY
    )
    assert transition.outcome is TxOutcome.ACCEPTED
    await _settle(
        lambda: runtime.tx_snapshot.watchdog_enabled, what="the ticker's first tick"
    )

    payload = build_tx_safety_payload(radio)

    assert payload["watchdog"]["driven"] is True  # the supervisor's own flag
    assert payload["watchdog"]["armed"] is False  # ...and it covers nothing
    assert payload["watchdog"]["deadlineMonotonic"] is None
    assert payload["watchdog"]["secondsRemaining"] is None
    assert payload["durableOff"]["owed"] is True
    assert payload["lease"]["held"] is True
    assert payload["keyRequested"] is False  # a release obligation, not a key
    # A driven supervisor still has a retry ladder, so this is not uncertain.
    assert payload["uncertainShutdown"] is False


async def test_a_cancelled_handback_is_an_uncertain_shutdown() -> None:
    """MOR-1014's terminal, surfaced: RELEASE_REQUIRED, no watchdog, no path out.

    A rigctld session drops mid-over and its handback is cancelled while the
    ``WRITE_OFF`` is on the wire. The supervisor keeps the lease, keeps the
    obligation, and keeps the attempt — which nothing will ever settle, so
    ``_service_release`` will not start another and the cleared watchdog
    deadline will not re-arm. The pair of reads is the discriminating part: one
    moment inside the attempt's own deadline is *not* uncertain, and an
    implementation that calls every pending release uncertain fails on it.
    """
    provider = _GatedUnkey([])
    radio, runtime = await _armed(provider)
    assert (await runtime.request_on(_RIGCTLD)).outcome is TxOutcome.ACCEPTED
    provider.gate_unkey = True

    handback = asyncio.ensure_future(
        runtime.release_owner(_RIGCTLD, reason=TxReleaseReason.CLIENT_DISCONNECTED)
    )
    await asyncio.wait_for(provider.reached.wait(), timeout=2)
    handback.cancel()
    await asyncio.gather(handback, return_exceptions=True)

    attempt = runtime.tx_snapshot.active_attempt
    assert attempt is not None and attempt.kind is ProviderAttemptKind.WRITE_OFF

    in_flight = build_tx_safety_payload(radio, now=attempt.started_at_monotonic)
    assert in_flight["uncertainShutdown"] is False
    assert in_flight["activeAttempt"]["overdue"] is False

    stranded = build_tx_safety_payload(
        radio, now=attempt.started_at_monotonic + attempt.timeout_seconds
    )
    assert stranded["phase"] == "release_required"
    assert stranded["lease"]["held"] is True
    assert stranded["owner"] == {"source": "rigctld", "sessionId": "rigctld-client-1"}
    assert stranded["watchdog"]["armed"] is False
    assert stranded["durableOff"]["owed"] is True
    assert stranded["durableOff"]["requestedReason"] == "client_disconnected"
    assert stranded["activeAttempt"]["kind"] == "write_off"
    assert stranded["activeAttempt"]["overdue"] is True
    assert stranded["uncertainShutdown"] is True
    assert stranded["uncertainReason"] == "unsettled_attempt"

    provider.gate.set()  # let the parked write finish; nothing depends on it


async def test_a_shutdown_that_could_not_unkey_reports_its_retries_and_last_error() -> (
    None
):
    """The other uncertain terminal: the obligation outlives its driver.

    A server shutdown emergency-releases a keyed rig, the OFF write fails, and
    the ticker that owns the retry ladder is retired on the way out. What is
    left is an owed durable OFF with no attempt in flight and nothing to start
    another — and the retry count and the provider's own error are the evidence
    an operator needs to know how hard it tried. The mutation this kills is a
    ``uncertainShutdown`` keyed on the watchdog flag alone: ``watchdog.driven``
    is ``False`` here, but so is it during an orderly idle shutdown.
    """
    provider = _Provider([])
    radio, runtime = await _armed(provider)
    assert (await runtime.request_on(_WEB)).outcome is TxOutcome.ACCEPTED
    provider.write_failures = 99  # every OFF from here fails on the wire

    async def _release_provider() -> None:
        return None

    await runtime.shutdown(release_provider=_release_provider)

    payload = build_tx_safety_payload(radio)

    assert payload["lease"]["held"] is True
    assert payload["durableOff"]["owed"] is True
    assert payload["durableOff"]["terminalReason"] == "server_shutdown"
    assert payload["durableOff"]["attempts"] >= 1
    assert payload["durableOff"]["lastError"] == "managed PTT write failed"
    assert payload["watchdog"]["armed"] is False
    assert payload["watchdog"]["driven"] is False
    assert payload["activeAttempt"] is None
    assert payload["uncertainShutdown"] is True
    assert payload["uncertainReason"] == "no_driver"


# ===========================================================================
# Review cycle 1 -- the three lies the mutation battery found unpinned
#
# The payload was already honest on all three; nothing here changes production
# code. What was missing is a test that fails when it stops being honest, which
# is the MOR-1221/1226 pinning discipline applied to this surface: each case
# below drives the one snapshot state the mutation is invisible in, because
# every other test in this file happens to run with that field at its
# uninteresting value.
# ===========================================================================


async def test_an_unobserved_ptt_reads_unknown_and_never_off() -> None:
    """F4: ``radioTx`` must not launder "we do not know" into "off".

    Losing provider readiness drops the observation — there is no longer any
    authoritative PTT truth — while the lease and its fresh durable OFF
    obligation stand. ``RadioTx.UNKNOWN`` is the whole point of the third
    value: an operator reading ``off`` here would conclude the rig is off the
    air at exactly the moment nothing knows whether it is. The mutation this
    kills ("unknown reads as off") is invisible everywhere else in this file,
    because every other case has a live observation.
    """
    radio, runtime = await _armed(_Provider([]))
    assert (await runtime.request_on(_WEB)).outcome is TxOutcome.ACCEPTED
    assert build_tx_safety_payload(radio)["radioTx"] == "on"

    # The control transport goes: the provider is no longer authoritative for
    # PTT, so the supervisor discards the observation and owes an OFF.
    await runtime.set_provider_ready(ready=False)

    payload = build_tx_safety_payload(radio)
    assert payload["radioTx"] == "unknown"
    assert payload["radioTx"] != "off"
    assert payload["rfConfirmed"] is False
    assert payload["provider"]["ready"] is False
    assert payload["lease"]["held"] is True
    assert payload["durableOff"]["owed"] is True
    assert payload["phase"] == "release_required"


async def test_a_release_reason_that_changed_shows_both_reasons() -> None:
    """F6: the terminal reason may diverge from the requested one, and both matter.

    The supervisor keeps the reason a release was *begun* for and overwrites
    only the terminal one when a second, later reason claims it — here a
    transport loss that starts the release, then the operator's own release
    landing on top of it. Collapsing the two loses the diagnosis: "the operator
    unkeyed" and "the link dropped and then the operator unkeyed" are different
    incidents. The mutation this kills is ``terminalReason ← requestedReason``
    (and its mirror), which every other case in this file agrees with, because
    a release begun once and never re-reasoned has them equal.
    """
    radio, runtime = await _armed(_Provider([]))
    assert (await runtime.request_on(_WEB)).outcome is TxOutcome.ACCEPTED
    await runtime.set_provider_ready(ready=False)  # begins it: transport lost

    transition = await runtime.release_owner(
        _WEB, reason=TxReleaseReason.OPERATOR_RELEASE
    )

    assert transition.outcome is TxOutcome.IDEMPOTENT  # one release, new reason
    payload = build_tx_safety_payload(radio)
    assert payload["durableOff"]["requestedReason"] == "control_transport_lost"
    assert payload["durableOff"]["terminalReason"] == "operator_release"
    assert (
        payload["durableOff"]["requestedReason"]
        != payload["durableOff"]["terminalReason"]
    )


class _Clock:
    """A hand-wound monotonic clock; the supervisor is pure, so this is enough."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _SupervisorHost:
    """A radio-shaped publisher over a **real** supervisor, and nothing else.

    The runtime rewrites every observation's timestamp to its own ``clock()``
    on the way in, which is right for production and makes the pre-acquisition
    observation below unreachable through it. So this drives the real
    ``TxSafetySupervisor`` — the actual policy object, not a double — directly,
    and publishes it the way :class:`ManagedTxCapable` requires: a real class
    member holding an object with a ``tx_snapshot``. Same synthetic footing as
    ``_NoOpRetireBackend``/``_EqTransport`` in ``test_managed_tx_identity_pins``:
    no shipped backend is shaped like this, and the projection must not depend
    on that being true.
    """

    def __init__(self, supervisor: TxSafetySupervisor) -> None:
        self._supervisor = supervisor

    @property
    def managed_tx(self) -> "_SupervisorHost":
        return self

    @property
    def tx_snapshot(self) -> TxSafetySnapshot:
        return self._supervisor.snapshot


def test_rf_the_lease_cannot_account_for_is_reported_as_an_external_conflict() -> None:
    """F5: the rig reads ON, we hold the lease, and it still is not our RF.

    A PTT read already in flight when the key request landed answers ``ON``
    from *before* the acquisition boundary. The supervisor accepts it as the
    newest truth and refuses to credit it to this lease — which is exactly the
    causal boundary's job — so the rig is transmitting for a reason this lease
    cannot account for: a front-panel key, another controller, a stuck PTT.
    ``externalConflict`` is the only field that says so, and hardcoding it
    ``False`` survives every other test here, all of which key a rig that was
    observably off.
    """
    clock = _Clock(now=0.0)
    ids = iter(f"id-{index}" for index in range(1, 100))
    supervisor = TxSafetySupervisor(clock=clock, id_factory=lambda: next(ids))
    supervisor.replace_provider(1, ready=True)
    supervisor.observe_ptt(ProviderPttObservation(RadioTx.OFF, 1, 1, 0.0))
    host = _SupervisorHost(supervisor)

    clock.now = 10.0  # the key request lands, and with it the causal boundary
    assert supervisor.request_on(_WEB).outcome is TxOutcome.ACCEPTED
    # The in-flight read returns: newer than anything seen, older than the key.
    assert (
        supervisor.observe_ptt(ProviderPttObservation(RadioTx.ON, 1, 2, 5.0)).outcome
        is TxOutcome.APPLIED
    )

    payload = build_tx_safety_payload(host, now=clock.now)

    assert payload["radioTx"] == "on"  # the rig is transmitting
    assert payload["externalConflict"] is True  # ...and not because of us
    assert payload["rfConfirmed"] is False  # so this is not confirmed RF
    assert payload["phase"] == "key_pending"
    assert payload["keyRequested"] is True
    assert payload["lease"]["held"] is True


# ===========================================================================
# Unsupported ingresses are visible, not silently uncovered
# ===========================================================================


def test_an_unmanaged_radio_says_so_and_publishes_no_safety_fields() -> None:
    """MOR-1225: absence is stated, never implied by a block of falses.

    A radio outside a connect session publishes no supervisor. Emitting the
    managed field set with zeroes would read exactly like a supervised rig at
    rest — idle phase, no obligation, no conflict — which is the one reading
    this block must never support.
    """
    payload = build_tx_safety_payload(IcomRadio("127.0.0.1"))

    assert payload["status"] == "unmanaged"
    for field in ("phase", "lease", "watchdog", "durableOff", "uncertainShutdown"):
        assert field not in payload
    assert all(entry["supervised"] is False for entry in payload["ingress"].values())


def test_the_backends_with_no_managed_arm_publish_no_supervisor() -> None:
    """The boundary MOR-1225 documents, asserted on the shipping classes.

    Yaesu CAT and the rigctld-client backend are legacy TX paths pending
    MOR-1190: they publish no ``managed_tx`` member at all, so the diagnostics
    above report them ``unmanaged`` by construction rather than by a hard-coded
    list of backend names that could drift away from the code.
    """
    for backend in (YaesuCatRadio, RigctldClientRadio):
        assert getattr_static(backend, "managed_tx", None) is None


def test_a_raising_supervisor_accessor_is_unreadable_never_unmanaged() -> None:
    """A broken accessor must not resolve towards "no supervision here".

    ``getattr(radio, "managed_tx", None)`` — the MOR-1193 collapse — absorbs an
    ``AttributeError`` raised *inside* the property and reports ``None``, which
    on this surface would print "unmanaged" over a rig that may well be keyed
    under a supervisor nobody can read. The status must say the read failed.
    """

    class _BrokenBackend:
        @property
        def managed_tx(self) -> object:
            raise AttributeError("managed_tx: typo on a nested attribute")

    payload = build_tx_safety_payload(_BrokenBackend())

    assert payload["status"] == "unreadable"
    assert payload["error"] == "AttributeError"
    assert "phase" not in payload


async def test_ingresses_without_a_stable_owner_show_as_refused_on_a_managed_rig() -> (
    None
):
    """ "Visible/disabled" is answered by the key path's own gate.

    Web and rigctld carry a session id something is obliged to tear down, so
    they hold leases; HTTP and the public API do not, and on a managed rig the
    ingress gate refuses their keys outright rather than dropping to the legacy
    write. The payload must report that refusal, not paper over it — a view
    that marked every ingress supervised because the *radio* is managed fails
    here.
    """
    radio, _runtime = await _armed(_Provider([]))

    ingress = build_tx_safety_payload(radio)["ingress"]

    assert ingress["websocket"] == {"supervised": True, "keyRefused": False}
    assert ingress["rigctld"] == {"supervised": True, "keyRefused": False}
    assert ingress["http"] == {"supervised": False, "keyRefused": True}
    assert ingress["public_api"] == {"supervised": False, "keyRefused": True}


# ===========================================================================
# The wiring: additive on an existing document
# ===========================================================================


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None

    def get_extra_info(self, _name: str, default: object = None) -> object:
        return default


def _runtime_document(writer: _FakeWriter) -> dict[str, object]:
    head, _, body = bytes(writer.buffer).partition(b"\r\n\r\n")
    assert b"200" in head.split(b"\r\n")[0]
    return json.loads(body.decode())


async def test_the_runtime_document_gains_tx_safety_and_loses_nothing() -> None:
    """Additive: a new key beside the blocks that were already there."""
    radio = SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    writer = _FakeWriter()

    await server._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001

    document = _runtime_document(writer)
    assert document["txSafety"] == {"status": "unmanaged", "ingress": ANY_INGRESS}
    for existing in ("radio", "station", "bridge", "audioBus", "connection", "rigctld"):
        assert existing in document


ANY_INGRESS = {
    "websocket": {"supervised": False, "keyRefused": False},
    "http": {"supervised": False, "keyRefused": False},
    "rigctld": {"supervised": False, "keyRefused": False},
    "public_api": {"supervised": False, "keyRefused": False},
}


# ===========================================================================
# System proof 1 -- one supervisor behind every supported ingress
# ===========================================================================


async def test_every_supported_ingress_binds_the_one_supervisor_the_payload_reads() -> (
    None
):
    """Four doors, one supervisor — and the diagnostics read that same one.

    ``test_managed_tx_assembly`` proves Web, CLI and SDK land on one object.
    This adds the two the paid MSP also ships — rigctld (MOR-1014) and the
    validation tool (MOR-1222) — and closes the loop MOR-1015 owns: a lease
    taken through rigctld must be the lease the Web diagnostics report, and a
    Web bind must be refused while it stands. Two supervisors over one PTT line
    would satisfy every ``is`` assertion written against the first one and fail
    exactly here, on the BUSY.
    """
    radio, runtime = await _armed(_Provider([]))

    web = bind_managed_tx(radio, "websocket", "ws-1")
    rigctld = bind_managed_tx(radio, "rigctld", "rigctld-client-1")
    sdk = ManagedTxApi.bind(radio, _SDK)
    assert web is not None and rigctld is not None and sdk is not None
    assert web.supervisor is rigctld.supervisor is sdk.supervisor is runtime

    assert (await rigctld.set_ptt(True)).outcome is TxOutcome.ACCEPTED

    payload = build_tx_safety_payload(radio)
    assert payload["owner"] == {"source": "rigctld", "sessionId": "rigctld-client-1"}
    assert payload["lease"]["id"] == runtime.tx_snapshot.lease_id
    # One supervisor, so the other doors see the lease rather than a second one.
    assert (await web.set_ptt(True)).outcome is TxOutcome.BUSY
    assert (await sdk.set_ptt(True)).outcome is TxOutcome.BUSY

    await rigctld.set_ptt(False)


# ===========================================================================
# System proof 2 -- no bare set_ptt bypass anywhere in the shipping package
# ===========================================================================

#: Modules whose backend publishes no managed supervisor at all, so their PTT
#: write has nothing to route through. Both are the documented legacy boundary
#: (MOR-1225), both are pending managed arm under MOR-1190, and both are here
#: by module rather than by line so a refactor inside them stays green while a
#: *new* module joining the list does not.
_UNMANAGED_TX_BACKENDS = frozenset(
    {
        "backends/rigctld_client/radio.py",
        "backends/yaesu_cat/poller.py",
    }
)

#: The receiver name every managed write is made through: a bound
#: :class:`ManagedTxApi`, never the radio.
_FACADE = "managed"


def _package_root() -> Path:
    """The ``rigplane`` package these tests actually imported.

    Keyed off the imported module, not ``cwd``: an editable install can resolve
    to a different worktree's source, and a scan run from the checkout would be
    auditing files that are not the ones under test.
    """
    return Path(radio_module.__file__).parent.parent


def _ptt_write_sites() -> list[tuple[str, str, str, bool]]:
    """Every ``X.set_ptt(...)`` call in the package.

    Returns ``(module, function, receiver, function_also_uses_the_facade)``.
    Parsed rather than grepped so the receiver — which is the whole question —
    is read from the syntax tree instead of a regexp over a line.
    """
    root = _package_root()
    sites: list[tuple[str, str, str, bool]] = []
    for path in sorted(root.rglob("*.py")):
        module = str(path.relative_to(root))
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [
                call
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "set_ptt"
            ]
            receivers = [ast.unparse(call.func.value) for call in calls]
            facade = _FACADE in receivers
            sites.extend(
                (module, node.name, receiver, facade) for receiver in receivers
            )
    return sites


def test_no_production_path_writes_ptt_around_the_managed_facade() -> None:
    """The repository search MOR-1015 asks for, as an executable invariant.

    A bare ``radio.set_ptt(...)`` is legitimate in exactly two shapes: the
    legacy arm of a managed check — the same function also writes through the
    bound facade, so the bare call is the ``managed is None`` branch — or a
    backend that publishes no supervisor to route through at all. Anything else
    is a key with no lease, no owner and no watchdog, which is the failure this
    subsystem exists to prevent.

    The kill: adding ``await self._radio.set_ptt(True)`` to any web handler,
    rigctld command, CLI path or SDK call that does not already bind a facade
    fails this test, and no assertion written against today's call sites would
    have noticed.
    """
    unguarded = [
        (module, function, receiver)
        for module, function, receiver, facade in _ptt_write_sites()
        if receiver != _FACADE and not facade and module not in _UNMANAGED_TX_BACKENDS
    ]

    assert unguarded == []


def test_the_unmanaged_backend_allowance_is_used_and_not_a_blanket() -> None:
    """The allowance must stay exactly as wide as the backends that need it.

    Two failure modes, both silent: an entry that no longer matches anything
    (the module was fixed, or renamed, and the exemption lingers to cover the
    next bare write that lands there), and a managed module quietly acquiring
    one. Pinning the exercised set to the declared set catches both. Also the
    non-vacuity guard for the test above — a scan that found nothing would pass
    it trivially.
    """
    sites = _ptt_write_sites()
    assert sites, "the scan found no PTT writes at all — it is not reading the package"

    exempt = {
        module
        for module, _function, receiver, facade in sites
        if receiver != _FACADE and not facade
    }
    assert exempt == _UNMANAGED_TX_BACKENDS

    # And the managed facade is genuinely the path everywhere else: every
    # ingress that ships one is represented.
    facade_modules = {
        module for module, _fn, receiver, _facade in sites if receiver == _FACADE
    }
    assert {"web/radio_poller.py", "rigctld/handler.py", "runtime/sync.py"} <= (
        facade_modules
    )
