"""Independent adversarial reproductions, disposable files only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from service import release_transaction as rt

class ReviewFindings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dc-release-sandbox-review-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        for name in rt.SOURCE_FILES:
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(name.encode())

    def test_dangling_active_record_is_rejected(self):
        p = self.root / rt.CURRENT
        p.symlink_to(self.root / 'missing')
        with self.assertRaises(rt.ReleaseRejected):
            rt.inspect(self.root)
        self.assertTrue(p.is_symlink())

    def test_source_intermediate_symlink_is_rejected(self):
        p = self.source / 'service'
        outside = self.root / 'outside'
        p.rename(outside)
        p.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(rt.ReleaseRejected):
            rt.stage(self.root, self.source, 'r1')

    def test_extra_release_file_is_rejected(self):
        rt.stage(self.root, self.source, 'r1')
        (self.root / 'releases/r1/extra').write_bytes(b'not allowlisted')
        with self.assertRaises(rt.ReleaseRejected):
            rt.activate(self.root, 'r1')

    def test_nested_directory_is_synced_before_parent(self):
        calls = []
        orig = rt._fsync_dir
        def capture(path):
            calls.append(Path(path))
            orig(path)
        with mock.patch.object(rt, '_fsync_dir', side_effect=capture):
            rt.stage(self.root, self.source, 'r1')
        candidate = self.root / 'releases/r1'
        self.assertIn(candidate / 'service', calls)
        self.assertLess(calls.index(candidate / 'service'), calls.index(candidate))
        self.assertIn(self.root, calls)

    def test_metadata_requires_exact_keys_and_nonboolean_schema(self):
        for record in ({'schema': True, 'revision': 0, 'active': None, 'previous': None},
                       {'schema': 1, 'revision': 0},
                       {'schema': 1, 'revision': 0, 'active': None, 'previous': None, 'unexpected': 1}):
            with self.subTest(record=record):
                (self.root / rt.CURRENT).write_text(json.dumps(record))
                with self.assertRaises(rt.ReleaseRejected):
                    rt.inspect(self.root)

if __name__ == '__main__':
    unittest.main()
