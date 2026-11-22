#!/usr/bin/env python3
"""Source-template and strict transport tests for Windows `ls` (no Windows execution)."""
import base64
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import router


class WindowsLsTests(unittest.TestCase):
    def test_unicode_json_roundtrips_and_exact_powershell_template(self):
        path = 'C:/Test/O\u2019Reilly'
        rows = [{'Name': name, 'PSIsContainer': False, 'Length': 1}
                for name in ('café.txt', '香港文件.txt', 'O\u2019Reilly.txt', '🚀.bin')]
        expected_json = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
        encoded = base64.b64encode(expected_json.encode('utf-8')).decode('ascii')
        with mock.patch.object(router, 'powershell', return_value=encoded) as remote:
            received = router.windows_ls(path)
        self.assertEqual(json.loads(received), rows)
        script = remote.call_args.args[0]
        expected_script = (
            "$ErrorActionPreference='Stop';$items=@(Get-ChildItem -LiteralPath "
            + router.quote_ps(path) +
            " | Select-Object Name,PSIsContainer,Length);$json=ConvertTo-Json -InputObject $items -Compress;"
            "[Console]::Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))"
        )
        self.assertEqual(script, expected_script)
        self.assertNotIn(path, script)
        self.assertTrue(script.isascii())
        remote.assert_called_once()

    def test_corrupt_output_is_rejected_not_replaced(self):
        bad_outputs = ('%', base64.b64encode(b'\xff').decode('ascii'),
                       base64.b64encode(b'\xff\xfe').decode('ascii'))
        for payload in bad_outputs:
            with self.subTest(payload=payload), mock.patch.object(router, 'powershell', return_value=payload):
                with self.assertRaises(ValueError):
                    router.windows_ls('C:/Test/safe')

    def test_ls_cli_prints_decoded_json_unchanged(self):
        path = 'C:/Test/dir'
        response = '[{"Name":"café.txt","PSIsContainer":false,"Length":1}]'
        buffer = io.StringIO()
        with mock.patch.object(router, 'windows_ls', return_value=response) as handler, \
             mock.patch.object(sys, 'argv', ['router.py', 'ls', 'windows', path]), \
             contextlib.redirect_stdout(buffer):
            router.main()
        handler.assert_called_once_with(path)
        self.assertEqual(buffer.getvalue(), response + '\n')


if __name__ == '__main__':
    unittest.main()
