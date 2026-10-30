"""MOR-3858: the default backend follows the profile's transports.

A CI-V radio whose profile has no LAN interface (IC-7300, X6200) must not
scan the network when no ``--serial-port`` is given; it uses USB discovery.
LAN-capable profiles (IC-7610, IC-705) keep ``lan``. An explicit ``--host``
still means LAN. The facts come from the profile (``has_lan``), never from a
model name.
"""

from __future__ import annotations

import pytest

from rigplane import cli
from rigplane.backends.config import LanBackendConfig, SerialBackendConfig
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.rig_loader import discover_available_rigs


@pytest.fixture(autouse=True)
def _no_ambient_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICOM_HOST", raising=False)


def _scan_serial_finds(
    monkeypatch: pytest.MonkeyPatch, device: str = "/dev/ttyUSB9"
) -> None:
    async def _serial() -> tuple[str, int | None]:
        return device, 115200

    async def _no_lan(**_kwargs: object) -> str:
        pytest.fail("a profile without LAN must not scan the network")

    monkeypatch.setattr(cli, "_auto_discover_serial", _serial)
    monkeypatch.setattr(cli, "_auto_discover_lan", _no_lan)


def test_the_models_used_below_have_the_profile_facts_the_tests_rely_on() -> None:
    rigs = discover_available_rigs(cli._rigs_dir())
    assert [rigs[m].has_lan for m in ("IC-7300", "X6200")] == [False, False]
    assert [rigs[m].has_lan for m in ("IC-7610", "IC-705")] == [True, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300", "X6200"])
async def test_civ_radio_without_lan_defaults_to_serial(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_serial_finds(monkeypatch)
    args = _build_parser().parse_args(["--model", model, "status"])
    config = await _build_backend_config(args)
    assert isinstance(config, SerialBackendConfig)
    assert config.device == "/dev/ttyUSB9"
    assert config.model == model


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7610", "IC-705"])
async def test_civ_radio_with_lan_keeps_lan_without_serial_port(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _lan(**_kwargs: object) -> str:
        return "192.168.1.77"

    async def _no_serial() -> tuple[str, int | None]:
        pytest.fail("a LAN-capable profile must not scan serial ports")

    monkeypatch.setattr(cli, "_auto_discover_lan", _lan)
    monkeypatch.setattr(cli, "_auto_discover_serial", _no_serial)
    args = _build_parser().parse_args(["--model", model, "status"])
    config = await _build_backend_config(args)
    assert isinstance(config, LanBackendConfig)
    assert config.host == "192.168.1.77"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7610", "IC-705"])
async def test_explicit_host_on_a_lan_capable_profile_is_lan(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_serial_finds(monkeypatch)
    args = _build_parser().parse_args(["--model", model, "--host", "1.2.3.4", "status"])
    config = await _build_backend_config(args)
    assert isinstance(config, LanBackendConfig)
    assert config.host == "1.2.3.4"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300", "X6200"])
async def test_explicit_host_still_means_lan_even_without_a_lan_profile(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit --host is the operator's choice and is not second-guessed."""
    _scan_serial_finds(monkeypatch)
    args = _build_parser().parse_args(["--model", model, "--host", "1.2.3.4", "status"])
    config = await _build_backend_config(args)
    assert isinstance(config, LanBackendConfig)


@pytest.mark.asyncio
async def test_serial_radio_with_no_port_found_gives_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _none_found() -> list[object]:
        return []

    async def _no_lan(**_kwargs: object) -> str:
        pytest.fail("a profile without LAN must not scan the network")

    monkeypatch.setattr("rigplane.discovery.discover_serial_radios", _none_found)
    monkeypatch.setattr(cli, "_auto_discover_lan", _no_lan)
    args = _build_parser().parse_args(["--model", "IC-7300", "status"])
    with pytest.raises(SystemExit) as excinfo:
        await _build_backend_config(args)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "no radios found on serial ports" in err
    assert "--serial-port" in err
