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

import inspect
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


class _Session:
    """The session lifecycle reduced to what ``connect()`` observes.

    A real ``CoreRadioSessionLifecycle`` wants a rig on the other end of a
    socket. All ``_arm_managed_tx`` needs from it is the state a completed
    control phase leaves behind: a negotiated CI-V data port and a fresh CI-V
    epoch (the same ``_advance_civ_generation`` the real path calls).
    """

    def __init__(self, radio: CoreRadio) -> None:
        self._radio, self.connects = radio, 0

    async def connect(self) -> None:
        self.connects += 1
        self._radio._civ_port = 50002
        self._radio._civ_transport = object()  # type: ignore[assignment]
        self._radio._advance_civ_generation("test-connect")
        self._radio._connected = True


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
