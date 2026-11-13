#!/usr/bin/env python3
"""Offline failure-injection tests: only temporary fake Deck/Windows files."""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import transfer_reliability as safe

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):
            h.update(b)
    return h.hexdigest()

class FakeBackend:
    def __init__(self,root):
        self.ROOT=root/'manager'
        self.ROOT.mkdir(mode=0o700)
        self.remoteroot=root/'fake_windows'
        self.remoteroot.mkdir()
        self.controls=set()
        self.actions=[]
    def check_windows_pairing(self): return True
    def qpath(self,p):
        raw=str(p).replace('\\','/')
        assert raw.startswith('C:/Test/'),raw
        rel=raw[len('C:/Test/'):]
        assert '/' not in rel and '..' not in rel
        return self.remoteroot/rel
    def quote_ps(self,p):
        if not p or '\n' in p or '\r' in p or '\0' in p:raise ValueError('Invalid path')
        return "'"+str(p).replace("'","''")+"'"
    def remote_exists(self,p):return self.qpath(p).exists()
    def remote_hash(self,p):
        if 'bad_postcommit' in self.controls and self.qpath(p).name=='destination.bin':
            return '0'*64
        return sha(self.qpath(p))
    def local_hash(self,p):return sha(p)
    def sftp(self,op,src,dst):
        self.actions.append('sftp-'+op)
        if op=='put':
            shutil.copyfile(src,self.qpath(dst))
            if 'corrupt_upload' in self.controls:self.qpath(dst).write_bytes(b'CORRUPTED')
            if 'change_upload_source' in self.controls:Path(src).write_bytes(b'MUTATED_AFTER_UPLOAD')
            if 'fail_upload' in self.controls:raise OSError('simulated transfer interruption')
        elif op=='get':
            shutil.copyfile(self.qpath(src),dst)
            if 'corrupt_download' in self.controls:Path(dst).write_bytes(b'CORRUPTED')
            if 'change_download_source' in self.controls:self.qpath(src).write_bytes(b'MUTATED_AFTER_DOWNLOAD')
            if 'fail_download' in self.controls:raise OSError('simulated transfer interruption')
        else:raise ValueError(op)
    def powershell(self,script,timeout=60):
        fields=[x.replace("''","'") for x in re.findall(r"'((?:''|[^'])*)'",script)]
        fields=[x for x in fields if x!='Stop'] # Ignore the fixed PowerShell preference literal.
        if '[System.IO.File]::Move' in script:
            self.actions.append('remote-no-clobber-commit')
            assert len(fields)>=2
            src,dst=self.qpath(fields[0]),self.qpath(fields[1])
            if 'race_remote_destination' in self.controls:
                dst.write_bytes(b'THIRD_PARTY')
            if dst.exists():raise FileExistsError('simulated no-clobber remote move')
            src.rename(dst)
            return ''
        if 'Remove-Item -LiteralPath' in script:
            self.actions.append('remote-partial-cleanup')
            if 'fail_cleanup' in self.controls:raise OSError('simulated Windows link lost during cleanup')
            if fields:self.qpath(fields[0]).unlink(missing_ok=True)
            return ''
        raise AssertionError('Unexpected PowerShell:'+script[:180])

class TransferReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(prefix='dc-transfer-test-')
        self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.b=FakeBackend(self.root)
        self.decksource=self.root/'source.bin'
        self.decksource.write_bytes(b'APPROVED_MANGA_REFERENCE_DATA_'*100)
        self.deckdest=self.root/'downloaded.bin'
        self.remote_source='C:/Test/source.bin'
        self.remote_dest='C:/Test/destination.bin'
        self.b.qpath(self.remote_source).write_bytes(b'WINDOWS_MANGA_DATA_'*100)
    def journal(self):return safe.pending_transfers(self.b.ROOT)
    def remote_partials(self):return list(self.b.remoteroot.glob('*.dc-part-*'))
    def deck_partials(self):return list(self.root.glob('*.dc-part-*'))
    def upload(self):return safe.copy_v2('steamdeck',self.decksource,'windows',self.remote_dest,self.b)
    def download(self):return safe.copy_v2('windows',self.remote_source,'steamdeck',self.deckdest,self.b)
    def test_upload_success(self):
        expected=self.decksource.read_bytes();r=self.upload()
        self.assertEqual(self.b.qpath(self.remote_dest).read_bytes(),expected)
        self.assertEqual(r['sha256'],hashlib.sha256(expected).hexdigest())
        self.assertEqual(self.journal(),[])
        self.assertEqual(self.remote_partials(),[])
        self.assertIn('remote-no-clobber-commit',self.b.actions)
    def test_download_success(self):
        expected=self.b.qpath(self.remote_source).read_bytes();r=self.download()
        self.assertEqual(self.deckdest.read_bytes(),expected)
        self.assertEqual(r['sha256'],hashlib.sha256(expected).hexdigest())
        self.assertEqual(self.journal(),[])
        self.assertEqual(self.deck_partials(),[])
    def test_upload_preexisting_target_no_overwrite(self):
        self.b.qpath(self.remote_dest).write_bytes(b'KEEP_ME')
        with self.assertRaises(FileExistsError):self.upload()
        self.assertEqual(self.b.qpath(self.remote_dest).read_bytes(),b'KEEP_ME')
        self.assertEqual(self.journal(),[])
    def test_download_preexisting_target_no_overwrite(self):
        self.deckdest.write_bytes(b'KEEP_ME')
        with self.assertRaises(FileExistsError):self.download()
        self.assertEqual(self.deckdest.read_bytes(),b'KEEP_ME')
        self.assertEqual(self.journal(),[])
    def test_upload_partial_corruption_cleaned(self):
        self.b.controls.add('corrupt_upload')
        with self.assertRaisesRegex(RuntimeError,'SHA256 mismatch'):self.upload()
        self.assertEqual(self.journal(),[])
        self.assertEqual(self.remote_partials(),[])
        self.assertFalse(self.b.qpath(self.remote_dest).exists())
    def test_download_partial_corruption_cleaned(self):
        self.b.controls.add('corrupt_download')
        with self.assertRaisesRegex(RuntimeError,'SHA256 mismatch'):self.download()
        self.assertEqual(self.journal(),[])
        self.assertEqual(self.deck_partials(),[])
        self.assertFalse(self.deckdest.exists())
    def test_upload_interruption_cleaned(self):
        self.b.controls.add('fail_upload')
        with self.assertRaisesRegex(OSError,'simulated transfer interruption'):self.upload()
        self.assertFalse(self.b.qpath(self.remote_dest).exists())
        self.assertEqual(self.remote_partials(),[])
        self.assertEqual(self.journal(),[])
    def test_download_interruption_cleaned(self):
        self.b.controls.add('fail_download')
        with self.assertRaisesRegex(OSError,'simulated transfer interruption'):self.download()
        self.assertFalse(self.deckdest.exists())
        self.assertEqual(self.deck_partials(),[])
        self.assertEqual(self.journal(),[])
    def test_source_changed_during_upload_rejected(self):
        self.b.controls.add('change_upload_source')
        with self.assertRaisesRegex(RuntimeError,'Source file changed'):self.upload()
        self.assertFalse(self.b.qpath(self.remote_dest).exists())
        self.assertEqual(self.remote_partials(),[])
        self.assertEqual(self.journal(),[])
    def test_source_changed_during_download_rejected(self):
        self.b.controls.add('change_download_source')
        with self.assertRaisesRegex(RuntimeError,'Windows source changed'):self.download()
        self.assertFalse(self.deckdest.exists())
        self.assertEqual(self.deck_partials(),[])
        self.assertEqual(self.journal(),[])
    def test_remote_destination_created_during_transfer_no_overwrite(self):
        self.b.controls.add('race_remote_destination')
        with self.assertRaises(FileExistsError):self.upload()
        self.assertEqual(self.b.qpath(self.remote_dest).read_bytes(),b'THIRD_PARTY')
        self.assertEqual(self.remote_partials(),[])
        self.assertEqual(self.journal(),[])
    def test_failed_remote_cleanup_preserves_recovery_record(self):
        self.b.controls.update({'fail_upload','fail_cleanup'})
        with self.assertRaisesRegex(RuntimeError,'recovery journal'):self.upload()
        pending=self.journal()
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['state'],'cleanup_pending')
        self.assertTrue(self.b.qpath(pending[0]['partial']).exists())
        self.assertEqual(self.decksource.read_bytes(),b'APPROVED_MANGA_REFERENCE_DATA_'*100)
        records=list((self.b.ROOT/'transfer-journal').glob('*.json'))
        self.assertEqual(len(records),1)
        self.assertEqual(records[0].stat().st_mode&0o077,0)
        self.assertEqual((self.b.ROOT/'transfer-journal').stat().st_mode&0o077,0)
    def test_bad_hash_after_remote_commit_requires_inspection(self):
        self.b.controls.add('bad_postcommit')
        with self.assertRaisesRegex(RuntimeError,'AFTER Windows commit'):self.upload()
        self.assertTrue(self.b.qpath(self.remote_dest).exists())
        pending=self.journal()
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['state'],'committed_needs_inspection')
        self.assertEqual(self.remote_partials(),[])
    def test_broken_symlink_download_target_rejected(self):
        self.deckdest.symlink_to(self.root/'nonexistent')
        with self.assertRaises(FileExistsError):self.download()
        self.assertTrue(self.deckdest.is_symlink())
        self.assertEqual(self.journal(),[])
    def test_destination_parent_missing(self):
        with self.assertRaises(FileNotFoundError):
            safe.copy_v2('windows',self.remote_source,'steamdeck',self.root/'missing'/'target.bin',self.b)
        self.assertEqual(self.journal(),[])
    def test_local_target_race_during_download_no_overwrite(self):
        original_sftp=self.b.sftp
        def race_sftp(op,src,dst):
            original_sftp(op,src,dst)
            self.deckdest.write_bytes(b'THIRD_PARTY_CONTENT')
        self.b.sftp=race_sftp
        with self.assertRaises(FileExistsError):self.download()
        self.assertEqual(self.deckdest.read_bytes(),b'THIRD_PARTY_CONTENT')
        self.assertEqual(self.deck_partials(),[])
        self.assertEqual(self.journal(),[])

    def test_abrupt_process_exit_keeps_recovery_journal(self):
        child = ("from pathlib import Path; import os,sys; "
                 "sys.path.insert(0," + repr(str(HERE)) + "); "
                 "from transfer_reliability import TransferJournal; "
                 "r=Path(" + repr(str(self.b.ROOT)) + "); "
                 "TransferJournal(r,('steamdeck','windows'),'from.bin',"
                 "'C:/Test/dest.bin','C:/Test/dest.bin.dc-part-crash'); "
                 "os._exit(17)")
        p=subprocess.run([sys.executable,'-c',child],capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,17)
        entries=self.journal()
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['state'],'preparing')
        self.assertEqual(entries[0]['partial'],'C:/Test/dest.bin.dc-part-crash')
        self.assertFalse(self.b.qpath(self.remote_dest).exists())

    def test_wrong_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            safe.copy_v2('steamdeck',self.decksource,'steamdeck',self.deckdest,self.b)
        self.assertEqual(self.journal(),[])

if __name__=='__main__':
    unittest.main(verbosity=2)