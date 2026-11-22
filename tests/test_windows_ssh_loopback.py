#!/usr/bin/env python3
"""Ephemeral Windows sshd loopback integration test; no personal device/network.

One Windows runner only. Starts sshd with ListenAddress 127.0.0.1, ephemeral
host/client keys and a custom test config. No service, firewall or public port.
Checks actual `ssh -T` + production router generated PowerShell and negative
host-key/authorization checks. Deletes material and closes sshd in finally.
"""
import base64
import getpass
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def executable(name):
    return shutil.which(name) or str(Path(os.environ['SystemRoot'])/'System32'/'OpenSSH'/name)


def call(args, *, timeout=30, expect_success=True):
    r=subprocess.run([str(s) for s in args],capture_output=True,timeout=timeout)
    if expect_success and r.returncode:
        raise RuntimeError('Command '+str(args[0])+' exit '+str(r.returncode)+': '+r.stderr.decode('utf-8','replace')[-900:])
    return r


def acl_private(path):
    # Windows OpenSSH StrictModes requires owner-only/admin/system key material.
    user=getpass.getuser()
    call(['icacls',str(path),'/inheritance:r','/grant:r',
          '*S-1-5-18:(F)','*S-1-5-32-544:(F)',user+':(F)'])


def build(root, port):
    ssh=executable('ssh.exe');sshd=executable('sshd.exe');keygen=executable('ssh-keygen.exe')
    for item in (ssh,sshd,keygen):
        if not Path(item).is_file():raise RuntimeError('Windows OpenSSH missing: '+str(item))
    server_key=root/'host_ed25519'
    client_key=root/'client_ed25519'
    rogue_key=root/'rogue_ed25519'
    for path in (server_key,client_key,rogue_key):
        call([keygen,'-q','-t','ed25519','-N','','-f',str(path)])
        acl_private(path)
    authorized=root/'authorized_keys'
    authorized.write_text(client_key.with_suffix('.pub').read_text(encoding='utf8'),encoding='utf8')
    acl_private(authorized)
    hostpub=server_key.with_suffix('.pub').read_text(encoding='utf8').split()
    known=root/'known_hosts';known.write_text('[127.0.0.1]:'+str(port)+' '+hostpub[0]+' '+hostpub[1]+'\n',encoding='ascii')
    bad_pub=rogue_key.with_suffix('.pub').read_text(encoding='utf8').split()
    bad_known=root/'bad_known_hosts';bad_known.write_text('[127.0.0.1]:'+str(port)+' '+bad_pub[0]+' '+bad_pub[1]+'\n',encoding='ascii')
    username=getpass.getuser()
    def configpath(p):return str(p).replace('\\','/')
    server_conf=root/'sshd_loopback_config'
    server_conf.write_text('\n'.join([
        'Port '+str(port),
        'AddressFamily inet',
        'ListenAddress 127.0.0.1',
        'HostKey '+configpath(server_key),
        'AuthorizedKeysFile '+configpath(authorized),
        'PubkeyAuthentication yes','PasswordAuthentication no',
        'KbdInteractiveAuthentication no','PermitEmptyPasswords no',
        'AllowTcpForwarding no','X11Forwarding no','PermitTunnel no',
        'LogLevel VERBOSE','AllowUsers '+username,
        'Subsystem sftp '+configpath(Path(os.environ['SystemRoot'])/'System32'/'OpenSSH'/'sftp-server.exe'),
        '']) ,encoding='utf8')
    acl_private(server_conf)
    call([sshd,'-t','-f',str(server_conf)])
    client_config=root/'ssh_client_config'
    client_config.write_text('\n'.join([
        'Host dc-ci-loopback',
        '  HostName 127.0.0.1',
        '  Port '+str(port),
        '  User '+username,
        '  IdentityFile '+configpath(client_key),
        '  IdentitiesOnly yes',
        '  BatchMode yes',
        '  StrictHostKeyChecking yes',
        '  UserKnownHostsFile '+configpath(known),
        '  GlobalKnownHostsFile '+configpath(root/'none'),
        '  CheckHostIP no',
        '  ControlMaster no',
        '  ConnectTimeout 4',
        '']) ,encoding='utf8')
    return ssh,sshd,server_conf,client_config,known,bad_known,authorized


def run():
    if sys.platform!='win32':raise RuntimeError('Disposable Windows CI only')
    import router
    import transfer_reliability
    with tempfile.TemporaryDirectory(prefix='dc-ci-ssh-loopback-') as tmp:
        root=Path(tmp)
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        ssh,sshd,server_conf,client_config,known,bad_known,authorized=build(root,port)
        process=subprocess.Popen([sshd,'-D','-e','-f',str(server_conf)],
              stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(45):
                if process.poll() is not None:
                    err=process.stderr.read().decode('utf-8','replace')[-1500:]
                    raise RuntimeError('sshd exited before loopback test: '+err)
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=0.2):break
                except OSError:time.sleep(0.15)
            else:raise TimeoutError('Loopback sshd did not bind')
            base=[ssh,'-T','-F',str(client_config),'dc-ci-loopback']
            p=call(base+['echo','LOOPBACK_AUTH_OK'])
            if 'LOOPBACK_AUTH_OK' not in p.stdout.decode('utf8','replace'):
                raise AssertionError('ssh -T did not execute authorized command')
            print('SSH_LOOPBACK_AUTH=PASS',flush=True)

            old_ssh=router.ssh
            try:
                router.ssh=lambda:base
                remote_home=router.powershell('[Console]::Write($PSVersionTable.PSVersion.ToString())')
                print('SSH_T_POWERSHELL_VERSION='+remote_home,flush=True)
                fixture=root/'Café 香港 🚀 O’Reilly [01].bin'
                fixture.write_bytes(b'SAFE_UTF8_PATH_CONTENT')
                actual_path=str(fixture).replace('\\','/')
                assert router.remote_exists(actual_path)
                assert router.remote_hash(actual_path)==hashlib.sha256(b'SAFE_UTF8_PATH_CONTENT').hexdigest()
                listing=json.loads(router.windows_ls(str(root).replace('\\','/')))
                assert isinstance(listing,list) and fixture.name in {x['Name'] for x in listing}
                print('SSH_T_UNICODE_HASH_AND_BASE64_JSON=PASS',flush=True)
            finally:router.ssh=old_ssh

            # Pinned host keys: a different host key MUST fail, even if user key is valid.
            denied=call(base[:1]+['-T','-F',str(client_config),'-o',
                        'UserKnownHostsFile='+str(bad_known),'dc-ci-loopback','echo','MUST_NOT_RUN'],
                        expect_success=False)
            if denied.returncode==0 or b'MUST_NOT_RUN' in denied.stdout:
                raise AssertionError('Wrong host key was accepted')
            print('SSH_HOST_KEY_MISMATCH_DENIED=PASS',flush=True)

            # Revocation: remove client public key, force new SSH connection, deny.
            original=authorized.read_bytes();authorized.write_bytes(b'')
            try:
                denied=call(base+['echo','MUST_NOT_RUN'],expect_success=False)
                if denied.returncode==0 or b'MUST_NOT_RUN' in denied.stdout:
                    raise AssertionError('Client remained authorized after key removal')
            finally:authorized.write_bytes(original)
            print('SSH_REVOKED_KEY_DENIED=PASS',flush=True)
            print('SSH_LOOPBACK_COMPLETE=PASS',flush=True)
        finally:
            process.terminate()
            try:process.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill();process.communicate(timeout=4)


if __name__=='__main__':run()
