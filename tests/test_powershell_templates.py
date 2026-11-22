#!/usr/bin/env python3
"""Golden-template tests for every PowerShell script that embeds a path.

PURELY SYNTHETIC: PowerShell is never executed (pwsh/Windows are unavailable
here). These tests prove what *source text* Python assembles: fixed templates
plus ASCII Base64 payloads that decode back to the exact original path. They do
NOT prove PowerShell semantics (parsing, Test-Path, Move, encoding behaviour);
that remains an open real-Windows verification gap.
"""
import base64
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import router
import transfer_reliability as safe
import resumable_file_transfer as resumable
from folder_backend import FolderBackend

# Written independently of router.quote_ps on purpose.
EXPR_RE = re.compile(r"\(\[System\.Text\.Encoding\]::UTF8\.GetString\("
                     r"\[System\.Convert\]::FromBase64String\('([A-Za-z0-9+/=]+)'\)\)\)")


def ps(value):
    return ("([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('" +
            base64.b64encode(value.encode("utf-8")).decode("ascii") + "')))")


def b64_utf8(text):
    """What the fixed PowerShell tail [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)) prints."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# Fixed PowerShell tail that emits structured output as ASCII Base64 of UTF-8 (no attacker data in it).
B64_TAIL = "[Console]::Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))"

# File names that break under OEM/ANSI code pages or naive quoting.
UNICODE_NAMES = ["café.txt", "香港語文件 廣東話.txt",
                 "\U0001F680 rocket \U0001F4C1.txt", "“smart” ‘quotes’.txt"]


HOSTILE_NAMES = [
    "O'Reilly.txt",
    "O\u2018Reilly\u2019.txt",
    "\u201aquote\u201b\u201cdouble\u201d.txt",
    "note\u2019);Start-Process calc;#.txt",
    "`$(Get-Process);[]\u00e1\U0001F680.txt",
    "a b  c.txt",
]
HOSTILE = ["C:/Test/" + n for n in HOSTILE_NAMES]


class Recorder:
    """Stand-in for router: records scripts, returns a canned answer."""
    ROOT = Path("/nonexistent-synthetic-root")
    quote_ps = staticmethod(router.quote_ps)

    def __init__(self, answer=""):
        self.answer = answer
        self.calls = []

    def check_windows_pairing(self):
        return True

    def powershell(self, script, timeout=60):
        self.calls.append((script, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    @property
    def scripts(self):
        return [s for s, _ in self.calls]


class Scripted(Recorder):
    """Recorder that answers successive powershell() calls from a list."""

    def __init__(self, *answers):
        super().__init__()
        self.queue = list(answers)

    def powershell(self, script, timeout=60):
        self.calls.append((script, timeout))
        if not self.queue:
            raise AssertionError("unexpected extra powershell call: " + script[:80])
        return self.queue.pop(0)


class DataOnlyMixin:
    def assert_data_only(self, script, expected_paths):
        self.assertTrue(script.isascii(), "script must be pure ASCII")
        found = [base64.b64decode(v, validate=True).decode("utf-8") for v in EXPR_RE.findall(script)]
        self.assertEqual(found, expected_paths)
        for path in expected_paths:
            self.assertNotIn(path, script)
        residue = EXPR_RE.sub("", script)  # everything except the path payloads
        for fragment in ("Start-Process", "calc", "Reilly", "quote", "Get-Process", "\u2019"):
            self.assertNotIn(fragment, residue)


class RouterTemplateTests(DataOnlyMixin, unittest.TestCase):
    def run_with(self, func, path, answer):
        seen = []

        def fake(script, timeout=60):
            seen.append(script)
            return answer

        with mock.patch.object(router, "powershell", side_effect=fake):
            result = func(path)
        return seen, result

    def test_remote_exists_golden_and_results(self):
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                seen, result = self.run_with(router.remote_exists, path, "1")
                self.assertTrue(result)
                self.assertEqual(seen, ["$ErrorActionPreference='Stop';[Console]::Write([int](Test-Path -LiteralPath " +
                                        ps(path) + "))"])
                self.assert_data_only(seen[0], [path])
                self.assertFalse(self.run_with(router.remote_exists, path, "0")[1])

    def test_remote_hash_golden_and_results(self):
        digest = "AB" * 32
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                seen, result = self.run_with(router.remote_hash, path, digest)
                self.assertEqual(result, digest.lower())
                self.assertEqual(seen, ["$ErrorActionPreference='Stop';[Console]::Write(" +
                                        "(Get-FileHash -Algorithm SHA256 -LiteralPath " + ps(path) + ").Hash)"])
                self.assert_data_only(seen[0], [path])

    def test_remote_hash_without_sha_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "No SHA256"):
            self.run_with(router.remote_hash, "C:/Test/x", "not a hash")

    def test_helper_failures_propagate(self):
        boom = RuntimeError("command exited 1")
        for func in (router.remote_exists, router.remote_hash):
            with self.subTest(func=func.__name__):
                with mock.patch.object(router, "powershell", side_effect=boom):
                    with self.assertRaises(RuntimeError):
                        func("C:/Test/x")

    def test_invalid_paths_never_reach_powershell(self):
        bad = [None, 5, b"C:/x", "", "a\nb", "a\x00b", "C:/\ud800", "a" * 769, "\u20ac" * 257]
        for func in (router.remote_exists, router.remote_hash):
            for value in bad:
                with self.subTest(func=func.__name__, value=ascii(value)[:20]):
                    with mock.patch.object(router, "powershell") as ps_mock:
                        with self.assertRaises(ValueError):
                            func(value)
                    ps_mock.assert_not_called()


class PowershellWrapperTests(unittest.TestCase):
    def capture(self, script, timeout=60):
        calls = []
        with mock.patch.object(router, "ssh", return_value=["ssh", "-T", "synthetic"]), \
                mock.patch.object(router, "run", side_effect=lambda cmd, **kw: calls.append((cmd, kw)) or "OK"):
            result = router.powershell(script, timeout=timeout)
        return result, calls

    def test_prefix_is_syntactically_balanced_ascii_and_minimal(self):
        prefix = router.POWERSHELL_PREFIX
        self.assertTrue(prefix.isascii())
        self.assertTrue(prefix.startswith("try{[Console]::OutputEncoding="))
        self.assertTrue(prefix.endswith("catch{};"))  # statement-terminated, safe to concatenate
        self.assertIn("UTF8Encoding($false)", prefix)  # no BOM
        self.assertNotIn("InputEncoding", prefix)  # no stdin is used; avoid unnecessary knobs
        self.assertEqual(prefix.count("{"), prefix.count("}"))
        self.assertEqual(prefix.count("("), prefix.count(")"))

    def test_every_script_is_prefixed_once_and_otherwise_unchanged(self):
        for script in ("[Console]::Write($env:COMPUTERNAME)",
                       "$ErrorActionPreference='Stop';" + ps("C:/Test/a\u2019b")):
            with self.subTest(script=script[:30]):
                result, calls = self.capture(script, timeout=7)
                self.assertEqual(result, "OK")
                cmd, kw = calls[0]
                self.assertEqual(kw, {"timeout": 7})
                self.assertEqual(cmd[:3], ["ssh", "-T", "synthetic"])
                self.assertEqual(cmd[3:8], ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"])
                decoded = base64.b64decode(cmd[8], validate=True).decode("utf-16-le")
                self.assertEqual(decoded, router.POWERSHELL_PREFIX + script)
                self.assertEqual(decoded.count("OutputEncoding"), 1)

    def test_worst_case_commit_command_fits_under_cmd_exe_limit(self):
        for unit in ("a", "\u20ac", "\U0001F680"):
            size = len(unit.encode("utf-8"))
            path = unit * (router.MAX_PATH_UTF8_BYTES // size)
            self.assertEqual(len(path.encode("utf-8")), router.MAX_PATH_UTF8_BYTES)
            backend = Recorder()
            safe._remote_commit_no_clobber(backend, path, path)
            script = backend.scripts[0]
            _, calls = self.capture(script)
            enc = calls[0][0][8]
            self.assertLessEqual(len(enc), router.MAX_ENCODED_COMMAND_CHARS)
            line = "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " + enc
            self.assertLess(len(line), 8191 - 1000, "keep >1000 chars margin below the cmd.exe limit")

    def test_encoded_command_limit_boundary_is_exact(self):
        # Base64 length is 4*ceil(bytes/3) and UTF-16LE doubles ASCII, so 2625
        # characters -> 5250 bytes -> exactly 7000 chars (allowed); one more
        # character -> 5252 bytes -> 7004 chars (rejected, nothing is run).
        self.assertEqual(router.MAX_ENCODED_COMMAND_CHARS, 7000)
        prefix = len(router.POWERSHELL_PREFIX)
        at_limit = "a" * (2625 - prefix)
        result, calls = self.capture(at_limit)
        self.assertEqual(result, "OK")
        self.assertEqual(len(calls[0][0][8]), 7000)
        with mock.patch.object(router, "ssh", return_value=["ssh"]), mock.patch.object(router, "run") as run_mock:
            with self.assertRaisesRegex(ValueError, "too large"):
                router.powershell(at_limit + "a")
        run_mock.assert_not_called()

    def test_oversized_encoded_command_fails_closed_without_running(self):
        with mock.patch.object(router, "ssh", return_value=["ssh"]), mock.patch.object(router, "run") as run_mock:
            with self.assertRaisesRegex(ValueError, "too large"):
                router.powershell("a" * 6000)
        run_mock.assert_not_called()

    def test_longest_real_scripts_with_longest_paths_fit(self):
        longest = "C:/" + "a" * (router.MAX_PATH_UTF8_BYTES - 3)
        self.assertEqual(len(longest), router.MAX_PATH_UTF8_BYTES)
        sizes = []
        answer = {"value": ""}

        def fake_run(cmd, **kw):
            sizes.append(len(cmd[8]))
            return answer["value"]

        calls = [  # (canned stdout, callable exercising the real router.powershell)
            ("dir", lambda: FolderBackend(router).path_kind("windows", longest)),
            ("", lambda: FolderBackend(router).ensure_directory("windows", longest)),
            (b64_utf8("[]"), lambda: FolderBackend(router).list_tree("windows", longest)),
            ("42", lambda: resumable.remote_size(router, longest)),
            ("ab" * 32, lambda: resumable.remote_prefix_hash(router, longest, 1)),
        ]
        with mock.patch.object(router, "ssh", return_value=["ssh", "-T", "x"]), \
                mock.patch.object(router, "run", side_effect=fake_run):
            for stdout, call in calls:
                answer["value"] = stdout
                call()
        self.assertEqual(len(sizes), len(calls))
        self.assertLessEqual(max(sizes), router.MAX_ENCODED_COMMAND_CHARS)


class TransferTemplateTests(DataOnlyMixin, unittest.TestCase):
    def test_remote_cleanup_golden_binds_path_once(self):
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                backend = Recorder()
                safe._remote_cleanup(backend, path)
                self.assertEqual(backend.calls, [(
                    "$ErrorActionPreference='Stop';$p=" + ps(path) + ";"
                    "if(Test-Path -LiteralPath $p -PathType Leaf){Remove-Item -LiteralPath $p -Force -ErrorAction Stop}",
                    12)])
                self.assert_data_only(backend.scripts[0], [path])

    def test_remote_commit_no_clobber_golden(self):
        for source, dest in zip(HOSTILE, reversed(HOSTILE)):
            with self.subTest(source=ascii(source)):
                backend = Recorder()
                safe._remote_commit_no_clobber(backend, source + ".dc-part-" + "0" * 32, dest)
                self.assertEqual(backend.scripts, [
                    "$ErrorActionPreference='Stop';[System.IO.File]::Move(" +
                    ps(source + ".dc-part-" + "0" * 32) + "," + ps(dest) + ")"])
                self.assert_data_only(backend.scripts[0], [source + ".dc-part-" + "0" * 32, dest])

    def test_cleanup_and_commit_failures_propagate(self):
        for func, args in ((safe._remote_cleanup, ("C:/Test/p",)), (safe._remote_commit_no_clobber, ("C:/Test/p", "C:/Test/d"))):
            with self.subTest(func=func.__name__):
                with self.assertRaises(RuntimeError):
                    func(Recorder(RuntimeError("remote failure")), *args)

    def test_cleanup_and_commit_reject_invalid_paths_before_powershell(self):
        for func, extra in ((safe._remote_cleanup, ()), (safe._remote_commit_no_clobber, ("C:/Test/d",))):
            for bad in ("", "a\nb", "a\x00", "C:/\ud800", "a" * 769):
                with self.subTest(func=func.__name__, bad=ascii(bad)[:20]):
                    backend = Recorder()
                    with self.assertRaises(ValueError):
                        func(backend, bad, *extra)
                    self.assertEqual(backend.calls, [])


class FolderBackendTemplateTests(DataOnlyMixin, unittest.TestCase):
    def test_path_kind_golden(self):
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                backend = Recorder("file")
                self.assertEqual(FolderBackend(backend).path_kind("windows", path), "file")
                self.assertEqual(backend.calls, [(
                    "$ErrorActionPreference='Stop';$p=" + ps(path) + ";"
                    "if(-not(Test-Path -LiteralPath $p)){[Console]::Write('missing')}"
                    "else{$i=Get-Item -LiteralPath $p -Force -ErrorAction Stop;"
                    "if(($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){[Console]::Write('other')}"
                    "elseif($i.PSIsContainer){[Console]::Write('dir')}else{[Console]::Write('file')}}", 30)])
                self.assert_data_only(backend.scripts[0], [path])

    def test_path_kind_rejects_unexpected_output_and_propagates_failure(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid remote path kind"):
            FolderBackend(Recorder("surprise")).path_kind("windows", "C:/Test/x")
        with self.assertRaises(RuntimeError):
            FolderBackend(Recorder(RuntimeError("boom"))).path_kind("windows", "C:/Test/x")

    def test_ensure_directory_golden_one_data_payload_bound_to_p(self):
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                backend = Recorder("")
                FolderBackend(backend).ensure_directory("windows", path)
                self.assertEqual(backend.calls, [(
                    "$ErrorActionPreference='Stop';$p=" + ps(path) + ";"
                    "$drive=[System.IO.Path]::GetPathRoot($p);"
                    "$parts=$p.Substring($drive.Length).Split([char]'/');$current=$drive;"
                    "foreach($piece in $parts){if($piece -eq ''){continue};"
                    "$current=[System.IO.Path]::Combine($current,$piece);"
                    "if(Test-Path -LiteralPath $current){"
                    "$item=Get-Item -LiteralPath $current -Force -ErrorAction Stop;"
                    "if(-not $item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Unsafe destination directory'}"
                    "}else{[System.IO.Directory]::CreateDirectory($current) | Out-Null}}", 35)])
                self.assert_data_only(backend.scripts[0], [path])

    def test_ensure_directory_invalid_paths_never_reach_powershell(self):
        bad = [None, 5, b"C:/Test/x", ["C:/Test/x"], Path("C:/Test/x"), "", "C:/", "relative/dir", "C:dir",
               "//server/share/x", "\\\\server\\share\\x", "C:/a\"b", "C:/a\nb", "C:/a\x00b", "C:/a/../b",
               "C:/a/./b", "C:/" + "a" * 800, "C:/" + "\u6f22" * 260, "C:/\ud800"]
        for value in bad:
            with self.subTest(value=ascii(value)[:30]):
                backend = Recorder("")
                with self.assertRaises(ValueError):
                    FolderBackend(backend).ensure_directory("windows", value)
                self.assertEqual(backend.calls, [])

    def test_ensure_directory_accepts_backslashes_and_sends_normalized_form(self):
        backend = Recorder("")
        FolderBackend(backend).ensure_directory("windows", "C:\\Test\\sub\\dir\\")
        self.assert_data_only(backend.scripts[0], ["C:/Test/sub/dir"])

    def test_list_tree_golden_decodes_base64_utf8_json(self):
        rows = [{"rel": n, "type": "file", "size": 7} for n in UNICODE_NAMES] + [{"rel": "d", "type": "dir"}]
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                backend = Recorder(b64_utf8(json.dumps(rows, ensure_ascii=False, separators=(",", ":"))))
                self.assertEqual(FolderBackend(backend).list_tree("windows", path), rows)
                self.assertEqual(backend.calls, [(
                    "$ErrorActionPreference='Stop';"
                    "$root=Get-Item -LiteralPath " + ps(path) + " -Force -ErrorAction Stop;"
                    "if(-not $root.PSIsContainer){throw 'Source is not a directory'};"
                    "if(($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Source root reparse point'};"
                    "$base=$root.FullName.TrimEnd([char]'\\');"
                    "$n=0;$entries=@(Get-ChildItem -LiteralPath $root.FullName -Recurse -Force -ErrorAction Stop | ForEach-Object {"
                    "$script:n++;if($script:n -gt 6000){throw 'Source tree exceeds folder safety limit'};"
                    "if(($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Source contains a reparse point'};"
                    "$rel=$_.FullName.Substring($base.Length+1).Replace('\\','/');"
                    "if($_.PSIsContainer){[PSCustomObject]@{rel=$rel;type='dir'}}"
                    "else{[PSCustomObject]@{rel=$rel;type='file';size=[int64]$_.Length}}"
                    "});"
                    "$json=ConvertTo-Json -InputObject @($entries) -Depth 3 -Compress;" + B64_TAIL, 120)])
                self.assert_data_only(backend.scripts[0], [path])

    def test_list_tree_single_object_and_empty_listing(self):
        one = {"rel": "caf\u00e9.txt", "type": "file", "size": 1}
        self.assertEqual(FolderBackend(Recorder(b64_utf8(json.dumps(one, ensure_ascii=False)))).list_tree(
            "windows", "C:/Test/x"), [one])
        self.assertEqual(FolderBackend(Recorder(b64_utf8("[]"))).list_tree("windows", "C:/Test/x"), [])

    def test_list_tree_corrupt_output_fails_closed(self):
        good = b64_utf8('[{"rel":"caf\u00e9","type":"dir"}]')
        bad_outputs = {
            "raw json, not base64": '[{"rel":"caf\u00e9","type":"dir"}]',
            "truncated base64": good[:-3],
            "illegal characters": good[:8] + "!!" + good[10:],
            "invalid utf-8 bytes": base64.b64encode(b'[{"rel":"caf\xc3("}]').decode("ascii"),
            "windows-1252 bytes": base64.b64encode('[{"rel":"caf\u00e9"}]'.encode("cp1252")).decode("ascii"),
            "valid utf-8 but not json": b64_utf8("not json"),
            "non-ascii text": "W10=\u00e9",
        }
        for label, data in bad_outputs.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    FolderBackend(Recorder(data)).list_tree("windows", "C:/Test/x")

    def test_invalid_windows_paths_never_reach_powershell(self):
        for bad in ("", "C:/Test/a\nb", "C:/" + "a" * 800, "C:/\ud800", None):
            for op in ("path_kind", "list_tree"):
                with self.subTest(op=op, bad=ascii(bad)[:20]):
                    backend = Recorder("file")
                    with self.assertRaises(ValueError):
                        getattr(FolderBackend(backend), op)("windows", bad)
                    self.assertEqual(backend.calls, [])


class ResumableTemplateTests(DataOnlyMixin, unittest.TestCase):
    def test_remote_size_golden_and_parsing(self):
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                backend = Recorder("12345\r\n")
                self.assertEqual(resumable.remote_size(backend, path), 12345)
                self.assertEqual(backend.calls, [(
                    "$ErrorActionPreference='Stop';$f=Get-Item -LiteralPath " + ps(path) +
                    " -Force -ErrorAction Stop;"
                    "if($f.PSIsContainer -or (($f.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Not a normal file'};"
                    "[Console]::Write([long]$f.Length)", 35)])
                self.assert_data_only(backend.scripts[0], [path])

    def test_remote_size_rejects_garbage(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid remote file length"):
            resumable.remote_size(Recorder("-1"), "C:/Test/x")

    def test_remote_prefix_hash_template_and_validation(self):
        digest = "ab" * 32
        for path in HOSTILE:
            with self.subTest(path=ascii(path)):
                backend = Recorder(digest.upper())
                self.assertEqual(resumable.remote_prefix_hash(backend, path, 5), digest)
                self.assertEqual(backend.calls, [(
                    "$ErrorActionPreference='Stop';"
                    "$p=" + ps(path) + ";$remaining=[long]5;"
                    "$s=[System.IO.File]::OpenRead($p);$h=[Security.Cryptography.SHA256]::Create();"
                    "try{if($remaining -gt $s.Length){throw 'Prefix beyond source file'};"
                    "$buffer=New-Object byte[] 1048576;"
                    "while($remaining -gt 0){$n=$s.Read($buffer,0,[int][Math]::Min($remaining,1048576));"
                    "if($n -le 0){throw 'Unexpected remote end-of-file'};"
                    "[void]$h.TransformBlock($buffer,0,$n,$buffer,0);$remaining-=$n};"
                    "[void]$h.TransformFinalBlock([byte[]]::new(0),0,0);"
                    "[Console]::Write([BitConverter]::ToString($h.Hash).Replace('-','').ToLowerInvariant())"
                    "}finally{$h.Dispose();$s.Dispose()}", 180)])
                self.assert_data_only(backend.scripts[0], [path])
        with self.assertRaisesRegex(RuntimeError, "Invalid Windows source prefix"):
            resumable.remote_prefix_hash(Recorder("zz"), "C:/Test/x", 1)
        for bad_size in (-1, "5", 1.5, None, resumable.MAX_BYTES + 1):
            with self.assertRaises(ValueError):
                resumable.remote_prefix_hash(Recorder(digest), "C:/Test/x", bad_size)


class Untouchable:
    """Backend where any use beyond pairing/quote validation fails the test."""
    quote_ps = staticmethod(router.quote_ps)

    def __init__(self, root):
        self.ROOT = Path(root)

    def check_windows_pairing(self):
        return True

    def __getattr__(self, name):  # only reached for attributes not defined above
        raise AssertionError("unexpected backend use before validation: " + name)


class ResumeFileOverflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dc-resume-overflow-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "manager").mkdir(mode=0o700)
        self.deck_file = self.root / "source.bin"
        self.deck_file.write_bytes(b"synthetic")
        self.backend = Untouchable(self.root / "manager")

    def assert_no_journal(self):
        self.assertFalse((self.root / "manager" / resumable.SUBDIR).exists())
        self.assertEqual(list((self.root / "manager").iterdir()), [])

    def test_upload_whose_resume_partial_overflows_is_rejected_before_anything(self):
        destination = "C:/Test/" + "a" * 750  # valid itself (758 B), but +43 B suffix = 801 > 768
        self.assertLessEqual(len(destination), router.MAX_PATH_UTF8_BYTES)
        for resume in (False, True):
            with self.subTest(resume=resume):
                with self.assertRaisesRegex(ValueError, "too long"):
                    resumable.resume_file("steamdeck", str(self.deck_file), "windows", destination,
                                          self.backend, resume=resume)
                self.assert_no_journal()

    def test_download_with_overlong_windows_source_is_rejected_before_anything(self):
        with self.assertRaisesRegex(ValueError, "too long"):
            resumable.resume_file("windows", "C:/Test/" + "a" * 800, "steamdeck", str(self.root / "out.bin"),
                                  self.backend)
        self.assert_no_journal()
        self.assertFalse((self.root / "out.bin").exists())

    def test_control_character_paths_rejected_before_anything(self):
        for bad in ("C:/Test/a\nb", "C:/Test/a\x00b"):
            with self.subTest(bad=ascii(bad)):
                with self.assertRaises(ValueError):
                    resumable.resume_file("steamdeck", str(self.deck_file), "windows", bad, self.backend)
                self.assert_no_journal()

    def test_boundary_partial_exactly_at_limit_passes_validation(self):
        destination = "C:/Test/" + "a" * (router.MAX_PATH_UTF8_BYTES - 8 - len(".dc-resume-") - 32)
        self.assertEqual(len(destination + ".dc-resume-" + "0" * 32), router.MAX_PATH_UTF8_BYTES)
        # Validation passes; the Untouchable backend then fails on the first
        # real Windows call (the parent-directory probe), proving the order.
        with self.assertRaisesRegex(AssertionError, "powershell"):
            resumable.resume_file("steamdeck", str(self.deck_file), "windows", destination, self.backend)
        self.assert_no_journal()


if __name__ == "__main__":
    unittest.main()
