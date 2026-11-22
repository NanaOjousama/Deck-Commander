#!/usr/bin/env python3
"""Failure-injected no-clobber transfer tests against temporary fake devices.

Concentrates on the dangerous interval where the commit happened but its
acknowledgement was lost. Those cases MUST retain a receipt, never erase an
ambiguous journal, and must not replay or overwrite a committed destination.
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import transfer_reliability as transfer
from test_transfer_reliability import FakeBackend


class CommitAcknowledgementRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='deck-commander-ack-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.backend=FakeBackend(self.root)
        self.source=self.root/'upload.bin'
        self.source.write_bytes(b'DURABLE_EVIDENCE'*100)
        self.remote_target='C:/Test/destination.bin'

    def pending(self):
        return transfer.pending_transfers(self.backend.ROOT)

    def test_remote_commit_succeeds_but_acknowledgement_is_lost(self):
        original=self.backend.powershell
        def lose_ack(script,timeout=60):
            result=original(script,timeout=timeout)
            if '[System.IO.File]::Move' in script:
                raise TimeoutError('simulated network acknowledgement loss after remote commit')
            return result
        self.backend.powershell=lose_ack
        with self.assertRaisesRegex(Exception,'commit.*uncertain|result unknown|inspect'):
            transfer.copy_v2('steamdeck',self.source,'windows',self.remote_target,self.backend)
        self.assertEqual(self.backend.qpath(self.remote_target).read_bytes(),self.source.read_bytes())
        self.assertEqual(len(self.pending()),1,'commit acknowledgement loss must retain a durable receipt')
        self.assertEqual(self.pending()[0]['state'],'commit_unknown')
        self.assertEqual(list(self.backend.remoteroot.glob('*.dc-part-*')),[])

    def test_remote_commit_timeout_before_move_is_not_assumed_failed(self):
        original=self.backend.powershell
        def lose_ack(script,timeout=60):
            if '[System.IO.File]::Move' in script:
                raise TimeoutError('simulated SSH disconnect before commit result')
            return original(script,timeout=timeout)
        self.backend.powershell=lose_ack
        with self.assertRaisesRegex(Exception,'commit.*uncertain|result unknown|inspect'):
            transfer.copy_v2('steamdeck',self.source,'windows',self.remote_target,self.backend)
        self.assertFalse(self.backend.qpath(self.remote_target).exists())
        self.assertEqual(len(self.pending()),1)
        self.assertEqual(self.pending()[0]['state'],'commit_unknown')
        self.assertTrue(self.backend.qpath(self.pending()[0]['partial']).exists(),
                        'partial must remain for authorized inspection, not replay')

    def test_local_commit_link_succeeds_but_acknowledgement_is_lost(self):
        remote_source='C:/Test/source.bin'
        content=b'WINDOWS_SOURCE_REFERENCE'*75
        self.backend.qpath(remote_source).write_bytes(content)
        target=self.root/'download.bin'
        real_link=os.link
        def lost_link_ack(src,dst,*args,**kwargs):
            real_link(src,dst,*args,**kwargs)
            raise OSError('simulated local acknowledgement loss after os.link')
        with mock.patch.object(transfer.os,'link',side_effect=lost_link_ack):
            with self.assertRaisesRegex(Exception,'commit.*uncertain|result unknown|inspect'):
                transfer.copy_v2('windows',remote_source,'steamdeck',target,self.backend)
        self.assertEqual(target.read_bytes(),content)
        pending=self.pending()
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['state'],'commit_unknown')
        self.assertTrue(Path(pending[0]['partial']).exists(),
                        'ambiguous local hardlink may require manual recovery inspection')

    def test_upload_interrupted_after_partial_bytes_removes_only_owned_partial(self):
        original_source=self.source.read_bytes()
        def break_after_partial(op,src,dst):
            self.backend.qpath(dst).write_bytes(original_source[:27])
            raise ConnectionError('injected SFTP connection drop after partial bytes')
        self.backend.sftp=break_after_partial
        with self.assertRaisesRegex(ConnectionError,'connection drop'):
            transfer.copy_v2('steamdeck',self.source,'windows',self.remote_target,self.backend)
        self.assertEqual(self.source.read_bytes(),original_source)
        self.assertFalse(self.backend.qpath(self.remote_target).exists())
        self.assertEqual(list(self.backend.remoteroot.glob('*.dc-part-*')),[])
        self.assertEqual(self.pending(),[])

    def test_download_interrupted_after_partial_bytes_leaves_no_target(self):
        source='C:/Test/source.bin'
        self.backend.qpath(source).write_bytes(b'WINDOWS_DOWNLOAD'*15)
        target=self.root/'download.bin'
        def break_after_partial(op,src,dst):
            Path(dst).write_bytes(b'WINDOWS_DOWNLOAD'[:8])
            raise ConnectionError('injected SFTP download disconnect')
        self.backend.sftp=break_after_partial
        with self.assertRaisesRegex(ConnectionError,'download disconnect'):
            transfer.copy_v2('windows',source,'steamdeck',target,self.backend)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob('download.bin.dc-part-*')),[])
        self.assertEqual(self.pending(),[])

    def test_known_no_clobber_collision_still_preserves_external_file_and_cleans(self):
        self.backend.controls.add('race_remote_destination')
        with self.assertRaises(FileExistsError):
            transfer.copy_v2('steamdeck',self.source,'windows',self.remote_target,self.backend)
        self.assertEqual(self.backend.qpath(self.remote_target).read_bytes(),b'THIRD_PARTY')
        self.assertEqual(self.pending(),[])
        self.assertEqual(list(self.backend.remoteroot.glob('*.dc-part-*')),[])


if __name__=='__main__':unittest.main()
