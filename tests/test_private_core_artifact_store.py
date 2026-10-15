"""Synthetic completed-producer bundles; no store activation or release proof."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / '.github/scripts/private_core_artifact_store.py'


@pytest.fixture
def store_module():
    spec = importlib.util.spec_from_file_location('private_core_artifact_store', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(params=['b13', 'b14'])
def bundle(tmp_path, request):
    version = f'3.0.0{request.param}'
    source, tree = ('f66d2553bab70ffeeaa6190a77150cf93ba5e4a5',
                    '36fe7facd615931a81482bc0be9e83545b142298') if request.param == 'b13' else (
                        '00b82396fb51617911273076dd8925058343e136',
                        '2' * 40)
    root = tmp_path / 'bundle'
    (root / 'dist').mkdir(parents=True)
    (root / 'inputs').mkdir()
    archive = f'source-{source[:8]}.tar'
    inputs = {archive: b'synthetic source archive', 'artifact-receipt.py': b'receipt',
              'installed-software-smoke.py': b'smoke', 'build-recipe.sh': b'build',
              'installed_profile_smoke.py': b'profile'}
    freeze = {'sourceAcceptance': 'ACCEPTED', 'commit': source, 'tree': tree,
              'version': version, 'sourceArchiveSha256': sha(inputs[archive])}
    inputs['source-freeze.json'] = json.dumps(freeze).encode()
    for name, data in inputs.items():
        (root / 'inputs' / name).write_bytes(data)
    evidence = dict.fromkeys((
        'installed-profile-smoke.json', 'installed-software-smoke.json',
        'installed-software-smoke.log', 'installed-cli-help.txt', 'installed-profile.txt',
        'wheel-assets.json', 'module-bytes.json', 'tooling-profile.txt', 'source-identity.txt',
        'build.log', 'input-sha256-before.txt', 'input-sha256-after.txt',
        'frozen-input-sha256.txt', 'artifact-sha256.txt', 'artifact-bytes.txt',
        'build-completed-utc.txt',
    ), b'synthetic evidence')
    if request.param == 'b14':
        evidence['dependency-cache.txt'] = b'synthetic cache telemetry'
    evidence['frozen-input-sha256.txt'] = ''.join(
        f'{sha(data)}  {name}\n' for name, data in inputs.items()).encode()
    for name, data in evidence.items():
        (root / name).write_bytes(data)
    artifacts = {}
    for name in (f'rigplane-{version}-py3-none-any.whl', f'rigplane-{version}.tar.gz'):
        data = f'synthetic {name}'.encode()
        (root / 'dist' / name).write_bytes(data)
        artifacts[name] = {'sha256': sha(data), 'bytes': len(data)}
    manifest = {'source_sha': source, 'source_tree': tree, 'version': version,
                'workflow': 'private-core-artifact.yml' if request.param == 'b13' else
                            'private-core-b14-artifact.yml',
                'workflow_control_sha': 'c' * 40, 'run_id': '123', 'run_attempt': '2',
                'source_bundle_sha256': sha(inputs[archive]),
                'source_freeze_sha256': sha(inputs['source-freeze.json']),
                'publication': False, 'artifacts': artifacts,
                'evidence': {name: sha(data) for name, data in evidence.items()}}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return root


def rewrite(bundle, edit):
    path = bundle / 'manifest.json'
    manifest = json.loads(path.read_text())
    edit(manifest)
    path.write_text(json.dumps(manifest))


def test_roundtrip_preserves_manifest_and_only_declared_members(store_module, bundle, tmp_path):
    store = tmp_path / 'store'
    store.mkdir()
    (bundle / 'source').mkdir()
    (bundle / 'source' / 'unrelated').write_text('not evidence')
    (bundle / 'installed').mkdir()
    (bundle / 'installed' / 'secret').write_text('not evidence')
    (bundle / 'unlisted.log').write_text('not declared')
    identity = store_module.persist(bundle, store)
    target = tmp_path / 'retrieved'
    store_module.retrieve(store, identity, target)
    assert (target / 'manifest.json').read_bytes() == (bundle / 'manifest.json').read_bytes()
    assert not (target / 'source').exists()
    assert not (target / 'installed').exists()
    assert not (target / 'unlisted.log').exists()
    assert len(list((target / 'dist').iterdir())) == 2
    assert len(list((target / 'inputs').iterdir())) == 6
    assert identity.startswith('rigplane/rigplane-core/')
    assert identity.endswith('/' + 'c' * 40 + '/123/2')
    for member in target.rglob('*'):
        if member.is_file():
            assert member.read_bytes() == (bundle / member.relative_to(target)).read_bytes()


@pytest.mark.parametrize('member', ['dist', 'inputs', 'build.log', 'manifest.json'])
def test_rejects_symlink_members(store_module, bundle, tmp_path, member):
    original = bundle / member
    moved = tmp_path / 'moved'
    original.rename(moved)
    original.symlink_to(moved)
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises((ValueError, OSError)):
        store_module.persist(bundle, store)
    assert not list(store.rglob('manifest.json'))


@pytest.mark.parametrize('member', ['build.log', 'inputs/build-recipe.sh',
                                  'dist/rigplane-3.0.0b13-py3-none-any.whl'])
def test_rejects_missing_or_corrupted_members(store_module, bundle, tmp_path, member):
    # Choose the actual wheel for either producer fixture.
    path = next((bundle / 'dist').glob('*.whl')) if member.startswith('dist/') else bundle / member
    path.write_bytes(b'corruption')
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(ValueError):
        store_module.persist(bundle, store)
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        store_module.persist(bundle, store)


@pytest.mark.parametrize('field,value', [
    ('source_sha', '../escape'), ('workflow_control_sha', 'A' * 40),
    ('run_id', '../123'), ('run_attempt', '0'), ('workflow', 'publish.yml'),
    ('publication', True), ('source_tree', 'short'),
])
def test_rejects_invalid_identity(store_module, bundle, tmp_path, field, value):
    rewrite(bundle, lambda m: m.update({field: value}))
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(ValueError):
        store_module.persist(bundle, store)


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'inputs/../outside',
                                 'source/private', 'bad\\name', '.hidden'])
def test_rejects_unsafe_declared_members(store_module, bundle, tmp_path, name):
    rewrite(bundle, lambda m: m['evidence'].update({name: sha(b'x')}))
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(ValueError):
        store_module.persist(bundle, store)


def test_rejects_missing_required_evidence_and_wrong_size(store_module, bundle, tmp_path):
    store = tmp_path / 'store'
    store.mkdir()
    original = (bundle / 'manifest.json').read_bytes()
    rewrite(bundle, lambda m: m['evidence'].pop('build-completed-utc.txt'))
    with pytest.raises(ValueError):
        store_module.persist(bundle, store)
    (bundle / 'manifest.json').write_bytes(original)
    rewrite(bundle, lambda m: next(iter(m['artifacts'].values())).update(bytes=0))
    with pytest.raises(ValueError):
        store_module.persist(bundle, store)


def test_refuses_existing_identity_and_retrieval_destination(store_module, bundle, tmp_path):
    store = tmp_path / 'store'
    store.mkdir()
    identity = store_module.persist(bundle, store)
    before = (store / identity / 'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):
        store_module.persist(bundle, store)
    assert (store / identity / 'manifest.json').read_bytes() == before
    target = tmp_path / 'target'
    target.mkdir()
    with pytest.raises(FileExistsError):
        store_module.retrieve(store, identity, target)
    assert not list(target.iterdir())


def test_retrieval_checks_identity_and_corruption(store_module, bundle, tmp_path):
    store = tmp_path / 'store'
    store.mkdir()
    identity = store_module.persist(bundle, store)
    wrong = identity.removesuffix('/2') + '/3'
    (store / identity).rename(store / wrong)
    with pytest.raises(ValueError):
        store_module.retrieve(store, wrong, tmp_path / 'target')
    (store / wrong).rename(store / identity)
    (store / identity / 'build.log').write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        store_module.retrieve(store, identity, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()


@pytest.mark.parametrize('failure', [OSError('disk full'), KeyboardInterrupt()])
def test_failed_copy_never_publishes_and_retry_succeeds(store_module, bundle, tmp_path, monkeypatch, failure):
    store = tmp_path / 'store'
    store.mkdir()
    copy = store_module._copy_member
    count = 0

    def interrupted(*args):
        nonlocal count
        count += 1
        if count == 3:
            raise failure
        return copy(*args)

    monkeypatch.setattr(store_module, '_copy_member', interrupted)
    with pytest.raises(type(failure)):
        store_module.persist(bundle, store)
    assert not list(store.rglob('manifest.json'))
    assert not [p for p in store.rglob('*') if p.name.startswith('.pending-')]
    monkeypatch.setattr(store_module, '_copy_member', copy)
    identity = store_module.persist(bundle, store)
    assert (store / identity / 'manifest.json').is_file()


def test_rejects_store_symlink_and_missing_root(store_module, bundle, tmp_path):
    with pytest.raises((OSError, ValueError)):
        store_module.persist(bundle, tmp_path / 'absent')
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        store_module.persist(bundle, alias)
    assert not list(real.iterdir())


def test_atomic_publish_race_never_overwrites_destination(store_module, bundle, tmp_path, monkeypatch):
    store = tmp_path / 'store'
    store.mkdir()
    publish = store_module._publish

    def competitor(stage, target):
        target.mkdir()
        (target / 'winner').write_bytes(b'other publisher')
        return publish(stage, target)

    monkeypatch.setattr(store_module, '_publish', competitor)
    with pytest.raises(FileExistsError):
        store_module.persist(bundle, store)
    winner = list(store.rglob('winner'))
    assert len(winner) == 1 and winner[0].read_bytes() == b'other publisher'
    assert not list(store.rglob('manifest.json'))


def test_retrieval_failure_cleans_partial_destination(store_module, bundle, tmp_path, monkeypatch):
    store = tmp_path / 'store'
    store.mkdir()
    identity = store_module.persist(bundle, store)
    copy = store_module._copy_member
    calls = 0

    def fail(*args):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError('synthetic I/O failure')
        return copy(*args)

    monkeypatch.setattr(store_module, '_copy_member', fail)
    target = tmp_path / 'retrieved'
    with pytest.raises(OSError):
        store_module.retrieve(store, identity, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.pending-*'))
    assert (store / identity / 'manifest.json').is_file()


def test_duplicate_manifest_keys_rejected(store_module, bundle, tmp_path):
    path = bundle / 'manifest.json'
    path.write_text(path.read_text().replace('{', '{"run_id":"999",', 1))
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(ValueError, match='duplicate JSON key'):
        store_module.persist(bundle, store)


def test_repository_assertion_must_match_fixed_namespace(store_module, bundle, tmp_path):
    rewrite(bundle, lambda m: m.update(repository='another/repository'))
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(ValueError, match='repository mismatch'):
        store_module.persist(bundle, store)


def test_rejects_symlink_store_ancestor_and_member_hardlink(store_module, bundle, tmp_path):
    real = tmp_path / 'real'
    (real / 'store').mkdir(parents=True)
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        store_module.persist(bundle, link / 'store')
    (tmp_path / 'hardlink').hardlink_to(bundle / 'build.log')
    with pytest.raises(ValueError, match='regular file'):
        store_module.persist(bundle, real / 'store')


def test_incomplete_staging_is_not_a_completed_identity(store_module, bundle, tmp_path):
    store = tmp_path / 'store'
    store.mkdir()
    identity = store_module.persist(bundle, store)
    final = store / identity
    pending = final.parent / '.pending-abandoned'
    pending.mkdir()
    (pending / 'manifest.json').write_bytes(b'partial')
    # A killed prior attempt's debris cannot prevent reading an intact final identity.
    store_module.retrieve(store, identity, tmp_path / 'retrieved')
    final.rename(final.parent / '.pending-unpublished')
    with pytest.raises(FileNotFoundError):
        store_module.retrieve(store, identity, tmp_path / 'missing')


def test_source_freeze_identity_is_checked_beyond_member_hashes(store_module, bundle, tmp_path):
    freeze_path = bundle / 'inputs/source-freeze.json'
    freeze = json.loads(freeze_path.read_text())
    freeze['tree'] = 'f' * 40
    freeze_path.write_text(json.dumps(freeze))
    new_hash = sha(freeze_path.read_bytes())
    inventory = bundle / 'frozen-input-sha256.txt'
    lines = inventory.read_text().splitlines()
    inventory.write_text('\n'.join(
        f'{new_hash}  source-freeze.json' if line.endswith('  source-freeze.json') else line
        for line in lines) + '\n')
    rewrite(bundle, lambda m: (
        m.update(source_freeze_sha256=new_hash),
        m['evidence'].update({'frozen-input-sha256.txt': sha(inventory.read_bytes())})))
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(ValueError, match='source freeze identity mismatch'):
        store_module.persist(bundle, store)


def test_cli_roundtrip(store_module, bundle, tmp_path):
    import subprocess
    import sys

    store = tmp_path / 'store'
    store.mkdir()
    saved = subprocess.run([sys.executable, str(SCRIPT), 'persist', '--bundle', str(bundle),
                            '--store', str(store)], capture_output=True, text=True, check=True)
    identity = saved.stdout.strip()
    target = tmp_path / 'retrieved'
    loaded = subprocess.run([sys.executable, str(SCRIPT), 'retrieve', '--store', str(store),
                             '--identity', identity, '--destination', str(target)],
                            capture_output=True, text=True, check=True)
    assert loaded.stdout.strip() == identity
    assert (target / 'manifest.json').read_bytes() == (bundle / 'manifest.json').read_bytes()
    refused = subprocess.run([sys.executable, str(SCRIPT), 'persist', '--bundle', str(bundle),
                              '--store', str(store)], capture_output=True, text=True)
    assert refused.returncode == 1
    assert 'refused' in refused.stderr


def test_concurrent_publishers_have_exactly_one_winner(store_module, bundle, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = tmp_path / 'store'
    store.mkdir()

    def save():
        try:
            return store_module.persist(bundle, store)
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(), range(2)))
    assert sum(result is not None for result in results) == 1
    identity = next(result for result in results if result)
    store_module.retrieve(store, identity, tmp_path / 'retrieved')
    assert not list(store.rglob('.pending-*'))


@pytest.mark.parametrize('identity', ['../escape', '/absolute',
    'other/repo/private-core-artifact.yml/' + 'a' * 40 + '/' + 'b' * 40 + '/1/1',
    'rigplane/rigplane-core/private-core-artifact.yml/' + 'a' * 40 + '/' + 'b' * 40 + '/1/01'])
def test_retrieval_rejects_unsafe_identity(store_module, tmp_path, identity):
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(ValueError):
        store_module.retrieve(store, identity, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()


def test_no_replace_api_unavailable_fails_closed(store_module, bundle, tmp_path, monkeypatch):
    store = tmp_path / 'store'
    store.mkdir()
    monkeypatch.setattr(store_module.ctypes, 'CDLL', lambda *a, **k: object())
    with pytest.raises(OSError, match='renameat2'):
        store_module.persist(bundle, store)
    assert not list(store.rglob('manifest.json'))
    assert not list(store.rglob('.pending-*'))
