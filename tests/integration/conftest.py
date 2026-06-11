"""Integration tests configuration and fixtures.

Tests require a real Icom radio on the network.
Set environment variables to enable:

    export ICOM_HOST=192.168.55.40
    export ICOM_USER=your_username
    export ICOM_PASS=your_password

Serial-backend integration tests use:

    export ICOM_SERIAL_DEVICE=/dev/ttyUSB0
    export ICOM_SERIAL_BAUDRATE=115200
    export ICOM_SERIAL_RADIO_ADDR=0x98
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator

import pytest

# Add src to path for imports
import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from rigplane import IcomRadio  # noqa: E402


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw, 0)


# Environment variables
ICOM_HOST = os.environ.get("ICOM_HOST", "")
ICOM_USER = os.environ.get("ICOM_USER", "")
ICOM_PASS = os.environ.get("ICOM_PASS", "")
ICOM_RADIO_ADDR = _env_int("ICOM_RADIO_ADDR", 0x98)  # IC-7610 default
ICOM_SERIAL_DEVICE = os.environ.get("ICOM_SERIAL_DEVICE", "")
ICOM_SERIAL_BAUDRATE = _env_int("ICOM_SERIAL_BAUDRATE", 115200)
ICOM_SERIAL_RADIO_ADDR = _env_int("ICOM_SERIAL_RADIO_ADDR", ICOM_RADIO_ADDR)


def has_radio_config() -> bool:
    """Check if radio connection is configured."""
    return bool(ICOM_HOST and ICOM_USER and ICOM_PASS)


def has_serial_radio_config() -> bool:
    """Check if serial integration backend is configured."""
    return bool(ICOM_SERIAL_DEVICE)


async def _connect_with_retries(radio: IcomRadio, attempts: int = 7) -> None:
    """Connect with firmware-aware cooldown retries for real hardware."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await radio.connect()
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            text = str(exc).lower()
            if (
                "status error=0xffffffff" in text
                or "rejected session allocation" in text
            ):
                # IC-7610 can hold old session slot for tens of seconds.
                pause = min(12 + attempt * 4, 40)
            else:
                pause = min(attempt * 2, 12)
            await asyncio.sleep(float(pause))
    assert last_exc is not None
    raise last_exc


@pytest.fixture
def radio_config() -> dict:
    """Radio connection configuration."""
    return {
        "host": ICOM_HOST,
        "username": ICOM_USER,
        "password": ICOM_PASS,
        "radio_addr": ICOM_RADIO_ADDR,
    }


@pytest.fixture
def serial_radio_config() -> dict:
    """Serial backend connection configuration for integration tests."""
    return {
        "device": ICOM_SERIAL_DEVICE,
        "baudrate": ICOM_SERIAL_BAUDRATE,
        "radio_addr": ICOM_SERIAL_RADIO_ADDR,
    }


@pytest.fixture
async def radio(radio_config: dict) -> AsyncGenerator[IcomRadio, None]:
    """Connected radio instance for integration tests.

    Automatically connects before test and disconnects after.
    Skips test if radio is not configured.
    """
    if not has_radio_config():
        pytest.skip("Radio not configured (set ICOM_HOST, ICOM_USER, ICOM_PASS)")

    r = IcomRadio(**radio_config)
    await _connect_with_retries(r)
    assert r.connected, "Failed to connect to radio"

    # Wait for status packet with audio port mapping
    await asyncio.sleep(0.5)

    # Per-test baseline (best effort).
    baseline_freq = None
    baseline_mode = None
    baseline_filter = None
    baseline_power = None
    baseline_digisel = None
    try:
        baseline_freq = await r.get_frequency()
        baseline_mode, baseline_filter = await r.get_mode_info()
        baseline_power = await r.get_power()
        baseline_digisel = await r.get_digisel()
    except Exception:
        # Don't fail fixture on baseline read issues.
        pass

    try:
        yield r
    finally:
        # Guardrails: restore safe/common state after each test.
        try:
            await r.set_split(False)
        except Exception:
            pass
        try:
            await r.select_receiver("MAIN")
        except Exception:
            pass

        # Restore baseline values if we captured them.
        if baseline_power is not None:
            try:
                await r.set_power(baseline_power)
            except Exception:
                pass
        if baseline_mode is not None:
            try:
                await r.set_mode(baseline_mode, filter_width=baseline_filter)
            except Exception:
                pass
        if baseline_digisel is not None:
            try:
                await r.set_digisel(baseline_digisel)
            except Exception:
                pass
        if baseline_freq is not None:
            try:
                await r.set_frequency(baseline_freq)
            except Exception:
                pass

        await r.disconnect()


# ---------------------------------------------------------------------------
# Owned-profile enumeration (MOR-647 mock validation matrix)
# ---------------------------------------------------------------------------

_RIGS_DIR = Path(__file__).resolve().parent.parent.parent / "rigs"


def owned_profile_models() -> list[str]:
    """Model names of every radio profile shipped in ``rigs/``.

    These are the "owned" profiles for the validation matrix: the native
    dry-run template generator (``build_template_from_capabilities``) only
    needs ``profile.capabilities``, so it can build a full matrix for any
    profile the rig loader discovers. Enumerated dynamically — a new rig
    TOML automatically joins the mock integration matrix.
    """
    from rigplane.profiles.rig_loader import discover_rigs

    return sorted(discover_rigs(_RIGS_DIR).keys())


@pytest.fixture(scope="session")
def all_owned_profile_models() -> list[str]:
    """Session-scoped list of all owned profile model names."""
    return owned_profile_models()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize any test requesting ``owned_profile_model`` per owned rig."""
    if "owned_profile_model" in metafunc.fixturenames:
        metafunc.parametrize("owned_profile_model", owned_profile_models())


# Pytest configuration
def pytest_configure(config: pytest.Config) -> None:
    """Register integration marker."""
    config.addinivalue_line(
        "markers",
        "integration: tests requiring real radio hardware (skip if not configured)",
    )
    config.addinivalue_line(
        "markers",
        "serial_integration: tests requiring serial backend hardware configuration",
    )
    config.addinivalue_line(
        "markers",
        "ic7610_parity: maintained IC-7610 parity smoke profile across high-risk families",
    )
    config.addinivalue_line(
        "markers",
        "mock_integration: integration tests using MockIcomRadio (no real hardware required)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip integration tests if required hardware profile is not configured."""
    _ = config
    missing_lan = not has_radio_config()
    missing_serial = not has_serial_radio_config()
    skip_lan = pytest.mark.skip(
        reason="Radio not configured (set ICOM_HOST, ICOM_USER, ICOM_PASS)"
    )
    skip_serial = pytest.mark.skip(
        reason=(
            "Serial radio not configured "
            "(set ICOM_SERIAL_DEVICE, optional ICOM_SERIAL_BAUDRATE/ICOM_SERIAL_RADIO_ADDR)"
        )
    )
    for item in items:
        if "integration" not in item.keywords:
            continue
        # mock_integration: uses MockIcomRadio — no real hardware needed
        if "mock_integration" in item.keywords:
            continue
        if "serial_integration" in item.keywords:
            if missing_serial:
                item.add_marker(skip_serial)
            continue
        if missing_lan:
            item.add_marker(skip_lan)
