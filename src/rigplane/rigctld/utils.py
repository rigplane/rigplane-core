from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from ..radio_protocol import ModeInfoCapable

ModeReader = Callable[..., Awaitable[tuple[str, int | None]]]

__all__ = ["get_mode_reader"]


def get_mode_reader(
    radio: object,
    normalizer: Callable[[object], str],
) -> ModeReader | None:
    """Return a mode reader using backend-native info or the core contract.

    The reader prefers :class:`ModeInfoCapable.get_mode_info` when available,
    falling back to a dynamic ``get_mode_info`` attribute and finally to the
    core :meth:`Radio.get_mode` contract. The *normalizer* function is applied
    to the backend-native mode value to produce a string suitable for the
    rigctld layer (hamlib-compatible or generic uppercase name).
    """
    if isinstance(radio, ModeInfoCapable):

        async def _read_mode_info(
            receiver: int = 0,
        ) -> tuple[str, int | None]:
            mode, filt = await radio.get_mode_info(receiver=receiver)
            return normalizer(mode), filt

        return _read_mode_info

    get_mode_info = getattr(radio, "get_mode_info", None)
    if callable(get_mode_info):

        async def _read_dynamic_mode_info(
            receiver: int = 0,
        ) -> tuple[str, int | None]:
            mode, filt = await cast(
                Callable[..., Awaitable[tuple[Any, int | None]]],
                get_mode_info,
            )(receiver=receiver)
            return normalizer(mode), filt

        return _read_dynamic_mode_info

    get_mode = getattr(radio, "get_mode", None)
    if callable(get_mode):

        async def _read_mode(
            receiver: int = 0,
        ) -> tuple[str, int | None]:
            mode, filt = await cast(
                Callable[..., Awaitable[tuple[Any, int | None]]],
                get_mode,
            )(receiver=receiver)
            return normalizer(mode), filt

        return _read_mode

    return None
