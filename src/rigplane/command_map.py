"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.commands.command_map
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan.command_map`` literally the same module object as
``icom_lan.commands.command_map``. This preserves attribute walks (incl.
stdlib names not in ``__all__``) and monkeypatch targets.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.commands.command_map import *`` — static-analysis adapter.
* ``sys.modules[__name__] = _canonical`` — the runtime invariant.
"""

import sys

from icom_lan.commands.command_map import *  # noqa: F401, F403
import icom_lan.commands.command_map as _canonical

sys.modules[__name__] = _canonical
