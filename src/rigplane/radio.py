"""Re-export shim for backwards compatibility.

Canonical location: icom_lan.runtime.radio
Do not add new symbols here — add them at the canonical location.

This file uses the sys.modules-alias pattern: importing this shim
makes ``icom_lan.radio`` literally the same module object as
``icom_lan.runtime.radio``. This preserves attribute walks (incl.
stdlib names not in ``__all__``) and monkeypatch targets.

The two import lines below are BOTH load-bearing — do not remove
either:

* ``from icom_lan.runtime.radio import *`` — static-analysis adapter.
* ``sys.modules[__name__] = _canonical`` — the runtime invariant.

If a future external consumer needs to reach a private (``_``-prefixed)
symbol through this shim, add an explicit ``from icom_lan.runtime.radio
import _foo as _foo`` line — ``import *`` excludes underscores by
Python spec, so without it mypy can't see the symbol statically through
the shim. The plan §5.1.1 hybrid pattern documents this case. No such
re-export is currently needed.
"""

import sys

from icom_lan.runtime.radio import *  # noqa: F401, F403
import icom_lan.runtime.radio as _canonical

sys.modules[__name__] = _canonical
