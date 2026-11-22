#!/usr/bin/env python3
"""Offline regression checks for safe Windows PowerShell path expressions."""
import base64
import re
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
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

    def test_non_string_paths_refused(self):
        for value in (None, 5, b"C:/Test/x", bytearray(b"C:/x"), ["C:/x"], Path("C:/Test/x"), object()):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    router.quote_ps(value)

    def test_lone_surrogates_refused_cleanly(self):
        for value in ("C:/Test/\ud800.txt", "\udfff", "C:/Test/a\udc80b"):
            with self.subTest(value=ascii(value)):
                with self.assertRaisesRegex(ValueError, "Invalid path"):
                    router.quote_ps(value)  # ValueError, not a bare UnicodeEncodeError leak

    def test_valid_surrogate_pair_is_accepted_as_one_character(self):
        expr = router.quote_ps("C:/Test/\U0001F680.txt")  # a real astral character
        self.assertEqual(base64.b64decode(EXPRESSION.fullmatch(expr).group(1)).decode("utf-8"),
                         "C:/Test/\U0001F680.txt")

    def test_limit_is_utf8_bytes_not_characters(self):
        self.assertEqual(router.MAX_PATH_UTF8_BYTES, 768)
        for value in ("a" * 768, "\u20ac" * 256, "\U0001F680" * 192):  # all exactly 768 bytes
            with self.subTest(value=ascii(value[:3])):
                self.assertEqual(len(value.encode("utf-8")), 768)
                router.quote_ps(value)
        for value in ("a" * 769, "\u20ac" * 257, "\U0001F680" * 193):
            with self.subTest(over=ascii(value[:3])):
                self.assertLess(len(value), 2048)  # the old character limit would have accepted it
                with self.assertRaisesRegex(ValueError, "too long"):
                    router.quote_ps(value)

    def test_generated_temporary_suffixes_fit_inside_the_same_limit(self):
        # The longest accepted target leaves room for neither suffix; callers
        # must validate the generated partial path, not only the target.
        longest_resume = ".dc-resume-" + "0" * 32
        longest_part = ".dc-part-" + "0" * 32
        self.assertEqual((len(longest_part), len(longest_resume)), (41, 43))
        target = "C:/Test/" + "a" * (768 - 8 - 43)
        router.quote_ps(target + longest_resume)
        with self.assertRaises(ValueError):
            router.quote_ps(target + "a" + longest_resume)

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
