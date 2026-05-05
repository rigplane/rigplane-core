"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.core._bounded_queue
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan._bounded_queue`` literally the same module object as
``icom_lan.core._bounded_queue``. This preserves attribute walks
(incl. stdlib names not in ``__all__``) and monkeypatch targets such
as ``unittest.mock.patch('icom_lan._bounded_queue.asyncio.…', …)``.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.core._bounded_queue import *`` — static-analysis
  adapter. Mypy and ruff resolve re-exported names through
  star-imports; they do not model the ``sys.modules`` mutation.
  Without this line, every consumer of
  ``from icom_lan._bounded_queue import X`` triggers ``attr-defined``
  errors. At runtime this populates the temporary module object,
  which is immediately superseded by the swap below.

* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
  Makes ``icom_lan._bounded_queue`` and
  ``icom_lan.core._bounded_queue`` the same module object so
  attribute lookups (including stdlib names imported by the
  canonical module) flow to the canonical module.
"""

import sys

from icom_lan.core._bounded_queue import *  # noqa: F401, F403
import icom_lan.core._bounded_queue as _canonical

sys.modules[__name__] = _canonical
