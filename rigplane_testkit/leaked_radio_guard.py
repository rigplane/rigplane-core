"""Fail the test that leaks a connected Radio, not its neighbour (MOR-4333).

``CoreRadio.__del__`` logs a WARNING ("Radio collected with active connection/tasks")
when an instance is garbage collected while still ``CONNECTED``. The finalizer runs
whenever the cyclic collector gets to the object, which is usually long after the test
that forgot ``disconnect()`` (or ``async with``) is over. The warning then lands in an
unrelated test on the same worker, and a neighbour that asserts "no warnings" fails
(quick-python run 42622079703: ``test_audio_scope_source`` blamed for somebody else's radio).

This plugin moves the failure to its cause. Every ``CoreRadio`` is remembered through a
weak reference. At the end of each test's teardown, and only when a radio is still alive
and connected, it runs ``gc.collect()`` and fails the test if the finalizer warned during
that collection. Tests that never create a connected radio pay one list scan; no test pays
a full collection it did not earn.

Load it from the top-level ``conftest.py`` of a suite::

    pytest_plugins = ["rigplane_testkit.leaked_radio_guard"]

A test that fakes a connection (``fake_connected(radio)``, which stands in for a login
the test does not perform) gets the fake undone when the test ends, so the radio is
disconnected the way a real one would be. A faked radio is kept alive until then, so the
collector cannot finalize it while it still reads as connected. A test that provokes the
finalizer's warning on purpose says so with ``@pytest.mark.leaks_connected_radio(reason="...")``.

Fixture values of the test are released before the collection (pytest would keep them
until after the teardown hooks). Known limit: a radio that is still referenced after
teardown (a module- or session-scoped fixture, a module global, an attribute of a test
class instance, or the traceback of a failed test) is not collected there, so it is judged when it is finally
dropped, in whichever test's teardown that happens.
"""

from __future__ import annotations

import gc
import logging
import weakref
from collections.abc import Generator

import pytest

from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import CoreRadio

__all__ = ["LeakedRadioError", "fake_connected"]

# The finalizer's message in ``rigplane.runtime.radio``; the self-test pins it end to end.
_LOGGER_NAME = "rigplane.runtime.radio"
_MESSAGE_PREFIX = "Radio collected with active connection"


class LeakedRadioError(AssertionError):
    """A test left a connected ``CoreRadio`` behind for the garbage collector."""


class _Recorder(logging.Handler):
    """Collects the finalizer's warning without depending on propagation or caplog."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.leaks: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(_MESSAGE_PREFIX):
            self.leaks.append(message)


_recorder = _Recorder()
_faked: list[CoreRadio] = []
_alive: list[weakref.ref[CoreRadio]] = []
_original_init = CoreRadio.__init__


def _tracking_init(self: CoreRadio, *args: object, **kwargs: object) -> None:
    _original_init(self, *args, **kwargs)  # type: ignore[arg-type]
    _alive.append(weakref.ref(self))


def fake_connected(radio: CoreRadio) -> CoreRadio:
    """Put ``radio`` in the CONNECTED state without a login; undone when the test ends.

    For tests that exercise code behind the connection check with fake transports. The
    radio holds no real connection, so ending the test just marks it disconnected, which
    is what keeps its finalizer quiet.
    """
    radio._connected = True
    # A strong reference: a radio the test lets go of early (a helper's local, one per loop
    # turn) must not be finalized while still marked connected, which only the timing of the
    # collector would decide. It is released after the fake is undone.
    _faked.append(radio)
    return radio


def _undo_faked_connections() -> None:
    for radio in _faked:
        radio._connected = False
    _faked.clear()


def _connected_radio_alive() -> bool:
    """Drop dead references; True if a live radio is still CONNECTED."""
    _alive[:] = [ref for ref in _alive if ref() is not None]
    return any(
        radio is not None and radio._conn_state == RadioConnectionState.CONNECTED
        for radio in (ref() for ref in _alive)
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "leaks_connected_radio(reason): the test provokes the Radio finalizer's "
        "'collected while connected' warning on purpose",
    )
    CoreRadio.__init__ = _tracking_init  # type: ignore[method-assign]
    logging.getLogger(_LOGGER_NAME).addHandler(_recorder)


def pytest_unconfigure(config: pytest.Config) -> None:
    CoreRadio.__init__ = _original_init  # type: ignore[method-assign]
    logging.getLogger(_LOGGER_NAME).removeHandler(_recorder)


def _expects_leak(item: pytest.Item) -> bool:
    mark = item.get_closest_marker("leaks_connected_radio")
    if mark is None:
        return False
    if not str(mark.kwargs.get("reason", "")).strip():
        raise pytest.UsageError(
            f"{item.nodeid}: leaks_connected_radio needs a non-empty reason=..."
        )
    return True


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    _recorder.leaks.clear()
    return (yield)


def _release_fixture_values(item: pytest.Item) -> None:
    """Drop the test's fixture values, which pytest keeps until after teardown returns.

    ``runtestprotocol`` sets ``item.funcargs = None`` only once every teardown hook has
    run, so a function-scoped fixture that handed out a connected radio would still be
    referenced here and the collector would reach the radio during a later test. The
    finalizers have already run when this is called, so nothing needs the values again.
    """
    funcargs = getattr(item, "funcargs", None)
    if isinstance(funcargs, dict):
        funcargs.clear()


def _collect_leaks(item: pytest.Item) -> list[str]:
    """Collect now if a connected radio is still alive; return the finalizer's warnings."""
    _undo_faked_connections()
    _release_fixture_values(item)
    if _connected_radio_alive():
        gc.collect()
    leaks = list(_recorder.leaks)
    _recorder.leaks.clear()
    return leaks


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, None, None]:
    # tryfirst makes this the outermost wrapper: it runs after every fixture finalizer.
    try:
        result = yield
    except BaseException:
        # A finalizer already failed this test. Still collect so the warning is spent
        # here instead of landing in the next test, but keep the original error.
        _collect_leaks(item)
        raise
    leaks = _collect_leaks(item)
    if leaks and not _expects_leak(item):
        raise LeakedRadioError(
            f"{item.nodeid} left a connected Radio for the garbage collector "
            f"({len(leaks)}x {leaks[0]!r}). Call await radio.disconnect() or use "
            "'async with' so the radio is disconnected before the test ends."
        )
    return result
