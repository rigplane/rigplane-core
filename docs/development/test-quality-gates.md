# Test-quality gates

Self-checking meta-tests that keep the test suite's own quality from
regressing. Each gate scans the `tests/` tree with the `ast` module and
compares the offenders it finds against a committed ledger: an offender
missing from the ledger fails CI until it is fixed or the ledger is
regenerated in a reviewable commit, and a ledger line whose file no
longer offends also fails, so the ledgers only shrink.

## Spec-less mocks standing in for project objects (MOR-3238)

`tests/test_meta_mock_specs.py: test_no_unlisted_specless_project_mock`
fails when a test file constructs `MagicMock()`/`AsyncMock()`/`Mock()`
without `spec=`/`spec_set=` and uses it where a radio, backend or
transport is expected — assigned to such a name, assigned to such an
attribute, passed as such a keyword parameter, or returned from a
fixture factory with such a name. The full counting rule and the
regeneration command are in the module docstring; today's offenders are
listed in `tests/mock_spec_uncovered.txt`.

## Assertion-free test functions (MOR-3239)

`tests/test_meta_assertions.py: test_no_unlisted_assertion_free_test`
fails when a test file contains a `test_*` function that asserts
nothing real: no `assert` statement, no `raises`/`approx` call, and no
assert-prefixed call other than mock-verification
(`assert_called*`/`assert_awaited*`). The full counting rule and the
regeneration command are in the module docstring; today's offenders are
listed in `tests/assertion_free_uncovered.txt`.

