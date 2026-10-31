"""TCI adapter: the Core side of the TCI server (MOR-3821, spec 4).

Sibling of ``rigplane.web`` and ``rigplane.rigctld``. Translates TCI text
commands to the shared ``CommandIntent`` pipeline, reads state from the
shared ``StateStore``, pushes changes to every client, and keys only
through ``ManagedTxAuthority``.
"""

from .commands import (
    TCI_CHANNELS_COUNT,
    TCI_DEFAULT_HOST,
    TCI_DEFAULT_PORT,
    TCI_PROTOCOL_NAME,
    TCI_PROTOCOL_VERSION,
    TCI_TRX_COUNT,
)
from .handler import TciAdapterConfig, TciAdapterHandler
from .notifier import TciNotifier
from .server import TciAdapterServer, build_tci_server, tci_allow_predicate

__all__ = [
    "TCI_CHANNELS_COUNT",
    "TCI_DEFAULT_HOST",
    "TCI_DEFAULT_PORT",
    "TCI_PROTOCOL_NAME",
    "TCI_PROTOCOL_VERSION",
    "TCI_TRX_COUNT",
    "TciAdapterConfig",
    "TciAdapterHandler",
    "TciAdapterServer",
    "TciNotifier",
    "build_tci_server",
    "tci_allow_predicate",
]
