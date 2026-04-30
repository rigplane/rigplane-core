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

The third line below — the explicit underscore re-export — is needed
for any private (``_``-prefixed) symbol that an external consumer
imports through this shim. ``import *`` excludes underscores by
Python spec, so mypy can't see the symbol statically through the
shim. Explicit re-import makes the static graph match the runtime
identity. As consumers migrate to canonical paths in Step 13, the
underscore re-imports here become dead and can be removed.
"""

import sys

from icom_lan.runtime.radio import *  # noqa: F401, F403
from icom_lan.runtime.radio import _DEFAULT_AUDIO_CODEC as _DEFAULT_AUDIO_CODEC  # noqa: F401  # consumer: sync.py
import icom_lan.runtime.radio as _canonical

sys.modules[__name__] = _canonical
