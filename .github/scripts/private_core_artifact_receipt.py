"""Receipt for the build-only adapter of the accepted one-shot Core recipe."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import zipfile
from importlib import metadata
from pathlib import Path

import rigplane

root = Path(sys.argv[1]).resolve()
source = root / 'source/src/rigplane'
installed = Path(rigplane.__file__).resolve().parent
version = '3.0.0b13'
wheel = root / f'dist/rigplane-{version}-py3-none-any.whl'
sdist = root / f'dist/rigplane-{version}.tar.gz'
freeze = json.loads((root / 'inputs/source-freeze.json').read_text())
assert freeze['sourceAcceptance'] == 'ACCEPTED'
assert freeze['commit'] == 'f66d2553bab70ffeeaa6190a77150cf93ba5e4a5'
assert freeze['tree'] == '36fe7facd615931a81482bc0be9e83545b142298'
assert freeze['version'] == version
assert metadata.version('rigplane') == version
assert installed.is_relative_to(root / 'installed')
assert os.environ['RUNNER_ARCH'] == 'X64'
assert (root / 'source/src').resolve() not in {Path(p or '.').resolve() for p in sys.path}

catalog = (
    'audio/route.py', 'web/handlers/audio.py', 'runtime/_civ_rx.py',
    'runtime/_control_phase.py', 'runtime/_runtime_protocols.py',
    'runtime/session_lifecycle.py', 'core/acquisition_scheduler.py',
    'rigctld/protocol.py',
)
assert list(catalog) == freeze['byteCatalog']
def digest(data):
    return hashlib.sha256(data).hexdigest()
def file_digest(path):
    return digest(path.read_bytes())

assert file_digest(root / 'inputs/source-f66d2553.tar') == freeze['sourceArchiveSha256']
assert freeze['sourceArchiveSha256'] == '511c4125a5b64ad4e2c372c6ad70e896c5043f460ee2a8aec0fe960f38a5098e'
assets, modules = [], []
built = root / 'source/frontend/dist'
built_inventory = {str(p.relative_to(built)): p for p in built.rglob('*') if p.is_file()}
assert built_inventory and 'index.html' in built_inventory
assert built_inventory['index.html'].stat().st_size > 0
installed_inventory = {str(p.relative_to(installed / 'web/static')) for p in (installed / 'web/static').rglob('*') if p.is_file()}
with zipfile.ZipFile(wheel) as archive, tarfile.open(sdist, 'r:gz') as source_archive:
    prefix = f'rigplane-{version}/'
    assert f'\nVersion: {version}\n' in archive.read(f'rigplane-{version}.dist-info/METADATA').decode()
    sdist_meta = source_archive.extractfile(prefix + 'PKG-INFO')
    assert sdist_meta is not None and f'\nVersion: {version}\n' in sdist_meta.read().decode()
    for relative in catalog:
        data = (source / relative).read_bytes()
        member = source_archive.extractfile(prefix + 'src/rigplane/' + relative)
        assert member is not None
        assert data == archive.read('rigplane/' + relative) == member.read() == (installed / relative).read_bytes(), relative
        modules.append({'path': relative, 'sha256': digest(data), 'source_wheel_sdist_installed_equal': True})
    wheel_inventory = {name.removeprefix('rigplane/web/static/') for name in archive.namelist() if name.startswith('rigplane/web/static/') and not name.endswith('/')}
    assert wheel_inventory == set(built_inventory) == installed_inventory
    sdist_inventory = {member.name.removeprefix(prefix + 'src/rigplane/web/static/') for member in source_archive.getmembers() if member.isfile() and member.name.startswith(prefix + 'src/rigplane/web/static/')}
    assert sdist_inventory == wheel_inventory
    for relative in sorted(wheel_inventory):
        data = archive.read('rigplane/web/static/' + relative)
        member = source_archive.extractfile(prefix + 'src/rigplane/web/static/' + relative)
        assert member is not None
        assert data == built_inventory[relative].read_bytes() == (installed / 'web/static' / relative).read_bytes() == member.read(), relative
        assets.append({'path': relative, 'sha256': digest(data), 'bytes': len(data)})

(root / 'wheel-assets.json').write_text(json.dumps(assets, indent=2) + '\n')
(root / 'module-bytes.json').write_text(json.dumps(modules, indent=2) + '\n')
profile = json.loads((root / 'installed-profile-smoke.json').read_text())
assert profile['profile_count'] == 9 and profile['package_version'] == version
assert profile['candidate_sha'] == freeze['commit']
assert profile['artifact_sha256'] == file_digest(wheel)
assert any(p['id'] == 'icom_ic7300mk2' for p in profile['profiles'])
software = json.loads((root / 'installed-software-smoke.json').read_text())
assert software['installed_meter_exit_code'] == 0 and software['fake_tcp_exit_code'] == 0
assert software['package_version'] == version and not software['source_src_on_import_path']
assert len(software['installed_meter_nodes']) == 2
artifacts = {path.name: {'sha256': file_digest(path), 'bytes': path.stat().st_size} for path in (wheel, sdist)}
evidence_names = (
    'installed-profile-smoke.json', 'installed-software-smoke.json',
    'installed-software-smoke.log', 'installed-cli-help.txt', 'installed-profile.txt',
    'wheel-assets.json', 'module-bytes.json', 'tooling-profile.txt', 'source-identity.txt',
    'build.log', 'input-sha256-before.txt', 'input-sha256-after.txt',
    'frozen-input-sha256.txt', 'artifact-sha256.txt', 'artifact-bytes.txt',
    'build-completed-utc.txt',
)
manifest = {
    'source_sha': freeze['commit'], 'source_tree': freeze['tree'], 'version': version,
    'host': 'existing Core GitHub Actions Linux build runner',
    'runner_name': os.environ['RUNNER_NAME'],
    'runner_arch': os.environ['RUNNER_ARCH'],
    'workflow': 'private-core-artifact.yml',
    'workflow_control_sha': os.environ['GITHUB_SHA'],
    'run_id': os.environ['GITHUB_RUN_ID'],
    'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
    'source_bundle_sha256': freeze['sourceArchiveSha256'],
    'source_freeze_sha256': file_digest(root / 'inputs/source-freeze.json'),
    'aggregate_quick_run': freeze['aggregateMainQuick'], 'publication': False,
    'producer': 'preliminary build-only GHA adapter of the existing one-shot recipe',
    'artifacts': artifacts, 'packaged_asset_count': len(assets),
    'module_byte_catalog': modules,
    'evidence': {name: file_digest(root / name) for name in evidence_names},
    'limitations': [
        'Online isolated software preparation; no owner Pro/native/offline/GUI/radio acceptance.',
        'The official release-published workflow is a distinct producer; public bytes require actual comparison.',
    ],
}
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
