"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.runtime._dual_rx_runtime
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan._dual_rx_runtime`` literally the same module object
as ``icom_lan.runtime._dual_rx_runtime``. This preserves attribute
walks (incl. stdlib names not in ``__all__``) and monkeypatch
targets.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.runtime._dual_rx_runtime import *`` —
  static-analysis adapter.
* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
"""

import sys

from icom_lan.runtime._dual_rx_runtime import *  # noqa: F401, F403
import icom_lan.runtime._dual_rx_runtime as _canonical

sys.modules[__name__] = _canonical
