"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.core.protocol
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan.protocol`` literally the same module object as
``icom_lan.core.protocol``. This preserves attribute walks (incl.
stdlib names like ``asyncio`` not in ``__all__``) and monkeypatch
targets such as
``unittest.mock.patch('icom_lan.transport.asyncio.get_running_loop', …)``.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.core.protocol import *`` — static-analysis adapter.
  Mypy and ruff resolve re-exported names through star-imports; they
  do not model the ``sys.modules`` mutation. Without this line,
  every consumer of ``from icom_lan.protocol import X`` triggers
  ``attr-defined`` errors. At runtime this populates the temporary
  module object, which is immediately superseded by the swap below.

* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
  Makes ``icom_lan.protocol`` and ``icom_lan.core.protocol`` the
  same module object so attribute lookups (including stdlib names
  imported by the canonical module) flow to the canonical module.
"""

import sys

from icom_lan.core.protocol import *  # noqa: F401, F403
import icom_lan.core.protocol as _canonical

sys.modules[__name__] = _canonical
