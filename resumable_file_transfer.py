#!/usr/bin/env python3
"""Explicit, hash-safe byte-resumable Deck <-> Windows file transfer.

Never replays a command automatically. Uses independent private manifests so
ordinary copy_file_between_devices keeps its original cleanup semantics.
"""
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from folder_backend import FolderBackend
from folder_transfer import directory_for, manifest_key, exclusive_operation_lock, atomic_json

SUBDIR='resumable-file-journal'
MAX_BYTES=40*1024**3


def prefix_hash(path, size):
    h=hashlib.sha256();remaining=size
    with open(path,'rb') as f:
        while remaining:
            chunk=f.read(min(remaining,1024*1024))
            if not chunk:raise IOError('Source unexpectedly shorter than verified prefix')
            h.update(chunk);remaining-=len(chunk)
    return h.hexdigest()


def remote_size(b,path):
    q=b.quote_ps(path)
    script=("$ErrorActionPreference='Stop';$f=Get-Item -LiteralPath "+q+" -Force -ErrorAction Stop;"+
            "if($f.PSIsContainer -or (($f.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Not a normal file'};"+
            "[Console]::Write([long]$f.Length)")
    text=b.powershell(script,timeout=35).strip()
    if not re.fullmatch(r'[0-9]+',text):raise RuntimeError('Invalid remote file length')
    return int(text)


def remote_prefix_hash(b,path,size):
    if not isinstance(size,int) or size<0 or size>MAX_BYTES:raise ValueError('Invalid prefix size')
    q=b.quote_ps(path)
    script=("$ErrorActionPreference='Stop';"+
            "$p="+q+";$remaining=[long]"+str(size)+";"+
            "$s=[System.IO.File]::OpenRead($p);$h=[Security.Cryptography.SHA256]::Create();"+
            "try{if($remaining -gt $s.Length){throw 'Prefix beyond source file'};"+
            "$buffer=New-Object byte[] 1048576;"+
            "while($remaining -gt 0){$n=$s.Read($buffer,0,[int][Math]::Min($remaining,1048576));"+
            "if($n -le 0){throw 'Unexpected remote end-of-file'};"+
            "[void]$h.TransformBlock($buffer,0,$n,$buffer,0);$remaining-=$n};"+
            "[void]$h.TransformFinalBlock([byte[]]::new(0),0,0);"+
            "[Console]::Write([BitConverter]::ToString($h.Hash).Replace('-','').ToLowerInvariant())"+
            "}finally{$h.Dispose();$s.Dispose()}")
    text=b.powershell(script,timeout=180).strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}',text):raise RuntimeError('Invalid Windows source prefix SHA-256')
    return text


def sftp_resume(b,kind,source,destination):
    # Validate existing SFTP rules and target absolute path before invocation.
    if kind=='upload':
        windows=destination
        local=str(source)
        command='reput'
        left=local;right=windows
    elif kind=='download':
        windows=source
        local=str(destination)
        command='reget'
        left=windows;right=local
    else:raise ValueError('Invalid SFTP resume direction')
    if not re.fullmatch(r'[A-Za-z]:/[^\r\n\x00]*',str(windows)):
        raise ValueError('Windows path must be absolute')
    windows='/'+str(windows).replace('\\','/')
    if kind=='upload':right=windows
    else:left=windows
    def quoted(value):
        value=str(value)
        if any(x in value for x in ('\n','\r','"','\x00')):raise ValueError('Unsupported filename characters for SFTP')
        return '"'+value.replace('\\','\\\\')+'"'
    batch=(command+' '+quoted(left)+' '+quoted(right)+'\n').encode()
    b.run(['sftp','-q','-b','-','-F',str(b.SSHCONF),'dc-windows'],data=batch,timeout=300)


def file_journal_dir(root):
    folder=Path(root)/SUBDIR
    folder.mkdir(mode=0o700,exist_ok=True)
    if folder.is_symlink() or folder.stat().st_mode&0o077:
        raise PermissionError('Resumable journal must be private')
    return folder


def pending_resumable_files(root):
    folder=file_journal_dir(root)
    items=[]
    for path in sorted(folder.glob('*.json')):
        if path.is_symlink():continue
        try:
            data=json.loads(path.read_text())
            items.append({k:data.get(k) for k in ('source_device','source','destination_device','destination','partial','state','size','error')})
        except (OSError,ValueError):items.append({'state':'unreadable','journal':path.name})
    return items


def resume_file(frm,source,to,destination,backend,*,resume=False):
    b=backend
    if {frm,to}!={'steamdeck','windows'} or frm==to:
        raise ValueError('Explicit Deck and Windows devices are required')
    if not isinstance(resume,bool):raise ValueError('resume must be boolean')
    b.check_windows_pairing()
    paths=FolderBackend(b)
    source=paths.normalize_root(frm,str(source))
    destination=paths.normalize_root(to,str(destination))
    if source==destination:raise ValueError('Identical source and destination')
    if paths.path_kind(frm,source)!='file':raise FileNotFoundError('Source is not an ordinary file: '+source)
    if to=='steamdeck':
        parent=str(Path(destination).parent)
    else:parent=destination.rsplit('/',1)[0]
    if paths.path_kind(to,parent)!='dir':raise FileNotFoundError('Destination parent directory does not exist')
    # Also reject Windows reparse point ancestors and Deck symlink ancestors.
    paths.ensure_directory(to,parent)
    folder=file_journal_dir(b.ROOT)
    key=manifest_key(frm,source,to,destination)
    record=folder/(key+'.json')
    with exclusive_operation_lock(folder/(key+'.lock')):
        target_kind=paths.path_kind(to,destination)
        if target_kind not in ('missing','file'):raise ValueError('Destination is not a normal file')
        if frm=='steamdeck':
            fullhash=b.local_hash(source);total=Path(source).stat().st_size
        else:
            fullhash=b.remote_hash(source);total=remote_size(b,source)
        if total>MAX_BYTES:raise ValueError('File exceeds 40 GiB safety limit')
        if resume:
            if not record.is_file():raise FileNotFoundError('No interrupted resumable copy recorded for these exact paths')
            job=json.loads(record.read_text())
            expected={'source_device':frm,'destination_device':to,'source':source,'destination':destination,'source_sha256':fullhash,'size':total}
            for k,v in expected.items():
                if job.get(k)!=v:raise RuntimeError('Source changed since transfer started; resume refused ('+k+')')
            partial=job['partial']
            # Never use an untrusted journal to target arbitrary paths.
            if not re.fullmatch(re.escape(destination)+r'\.dc-resume-[0-9a-f]{32}',partial) and to=='windows':
                raise ValueError('Unsafe Windows journal partial path')
            if to=='steamdeck':
                p=Path(partial)
                if p.parent!=Path(destination).parent or not re.fullmatch(re.escape(Path(destination).name)+r'\.dc-resume-[0-9a-f]{32}',p.name):
                    raise ValueError('Unsafe Steam Deck journal partial path')
        else:
            if record.exists():raise FileExistsError('A resumable transfer is already pending; use resume=true')
            if target_kind!='missing':raise FileExistsError('Destination already exists; overwrite prohibited')
            partial=destination+'.dc-resume-'+uuid.uuid4().hex
            job={'version':1,'source_device':frm,'source':source,'destination_device':to,
                 'destination':destination,'partial':partial,'source_sha256':fullhash,
                 'size':total,'created_unix':int(time.time()),'state':'starting'}
            atomic_json(record,job)
        def stage(status,error=None):
            job['state']=status
            if error is not None:job['error']=str(error)[:500]
            else:job.pop('error',None)
            job['updated_unix']=int(time.time())
            atomic_json(record,job)
        try:
            if target_kind=='file':
                if paths.file_hash(to,destination)!=fullhash:
                    raise FileExistsError('Existing destination differs from source; refusing overwrite')
                stage('committed_verified')
                # After a previous crash between commit and record removal,
                # removing only the known private temporary file is safe.
                if to=='steamdeck':
                    p=Path(partial)
                    if p.is_file() and not p.is_symlink():p.unlink()
                else:
                    if paths.path_kind(to,partial)=='file':
                        b.powershell("$ErrorActionPreference='Stop';Remove-Item -LiteralPath "+b.quote_ps(partial)+" -Force",timeout=25)
                record.unlink(missing_ok=True)
                return {'copied':True,'resumed':True,'bytes_reused':total,'bytes_transferred':0,'bytes':total,'sha256':fullhash}
            if to=='windows':
                kind=paths.path_kind(to,partial)
                if kind not in ('missing','file'):raise RuntimeError('Unsafe remote partial type')
                done=remote_size(b,partial) if kind=='file' else 0
                if done>total:raise RuntimeError('Remote partial larger than source; resume refused')
                if done and b.remote_hash(partial)!=prefix_hash(source,done):
                    raise RuntimeError('Remote partial differs from source prefix; resume refused')
                stage('uploading')
                if done:
                    if done<total:sftp_resume(b,'upload',source,partial)
                else:b.sftp('put',source,partial)
                stage('verifying')
                if b.remote_hash(partial)!=fullhash:raise RuntimeError('Windows SHA256 mismatch after partial resume')
                if b.local_hash(source)!=fullhash:raise RuntimeError('Source changed during upload; commit refused')
                stage('committing')
                from transfer_reliability import _remote_commit_no_clobber
                _remote_commit_no_clobber(b,partial,destination)
                stage('committed_verifying')
                if b.remote_hash(destination)!=fullhash:raise RuntimeError('Post-commit Windows SHA256 mismatch')
            else:
                p=Path(partial)
                if p.is_symlink():raise RuntimeError('Local partial is a symlink; resume refused')
                if p.exists() and not p.is_file():raise RuntimeError('Local partial is not a regular file')
                done=p.stat().st_size if p.exists() else 0
                if done>total:raise RuntimeError('Local partial larger than Windows source')
                if done and prefix_hash(partial,done)!=remote_prefix_hash(b,source,done):
                    raise RuntimeError('Local partial differs from Windows source prefix; resume refused')
                stage('downloading')
                if done:
                    if done<total:sftp_resume(b,'download',source,partial)
                else:b.sftp('get',source,partial)
                stage('verifying')
                if b.local_hash(partial)!=fullhash:raise RuntimeError('Deck SHA256 mismatch after partial resume')
                if b.remote_hash(source)!=fullhash:raise RuntimeError('Windows source changed during download')
                stage('committing')
                os.link(partial,destination)
                stage('committed_cleaning')
                p.unlink()
            record.unlink(missing_ok=True)
            return {'copied':True,'resumed':resume,'bytes_reused':done,'bytes_transferred':total-done,
                    'bytes':total,'sha256':fullhash}
        except Exception as error:
            stage('interrupted',error)
            raise