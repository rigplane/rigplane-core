#!/usr/bin/env bash
# Portable adapter of the accepted one-shot recipe; no publication or cleanup.
set -euo pipefail
task_root=${CORE_ARTIFACT_ROOT:?owned build directory required}
control_root="$GITHUB_WORKSPACE/control/.github/scripts"
target_root="$GITHUB_WORKSPACE/target"
expected_source=f66d2553bab70ffeeaa6190a77150cf93ba5e4a5
expected_tree=36fe7facd615931a81482bc0be9e83545b142298
expected_archive=511c4125a5b64ad4e2c372c6ad70e896c5043f460ee2a8aec0fe960f38a5098e
version=3.0.0b13

test -d "$task_root"
test "$(git -C "$target_root" rev-parse HEAD)" = "$expected_source"
test "$(git -C "$target_root" rev-parse 'HEAD^{tree}')" = "$expected_tree"
mkdir "$task_root/inputs" "$task_root/source" "$task_root/dist" \
  "$task_root/tmp" "$task_root/pytest-root" "$task_root/installed-tests"
git -C "$target_root" archive --format=tar --prefix=rigplane-core/ \
  --output="$task_root/inputs/source-f66d2553.tar" "$expected_source"
observed_archive=$(sha256sum "$task_root/inputs/source-f66d2553.tar" | awk '{print $1}')
printf '%s\n' "$observed_archive" > "$task_root/source-archive-sha256.txt"
test "$observed_archive" = "$expected_archive"
test "$(git get-tar-commit-id < "$task_root/inputs/source-f66d2553.tar")" = "$expected_source"
tar -xf "$task_root/inputs/source-f66d2553.tar" --strip-components=1 -C "$task_root/source"
cp "$control_root/private-core-artifact-input.json" "$task_root/inputs/source-freeze.json"
cp "$control_root/private_core_artifact_receipt.py" "$task_root/inputs/artifact-receipt.py"
cp "$control_root/private_core_installed_smoke.py" "$task_root/inputs/installed-software-smoke.py"
cp "$control_root/build-private-core-artifact.sh" "$task_root/inputs/build-recipe.sh"
cp "$task_root/source/.github/scripts/installed_profile_smoke.py" "$task_root/inputs/"
cp "$task_root/source/tests/test_ic7300_swr_cadence.py" \
  "$task_root/source/tests/test_combined_acquisition_drain.py" "$task_root/installed-tests/"
(cd "$task_root/inputs" && sha256sum *) > "$task_root/input-sha256.txt"

# Job-local caches and bounded workers, without host configuration changes.
export PYTHONPATH="" PYTHONNOUSERSITE=1 PYTEST_ADDOPTS="" PYTEST_PLUGINS=""
export TMPDIR="$task_root/tmp" UV_PYTHON=3.13
export npm_config_jobs=4 UV_CONCURRENT_BUILDS=4 UV_CONCURRENT_DOWNLOADS=4
export UV_THREADPOOL_SIZE=4 GOMAXPROCS=4 RAYON_NUM_THREADS=4
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4 NUMEXPR_NUM_THREADS=4
uv python install 3.13
build_python=$(uv python find 3.13)
case "$(node --version)" in v20.*) ;; *) exit 1 ;; esac
{
  date -u
  git --version
  uv --version
  "$build_python" --version
  node --version
  npm --version
} > "$task_root/tooling-profile.txt"
printf '%s\n' "$expected_source" "$expected_tree" "$version" > "$task_root/source-identity.txt"
"$build_python" - "$task_root/source/pyproject.toml" "$version" <<'PY'
import sys
import tomllib
from pathlib import Path
assert tomllib.loads(Path(sys.argv[1]).read_text())["project"]["version"] == sys.argv[2]
PY

cd "$task_root"
sha256sum source/pyproject.toml source/uv.lock source/frontend/package-lock.json \
  source/hatch_build.py > input-sha256-before.txt
cd "$task_root/source/frontend"
npm ci
cd "$task_root/source"
uv build --python 3.13 --out-dir "$task_root/dist"
cd "$task_root"
sha256sum source/pyproject.toml source/uv.lock source/frontend/package-lock.json \
  source/hatch_build.py > input-sha256-after.txt
diff -u input-sha256-before.txt input-sha256-after.txt

uv venv --python 3.13 installed
uv pip install --python installed/bin/python "dist/rigplane-$version-py3-none-any.whl[dev]"
installed/bin/python inputs/installed_profile_smoke.py \
  --artifact "$task_root/dist/rigplane-$version-py3-none-any.whl" \
  --candidate-sha "$expected_source" --forbid-root "$task_root/source" \
  > installed-profile-smoke.json
installed/bin/python inputs/installed-software-smoke.py "$task_root" \
  > installed-software-smoke.log 2>&1
installed/bin/rigplane --help > installed-cli-help.txt
uv pip freeze --python installed/bin/python > installed-profile.txt
sha256sum "dist/rigplane-$version-py3-none-any.whl" "dist/rigplane-$version.tar.gz" \
  > artifact-sha256.txt
stat -c '%n %s' "dist/rigplane-$version-py3-none-any.whl" "dist/rigplane-$version.tar.gz" \
  > artifact-bytes.txt
(cd inputs && sha256sum -c ../input-sha256.txt)
cp input-sha256.txt frozen-input-sha256.txt
date -u > build-completed-utc.txt
# The workflow finalizes the receipt after tee has closed build.log.
