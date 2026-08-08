"""Explicit opt-in startup hook for the MOR-1404 passive inbound capture."""

from __future__ import annotations

import atexit

from support.mor1404_passive_civ_capture import install_from_environment

_INSTALLATION = install_from_environment()
if _INSTALLATION is not None:
    atexit.register(_INSTALLATION.close)
