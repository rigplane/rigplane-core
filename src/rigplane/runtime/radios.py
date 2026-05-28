"""Radio model presets with CI-V addresses and capabilities.

Reference: wfview rigs/*.rig files and rigidentities.h.
"""

import logging
from dataclasses import dataclass

__all__ = [
    "RadioModel",
    "RADIOS",
    "SERIAL_RADIO_MAP",
    "CIV_PROFILE_MAP",
    "identify_radio",
    "IC_7610_ADDR",
]

logger = logging.getLogger(__name__)

# Convenience constant for IC-7610 CI-V address (used extensively in tests)
IC_7610_ADDR = 0x98


@dataclass(frozen=True, slots=True)
class RadioModel:
    """Radio model preset.

    Attributes:
        name: Human-readable model name.
        civ_addr: Default CI-V address.
        receivers: Number of independent receivers.
        has_lan: Whether the radio supports LAN control.
        has_wifi: Whether the radio has built-in WiFi.
    """

    name: str
    civ_addr: int
    receivers: int = 1
    has_lan: bool = True
    has_wifi: bool = False


#: Known Icom radio models with LAN/WiFi support.
RADIOS: dict[str, RadioModel] = {
    "IC-7610": RadioModel(
        name="IC-7610",
        civ_addr=0x98,
        receivers=2,
    ),
    "IC-7300": RadioModel(
        name="IC-7300",
        civ_addr=0x94,
    ),
    "IC-705": RadioModel(
        name="IC-705",
        civ_addr=0xA4,
        has_wifi=True,
    ),
    "IC-9700": RadioModel(
        name="IC-9700",
        civ_addr=0xA2,
        receivers=2,
    ),
    "IC-R8600": RadioModel(
        name="IC-R8600",
        civ_addr=0x96,
    ),
    "IC-7851": RadioModel(
        name="IC-7851",
        civ_addr=0x8E,
        receivers=2,
    ),
    # Xiegu X6200: shares CI-V address 0xA4 with Icom IC-705 by factory
    # default (Radioddity X6200 CI-V Implementation V1.0.6, page 4; Hamlib
    # rigs/icom/xiegu.c x6100_priv_caps). MOR-170 added a distinct profile
    # (rigs/x6200.toml) and hwid-based discovery disambiguation; this entry
    # makes ``--model X6200`` resolve in the parallel hardcoded registry so
    # the CLI path stops silently falling back to IC-7610. The discovery-
    # time override on address 0xA4 (discovery._resolve_xiegu_x6200_override)
    # remains the source of truth for *auto-detected* radios.
    "X6200": RadioModel(
        name="X6200",
        civ_addr=0xA4,
        has_lan=False,
        has_wifi=True,
    ),
}


#: Mapping from CI-V address to (model name, model ID bytes).
#: Model ID is the 2-byte BCD identifier from the 0x19 0x00 response.
#: Source: wfview rigidentities.h
SERIAL_RADIO_MAP: dict[int, tuple[str, bytes]] = {
    0x98: ("IC-7610", b"\x01\x06"),
    0xA4: ("IC-705", b"\x01\x05"),
    0x94: ("IC-7300", b"\x01\x01"),
    0xA2: ("IC-9700", b"\x01\x20"),
    0x8E: ("IC-7851", b"\x01\x35"),
    0x96: ("IC-R8600", b"\x01\x26"),
}


#: Mapping from CI-V address to rig profile ID (matches ``id`` in ``rigs/*.toml``).
#: Empty string means no matching profile file exists yet.
CIV_PROFILE_MAP: dict[int, str] = {
    0x98: "icom_ic7610",
    0xA4: "icom_ic705",
    0x94: "icom_ic7300",
    0xA2: "icom_ic9700",
    0x8E: "",  # IC-7851 — no profile file yet
    0x96: "",  # IC-R8600 — no profile file yet
}


def identify_radio(address: int, model_id: bytes) -> str:
    """Identify radio from CI-V address and model ID.

    Args:
        address: CI-V address (e.g. 0x98 for IC-7610).
        model_id: 2-byte BCD model ID from 0x19 0x00 response.

    Returns:
        Radio name (e.g. "IC-7610") or "Unknown (0xXX)" if address not found.
    """
    entry = SERIAL_RADIO_MAP.get(address)
    if not entry:
        return f"Unknown (0x{address:02X})"

    expected_name, expected_id = entry

    if model_id != expected_id:
        # Some radios return a shorter model ID via USB serial (e.g. single
        # byte CI-V address echo) vs the full 2-byte BCD ID.  The CI-V
        # address alone is sufficient for identification, so only log at
        # debug level.
        logger.debug(
            "CI-V address 0x%02X maps to %s, model ID %s != expected %s (OK)",
            address,
            expected_name,
            model_id.hex(),
            expected_id.hex(),
        )

    return expected_name


def _normalize_model_name(model: str) -> str:
    """Normalize model names for flexible lookup."""
    return "".join(ch for ch in model.upper() if ch.isalnum())


_RADIOS_BY_NORMALIZED: dict[str, RadioModel] = {
    _normalize_model_name(name): radio for name, radio in RADIOS.items()
}


def get_civ_addr(model: str) -> int:
    """Look up CI-V address by model name.

    Args:
        model: Model name (e.g. "IC-705", case-insensitive).

    Returns:
        CI-V address byte.

    Raises:
        KeyError: If model not found.
    """
    key = _normalize_model_name(model)
    if key in _RADIOS_BY_NORMALIZED:
        return _RADIOS_BY_NORMALIZED[key].civ_addr
    raise KeyError(f"Unknown radio model: {model}. Known: {', '.join(RADIOS)}")
