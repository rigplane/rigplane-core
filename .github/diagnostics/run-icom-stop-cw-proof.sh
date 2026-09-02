#!/usr/bin/env bash
set -euo pipefail

source_sha=e449091b88696fa7eee3d505c41b1b0b6b2c5c3b
artifact_dir=icom-stop-cw-proof
mkdir -p "$artifact_dir"
source_files=(
  src/rigplane/runtime/radio.py
  tests/test_radio_extended.py
  .claude/audits/README.md
  .claude/audits/2026-09-02-mechanism-audit-icom-stop-cw.md
)
tests=(
  tests/test_radio_extended.py::TestCW
  tests/test_commands_extended.py::TestCw
  tests/test_command_map_integration.py::TestSetterParity::test_stop_cw
)

printf 'source_sha=%s\n' "$source_sha" | tee "$artifact_dir/source.txt"
git diff --exit-code "$source_sha" -- "${source_files[@]}" \
  | tee "$artifact_dir/source-diff-before.txt"

run_suite() {
  local cache_dir
  cache_dir="$(mktemp -d)"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX="$cache_dir" \
    uv run pytest "${tests[@]}" -q --tb=short --color=no
}

uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run lint-imports
uv run mypy --strict src/rigplane/web
run_suite | tee "$artifact_dir/green-before.txt"

mutant_applied=0
restore_mutant() {
  if [ "$mutant_applied" -eq 1 ]; then
    git apply -R "$artifact_dir/mutant.patch"
    mutant_applied=0
  fi
}

git apply <<'PATCH'
diff --git a/src/rigplane/runtime/radio.py b/src/rigplane/runtime/radio.py
--- a/src/rigplane/runtime/radio.py
+++ b/src/rigplane/runtime/radio.py
@@ -4917,5 +4917,5 @@ class CoreRadio(ScopeRuntimeMixin, AudioRuntimeMixin, DualRxRuntimeMixin):
         self._check_connected()
         civ = self._commands.stop_cw(to_addr=self._radio_addr)
         resp = await self._send_civ_raw(civ, priority=Priority.IMMEDIATE)
-        if resp is not None and parse_ack_nak(resp) is False:
+        if False:
             raise CommandError("Radio rejected CW stop")
PATCH

mutant_applied=1
trap restore_mutant EXIT
git diff -- src/rigplane/runtime/radio.py | tee "$artifact_dir/mutant.patch"
set +e
cache_dir="$(mktemp -d)"
PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX="$cache_dir" \
  uv run pytest "${tests[@]}" -q --tb=short --color=no > "$artifact_dir/mutant-output.txt" 2>&1
result=$?
set -e
restore_mutant
git diff --exit-code "$source_sha" -- "${source_files[@]}" \
  | tee "$artifact_dir/source-diff-restored.txt"
git status --short --untracked-files=no > "$artifact_dir/source-status-restored.txt"

cat "$artifact_dir/mutant-output.txt"
test "$result" -eq 1
test "$(grep -c '^FAILED ' "$artifact_dir/mutant-output.txt")" -eq 1
grep -F 'FAILED tests/test_radio_extended.py::TestCW::test_stop_cw_nak' \
  "$artifact_dir/mutant-output.txt"
trap - EXIT
run_suite | tee "$artifact_dir/green-restored.txt"
