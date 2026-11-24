"""Multi-process version selection, damaged-current rollback and crash remnants."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest

PATH=Path(__file__).resolve().parents[1]/'service/release_transaction.py'
spec=importlib.util.spec_from_file_location('rt',PATH)
rt=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

def activate_parallel(root,version,output):
    try:
        output.put(('ok',rt.activate(root,version)))
    except Exception as exc:
        output.put(('err',type(exc).__name__,str(exc)))

class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dc-release-parallel-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        os.chmod(self.root,0o700)
        self.source=self.root/'src'
        self.source.mkdir(mode=0o700)
        for relative in rt.SOURCE_FILES:
            p=self.source/relative
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(('clean-'+relative).encode())

    def test_two_concurrent_activations_are_serialized_and_versions_preserved(self):
        rt.stage(self.root,self.source,'r1')
        rt.activate(self.root,'r1')
        rt.stage(self.root,self.source,'r2')
        rt.stage(self.root,self.source,'r3')
        context=multiprocessing.get_context('fork')
        queue=context.Queue()
        workers=[context.Process(target=activate_parallel,args=(self.root,id,queue))
                 for id in ('r2','r3')]
        for proc in workers:proc.start()
        for proc in workers:
            proc.join(4)
            self.assertFalse(proc.is_alive(),'bounded update did not exit')
            self.assertEqual(proc.exitcode,0)
        results=[queue.get(timeout=1) for _ in workers]
        self.assertTrue(all(entry[0]=='ok' for entry in results),str(results))
        manifest=rt.inspect(self.root)
        self.assertEqual(manifest['revision'],3)
        self.assertEqual({manifest['active'],manifest['previous']},{'r2','r3'})
        self.assertEqual(rt._verify_release(self.root,'r1'),
                         json.loads((self.root/'releases/r1/ready.json').read_text())['manifest_sha256'])

    def test_rollback_is_possible_from_corrupted_active_release(self):
        rt.stage(self.root,self.source,'r1');rt.activate(self.root,'r1')
        rt.stage(self.root,self.source,'r2');rt.activate(self.root,'r2')
        broken=self.root/'releases/r2/gateway.mjs'
        broken.chmod(0o600);broken.write_bytes(b'damaged')
        with self.assertRaises(rt.ReleaseRejected):rt.inspect(self.root)
        update=rt.rollback(self.root)
        self.assertEqual(update['active'],'r1')
        self.assertEqual(rt.inspect(self.root)['active'],'r1')
        self.assertEqual(rt.inspect(self.root)['previous'],'r2')
        # Cannot reselect the corrupted one, even with a previous reference.
        with self.assertRaises(rt.ReleaseRejected):rt.rollback(self.root)

    def test_partial_crash_remnant_is_not_activatable_or_overwritten(self):
        rt.stage(self.root,self.source,'r1');rt.activate(self.root,'r1')
        incomplete=self.root/'releases/r2'
        incomplete.mkdir(mode=0o700)
        (incomplete/'gateway.mjs').write_bytes(b'partial')
        with self.assertRaises((FileNotFoundError,rt.ReleaseRejected)):
            rt.activate(self.root,'r2')
        with self.assertRaises(FileExistsError):
            rt.stage(self.root,self.source,'r2')
        self.assertEqual(rt.inspect(self.root)['active'],'r1')
        self.assertEqual((incomplete/'gateway.mjs').read_bytes(),b'partial')

    def test_release_lock_symlink_is_rejected_without_following_it(self):
        external=self.root/'external'
        external.write_bytes(b'protected')
        (self.root/'.release.lock').symlink_to(external)
        with self.assertRaises(OSError):
            rt.stage(self.root,self.source,'r1')
        self.assertEqual(external.read_bytes(),b'protected')

if __name__=='__main__':unittest.main()
