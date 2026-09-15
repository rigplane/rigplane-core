"""Forward-extension test for the :class:`RigctldRoutable` capability.

The load-bearing assertion of epic #1322: the architecture admits new
radio backends purely by **structural conformance** to the public
Capability Protocols — no upper-layer (web/rigctld) code change needed.

If a new backend implements ``rigctld_routing(max_power_w)``
returning an object that satisfies
:class:`~rigplane.rigctld.routing.RigctldRouting`, the rigctld handler's
``isinstance(radio, RigctldRoutable)`` check picks it up without any
registry, plugin mechanism, or string discriminator.
"""

from __future__ import annotations

from rigplane import RigctldRoutable
from rigplane.rigctld.contract import RigctldResponse
from rigplane.rigctld.routing import RigctldRouting


class _StubRouting:
    """Structural stub that satisfies :class:`RigctldRouting`.

    Accepts the optional ``vfo`` kwarg added in #1345 for per-VFO routing
    under ``vfo_opt``. The stub ignores ``vfo`` — it only exists to verify
    structural conformance to the extended Protocol surface.
    """

    async def get_level(self, level: str, *, vfo: str | None = None) -> RigctldResponse:
        return RigctldResponse(values=["0"])

    async def set_level(
        self, level: str, value: float, *, vfo: str | None = None
    ) -> RigctldResponse:
        return RigctldResponse(values=["RPRT 0"])

    async def get_func(self, func: str, *, vfo: str | None = None) -> RigctldResponse:
        return RigctldResponse(values=["0"])

    async def set_func(
        self, func: str, on: bool, *, vfo: str | None = None
    ) -> RigctldResponse:
        return RigctldResponse(values=["RPRT 0"])

    def dump_state(self) -> list[str]:
        return []

    def get_info(self) -> str:
        return "Stub Rig"


class _StubRoutableRadio:
    """Structural stub that satisfies :class:`RigctldRoutable`."""

    def rigctld_routing(
        self,
        max_power_w: float = 100.0,
    ) -> _StubRouting:
        return _StubRouting()


def test_stub_routable_satisfies_protocol() -> None:
    """A new radio backend gets custom rigctld routing purely by
    structural conformance — no upper-layer code change needed."""
    stub = _StubRoutableRadio()
    assert isinstance(stub, RigctldRoutable)

    routing = stub.rigctld_routing()
    assert isinstance(routing, RigctldRouting)


def test_stub_routing_satisfies_protocol() -> None:
    """``RigctldRouting`` is satisfied by any object exposing the
    six required get/set level + get/set func + dump_state + get_info
    methods."""
    routing = _StubRouting()
    assert isinstance(routing, RigctldRouting)


def test_yaesu_cat_radio_satisfies_rigctld_routable() -> None:
    """The shipping Yaesu CAT backend already conforms to the public
    :class:`RigctldRoutable` Protocol (no inheritance required)."""
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

    assert issubclass(YaesuCatRadio, RigctldRoutable)
