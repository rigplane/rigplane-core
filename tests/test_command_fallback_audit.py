"""Tests for the Step 1 command-fallback measurement hook (MOR-2001).

Temporary, like the code under test: this file is deleted in Step Z of
``docs/plans/2026-08-29-profile-driven-command-bytes.md`` together with
``src/rigplane/commands/_fallback_audit.py``, its install call in
``commands/__init__.py``, and the charter exception recorded for it in
``commands/LAYER.md``.

The flag-on tests need ``rigplane.commands`` reloaded with
``RIGPLANE_COMMAND_FALLBACK_AUDIT`` set, because the wrapper installs once,
at import. Several other test files
(``tests/_command_test_helpers.py: bind_default_addr_module``) mutate the
same shared ``rigplane.commands`` module object at their own collection
time to bind a default ``to_addr`` onto every builder, and that mutation
must still be in place for those files' tests, wherever they run relative
to this one in the same worker process. ``_reloaded_with_flag`` below
snapshots ``vars(rigplane.commands)`` before reloading and restores that
exact snapshot in a ``finally``, so a reload done inside one test here
never leaks into the next test -- in this file or any other.

Representative builder: ``dsp.py``'s ``get_attenuator``/``set_attenuator``,
not ``freq.py``'s ``get_freq``/``set_freq`` as originally written -- MOR-2008
batch 2 migrated ``get_freq``/``set_freq`` onto the required-``cmd_map``
contract, so calling either with ``cmd_map=None`` (this file's whole point:
proving the audit wrapper logs when the old fallback engages) now raises
``TypeError`` before the wrapper's own logic ever runs. ``dsp.py`` is not
migrated by any batch so far, so its builders still carry a real
``cmd_map is None`` fallback branch to audit.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import logging

import pytest

import rigplane.commands as commands
import rigplane.commands._frame as frame_module
import rigplane.commands.dsp as dsp_module
from rigplane.commands import _fallback_audit
from rigplane.commands.command_map import CommandMap
from rigplane.commands.speech import get_speech as raw_get_speech
from rigplane.core.env_config import get_command_fallback_audit_enabled

# ``commands/__init__.py`` does ``from .speech import get_speech, speech``,
# which rebinds the package attribute ``rigplane.commands.speech`` from the
# submodule to the *function* alias (``speech = get_speech`` in
# ``speech.py``). So ``import rigplane.commands.speech as speech_module``
# resolves to that function, not the submodule -- the import above pins the
# submodule's own name instead, unaffected by the shadowing.

_FLAG = "RIGPLANE_COMMAND_FALLBACK_AUDIT"
_AUDIT_LOGGER = _fallback_audit.__name__


@contextlib.contextmanager
def _reloaded_with_flag(monkeypatch: pytest.MonkeyPatch, value: str | None):
    """Reload ``rigplane.commands`` with ``_FLAG`` set to *value*.

    Restores the module's prior attribute set exactly on exit, so this
    reload cannot leak into any other test -- including one that mutated
    the same shared module object before this one ran (see module
    docstring).
    """
    snapshot = dict(vars(commands))
    if value is None:
        monkeypatch.delenv(_FLAG, raising=False)
    else:
        monkeypatch.setenv(_FLAG, value)
    try:
        yield importlib.reload(commands)
    finally:
        vars(commands).clear()
        vars(commands).update(snapshot)


class TestGetCommandFallbackAuditEnabled:
    """The env reader in core/env_config.py, matching get_managed_tx_enabled's style."""

    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_FLAG, raising=False)
        assert get_command_fallback_audit_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "on", "yes", "TRUE", "On"])
    def test_truthy_values_enable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(_FLAG, value)
        assert get_command_fallback_audit_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
    def test_falsy_values_disable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(_FLAG, value)
        assert get_command_fallback_audit_enabled() is False

    def test_unrecognised_value_warns_and_stays_off(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(_FLAG, "maybe")
        with caplog.at_level(logging.WARNING, logger="rigplane.core.env_config"):
            result = get_command_fallback_audit_enabled()
        assert result is False
        assert _FLAG in caplog.text


class TestFlagOff:
    """Off means the raw functions -- not a pass-through wrapper."""

    def test_exports_are_the_raw_functions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _reloaded_with_flag(monkeypatch, None) as reloaded:
            assert reloaded.get_attenuator is dsp_module.get_attenuator
            assert reloaded.set_attenuator is dsp_module.set_attenuator
            # Backward-compat alias: same object as its canonical name.
            assert reloaded.speech is raw_get_speech
            assert reloaded.get_speech is raw_get_speech

    def test_calling_without_a_map_logs_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        with _reloaded_with_flag(monkeypatch, None) as reloaded:
            with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
                reloaded.get_attenuator(to_addr=0x94, cmd_map=None)
            assert caplog.records == []


class TestFlagOn:
    """On means every exported cmd_map-taking builder is wrapped."""

    def test_exports_are_wrapped_but_unwrap_to_the_raw_function(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _reloaded_with_flag(monkeypatch, "1") as reloaded:
            assert reloaded.get_attenuator is not dsp_module.get_attenuator
            assert inspect.unwrap(reloaded.get_attenuator) is dsp_module.get_attenuator
            assert reloaded.get_attenuator.__name__ == "get_attenuator"
            assert reloaded.get_attenuator.__doc__ == dsp_module.get_attenuator.__doc__

    def test_alias_identity_is_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _reloaded_with_flag(monkeypatch, "1") as reloaded:
            # speech = get_speech in speech.py: one wrap, shared by both names.
            assert reloaded.speech is reloaded.get_speech

    def test_functions_without_cmd_map_are_left_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _reloaded_with_flag(monkeypatch, "1") as reloaded:
            assert (
                "cmd_map" not in inspect.signature(reloaded.build_civ_frame).parameters
            )
            assert reloaded.build_civ_frame is frame_module.build_civ_frame

    def test_call_without_a_map_logs_exactly_one_warning_naming_the_builder(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        with _reloaded_with_flag(monkeypatch, "1") as reloaded:
            with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
                result = reloaded.get_attenuator(to_addr=0x94, cmd_map=None)
            assert result == dsp_module.get_attenuator(to_addr=0x94, cmd_map=None)
            assert len(caplog.records) == 1
            assert "get_attenuator" in caplog.text

    def test_call_with_a_map_logs_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        cmd_map = CommandMap({"get_attenuator": (0x11,)})
        with _reloaded_with_flag(monkeypatch, "1") as reloaded:
            with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
                reloaded.get_attenuator(to_addr=0x94, cmd_map=cmd_map)
            assert caplog.records == []

    def test_return_value_and_exceptions_are_preserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _reloaded_with_flag(monkeypatch, "1") as reloaded:
            assert reloaded.get_attenuator(
                to_addr=0x94, cmd_map=None
            ) == dsp_module.get_attenuator(to_addr=0x94, cmd_map=None)
            with pytest.raises(TypeError):
                reloaded.get_attenuator()  # missing required to_addr, both wrapped and raw

    def test_reload_does_not_leak_into_the_ambient_module(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The context manager's restore must actually run.

        Compares against whatever ``commands.get_attenuator`` was *before*
        the ``with`` block, not against the raw ``dsp_module.get_attenuator``:
        another collected test file's ``bind_default_addr_module`` (see
        module docstring) may already have rebound it to a partial before
        this test runs, and that is exactly the state the restore must put
        back.
        """
        before = commands.get_attenuator
        with _reloaded_with_flag(monkeypatch, "1"):
            pass
        assert commands.get_attenuator is before


def test_module_docstring_cites_the_plan_and_step_z() -> None:
    assert _fallback_audit.__doc__ is not None
    assert (
        "docs/plans/2026-08-29-profile-driven-command-bytes.md"
        in _fallback_audit.__doc__
    )
    assert "Step Z" in _fallback_audit.__doc__
