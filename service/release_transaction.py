#!/usr/bin/env python3
"""Non-deploying release staging and rollback rehearsal for Deck-Commander.

This intentionally NEVER modifies systemd, services, user accounts, connectors,
SSH configuration, active processes, private state directories or personal data.
Its `active.json` is a rehearsal manifest, NOT an installed-service switch.

Only a fixed allowlist of source files is copied into a caller-owned private
sandbox. It is not a package installer, code signature check, or production
update mechanism. A future installer must additionally prove systemd unit
activation, OS ownership, signature verification and recovery safety.
"""
from __future__ import annotations

import hashlib
import errno
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import uuid

SOURCE_FILES = (
    'package.json', 'gateway.mjs', 'gateway_access.mjs', 'service_layout.mjs',
    'process_tree.mjs', 'transfer_registry.mjs', 'router.py', 'folder_backend.py',
    'folder_transfer.py', 'resumable_file_transfer.py', 'transfer_reliability.py',
    'service/check_installation.py', 'service/render_service.py',
    'service/deck-commander-connector.service.in',
)
ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}\Z')
MANIFEST = 'manifest.json'
MARKER = 'ready.json'
CURRENT = 'active.json'
MAX_FILE = 4 * 1024 * 1024


class ReleaseRejected(ValueError):
    pass


def _id(release_id):
    if not isinstance(release_id, str) or not ID.fullmatch(release_id):
        raise ReleaseRejected('Release ID must be a simple 1–48 character identifier')
    return release_id


@contextmanager
def _serialized(root):
    """Serialize cooperative rehearsal managers; protect against damaged lock files."""
    fd = os.open(root / '.release.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077 or info.st_nlink != 1):
            raise ReleaseRejected('Release lock must be a private owned regular file')
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ReleaseRejected('Release lock busy; retry later')
                time.sleep(0.05)
        try:
            current = (root / '.release.lock').lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ReleaseRejected('Release lock path changed')
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _private_root(root):
    path = Path(root)
    # The rehearsal must never be mistaken for an installer and pointed at
    # ~/.ssh, /var/lib or another real state directory. Restrict it to
    # specifically named children of the system temporary directory.
    scratch = Path('/tmp').resolve()  # Linux-only rehearsal; do not trust TMPDIR
    if (not path.is_absolute() or not path.name.startswith('dc-release-sandbox-') or
            path.parent.resolve() != scratch):
        raise ReleaseRejected('Only a named temporary dc-release-sandbox-* directory is supported')
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ReleaseRejected('Sandbox root must exist before staging') from error
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or
            metadata.st_mode & 0o077 or path.resolve() != path):
        raise ReleaseRejected('Sandbox root must be an owned, private non-symlink directory')
    return path


@contextmanager
def _directory(path):
    # Traverse from / using descriptors: no symlink in any component.
    path = Path(path).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in ('.', '..'):
                raise ReleaseRejected('Unsafe directory component')
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    except NotADirectoryError as exc:
        raise ReleaseRejected('Symlinked or invalid directory') from exc
    finally:
        os.close(fd)


def _read_regular(path, *, max_bytes=MAX_FILE, immutable=False):
    path = Path(path)
    with _directory(path.parent) as parent:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ReleaseRejected('Symlinked file') from exc
            raise
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes or info.st_nlink != 1 or
                    (immutable and (info.st_uid != os.geteuid() or info.st_mode & 0o377))):
                raise ReleaseRejected('Unsafe regular file ownership, mode or size')
            data = handle.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ReleaseRejected('File exceeds size limit')
            return data


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_exclusive(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as dst:
        dst.write(data)
        dst.flush()
        os.fsync(dst.fileno())


def _json_bytes(data):
    return (json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def _read_json(path, *, limit=MAX_FILE):
    return _parse_json(_read_regular(path, max_bytes=limit))


def _parse_json(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ReleaseRejected('Invalid JSON record') from exc


def _verify_release(root, release_id):
    _id(release_id)
    release_dir = root / 'releases' / release_id
    for directory in (root / 'releases', release_dir, release_dir / 'service'):
        with _directory(directory) as fd:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ReleaseRejected('Release directory is not private')
    expected_entries = set(SOURCE_FILES) | {MARKER, MANIFEST, 'service'}
    actual_entries = {str(p.relative_to(release_dir)) for p in release_dir.rglob('*')}
    if actual_entries != expected_entries:
        raise ReleaseRejected('Unexpected release entries')
    marker = _parse_json(_read_regular(release_dir / MARKER, max_bytes=32768, immutable=True))
    raw_manifest = _read_regular(release_dir / MANIFEST, max_bytes=32768, immutable=True)
    manifest = _parse_json(raw_manifest)
    if (not isinstance(marker, dict) or set(marker) != {'id', 'manifest_sha256'} or
            not isinstance(manifest, dict) or set(manifest) != {'schema', 'id', 'files'} or
            type(manifest['schema']) is not int or manifest['schema'] != 1 or
            marker['id'] != release_id or manifest['id'] != release_id):
        raise ReleaseRejected('Release identity or schema mismatch')
    files = manifest['files']
    if not isinstance(files, dict) or set(files) != set(SOURCE_FILES):
        raise ReleaseRejected('Release file allowlist mismatch')
    expected = marker['manifest_sha256']
    actual = hashlib.sha256(raw_manifest).hexdigest()
    if expected != actual:
        raise ReleaseRejected('Release manifest was modified')
    for relative in SOURCE_FILES:
        if hashlib.sha256(_read_regular(release_dir / relative, immutable=True)).hexdigest() != files[relative]:
            raise ReleaseRejected('Staged code differs from verified manifest')
    return expected


def _stage_unlocked(root, source, release_id, *, fault_after=None):
    root = _private_root(root)
    _id(release_id)
    source = Path(source)
    if not source.is_dir() or source.is_symlink():
        raise ReleaseRejected('Source must be a normal directory')
    if (root / 'releases').is_symlink():
        raise ReleaseRejected('Releases path must not be a symlink')
    releases = root / 'releases'
    releases.mkdir(mode=0o700, exist_ok=True)
    if releases.stat().st_mode & 0o077:
        raise ReleaseRejected('Release directory is not private')
    candidate = releases / release_id
    candidate.mkdir(mode=0o700)  # refuses to overwrite any preexisting release
    try:
        hashes = {}
        for i, name in enumerate(SOURCE_FILES, start=1):
            src = source / name
            target = candidate / name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            data = _read_regular(src)
            _write_exclusive(target, data, 0o400)
            hashes[name] = hashlib.sha256(data).hexdigest()
            if fault_after == i:
                raise OSError('Deliberate staging failure at copy '+str(i))
        manifest_bytes = _json_bytes({'schema': 1, 'id': release_id, 'files': hashes})
        _write_exclusive(candidate / MANIFEST, manifest_bytes, 0o400)
        if fault_after == 'manifest':
            raise OSError('Deliberate staging failure after manifest')
        marker_bytes = _json_bytes({'id': release_id,
                                    'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest()})
        _write_exclusive(candidate / MARKER, marker_bytes, 0o400)
        _fsync_dir(candidate / 'service')
        _fsync_dir(candidate)
        _fsync_dir(releases)
        _fsync_dir(root)
        return _verify_release(root, release_id)
    except BaseException:
        # Staging is never considered active until a separate metadata promotion.
        # Cleanup only the version directory we just created, never another one.
        shutil.rmtree(candidate)
        raise


def _read_active(root, *, verify_releases=True):
    path = root / CURRENT
    try:
        path.lstat()
    except FileNotFoundError:
        return {'schema': 1, 'revision': 0, 'active': None, 'previous': None}
    data = _read_json(path, limit=4096)
    if (not isinstance(data, dict) or set(data) != {'schema', 'revision', 'active', 'previous'} or
            type(data.get('schema')) is not int or data['schema'] != 1 or
            type(data.get('revision')) is not int or data['revision'] < 0):
        raise ReleaseRejected('Invalid active release record')
    for k in ('active', 'previous'):
        if data.get(k) is not None:
            _id(data[k])
    if ((data['revision'] == 0) != (data['active'] is None) or
            (data['active'] is None and data['previous'] is not None) or
            (data['active'] is not None and data['active'] == data['previous'])):
        raise ReleaseRejected('Inconsistent active release record')
    # Previous may have been damaged while the active release remains healthy.
    # Only verify it when actually selecting it for rollback.
    if verify_releases and data.get('active') is not None:
        _verify_release(root, data['active'])
    return data


def _switch(root, old, update, *, verify_releases=True):
    path = root / CURRENT
    tmp = root / ('.active-' + uuid.uuid4().hex + '.tmp')
    content = _json_bytes(update)
    try:
        _write_exclusive(tmp, content)
        # Fail if an unexpected active record has changed during this operation.
        # This is not atomic CAS. The cooperative OS lock is required.
        if _read_active(root, verify_releases=verify_releases) != old:
            raise ReleaseRejected('Active release changed concurrently')
        os.replace(tmp, path)
        _fsync_dir(root)
    finally:
        tmp.unlink(missing_ok=True)
    return update


def _activate_unlocked(root, release_id, *, fault_before_commit=False):
    root = _private_root(root)
    _verify_release(root, release_id)
    old = _read_active(root, verify_releases=False)
    if old['active'] == release_id:
        raise ReleaseRejected('Release already active')
    update = {'schema': 1, 'revision': old['revision'] + 1,
              'active': release_id, 'previous': old['active']}
    if fault_before_commit:
        raise OSError('Injected failure before promotion; old release preserved')
    return _switch(root, old, update, verify_releases=False)


def _rollback_unlocked(root, *, fault_before_commit=False):
    root = _private_root(root)
    old = _read_active(root, verify_releases=False)
    previous = old.get('previous')
    if previous is None:
        raise ReleaseRejected('No verified previous release to restore')
    _verify_release(root, previous)
    update = {'schema': 1, 'revision': old['revision'] + 1,
              'active': previous, 'previous': old['active']}
    if fault_before_commit:
        raise OSError('Injected rollback failure; active record preserved')
    return _switch(root, old, update, verify_releases=False)


def stage(root, source, release_id, *, fault_after=None):
    root = _private_root(root)
    with _serialized(root):
        return _stage_unlocked(root, source, release_id, fault_after=fault_after)


def activate(root, release_id, *, fault_before_commit=False, expected_revision=None):
    root = _private_root(root)
    with _serialized(root):
        if expected_revision is not None and _read_active(root, verify_releases=False)['revision'] != expected_revision:
            raise ReleaseRejected('Stale release revision')
        return _activate_unlocked(root, release_id, fault_before_commit=fault_before_commit)


def rollback(root, *, fault_before_commit=False, expected_revision=None):
    root = _private_root(root)
    with _serialized(root):
        if expected_revision is not None and _read_active(root, verify_releases=False)['revision'] != expected_revision:
            raise ReleaseRejected('Stale release revision')
        return _rollback_unlocked(root, fault_before_commit=fault_before_commit)


def inspect(root):
    root = _private_root(root)
    return _read_active(root)
