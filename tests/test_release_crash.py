"""Real SIGKILL probes for sandbox release transactions (Linux only)."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "service/release_transaction.py"
spec = importlib.util.spec_from_file_location("rt", MODULE)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

CHILD = """
import importlib.util, os, signal, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('rt',sys.argv[1])
rt=importlib.util.module_from_spec(spec);spec.loader.exec_module(rt)
root=Path(sys.argv[2]);source=root/'source';phase=sys.argv[3]
def die(): os.kill(os.getpid(),signal.SIGKILL)
if phase=='stage':
    original=rt._write_exclusive
    def write(path,data,mode=0o600):
        original(path,data,mode)
        if path.name=='gateway.mjs':die()
    rt._write_exclusive=write
    rt.stage(root,source,'r2')
else:
    original=rt.os.replace
    def replace(src,dst):
        if phase=='before':die()
        original(src,dst)
        die()
    rt.os.replace=replace
    rt.activate(root,'r2')
"""

class CrashTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="dc-release-sandbox-crash-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/"source"
        self.source.mkdir()
        for name in rt.SOURCE_FILES:
            path=self.source/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(name.encode())
        self.state=self.root/"synthetic-state"
        self.state.mkdir()
        self.receipt=self.state/"recovery.json"
        self.receipt.write_bytes(b'{"state":"commit_unknown"}')
        rt.stage(self.root,self.source,"r1")
        rt.activate(self.root,"r1")

    def kill(self,phase):
        p=subprocess.run([sys.executable,"-c",CHILD,str(MODULE),str(self.root),phase],
                         capture_output=True,timeout=10)
        self.assertEqual(p.returncode,-signal.SIGKILL,p.stderr)
        self.assertEqual(self.receipt.read_bytes(),b'{"state":"commit_unknown"}')

    def test_killed_staging_preserves_active_and_refuses_partial(self):
        self.kill("stage")
        self.assertEqual(rt.inspect(self.root)["active"],"r1")
        with self.assertRaises((FileNotFoundError,rt.ReleaseRejected)):
            rt.activate(self.root,"r2")
        with self.assertRaises(FileExistsError):
            rt.stage(self.root,self.source,"r2")
        rt.stage(self.root,self.source,"r3")
        rt.activate(self.root,"r3")
        self.assertEqual(rt.inspect(self.root)["active"],"r3")

    def test_killed_before_replace_preserves_old_selection(self):
        rt.stage(self.root,self.source,"r2")
        self.kill("before")
        self.assertEqual(rt.inspect(self.root)["active"],"r1")
        rt.activate(self.root,"r2")
        self.assertEqual(rt.inspect(self.root)["active"],"r2")

    def test_killed_after_replace_preserves_complete_selection(self):
        rt.stage(self.root,self.source,"r2")
        self.kill("after")
        self.assertEqual(rt.inspect(self.root)["active"],"r2")
        self.assertEqual(rt.rollback(self.root)["active"],"r1")

if __name__=="__main__":
    unittest.main()
