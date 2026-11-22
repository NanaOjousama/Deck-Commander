#!/usr/bin/env python3
"""Offline failure-injection tests: only temporary fake Deck/Windows files."""
import base64
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

HERE=Path(__file__).resolve().parents[1]  # repository root (modules live here, not in tests/)
sys.path.insert(0,str(HERE))
import transfer_reliability as safe
import router as actual_router

# Independently written (not imported from production) so a regression in
# router.quote_ps cannot silently make the fake agree with it.
EXPR_RE=re.compile(r"\(\[System\.Text\.Encoding\]::UTF8\.GetString\("
                   r"\[System\.Convert\]::FromBase64String\('([A-Za-z0-9+/=]+)'\)\)\)")
def ps_expr(value):
    return ("([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('"+
            base64.b64encode(value.encode('utf-8')).decode('ascii')+"')))")

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
        self.put_destinations=[]
        self.cleanup_paths=[]
    def check_windows_pairing(self): return True
    def qpath(self,p):
        raw=str(p).replace('\\','/')
        assert raw.startswith('C:/Test/'),raw
        rel=raw[len('C:/Test/'):]
        assert '/' not in rel and '..' not in rel
        return self.remoteroot/rel
    def quote_ps(self,p):
        # Use the actual production encoder (no str() cast, so a type confusion
        # in the code under test is rejected here exactly as in production).
        return actual_router.quote_ps(p)
    def remote_exists(self,p):
        self.actions.append('remote-exists')
        return self.qpath(p).exists()
    def remote_hash(self,p):
        self.actions.append('remote-hash')
        if 'bad_postcommit' in self.controls and self.qpath(p).name=='destination.bin':
            return '0'*64
        return sha(self.qpath(p))
    def local_hash(self,p):
        self.actions.append('local-hash')
        return sha(p)
    def sftp(self,op,src,dst):
        self.actions.append('sftp-'+op)
        if op=='put':
            self.put_destinations.append(dst)
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
        # Decode the fixed PowerShell Base64 expressions without invoking Windows.
        fields = [base64.b64decode(v, validate=True).decode('utf-8') for v in EXPR_RE.findall(script)]
        if '[System.IO.File]::Move' in script:
            self.actions.append('remote-no-clobber-commit')
            # Exact template: anything else is a production change to review.
            assert len(fields)==2,script
            assert script=="$ErrorActionPreference='Stop';[System.IO.File]::Move("+ps_expr(fields[0])+","+ps_expr(fields[1])+")",script
            src,dst=self.qpath(fields[0]),self.qpath(fields[1])
            if 'race_remote_destination' in self.controls:
                dst.write_bytes(b'THIRD_PARTY')
            if dst.exists():raise FileExistsError('simulated no-clobber remote move')
            src.rename(dst)
            return ''
        if 'Remove-Item -LiteralPath' in script:
            self.actions.append('remote-partial-cleanup')
            # Bound once to $p and referenced twice; never silently skipped.
            assert len(fields)==1,script
            assert script==("$ErrorActionPreference='Stop';$p="+ps_expr(fields[0])+";"
                            "if(Test-Path -LiteralPath $p -PathType Leaf){"
                            "Remove-Item -LiteralPath $p -Force -ErrorAction Stop}"),script
            self.cleanup_paths.append(fields[0])
            if 'fail_cleanup' in self.controls:raise OSError('simulated Windows link lost during cleanup')
            self.qpath(fields[0]).unlink(missing_ok=True)
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

    def assert_nothing_started(self):
        self.assertEqual(self.b.actions,[])          # no Windows call, no SFTP, no commit
        self.assertEqual(self.b.put_destinations,[])
        self.assertEqual(self.journal(),[])
        self.assertFalse((self.b.ROOT/'transfer-journal').exists())  # journal dir never even created
        self.assertEqual([p.name for p in self.b.remoteroot.iterdir()],['source.bin'])  # only the setUp fixture
    def test_upload_overflowing_partial_rejected_before_sftp_or_journal(self):
        # Target alone is valid (758 bytes) but target+'.dc-part-'+32 hex is 799 > 768.
        target='C:/Test/'+'a'*750
        self.assertLessEqual(len(target),actual_router.MAX_PATH_UTF8_BYTES)
        self.assertGreater(len(target)+len('.dc-part-')+32,actual_router.MAX_PATH_UTF8_BYTES)
        with self.assertRaisesRegex(ValueError,'too long'):
            safe.copy_v2('steamdeck',self.decksource,'windows',target,self.b)
        self.assert_nothing_started()
    def test_upload_target_itself_too_long_rejected_before_anything(self):
        with self.assertRaisesRegex(ValueError,'too long'):
            safe.copy_v2('steamdeck',self.decksource,'windows','C:/Test/'+'a'*800,self.b)
        self.assert_nothing_started()
    def test_upload_invalid_target_rejected_before_anything(self):
        for bad in ('C:/Test/\ud800.bin','C:/Test/a\nb.bin','C:/Test/a\x00b.bin'):
            with self.subTest(target=ascii(bad)):
                with self.assertRaises(ValueError):
                    safe.copy_v2('steamdeck',self.decksource,'windows',bad,self.b)
                self.assert_nothing_started()
    def test_download_overlong_windows_source_rejected_before_any_call(self):
        with self.assertRaisesRegex(ValueError,'too long'):
            safe.copy_v2('windows','C:/Test/'+'a'*800,'steamdeck',self.deckdest,self.b)
        self.assert_nothing_started()
        self.assertFalse(self.deckdest.exists())
        self.assertEqual(self.deck_partials(),[])
    def test_validate_windows_paths_boundary_uses_generated_partial_length(self):
        # Exactly at the limit (partial == 768 bytes) is accepted; one byte more is not.
        fits='C:/Test/'+'a'*(768-8-len('.dc-part-')-32)
        safe.validate_windows_paths(self.b,fits,fits+'.dc-part-'+'0'*32)
        with self.assertRaises(ValueError):
            safe.validate_windows_paths(self.b,fits,fits+'a.dc-part-'+'0'*32)
    def test_cleanup_targets_exactly_the_generated_partial(self):
        self.b.controls.add('fail_upload')
        with self.assertRaises(OSError):self.upload()
        self.assertEqual(len(self.b.put_destinations),1)
        partial=self.b.put_destinations[0]
        self.assertRegex(partial,re.escape(self.remote_dest)+r'\.dc-part-[0-9a-f]{32}$')
        self.assertEqual(self.b.cleanup_paths,[partial])   # not the destination, not the source
        self.assertEqual(self.b.actions.count('remote-partial-cleanup'),1)
        self.assertEqual(self.remote_partials(),[])
    def test_upload_non_str_windows_target_rejected_before_anything(self):
        # No silent str() cast: None -> 'None', 5 -> '5', Path/bytes must not be coerced.
        for bad in (None,5,Path('C:/Test/x.bin'),b'C:/Test/x.bin',['C:/Test/x.bin'],object()):
            with self.subTest(target=repr(bad)[:30]):
                with self.assertRaisesRegex(ValueError,'must be text'):
                    safe.copy_v2('steamdeck',self.decksource,'windows',bad,self.b)
                self.assert_nothing_started()
    def test_download_non_str_windows_source_rejected_before_anything(self):
        for bad in (None,5,Path('C:/Test/source.bin'),b'C:/Test/source.bin'):
            with self.subTest(source=repr(bad)[:30]):
                with self.assertRaisesRegex(ValueError,'must be text'):
                    safe.copy_v2('windows',bad,'steamdeck',self.deckdest,self.b)
                self.assert_nothing_started()
                self.assertFalse(self.deckdest.exists())
                self.assertEqual(self.deck_partials(),[])
    def test_sftp_invalid_windows_paths_rejected_before_anything(self):
        bad_paths=('dest.bin','Test/dest.bin','C:dest.bin','/C:/Test/dest.bin','C:/','',
                   '//server/share/dest.bin','\\\\server\\share\\dest.bin',
                   'C:/Test/a"b.bin','C:\\Test\\a"b.bin','C:/Test/a\rb.bin')
        for bad in bad_paths:
            with self.subTest(path=ascii(bad)):
                with self.assertRaises(ValueError):
                    safe.copy_v2('steamdeck',self.decksource,'windows',bad,self.b)
                self.assert_nothing_started()
                with self.assertRaises(ValueError):
                    safe.copy_v2('windows',bad,'steamdeck',self.deckdest,self.b)
                self.assert_nothing_started()
                self.assertFalse(self.deckdest.exists())
                self.assertEqual(self.deck_partials(),[])
    def test_cjk_byte_overflow_and_lone_surrogate_rejected_before_anything(self):
        cjk='C:/Test/'+'\u6f22'*250        # 258 chars but 758 UTF-8 bytes: fits alone, partial (+41) does not
        self.assertLessEqual(len(cjk.encode('utf-8')),768)
        too_long='C:/Test/'+'\u6f22'*260   # 788 bytes
        surrogate='C:/Test/\ud800.bin'
        for bad,pattern in ((cjk,'too long'),(too_long,'too long'),(surrogate,'not valid Unicode')):
            with self.subTest(upload=ascii(bad)[:24]):
                with self.assertRaisesRegex(ValueError,pattern):
                    safe.copy_v2('steamdeck',self.decksource,'windows',bad,self.b)
                self.assert_nothing_started()
        # A download source has no temporary suffix, so only the 2nd/3rd are invalid there.
        for bad,pattern in ((too_long,'too long'),(surrogate,'not valid Unicode')):
            with self.subTest(download=ascii(bad)[:24]):
                with self.assertRaisesRegex(ValueError,pattern):
                    safe.copy_v2('windows',bad,'steamdeck',self.deckdest,self.b)
                self.assert_nothing_started()
                self.assertFalse(self.deckdest.exists())
    def test_backslash_drive_paths_are_accepted_and_normalized(self):
        # Same contract as router.sftp: C:\... is a valid drive path, sent in '/' form.
        r=safe.copy_v2('steamdeck',self.decksource,'windows','C:\\Test\\destination.bin',self.b)
        self.assertTrue(r['copied'])
        self.assertEqual(self.b.qpath(self.remote_dest).read_bytes(),self.decksource.read_bytes())
        self.assertRegex(self.b.put_destinations[0],r'^C:/Test/destination\.bin\.dc-part-[0-9a-f]{32}$')
        r=safe.copy_v2('windows','C:\\Test\\source.bin','steamdeck',self.deckdest,self.b)
        self.assertTrue(r['copied'])
        self.assertEqual(self.deckdest.read_bytes(),self.b.qpath(self.remote_source).read_bytes())
    def test_normalize_and_validate_windows_paths_contract(self):
        self.assertEqual(safe.normalize_windows_path('C:\\a\\b.txt'),'C:/a/b.txt')
        self.assertEqual(safe.normalize_windows_path('d:/x'),'d:/x')
        self.assertEqual(safe.validate_windows_paths(self.b,'C:\\a','E:/b'),('C:/a','E:/b'))
        for bad in (None,1,'','C:','C:/','C:x','x/y','//h/s/f','C:/a"b','C:/a\nb','C:/a\0b'):
            with self.subTest(bad=ascii(bad)):
                with self.assertRaises(ValueError):safe.normalize_windows_path(bad)
    def test_cleanup_script_binds_path_once(self):
        scripts=[]
        class Probe:
            quote_ps=staticmethod(actual_router.quote_ps)
            def powershell(self,script,timeout=60):scripts.append((script,timeout))
        hostile="C:/Test/it\u2019s');calc;#.part"
        safe._remote_cleanup(Probe(),hostile)
        script,timeout=scripts[0]
        self.assertEqual(timeout,12)
        self.assertEqual(script,"$ErrorActionPreference='Stop';$p="+ps_expr(hostile)+";"
                         "if(Test-Path -LiteralPath $p -PathType Leaf){Remove-Item -LiteralPath $p -Force -ErrorAction Stop}")
        self.assertEqual(len(EXPR_RE.findall(script)),1)
        self.assertEqual(script.count('$p'),3)   # assignment + Test-Path + Remove-Item

if __name__=='__main__':
    unittest.main(verbosity=2)
