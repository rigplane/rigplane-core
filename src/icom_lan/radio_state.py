"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.core.radio_state
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan.radio_state`` literally the same module object as
``icom_lan.core.radio_state``. This preserves attribute walks (incl.
stdlib names not in ``__all__``) and monkeypatch targets such as
``unittest.mock.patch('icom_lan.radio_state.…', …)``.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.core.radio_state import *`` — static-analysis
  adapter. Mypy and ruff resolve re-exported names through
  star-imports; they do not model the ``sys.modules`` mutation.
  Without this line, every consumer of
  ``from icom_lan.radio_state import X`` triggers ``attr-defined``
  errors. At runtime this populates the temporary module object,
  which is immediately superseded by the swap below.

* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
  Makes ``icom_lan.radio_state`` and ``icom_lan.core.radio_state``
  the same module object so attribute lookups (including stdlib
  names imported by the canonical module) flow to the canonical
  module.
"""

import sys

from icom_lan.core.radio_state import *  # noqa: F401, F403
import icom_lan.core.radio_state as _canonical

sys.modules[__name__] = _canonical
