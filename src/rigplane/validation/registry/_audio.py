"""Automated audio-pipeline probe checks (GH #1650; MOR-639/640/641).

Registry positions 22+ (appended after the historical 1-21 block; the
template generators stable-sort by level, so these COMPATIBILITY_SURFACES
checks slot in next to the MANUAL ``audio.rx`` / ``scope.capture`` entries).

The AUDIO_PROBE kind is CI-automated: each check executes against the
deterministic audio fakes via :mod:`rigplane.validation.audio_checks` and is
never auto-run on a live radio. The pre-existing MANUAL entries are kept for
real-hardware operator confirmation; these probes are their automated,
regression-gating counterparts.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
    # 22 — audio.rx.rms (T4 / MOR-639)
    CheckSpec(
        check_id="audio.rx.rms",
        capability="audio",
        kind=CheckKind.AUDIO_PROBE,
        level=ValidationLevel.COMPATIBILITY_SURFACES,
        failure_domain=FailureDomain.AUDIO,
        summary=(
            "Automated CI probe: inject a reference tone through the RX audio "
            "pipeline and verify the delivered PCM RMS stays in the expected band."
        ),
        protocol="audio",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 23 — audio.tx.byte_perfect (T5 / MOR-640)
    CheckSpec(
        check_id="audio.tx.byte_perfect",
        capability="audio",
        kind=CheckKind.AUDIO_PROBE,
        level=ValidationLevel.COMPATIBILITY_SURFACES,
        failure_domain=FailureDomain.AUDIO,
        summary=(
            "Automated CI probe: verify captured TX audio PCM survives the "
            "LAN audio packetization pipeline byte-perfect."
        ),
        protocol="audio",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        # GH #1650: TX audio requires explicit operator safety enablement on
        # real hardware. The CI probe never keys anything, but the template
        # entry stays behind the tx_allowed operator gate like tx.ptt.
        tx_adjacent=True,
    ),
)
