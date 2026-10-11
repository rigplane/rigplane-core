"""Test utilities for rigplane: real-socket radio simulators and fault injectors.

This package is a development aid for the test suites of rigplane-core and
downstream projects. It is NOT shipped in the published ``rigplane`` wheel:
``[tool.hatch.build.targets.wheel]`` in ``pyproject.toml`` packages only
``src/rigplane`` and ``src/icom_lan``.

Import path for the Icom LAN simulator::

    from rigplane_testkit.icom_lan import MockIcomRadio

``tests/mock_server.py`` remains as a thin re-export shim so existing test
imports keep working unchanged.
"""
