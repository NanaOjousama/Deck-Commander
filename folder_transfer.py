#!/usr/bin/env python3
"""Verified per-file resumable *folder* transfer, independent of MCP gateway.

A folder resume skips only byte-identical completed files. It deliberately does
not pretend to support resuming bytes within an unfinished single file.
"""
import fcntl
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

JOURNAL_SUBDIR = 'folder-transfer-journal'
MAX_FILES = 2000
MAX_TOTAL_BYTES = 20 * 1024**3
WINDOWS_ILLEGAL = set('<>:"\\|?*')
WINDOWS_RESERVED = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}


def safe_relative(rel):
    if not isinstance(rel,str) or not rel or len(rel)>1600 or '\\' in rel or '\x00' in rel:
        raise ValueError('Unsafe folder entry path')
    path=PurePosixPath(rel)
    parts=rel.split('/')
    if path.is_absolute() or any(p in ('','.','..') for p in parts):
        raise ValueError('Unsafe relative path: '+repr(rel))
    for part in parts:
        if any(ord(ch)<32 or ch in WINDOWS_ILLEGAL for ch in part) or part.rstrip(' .')!=part:
            raise ValueError('Filename not safely portable to Windows: '+repr(rel))
        if part.split('.')[0].upper() in WINDOWS_RESERVED:
            raise ValueError('Windows reserved filename: '+repr(rel))
    return rel


def canonical_entries(entries):
    files=[]; directories=[];seen={}
    for raw in entries:
        rel=safe_relative(raw['rel'])
        typ=raw['type']
        key=rel.casefold()
        if key in seen:
            raise ValueError('Case-insensitive filename collision: '+repr(rel)+' vs '+repr(seen[key]))
        seen[key]=rel
        if typ=='file':
            # A prior interrupted single-file copy can leave this DC-owned
            # temporary file in a source folder. Never silently publish it as
            # user content during a recursive transfer.
            if re.search(r'\.dc-part-(?:[0-9a-f]{10}|[0-9a-f]{32})$', rel):
                raise ValueError('Source contains unresolved DC transfer partial: ' + rel +
                                 '; inspect pending-transfers before copying this folder')
            size=raw['size']
            if not isinstance(size,int) or isinstance(size,bool) or size<0:
                raise ValueError('Invalid source file size')
            files.append({'rel':rel,'size':size,'type':'file'})
        elif typ=='dir':directories.append({'rel':rel,'type':'dir'})
        else:raise ValueError('Source contains unsupported file/link type: '+repr(rel))
    if len(files)>MAX_FILES:
        raise ValueError(f'Folder contains {len(files)} files; limit is {MAX_FILES}')
    total=sum(x['size'] for x in files)
    if total>MAX_TOTAL_BYTES:raise ValueError('Folder exceeds safety limit of 20 GiB')
    if len(directories)>MAX_FILES*2:
        raise ValueError('Folder contains too many directories')
    files.sort(key=lambda x:x['rel'].casefold())
    directories.sort(key=lambda x:(x['rel'].count('/'), x['rel'].casefold()))
    return files,directories,total


def directory_for(root):
    directory=Path(root)/JOURNAL_SUBDIR
    directory.mkdir(mode=0o700,exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise PermissionError('Folder journal must be private')
    return directory


def manifest_key(frm,src,to,dst):
    description=json.dumps([frm,str(src),to,str(dst)],ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(description.encode()).hexdigest()[:32]


def atomic_json(path, content):
    temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(content,f,ensure_ascii=False,sort_keys=True)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


@contextmanager
def exclusive_operation_lock(path):
    fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as e:raise RuntimeError('This folder transfer is already running') from e
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def join_target(root,rel,device):
    safe_relative(rel)
    if device=='windows':return root.rstrip('/\\') + '/' + rel
    return str(Path(root).joinpath(*rel.split('/')))


def folder_copy(frm, source, to, target, backend, *, resume=False):
    if frm == to or {frm,to}!={'steamdeck','windows'}:
        raise ValueError('Folder copies require explicit Steam Deck and Windows devices')
    if not isinstance(resume,bool):raise ValueError('resume must be boolean')
    backend.check_windows_pairing()
    # Normalize source and destination without silently changing device routing.
    src=backend.normalize_root(frm,source)
    dst=backend.normalize_root(to,target)
    if src==dst:raise ValueError('Source and destination cannot be identical')
    directory=directory_for(backend.ROOT)
    jobid=manifest_key(frm,src,to,dst)
    manifest_path=directory/(jobid+'.json')
    with exclusive_operation_lock(directory/(jobid+'.lock')):
        entries=backend.list_tree(frm,src)
        files,dirs,total=canonical_entries(entries)
        expected={'version':1,'source_device':frm,'source':src,
                  'destination_device':to,'destination':dst,
                  'files':files,'directories':dirs,'total_bytes':total}
        root_kind=backend.path_kind(to,dst)
        if not resume:
            if manifest_path.exists():raise FileExistsError('An interrupted folder copy exists; call again with resume=true')
            if root_kind!='missing':raise FileExistsError('Folder destination already exists; overwrite prohibited')
            state=dict(expected,started_unix=int(time.time()),state='preparing',processed=0,copied=0,skipped=0)
            atomic_json(manifest_path,state)
        else:
            if not manifest_path.is_file():raise FileNotFoundError('No matching interrupted folder job found to resume')
            state=json.loads(manifest_path.read_text(encoding='utf-8'))
            for key in expected:
                if state.get(key)!=expected[key]:
                    raise RuntimeError('Source folder manifest differs from prior run; refusing unsafe resume ('+key+')')
            if root_kind not in ('dir','missing'):raise FileExistsError('Destination path not a directory')
            state.update(state='resuming',processed=0,copied=0,skipped=0)
            atomic_json(manifest_path,state)
        try:
            backend.ensure_directory(to,dst)
            for item in dirs:
                backend.ensure_directory(to,join_target(dst,item['rel'],to))
            for index,item in enumerate(files):
                rel=item['rel']
                src_file=join_target(src,rel,frm)
                dst_file=join_target(dst,rel,to)
                dest_kind=backend.path_kind(to,dst_file)
                if dest_kind=='file':
                    if not resume:
                        raise FileExistsError('Destination file already exists: '+dst_file)
                    if backend.file_hash(frm,src_file)!=backend.file_hash(to,dst_file):
                        raise FileExistsError('Existing destination differs; refusing overwrite: '+dst_file)
                    state['skipped']+=1
                elif dest_kind=='missing':
                    backend.ensure_directory(to,str(Path(dst_file).parent) if to=='steamdeck' else dst_file.replace('\\','/').rsplit('/',1)[0])
                    backend.copy_file(frm,src_file,to,dst_file)
                    state['copied']+=1
                else:raise ValueError('Destination is not a regular file: '+dst_file)
                state.update(state='copying',processed=index+1,last_file=rel)
                atomic_json(manifest_path,state)
            result={'copied_files':state['copied'],'verified_existing_files':state['skipped'],
                    'total_files':len(files),'total_bytes':total,'to':to,
                    'destination':dst,'resumed':resume,'sha256_verified':True}
            manifest_path.unlink()
            return result
        except Exception as err:
            state.update(state='interrupted',error=str(err)[:600],updated_unix=int(time.time()))
            atomic_json(manifest_path,state)
            raise