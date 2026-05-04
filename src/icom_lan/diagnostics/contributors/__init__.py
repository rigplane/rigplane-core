"""Built-in diagnostic contributors.

#1390 ships: system, invocation, dependencies, config.
#1391 adds: radio, audio.
#1392 adds: logs, state, errors.
"""

from icom_lan.diagnostics.contributors.audio import AudioContributor
from icom_lan.diagnostics.contributors.config import ConfigContributor
from icom_lan.diagnostics.contributors.dependencies import DependenciesContributor
from icom_lan.diagnostics.contributors.errors import ErrorsContributor
from icom_lan.diagnostics.contributors.invocation import InvocationContributor
from icom_lan.diagnostics.contributors.logs import LogsContributor
from icom_lan.diagnostics.contributors.radio import RadioContributor
from icom_lan.diagnostics.contributors.state import StateContributor
from icom_lan.diagnostics.contributors.system import SystemContributor

__all__ = [
    "AudioContributor",
    "ConfigContributor",
    "DependenciesContributor",
    "ErrorsContributor",
    "InvocationContributor",
    "LogsContributor",
    "RadioContributor",
    "StateContributor",
    "SystemContributor",
]
