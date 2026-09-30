"""MOR-3064: optional ``[identity].expected_ids`` profile metadata.

The identity-gate contract: tokens are opaque, protocol-native response
strings (CI-V uppercase payload hex after the 19 00 echo, Yaesu four-digit
ID payload) — never ``civ_addr``, ``hamlib_model_id``, or firmware
versions. The loader validates shape only; callers supply their backend's
canonical spelling, which is preserved exactly.
"""

import textwrap
from pathlib import Path

import pytest

from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import RigLoadError, load_rig


def _write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "test.toml"
    path.write_text(textwrap.dedent(content))
    return path


def _minimal_toml(identity: str = "", top_level: str = "") -> str:
    return f"""
    {top_level}
    [radio]
    id = "identity_schema_test"
    model = "IDENTITY-SCHEMA-TEST"
    civ_addr = 0x94
    receiver_count = 1
    has_lan = false
    has_wifi = false

    [capabilities]
    features = ["audio"]

    [modes]
    list = ["USB"]

    [filters]
    list = ["FIL1"]

    [vfo]
    scheme = "ab"
    {identity}
    """


class TestLoaderRoundtrip:
    def test_loads_expected_ids_into_config_and_profile(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            _minimal_toml(
                """
                [identity]
                expected_ids = ["94", "0840"]
                """
            ),
        )
        config = load_rig(path)
        assert config.expected_identity_ids == ("94", "0840")
        profile = config.to_profile()
        assert profile.expected_identity_ids == ("94", "0840")

    def test_absent_identity_section_gives_empty_tuple(self, tmp_path: Path) -> None:
        config = load_rig(_write_toml(tmp_path, _minimal_toml()))
        assert config.expected_identity_ids == ()
        assert config.to_profile().expected_identity_ids == ()

    def test_empty_array_is_explicitly_allowed(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            _minimal_toml(
                """
                [identity]
                expected_ids = []
                """
            ),
        )
        assert load_rig(path).expected_identity_ids == ()


class TestStorage:
    def test_stored_as_immutable_tuple(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            _minimal_toml(
                """
                [identity]
                expected_ids = ["94"]
                """
            ),
        )
        ids = load_rig(path).expected_identity_ids
        assert isinstance(ids, tuple)
        with pytest.raises((AttributeError, TypeError)):
            ids[0] = "08"  # type: ignore[index]

    def test_preserves_leading_zero_and_case(self, tmp_path: Path) -> None:
        path = _write_toml(
            tmp_path,
            _minimal_toml(
                """
                [identity]
                expected_ids = ["0840", "94aF"]
                """
            ),
        )
        assert load_rig(path).expected_identity_ids == ("0840", "94aF")

    def test_hand_built_profile_defaults_to_empty_tuple(self) -> None:
        profile = RadioProfile(
            id="t",
            model="T",
            civ_addr=0x94,
            receiver_count=1,
            capabilities=frozenset({"audio"}),
            cmd29_routes=frozenset(),
        )
        assert profile.expected_identity_ids == ()


class TestRejection:
    def _load(self, tmp_path: Path, toml: str) -> None:
        load_rig(_write_toml(tmp_path, toml))

    def test_rejects_non_table_section(self, tmp_path: Path) -> None:
        # A scalar `identity = "94"` after [vfo] would be a nested vfo key,
        # so the top-level spelling is injected before the first table.
        with pytest.raises(RigLoadError, match=r"\[identity\] must be a table"):
            self._load(
                tmp_path,
                _minimal_toml(top_level='identity = "94"'),
            )

    def test_rejects_unknown_key(self, tmp_path: Path) -> None:
        with pytest.raises(RigLoadError, match="unknown key"):
            self._load(
                tmp_path,
                _minimal_toml(
                    """
                    [identity]
                    expected_ids = ["94"]
                    extra = 1
                    """
                ),
            )

    def test_rejects_bare_string(self, tmp_path: Path) -> None:
        with pytest.raises(RigLoadError, match="array of strings"):
            self._load(
                tmp_path,
                _minimal_toml(
                    """
                    [identity]
                    expected_ids = "94"
                    """
                ),
            )

    @pytest.mark.parametrize("bad", ["94", "true", "3.5"])
    def test_rejects_non_string_entries(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(RigLoadError, match="must be a string"):
            self._load(
                tmp_path,
                _minimal_toml(
                    f"""
                    [identity]
                    expected_ids = [{bad}]
                    """
                ),
            )

    def test_rejects_empty_string_entry(self, tmp_path: Path) -> None:
        with pytest.raises(RigLoadError, match="must not be empty"):
            self._load(
                tmp_path,
                _minimal_toml(
                    """
                    [identity]
                    expected_ids = [""]
                    """
                ),
            )

    @pytest.mark.parametrize("bad", [" 94", "94 ", "9 4", "94\t"])
    def test_rejects_whitespace_entries(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(RigLoadError, match="whitespace or control"):
            self._load(
                tmp_path,
                _minimal_toml(
                    f"""
                    [identity]
                    expected_ids = ["{bad}"]
                    """
                ),
            )

    def test_rejects_control_character_entry(self, tmp_path: Path) -> None:
        with pytest.raises(RigLoadError, match="whitespace or control"):
            self._load(
                tmp_path,
                _minimal_toml(
                    """
                    [identity]
                    expected_ids = ["94\\u0007"]
                    """
                ),
            )

    def test_rejects_duplicates(self, tmp_path: Path) -> None:
        with pytest.raises(RigLoadError, match="duplicates"):
            self._load(
                tmp_path,
                _minimal_toml(
                    """
                    [identity]
                    expected_ids = ["94", "94"]
                    """
                ),
            )
