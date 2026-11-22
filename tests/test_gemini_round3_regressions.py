"""Gemini round-3 source-only hygiene regressions; no real Windows/SSH."""
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from folder_backend import FolderBackend
from resumable_file_transfer import resume_file
import router


def b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


class MinimalBackend:
    def __init__(self, root):
        self.ROOT = root
        self.calls = []
    def check_windows_pairing(self):
        self.calls.append('pairing')
    def powershell(self, script, timeout=60):
        self.calls.append('powershell')
        raise AssertionError('Remote operation must never start')
    quote_ps = staticmethod(router.quote_ps)


class GeminiRound3HygieneTests(unittest.TestCase):
    def test_resume_windows_non_string_source_or_destination_fails_before_remote(self):
        with tempfile.TemporaryDirectory(prefix='dc-round3-hygiene-') as d:
            for bad in (None, 5, Path('C:/Test/file.bin'), b'C:/Test/file.bin'):
                for frm, source, to, target in (
                    ('steamdeck', str(Path(d)/'source.bin'), 'windows', bad),
                    ('windows', bad, 'steamdeck', str(Path(d)/'out.bin')),
                ):
                    with self.subTest(value=repr(bad), direction=frm):
                        backend=MinimalBackend(Path(d))
                        with self.assertRaises(ValueError):
                            resume_file(frm, source, to, target, backend)
                        self.assertEqual(backend.calls, ['pairing'])
                        self.assertFalse((Path(d)/'resumable-file-journal').exists())

    def test_folder_listing_rejects_scalar_json_instead_of_implicit_wrap(self):
        for payload in ('null','123','true','"not a directory listing"'):
            with self.subTest(payload=payload):
                class B:
                    ROOT=Path('/nonexistent')
                    quote_ps=staticmethod(router.quote_ps)
                    def powershell(self, script, timeout=60):
                        return b64(payload)
                with self.assertRaisesRegex(ValueError,'must be a JSON list or object'):
                    FolderBackend(B()).list_tree('windows','C:/Test/src')

    def test_ls_windows_template_explicitly_preserves_json_arrays(self):
        output=b64('[]')
        with mock.patch.object(router,'powershell',return_value=output) as capture:
            self.assertEqual(router.windows_ls('C:/Test/dir'),'[]')
        script=capture.call_args.args[0]
        self.assertIn('ConvertTo-Json -InputObject $items -Compress', script)
        self.assertIn('$items=@(Get-ChildItem -LiteralPath ', script)
        self.assertNotIn('C:/Test/dir',script)


if __name__=='__main__':
    unittest.main()
