"""Tests for rigplane.validation.comparison.compute_comparison_dimensions.

TDD: tests written first, confirmed red, then implementation added.
"""

from __future__ import annotations

import pytest

from rigplane.validation.schema import CapabilityDeclaration, CheckStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUPPORTED = CapabilityDeclaration.SUPPORTED.value
_UNSUPPORTED_PENDING = CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE.value
_MANUAL_REQUIRED_DECL = CapabilityDeclaration.MANUAL_REQUIRED.value

_PASS = CheckStatus.PASS.value
_FAIL = CheckStatus.FAIL.value
_SKIP = CheckStatus.SKIP.value
_UNSUPPORTED = CheckStatus.UNSUPPORTED.value
_MANUAL_REQUIRED_STATUS = CheckStatus.MANUAL_REQUIRED.value
_BLOCKED = CheckStatus.BLOCKED.value


def _artifact(checks: list[tuple[str, str, str]]) -> dict:
    """Build a minimal artifact dict from (check_id, declaration, status) triples."""
    return {
        "levels": [
            {
                "checks": [
                    {"check_id": cid, "declaration": decl, "status": status}
                    for cid, decl, status in checks
                ]
            }
        ]
    }


# ---------------------------------------------------------------------------
# Import smoke
# ---------------------------------------------------------------------------


def test_import():
    import rigplane.validation as _val
    import rigplane.validation.comparison as _comp

    assert hasattr(_val, "compute_comparison_dimensions")
    assert hasattr(_comp, "compute_comparison_dimensions")


# ---------------------------------------------------------------------------
# profile_vs_reality
# ---------------------------------------------------------------------------


class TestProfileVsReality:
    def _get(self, checks_native: list[tuple[str, str, str]]) -> dict:
        from rigplane.validation.comparison import compute_comparison_dimensions

        result = compute_comparison_dimensions(_artifact(checks_native), _artifact([]))
        return result["profile_vs_reality"]

    def test_supported_pass_counts_agree(self):
        dim = self._get([("rf_gain", _SUPPORTED, _PASS)])
        assert dim["agree"] == 1
        assert dim["differ"] == 0
        assert dim["differing"] == []

    def test_supported_fail_counts_differ(self):
        dim = self._get([("mode.set", _SUPPORTED, _FAIL)])
        assert dim["agree"] == 0
        assert dim["differ"] == 1
        assert "mode.set" in dim["differing"]

    def test_supported_unsupported_counts_differ(self):
        dim = self._get([("att", _SUPPORTED, _UNSUPPORTED)])
        assert dim["differ"] == 1
        assert "att" in dim["differing"]

    def test_unsupported_pending_unsupported_counts_agree(self):
        dim = self._get([("ext.feature", _UNSUPPORTED_PENDING, _UNSUPPORTED)])
        assert dim["agree"] == 1
        assert dim["differ"] == 0
        assert dim["differing"] == []

    def test_unsupported_pending_pass_counts_differ(self):
        dim = self._get([("ext.feature", _UNSUPPORTED_PENDING, _PASS)])
        assert dim["differ"] == 1
        assert "ext.feature" in dim["differing"]

    def test_manual_required_status_is_na(self):
        """manual_required status → na (not counted in agree/differ)."""
        dim = self._get([("tx.power", _SUPPORTED, _MANUAL_REQUIRED_STATUS)])
        assert dim["agree"] == 0
        assert dim["differ"] == 0
        assert dim["differing"] == []

    def test_skip_status_is_na(self):
        dim = self._get([("rf_gain", _SUPPORTED, _SKIP)])
        assert dim["agree"] == 0
        assert dim["differ"] == 0

    def test_blocked_status_is_na(self):
        dim = self._get([("rf_gain", _SUPPORTED, _BLOCKED)])
        assert dim["agree"] == 0
        assert dim["differ"] == 0

    def test_manual_required_decl_is_na(self):
        dim = self._get([("tx.power", _MANUAL_REQUIRED_DECL, _PASS)])
        assert dim["agree"] == 0
        assert dim["differ"] == 0

    def test_unsupported_pending_fail_is_na(self):
        """UNSUPPORTED_PENDING + fail is undefined per ADR → na."""
        dim = self._get([("ext.feature", _UNSUPPORTED_PENDING, _FAIL)])
        assert dim["agree"] == 0
        assert dim["differ"] == 0

    def test_differing_is_sorted(self):
        checks = [
            ("zzz.last", _SUPPORTED, _FAIL),
            ("aaa.first", _SUPPORTED, _FAIL),
            ("mmm.mid", _SUPPORTED, _FAIL),
        ]
        dim = self._get(checks)
        assert dim["differing"] == sorted(dim["differing"])
        assert dim["differing"] == ["aaa.first", "mmm.mid", "zzz.last"]

    def test_no_na_key_in_output(self):
        """profile_vs_reality must NOT emit an 'na' key (ADR §7.2)."""
        dim = self._get([("rf_gain", _SUPPORTED, _PASS)])
        assert "na" not in dim

    def test_output_keys(self):
        dim = self._get([])
        assert set(dim.keys()) == {"agree", "differ", "differing"}

    def test_multiple_checks_combined(self):
        checks = [
            ("rf_gain", _SUPPORTED, _PASS),  # agree
            ("mode.set", _SUPPORTED, _FAIL),  # differ
            ("att", _SUPPORTED, _UNSUPPORTED),  # differ
            ("ext.f", _UNSUPPORTED_PENDING, _UNSUPPORTED),  # agree
            ("ext.g", _UNSUPPORTED_PENDING, _PASS),  # differ
            ("tx.p", _SUPPORTED, _SKIP),  # na
        ]
        dim = self._get(checks)
        assert dim["agree"] == 2
        assert dim["differ"] == 3
        assert sorted(dim["differing"]) == ["att", "ext.g", "mode.set"]


# ---------------------------------------------------------------------------
# hamlib_vs_reality
# ---------------------------------------------------------------------------


class TestHamlibVsReality:
    def _get(self, checks_hamlib: list[tuple[str, str, str]]) -> dict:
        from rigplane.validation.comparison import compute_comparison_dimensions

        result = compute_comparison_dimensions(_artifact([]), _artifact(checks_hamlib))
        return result["hamlib_vs_reality"]

    def test_unsupported_pending_unsupported_counts_agree(self):
        dim = self._get([("discovery", _UNSUPPORTED_PENDING, _UNSUPPORTED)])
        assert dim["agree"] == 1
        assert dim["differ"] == 0

    def test_supported_pass_counts_agree(self):
        dim = self._get([("rf_gain", _SUPPORTED, _PASS)])
        assert dim["agree"] == 1
        assert dim["differ"] == 0

    def test_supported_fail_counts_differ(self):
        dim = self._get([("mode.set", _SUPPORTED, _FAIL)])
        assert dim["differ"] == 1

    def test_skip_counts_na(self):
        dim = self._get([("rf_gain", _SUPPORTED, _SKIP)])
        assert dim["na"] == 1
        assert dim["agree"] == 0
        assert dim["differ"] == 0

    def test_na_key_present(self):
        """hamlib_vs_reality MUST emit an 'na' key."""
        dim = self._get([("rf_gain", _SUPPORTED, _PASS)])
        assert "na" in dim

    def test_output_keys(self):
        dim = self._get([])
        assert set(dim.keys()) == {"agree", "differ", "na"}

    def test_na_count_accumulates(self):
        checks = [
            ("a", _SUPPORTED, _SKIP),
            ("b", _SUPPORTED, _BLOCKED),
            ("c", _SUPPORTED, _MANUAL_REQUIRED_STATUS),
            ("d", _SUPPORTED, _PASS),  # agree
        ]
        dim = self._get(checks)
        assert dim["na"] == 3
        assert dim["agree"] == 1

    def test_no_differing_list_key(self):
        """hamlib_vs_reality must NOT emit a 'differing' list key."""
        dim = self._get([("mode.set", _SUPPORTED, _FAIL)])
        assert "differing" not in dim


# ---------------------------------------------------------------------------
# cross_impl
# ---------------------------------------------------------------------------


class TestCrossImpl:
    def _get(
        self,
        native_checks: list[tuple[str, str, str]],
        hamlib_checks: list[tuple[str, str, str]],
    ) -> dict:
        from rigplane.validation.comparison import compute_comparison_dimensions

        result = compute_comparison_dimensions(
            _artifact(native_checks), _artifact(hamlib_checks)
        )
        return result["cross_impl"]

    def test_native_pass_hamlib_fail_counts_differ(self):
        dim = self._get(
            [("mode.set", _SUPPORTED, _PASS)],
            [("mode.set", _SUPPORTED, _FAIL)],
        )
        assert dim["differ"] == 1
        assert dim["agree"] == 0

    def test_both_pass_counts_agree(self):
        dim = self._get(
            [("rf_gain", _SUPPORTED, _PASS)],
            [("rf_gain", _SUPPORTED, _PASS)],
        )
        assert dim["agree"] == 1
        assert dim["differ"] == 0

    def test_hamlib_unsupported_is_na(self):
        """If either status is unsupported/skip/manual_required/blocked → na."""
        dim = self._get(
            [("rf_gain", _SUPPORTED, _PASS)],
            [("rf_gain", _SUPPORTED, _UNSUPPORTED)],
        )
        assert dim["na"] >= 1
        assert dim["differ"] == 0

    def test_check_id_only_in_native_is_na(self):
        dim = self._get(
            [("native.only", _SUPPORTED, _PASS)],
            [],
        )
        assert dim["na"] >= 1

    def test_check_id_only_in_hamlib_is_na(self):
        dim = self._get(
            [],
            [("hamlib.only", _SUPPORTED, _PASS)],
        )
        assert dim["na"] >= 1

    def test_output_keys(self):
        dim = self._get([], [])
        assert set(dim.keys()) == {"agree", "differ", "na"}

    def test_no_differing_list_key(self):
        """cross_impl must NOT emit a 'differing' key in v1."""
        dim = self._get(
            [("mode.set", _SUPPORTED, _PASS)],
            [("mode.set", _SUPPORTED, _FAIL)],
        )
        assert "differing" not in dim

    def test_native_fail_hamlib_fail_counts_agree(self):
        dim = self._get(
            [("rf_gain", _SUPPORTED, _FAIL)],
            [("rf_gain", _SUPPORTED, _FAIL)],
        )
        assert dim["agree"] == 1

    def test_skip_in_native_is_na(self):
        dim = self._get(
            [("rf_gain", _SUPPORTED, _SKIP)],
            [("rf_gain", _SUPPORTED, _PASS)],
        )
        assert dim["na"] >= 1
        assert dim["differ"] == 0


# ---------------------------------------------------------------------------
# Combined realistic case
# ---------------------------------------------------------------------------


class TestCombinedRealistic:
    """
    native:  rf_gain pass, mode.set pass, discovery SUPPORTED+pass
    hamlib:  rf_gain pass, mode.set fail, discovery UNSUPPORTED_PENDING+unsupported
    """

    @pytest.fixture
    def result(self):
        from rigplane.validation.comparison import compute_comparison_dimensions

        native = _artifact(
            [
                ("rf_gain", _SUPPORTED, _PASS),
                ("mode.set", _SUPPORTED, _PASS),
                ("discovery", _SUPPORTED, _PASS),
            ]
        )
        hamlib = _artifact(
            [
                ("rf_gain", _SUPPORTED, _PASS),
                ("mode.set", _SUPPORTED, _FAIL),
                ("discovery", _UNSUPPORTED_PENDING, _UNSUPPORTED),
            ]
        )
        return compute_comparison_dimensions(native, hamlib)

    def test_cross_impl_differ_includes_mode_set(self, result):
        # mode.set: native pass vs hamlib fail → differ
        assert result["cross_impl"]["differ"] >= 1

    def test_profile_vs_reality_native_clean(self, result):
        # native: rf_gain+mode.set+discovery all SUPPORTED+pass → all agree, no differ
        pvr = result["profile_vs_reality"]
        assert pvr["differ"] == 0
        assert pvr["agree"] == 3

    def test_hamlib_vs_reality_discovery_agree(self, result):
        # hamlib discovery UNSUPPORTED_PENDING+unsupported → agree
        hvr = result["hamlib_vs_reality"]
        assert hvr["agree"] >= 1

    def test_hamlib_vs_reality_mode_set_differ(self, result):
        # hamlib mode.set SUPPORTED+fail → differ
        hvr = result["hamlib_vs_reality"]
        assert hvr["differ"] >= 1

    def test_cross_impl_rf_gain_agree(self, result):
        # rf_gain: both pass → agree
        assert result["cross_impl"]["agree"] >= 1

    def test_cross_impl_discovery_na(self, result):
        # discovery: hamlib unsupported → na
        assert result["cross_impl"]["na"] >= 1


# ---------------------------------------------------------------------------
# Empty artifacts
# ---------------------------------------------------------------------------


class TestEmptyArtifacts:
    def test_all_zeros_on_empty(self):
        from rigplane.validation.comparison import compute_comparison_dimensions

        result = compute_comparison_dimensions(_artifact([]), _artifact([]))
        assert result["profile_vs_reality"] == {
            "agree": 0,
            "differ": 0,
            "differing": [],
        }
        assert result["hamlib_vs_reality"] == {"agree": 0, "differ": 0, "na": 0}
        assert result["cross_impl"] == {"agree": 0, "differ": 0, "na": 0}

    def test_missing_levels_key(self):
        """Artifacts missing 'levels' key should not raise — return zeros."""
        from rigplane.validation.comparison import compute_comparison_dimensions

        result = compute_comparison_dimensions({}, {})
        assert result["profile_vs_reality"]["agree"] == 0
        assert result["hamlib_vs_reality"]["agree"] == 0
        assert result["cross_impl"]["agree"] == 0
