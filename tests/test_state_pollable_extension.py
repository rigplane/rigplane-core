"""Forward-extension test for the :class:`StatePollable` capability.

The load-bearing assertion of epic #1322: the architecture admits new
radio backends purely by **structural conformance** to the public
Capability Protocols — no upper-layer (web/rigctld) code change needed.

If a new backend implements ``create_state_poller(...)`` returning an
object with ``start()``/``stop()`` coroutines, ``isinstance`` checks in
``web/web_startup.py`` recognise it without any registry, plugin
mechanism, or string discriminator.
"""

from __future__ import annotations

from typing import Callable

from icom_lan import StatePollable, StatePoller
from icom_lan.radio_state import RadioState


class _StubStatePoller:
    """Structural stub that satisfies :class:`StatePoller`."""

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _StubStatePollable:
    """Structural stub that satisfies :class:`StatePollable`."""

    def create_state_poller(
        self,
        *,
        callback: Callable[[RadioState], None],
        command_queue: object | None = None,
    ) -> _StubStatePoller:
        return _StubStatePoller()


def test_stub_pollable_satisfies_protocol() -> None:
    """A new radio backend gets state-polling support purely by
    structural conformance — no upper-layer code change needed."""
    stub = _StubStatePollable()
    assert isinstance(stub, StatePollable)

    poller = stub.create_state_poller(callback=lambda _state: None)
    assert isinstance(poller, StatePoller)


def test_stub_poller_satisfies_protocol() -> None:
    """``StatePoller`` is satisfied by any object exposing async
    ``start()`` and ``stop()``."""
    poller = _StubStatePoller()
    assert isinstance(poller, StatePoller)


def test_yaesu_cat_radio_satisfies_state_pollable() -> None:
    """The shipping Yaesu CAT backend already conforms to the public
    :class:`StatePollable` Protocol (no inheritance required)."""
    from icom_lan.backends.yaesu_cat.radio import YaesuCatRadio

    assert issubclass(YaesuCatRadio, StatePollable)
