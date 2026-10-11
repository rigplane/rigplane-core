"""Yaesu CAT fakes for the rigplane test suites.

The implementation lives in :mod:`rigplane_testkit.yaesu.cat_fake`; this
module re-exports its public surface so callers can write
``from rigplane_testkit.yaesu import FakeCatRadio``.
"""

from rigplane_testkit.yaesu.cat_fake import FakeCatRadio

__all__ = ["FakeCatRadio"]
