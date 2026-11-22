#!/usr/bin/env python3
"""Disposable Windows-only runtime smoke test for generated PowerShell commands.

Run on an ephemeral Windows runner, never the user's connected computer.
Windows PowerShell 5.1 and PowerShell 7 execute via -EncodedCommand; no SSH server
or persistent device configuration is involved. This is *not* an SFTP test.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import router
import transfer_reliability as transfers


def run_suite(executable):
    if sys.platform != 'win32':
        raise RuntimeError('Windows runtime smoke must run on Windows CI')
    shell = shutil.which(executable)
    if shell is None:
        raise RuntimeError('Required shell unavailable: ' + executable)

    original_ssh, original_run = router.ssh, router.run
    router.ssh = lambda: []  # replace SSH transport with a local disposable shell

    def local_run(cmd, **kwargs):
        if cmd[0] != 'powershell.exe':
            raise AssertionError('Unexpected executable')
        return original_run([shell] + cmd[1:], **kwargs)

    router.run = local_run
    successes = []
    try:
        print('DIAG_POWERSHELL_VERSION=', router.powershell('[Console]::Write($PSVersionTable.PSVersion.ToString())'), flush=True)
        print('DIAG_GET_FILEHASH_COMMAND=', router.powershell("[Console]::Write((Get-Command -Name Get-FileHash -ErrorAction SilentlyContinue).Name)"), flush=True)
        print('DIAG_MODULE_UTIL=', router.powershell('[Console]::Write((Get-Module -ListAvailable Microsoft.PowerShell.Utility | Select-Object -First 1).Name)'), flush=True)
        with tempfile.TemporaryDirectory(prefix='deck-commander-windows-ci-') as temp:
            root = Path(temp) / 'fixture'
            root.mkdir()
            names = ['café.txt', '香港語文件 廣東話.txt', 'O’Reilly.txt',
                     '🚀.bin', 'panel [01].txt', 'panel 0.txt',
                     'odd’);Write-Host hacked;#.txt']
            for i, name in enumerate(names):
                (root / name).write_bytes(('FIXTURE-'+str(i)).encode('ascii'))
            for i, name in enumerate(names):
                path = str(root / name).replace('\\', '/')
                if not router.remote_exists(path):
                    raise AssertionError('remote_exists false for ' + repr(name))
                got = router.remote_hash(path)
                actual = hashlib.sha256(('FIXTURE-'+str(i)).encode('ascii')).hexdigest()
                if got != actual:
                    raise AssertionError('SHA256 mismatch for ' + repr(name))
            successes.append('exists_and_sha256_unicode_smart_quotes_brackets')

            listing = json.loads(router.windows_ls(str(root).replace('\\', '/')))
            if not isinstance(listing, list):
                raise AssertionError('Windows listing not a JSON array')
            actual_names = {x['Name'] for x in listing}
            if actual_names != set(names):
                raise AssertionError('Unicode filename listing mismatch: ' + repr(actual_names))
            successes.append('lossless_unicode_json_listing')

            empty = root / 'empty'
            empty.mkdir()
            if json.loads(router.windows_ls(str(empty).replace('\\', '/'))) != []:
                raise AssertionError('Empty directory not returned as []')
            single = root / 'single'
            single.mkdir()
            (single / 'only.txt').write_bytes(b'single')
            one = json.loads(router.windows_ls(str(single).replace('\\', '/')))
            if not isinstance(one, list) or len(one)!=1 or one[0]['Name']!='only.txt':
                raise AssertionError('Singleton directory not returned as 1-item array')
            successes.append('empty_and_singleton_json_arrays')

            class LocalBackend:
                quote_ps = staticmethod(router.quote_ps)
                powershell = staticmethod(router.powershell)

            partial = root / 'partial-🚀.dc-part-0123456789abcdef0123456789abcdef'
            final = root / 'final-🚀.txt'
            partial.write_bytes(b'COMMIT_ME')
            transfers._remote_commit_no_clobber(LocalBackend(), str(partial).replace('\\', '/'), str(final).replace('\\', '/'))
            if partial.exists() or final.read_bytes()!=b'COMMIT_ME':
                raise AssertionError('Atomic no-clobber commit failed')
            new_partial = root / 'cleanup-O’Reilly.dc-part-0123456789abcdef0123456789abcdef'
            new_partial.write_bytes(b'TEMP')
            transfers._remote_cleanup(LocalBackend(), str(new_partial).replace('\\', '/'))
            if new_partial.exists():
                raise AssertionError('Cleanup failed')
            successes.append('commit_and_cleanup')

            # Existing destination must not be overwritten by File.Move.
            existing = root / 'existing.txt'
            candidate = root / 'candidate.txt'
            existing.write_bytes(b'ORIGINAL')
            candidate.write_bytes(b'NEW')
            try:
                transfers._remote_commit_no_clobber(LocalBackend(), str(candidate).replace('\\', '/'), str(existing).replace('\\', '/'))
            except RuntimeError:
                pass
            else:
                raise AssertionError('File.Move overwrote a preexisting destination')
            if existing.read_bytes()!=b'ORIGINAL' or candidate.read_bytes()!=b'NEW':
                raise AssertionError('File contents changed after refused commit')
            successes.append('no_clobber')

            print(json.dumps({'shell':executable,'shell_path':str(shell),
                              'result':'PASS','checks':successes}, ensure_ascii=True))
    finally:
        router.ssh, router.run = original_ssh, original_run


if __name__ == '__main__':
    shell = sys.argv[1] if len(sys.argv)>1 else 'powershell.exe'
    run_suite(shell)
