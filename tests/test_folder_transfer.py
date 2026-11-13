#!/usr/bin/env python3
"""Offline tests. Never use real Windows/SSH or user project directories."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from folder_transfer import folder_copy, canonical_entries, safe_relative, directory_for, manifest_key, exclusive_operation_lock
from folder_backend import FolderBackend

class SimulatedDevices:
    def __init__(self,root):
        self.base=Path(root)
        self.ROOT=self.base/'manager';self.ROOT.mkdir(mode=0o700)
        self.deck=self.base/'deck';self.deck.mkdir()
        self.windows=self.base/'windows';self.windows.mkdir()
        self.calls=[];self.failure_at=None
        self.srcdeck=str(self.deck/'source')
        self.dstdeck=str(self.deck/'target')
        self.srcwin='C:/Test/source'
        self.dstwin='C:/Test/target'
    def check_windows_pairing(self):pass
    def lookup(self,device,path):
        if device=='steamdeck':
            p=Path(path)
            assert str(p).startswith(str(self.deck)),repr(p)
            return p
        if device=='windows':
            p=str(path).replace('\\','/')
            assert p.startswith('C:/Test/'),p
            return self.windows / p[len('C:/Test/'):]
        raise ValueError(device)
    def normalize_root(self,device,path):
        p=self.lookup(device,path)
        if device=='windows':
            return 'C:/Test/'+str(p.relative_to(self.windows)).replace('\\','/')
        return str(p)
    def list_tree(self,device,root):
        p=self.lookup(device,root)
        if not p.is_dir():raise NotADirectoryError(root)
        items=[]
        for item in p.rglob('*'):
            if item.is_symlink():raise ValueError('Source symlink')
            rel=item.relative_to(p).as_posix()
            items.append({'rel':rel,'type':'dir' if item.is_dir() else 'file',**({'size':item.stat().st_size} if item.is_file() else {})})
        return items
    def path_kind(self,device,path):
        p=self.lookup(device,path)
        if p.is_symlink():return 'other'
        if p.is_file():return 'file'
        if p.is_dir():return 'dir'
        return 'missing'
    def ensure_directory(self,device,path):
        p=self.lookup(device,path)
        if p.is_symlink() or (p.exists() and not p.is_dir()):raise ValueError('unsafe directory')
        p.mkdir(parents=True,exist_ok=True)
    def file_hash(self,device,path):
        p=self.lookup(device,path)
        h=hashlib.sha256(p.read_bytes()).hexdigest()
        self.calls.append(('hash',device,path))
        return h
    def copy_file(self,frm,src,to,dst):
        self.calls.append(('copy',frm,to,dst))
        if self.failure_at is not None and sum(c[0]=='copy' for c in self.calls)==self.failure_at:
            raise ConnectionError('simulated link failure')
        srcfile=self.lookup(frm,src);dstfile=self.lookup(to,dst)
        if dstfile.exists():raise FileExistsError(dst)
        shutil.copyfile(srcfile,dstfile)
        return {'copied':True,'sha256':self.file_hash(to,dst),'bytes':dstfile.stat().st_size}

class FolderTransferTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='dc-folder-sim-')
        self.addCleanup(temp.cleanup)
        self.b=SimulatedDevices(temp.name)
        self.source=Path(self.b.srcdeck)
        self.source.mkdir()
        (self.source/'art').mkdir();(self.source/'empty').mkdir()
        (self.source/'art'/'panel01.png').write_bytes(b'MANGA_PANEL_01')
        (self.source/'art'/'panel02.png').write_bytes(b'MANGA_PANEL_02')
        (self.source/'a.txt').write_bytes(b'hello')
    def winroot(self):return self.b.lookup('windows',self.b.dstwin)
    def journal(self):return list((self.b.ROOT/'folder-transfer-journal').glob('*.json'))
    def transfer(self,resume=False):return folder_copy('steamdeck',self.b.srcdeck,'windows',self.b.dstwin,self.b,resume=resume)
    def test_full_folder_copy_nested_and_empty(self):
        result=self.transfer()
        self.assertEqual(result['total_files'],3)
        self.assertEqual(result['copied_files'],3)
        self.assertTrue((self.winroot()/'empty').is_dir())
        self.assertEqual((self.winroot()/'art'/'panel01.png').read_bytes(),b'MANGA_PANEL_01')
        self.assertEqual(self.journal(),[])
    def test_interruption_and_resume_skips_verified(self):
        self.b.failure_at=2
        with self.assertRaises(ConnectionError):self.transfer()
        self.assertEqual(len(self.journal()),1)
        self.assertEqual(len(list(self.winroot().rglob('*.png'))),0) # first file is a.txt
        self.assertEqual((self.winroot()/'a.txt').read_bytes(),b'hello')
        self.b.failure_at=None;self.b.calls.clear()
        r=self.transfer(resume=True)
        self.assertEqual(r['verified_existing_files'],1)
        self.assertEqual(r['copied_files'],2)
        self.assertEqual(len([c for c in self.b.calls if c[0]=='copy']),2)
        self.assertEqual(self.journal(),[])
    def test_resume_rejects_modified_existing(self):
        self.b.failure_at=2
        with self.assertRaises(ConnectionError):self.transfer()
        (self.winroot()/'a.txt').write_bytes(b'CHANGED_BY_SOMEONE')
        self.b.failure_at=None
        with self.assertRaises(FileExistsError):self.transfer(resume=True)
        self.assertEqual((self.winroot()/'a.txt').read_bytes(),b'CHANGED_BY_SOMEONE')
        self.assertEqual(len(self.journal()),1)
    def test_changed_manifest_cannot_resume(self):
        self.b.failure_at=2
        with self.assertRaises(ConnectionError):self.transfer()
        (self.source/'newfile.txt').write_text('new')
        self.b.failure_at=None
        with self.assertRaisesRegex(RuntimeError,'manifest differs'):self.transfer(resume=True)
        self.assertEqual(len(self.journal()),1)
    def test_no_resume_when_job_exists(self):
        self.b.failure_at=2
        with self.assertRaises(ConnectionError):self.transfer()
        with self.assertRaises(FileExistsError):self.transfer(resume=False)
    def test_resume_requires_matching_journal(self):
        with self.assertRaises(FileNotFoundError):self.transfer(resume=True)
        self.assertFalse(self.winroot().exists())
    def test_existing_destination_not_clobbered(self):
        self.winroot().mkdir();(self.winroot()/'do-not-touch').write_text('old')
        with self.assertRaises(FileExistsError):self.transfer()
        self.assertEqual((self.winroot()/'do-not-touch').read_text(),'old')
        self.assertEqual(self.journal(),[])
    def test_copy_windows_to_deck(self):
        source=self.b.lookup('windows',self.b.srcwin)
        source.mkdir(parents=True)
        (source/'chapter').mkdir()
        (source/'chapter'/'scene.txt').write_text('scene')
        result=folder_copy('windows',self.b.srcwin,'steamdeck',self.b.dstdeck,self.b)
        self.assertEqual(result['copied_files'],1)
        self.assertEqual((Path(self.b.dstdeck)/'chapter'/'scene.txt').read_text(),'scene')
        self.assertEqual(self.journal(),[])
    def test_empty_folder(self):
        for p in sorted(self.source.rglob('*'),reverse=True):
            if p.is_file():p.unlink()
            else:p.rmdir()
        (self.source/'empty').mkdir()
        result=self.transfer()
        self.assertEqual(result['copied_files'],0)
        self.assertTrue((self.winroot()/'empty').is_dir())
    def test_source_symlink_rejected(self):
        (self.source/'evil').symlink_to(self.source/'a.txt')
        with self.assertRaises(ValueError):self.transfer()
        self.assertFalse(self.winroot().exists())
    def test_unresolved_dc_partial_rejected(self):
        (self.source/'readme.txt.dc-part-0123456789abcdef0123456789abcdef').write_bytes(b'partial')
        with self.assertRaisesRegex(ValueError,'unresolved DC transfer partial'):self.transfer()
        self.assertFalse(self.winroot().exists())

    def test_legacy_dc_partial_rejected(self):
        (self.source/'readme.txt.dc-part-0123456789').write_bytes(b'partial')
        with self.assertRaisesRegex(ValueError,'unresolved DC transfer partial'):self.transfer()
        self.assertFalse(self.winroot().exists())

    def test_case_collision_rejected(self):
        (self.source/'A.txt').write_bytes(b'A')
        with self.assertRaisesRegex(ValueError,'Case-insensitive'):self.transfer()
        self.assertFalse(self.winroot().exists())
    def test_windows_reserved_name_rejected(self):
        (self.source/'CON.txt').write_bytes(b'not valid on windows')
        with self.assertRaisesRegex(ValueError,'reserved'):self.transfer()
        self.assertFalse(self.winroot().exists())
    def test_path_traversal_rejected(self):
        for x in ('../oops','a/../oops','/absolute','a//b','a/./b','a\\b'):
            with self.subTest(x=x),self.assertRaises(ValueError):safe_relative(x)
    def test_explicit_devices_required(self):
        with self.assertRaises(ValueError):folder_copy('steamdeck',self.b.srcdeck,'steamdeck',self.b.dstdeck,self.b)
    def test_journal_permissions(self):
        self.b.failure_at=2
        with self.assertRaises(ConnectionError):self.transfer()
        f=self.journal()[0]
        self.assertEqual(f.stat().st_mode&0o077,0)
        self.assertEqual(f.parent.stat().st_mode&0o077,0)
    def test_concurrent_lock(self):
        folder=directory_for(self.b.ROOT)
        with exclusive_operation_lock(folder/'running.lock'):
            with self.assertRaises(RuntimeError):
                with exclusive_operation_lock(folder/'running.lock'):
                    pass
    def test_no_overwrite_on_existing_file_during_copy(self):
        original=self.b.copy_file
        def colliding(frm,src,to,dst):
            p=self.b.lookup(to,dst)
            p.write_bytes(b'ANOTHER_PROCESS')
            return original(frm,src,to,dst)
        self.b.copy_file=colliding
        with self.assertRaises(FileExistsError):self.transfer()
        self.assertEqual((self.winroot()/'a.txt').read_bytes(),b'ANOTHER_PROCESS')
        self.assertEqual(len(self.journal()),1)
    def test_same_size_manifest_change_cannot_silently_overwrite(self):
        self.b.failure_at=2
        with self.assertRaises(ConnectionError):self.transfer()
        (self.source/'a.txt').write_bytes(b'HELLO')
        self.b.failure_at=None
        with self.assertRaises(FileExistsError):self.transfer(resume=True)
        self.assertEqual((self.winroot()/'a.txt').read_bytes(),b'hello')

if __name__=='__main__':unittest.main(verbosity=2)