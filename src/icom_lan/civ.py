"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.core.civ
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan.civ`` literally the same module object as
``icom_lan.core.civ``. This preserves attribute walks (incl.
stdlib names like ``asyncio`` not in ``__all__``) and monkeypatch
targets such as
``unittest.mock.patch('icom_lan.transport.asyncio.get_running_loop', …)``.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.core.civ import *`` — static-analysis adapter.
  Mypy and ruff resolve re-exported names through star-imports; they
  do not model the ``sys.modules`` mutation. Without this line,
  every consumer of ``from icom_lan.civ import X`` triggers
  ``attr-defined`` errors. At runtime this populates the temporary
  module object, which is immediately superseded by the swap below.

* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
  Makes ``icom_lan.civ`` and ``icom_lan.core.civ`` the
  same module object so attribute lookups (including stdlib names
  imported by the canonical module) flow to the canonical module.
"""

import sys

from icom_lan.core.civ import *  # noqa: F401, F403
import icom_lan.core.civ as _canonical

sys.modules[__name__] = _canonical
