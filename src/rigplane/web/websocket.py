"""Re-export shim for backwards compatibility.

Canonical location: rigplane.tci.ws
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``rigplane.web.websocket`` literally the same module object as
``rigplane.tci.ws``. This preserves attribute walks (incl.
stdlib names not in ``__all__``) and monkeypatch targets.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from rigplane.tci.ws import *`` — static-analysis adapter.
* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
"""

import sys

from rigplane.tci.ws import *  # noqa: F401, F403
import rigplane.tci.ws as _canonical
from rigplane.tci.ws import _read_one_frame as _read_one_frame  # noqa: F401

sys.modules[__name__] = _canonical
