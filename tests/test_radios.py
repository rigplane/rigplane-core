"""Tests for radio model presets."""

import logging

import pytest

from rigplane.radios import (
    RADIOS,
    SERIAL_RADIO_MAP,
    RadioModel,
    get_civ_addr,
    identify_radio,
)


class TestRadioModels:
    def test_ic7610(self) -> None:
        r = RADIOS["IC-7610"]
        assert r.civ_addr == 0x98
        assert r.receivers == 2
        assert r.has_lan is True

    def test_ic705(self) -> None:
        r = RADIOS["IC-705"]
        assert r.civ_addr == 0xA4
        assert r.has_wifi is True

    def test_ic7300(self) -> None:
        r = RADIOS["IC-7300"]
        assert r.civ_addr == 0x94

    def test_ic9700(self) -> None:
        r = RADIOS["IC-9700"]
        assert r.civ_addr == 0xA2
        assert r.receivers == 2

    def test_icr8600(self) -> None:
        r = RADIOS["IC-R8600"]
        assert r.civ_addr == 0x96

    def test_x6200(self) -> None:
        # Xiegu X6200 shares CI-V address 0xA4 with IC-705; the discovery
        # path disambiguates by USB hwid (see MOR-170). Has WiFi, no LAN.
        r = RADIOS["X6200"]
        assert r.civ_addr == 0xA4
        assert r.has_lan is False
        assert r.has_wifi is True
        assert r.receivers == 1

    def test_lan_capable_radios(self) -> None:
        # All Icom rigs in this registry are LAN-capable. The Xiegu X6200
        # is the first registry entry that is USB-only; documented as the
        # exception so future non-LAN rigs don't trip this assertion.
        lan_capable = set(RADIOS) - {"X6200"}
        for name in lan_capable:
            assert RADIOS[name].has_lan, f"{name} should have LAN"
        assert RADIOS["X6200"].has_lan is False


class TestGetCivAddr:
    def test_known_model(self) -> None:
        assert get_civ_addr("IC-7610") == 0x98
        assert get_civ_addr("IC-705") == 0xA4

    def test_x6200(self) -> None:
        # Regression for MOR-170: the CLI ``--model X6200`` path used to
        # fall through to "Unknown model, defaulting to IC-7610" because
        # X6200 wasn't in the RADIOS registry.
        assert get_civ_addr("X6200") == 0xA4
        assert get_civ_addr("x6200") == 0xA4
        assert get_civ_addr("X-6200") == 0xA4

    def test_case_insensitive(self) -> None:
        assert get_civ_addr("ic-7300") == 0x94

    def test_flexible_normalization(self) -> None:
        assert get_civ_addr("IC 7610") == 0x98
        assert get_civ_addr("IC7610") == 0x98
        assert get_civ_addr("ic_7610") == 0x98

    def test_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown radio"):
            get_civ_addr("IC-FAKE")


class TestIdentifyRadio:
    def test_ic7610(self) -> None:
        assert identify_radio(0x98, b"\x01\x06") == "IC-7610"

    def test_ic705(self) -> None:
        assert identify_radio(0xA4, b"\x01\x05") == "IC-705"

    def test_unknown_address(self) -> None:
        assert identify_radio(0xFF, b"\x00\x00") == "Unknown (0xFF)"

    def test_model_id_mismatch_returns_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="rigplane.runtime.radios"):
            result = identify_radio(0x98, b"\xff\xff")
        assert result == "IC-7610"
        assert "model id" in caplog.text.lower()

    def test_all_map_entries_covered(self) -> None:
        assert len(SERIAL_RADIO_MAP) == 6


class TestRadioModelDataclass:
    def test_frozen(self) -> None:
        r = RadioModel(name="Test", civ_addr=0x01)
        with pytest.raises(AttributeError):
            r.name = "Changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        r = RadioModel(name="Test", civ_addr=0x01)
        assert r.receivers == 1
        assert r.has_lan is True
        assert r.has_wifi is False
