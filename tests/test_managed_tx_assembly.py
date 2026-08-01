"""MOR-1016 PR1: the inert ``managed_tx`` member on ``CoreRadio``.

Nothing under ``src`` constructs a :class:`ManagedRadioRuntime` yet -- that is
PR2's job (arming happens in ``connect()``, after ``_civ_transport`` exists).
This PR only gives ``CoreRadio`` the real class member
:class:`~rigplane.core.radio_protocol.ManagedTxCapable` requires, so it must
prove two things and nothing more:

1. the member is a real property -- visible to
   :func:`inspect.getattr_static` without running it, never conjured through
   ``__getattr__`` (the exact trap ``ManagedTxCapable``'s docstring warns
   about); and
2. every consumer of it (``ManagedTxApi.bind``, the Web reconnect hook) still
   sees ``None`` and falls through to the legacy, unsupervised ``set_ptt``
   path -- because a freshly constructed radio armed with nothing keeps
   ``self._managed_tx_runtime`` at its ``None`` default.

No ``MagicMock`` stands in for a protocol-shaped object here (a mock answers
every ``getattr``, so it could never show the unmanaged path staying
unmanaged); every radio below is the real ``CoreRadio``/``IcomRadio``.
"""

from __future__ import annotations

import inspect

from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.tx_safety import TxOwner, TxSource
from rigplane.runtime import radio as radio_module
from rigplane.runtime.radio import CoreRadio, IcomRadio
from rigplane.web.server import WebServer

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
