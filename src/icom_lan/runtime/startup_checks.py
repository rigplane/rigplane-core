from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from icom_lan.radio_protocol import Radio

__all__ = ["assert_radio_startup_ready", "wait_for_radio_startup_ready"]


def _radio_ready(radio: "Radio | None") -> bool:
    if radio is None:
        return False
    ready: Any = getattr(radio, "radio_ready", None)
    if isinstance(ready, bool):
        return ready
    connected: Any = getattr(radio, "connected", False)
    return connected if isinstance(connected, bool) else False


def _radio_details(radio: "Radio | None") -> tuple[bool, bool, bool | None]:
    if radio is None:
        return False, False, None
    raw_connected: Any = getattr(radio, "connected", False)
    connected = raw_connected if isinstance(raw_connected, bool) else False
    ready = _radio_ready(radio)
    raw_control: Any = getattr(radio, "control_connected", None)
    control_connected = raw_control if isinstance(raw_control, bool) else None
    return connected, ready, control_connected


def _format_error(
    component: str,
    *,
    connected: bool,
    ready: bool,
    control_connected: bool | None,
    timeout: float | None = None,
) -> str:
    details = [f"connected={connected}", f"radio_ready={ready}"]
    if control_connected is not None:
        details.append(f"control_connected={control_connected}")
    if timeout is None:
        return (
            f"{component} aborted: radio is not ready "
            f"({', '.join(details)}). Refusing to start a half-working server."
        )
    return (
        f"{component} aborted: radio is not fully ready after "
        f"{timeout:.1f}s ({', '.join(details)}). Refusing to start a half-working server."
    )


def assert_radio_startup_ready(
    radio: "Radio | None",
    *,
    component: str = "startup",
) -> None:
    """Instantly assert that a radio is ready for server/library use.

    ``radio is None`` is treated as explicit offline/test mode and is allowed.
    """
    if radio is None:
        return
    connected, ready, control_connected = _radio_details(radio)
    if connected and ready and control_connected is not False:
        return
    raise RuntimeError(
        _format_error(
            component,
            connected=connected,
            ready=ready,
            control_connected=control_connected,
        )
    )


async def wait_for_radio_startup_ready(
    radio: "Radio | None",
    *,
    timeout: float = 5.0,
    component: str = "startup",
) -> None:
    """Wait until a radio is fully ready for connect/startup flows."""
    if radio is None:
        return

    timeout = max(float(timeout), 0.0)
    deadline = time.monotonic() + timeout

    while True:
        connected, ready, control_connected = _radio_details(radio)
        if connected and ready and control_connected is not False:
            return
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.1)

    raise RuntimeError(
        _format_error(
            component,
            connected=connected,
            ready=ready,
            control_connected=control_connected,
            timeout=timeout,
        )
    )
