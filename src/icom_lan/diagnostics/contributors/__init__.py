"""Built-in diagnostic contributors.

Batch #1390 ships: system, invocation, dependencies, config.
Batch #1391 adds: radio, audio.
Subsequent batches add: logs, state, errors (#1392).
"""

from icom_lan.diagnostics.contributors.audio import AudioContributor
from icom_lan.diagnostics.contributors.config import ConfigContributor
from icom_lan.diagnostics.contributors.dependencies import DependenciesContributor
from icom_lan.diagnostics.contributors.invocation import InvocationContributor
from icom_lan.diagnostics.contributors.radio import RadioContributor
from icom_lan.diagnostics.contributors.system import SystemContributor

__all__ = [
    "AudioContributor",
    "ConfigContributor",
    "DependenciesContributor",
    "InvocationContributor",
    "RadioContributor",
    "SystemContributor",
]
