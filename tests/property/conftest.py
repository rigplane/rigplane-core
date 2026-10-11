"""Hypothesis profiles for the property tests in this package.

``ci`` is loaded unless ``HYPOTHESIS_PROFILE`` names another registered
profile; ``nightly`` is the wide-search profile. Loading a profile sets
Hypothesis's process-wide default settings.
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile(
    "ci",
    max_examples=50,
    derandomize=True,
    deadline=None,
    database=None,
)
settings.register_profile(
    "nightly",
    max_examples=1000,
    deadline=None,
    database=None,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
