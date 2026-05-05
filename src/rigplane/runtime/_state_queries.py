"""Shared state query list for populating RadioState.

Used by RadioPoller (periodic polling) and will be used by CoreRadio
(initial state fetch on connection).  Each query is a 3-tuple::

    (cmd_byte, sub_byte | None, receiver | None)

``receiver=None`` means the query is global (not per-receiver).
``receiver=0`` or ``receiver=1`` targets a specific receiver and will
be wrapped in cmd29 when the profile supports it.
"""

from __future__ import annotations

import logging

from icom_lan.profiles import RadioProfile

logger = logging.getLogger(__name__)

# Type alias for a single state query: (cmd, sub, receiver).
StateQuery = tuple[int, int | None, int | None]


def build_state_queries(
    profile: RadioProfile,
    capabilities: set[str],
    *,
    is_serial: bool = False,
) -> list[StateQuery]:
    """Build the full list of CI-V state queries for the given profile.

    Parameters
    ----------
    profile:
        Radio profile (model, cmd29 support, receiver count).
    capabilities:
        Set of capability strings the radio exposes.
    is_serial:
        True for serial backends (adds extra meter queries).

    Returns
    -------
    list[StateQuery]
        Ordered list of ``(cmd_byte, sub_byte, receiver)`` tuples.
    """
    receivers = [0]
    if profile.receiver_count > 1:
        receivers.append(1)

    queries: list[StateQuery] = []

    for receiver in receivers:
        # Freq/mode — needed even on serial for initial state and to pick up
        # filter/attenuator/preamp that don't come via transceive.
        queries.append((0x25, None, receiver))  # frequency
        queries.append((0x26, None, receiver))  # mode

        # Per-receiver state queries.  On dual-receiver radios these use
        # cmd29 wrapping.  On single-receiver radios without cmd29 we send
        # plain CI-V queries (receiver=None).
        _PER_RX_QUERIES: list[tuple[str, int, int | None]] = [
            ("attenuator", 0x11, None),
            ("af_level", 0x14, 0x01),
            ("rf_gain", 0x14, 0x02),
            ("squelch", 0x14, 0x03),
            ("preamp", 0x16, 0x02),
            ("nb", 0x16, 0x22),
            ("nr", 0x16, 0x40),
            ("digisel", 0x16, 0x4E),
            ("ip_plus", 0x16, 0x65),
            ("repeater_tone", 0x16, 0x42),
            ("tsql", 0x16, 0x43),
            ("repeater_tone", 0x1B, 0x00),  # Tone frequency
            ("tsql", 0x1B, 0x01),  # TSQL frequency
            ("nr", 0x14, 0x06),  # NR Level
            ("nb", 0x14, 0x12),  # NB Level
            ("notch", 0x14, 0x0D),  # Notch position
            ("filter_width", 0x1A, 0x03),
            ("pbt", 0x14, 0x07),  # PBT Inner
            ("pbt", 0x14, 0x08),  # PBT Outer
            ("notch", 0x16, 0x57),  # Manual notch width
            ("squelch", 0x15, 0x01),  # S-meter squelch status
        ]
        for cap, cmd_byte, sub_byte in _PER_RX_QUERIES:
            if cap not in capabilities:
                logger.debug(
                    "Skipping %s: capability '%s' not supported by %s",
                    f"query 0x{cmd_byte:02X}/0x{sub_byte:02X}"
                    if sub_byte is not None
                    else f"query 0x{cmd_byte:02X}",
                    cap,
                    profile.model,
                )
                continue
            if profile.supports_cmd29(cmd_byte, sub_byte):
                # Dual-receiver: cmd29-wrapped with receiver byte
                queries.append((cmd_byte, sub_byte, receiver))
            elif receiver == 0:
                # Single-receiver: plain CI-V query (only once, not per-rx)
                queries.append((cmd_byte, sub_byte, None))

        # Per-receiver feature queries that use cmd29 wrapping.
        # Added for any radio whose profile declares cmd29 support for these.
        for cmd_byte, sub_byte in (
            (0x16, 0x12),  # AGC mode
            (0x16, 0x32),  # Audio peak filter
            (0x16, 0x41),  # Auto notch
            (0x16, 0x48),  # Manual notch
            (0x16, 0x4F),  # Twin peak filter
            (0x16, 0x56),  # Filter shape
            (0x1A, 0x04),  # AGC time constant
        ):
            if profile.supports_cmd29(cmd_byte, sub_byte):
                queries.append((cmd_byte, sub_byte, receiver))

    # Global queries (not per-receiver)
    queries.extend(
        [
            (0x18, None, None),  # Power status (on/off)
            (0x1C, 0x00, None),  # PTT (global)
            (0x1C, 0x01, None),  # Tuner/ATU status
            (0x1C, 0x03, None),  # TX frequency monitor
            (0x14, 0x0A, None),  # Power level (global)
            (0x14, 0x0B, None),  # Mic gain (global)
            (0x14, 0x0E, None),  # Compressor level (global)
            (0x14, 0x15, None),  # Monitor gain (global)
            (0x14, 0x09, None),  # CW pitch (global)
            (0x14, 0x0C, None),  # Key speed (global)
            (0x0F, None, None),  # Split (global)
            (0x07, 0xD2, None),  # Active receiver
            (0x07, 0xC2, None),  # Dual Watch status
            (0x21, 0x00, None),  # RIT frequency
            (0x21, 0x01, None),  # RIT status
            (0x21, 0x02, None),  # RIT TX status
        ]
    )

    # Common feature queries (data-driven: if radio has the command, poll it)
    _COMMON_FEATURE_QUERIES: list[tuple[int, int]] = [
        (0x16, 0x44),  # Compressor status
        (0x16, 0x45),  # Monitor status
        (0x16, 0x46),  # VOX status
        (0x16, 0x47),  # Break-in mode
        (0x16, 0x50),  # Dial lock status
        (0x14, 0x16),  # VOX gain
        (0x14, 0x17),  # Anti-VOX gain
        (0x14, 0x0F),  # Break-in delay
    ]
    # NOTE: Antenna status (0x12) is NOT polled.
    # CI-V 0x12 sub-commands are SET-only on IC-7610 (0x12 0x00 = select
    # ANT1, 0x12 0x01 = select ANT2).  Polling them would toggle the
    # antenna every cycle.
    if not profile.supports_cmd29(0x16, 0x12):
        _COMMON_FEATURE_QUERIES.insert(0, (0x16, 0x12))  # AGC mode

    # For serial: ALC/comp/VD/Id meters move to slow state queries
    if is_serial:
        _COMMON_FEATURE_QUERIES.extend(
            [
                (0x15, 0x13),  # ALC meter
                (0x15, 0x14),  # Compressor meter
                (0x15, 0x15),  # VD (voltage)
                (0x15, 0x16),  # Id (PA drain current)
            ]
        )

    for cmd, sub in _COMMON_FEATURE_QUERIES:
        queries.append((cmd, sub, None))

    # Capability-gated optional queries
    if "meters" in capabilities:
        queries.append((0x15, 0x07, None))  # Overflow status
    if "ssb_tx_bw" in capabilities:
        queries.append((0x16, 0x58, None))  # SSB TX bandwidth
    if "scope" in capabilities:
        queries.extend(
            [
                (0x27, 0x12, None),  # Scope receiver selection
                (0x27, 0x13, None),  # Scope single/dual mode
                (0x27, 0x14, None),  # Scope mode (center/fixed)
                (0x27, 0x15, None),  # Scope span
                (0x27, 0x16, None),  # Scope edge number
                (0x27, 0x17, None),  # Scope hold
                (0x27, 0x19, None),  # Scope REF level
                (0x27, 0x1A, None),  # Scope sweep speed
                (0x27, 0x1B, None),  # Scope during TX
                (0x27, 0x1C, None),  # Scope center type
                (0x27, 0x1D, None),  # Scope VBW
                (0x27, 0x1F, None),  # Scope RBW
            ]
        )

    return queries
