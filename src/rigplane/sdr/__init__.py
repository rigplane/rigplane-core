"""SDR panadapter contracts and runtime (MOR-3151/-3154/-3156/-3157).

Types, protocols, the fake IQ source, the FFT scope, the VFO-tracking
controller, and the server-side runtime wiring them together. SoapySDR
stays in ``sdr.soapy_source`` (lazy, not re-exported): importing
``rigplane.sdr`` must never import SoapySDR.
"""

from __future__ import annotations

from .controller import SdrScopeController
from .fake import FakeIqSource
from .iq_scope import IqFftScope
from .protocol import IqScopeSink, IqSource
from .types import IqBlock, SdrConfig

__all__ = [
    "FakeIqSource",
    "IqBlock",
    "IqFftScope",
    "IqScopeSink",
    "IqSource",
    "SdrConfig",
    "SdrScopeController",
]
