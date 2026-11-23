#!/usr/bin/env python3
"""Real systemd cgroup crash-containment acceptance on disposable Ubuntu CI.

Starts a transient test-only system service owned by the CI runner user. The
service's Node main invokes the production process_tree.mjs runner, spawning a
local Python router -> OpenSSH sftp -> OpenSSH sftp-server. SIGKILL its Node
main and assert systemd cleans all descendants, leaves unrelated work alone,
and preserves a synthetic receipt. Never run on a personal machine.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
RECEIPT = b'{"schema":1,"kind":"disposable_transfer","state":"commit_unknown","autoReplay":false}\n'


def run(*args, check=True, timeout=10):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode:
        raise AssertionError(f"Command failed: {args[0]} exit={proc.returncode}: {proc.stderr[-500:]}")
    return proc


def alive(pid):
    if not isinstance(pid,int) or pid < 2:
        return False
    try:
        match=re.search(r'^State:\s+(\S)',Path(f'/proc/{pid}/status').read_text(),re.M)
        return bool(match and match.group(1) not in ('Z','X'))
    except (OSError,PermissionError):
        return False


def cgroup(pid):
    return Path(f'/proc/{pid}/cgroup').read_text()


def worker_pids(marker):
    if not marker.is_file():
        return {}
    result={}
    for line in marker.read_text().splitlines():
        name,separator,value=line.partition(':')
        if separator and name in ('router','sftp','server') and value.isdecimal():
            result[name]=int(value)
    return result


def await_condition(predicate,deadline_seconds,explanation):
    end=time.monotonic()+deadline_seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.06)
    raise AssertionError(explanation)


def main():
    if sys.platform!='linux' or not NODE or Path('/proc/1/comm').read_text().strip()!='systemd':
        raise RuntimeError('This acceptance test requires an isolated Linux systemd CI runner')
    if not os.environ.get('GITHUB_ACTIONS')=='true' or not os.environ.get('RUNNER_TEMP'):
        raise RuntimeError('Refusing to run system service test outside ephemeral GitHub Actions')
    if os.geteuid()==0:
        raise RuntimeError('Run from unprivileged CI runner; use sudo only for transient systemd-run')
    if not shutil.which('sudo'):
        raise RuntimeError('CI sudo unavailable')
    if not Path('/usr/bin/sftp').is_file() or not any(Path(s).is_file() for s in ('/usr/lib/openssh/sftp-server','/usr/lib/ssh/sftp-server')):
        raise RuntimeError('OpenSSH client and server missing')
    principal=os.environ.get('USER','')
    if not re.fullmatch('[a-z_][a-z0-9_-]{0,30}',principal):
        raise AssertionError('Unexpected CI user identity')
    with tempfile.TemporaryDirectory(prefix='dc-supervised-',dir=os.environ['RUNNER_TEMP']) as folder:
        work=Path(folder)
        marker=work/'sftp-pids.txt'
        receipt=work/'recovery-receipt.json'
        mainpid_file=work/'gateway-main-pid.txt'
        unit='dc-disposable-ci-'+uuid.uuid4().hex[:12]+'.service'
        unrelated=subprocess.Popen(['/usr/bin/sleep','40'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        all_pids={}; mainpid=0
        try:
            properties=[
                'Type=exec','KillMode=control-group','Restart=no',
                'SendSIGKILL=yes','TimeoutStopSec=2s','NoNewPrivileges=yes',
                'User='+principal,'PrivateTmp=no','WorkingDirectory='+str(ROOT)
            ]
            command=['sudo','-n','systemd-run','--system','--collect','--unit='+unit]
            for prop in properties: command.append('--property='+prop)
            command.extend(['--',NODE,str(ROOT/'tests/fixtures/systemd_gateway_parent.mjs'),str(work)])
            run(*command, timeout=15)
            await_condition(lambda:mainpid_file.is_file() and len(worker_pids(marker))==3 and
                            marker.is_file() and 'upload:success' in marker.read_text(),
                            12,'Transient service did not perform real SFTP upload')
            mainpid=int(mainpid_file.read_text().strip())
            all_pids={'gateway':mainpid,**worker_pids(marker)}
            if len(all_pids)!=4 or not all(alive(pid) for pid in all_pids.values()):
                raise AssertionError('Four active service processes required before crash: '+repr(all_pids))
            if not all(unit in cgroup(pid) for pid in all_pids.values()):
                raise AssertionError('Some descendants escaped the service cgroup')
            properties=run('systemctl','show',unit,'--property=KillMode,Restart,SendSIGKILL,MainPID','--no-pager').stdout
            if 'KillMode=control-group' not in properties or 'Restart=no' not in properties:
                raise AssertionError('Service not configured with appropriate kill/restart policy')
            before=receipt.read_bytes()
            if before!=RECEIPT:raise AssertionError('Initial receipt differs from expected synthetic bytes')
            if (work/'disposable-uploaded.bin').read_bytes()!=b'DECK_COMMANDER_LOCAL_SFTP_TIMEOUT_FIXTURE':
                raise AssertionError('Real local SFTP transfer missing')
            os.kill(mainpid,signal.SIGKILL)
            await_condition(lambda:all(not alive(pid) for pid in all_pids.values()),
                            8,'Uncatchable gateway crash left active local transfers')
            if not alive(unrelated.pid):
                raise AssertionError('Unrelated test process was incorrectly terminated')
            if receipt.read_bytes()!=before:
                raise AssertionError('Recovery receipt changed/disappeared during service crash')
            if not (work/'disposable-uploaded.bin').exists():
                raise AssertionError('Previously transferred test data vanished')
            print(json.dumps({'result':'PASS','supervision':'transient_systemd_service',
                  'kill_mode':'control-group','restart':'no','service_processes_cleaned':4,
                  'sftp_upload_verified':True,'unrelated_process_survived':True,
                  'synthetic_receipt_sha256':hashlib.sha256(before).hexdigest(),
                  'synthetic_receipt_unchanged':True,'test_unit':unit}),flush=True)
        finally:
            # Only stop this random disposable unit; never control any installed service.
            run('sudo','-n','systemctl','stop',unit,check=False,timeout=9)
            run('sudo','-n','systemctl','reset-failed',unit,check=False,timeout=5)
            for pid in all_pids.values():
                if alive(pid):
                    # Verify PID still belongs to disposable unit before emergency cleanup.
                    try:
                        if unit in cgroup(pid):os.kill(pid,signal.SIGKILL)
                    except (OSError,PermissionError):pass
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.communicate(timeout=3)

if __name__=='__main__':main()
