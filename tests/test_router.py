#!/usr/bin/env python3
"""Offline regression checks for safe Windows PowerShell path expressions."""
import base64
import re
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import router

# The returned expression must consist exclusively of fixed PowerShell syntax
# and an ASCII Base64 payload; file names must never appear as source code.
EXPRESSION = re.compile(
    r"\(\[System\.Text\.Encoding\]::UTF8\.GetString\("
    r"\[System\.Convert\]::FromBase64String\('([A-Za-z0-9+/=]+)'\)\)\)"
)


class PowerShellPathTests(unittest.TestCase):
    def test_unicode_quotation_marks_remain_data(self):
        values = [
            "C:/Test/O'Reilly.txt",
            "C:/Test/O\u2018Reilly.txt",
            "C:/Test/O\u2019Reilly.txt",
            "C:/Test/O\u201aReilly.txt",
            "C:/Test/O\u201bReilly.txt",
            "C:/Test/note\u2019);Start-Process calc;#.txt",
            "C:/Test/`$(Get-Process);[]á🚀.txt",
        ]
        for value in values:
            with self.subTest(path=value):
                expr = router.quote_ps(value)
                match = EXPRESSION.fullmatch(expr)
                self.assertIsNotNone(match, expr)
                self.assertEqual(base64.b64decode(match.group(1), validate=True).decode('utf-8'), value)
                self.assertNotIn(value, expr)
                self.assertTrue(expr.isascii())

    def test_invalid_path_controls_refused(self):
        for value in ("", "line\nbreak", "line\rbreak", "nul\x00byte", "a" * 2049):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    router.quote_ps(value)

    def test_remote_file_helpers_never_embed_filename_as_powershell_code(self):
        dangerous = "C:/Test/a\u2019);whoami;#.txt"
        seen = []
        original = router.powershell
        try:
            router.powershell = lambda script, timeout=60: seen.append(script) or "0" * 64
            self.assertFalse(router.remote_exists(dangerous))
            self.assertEqual(router.remote_hash(dangerous), "0" * 64)
            self.assertEqual(len(seen), 2)
            for script in seen:
                self.assertNotIn(dangerous, script)
                payloads = EXPRESSION.findall(script)
                self.assertEqual(len(payloads), 1)
                self.assertEqual(base64.b64decode(payloads[0]).decode('utf-8'), dangerous)
        finally:
            router.powershell = original


if __name__ == '__main__':
    unittest.main()
