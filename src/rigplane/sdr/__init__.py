"""SDR panadapter contracts: IQ source protocol, block/config types, fake.

Contract-first package (MOR-3151): every other core SDR issue codes
against the types defined here — :class:`IqBlock`,
:class:`SdrConfig`, the :class:`IqSource` backend contract, the
:class:`IqScopeSink` surface the controller drives, and the
:class:`FakeIqSource` test double. FFT scope, the SoapySDR adapter, and
server wiring are separate issues and live outside this contract.
"""

from __future__ import annotations

from .fake import FakeIqSource
from .protocol import IqScopeSink, IqSource
from .types import IqBlock, SdrConfig

__all__ = [
    "FakeIqSource",
    "IqBlock",
    "IqScopeSink",
    "IqSource",
    "SdrConfig",
]
