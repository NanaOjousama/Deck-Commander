#!/usr/bin/env python3
"""Offline, no-network tests for Windows pairing safety."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "pair_windows.py"
spec = importlib.util.spec_from_file_location("dc_pair_windows", MODULE_PATH)
pair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pair)

FINGERPRINT = "SHA256:" + "A" * 43
KEYSCAN = "192.168.50.10 ssh-ed25519 AAAA_TEST_KEY"


class PairWindowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "devices.json"
        self.ssh_config = self.root / "ssh_config"
        self.known_hosts = self.root / "known_hosts"
        self.key = self.root / "keys" / "windows_ed25519"
        self.key.parent.mkdir()
        self.key.write_text("NOT A REAL PRIVATE KEY\n")
        self.config.write_text(json.dumps({"devices": {
            "steamdeck": {"enabled": True},
            "windows": {"enabled": False, "host": "", "user": "", "port": 22}
        }}))
        self.ssh_config.write_text("OLD SSH CONFIG\n")
        self.known_hosts.write_text("old.host ssh-ed25519 AAAA_OLD\n")
        self.before = self.snapshot()

    def snapshot(self):
        return {
            str(path): (path.read_bytes() if path.exists() else None)
            for path in (self.config, self.ssh_config, self.known_hosts)
        }

    def fake_run(self, args, **kwargs):
        if args[0] == "ssh-keyscan":
            return KEYSCAN
        if args[0] == "ssh-keygen":
            return "256 " + FINGERPRINT + " host (ED25519)"
        if args[0] == "ssh":
            # The candidate files must be private and separate from live ones.
            conf = Path(args[args.index("-F") + 1])
            self.assertNotEqual(conf, self.ssh_config)
            self.assertEqual(conf.stat().st_mode & 0o077, 0)
            self.assertIn("StrictHostKeyChecking yes", conf.read_text())
            self.assertIn("BatchMode yes", conf.read_text())
            self.assertEqual(self.snapshot(), self.before)
            return "WIN-TEST"
        raise AssertionError("Unexpected command " + repr(args))

    def pair_with(self, run):
        argv = [
            "pair_windows.py", "--host", "192.168.50.10",
            "--user", "tester", "--host-fingerprint", FINGERPRINT
        ]
        with patch.object(pair, "ROOT", self.root), \
             patch.object(pair, "CONFIG", self.config), \
             patch.object(pair, "SSHCONF", self.ssh_config), \
             patch.object(pair, "KNOWN", self.known_hosts), \
             patch.object(pair, "KEY", self.key), \
             patch.object(pair, "run", side_effect=run), \
             patch.object(sys, "argv", argv):
            pair.main()

    def test_success_commits_only_after_verified_ssh(self):
        self.pair_with(self.fake_run)
        cfg = json.loads(self.config.read_text())
        self.assertTrue(cfg["devices"]["windows"]["enabled"])
        self.assertEqual(cfg["devices"]["windows"]["host"], "192.168.50.10")
        self.assertIn(KEYSCAN, self.known_hosts.read_text())
        self.assertIn("old.host ssh-ed25519 AAAA_OLD", self.known_hosts.read_text())
        self.assertIn(str(self.known_hosts), self.ssh_config.read_text())
        for path in (self.config, self.ssh_config, self.known_hosts):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertFalse(list(self.root.glob(".*.stage")))
        self.assertEqual(sorted(x.name for x in self.root.iterdir()),
                         ["devices.json", "keys", "known_hosts", "ssh_config"])

    def test_fingerprint_mismatch_leaves_previous_files_untouched(self):
        def wrong_fingerprint(args, **kwargs):
            if args[0] == "ssh-keyscan":
                return KEYSCAN
            if args[0] == "ssh-keygen":
                return "256 SHA256:" + "B" * 43 + " host (ED25519)"
            raise AssertionError("SSH should not run after fingerprint mismatch")
        with self.assertRaisesRegex(ValueError, "FINGERPRINT MISMATCH"):
            self.pair_with(wrong_fingerprint)
        self.assertEqual(self.snapshot(), self.before)

    def test_ssh_failure_preserves_existing_pairing(self):
        def refuses_login(args, **kwargs):
            if args[0] == "ssh":
                self.assertEqual(self.snapshot(), self.before)
                raise RuntimeError("Authentication denied")
            return self.fake_run(args, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "Authentication denied"):
            self.pair_with(refuses_login)
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse(any(x.name.startswith(".ssh_config.") or
                             x.name.startswith(".known_hosts.")
                             for x in self.root.iterdir()))

    def test_failure_at_each_commit_step_restores_previous_files(self):
        original_replace = pair.os.replace
        for failing_step in (1, 2, 3):
            with self.subTest(failing_step=failing_step):
                calls = [0]

                def fail_once(src, dst):
                    calls[0] += 1
                    if calls[0] == failing_step:
                        raise OSError("injected replacement failure")
                    return original_replace(src, dst)

                with patch.object(pair.os, "replace", side_effect=fail_once):
                    with self.assertRaisesRegex(
                        RuntimeError, "previous configuration restored"
                    ):
                        self.pair_with(self.fake_run)
                self.assertEqual(self.snapshot(), self.before)
                self.assertEqual(
                    sorted(x.name for x in self.root.iterdir()),
                    ["devices.json", "keys", "known_hosts", "ssh_config"],
                )

    def test_failed_commit_with_no_original_known_hosts_removes_new_file(self):
        self.known_hosts.unlink()
        self.before = self.snapshot()
        original_replace = pair.os.replace
        calls = [0]

        def fail_after_known_hosts(src, dst):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("injected ssh config replacement failure")
            return original_replace(src, dst)

        with patch.object(pair.os, "replace", side_effect=fail_after_known_hosts):
            with self.assertRaisesRegex(RuntimeError, "previous configuration restored"):
                self.pair_with(self.fake_run)
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse(self.known_hosts.exists())
        self.assertEqual(
            sorted(x.name for x in self.root.iterdir()),
            ["devices.json", "keys", "ssh_config"],
        )

    def test_rollback_failure_is_reported_not_silenced(self):
        original_replace = pair.os.replace
        calls = [0]

        def fail_commit_then_rollback(src, dst):
            calls[0] += 1
            if calls[0] in (2, 3):
                raise OSError("injected failure")
            return original_replace(src, dst)

        with patch.object(pair.os, "replace", side_effect=fail_commit_then_rollback):
            with self.assertRaisesRegex(RuntimeError, "ROLLBACK INCOMPLETE"):
                self.pair_with(self.fake_run)
        # The device is not enabled because config is written last.
        self.assertEqual(self.config.read_bytes(), self.before[str(self.config)])

    def test_missing_known_hosts_is_created_only_after_success(self):
        self.known_hosts.unlink()
        self.before = self.snapshot()
        self.pair_with(self.fake_run)
        self.assertEqual(self.known_hosts.stat().st_mode & 0o077, 0)
        self.assertEqual(self.known_hosts.read_text(), KEYSCAN + "\n")

    def test_symlink_config_is_refused_without_changes(self):
        self.known_hosts.unlink()
        external = self.root / "external"
        external.write_text("DO NOT TOUCH")
        self.known_hosts.symlink_to(external)
        self.before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "may not be symlinks"):
            self.pair_with(self.fake_run)
        self.assertEqual(external.read_text(), "DO NOT TOUCH")
        self.assertTrue(self.known_hosts.is_symlink())


if __name__ == "__main__":
    unittest.main()
