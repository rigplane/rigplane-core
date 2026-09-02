#!/usr/bin/env bash
set -euo pipefail

source_sha=586fe412ae36c57bc85c157f81fcf09bbf1c72ab
printf 'source_sha=%s\n' "$source_sha"
git diff --exit-code "$source_sha" -- src/rigplane/runtime/radio.py tests/test_radio_extended.py

tests=(
  tests/test_radio_extended.py::TestCW
  tests/test_commands_extended.py::TestCw
  tests/test_command_map_integration.py::TestSetterParity::test_stop_cw
)

run_suite() {
  local cache_dir
  cache_dir="$(mktemp -d)"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX="$cache_dir" \
    uv run pytest "${tests[@]}" -q --tb=short --color=no
}

run_suite

git apply <<'PATCH'
diff --git a/src/rigplane/runtime/radio.py b/src/rigplane/runtime/radio.py
--- a/src/rigplane/runtime/radio.py
+++ b/src/rigplane/runtime/radio.py
@@ -4917,7 +4917,7 @@ class CoreRadio(ScopeRuntimeMixin, AudioRuntimeMixin, DualRxRuntimeMixin):
         self._check_connected()
         civ = self._commands.stop_cw(to_addr=self._radio_addr)
         resp = await self._send_civ_raw(civ, priority=Priority.IMMEDIATE)
-        if resp is not None and parse_ack_nak(resp) is False:
+        if False:
             raise CommandError("Radio rejected CW stop")
PATCH

set +e
cache_dir="$(mktemp -d)"
PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX="$cache_dir" \
  uv run pytest "${tests[@]}" -q --tb=short --color=no > mutant-output.txt 2>&1
result=$?
set -e
cat mutant-output.txt
test "$result" -ne 0
test "$(grep -c '^FAILED ' mutant-output.txt)" -eq 1
grep -F 'FAILED tests/test_radio_extended.py::TestCW::test_stop_cw_nak' mutant-output.txt

git apply -R <<'PATCH'
diff --git a/src/rigplane/runtime/radio.py b/src/rigplane/runtime/radio.py
--- a/src/rigplane/runtime/radio.py
+++ b/src/rigplane/runtime/radio.py
@@ -4917,7 +4917,7 @@ class CoreRadio(ScopeRuntimeMixin, AudioRuntimeMixin, DualRxRuntimeMixin):
         self._check_connected()
         civ = self._commands.stop_cw(to_addr=self._radio_addr)
         resp = await self._send_civ_raw(civ, priority=Priority.IMMEDIATE)
-        if resp is not None and parse_ack_nak(resp) is False:
+        if False:
             raise CommandError("Radio rejected CW stop")
PATCH

git diff --exit-code -- src/rigplane/runtime/radio.py
run_suite
