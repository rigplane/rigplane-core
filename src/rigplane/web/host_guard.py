"""Re-export shim for backwards compatibility.

Canonical location: rigplane.core.host_guard
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``rigplane.web.host_guard`` literally the same module object as
``rigplane.core.host_guard``. This preserves attribute walks (incl.
stdlib names not in ``__all__``) and monkeypatch targets.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from rigplane.core.host_guard import *`` — static-analysis adapter.
* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
"""

import sys

from rigplane.core.host_guard import *  # noqa: F401, F403
import rigplane.core.host_guard as _canonical

sys.modules[__name__] = _canonical
