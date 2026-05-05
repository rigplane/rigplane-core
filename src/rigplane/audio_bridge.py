"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.audio.bridge
Do not add new symbols here -- add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan.audio_bridge`` literally the same module object as
``icom_lan.audio.bridge``. This preserves attribute walks (incl.
stdlib names not in ``__all__``) and monkeypatch targets.

The two import lines below are BOTH load-bearing -- do not remove
either:

* ``from icom_lan.audio.bridge import *`` -- static-analysis adapter.
* ``sys.modules[__name__] = _canonical`` -- the runtime invariant.
"""

import sys

from icom_lan.audio.bridge import *  # noqa: F401, F403
import icom_lan.audio.bridge as _canonical

sys.modules[__name__] = _canonical
