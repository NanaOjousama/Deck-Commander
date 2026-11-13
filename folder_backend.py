#!/usr/bin/env python3
"""Read-only tree enumeration and guarded directory creation for folder_copy."""
import json
import os
import re
import stat
from pathlib import Path

from folder_transfer import safe_relative

class FolderBackend:
    def __init__(self,router):
        self.b=router
        self.ROOT=router.ROOT
        self._ensured_directories=set()

    def check_windows_pairing(self):return self.b.check_windows_pairing()

    def normalize_root(self,device,raw):
        if not isinstance(raw,str) or '\x00' in raw or '\r' in raw or '\n' in raw:
            raise ValueError('Invalid folder path')
        if device=='steamdeck':
            p=Path(raw).expanduser()
            if not p.is_absolute():raise ValueError('Use an absolute Steam Deck path')
            # Reject symlinks before resolution, to avoid copying from/to
            # locations outside the user's explicitly chosen tree.
            for ancestor in (p,*p.parents):
                if ancestor.is_symlink():raise ValueError('Folder path contains symlink: '+str(ancestor))
            p=p.resolve(strict=False)
            if str(p)=='/':raise ValueError('Refusing filesystem-root folder operation')
            return str(p)
        if device!='windows':raise ValueError('Unknown device')
        value=raw.replace('\\','/')
        if not re.fullmatch(r'[A-Za-z]:/[^\x00\r\n]+',value):
            raise ValueError('Use an absolute Windows folder path C:/...')
        parts=value[3:].rstrip('/').split('/')
        if not parts or any(x in ('','.','..') for x in parts):
            raise ValueError('Invalid Windows folder path components')
        # The root may contain spaces; reject all unportable or special segments.
        for x in parts:safe_relative(x)
        return value[:3]+('/'.join(parts))

    def _local_kind(self,path):
        p=Path(path)
        if p.is_symlink():return 'other'
        for a in p.parents:
            if a.is_symlink():return 'other'
        try:m=p.lstat().st_mode
        except FileNotFoundError:return 'missing'
        if stat.S_ISREG(m):return 'file'
        if stat.S_ISDIR(m):return 'dir'
        return 'other'

    def path_kind(self,device,path):
        if device=='steamdeck':return self._local_kind(path)
        if device!='windows':raise ValueError('Unknown device')
        q=self.b.quote_ps(path)
        code=("$ErrorActionPreference='Stop';"+
              "$p="+q+";"+
              "if(-not(Test-Path -LiteralPath $p)){[Console]::Write('missing')}"+
              "else{$i=Get-Item -LiteralPath $p -Force -ErrorAction Stop;"+
              "if(($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){[Console]::Write('other')}"+
              "elseif($i.PSIsContainer){[Console]::Write('dir')}else{[Console]::Write('file')}}")
        kind=self.b.powershell(code,timeout=30).strip()
        if kind not in ('missing','file','dir','other'):raise RuntimeError('Invalid remote path kind')
        return kind

    def ensure_directory(self,device,path):
        key=(device,str(path))
        if key in self._ensured_directories:
            return
        if device=='steamdeck':
            p=Path(path)
            if self._local_kind(p)=='other':raise ValueError('Unsafe local directory path')
            # Refuse following any pre-existing link in the parent chain.
            for anc in (p,*p.parents):
                if anc.is_symlink():raise ValueError('Directory parent is symlink: '+str(anc))
            p.mkdir(parents=True,exist_ok=True)
            if self._local_kind(p)!='dir':raise ValueError('Directory not created safely')
            self._ensured_directories.add(key)
            return
        if device!='windows':raise ValueError('Unknown device')
        p=self.normalize_root('windows',str(path))
        q=self.b.quote_ps(p)
        # Reject reparse-point ancestors (junctions/symlinks), not just the
        # named destination. Never remove/overwrite an existing directory.
        code=("$ErrorActionPreference='Stop';$p="+q+";"+
              "$drive=[System.IO.Path]::GetPathRoot($p);"+
              "$parts=$p.Substring($drive.Length).Split([char]'/');$current=$drive;"+
              "foreach($piece in $parts){if($piece -eq ''){continue};"+
              "$current=[System.IO.Path]::Combine($current,$piece);"+
              "if(Test-Path -LiteralPath $current){"+
              "$item=Get-Item -LiteralPath $current -Force -ErrorAction Stop;"+
              "if(-not $item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Unsafe destination directory'}"+
              "}else{[System.IO.Directory]::CreateDirectory($current) | Out-Null}}")
        self.b.powershell(code,timeout=35)
        self._ensured_directories.add(key)

    def list_tree(self,device,root):
        if device=='steamdeck':
            p=Path(root)
            if self._local_kind(p)!='dir':raise NotADirectoryError(root)
            entries=[]
            for base,dirs,files in os.walk(root,followlinks=False):
                dirs.sort();files.sort()
                for name in dirs:
                    f=Path(base)/name
                    if self._local_kind(f)!='dir':raise ValueError('Source contains link or non-directory: '+str(f))
                    rel=f.relative_to(p).as_posix();safe_relative(rel)
                    entries.append({'rel':rel,'type':'dir'})
                for name in files:
                    f=Path(base)/name
                    if self._local_kind(f)!='file':raise ValueError('Source contains link or non-file: '+str(f))
                    rel=f.relative_to(p).as_posix();safe_relative(rel)
                    entries.append({'rel':rel,'type':'file','size':f.stat().st_size})
                if len(entries)>6000:raise ValueError('Source tree exceeds folder safety limit')
            return entries
        if device!='windows':raise ValueError('Unknown device')
        q=self.b.quote_ps(root)
        code=("$ErrorActionPreference='Stop';"+
              "$root=Get-Item -LiteralPath "+q+" -Force -ErrorAction Stop;"+
              "if(-not $root.PSIsContainer){throw 'Source is not a directory'};"+
              "if(($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Source root reparse point'};"+
              "$base=$root.FullName.TrimEnd([char]'\\');"+
              "$n=0;$entries=@(Get-ChildItem -LiteralPath $root.FullName -Recurse -Force -ErrorAction Stop | ForEach-Object {"+
              "$script:n++;if($script:n -gt 6000){throw 'Source tree exceeds folder safety limit'};"+
              "if(($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Source contains a reparse point'};"+
              "$rel=$_.FullName.Substring($base.Length+1).Replace('\\','/');"+
              "if($_.PSIsContainer){[PSCustomObject]@{rel=$rel;type='dir'}}"+
              "else{[PSCustomObject]@{rel=$rel;type='file';size=[int64]$_.Length}}"+
              "});"+
              "[Console]::Write((ConvertTo-Json -InputObject @($entries) -Depth 3 -Compress))")
        data=self.b.powershell(code,timeout=120)
        rows=json.loads(data)
        if not isinstance(rows,list):rows=[rows]
        return rows

    def file_hash(self,device,path):
        if device=='steamdeck':
            if self._local_kind(path)!='file':raise FileNotFoundError(path)
            return self.b.local_hash(path)
        return self.b.remote_hash(path)

    def copy_file(self,frm,source,to,destination):
        return self.b.copy(frm,source,to,destination)