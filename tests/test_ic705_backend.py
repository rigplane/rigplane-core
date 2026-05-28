"""Tests for IC-705 backend initialization and profile routing."""

import pytest

from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.backends.icom7610.drivers.serial_stub import SerialMockRadio
from rigplane.backends.icom7610.serial import Icom7610SerialRadio
from rigplane.exceptions import CommandError


def test_ic705_factory_creates_correct_backend():
    """Factory should route IC-705 serial config to Ic705SerialRadio."""
    config = SerialBackendConfig(
        device="/dev/ttyUSB0",
        model="IC-705",
    )
    radio = create_radio(config)
    assert isinstance(radio, Ic705SerialRadio)
    assert radio.model == "IC-705"


def test_ic7610_factory_creates_correct_backend():
    """Factory should route IC-7610 serial config to Icom7610SerialRadio."""
    config = SerialBackendConfig(
        device="/dev/ttyUSB0",
        model="IC-7610",
    )
    radio = create_radio(config)
    assert isinstance(radio, Icom7610SerialRadio)
    assert not isinstance(radio, Ic705SerialRadio)


def test_x6200_factory_routes_to_ic705_class_without_warning(caplog):
    """Regression for MOR-170: ``--model X6200`` must route to the IC-705
    serial transport class (X6200 shares the IC-705 CI-V personality —
    Hamlib ``x6100_priv_caps`` is reused by ``x6200_caps``) and MUST NOT
    log the "Unknown model … defaulting to IC-7610" fallback warning.

    Behaviour separation from IC-705 happens via the loaded
    ``rigs/x6200.toml`` profile, not via a distinct serial class.
    """
    config = SerialBackendConfig(
        device="/dev/ttyUSB0",
        model="X6200",
    )
    with caplog.at_level("WARNING", logger="rigplane.backends.factory"):
        radio = create_radio(config)
    assert isinstance(radio, Ic705SerialRadio)
    assert radio.model == "X6200"
    # No silent fallback to IC-7610.
    assert not any(
        "defaulting to IC-7610" in rec.getMessage() for rec in caplog.records
    )
    assert not any("Unknown model" in rec.getMessage() for rec in caplog.records)


def test_ic705_backend_default_model():
    """IC-705 backend should default model to IC-705."""
    radio = Ic705SerialRadio(device="/dev/ttyUSB0")
    assert radio.model == "IC-705"


def test_ic705_profile_loading():
    """IC-705 backend should load ic705.toml profile."""
    radio = Ic705SerialRadio(device="/dev/ttyUSB0", model="IC-705")
    profile = radio._profile
    assert profile.model == "IC-705"
    assert profile.civ_addr == 0xA4  # IC-705 CI-V address


def test_ic705_serial_inherits_from_core():
    """Ic705SerialRadio should inherit from CoreRadio."""
    radio = Ic705SerialRadio(device="/dev/ttyUSB0")
    # Verify it has the expected methods and properties
    assert hasattr(radio, "connect")
    assert hasattr(radio, "disconnect")
    assert hasattr(radio, "get_frequency")
    assert hasattr(radio, "set_frequency")
    assert hasattr(radio, "enable_scope")
    assert hasattr(radio, "disable_scope")


# Contract tests using SerialMockRadio with IC-705 profile


def test_ic705_profile_single_receiver():
    """IC-705 profile should specify single receiver only."""
    radio = SerialMockRadio(model="IC-705")
    assert radio.profile.receiver_count == 1
    assert radio.profile.model == "IC-705"


def test_ic705_profile_no_command_29():
    """IC-705 profile should not support Command 29 (sub-receiver command)."""
    radio = SerialMockRadio(model="IC-705")
    assert "command_29" not in radio.capabilities


def test_ic705_profile_civ_address():
    """IC-705 profile should use CI-V address 0xA4."""
    radio = SerialMockRadio(model="IC-705")
    assert radio.profile.civ_addr == 0xA4


@pytest.mark.asyncio
async def test_ic705_contract_single_receiver_operation():
    """IC-705 should support receiver=0 operations but reject receiver=1."""
    radio = SerialMockRadio(model="IC-705")
    await radio.connect()

    # Receiver 0 should work
    await radio.set_freq(7_074_000, receiver=0)
    freq = await radio.get_freq(receiver=0)
    assert freq == 7_074_000

    # Receiver 1 should fail (IC-705 has only one receiver)
    with pytest.raises(CommandError, match="does not support receiver=1"):
        await radio.get_freq(receiver=1)

    with pytest.raises(CommandError, match="does not support receiver=1"):
        await radio.set_freq(14_074_000, receiver=1)


@pytest.mark.asyncio
async def test_ic705_contract_frequency_operations():
    """IC-705 should support frequency get/set operations."""
    radio = SerialMockRadio(model="IC-705")
    await radio.connect()

    # Set and get frequency
    await radio.set_freq(14_074_000)
    assert await radio.get_freq() == 14_074_000

    # Test HF range
    await radio.set_freq(7_100_000)
    assert await radio.get_freq() == 7_100_000

    # Test VHF range (IC-705 supports 144 MHz)
    await radio.set_freq(144_174_000)
    assert await radio.get_freq() == 144_174_000


@pytest.mark.asyncio
async def test_ic705_contract_mode_operations():
    """IC-705 should support mode get/set operations."""
    radio = SerialMockRadio(model="IC-705")
    await radio.connect()

    # Test USB mode
    await radio.set_mode("USB", filter_width=2)
    mode, filt = await radio.get_mode()
    assert mode == "USB"
    assert filt == 2

    # Test LSB mode
    await radio.set_mode("LSB", filter_width=1)
    mode, filt = await radio.get_mode()
    assert mode == "LSB"
    assert filt == 1

    # Test FM mode (IC-705 supports FM)
    await radio.set_mode("FM")
    mode, _ = await radio.get_mode()
    assert mode == "FM"


@pytest.mark.asyncio
async def test_ic705_contract_scope_operations():
    """IC-705 should support scope enable/disable operations."""
    radio = SerialMockRadio(model="IC-705")
    await radio.connect()

    # Enable scope
    await radio.enable_scope()
    assert radio._scope_enabled is True

    # Disable scope
    await radio.disable_scope()
    assert radio._scope_enabled is False


@pytest.mark.asyncio
async def test_ic705_contract_connection_lifecycle():
    """IC-705 should support connect/disconnect lifecycle."""
    radio = SerialMockRadio(model="IC-705")

    # Initially disconnected
    assert radio.connected is False
    assert radio.radio_ready is False

    # Connect
    await radio.connect()
    assert radio.connected is True
    assert radio.radio_ready is True

    # Disconnect
    await radio.disconnect()
    assert radio.connected is False
    assert radio.radio_ready is False
