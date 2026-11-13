#!/usr/bin/env python3
"""Crash-aware, no-clobber, checksum-verified Deck <-> Windows single-file copies.

No automatic retry of a transfer. Records only active/recovery work in a
private Steam Deck journal, and never deletes destination/source files.
"""
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

PARTIAL_DIR_NAME = "transfer-journal"
def _journal_dir(root):
    root = Path(root)
    directory = root / PARTIAL_DIR_NAME
    directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    if directory.is_symlink() or (directory.stat().st_mode & 0o077):
        raise PermissionError("Transfer journal directory must be private")
    return directory

class TransferJournal:
    def __init__(self, root, direction, source, destination, partial):
        self.path = _journal_dir(root) / (uuid.uuid4().hex + ".json")
        self.data = {
            "version": 2, "from": direction[0], "to": direction[1],
            "source": str(source), "destination": str(destination),
            "partial": str(partial), "started_unix": int(time.time()),
            "state": "preparing"
        }
        self._save(exclusive=True)

    def _save(self, exclusive=False):
        # Create/write to a private staging file, then atomically replace JSON.
        pending = self.path.with_suffix(".tmp")
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
        fd = os.open(pending, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(self.data, out, ensure_ascii=False, sort_keys=True)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(pending, self.path)
        finally:
            if pending.exists():
                pending.unlink()

    def stage(self, state, **kwargs):
        self.data.update(kwargs)
        self.data["state"] = state
        self._save()

    def complete(self):
        self.path.unlink(missing_ok=True)

def pending_transfers(root):
    directory = Path(root) / PARTIAL_DIR_NAME
    if not directory.exists():
        return []
    if directory.is_symlink():
        raise PermissionError("Refusing symlink transfer journal")
    pending = []
    for f in sorted(directory.glob("*.json"))[:1000]:
        if f.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}\.json", f.name):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            data["journal_id"] = f.stem
            pending.append(data)
        except (ValueError, OSError):
            pending.append({"journal_id": f.stem, "state": "unreadable"})
    return pending

def _remote_cleanup(b, partial):
    # The temporary filename is a fresh random transfer-owned path. Never
    # remove an original source or final destination here.
    name = b.quote_ps(str(partial))
    script = ("$ErrorActionPreference='Stop';"
              "if(Test-Path -LiteralPath " + name + " -PathType Leaf) {"
              "Remove-Item -LiteralPath " + name + " -Force -ErrorAction Stop}")
    b.powershell(script, timeout=12)

def _remote_commit_no_clobber(b, partial, destination):
    # .NET File.Move(old,new) refuses to replace an existing file.
    # Both paths are in the same directory/volume for an atomic rename.
    script = ("$ErrorActionPreference='Stop';"
              "[System.IO.File]::Move(" + b.quote_ps(str(partial)) + "," +
              b.quote_ps(str(destination)) + ")")
    b.powershell(script)

def _cleanup_local_partial(partial):
    partial.unlink(missing_ok=True)

def _local_destination(path):
    path = Path(path).expanduser()
    # Check symlinks before resolving; a dangling symlink must not be used.
    if path.is_symlink() or path.exists():
        raise FileExistsError("Steam Deck destination exists; overwrite prohibited: " + str(path))
    parent = path.parent.expanduser().resolve()
    if not parent.is_dir():
        raise FileNotFoundError("Steam Deck destination directory not found: " + str(parent))
    target = parent / path.name
    if target.is_symlink() or target.exists():
        raise FileExistsError("Steam Deck destination exists; overwrite prohibited: " + str(target))
    return target

def copy_v2(frm, source, to, target, backend):
    b = backend
    if frm == to or set((frm, to)) != {"steamdeck", "windows"}:
        raise ValueError("Transfers require explicitly naming steamdeck and windows")
    b.check_windows_pairing()
    root = b.ROOT
    if frm == "steamdeck":
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        target = str(target)
        b.quote_ps(target)  # Path validation before starting any transfer.
        if b.remote_exists(target):
            raise FileExistsError("Windows target exists; overwrite prohibited")
        partial = target + ".dc-part-" + uuid.uuid4().hex
        source_hash = b.local_hash(source)
        source_size = source.stat().st_size
        journal = TransferJournal(root, (frm,to), source,target,partial)
        committed = False
        try:
            journal.stage("uploading")
            b.sftp("put",source,partial)
            journal.stage("verifying")
            if b.local_hash(source) != source_hash:
                raise RuntimeError("Source file changed during upload; destination not committed")
            if b.remote_hash(partial) != source_hash:
                raise RuntimeError("SHA256 mismatch on Windows partial; destination not committed")
            journal.stage("committing")
            _remote_commit_no_clobber(b,partial,target)
            committed = True
            journal.stage("committed_verifying")
            if b.remote_hash(target) != source_hash:
                raise RuntimeError("Destination SHA256 mismatch AFTER Windows commit; inspect final file")
            journal.complete()
            return {"copied":True,"to":to,"bytes":source_size,"sha256":source_hash}
        except Exception as error:
            if committed:
                try: journal.stage("committed_needs_inspection", error=str(error)[:400])
                except Exception: pass
                raise
            try:
                _remote_cleanup(b,partial)
            except Exception as cleanup_error:
                try: journal.stage("cleanup_pending", error=str(error)[:400],
                                   cleanup_error=str(cleanup_error)[:300])
                except Exception: pass
                raise RuntimeError(str(error) + "; remote partial may remain: " + partial +
                                   "; recovery journal: " + str(journal.path)) from error
            else:
                journal.complete()
                raise
    # Windows -> Steam Deck
    target = _local_destination(target)
    source = str(source)
    b.quote_ps(source)
    if not b.remote_exists(source):
        raise FileNotFoundError("Windows source file not found: " + source)
    source_hash = b.remote_hash(source)
    fd, filename = tempfile.mkstemp(prefix=target.name+".dc-part-", dir=target.parent)
    os.close(fd)
    partial = Path(filename)
    try:
        journal = TransferJournal(root,(frm,to),source,target,partial)
    except Exception:
        _cleanup_local_partial(partial)
        raise
    committed = False
    try:
        journal.stage("downloading")
        b.sftp("get",source,partial)
        journal.stage("verifying")
        if b.local_hash(partial) != source_hash:
            raise RuntimeError("SHA256 mismatch on Deck partial; destination not committed")
        if b.remote_hash(source) != source_hash:
            raise RuntimeError("Windows source changed during download; destination not committed")
        journal.stage("committing")
        # os.link atomically creates a new name *only* if it doesn't exist.
        # Both paths share the parent filesystem, preserving no-clobber.
        os.link(partial,target)
        committed = True
        journal.stage("committed_cleaning")
        _cleanup_local_partial(partial)
        journal.complete()
        return {"copied":True,"to":to,"bytes":target.stat().st_size,"sha256":source_hash}
    except Exception as error:
        if committed:
            try: journal.stage("committed_needs_inspection",error=str(error)[:400])
            except Exception: pass
            raise
        try:
            _cleanup_local_partial(partial)
            journal.complete()
        except Exception as cleanup_error:
            try: journal.stage("cleanup_pending",error=str(error)[:400],
                               cleanup_error=str(cleanup_error)[:300])
            except Exception: pass
            raise RuntimeError(str(error)+"; local partial cleanup pending: "+str(partial)) from error
        raise