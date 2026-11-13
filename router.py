#!/usr/bin/env python3
"""Isolated, named-device CLI for the existing ChatGPT DC Steam Deck plugin.
This program NEVER silently changes which device a command targets.
Windows is disabled until SSH pairing is explicitly configured.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("DC_HOME", Path.home() / ".local/share/deck-commander-multidevice"))
CONF = ROOT / "devices.json"
SSHCONF = ROOT / "ssh_config"
ALIASES = ("steamdeck", "windows")


def config():
    return json.loads(CONF.read_text())


def check_windows_pairing():
    target = config()["devices"]["windows"]
    if not target.get("enabled"):
        raise ValueError("Windows is NOT PAIRED. The existing Steam Deck connection still works.")
    return target


def run(cmd, *, data=None, timeout=30):
    p = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode:
        raise RuntimeError("command exited " + str(p.returncode) + ": " +
                           p.stderr.decode("utf-8", "replace")[-400:])
    return p.stdout.decode("utf-8-sig", "replace").strip()


def ssh():
    check_windows_pairing()
    return ["ssh", "-T", "-F", str(SSHCONF), "dc-windows"]


def powershell(script, timeout=60):
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return run(ssh() + ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                         "-EncodedCommand", enc], timeout=timeout)


def quote_ps(raw):
    if not raw or len(raw) > 2048 or any(c in raw for c in "\r\n\0"):
        raise ValueError("Invalid path")
    return "'" + raw.replace("'", "''") + "'"


def remote_exists(path):
    result = powershell("$ErrorActionPreference='Stop';" +
        "[Console]::Write([int](Test-Path -LiteralPath " + quote_ps(path) + "))")
    return result.endswith("1")


def remote_hash(path):
    result = powershell("$ErrorActionPreference='Stop';[Console]::Write(" +
        "(Get-FileHash -Algorithm SHA256 -LiteralPath " + quote_ps(path) + ").Hash)")
    match = re.search("[A-Fa-f0-9]{64}", result)
    if not match:
        raise RuntimeError("No SHA256 returned from Windows")
    return match.group(0).lower()


def local_hash(path):
    dig = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            dig.update(chunk)
    return dig.hexdigest()


def sftp(op, src, dst):
    # Windows OpenSSH SFTP absolute paths use /C:/..., NOT C:/... (relative).
    def windows_sftp_path(raw):
        raw = str(raw).replace("\\", "/")
        if not re.fullmatch(r"[A-Za-z]:/[^\r\n]*", raw):
            raise ValueError("Windows SFTP path must be an absolute drive path such as C:/Users/... ")
        return "/" + raw
    if op == "put":
        dst = windows_sftp_path(dst)
    elif op == "get":
        src = windows_sftp_path(src)
    else:
        raise ValueError("Unsupported SFTP operation")
    # sftp batch syntax allows double-quoted names; do not allow quoting escape tricks.
    def q(value):
        value = str(value)
        if any(c in value for c in '\n\r"\0'):
            raise ValueError("Unsupported path characters for SFTP")
        return '"' + value.replace('\\', '\\\\') + '"'
    batch = (op + " " + q(src) + " " + q(dst) + "\n").encode()
    run(["sftp", "-q", "-b", "-", "-F", str(SSHCONF), "dc-windows"],
        data=batch, timeout=300)


def copy(frm, source, to, target):
    # The independently tested helper is intentionally loaded only for copies.
    from transfer_reliability import copy_v2
    return copy_v2(frm, source, to, target, backend=sys.modules[__name__])


def main():
    parser = argparse.ArgumentParser(description="ChatGPT Deck Commander multi-device helper")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("devices")
    sub.add_parser("selftest")
    sub.add_parser("pending-transfers")
    chk = sub.add_parser("check")
    chk.add_argument("device", choices=ALIASES)
    listing = sub.add_parser("ls")
    listing.add_argument("device", choices=ALIASES)
    listing.add_argument("path")
    cp = sub.add_parser("copy")
    cp.add_argument("--from-device", choices=ALIASES, required=True)
    cp.add_argument("--source", required=True)
    cp.add_argument("--to-device", choices=ALIASES, required=True)
    cp.add_argument("--destination", required=True)
    folder = sub.add_parser("folder-copy", help="Copy a folder; optionally resume only verified completed files")
    folder.add_argument("--from-device", choices=ALIASES, required=True)
    folder.add_argument("--source", required=True)
    folder.add_argument("--to-device", choices=ALIASES, required=True)
    folder.add_argument("--destination", required=True)
    folder.add_argument("--resume", action="store_true", help="Resume only an existing matching folder-transfer journal")
    sub.add_parser("pending-folder-transfers", help="List unfinished folder copy manifests without accessing Windows")
    resume_file_cmd = sub.add_parser("resumable-copy", help="Verified, explicit byte-level resume for one file")
    resume_file_cmd.add_argument("--from-device", choices=ALIASES, required=True)
    resume_file_cmd.add_argument("--source", required=True)
    resume_file_cmd.add_argument("--to-device", choices=ALIASES, required=True)
    resume_file_cmd.add_argument("--destination", required=True)
    resume_file_cmd.add_argument("--resume", action="store_true", help="Continue only an existing matching resumable-file record")
    sub.add_parser("pending-resumable-files", help="Inspect pending resumable-file journals without accessing Windows")
    args = parser.parse_args()
    if args.action == "devices":
        c = config()["devices"]
        for name in ALIASES:
            d = c[name]
            print(name, "LOCAL" if name == "steamdeck" else "SSH",
                  "READY" if name == "steamdeck" or d.get("enabled") else "NOT_PAIRED")
    elif args.action == "selftest":
        assert CONF.is_file()
        assert CONF.stat().st_mode & 0o077 == 0
        assert (ROOT / "keys/windows_ed25519").is_file()
        assert ((ROOT / "keys/windows_ed25519").stat().st_mode & 0o077) == 0
        assert set(config()["devices"]) == set(ALIASES)
        assert os.path.isfile("/usr/bin/ssh") and os.path.isfile("/usr/bin/sftp")
        print("PASS: isolated config, two explicit device aliases, secure key, SSH/SFTP")
    elif args.action == "pending-transfers":
        from transfer_reliability import pending_transfers
        print(json.dumps(pending_transfers(ROOT), indent=2))
    elif args.action == "check":
        if args.device == "steamdeck":
            print("steamdeck: local, ready")
        else:
            print("windows:", powershell("[Console]::Write($env:COMPUTERNAME)"))
    elif args.action == "ls":
        if args.device == "steamdeck":
            root = Path(args.path).expanduser().resolve()
            if not root.is_dir():
                raise NotADirectoryError(root)
            for item in sorted(root.iterdir(), key=lambda x: x.name.lower()):
                print(("DIR " if item.is_dir() else "FILE ") + item.name)
        else:
            cmd = "$ErrorActionPreference='Stop';Get-ChildItem -LiteralPath " + quote_ps(args.path) + " | Select-Object Name,PSIsContainer,Length | ConvertTo-Json -Compress"
            print(powershell(cmd))
    elif args.action == "copy":
        print(json.dumps(copy(args.from_device, args.source, args.to_device, args.destination), indent=2))
    elif args.action == "folder-copy":
        from folder_transfer import folder_copy
        from folder_backend import FolderBackend
        backend=FolderBackend(sys.modules[__name__])
        print(json.dumps(folder_copy(args.from_device,args.source,args.to_device,args.destination,
                                     backend,resume=args.resume),indent=2))
    elif args.action == "resumable-copy":
        from resumable_file_transfer import resume_file
        print(json.dumps(resume_file(args.from_device,args.source,args.to_device,args.destination,
                                     sys.modules[__name__],resume=args.resume),indent=2))
    elif args.action == "pending-resumable-files":
        from resumable_file_transfer import pending_resumable_files
        print(json.dumps(pending_resumable_files(ROOT), indent=2))
    elif args.action == "pending-folder-transfers":
        from folder_transfer import directory_for
        directory=directory_for(ROOT)
        pending=[]
        for file in sorted(directory.glob('*.json')):
            if file.is_symlink():continue
            try:
                item=json.loads(file.read_text(encoding='utf-8'))
                pending.append({k:item.get(k) for k in ('source_device','source','destination_device','destination',
                          'state','processed','total_bytes','last_file','error')})
            except (OSError,ValueError):pending.append({'state':'unreadable','journal':file.name})
        print(json.dumps(pending,indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)