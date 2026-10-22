"""Explicit Linux persistence/retrieval for the two frozen private Core producers.

Preserves their manifest and declared bytes, with no archive conversion or workflow
activation. Only verified, complete directories get a public identity. Repository
namespace is fixed: legacy manifests do not attest repository or authenticity.
All directory ancestors must be real directories. The caller must protect the
store and source from concurrent modification by other same-UID jobs: this is
integrity checking, not cross-job isolation. Actual equipped-runner durability,
retention, retrieval access and PyPI transport require separate acceptance.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

REPOSITORY = 'rigplane/rigplane-core'
PRODUCERS = {'private-core-artifact.yml': '3.0.0b13',
             'private-core-b14-artifact.yml': '3.0.0b14'}
EVIDENCE = frozenset((
    'installed-profile-smoke.json', 'installed-software-smoke.json',
    'installed-software-smoke.log', 'installed-cli-help.txt', 'installed-profile.txt',
    'wheel-assets.json', 'module-bytes.json', 'tooling-profile.txt', 'source-identity.txt',
    'build.log', 'input-sha256-before.txt', 'input-sha256-after.txt',
    'frozen-input-sha256.txt', 'artifact-sha256.txt', 'artifact-bytes.txt',
    'build-completed-utc.txt',
))
INPUTS = frozenset(('source-freeze.json', 'artifact-receipt.py',
                    'installed-software-smoke.py', 'build-recipe.sh',
                    'installed_profile_smoke.py'))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(f'[0-9a-f]{{{length}}}', value)


def _name(value):
    _require(isinstance(value, str) and
             re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', value),
             'unsafe member name')
    return value


def _directory(path):
    """Reject aliases, dot components and symlinks in every absolute ancestor."""
    path = Path(path)
    _require(path.is_absolute() and '..' not in path.parts, 'absolute canonical path required')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        _require(stat.S_ISDIR(current.lstat().st_mode), f'not a real directory: {current}')
    return path


def _open(root, relative):
    path = root / relative
    _directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                 f'not a private regular file: {relative}')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def _small(root, relative):
    with _open(root, relative) as stream:
        data = stream.read(1024 * 1024 + 1)
    _require(len(data) <= 1024 * 1024, f'oversized metadata: {relative}')
    return data


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, f'duplicate JSON key: {key}')
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=unique)
    _require(isinstance(result, dict), 'metadata must be an object')
    return result


def _identity(manifest):
    workflow = manifest.get('workflow')
    _require(isinstance(workflow, str) and workflow in PRODUCERS, 'unsupported producer')
    _require(manifest.get('version') == PRODUCERS[workflow], 'producer version mismatch')
    _require(manifest.get('publication') is False, 'not a private build receipt')
    if 'repository' in manifest:
        _require(manifest['repository'] == REPOSITORY, 'repository mismatch')
    for field in ('source_sha', 'source_tree', 'workflow_control_sha'):
        _require(_hex(manifest.get(field), 40), f'invalid {field}')
    for field in ('run_id', 'run_attempt'):
        value = manifest.get(field)
        _require(isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,19}', value),
                 f'invalid {field}')
    return '/'.join((REPOSITORY, workflow, manifest['source_sha'],
                     manifest['workflow_control_sha'], manifest['run_id'], manifest['run_attempt']))


def _inventory(root):
    """Use existing manifest and frozen-input-sha256; never scan source/venv."""
    raw = _small(root, 'manifest.json')
    manifest = _json(raw)
    identity = _identity(manifest)
    members = {'manifest.json': (hashlib.sha256(raw).hexdigest(), len(raw))}
    artifacts, evidence = manifest.get('artifacts'), manifest.get('evidence')
    _require(isinstance(artifacts, dict) and isinstance(evidence, dict), 'missing member maps')
    version = manifest['version']
    _require(set(artifacts) == {f'rigplane-{version}-py3-none-any.whl',
                                f'rigplane-{version}.tar.gz'}, 'incomplete artifact pair')
    expected_evidence = EVIDENCE | ({'dependency-cache.txt'} if version == '3.0.0b14' else set())
    _require(set(evidence) == expected_evidence, 'incomplete or unexpected evidence')
    for name, entry in artifacts.items():
        _name(name)
        _require(isinstance(entry, dict) and _hex(entry.get('sha256'), 64) and
                 type(entry.get('bytes')) is int and entry['bytes'] > 0,
                 'invalid artifact hash/size')
        members[f'dist/{name}'] = (entry['sha256'], entry['bytes'])
    for name, digest in evidence.items():
        _name(name)
        _require(_hex(digest, 64), 'invalid evidence hash')
        members[name] = (digest, None)
    frozen_raw = _small(root, 'frozen-input-sha256.txt')
    _require(hashlib.sha256(frozen_raw).hexdigest() == evidence['frozen-input-sha256.txt'],
             'frozen input inventory hash mismatch')
    inputs = {}
    for line in frozen_raw.decode('utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64}) [ *]([A-Za-z0-9][A-Za-z0-9_.-]{0,199})', line)
        _require(match is not None, 'invalid frozen input inventory')
        digest, name = match.groups()
        _require(name not in inputs, 'duplicate input member')
        inputs[name] = digest
    archive = f'source-{manifest["source_sha"][:8]}.tar'
    _require(set(inputs) == INPUTS | {archive}, 'incomplete or unexpected inputs')
    for field, name in (('source_bundle_sha256', archive),
                        ('source_freeze_sha256', 'source-freeze.json')):
        _require(_hex(manifest.get(field), 64) and manifest[field] == inputs[name],
                 f'{field} mismatch')
    freeze_raw = _small(root, 'inputs/source-freeze.json')
    _require(hashlib.sha256(freeze_raw).hexdigest() == inputs['source-freeze.json'],
             'source freeze hash mismatch')
    freeze = _json(freeze_raw)
    _require(freeze.get('sourceAcceptance') == 'ACCEPTED' and
             freeze.get('commit') == manifest['source_sha'] and
             freeze.get('tree') == manifest['source_tree'] and
             freeze.get('version') == version and
             freeze.get('sourceArchiveSha256') == inputs[archive], 'source freeze identity mismatch')
    for name, digest in inputs.items():
        members[f'inputs/{name}'] = (digest, None)
    return identity, members


def _copy_member(root, relative, expected, destination):
    digest, count = hashlib.sha256(), 0
    with _open(root, relative) as source:
        # Exclusive creation; never follow or overwrite any destination file.
        with destination.open('xb') as target:
            os.chmod(destination, 0o600)
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    _require(digest.hexdigest() == expected[0] and
             (expected[1] is None or count == expected[1]), f'bytes mismatch: {relative}')


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(stage, destination):
    """Linux atomic no-replace; fail closed if libc/filesystem lacks support."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError(errno.ENOSYS, 'renameat2(RENAME_NOREPLACE) required')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
    _sync_dir(destination.parent)


def _transfer(root, destination, expected_identity=None):
    root = _directory(root)
    parent = _directory(destination.parent)
    _require(not destination.name.startswith('.') and destination.name not in ('', '..'),
             'unsafe destination')
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    identity, members = _inventory(root)
    _require(expected_identity is None or identity == expected_identity, 'stored identity mismatch')
    stage = Path(tempfile.mkdtemp(prefix='.pending-', dir=parent))
    try:
        (stage / 'dist').mkdir(mode=0o700)
        (stage / 'inputs').mkdir(mode=0o700)
        for relative, expected in members.items():
            _copy_member(root, relative, expected, stage / relative)
        copied_identity, copied_members = _inventory(stage)
        _require(copied_identity == identity and copied_members == members,
                 'staged manifest identity/member mismatch')
        for directory in (stage / 'dist', stage / 'inputs', stage):
            _sync_dir(directory)
        _publish(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return identity


def persist(bundle, store):
    """Publish one verified legacy bundle; existing identities always conflict."""
    bundle, store = _directory(bundle), _directory(store)
    identity, _ = _inventory(bundle)
    parent = store
    for component in identity.split('/')[:-1]:
        child = parent / component
        try:
            child.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _directory(child)
        _sync_dir(parent)
        parent = child
    return _transfer(bundle, parent / identity.split('/')[-1], identity)


def retrieve(store, identity, destination):
    """Re-hash every declared member before exposing a new destination."""
    store = _directory(store)
    _require(isinstance(identity, str), 'invalid identity')
    parts = identity.split('/')
    _require(len(parts) == 7 and parts[:2] == REPOSITORY.split('/'), 'invalid identity namespace')
    for part in parts:
        _name(part)
    _require(parts[2] in PRODUCERS and _hex(parts[3], 40) and _hex(parts[4], 40) and
             all(re.fullmatch(r'[1-9][0-9]{0,19}', p) for p in parts[5:]), 'invalid identity')
    return _transfer(store / identity, Path(destination), identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    save = commands.add_parser('persist')
    save.add_argument('--bundle', type=Path, required=True)
    save.add_argument('--store', type=Path, required=True)
    load = commands.add_parser('retrieve')
    load.add_argument('--store', type=Path, required=True)
    load.add_argument('--identity', required=True)
    load.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        identity = (persist(args.bundle, args.store) if args.command == 'persist' else
                    retrieve(args.store, args.identity, args.destination))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'private Core bundle refused: {error}\n')
    print(identity)


if __name__ == '__main__':
    main()
