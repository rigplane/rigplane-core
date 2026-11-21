"""Control transport family of a radio backend (MOR-4437).

A profile describes a radio; a *transport* describes the connection
actually in use.  Some radios answer a capability over one transport only
(the Xiegu G7250 firmware V1.0.1 answers CI-V scope ``27 00`` over LAN and
stays silent over USB serial), so consumers that treat profile fields as
facts about the current connection must derive the transport from the
backend in use — never from ``RadioProfile.has_lan``, which only says the
radio model *offers* a LAN interface.

This module owns the one shared transport enum and the one shared
derivation from the live backend.  The enum lives in ``rigplane.core`` so
every tier (profiles, audio, runtime, backends, web) can import it without
a layer violation; ``rigplane.audio.route`` re-exports it for backwards
compatibility.
"""

from enum import StrEnum
from typing import Final

__all__ = ["RadioTransport", "control_transport_of"]


class RadioTransport(StrEnum):
    """Control transport family used by the active radio backend."""

    LAN = "lan"
    SERIAL = "serial"
    REMOTE = "remote"
    UNKNOWN = "unknown"

    def paces_like_serial(self, *, has_lan: bool) -> bool:
        """Whether link pacing on this transport follows the slow serial budget.

        LAN links take the fast poll/initial-state budget; SERIAL links
        take the slow one (the CI-V UART cannot absorb LAN rates).
        REMOTE and UNKNOWN keep the legacy profile-based answer
        (``not has_lan``) — exactly today's behaviour for every backend
        that predates the explicit transport value, and the safe fallback
        for test doubles that declare no backend.
        """
        if self is RadioTransport.LAN:
            return False
        if self is RadioTransport.SERIAL:
            return True
        return not has_lan


#: The one mapping from a backend family's ``backend_id`` to its transport.
#: This table — not any per-radio branch — is how the transport is derived:
#: ``backend_id`` is a class-level fact of the backend in use, so it cannot
#: be confused by which profile the radio resolved.
_TRANSPORT_BY_BACKEND_ID: Final[dict[str, RadioTransport]] = {
    "rigplane": RadioTransport.LAN,  # IcomRadio — CI-V over Ethernet
    "icom_serial": RadioTransport.SERIAL,
    "yaesu_cat": RadioTransport.SERIAL,
    "rigctld": RadioTransport.REMOTE,
}


def control_transport_of(radio: object) -> RadioTransport:
    """Transport family of the backend actually driving *radio*.

    Reads the backend's own ``backend_id`` (a class-level property of every
    shipped backend); anything without one — a hand-rolled test double, a
    foreign adapter — resolves to ``UNKNOWN``, whose consumers fall back to
    the pre-MOR-4437 behaviour.
    """
    backend_id = getattr(radio, "backend_id", None)
    if isinstance(backend_id, str):
        return _TRANSPORT_BY_BACKEND_ID.get(backend_id, RadioTransport.UNKNOWN)
    return RadioTransport.UNKNOWN
