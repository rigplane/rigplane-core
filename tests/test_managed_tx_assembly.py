"""MOR-1016: the ``managed_tx`` member on ``CoreRadio``, and its assembly.

PR1 gave ``CoreRadio`` the real class member
:class:`~rigplane.core.radio_protocol.ManagedTxCapable` requires, inert:

1. the member is a real property -- visible to
   :func:`inspect.getattr_static` without running it, never conjured through
   ``__getattr__`` (the exact trap ``ManagedTxCapable``'s docstring warns
   about); and
2. before the first ``connect()`` every consumer of it (``ManagedTxApi.bind``,
   the Web reconnect hook) sees ``None`` and falls through to the legacy,
   unsupervised ``set_ptt`` path -- because a radio that was never connected
   keeps ``self._managed_tx_runtime`` at its ``None`` default.

PR2 makes it real: ``connect()`` builds the runtime, captures the CI-V
transport it just opened and seeds the authoritative OFF, so a connected radio
publishes a supervisor every ingress binds. What that adds here is the
three-step handshake and, more importantly, its failure mode -- a rig that
cannot be supervised must degrade to refusing TX, never back to ``None``,
because ``None`` hands the next key to the unsupervised legacy write with no
lease, no owner and no watchdog (MOR-1193).

No ``MagicMock`` stands in for a protocol-shaped object here (a mock answers
every ``getattr``, so it could never show the unmanaged path staying
unmanaged, and it satisfies a ``runtime_checkable`` protocol on 3.11 but not
on 3.12+ -- gh-102433); every radio below is a real ``CoreRadio``. The wire is
the hand-rolled ``_Provider`` the watchdog suite already uses, driving a real
:class:`~rigplane.runtime.managed_radio_runtime.ManagedRadioRuntime` over the
real effect service.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Iterator

import pytest

from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.tx_safety import (
    RadioTx,
    TxOutcome,
    TxOwner,
    TxSource,
)
from rigplane.exceptions import CommandError
from rigplane.runtime import radio as radio_module
from rigplane.runtime.radio import CoreRadio, IcomRadio
from rigplane.web.server import WebServer
from test_web_recovery_durable_off import _Observer, _Provider

_OWNER = TxOwner(TxSource.SDK, "session")


def _unconnected_radio() -> IcomRadio:
    """A constructed-but-never-connected radio -- no transport, no runtime."""
    return IcomRadio("127.0.0.1")


# --- (a) real class member, not a conjured attribute -----------------------


def test_managed_tx_is_a_real_class_member_not_a_conjured_attribute() -> None:
    # ``getattr_static`` bypasses ``__getattribute__``/``__getattr__``
    # entirely, so it only finds ``managed_tx`` if some class in the MRO
    # actually defines it (as a property, here). If a subclass ever "adds"
    # the attribute dynamically instead, this is exactly what stops seeing
    # it -- which is the point: ``ManagedTxApi.bind`` relies on the same
    # read to settle absence without running any accessor.
    radio = _unconnected_radio()

    static = inspect.getattr_static(radio, "managed_tx", None)

    assert static is not None
    assert isinstance(static, property)


def test_managed_tx_member_lives_on_core_radio_not_a_subclass() -> None:
    # The property must be on ``CoreRadio`` itself so every backend built on
    # it (not just ``IcomRadio``) inherits the same structural surface.
    assert isinstance(vars(CoreRadio).get("managed_tx"), property)


# --- (b) constructed-but-unconnected radio: managed_tx is None -------------


def test_unconnected_radio_managed_tx_is_none() -> None:
    radio = _unconnected_radio()

    assert radio.managed_tx is None


def test_bare_core_radio_managed_tx_is_none() -> None:
    # Not just the LAN subclass -- the member is inert on the shared base too.
    radio = CoreRadio("127.0.0.1")

    assert radio.managed_tx is None


# --- (c) ManagedTxApi.bind still declines: legacy path preserved -----------


def test_bind_on_unconnected_radio_returns_none() -> None:
    # Unmanaged is a positive finding here, not a fallback from a failed
    # read: the member exists (case a/b) and answers ``None``, so ``bind``
    # must decline without touching ``set_ptt`` or anything else on the
    # radio.
    radio = _unconnected_radio()

    assert ManagedTxApi.bind(radio, _OWNER) is None


async def test_legacy_set_ptt_is_still_the_only_write_path() -> None:
    # With no supervisor bound, a caller that goes through ``ManagedTxApi``
    # gets nothing to call; the legacy ``set_ptt`` on the radio itself must
    # remain the sole write path, completely unaffected by the new member.
    radio = _unconnected_radio()
    writes: list[bool] = []

    async def _record(on: bool) -> None:
        writes.append(on)

    radio.set_ptt = _record  # type: ignore[method-assign]

    assert ManagedTxApi.bind(radio, _OWNER) is None
    await radio.set_ptt(True)
    await radio.set_ptt(False)

    assert writes == [True, False]


# --- (d) the Web reconnect consumer sees None and no-ops -------------------


async def test_web_managed_tx_release_hook_no_ops_on_an_inert_radio() -> None:
    # ``WebServer._service_managed_tx_release`` is the one production
    # consumer already wired to the ``getattr_static`` two-step (MOR-1192/
    # MOR-1196). Against a radio with the new-but-inert member it must take
    # the same "unmanaged" early return it always has -- no exception, no
    # rebind attempted, no lock contention.
    radio = _unconnected_radio()
    server = WebServer(radio)  # type: ignore[arg-type]

    result = await server._service_managed_tx_release()

    assert result is None
    assert not server._managed_tx_rebind_lock.locked()


# --- (e) static protocol-conformance guard lives in radio.py, mypy-checked -


def test_static_supervisor_conformance_guard_is_mypy_only() -> None:
    # ``radio.py`` carries a ``TYPE_CHECKING``-guarded function whose only
    # job is to make ``uv run mypy src/`` fail if ``ManagedRadioRuntime``
    # ever stops satisfying ``ManagedTxSupervisor`` -- signature drift that
    # ``ManagedTxApi.bind``'s ``getattr_static`` read cannot catch, since it
    # only checks member *presence*, never shape. It must not exist at
    # runtime (guarded by ``TYPE_CHECKING``, so it costs nothing today), and
    # its source must actually reference the supervisor protocol for the
    # mypy check to mean anything.
    assert not hasattr(radio_module, "_managed_tx_runtime_satisfies_supervisor")

    source = inspect.getsource(radio_module)
    assert "_managed_tx_runtime_satisfies_supervisor" in source
    assert "ManagedTxSupervisor" in source
    assert "ManagedRadioRuntime" in source


def test_unconnected_radio_publishes_no_tx_snapshot() -> None:
    # The MOR-1015 seam follows the member: no runtime, no snapshot -- and no
    # exception, so a presentation layer can read it unconditionally.
    assert _unconnected_radio().tx_snapshot is None


# ===========================================================================
# PR2 -- connect() builds, binds and seeds the runtime
# ===========================================================================


class _CivPort:
    """A CI-V data transport stand-in; its identity is all the radio reads.

    ``soft_disconnect`` is the one path that calls a method on it, so the close
    is real (and idempotent) rather than an attribute error swallowed by the
    teardown's ``except``.
    """

    def __init__(self) -> None:
        self.closed = False

    async def disconnect(self) -> None:
        self.closed = True


class _Session:
    """The session lifecycle reduced to what connect/disconnect observe.

    A real ``CoreRadioSessionLifecycle`` wants a rig on the other end of a
    socket. All ``_arm_managed_tx`` needs from it is the state a completed
    control phase leaves behind: a negotiated CI-V data port and a fresh CI-V
    epoch (the same ``_advance_civ_generation`` the real path calls).

    ``disconnect()`` is the mirror, and the reason it logs into the provider's
    log rather than one of its own: PR4's claim is an *ordering* one — the
    durable OFF against the closing of the session that carries it — and one
    list is the only way to state it. It keys off its own connected flag, not
    the transport, because by the time it runs the managed shutdown has
    already retired that port; the session close is what is left, and it is a
    no-op on a radio that never connected (as the real lifecycle's is).
    """

    def __init__(self, radio: CoreRadio) -> None:
        self._radio, self.connects, self.closes = radio, 0, 0

    async def connect(self) -> None:
        self.connects += 1
        self._radio._civ_port = 50002
        self._radio._civ_transport = _CivPort()  # type: ignore[assignment]
        self._radio._advance_civ_generation("test-connect")
        self._radio._connected = True

    async def disconnect(self) -> None:
        if not self._radio._connected:
            return
        self.closes += 1
        self._radio.provider.log.append("session_close")  # type: ignore[attr-defined]
        self._radio._civ_transport = None
        self._radio._connected = False


class _AssembledRadio(CoreRadio):
    """A real ``CoreRadio`` whose CI-V wire is a hand-rolled provider.

    Everything MOR-1016 assembles is genuine here -- ``connect()``, the
    runtime, the effect service, the supervisor. Only the five
    ``ProviderTxLifecycle`` methods and the probe's one command send are
    re-pointed at the double, which is where the CI-V frames would go;
    ``tests/test_civ_rx_coverage.py`` owns that layer, and routing through it
    here would test the transport twice while proving nothing about the
    assembly.
    """

    def __init__(self, provider: _Provider, *, answers_probe: bool = True) -> None:
        super().__init__("127.0.0.1")
        self.provider = provider
        self.answers_probe = answers_probe
        self.probes: list[bytes] = []
        self._session_lifecycle = _Session(self)  # type: ignore[assignment]

    async def _fetch_initial_state(self) -> None:
        return None

    async def _send_civ_expect(self, civ_frame: bytes, **kwargs: object) -> object:
        # The ordinary command path the arming probe rides on. A rig that does
        # not implement 0x1C 0x00 answers nothing and the real helper raises.
        self.probes.append(civ_frame)
        if not self.answers_probe:
            raise CommandError("No response for managed_tx_ptt_probe")
        return object()

    def _unbind_authoritative_ptt_observer(self) -> None:
        self.provider._unbind_authoritative_ptt_observer()

    def _capture_managed_tx_port(
        self, provider_generation: int, observer: _Observer
    ) -> bool:
        return self.provider._capture_managed_tx_port(provider_generation, observer)

    async def _write_managed_ptt(self, provider_generation: int, on: bool) -> None:
        await self.provider._write_managed_ptt(provider_generation, on)

    async def _request_authoritative_ptt_read(
        self, provider_generation: int, observer: _Observer
    ) -> None:
        await self.provider._request_authoritative_ptt_read(
            provider_generation=provider_generation, observer=observer
        )

    async def _retire_managed_tx_port(self, provider_generation: int) -> None:
        await self.provider._retire_managed_tx_port(provider_generation)
        # What the real ``CivRuntime.retire_managed_tx_port`` does to the
        # radio, and the reason a failed arm is not a dead end: retiring the
        # port drops the CI-V transport and advances the epoch, so the next
        # ``connect()`` is a genuinely new epoch and may attempt arming again.
        self._advance_civ_generation("managed TX physical port retired")
        self._civ_transport = None


class _MutePtt(_Provider):
    """A rig that passes the probe, captures its port, then goes quiet.

    Exactly what ``CoreRadio._request_authoritative_ptt_read`` raises when the
    response is not authoritative for the request -- the shape of a seed
    failure on an already-captured port, which is the one that walks into the
    retirement trap.
    """

    async def _request_authoritative_ptt_read(
        self, *, provider_generation: int, observer: _Observer
    ) -> None:
        self.log.append("read_ptt")
        raise CommandError("PTT response was not authoritative for this request")


_LIVE: list[_AssembledRadio] = []


@pytest.fixture(autouse=True)
def _release_assembled_radios() -> Iterator[None]:
    """Keep ``CoreRadio.__del__``'s forgotten-teardown WARN out of the log."""
    yield
    while _LIVE:
        _LIVE.pop()._connected = False


async def _connected(
    provider: _Provider | None = None, *, answers_probe: bool = True
) -> _AssembledRadio:
    radio = _AssembledRadio(provider or _Provider([]), answers_probe=answers_probe)
    _LIVE.append(radio)
    await radio.connect()
    return radio


# --- (a) a connected radio is armed, ready and seeded ----------------------


async def test_connect_publishes_an_armed_supervisor() -> None:
    radio = await _connected()

    assert radio.managed_tx is not None
    assert radio.managed_tx is radio._managed_tx_runtime
    # A successful arm captures the CI-V transport connect() just built and
    # leaves it exactly where it found it -- no retirement, no epoch churn.
    assert radio.connected
    snapshot = radio.tx_snapshot
    assert snapshot is not None
    assert snapshot.provider_ready is True
    # The seed landed: the supervisor has an authoritative OFF, which is the
    # difference between a runtime that can key and one that cannot.
    assert snapshot.radio_tx is RadioTx.OFF
    # Exactly one authoritative read, and nothing keyed the rig on the way.
    assert radio.provider.log == ["read_ptt"]


async def test_target_id_names_the_civ_endpoint_of_a_lan_radio() -> None:
    # The port is the one the control phase negotiated, not the constructor's
    # control port -- two rigs behind one host still get distinct runtimes.
    radio = await _connected()

    assert radio._managed_tx_runtime is not None
    assert radio._managed_tx_runtime.target_id == "rigplane:127.0.0.1:50002"


def test_target_id_names_the_device_of_a_serial_radio() -> None:
    # ``_IcomSerialRadioBase`` passes the device path as ``host`` with no
    # ports, so the LAN form would render it as a rig on port zero.
    radio = Icom7610SerialRadio(device="/dev/tty.usbserial-1", civ_link=_FakeCivLink())

    assert radio._managed_tx_target_id == "serial:/dev/tty.usbserial-1"


class _FakeCivLink:
    """Enough of ``SerialCivLink`` to construct a serial radio, never used."""

    connected = ready = healthy = False

    async def connect(self) -> None:  # pragma: no cover - never connected
        raise AssertionError("the serial target-id test must not open a port")

    async def disconnect(self) -> None:  # pragma: no cover - never connected
        return None

    async def send(self, frame: bytes) -> None:  # pragma: no cover
        return None

    async def receive(self, timeout: float | None = None) -> bytes | None:
        return None  # pragma: no cover


# --- (c) the seed is what makes a key possible -----------------------------


async def test_the_seeded_off_is_what_lets_a_key_through() -> None:
    # Nothing polls PTT periodically, so ``request_on`` would answer
    # RADIO_NOT_OFF forever if arming skipped the ``request_fresh_ptt`` step.
    # This is the test that pins step 3: drop the seed and every key on every
    # managed rig is refused, silently and permanently.
    radio = await _connected()
    api = ManagedTxApi.bind(radio, _OWNER)
    assert api is not None

    transition = await api.set_ptt(True)

    assert transition.outcome is TxOutcome.ACCEPTED
    assert transition.snapshot.lease_id is not None
    assert "ptt(on)" in radio.provider.log

    await api.set_ptt(False)


# --- (b) a rig that cannot be supervised degrades, never disappears --------


async def test_a_rig_that_never_answers_ptt_stays_managed_and_refuses_tx() -> None:
    # A rig that does not implement 0x1C 0x00 at all: the probe settles it
    # before any port is captured.
    radio = await _connected(answers_probe=False)

    # connect() returned normally -- the arming failure never propagated --
    # and the member is still published.
    assert radio.managed_tx is not None
    snapshot = radio.tx_snapshot
    assert snapshot is not None
    assert snapshot.provider_ready is False

    api = ManagedTxApi.bind(radio, _OWNER)
    assert api is not None  # NOT the legacy path: bind still finds a supervisor

    transition = await api.set_ptt(True)

    # Fail closed: refused for a stated reason, and nothing reached the wire.
    assert transition.outcome is TxOutcome.NOT_READY
    assert transition.snapshot.lease_id is None
    assert radio.provider.log == []


async def test_a_rig_that_fails_the_probe_keeps_its_civ_session() -> None:
    # The guard on the retirement trap. Retirement disconnects the CI-V
    # transport, so an arming attempt that captures a port it cannot seed
    # closes the socket ``connect()`` just opened and hands the caller back a
    # radio that reports ``connected is False``. The probe runs on the
    # ordinary command path, before any capture, so a rig that cannot answer
    # never gets that far and keeps the session it just built.
    radio = await _connected(answers_probe=False)

    assert len(radio.probes) == 1  # exactly one probe, not a retry storm
    assert radio._civ_transport is not None
    assert radio.connected
    # Never captured means never retired: the epoch is the one connect() left.
    assert radio._managed_tx_armed_epoch == radio._civ_epoch


async def test_a_failed_seed_leaves_the_radio_armable_on_the_next_epoch() -> None:
    # The residual exposure the probe narrows but cannot close: a rig that
    # answers the probe and then drops the seed reply. The retirement fires,
    # which advances the CI-V epoch by itself. That must read as "eligible
    # again", not as a latched-off radio: the same runtime object arms cleanly
    # next time, so every facade already bound to it starts working without
    # rebinding.
    radio = await _connected(_MutePtt([]))
    runtime = radio._managed_tx_runtime
    assert runtime is not None
    assert radio.tx_snapshot is not None
    assert radio.tx_snapshot.provider_ready is False
    # The stated cost: the retirement took the CI-V transport with it, so the
    # data watchdog will reconnect. That reconnect is what supplies the next
    # epoch -- the attempt is not silently retried on a dead port, and it is
    # not latched off either.
    assert radio._civ_transport is None
    assert radio._managed_tx_armed_epoch != radio._civ_epoch

    radio.provider = _Provider(radio.provider.log)
    await radio._arm_managed_tx()

    assert radio._managed_tx_runtime is runtime
    snapshot = radio.tx_snapshot
    assert snapshot is not None
    assert (snapshot.provider_ready, snapshot.radio_tx) == (True, RadioTx.OFF)


# --- (d) one arming attempt per CI-V epoch ---------------------------------


async def test_arming_is_bounded_to_one_attempt_per_civ_epoch() -> None:
    # ``replace_provider`` is not free and not idempotent: a second one
    # retires the port the first captured, taking the live CI-V transport with
    # it. On an unchanged epoch, re-arming must not happen at all.
    radio = await _connected()
    runtime = radio._managed_tx_runtime
    assert runtime is not None
    replacements: list[bool] = []
    replace_provider = runtime.replace_provider

    async def _counted(*, ready: bool):  # type: ignore[no-untyped-def]
        replacements.append(ready)
        return await replace_provider(ready=ready)

    runtime.replace_provider = _counted  # type: ignore[method-assign]

    await radio._arm_managed_tx()
    await radio._arm_managed_tx()

    assert replacements == []
    assert radio.provider.log == ["read_ptt"]  # and no second seed either


async def test_a_fresh_epoch_re_arms_the_very_same_runtime() -> None:
    # Per *epoch*, not once per process: a reconnect gets a new managed port,
    # but the supervisor object survives it, so an ingress that bound a facade
    # before the drop still holds the right one afterwards.
    radio = await _connected()
    runtime = radio._managed_tx_runtime

    await radio.connect()

    assert radio._managed_tx_runtime is runtime
    assert radio.provider.log == ["read_ptt", "read_ptt"]
    snapshot = radio.tx_snapshot
    assert snapshot is not None
    assert (snapshot.provider_ready, snapshot.radio_tx) == (True, RadioTx.OFF)


# --- (e) every ingress binds the radio's own supervisor --------------------


async def test_bind_returns_a_facade_over_the_radios_own_runtime() -> None:
    radio = await _connected()

    api = ManagedTxApi.bind(radio, _OWNER)

    assert api is not None
    # Identity, not equality: two supervisors over one rig is two independent
    # leases over one PTT line, which is the failure managed TX exists to
    # prevent.
    assert api.supervisor is radio._managed_tx_runtime


async def test_two_radios_get_two_independent_supervisors() -> None:
    first, second = await _connected(), await _connected()

    one, two = ManagedTxApi.bind(first, _OWNER), ManagedTxApi.bind(second, _OWNER)

    assert one is not None and two is not None
    assert one.supervisor is not two.supervisor


# ===========================================================================
# PR4 -- teardown: the OFF goes out before the path does
# ===========================================================================
#
# ``disconnect()`` is the first production caller of ``shutdown``, which makes
# two things true here for the first time: a held lease is emergency-released
# on the way out, and the radio stops being managed. The order of the first
# against the session close is the whole point -- a WRITE_OFF issued after the
# transport is gone is a rig left keyed -- and the second is bounded, because a
# supervisor wedged on a rig that stopped answering must not be able to hold
# the socket close (the de-key of last resort) open behind it.
#
# ``soft_disconnect`` is the deliberate asymmetry: the path is coming back, so
# the runtime is parked rather than shut down.


async def test_a_keyed_rig_gets_its_off_before_the_session_closes() -> None:
    # The ordering claim, on one log: the durable OFF and the close of the
    # session that carries it. Reversed, the write goes out over a transport
    # that is already gone and the rig stays keyed with nobody supervising it
    # -- and disconnect() is precisely when nobody is left to notice.
    radio = await _connected()
    api = ManagedTxApi.bind(radio, _OWNER)
    assert api is not None
    assert (await api.set_ptt(True)).outcome is TxOutcome.ACCEPTED
    log = radio.provider.log

    await radio.disconnect()

    assert "ptt(off)" in log
    assert log.index("ptt(off)") < log.index("session_close")
    # Not merely written: the confirming read settled it, so the rig is
    # observably unkeyed rather than optimistically assumed to be.
    assert radio.provider._keyed is False
    assert radio._session_lifecycle.closes == 1  # type: ignore[attr-defined]


async def test_a_wedged_supervisor_cannot_hold_disconnect_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The bound is the whole reason the shutdown may go first at all. A rig
    # that stopped answering mid-release is exactly the case where the release
    # cannot complete *and* the socket close is the only de-key left, so the
    # wait has to be abandonable. ``shutdown`` shields its own task, so what is
    # abandoned is this wait, not the OFF: it keeps retrying behind us.
    #
    # Over the real port registry (defined below), because a shutdown that
    # never ran also never retired its port: teardown has to release the
    # session's CI-V bookkeeping on this path too, or a wedged supervisor
    # leaves behind a radio that can never arm again.
    radio = await _connected_over_the_real_registry()
    runtime = radio._managed_tx_runtime
    assert runtime is not None
    monkeypatch.setattr(radio_module, "_MANAGED_TX_TEARDOWN_TIMEOUT_S", 0.05)
    wedged = asyncio.Event()

    async def _never_settles(*, release_provider: object) -> None:
        await wedged.wait()  # a supervisor that never comes back

    runtime.shutdown = _never_settles  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="rigplane.runtime.radio"):
        # The outer bound is the test's own tripwire: without the inner one
        # this call never returns at all.
        await asyncio.wait_for(radio.disconnect(), timeout=2.0)

    assert radio._session_lifecycle.closes == 1  # type: ignore[attr-defined]
    assert radio.managed_tx is None  # and the radio is unmanaged either way
    assert any(
        "managed TX shutdown" in record.getMessage() for record in caplog.records
    )

    # And the radio is still reconnectable: a wedged supervisor costs this
    # session's supervision, not every session after it.
    await radio.connect()

    snapshot = radio.tx_snapshot
    assert snapshot is not None
    assert (snapshot.provider_ready, snapshot.radio_tx) == (True, RadioTx.OFF)


async def test_soft_disconnect_parks_the_provider_and_recovery_re_arms_it() -> None:
    # The asymmetry with disconnect(): the CI-V path is coming back, so the
    # runtime stays published and every facade bound to it stays valid. What
    # changes is that the gap refuses keys instead of accepting a lease whose
    # WRITE_ON would land on a socket that is already closing -- a key the
    # supervisor believes in and the rig never saw.
    radio = await _connected()
    runtime = radio._managed_tx_runtime
    api = ManagedTxApi.bind(radio, _OWNER)
    assert api is not None
    bound_before = radio._managed_tx_bound_port

    await radio.soft_disconnect()

    assert radio.managed_tx is runtime  # parked, never unpublished
    transition = await api.set_ptt(True)
    assert transition.outcome is TxOutcome.NOT_READY
    assert transition.snapshot.lease_id is None
    assert "ptt(on)" not in radio.provider.log

    # What ``soft_reconnect`` then does: a rebuilt CI-V port on a fresh
    # generation, and the re-arm it drives before any consumer's recovery (that
    # call's ordering is pinned in ``test_web_recovery_durable_off``).
    radio._civ_transport = _CivPort()  # type: ignore[assignment]
    radio._advance_civ_generation("soft_reconnect")
    await radio.rearm_managed_tx()

    assert radio._managed_tx_bound_port != bound_before  # the binding moved
    assert (await api.set_ptt(True)).outcome is TxOutcome.ACCEPTED
    assert radio.provider.log.count("ptt(on)") == 1

    await api.set_ptt(False)


async def test_disconnect_unpublishes_and_a_new_connect_arms_a_new_runtime() -> None:
    # The MOR-1193 invariant is bounded by the session, not by the process:
    # "managed-eligible means never ``None``" holds from connect to disconnect,
    # and past disconnect there is no session to supervise. Keeping the runtime
    # would advertise a supervisor over a closed port -- and the next connect()
    # would arm a rig through a provider bound to the previous one.
    radio = await _connected()
    first = radio.managed_tx
    assert first is not None

    await radio.disconnect()

    assert radio.managed_tx is None
    assert radio.tx_snapshot is None
    assert radio._managed_tx_bound_port is None
    assert radio._managed_tx_armed_epoch is None

    await radio.connect()

    second = radio.managed_tx
    assert second is not None
    assert second is not first  # identity: a new session gets a new supervisor
    snapshot = radio.tx_snapshot
    assert snapshot is not None
    assert (snapshot.provider_ready, snapshot.radio_tx) == (True, RadioTx.OFF)


async def test_re_arming_a_disconnected_radio_does_nothing_at_all() -> None:
    # A reconnect callback that fires after teardown, or a consumer holding the
    # radio past its session. Re-arm must not resurrect managed TX here: the
    # arming path builds a runtime when it finds none, so an unguarded re-arm
    # would publish a supervisor whose port was captured from a closed session
    # -- live-but-unusable, and worse than the honest ``None``.
    radio = await _connected()
    await radio.disconnect()
    log_before = list(radio.provider.log)

    await radio.rearm_managed_tx()

    assert radio.managed_tx is None
    assert radio._managed_tx_bound_port is None
    assert radio.provider.log == log_before  # no capture, no seed
    assert len(radio.probes) == 1  # and no second arming probe


async def test_a_facade_bound_before_disconnect_refuses_keys_after_it() -> None:
    # The other half of the same question, asked from the ingress side: a
    # binding taken while the radio was managed outlives the session it was
    # taken in. It must fail closed -- refused, with nothing on the wire --
    # rather than key a rig through a supervisor whose port is gone.
    radio = await _connected()
    api = ManagedTxApi.bind(radio, _OWNER)
    assert api is not None

    await radio.disconnect()
    transition = await api.set_ptt(True)

    assert transition.outcome is not TxOutcome.ACCEPTED
    assert transition.snapshot.lease_id is None
    assert "ptt(on)" not in radio.provider.log


async def test_disconnecting_an_unmanaged_radio_touches_no_managed_state() -> None:
    # The unmanaged path is unchanged: a radio that never connected has no
    # runtime, so teardown does exactly what it did before MOR-1016 -- no
    # shutdown, no wait, no wire traffic.
    radio = _AssembledRadio(_Provider([]))
    _LIVE.append(radio)
    assert radio.managed_tx is None

    await radio.disconnect()

    assert radio.managed_tx is None
    assert radio._managed_tx_bound_port is None
    assert radio.provider.log == []
    assert radio._session_lifecycle.closes == 0  # type: ignore[attr-defined]


class _RegistryRadio(_AssembledRadio):
    """An assembled radio whose port capture/retirement is the *real* one.

    ``_AssembledRadio`` answers ``_capture_managed_tx_port`` with a bare
    ``True``, which is right for the arming cases it pins and blind to the one
    thing PR4 changed underneath it: ``CivRuntime`` keeps a registry of managed
    ports **keyed by provider generation**, and a runtime's generation counter
    starts at 1. Clearing the runtime at disconnect means the next session's
    first generation is 1 again -- straight into whatever session 1 left in
    that registry. Only the real hooks can show it, so these three are put back
    (the write and the authoritative read stay on the provider double: they are
    the CI-V wire, which ``tests/test_civ_rx_coverage.py`` owns).
    """

    _capture_managed_tx_port = CoreRadio._capture_managed_tx_port
    _retire_managed_tx_port = CoreRadio._retire_managed_tx_port
    _unbind_authoritative_ptt_observer = CoreRadio._unbind_authoritative_ptt_observer


async def _connected_over_the_real_registry() -> _RegistryRadio:
    radio = _RegistryRadio(_Provider([]))
    _LIVE.append(radio)
    await radio.connect()
    return radio


async def test_a_second_session_arms_over_the_real_civ_port_registry() -> None:
    # The regression that clearing the runtime at disconnect introduces if the
    # CI-V registry outlives it: session 2's runtime presents generation 1,
    # session 1's entry for generation 1 is still there, ``capture_managed_port``
    # raises "already captured", ``_run_managed_tx_arm`` swallows it as an
    # arming failure -- and the radio is provider-not-ready for the rest of its
    # life. Production-reachable with one radio object and a disconnect/connect
    # cycle (the Web API exposes exactly that pair).
    radio = await _connected_over_the_real_registry()
    first = radio.managed_tx
    assert first is not None
    assert radio.tx_snapshot is not None and radio.tx_snapshot.provider_ready

    await radio.disconnect()
    await radio.connect()

    second = radio.managed_tx
    assert second is not None
    assert second is not first
    snapshot = radio.tx_snapshot
    assert snapshot is not None
    assert (snapshot.provider_ready, snapshot.radio_tx) == (True, RadioTx.OFF)

    # Armed is not the claim; keyable is. A second session that cannot key is
    # the same outage as one that never armed.
    api = ManagedTxApi.bind(radio, _OWNER)
    assert api is not None
    assert (await api.set_ptt(True)).outcome is TxOutcome.ACCEPTED

    await api.set_ptt(False)


async def test_a_finished_session_leaves_no_managed_tx_generations_behind() -> None:
    # The contract underneath the case above. Four CI-V structures are keyed by
    # provider generation, and that key is a per-runtime counter that restarts
    # at 1 -- so all four have to end with the session that generated them, not
    # just the port token: the terminal marker ``bind_ptt_observer`` refuses on
    # would fail session 2's bind, and a completed retirement barrier would be
    # read as session 2's transport already closing and skip that close.
    #
    # Within a session these marks stay load-bearing (a port whose close failed
    # is deliberately left registered so the retirement can be retried, which
    # ``test_radio.py::TestPtt::test_managed_ptt_port_token_safety`` pins) --
    # teardown is the only point where the counter restarts.
    radio = await _connected_over_the_real_registry()
    civ = radio._civ_runtime
    assert list(civ._managed_tx_ports) == [1]

    await radio.disconnect()

    assert civ._managed_tx_ports == {}
    assert civ._poisoned_managed_tx_generations == set()
    assert civ._managed_tx_sends == {}
    assert civ._managed_tx_retirements == {}


async def test_an_unmanaged_radio_disconnects_through_the_real_lifecycle() -> None:
    # And the same over the production wiring rather than the double: the
    # real ``CoreRadioSessionLifecycle`` still treats teardown on an unclaimed
    # radio as the idempotent no-op it always was.
    radio = _unconnected_radio()

    await radio.disconnect()

    assert radio.managed_tx is None
    assert not radio.connected
