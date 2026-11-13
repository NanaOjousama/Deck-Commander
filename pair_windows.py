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
    # The fingerprint matches the value read directly from the Windows host.
    if keylines[0] not in KNOWN.read_text():
        with KNOWN.open("a") as f:
            f.write(keylines[0]+"\n")
    host = "\n".join([
        "Host dc-windows", "    HostName " + v.host, "    User " + v.user,
        "    Port " + str(v.port), "    IdentityFile " + str(KEY),
        "    UserKnownHostsFile " + str(KNOWN), "    IdentitiesOnly yes",
        "    BatchMode yes", "    StrictHostKeyChecking yes",
        "    ConnectTimeout 6", "    ServerAliveInterval 10",
        "    ConnectionAttempts 1", "    LogLevel ERROR", ""
    ])
    tmp = SSHCONF.with_suffix(".tmp")
    tmp.write_text(host)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SSHCONF)
    probe = base64.b64encode("[Console]::Write($env:COMPUTERNAME)".encode("utf-16-le")).decode()
    # Verify passwordless login BEFORE allowing routing to this machine.
    try:
        answer = run(["ssh", "-T", "-F", str(SSHCONF), "dc-windows",
                      "powershell.exe", "-NoProfile", "-NonInteractive",
                      "-EncodedCommand", probe], timeout=16)
    except Exception:
        print("Verified host key, but Windows key login is not ready. Windows remains DISABLED.")
        raise
    devices = json.loads(CONFIG.read_text())
    devices["devices"]["windows"].update(
        {"enabled": True, "host": v.host, "user": v.user, "port": v.port}
    )
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(devices, indent=2)+"\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG)
    print("PAIRING SUCCESSFUL: windows:", answer.strip())
    print("Explicit device routing and hashed file transfers are now enabled.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("PAIRING FAILED:", exc, file=sys.stderr)
        sys.exit(1)