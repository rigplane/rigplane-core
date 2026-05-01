"""Forward-extension test for ``PowerControlCapable.native_power_unit``.

The load-bearing assertion of epic #1322 (Capability 3/4): the
architecture admits new radio backends purely by **structural
conformance** to the public Capability Protocols — no upper-layer
(web, rigctld) code change needed to dispatch on the wire-level
power unit.

A backend declares ``native_power_unit: Literal["raw_255", "watts"]``
as a class attribute (or returns it from a ``@property``) and the
control handler picks up the right unit tag for ``SetPower`` without
ever looking at ``backend_id`` strings.
"""

from __future__ import annotations

from typing import Literal

from icom_lan import PowerControlCapable


class _RawIcomLikeRadio:
    """Structural stub mimicking an Icom CI-V radio: raw 0-255 scale."""

    native_power_unit: Literal["raw_255", "watts"] = "raw_255"

    async def get_powerstat(self) -> bool:
        return True

    async def set_powerstat(self, on: bool) -> None:
        return None

    async def get_rf_power(self) -> int:
        return 128

    async def set_rf_power(self, level: int) -> None:
        return None


class _WattsYaesuLikeRadio:
    """Structural stub mimicking a Yaesu CAT radio: watts."""

    native_power_unit: Literal["raw_255", "watts"] = "watts"

    async def get_powerstat(self) -> bool:
        return True

    async def set_powerstat(self, on: bool) -> None:
        return None

    async def get_rf_power(self) -> int:
        return 50

    async def set_rf_power(self, level: int) -> None:
        return None


def test_raw_stub_satisfies_protocol() -> None:
    """A class attribute with the right Literal value satisfies the
    ``@property``-shaped Protocol member under
    :func:`runtime_checkable`."""
    stub = _RawIcomLikeRadio()
    assert isinstance(stub, PowerControlCapable)
    assert stub.native_power_unit == "raw_255"


def test_watts_stub_satisfies_protocol() -> None:
    """The same Protocol admits a ``"watts"`` declaration — no
    inheritance, no registry, no string discriminator."""
    stub = _WattsYaesuLikeRadio()
    assert isinstance(stub, PowerControlCapable)
    assert stub.native_power_unit == "watts"


def test_icom_core_radio_declares_raw_255() -> None:
    """The shipping Icom :class:`CoreRadio` declares ``"raw_255"``."""
    from icom_lan.runtime.radio import CoreRadio

    assert CoreRadio.native_power_unit == "raw_255"


def test_yaesu_cat_radio_declares_watts() -> None:
    """The shipping :class:`YaesuCatRadio` declares ``"watts"``."""
    from icom_lan.backends.yaesu_cat.radio import YaesuCatRadio

    assert YaesuCatRadio.native_power_unit == "watts"
