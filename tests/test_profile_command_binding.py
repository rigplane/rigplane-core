"""Tests for MOR-2003 Step 3 of
``docs/plans/2026-08-29-profile-driven-command-bytes.md``: binding a
radio's ``CommandMap`` once, at construction, into
``commands.bound.BoundCommands``.

Four things are pinned here:

- every CI-V profile the library loads out of ``rigs/`` carries its
  ``CommandMap`` through ``RigConfig.to_profile`` -> ``RadioProfile`` ->
  ``CoreRadio.__init__`` -> ``BoundCommands`` unchanged (§4 Step 3's own
  "red if broken" test);
- ``BoundCommands.__getattr__`` resolves a builder from
  ``rigplane.commands``'s public namespace with the map already applied,
  and refuses a name that is not such a builder;
- ``BoundCommands.expect`` decodes the same map entry, via the same
  decoder, that the builder itself would use -- for an exposed builder --
  and refuses with a named-migration message for one that is not exposed
  yet;
- the drift guard the owner ruling on MOR-2003 requires: for every builder
  that exposes a command-map key (``@expose_command_key`` in
  ``commands/_frame.py``), the exposed callable resolves to exactly the
  key the builder's own body passes to ``_build_from_map``. This is
  checked dynamically -- by capturing what each builder actually passes --
  rather than by re-stating the literal, so the two cannot silently drift
  apart.

Constructing a full ``CoreRadio`` per profile (rather than binding
directly off the ``RadioProfile.command_map`` field) was the more thorough
option and was cheap to run here (six profiles, all in-memory, no I/O) --
it also doubles as coverage that construction never raises for a real
profile's map, plain or empty.
"""

from __future__ import annotations

import functools
import inspect
import pathlib
import sys
from typing import Any

import pytest

import rigplane.commands as commands
from rigplane.commands import get_rf_power, get_speech, ptt_on
from rigplane.commands._frame import decode_wire_tuple
from rigplane.commands.bound import BoundCommands
from rigplane.commands.command_map import CommandMap
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.runtime.radio import CoreRadio

# Reused rather than re-implemented, per the MOR-2006 drift-guard extension:
# the same probe-ladder search test_command_map_parity.py uses to find a
# builder's required arguments (see TestExposedKeyDriftGuard below).
from test_command_map_parity import _candidate_kwargs, _split_params

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"


@functools.lru_cache(maxsize=1)
def _civ_rig_configs() -> dict[str, Any]:
    """CI-V rig configs out of ``rigs/`` -- CAT-only profiles excluded.

    A CAT-only profile (FTX-1, TX-500: ``protocol_type`` other than
    ``"civ"``) has an intentionally empty ``CommandMap`` --
    ``RigConfig.to_command_map`` drops every ``CatCommandSpec`` entry
    (plan §7, "FTX-1 and TX-500 are not stranded"). Excluding them here
    keeps this test's "non-empty" assertion meaningful for the profiles it
    actually covers.
    """
    return {
        model: config
        for model, config in discover_rigs(RIGS_DIR).items()
        if config.protocol_type == "civ"
    }


class TestProfileCarriesCommandMap:
    """Step 3's own red-if-broken test (plan §4)."""

    @pytest.mark.parametrize("model", sorted(_civ_rig_configs()))
    def test_bound_command_set_matches_to_command_map(self, model: str) -> None:
        config = _civ_rig_configs()[model]
        expected = set(config.to_command_map())
        assert expected, f"{model}: to_command_map() produced no CI-V commands"

        profile = config.to_profile()
        assert profile.command_map is not None
        assert set(profile.command_map) == expected

        radio = CoreRadio("127.0.0.1", profile=profile)
        assert set(radio._commands._map) == expected


class TestCommandMapEquality:
    """``CommandMap`` is documented as an immutable mapping; make that true for ``==``.

    Motivated directly by this step: ``RadioProfile`` now carries a
    ``CommandMap`` field, and
    ``tests/test_ftx1_control_domains.py:
    test_ftx1_scalar_domains_are_exact_loader_published_capabilities``
    sweeps every ``RadioProfile`` field for equality between two profiles
    independently loaded from the same TOML -- which needs two
    ``CommandMap`` instances with the same contents to compare equal, not
    only to hold the same names. Before this, ``CommandMap`` had no
    ``__eq__``, so two instances built from identical dicts compared
    unequal (identity), and that sweep failed on the new field.
    """

    def test_equal_contents_compare_equal(self) -> None:
        assert CommandMap({"ptt_on": (0x1C, 0x00)}) == CommandMap(
            {"ptt_on": (0x1C, 0x00)}
        )

    def test_different_contents_compare_unequal(self) -> None:
        assert CommandMap({"ptt_on": (0x1C, 0x00)}) != CommandMap(
            {"ptt_on": (0x1C, 0x01)}
        )

    def test_not_equal_to_a_non_command_map(self) -> None:
        assert CommandMap({}) != {}
        assert CommandMap({}) != object()

    def test_hashable_and_consistent_with_equality(self) -> None:
        a = CommandMap({"ptt_on": (0x1C, 0x00)})
        b = CommandMap({"ptt_on": (0x1C, 0x00)})
        assert hash(a) == hash(b)


class TestBoundCommandsGetattr:
    def test_returns_builder_with_map_applied(self) -> None:
        cmd_map = CommandMap({"ptt_on": (0x1C, 0x00, 0x01)})
        bound = BoundCommands(cmd_map)
        assert bound.ptt_on(to_addr=0x94) == ptt_on(to_addr=0x94, cmd_map=cmd_map)

    def test_unknown_name_raises_attribute_error(self) -> None:
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(AttributeError):
            bound.not_a_real_builder_name  # noqa: B018

    def test_non_builder_name_raises_attribute_error(self) -> None:
        # Exported from rigplane.commands, but not a cmd_map-taking builder.
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(AttributeError):
            bound.CONTROLLER_ADDR  # noqa: B018

    def test_leading_underscore_name_raises_attribute_error(self) -> None:
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(AttributeError):
            bound._build_from_map  # noqa: B018


class TestExpect:
    def test_expect_on_exposed_builder_matches_decoder(self) -> None:
        wire = (0x1C, 0x00, 0x01)
        bound = BoundCommands(CommandMap({"ptt_on": wire}))
        assert bound.expect(ptt_on) == decode_wire_tuple(wire)

    def test_expect_on_speech_resolves_per_map_probe(self) -> None:
        """The fourth case in plan §3.1: get_speech's key is a function of the map."""
        with_set = CommandMap({"set_speech": (0x13,)})
        assert BoundCommands(with_set).expect(get_speech) == decode_wire_tuple((0x13,))

        with_get = CommandMap({"get_speech": (0x13, 0x01)})
        assert BoundCommands(with_get).expect(get_speech) == decode_wire_tuple(
            (0x13, 0x01)
        )

    def test_expect_on_unexposed_builder_names_the_migration(self) -> None:
        bound = BoundCommands(CommandMap({"get_rf_power": (0x14, 0x0A)}))
        with pytest.raises(AttributeError, match="Steps 5..N"):
            bound.expect(get_rf_power)


# ── the drift guard ──

_PROBE_MAPS = (
    CommandMap({}),
    CommandMap({"set_speech": (0x13,)}),
)


def _exposed_builders() -> list[tuple[str, Any]]:
    """Every builder in ``rigplane.commands``'s public namespace exposing a key.

    Deduplicated by the identity of the attached ``cmd_map_key`` callable --
    not by ``id(value)`` or a single ``__wrapped__`` hop, and not by
    ``__qualname__`` -- so a backward-compat alias (``speech = get_speech``)
    is not exercised twice under two names, and stays that way regardless of
    how many times the shared ``rigplane.commands`` package namespace has
    already been rebound by the time this file is collected.

    Not gated on ``inspect.isfunction``: five other test files
    (``tests/_command_test_helpers.py: bind_default_addr_module``, called
    from ``test_commands.py``, ``test_commands_extended.py``,
    ``test_main_sub_tracking.py``, ``test_rf_gain_af_level.py`` and
    ``test_scope.py``) each rebind every builder on the shared
    ``rigplane.commands`` package namespace into a fresh
    ``functools.partial`` with a default ``to_addr`` bound, at their own
    collection time -- and each rebind wraps whatever is *currently* bound
    to a name, not the original function, so when more than one of those
    files is collected before this one the same underlying builder ends up
    wrapped through two or more independent layers (measured: 24 items
    standalone, 26 in the full suite, before this fix).

    A single ``__wrapped__`` hop only reaches the immediately-inner layer.
    Two names that alias the same function (``speech`` and ``get_speech``)
    are wrapped *separately* at every layer -- each rebind iterates
    ``dir(module)`` and wraps each qualifying name on its own -- so at two
    or more layers ``speech.__wrapped__`` and ``get_speech.__wrapped__`` are
    two different partial objects (each wrapping the *previous* layer's own
    partial for that name), and comparing their identity no longer
    collapses the alias. ``__qualname__`` would collapse it correctly here
    (it is copied by value at every layer and is intrinsic to the
    underlying function, not the name it is reached through), but two
    *different* builders in two different modules could coincidence-collide
    on a bare function name as Steps 5..N add more exposed builders here,
    which ``__qualname__`` alone cannot tell apart.

    ``cmd_map_key`` has neither problem: ``commands/_frame.py:
    expose_command_key`` attaches it once, directly, to the underlying
    function's own ``__dict__``, and ``functools.update_wrapper``'s
    ``__dict__.update()`` step re-copies that exact object -- never a copy
    of a copy, the same object every time -- onto each new wrapper,
    however many layers stack, and it is unique per builder (an alias
    shares the object because it shares the underlying function; two
    distinct builders never do, even if their keys happen to return the
    same literal).

    Deduplicated on ``(id(key_fn), qualname)``, not ``id(key_fn)`` alone
    (a Step 3/4 review trap recorded against MOR-2006): bare ``id``
    dedup goes false-green the moment two *distinct* builders ever end up
    sharing one ``cmd_map_key`` callable object -- e.g. a shared, named key
    function (unlike each builder's own one-off ``lambda``) passed to
    ``@expose_command_key`` on two different functions by mistake -- because
    the second builder found would be silently skipped as "the same
    alias" and never exercised below. The alias case this function must
    still collapse (``speech = get_speech``) keeps working: both names
    resolve to the same underlying function, so they share both ``id``
    and ``qualname``. Two genuinely different builders share neither.
    """
    seen: set[tuple[int, str]] = set()
    found: list[tuple[str, Any]] = []
    for name in commands.__all__:
        value = getattr(commands, name, None)
        if not callable(value):
            continue
        key_fn = getattr(value, "cmd_map_key", None)
        if key_fn is None:
            continue
        identity = (id(key_fn), getattr(value, "__qualname__", name))
        if identity in seen:
            continue
        seen.add(identity)
        found.append((name, value))
    return found


class TestExposedKeyDriftGuard:
    """The owner ruling's drift test: exposed callable == body's actual key.

    For each exposed builder and each probe map, the builder is called
    with ``_build_from_map`` monkeypatched to capture the ``name`` it was
    given, then that capture is compared against calling the builder's own
    ``cmd_map_key(cmd_map)``. Two probe maps exercise both branches of
    ``get_speech``'s per-map probe; the other exposed builders (ptt_on,
    ptt_off) ignore the map and must produce the same literal for both.

    A fixed call shape of ``builder(to_addr=..., cmd_map=...)`` was the
    original design here, and it broke the moment MOR-2006 exposed
    ``commands/config.py``'s nine setters, each with a required value
    argument (``level``, ``source`` or ``enabled``): every one of them
    raised ``missing 1 required positional argument`` before
    ``_build_from_map`` was ever reached, so the drift guard never ran the
    check it exists for. ``_synthesize_case`` below finds a value that
    argument accepts, reusing ``tests/test_command_map_parity.py``'s own
    probe-ladder search (``_split_params``/``_candidate_kwargs``) rather
    than re-implementing it -- ``to_addr``/``from_addr``/``cmd_map`` are
    supplied by the harness, so those never need synthesising here.
    """

    @staticmethod
    def _synthesize_case(builder: Any, cmd_map: CommandMap) -> dict[str, Any]:
        """First argument combination *builder* accepts, beyond the harness.

        Probed with ``_build_from_map`` already faked out (see the caller),
        so a builder's own validation (e.g. ``config.py:
        set_data_off_mod_input``'s ``0 <= source <= 5``) is what is being
        satisfied here, never a real map lookup -- the probe *cmd_map*'s
        contents never matter to this search, only its type.

        Synthesises against ``inspect.unwrap(builder)``, not *builder*
        itself -- a full-suite-only failure (18 deterministic failures,
        standalone file green) traced to this: when one of the five files
        that rebind the shared ``rigplane.commands`` namespace into
        ``functools.partial`` objects (``bind_default_addr_module``, this
        class's docstring) collects first, ``_exposed_builders`` enumerates
        a partial, not the raw function. Its ``inspect.signature`` is
        identical to the raw function's (verified directly), so
        ``_split_params`` is unaffected -- but ``functools.partial`` has no
        ``__globals__`` (``update_wrapper`` never copies it), so
        ``_values_for``'s ``eval(param.annotation, fn.__globals__)`` raises,
        its bare ``except Exception: return ()`` turns that into an empty
        probe ladder for every required parameter, and
        ``_candidate_kwargs`` yields nothing at all -- the loop below never
        calls *builder* even once. ``inspect.unwrap`` follows the
        ``__wrapped__`` chain ``update_wrapper`` sets, however many
        collected files stacked a layer each, back to a function with a
        real ``__globals__``.
        """
        target = inspect.unwrap(builder)
        required, _optional = _split_params(target)
        for kwargs in _candidate_kwargs(target, required):
            try:
                builder(to_addr=0x94, cmd_map=cmd_map, **kwargs)
            except Exception:
                continue
            return kwargs
        raise AssertionError(
            f"{builder.__qualname__}: no synthesized argument combination was "
            "accepted, even with _build_from_map faked out"
        )

    @pytest.mark.parametrize(
        "cmd_map", _PROBE_MAPS, ids=["empty_map", "set_speech_map"]
    )
    @pytest.mark.parametrize(
        "case", _exposed_builders(), ids=[c[0] for c in _exposed_builders()]
    )
    def test_exposed_key_matches_build_from_map_argument(
        self,
        case: tuple[str, Any],
        cmd_map: CommandMap,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _name, builder = case
        captured: dict[str, str] = {}

        def _fake_build_from_map(
            _cmd_map: CommandMap, key: str, *args: Any, **kwargs: Any
        ) -> bytes:
            captured["key"] = key
            return b""

        # Patch the defining module object directly rather than through
        # monkeypatch's dotted-string resolution: a backward-compat alias
        # such as ``speech.py``'s ``speech = get_speech`` rebinds the
        # package-level attribute ``rigplane.commands.speech`` to the
        # function itself, shadowing the submodule of the same name that
        # dotted-string lookup would otherwise walk through.
        defining_module = sys.modules[builder.__module__]
        monkeypatch.setattr(defining_module, "_build_from_map", _fake_build_from_map)
        case_kwargs = self._synthesize_case(builder, cmd_map)
        builder(to_addr=0x94, cmd_map=cmd_map, **case_kwargs)
        assert "key" in captured, (
            f"{builder.__qualname__} did not call _build_from_map with cmd_map set"
        )
        assert captured["key"] == builder.cmd_map_key(cmd_map)


def test_ptt_off_is_also_covered_directly() -> None:
    """Sanity check the dynamic enumeration actually found both ptt builders.

    Checked by name, not object identity: by the time this runs, another
    test file collected in the same session may already have rebound
    ``rigplane.commands.ptt_off`` into a ``functools.partial`` (see
    ``_exposed_builders``'s docstring), so comparing against the raw
    ``ptt_off`` imported at the top of this module would be fragile to
    collection order rather than testing anything real.
    """
    names = {name for name, _ in _exposed_builders()}
    assert {"get_speech", "ptt_on", "ptt_off"} <= names
