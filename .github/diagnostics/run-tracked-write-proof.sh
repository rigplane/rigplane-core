#!/usr/bin/env bash
set -euo pipefail

base_sha=2de582f352e70103c575bae0b7326172b98c391c
source_sha=13195f5210827dbdeab7c861dfb835f309b78308
artifact_dir=tracked-write-proof
base_dir="$(mktemp -d)"
source_files=(src/rigplane/core/transport.py tests/test_transport.py)
guard_suite=tests/test_transport.py::TestTrackedWriteGuard
mkdir -p "$artifact_dir"

mutant_applied=0
mutation_name=""
restore_mutant() {
  if [ "$mutant_applied" -eq 1 ]; then
    git apply -R "$artifact_dir/$mutation_name.diff"
    mutant_applied=0
  fi
}
cleanup() {
  restore_mutant
  git worktree remove "$base_dir" || true
}
trap cleanup EXIT

printf 'base_sha=%s\nsource_sha=%s\n' "$base_sha" "$source_sha" \
  | tee "$artifact_dir/revisions.txt"
git diff --exit-code "$source_sha" -- "${source_files[@]}" \
  | tee "$artifact_dir/source-diff-before.txt"
git worktree add --detach "$base_dir" "$base_sha"

run_extra_mypy() {
  local label=$1
  local directory=$2
  local result
  set +e
  (cd "$directory" && uv sync --all-extras && uv run mypy src/rigplane/core/transport.py) \
    > "$artifact_dir/$label-mypy.log" 2>&1
  result=$?
  set -e
  printf '%s\n' "$result" > "$artifact_dir/$label-mypy.rc"
  cat "$artifact_dir/$label-mypy.log"
}

run_extra_mypy base "$base_dir"
base_result=$(cat "$artifact_dir/base-mypy.rc")
run_extra_mypy source .
source_result=$(cat "$artifact_dir/source-mypy.rc")
test "$base_result" -eq "$source_result"
test "$(grep -c '\[no-any-return\]' "$artifact_dir/base-mypy.log")" -eq \
  "$(grep -c '\[no-any-return\]' "$artifact_dir/source-mypy.log")"

uv run ruff check src/ tests/ | tee "$artifact_dir/ruff.log"
uv run ruff format --check src/ tests/ | tee "$artifact_dir/format.log"
uv run lint-imports | tee "$artifact_dir/imports.log"
uv run mypy --strict src/rigplane/web | tee "$artifact_dir/web-mypy.log"

run_guard_suite() {
  local label=$1
  local cache_dir
  cache_dir="$(mktemp -d)"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX="$cache_dir" \
    uv run pytest "$guard_suite" -q --tb=short --color=no \
    | tee "$artifact_dir/$label.log"
}

run_guard_suite control-before

run_mutant() {
  local name=$1
  local node=$2
  local failures=$3
  local expected=$4
  local cache_dir
  mutation_name=$name
  mutant_applied=1
  git diff -- src/rigplane/core/transport.py | tee "$artifact_dir/$name.diff"
  set +e
  cache_dir="$(mktemp -d)"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX="$cache_dir" \
    uv run pytest "$node" -q --tb=short --color=no > "$artifact_dir/$name.log" 2>&1
  result=$?
  set -e
  restore_mutant
  git diff --exit-code "$source_sha" -- "${source_files[@]}" \
    | tee "$artifact_dir/$name-restored.diff"
  cat "$artifact_dir/$name.log"
  test "$result" -eq 1
  test "$(grep -c '^FAILED ' "$artifact_dir/$name.log")" -eq "$failures"
  grep -F "$expected" "$artifact_dir/$name.log"
}

git apply <<'PATCH'
diff --git a/src/rigplane/core/transport.py b/src/rigplane/core/transport.py
--- a/src/rigplane/core/transport.py
+++ b/src/rigplane/core/transport.py
@@ -424,6 +424,4 @@ class IcomTransport:
         This guards local submission only, not delivery or radio execution.
         """
-        if is_current is not None and not is_current():
-            raise CommandError("Tracked write is no longer current")
         seq = self._next_send_seq()
         pkt = bytearray(data)
PATCH
run_mutant initial \
  tests/test_transport.py::TestTrackedWriteGuard::test_initial_suppression_has_no_send_side_effects \
  2 test_initial_suppression_has_no_send_side_effects

git apply <<'PATCH'
diff --git a/src/rigplane/core/transport.py b/src/rigplane/core/transport.py
--- a/src/rigplane/core/transport.py
+++ b/src/rigplane/core/transport.py
@@ -596,5 +596,6 @@ class IcomTransport:
         """Handle ptype=0x01 retransmit requests (single or multi)."""
         if length == CONTROL_SIZE and len(data) == CONTROL_SIZE:
             # Single retransmit request from radio
-            self._retransmit(seq)
+            if seq in self.tx_buffer:
+                self._raw_send(self.tx_buffer[seq])
             return True
PATCH
run_mutant single \
  tests/test_transport.py::TestTrackedWriteGuard::test_stale_on_replay_after_current_off_is_suppressed[False] \
  1 'test_stale_on_replay_after_current_off_is_suppressed[False]'

git apply <<'PATCH'
diff --git a/src/rigplane/core/transport.py b/src/rigplane/core/transport.py
--- a/src/rigplane/core/transport.py
+++ b/src/rigplane/core/transport.py
@@ -603,5 +603,6 @@ class IcomTransport:
         for i in range(CONTROL_SIZE, len(data), 2):
             if i + 2 <= len(data):
                 rseq = struct.unpack_from("<H", data, i)[0]
-                self._retransmit(rseq)
+                if rseq in self.tx_buffer:
+                    self._raw_send(self.tx_buffer[rseq])
         return True
PATCH
run_mutant multi \
  tests/test_transport.py::TestTrackedWriteGuard::test_stale_on_replay_after_current_off_is_suppressed[True] \
  1 'test_stale_on_replay_after_current_off_is_suppressed[True]'

git apply <<'PATCH'
diff --git a/src/rigplane/core/transport.py b/src/rigplane/core/transport.py
--- a/src/rigplane/core/transport.py
+++ b/src/rigplane/core/transport.py
@@ -500,5 +500,4 @@ class IcomTransport:

         if len(self.tx_buffer) >= BUFSIZE:
             evicted, _ = self.tx_buffer.popitem(last=False)
-            self._tx_guards.pop(evicted, None)
         self.tx_buffer[seq] = data
         if is_current is not None:
PATCH
run_mutant eviction \
  tests/test_transport.py::TestTrackedWriteGuard::test_guard_metadata_follows_fifo_eviction \
  1 test_guard_metadata_follows_fifo_eviction

git diff --exit-code "$source_sha" -- "${source_files[@]}" \
  | tee "$artifact_dir/source-diff-final.txt"
git status --short --untracked-files=no > "$artifact_dir/source-status-final.txt"
run_guard_suite control-after
