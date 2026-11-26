"""Release staging/rollback safety tests: temporary sandbox only."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

CODE=Path(__file__).resolve().parents[1]/'service/release_transaction.py'
spec=importlib.util.spec_from_file_location('release_transaction',CODE)
rt=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dc-release-sandbox-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        os.chmod(self.root,0o700)
        self.source=self.root/'source'
        self.source.mkdir(mode=0o700)
        for name in rt.SOURCE_FILES:
            path=self.source/name
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            path.write_bytes(('source-v1:'+name).encode())
        self.state=self.root/'state'
        self.state.mkdir(mode=0o700)
        self.secret=self.state/'synthetic-private-key'
        self.secret.write_bytes(b'SECRET_NOT_TO_BE_PACKAGED')
        self.journal=self.state/'transfer-recovery.json'
        self.journal.write_bytes(b'{"state":"commit_unknown"}')

    def stage(self,version='r1',**args):
        return rt.stage(self.root,self.source,version,**args)

    def test_allowlisted_code_only_and_no_autoinstall(self):
        digest=self.stage()
        self.assertEqual(len(digest),64)
        folder=self.root/'releases/r1'
        self.assertEqual(rt._verify_release(self.root,'r1'),digest)
        self.assertEqual(sorted(json.loads((folder/'manifest.json').read_text())['files']),sorted(rt.SOURCE_FILES))
        self.assertFalse((folder/'state').exists())
        self.assertFalse((folder/'.env').exists())
        self.assertFalse((self.root/'active.json').exists())
        self.assertEqual(self.secret.read_bytes(),b'SECRET_NOT_TO_BE_PACKAGED')
        self.assertEqual((folder/'gateway.mjs').stat().st_mode & 0o222,0)

    def test_atomic_metadata_activation_and_rollback(self):
        self.stage('r1')
        self.assertEqual(rt.activate(self.root,'r1')['active'],'r1')
        (self.source/'gateway.mjs').write_bytes(b'V2')
        self.stage('r2')
        self.assertEqual(rt.activate(self.root,'r2'),
            {'schema':1,'revision':2,'active':'r2','previous':'r1'})
        self.assertEqual(rt.rollback(self.root),
            {'schema':1,'revision':3,'active':'r1','previous':'r2'})
        self.assertEqual((self.root/'releases/r2/gateway.mjs').read_bytes(),b'V2')
        self.assertEqual(self.secret.read_bytes(),b'SECRET_NOT_TO_BE_PACKAGED')
        self.assertEqual(self.journal.read_bytes(),b'{"state":"commit_unknown"}')

    def test_injected_copy_failure_never_changes_active_version_or_journal(self):
        self.stage('r1');rt.activate(self.root,'r1')
        original=(self.root/'active.json').read_bytes()
        for failure in (1,4,'manifest'):
            name='broken'+str(failure)
            with self.assertRaises(OSError):
                self.stage(name,fault_after=failure)
            self.assertEqual((self.root/'active.json').read_bytes(),original)
            self.assertFalse((self.root/'releases'/name).exists())
            self.assertEqual(self.journal.read_bytes(),b'{"state":"commit_unknown"}')

    def test_failed_activation_and_rollback_leave_prior_record_untouched(self):
        self.stage('r1');rt.activate(self.root,'r1');self.stage('r2')
        original=(self.root/'active.json').read_bytes()
        with self.assertRaises(OSError):
            rt.activate(self.root,'r2',fault_before_commit=True)
        self.assertEqual((self.root/'active.json').read_bytes(),original)
        rt.activate(self.root,'r2')
        promoted=(self.root/'active.json').read_bytes()
        with self.assertRaises(OSError):
            rt.rollback(self.root,fault_before_commit=True)
        self.assertEqual((self.root/'active.json').read_bytes(),promoted)

    def test_modified_code_or_manifest_blocks_roll_forward_and_rollback(self):
        self.stage('r1');rt.activate(self.root,'r1');self.stage('r2')
        binary=self.root/'releases/r2/gateway.mjs'
        old=binary.read_bytes()
        binary.chmod(0o600);binary.write_bytes(b'TAMPER')
        with self.assertRaises(rt.ReleaseRejected):rt.activate(self.root,'r2')
        self.assertEqual(rt.inspect(self.root)['active'],'r1')
        binary.write_bytes(old)
        binary.chmod(0o400)
        rt.activate(self.root,'r2')
        manifest=self.root/'releases/r1/manifest.json'
        manifest.chmod(0o600);manifest.write_bytes(b'{}')
        with self.assertRaises(rt.ReleaseRejected):rt.rollback(self.root)
        self.assertEqual(json.loads((self.root/'active.json').read_text())['active'],'r2')

    def test_no_overwriting_existing_release(self):
        self.stage('r1')
        before=(self.root/'releases/r1/gateway.mjs').read_bytes()
        (self.source/'gateway.mjs').write_bytes(b'NEW')
        with self.assertRaises(FileExistsError):self.stage('r1')
        self.assertEqual((self.root/'releases/r1/gateway.mjs').read_bytes(),before)

    def test_bad_release_names_and_public_root_are_rejected(self):
        for bad in ('.','..','../../etc','../r2','sub/folder','x y','',None,'a'*49):
            with self.subTest(bad=bad):
                with self.assertRaises(rt.ReleaseRejected):self.stage(bad)
        # Even a private directory is not a valid rehearsal target unless it
        # was specifically named and created under the OS temporary directory.
        unrelated=self.root/'unrelated-private-directory'
        unrelated.mkdir(mode=0o700)
        with self.assertRaises(rt.ReleaseRejected):rt.inspect(unrelated)
        os.chmod(self.root,0o755)
        with self.assertRaises(rt.ReleaseRejected):self.stage('r1')

    def test_source_symlink_and_marker_symlink_rejected(self):
        (self.source/'gateway.mjs').unlink()
        outside=self.root/'external';outside.write_bytes(b'UNTRUSTED')
        (self.source/'gateway.mjs').symlink_to(outside)
        with self.assertRaises((rt.ReleaseRejected,OSError)):self.stage('r1')
        (self.source/'gateway.mjs').unlink()
        (self.source/'gateway.mjs').write_bytes(b'SAFE')
        self.stage('r1')
        marker=self.root/'releases/r1/ready.json'
        marker.unlink();marker.symlink_to(outside)
        with self.assertRaises((rt.ReleaseRejected,OSError)):rt.activate(self.root,'r1')

    def test_corrupt_active_record_is_not_reset(self):
        self.stage('r1');rt.activate(self.root,'r1')
        path=self.root/'active.json'
        path.write_text('{bad')
        with self.assertRaises(ValueError):rt.activate(self.root,'r1')
        self.assertEqual(path.read_text(),'{bad')

    def test_cannot_rollback_without_verified_previous_release(self):
        self.stage('r1');rt.activate(self.root,'r1')
        with self.assertRaises(rt.ReleaseRejected):rt.rollback(self.root)

if __name__=='__main__':
    unittest.main()
