"""Built-in diagnostic contributors.

This batch (#1390) ships: system, invocation, dependencies, config.
Subsequent batches add: radio, audio (#1391); logs, state, errors (#1392).
"""

from icom_lan.diagnostics.contributors.config import ConfigContributor
from icom_lan.diagnostics.contributors.dependencies import DependenciesContributor
from icom_lan.diagnostics.contributors.invocation import InvocationContributor
from icom_lan.diagnostics.contributors.system import SystemContributor

__all__ = [
    "ConfigContributor",
    "DependenciesContributor",
    "InvocationContributor",
    "SystemContributor",
]
