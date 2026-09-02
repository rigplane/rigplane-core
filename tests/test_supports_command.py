"""Tests for profile-derived ``Radio.supports_command`` results."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
from collections.abc import Mapping
from pathlib import Path

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands import get_unselected_mode
from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.radio import CoreRadio
from rigplane.runtime._scope_runtime import ScopeRuntimeMixin
from rigplane.runtime.callable_support import (
    AUDIO_OPERATIONS,
    BUILDER_RELATIONS,
    CALLABLE_RELATIONS,
    EXCLUDED_OPERATIONS,
    BuilderAllOf,
    _builder_command_name,
    _relation_supported,
    _validate_relations,
    supports_callable,
)
from rigplane.rig_loader import load_rig

_RIGS_DIR = Path(__file__).parents[1] / "rigs"


@pytest.fixture(scope="module")
def profiles() -> Mapping[str, RadioProfile]:
    return {
        config.model: config.to_profile()
        for config in discover_rigs(_RIGS_DIR).values()
    }


@pytest.fixture()
def ic7300_profile(profiles):
    return profiles["IC-7300"]


@pytest.fixture()
def ic7300_radio(ic7300_profile):
    return CoreRadio("127.0.0.1", profile=ic7300_profile)


# ---------------------------------------------------------------------------
# CoreRadio (base for all Icom LAN + serial backends)
# ---------------------------------------------------------------------------


_EXPECTED_COUNTS = {
    "FTX-1": (14, 18),
    "IC-705": (31, 1),
    "IC-7300": (24, 8),
    "IC-7610": (32, 0),
    "IC-9700": (32, 0),
    "TX-500": (11, 21),
    "X6100": (15, 17),
    "X6200": (15, 17),
}
_ALIASES = {
    "disable_scope": ScopeRuntimeMixin,
    "get_scope_dual": ScopeRuntimeMixin,
    "get_scope_receiver": ScopeRuntimeMixin,
    "set_scope_receiver": ScopeRuntimeMixin,
    **{
        name: CoreRadio
        for name in (
            "get_alc_meter",
            "get_antenna_1",
            "get_antenna_2",
            "get_attenuator_level",
            "get_rx_antenna_ant1",
            "get_swr_meter",
            "send_cw_text",
            "set_antenna_1",
            "set_antenna_2",
            "set_attenuator_level",
            "set_rx_antenna_ant1",
            "stop_cw_text",
        )
    },
}


def _without(profile: RadioProfile, name: str) -> RadioProfile:
    return dataclasses.replace(profile, command_names=profile.command_names - {name})


def _command_calls(owner, name: str) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(owner, name))))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_commands"
    }


def _assert_parity(relations) -> None:
    by_name = {relation.operation: relation for relation in relations}
    for name, owner in _ALIASES.items():
        assert _command_calls(owner, name) == {
            builder.__name__ for builder in by_name[name].builders
        }
    scope = by_name["enable_scope"].builders
    assert {b.__name__ for b in scope} == _command_calls(
        ScopeRuntimeMixin, "enable_scope"
    )
    assert by_name["capture_scope_frame"].builders == scope
    assert by_name["capture_scope_frames"].builders == scope
    assert "capture_scope_frames" in inspect.getsource(
        ScopeRuntimeMixin.capture_scope_frame
    )
    assert "enable_scope" in inspect.getsource(ScopeRuntimeMixin.capture_scope_frames)
    assert {b.__name__ for b in by_name["get_mode_info"].builders} == {"get_mode"}
    assert _command_calls(CoreRadio, "_get_mode_info_main") == {"get_mode"}
    assert _command_calls(CoreRadio, "_get_unselected_mode") == {"get_unselected_mode"}
    dual_watch = by_name["set_dual_watch"].builders
    delegate_module = inspect.getmodule(dual_watch[0])
    assert delegate_module is not None
    delegate = inspect.getsource(delegate_module.set_dual_watch)
    assert {b.__name__ for b in dual_watch} == {
        "set_dual_watch_on",
        "set_dual_watch_off",
    }
    assert all(b.__name__ in delegate for b in dual_watch)


class TestProfileDerivedSupport:
    def test_registry_partition_and_all_profile_matrix(self, profiles):
        assert len(CALLABLE_RELATIONS) == 32
        assert len(BUILDER_RELATIONS) == 21
        assert {r.operation for r in BUILDER_RELATIONS if r.kind == "alias"} == set(
            _ALIASES
        )
        assert len(AUDIO_OPERATIONS) == 10
        assert set(profiles) == set(_EXPECTED_COUNTS)
        for model, profile in profiles.items():
            outcomes = [supports_callable(profile, name) for name in CALLABLE_RELATIONS]
            assert (sum(outcomes), len(outcomes) - sum(outcomes)) == _EXPECTED_COUNTS[
                model
            ]
            assert all(
                supports_callable(profile, name) for name in profile.command_names
            )
            assert not any(
                supports_callable(profile, name)
                for name in profile.absent_command_names
            )

    def test_exclusions_unknowns_and_method_presence(self, profiles):
        assert EXCLUDED_OPERATIONS == {
            "set_scope_dual",
            "get_mode_enum",
            "get_memory_mode",
        }
        assert set(CALLABLE_RELATIONS).isdisjoint(EXCLUDED_OPERATIONS)
        for profile in profiles.values():
            assert not any(
                supports_callable(profile, name)
                for name in (*EXCLUDED_OPERATIONS, "do_magic", "", "GET_FREQ")
            )
        for name in (*CALLABLE_RELATIONS, *EXCLUDED_OPERATIONS):
            assert hasattr(CoreRadio, name)

    def test_core_instances_delegate_for_all_six_civ_profiles(self, profiles):
        civ_profiles = [p for p in profiles.values() if p.protocol_type == "civ"]
        assert len(civ_profiles) == 6
        for profile in civ_profiles:
            radio = CoreRadio("127.0.0.1", profile=profile)
            assert all(
                radio.supports_command(name) == supports_callable(profile, name)
                for name in CALLABLE_RELATIONS
            )

    def test_explicit_absence_precedes_direct_and_derived(self, ic7300_profile):
        for name in ("get_freq", "capture_scope_frame"):
            overlap = dataclasses.replace(
                ic7300_profile,
                absent_command_names=ic7300_profile.absent_command_names | {name},
            )
            assert not supports_callable(overlap, name)


class TestRelationMutations:
    @pytest.mark.parametrize(
        "relation",
        [r for r in BUILDER_RELATIONS if r.kind == "alias"],
        ids=lambda r: r.operation,
    )
    def test_each_alias_requires_its_builder_key(self, profiles, relation):
        profile = next(
            p
            for p in profiles.values()
            if _relation_supported(relation, p, set(CALLABLE_RELATIONS))
        )
        key = _builder_command_name(relation.builders[0], profile)
        assert key and not supports_callable(_without(profile, key), relation.operation)

    def test_scope_and_dual_watch_require_every_key(self, profiles):
        cases = [
            (
                {"enable_scope", "capture_scope_frame", "capture_scope_frames"},
                "IC-7300",
            ),
            ({"set_dual_watch"}, "IC-7610"),
        ]
        for operations, model in cases:
            profile = profiles[model]
            for operation in operations:
                relation = CALLABLE_RELATIONS[operation]
                assert isinstance(relation, BuilderAllOf)
                keys = {_builder_command_name(b, profile) for b in relation.builders}
                for key in keys:
                    assert key and not supports_callable(
                        _without(profile, key), operation
                    )

    def test_mode_info_base_and_sub_are_distinct(self, profiles):
        single, dual = profiles["IC-7300"], profiles["IC-7610"]
        base = CALLABLE_RELATIONS["get_mode_info"]
        assert isinstance(base, BuilderAllOf)
        base_key = _builder_command_name(base.builders[0], single)
        assert base_key == "get_mode"
        assert not supports_callable(_without(single, base_key), "get_mode_info")
        sub_key = _builder_command_name(get_unselected_mode, dual)
        assert sub_key == "get_unselected_mode" and dual.supports_receiver(1)
        dual_without_sub = _without(dual, sub_key)
        assert supports_callable(dual_without_sub, "get_mode_info")
        assert not dual_without_sub.supports_command(sub_key)

    def test_audio_and_protocol_facts_are_independent(self, profiles):
        profile = profiles["IC-7300"]
        assert len(AUDIO_OPERATIONS) == 10
        no_audio = dataclasses.replace(
            profile, capabilities=profile.capabilities - {"audio"}
        )
        assert all(supports_callable(profile, name) for name in AUDIO_OPERATIONS)
        assert not any(supports_callable(no_audio, name) for name in AUDIO_OPERATIONS)
        for protocol in ("yaesu_cat", "kenwood_cat"):
            changed = dataclasses.replace(profile, protocol_type=protocol)
            assert not supports_callable(changed, "send_civ")
            assert all(
                supports_callable(changed, name) == supports_callable(profile, name)
                for name in set(CALLABLE_RELATIONS) - {"send_civ"}
            )

    def test_builder_metadata_and_ast_parity_fail_closed(self, ic7300_profile):
        _assert_parity(BUILDER_RELATIONS)
        swr = next(r for r in BUILDER_RELATIONS if r.operation == "get_swr_meter")
        alc = next(r for r in BUILDER_RELATIONS if r.operation == "get_alc_meter")
        mutated = tuple(
            dataclasses.replace(r, builders=alc.builders) if r is swr else r
            for r in BUILDER_RELATIONS
        )
        with pytest.raises(AssertionError):
            _assert_parity(mutated)

        def missing_metadata():
            return None

        invalid = BuilderAllOf("invalid", (missing_metadata,), "alias")
        with pytest.raises(ValueError, match="cmd_map_key"):
            _validate_relations((invalid,))
        missing_metadata.cmd_map_key = lambda command_map: "get_mode_info"
        assert not _relation_supported(invalid, ic7300_profile, set(CALLABLE_RELATIONS))

    def test_resolver_has_no_model_or_vendor_branch(self):
        module = inspect.getmodule(supports_callable)
        assert module is not None
        tree = ast.parse(inspect.getsource(module))
        fields = {
            n.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "profile"
        }
        assert fields.isdisjoint({"model", "id"})
        assert not any(
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and any(v in n.value.lower() for v in ("icom", "yaesu", "xiegu"))
            for n in ast.walk(tree)
        )


# ---------------------------------------------------------------------------
# YaesuCatRadio
# ---------------------------------------------------------------------------


@pytest.fixture()
def ftx1_config():
    return load_rig(_RIGS_DIR / "ftx1.toml")


@pytest.fixture()
def yaesu_radio(ftx1_config):
    return YaesuCatRadio("/dev/null", profile=ftx1_config)


class TestYaesuSupportsCommand:
    """YaesuCatRadio.supports_command delegates to _has_command."""

    def test_defined_commands_return_true(self, yaesu_radio):
        for cmd in (
            "get_freq",
            "set_freq",
            "get_mode",
            "set_mode",
            "set_ptt",
            "get_s_meter",
            "get_af_level",
            "set_af_level",
        ):
            assert yaesu_radio.supports_command(cmd), (
                f"{cmd} should be supported on FTX-1"
            )

    def test_undefined_commands_return_false(self, yaesu_radio):
        for cmd in ("do_magic", "fly_to_moon", "get_coffee", ""):
            assert not yaesu_radio.supports_command(cmd), (
                f"{cmd!r} should NOT be supported on FTX-1"
            )

    def test_matches_has_command(self, yaesu_radio, ftx1_config):
        """supports_command must agree with _has_command for every TOML key."""
        for name in ftx1_config.commands:
            assert yaesu_radio.supports_command(name) == yaesu_radio._has_command(name)


# ---------------------------------------------------------------------------
# Serial Icom backends (all inherit CoreRadio)
# ---------------------------------------------------------------------------


class TestSerialBackendsSupportsCommand:
    """Serial backends inherit supports_command from CoreRadio."""

    def test_ic7300_serial(self):
        from rigplane.backends.ic7300.serial import Ic7300SerialRadio

        assert hasattr(Ic7300SerialRadio, "supports_command")
        assert Ic7300SerialRadio.supports_command is CoreRadio.supports_command

    def test_ic705_serial(self):
        from rigplane.backends.ic705.serial import Ic705SerialRadio

        assert hasattr(Ic705SerialRadio, "supports_command")
        assert Ic705SerialRadio.supports_command is CoreRadio.supports_command

    def test_ic9700_serial(self):
        from rigplane.backends.ic9700.serial import Ic9700SerialRadio

        assert hasattr(Ic9700SerialRadio, "supports_command")
        assert Ic9700SerialRadio.supports_command is CoreRadio.supports_command

    def test_icom7610_serial(self):
        from rigplane.backends.icom7610.serial import Icom7610SerialRadio

        assert hasattr(Icom7610SerialRadio, "supports_command")
        assert Icom7610SerialRadio.supports_command is CoreRadio.supports_command


# ---------------------------------------------------------------------------
# DspControlCapable structural conformance — issue #1102
# ---------------------------------------------------------------------------


class TestDspControlCapableNotchExtension:
    """Both backends carry the extended notch surface (set/get_notch_filter)."""

    def test_core_radio_exposes_notch_filter_methods(self):
        for name in ("set_notch_filter", "get_notch_filter"):
            assert hasattr(CoreRadio, name), (
                f"CoreRadio must implement {name} (DspControlCapable, #1102)"
            )

    def test_yaesu_cat_radio_exposes_notch_filter_methods(self):
        for name in ("set_notch_filter", "get_notch_filter"):
            assert hasattr(YaesuCatRadio, name), (
                f"YaesuCatRadio must implement {name} (DspControlCapable, #1102)"
            )

    def test_notch_filter_signature_accepts_receiver(self):
        """set/get_notch_filter must accept the receiver kwarg on both backends."""
        import inspect

        for cls in (CoreRadio, YaesuCatRadio):
            for name in ("set_notch_filter", "get_notch_filter"):
                sig = inspect.signature(getattr(cls, name))
                assert "receiver" in sig.parameters, (
                    f"{cls.__name__}.{name} must accept 'receiver' kwarg"
                )
