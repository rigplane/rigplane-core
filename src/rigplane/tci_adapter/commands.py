"""TCI command and mode tables for the adapter (MOR-3821 C1, spec 5.5/5.6).

Radio-agnostic vocabulary lives here; which names a radio supports is read
from the profile, never branched on. Which TCI names the server honours is
the ``TIER_AB_COMMANDS`` set below: anything else is ignored without a
reply (spec 6.1), so no spec inventory beyond the honoured set is needed.
"""

from __future__ import annotations

from ..core.types import Mode

__all__ = [
    "CANONICAL_TCI_MODE_ORDER",
    "CORE_TO_TCI_MODE",
    "SPOT_COMMANDS",
    "START_STOP_COMMANDS",
    "TCI_CHANNELS_COUNT",
    "TCI_DEFAULT_HOST",
    "TCI_DEFAULT_PORT",
    "TCI_PROTOCOL_NAME",
    "TCI_PROTOCOL_VERSION",
    "TCI_TRX_COUNT",
    "TCI_TO_CORE_MODE",
    "TIER_AB_COMMANDS",
    "UNMAPPED_CORE_MODES",
    "modulations_list",
]

#: Announced program name. Open (design Q4): the coordinator may change it
#: once the C0 capture shows what clients need.
TCI_PROTOCOL_NAME = "ExpertSDR3"
#: Announced protocol version: the 2.1 spec this server implements.
TCI_PROTOCOL_VERSION = "2.1"
#: Default bind address (design 9: loopback; non-loopback logs a WARNING).
TCI_DEFAULT_HOST = "127.0.0.1"
#: Default port (design 9: AetherSDR's TCI default).
TCI_DEFAULT_PORT = 50001
#: Receiver count (design Q5: single-receiver radios only in release 1).
TCI_TRX_COUNT = 1
#: Channels per receiver, always (design 5.3: 0 = A, 1 = B).
TCI_CHANNELS_COUNT = 2

#: TCI name -> (Core mode, data-mode flag), both directions (spec 5.6).
TCI_TO_CORE_MODE: dict[str, tuple[Mode, bool]] = {
    "AM": (Mode.AM, False),
    "LSB": (Mode.LSB, False),
    "USB": (Mode.USB, False),
    "CW": (Mode.CW, False),
    "FM": (Mode.FM, False),
    "WFM": (Mode.WFM, False),
    "DIGL": (Mode.LSB, True),
    "DIGU": (Mode.USB, True),
}

#: Reverse of :data:`TCI_TO_CORE_MODE`.
CORE_TO_TCI_MODE: dict[tuple[Mode, bool], str] = {
    (mode, data): name for name, (mode, data) in TCI_TO_CORE_MODE.items()
}

#: Core modes with no TCI name (spec 5.6): never announced, never accepted.
#: ``Mode`` has no NFM member (narrow FM is a filter width, not a mode).
UNMAPPED_CORE_MODES = frozenset(
    {Mode.RTTY, Mode.RTTY_R, Mode.CW_R, Mode.PSK, Mode.PSK_R, Mode.DV}
)

#: Emission order of ``modulations_list`` (the order eesdr-tci observes).
CANONICAL_TCI_MODE_ORDER = ("AM", "LSB", "USB", "CW", "FM", "WFM", "DIGL", "DIGU")

#: TCI commands this server honours (spec 5.5 Tier A/B as built by C1+C2).
#: Init-only names answer reads; ``spot*`` are accepted and dropped (C2);
#: ``start``/``stop`` are echoed (C2); everything else is Tier C or unknown
#: and is ignored without a reply (spec 6.1).
TIER_AB_COMMANDS = frozenset(
    {
        "vfo",
        "modulation",
        "split_enable",
        "trx",
        "drive",
        "start",
        "stop",
        "rit_enable",
        "rit_offset",
        "xit_enable",
        "xit_offset",
        "tx_enable",
        "tx_frequency",
        "dds",
        "if",
        "vfo_limits",
        "modulations_list",
        "trx_count",
        "channels_count",
        "device",
        "protocol",
        "receive_only",
        "cw_keyer_speed",
        "cw_macros_speed",
        "spot",
        "spot_delete",
        "spot_clear",
        "tune",
        "tune_drive",
    }
)

#: Spots are accepted and dropped: no panorama exists (spec 5.5, C2).
SPOT_COMMANDS = frozenset({"spot", "spot_delete", "spot_clear"})

#: ``start``/``stop`` have no Core equivalent and are echoed (spec 5.5, C2).
START_STOP_COMMANDS = frozenset({"start", "stop"})


def modulations_list(
    profile_modes: tuple[str, ...] | list[str], *, data_capable: bool
) -> tuple[str, ...]:
    """Intersect TCI names with the modes the profile supports (spec 5.4).

    ``DIGL``/``DIGU`` need the matching sideband in the profile *and* a
    radio that implements the DATA-mode command.
    """
    supported = {str(mode).upper() for mode in profile_modes}
    names = []
    for name in CANONICAL_TCI_MODE_ORDER:
        if name == "DIGL":
            if data_capable and "LSB" in supported:
                names.append(name)
        elif name == "DIGU":
            if data_capable and "USB" in supported:
                names.append(name)
        elif name in supported:
            names.append(name)
    return tuple(names)
