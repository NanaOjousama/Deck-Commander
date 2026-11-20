#!/usr/bin/env python3
"""Explicit, fingerprint-verified onboarding of a Windows SSH device.
Will never accept an unverified SSH host key or store passwords.
"""
import argparse
import base64
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DC_HOME", Path.home() / ".local/share/deck-commander-multidevice"))
CONFIG = ROOT / "devices.json"
SSHCONF = ROOT / "ssh_config"
KNOWN = ROOT / "known_hosts"
KEY = ROOT / "keys/windows_ed25519"


def run(args, *, data=None, timeout=15):
    r = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-350:] or "Command failed")
    return r.stdout.decode("utf-8", "replace").strip()


def main():
    a = argparse.ArgumentParser(description="Pair a verified local Windows OpenSSH Server")
    a.add_argument("--host", required=True, help="Windows LAN IPv4 address")
    a.add_argument("--user", required=True, help="Actual Windows local login username")
    a.add_argument("--host-fingerprint", required=True, help="Host ED25519 SHA256 fingerprint independently checked on Windows")
    a.add_argument("--port", type=int, default=22)
    v = a.parse_args()
    addr = ipaddress.ip_address(v.host)
    if addr.version != 4 or not addr.is_private or addr.is_loopback:
        raise ValueError("Pairing requires a private LAN IPv4 address, not a public host")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", v.user):
        raise ValueError("Unexpected Windows username: ask before using domain/Microsoft account format")
    if not 1 <= v.port <= 65535:
        raise ValueError("Invalid port")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{35,50}", v.host_fingerprint):
        raise ValueError("Expected an ED25519 SSH host fingerprint like SHA256:...")
    hostlines = run(["ssh-keyscan", "-T", "6", "-t", "ed25519",
                     "-p", str(v.port), v.host], timeout=12)
    keylines = [line for line in hostlines.splitlines() if line and not line.startswith("#")]
    if len(keylines) != 1:
        raise ValueError("Could not retrieve exactly one ED25519 host key")
    fp = run(["ssh-keygen", "-lf", "-"], data=(keylines[0]+"\n").encode())
    match = re.search(r"SHA256:[A-Za-z0-9+/]+", fp)
    if not match or match.group(0) != v.host_fingerprint:
        raise ValueError("HOST KEY FINGERPRINT MISMATCH. No configuration was changed.")
    # Do not touch active pairing files until the proposed connection has
    # authenticated successfully. A failed attempt must leave existing pairing
    # configuration unchanged.
    if any(path.is_symlink() for path in (KNOWN, SSHCONF, CONFIG)):
        raise ValueError("Pairing files may not be symlinks")
    known_text = KNOWN.read_text(encoding="utf-8") if KNOWN.exists() else ""
    devices = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(devices.get("devices", {}).get("windows"), dict):
        raise ValueError("Windows device configuration is missing")
    if keylines[0] not in known_text.splitlines():
        if known_text and not known_text.endswith("\n"):
            known_text += "\n"
        known_text += keylines[0] + "\n"

    staged = set()

    def stage(path, content):
        # mkstemp uses exclusive creation and a private 0600 file.
        fd, filename = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
        tmp = Path(filename)
        staged.add(tmp)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        return tmp

    def ssh_config(known_path):
        return "\n".join([
            "Host dc-windows", "    HostName " + v.host, "    User " + v.user,
            "    Port " + str(v.port), "    IdentityFile " + str(KEY),
            "    UserKnownHostsFile " + str(known_path), "    IdentitiesOnly yes",
            "    BatchMode yes", "    StrictHostKeyChecking yes",
            "    ConnectTimeout 6", "    ServerAliveInterval 10",
            "    ConnectionAttempts 1", "    LogLevel ERROR", ""
        ])

    try:
        # SSH verifies the candidate against a temporary known_hosts and
        # ssh_config. Neither the old trusted host list nor router is altered.
        pending_known = stage(KNOWN, known_text)
        probe_ssh = stage(SSHCONF, ssh_config(pending_known))
        probe = base64.b64encode(
            "[Console]::Write($env:COMPUTERNAME)".encode("utf-16-le")
        ).decode()
        answer = run(["ssh", "-T", "-F", str(probe_ssh), "dc-windows",
                      "powershell.exe", "-NoProfile", "-NonInteractive",
                      "-EncodedCommand", probe], timeout=16)

        devices["devices"]["windows"].update(
            {"enabled": True, "host": v.host, "user": v.user, "port": v.port}
        )
        pending_ssh = stage(SSHCONF, ssh_config(KNOWN))
        pending_config = stage(CONFIG, json.dumps(devices, indent=2) + "\n")
        # Each replace is atomic. All are staged after successful verification;
        # multi-file commit/rollback is a follow-up release-hardening task.
        for temp, target in ((pending_known, KNOWN),
                             (pending_ssh, SSHCONF),
                             (pending_config, CONFIG)):
            os.replace(temp, target)
            staged.discard(temp)
    finally:
        for temp in staged:
            temp.unlink(missing_ok=True)
    print("PAIRING SUCCESSFUL: windows:", answer.strip())
    print("Explicit device routing and hashed file transfers are now enabled.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("PAIRING FAILED:", exc, file=sys.stderr)
        sys.exit(1)