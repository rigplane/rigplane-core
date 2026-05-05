"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.runtime.cw_auto_tuner
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan.cw_auto_tuner`` literally the same module object as
``icom_lan.runtime.cw_auto_tuner``. This preserves attribute walks
(incl. stdlib names not in ``__all__``) and monkeypatch targets.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.runtime.cw_auto_tuner import *`` — static-analysis
  adapter.
* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
"""

import sys

from icom_lan.runtime.cw_auto_tuner import *  # noqa: F401, F403
import icom_lan.runtime.cw_auto_tuner as _canonical

sys.modules[__name__] = _canonical
