#!/usr/bin/env python3
"""Disposable GitHub-only service topology, private paths and SIGKILL test.

Creates an ephemeral systemd user/account/unit on a hosted runner and removes
only those randomly named resources in finally. It NEVER targets Steam Deck.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
PYTHON=sys.executable
NODE=shutil.which('node')


def call(*args, check=True, timeout=12, input=None):
    p=subprocess.run(args,input=input,capture_output=True,text=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(args[0]+' exit='+str(p.returncode)+' '+p.stderr[-550:])
    return p


def alive(pid):
    try:
        status=Path(f'/proc/{pid}/status').read_text()
        state=re.search(r'^State:\s+([A-Z])',status,re.M)
        return bool(state and state[1] not in ('Z','X'))
    except OSError:return False


def within_cgroup(pid,unit):
    try:return unit in Path(f'/proc/{pid}/cgroup').read_text()
    except OSError:return False


def wait_for(predicate,seconds,message):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.07)
    raise AssertionError(message)


def sudo_read(path):
    return call('sudo','-n','cat',str(path)).stdout


def read_worker_pids(marker):
    try:body=sudo_read(marker)
    except RuntimeError:return {}
    pids={}
    for line in body.splitlines():
        part=line.split(':',1)
        if len(part)==2 and part[0] in ('router','sftp','server') and part[1].isdecimal():
            pids[part[0]]=int(part[1])
    return pids


def main():
    if (sys.platform!='linux' or os.geteuid()==0 or not NODE or
            os.environ.get('GITHUB_ACTIONS')!='true' or
            not os.environ.get('RUNNER_TEMP') or
            Path('/proc/1/comm').read_text().strip()!='systemd'):
        raise RuntimeError('Refusing service installation test outside disposable GitHub-hosted Linux CI')
    uid=secrets.token_hex(4)
    user='dc_ci_'+uid
    state_name='dc-supervised-ci-'+uid
    app=Path('/opt')/('dc-ci-'+uid)
    state=Path('/var/lib')/state_name
    unit='dc-ci-'+uid+'.service'
    unit_path=Path('/run/systemd/system')/unit
    wrapper=app/'bin/connector-entrypoint'
    other=subprocess.Popen(['/usr/bin/sleep','45'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    created_user=False; installed_unit=False; pids={}
    with tempfile.TemporaryDirectory(prefix='dc-service-render-',dir=os.environ['RUNNER_TEMP']) as scratch:
        try:
            call('sudo','-n','useradd','--system','--no-create-home','--shell','/usr/sbin/nologin',
                 '--user-group',user)
            created_user=True
            for dirname in ('','bin','service','tests','tests/fixtures'):
                call('sudo','-n','install','-d','-m','0755',str(app/dirname))
            for source in ('gateway.mjs','gateway_access.mjs','router.py',
                           'service_layout.mjs','process_tree.mjs','transfer_registry.mjs',
                           'package.json','service/check_installation.py',
                           'tests/fixtures/timeout_sftp_router.py',
                           'tests/fixtures/systemd_gateway_parent.mjs',
                           'tests/fixtures/supervised_connector_parent.mjs'):
                call('sudo','-n','install','-m','0644',str(ROOT/source),str(app/source))
            local_wrapper=Path(scratch)/'connector-entrypoint'
            local_wrapper.write_text('#!/bin/sh\nexec '+NODE+' '+str(app/'tests/fixtures/supervised_connector_parent.mjs')+'\n')
            call('sudo','-n','install','-m','0755',str(local_wrapper),str(wrapper))
            call('sudo','-n','chown','-R','root:root',str(app))
            local_unit=Path(scratch)/unit
            call(PYTHON,str(ROOT/'service/render_service.py'),'--app-root',str(app),
                 '--service-user',user,'--state-name',state_name,
                 '--connector-entrypoint',str(wrapper),'--output',str(local_unit))
            call('sudo','-n','install','-m','0644',str(local_unit),str(unit_path))
            installed_unit=True
            call('sudo','-n','systemctl','daemon-reload')
            call('sudo','-n','systemd-analyze','verify',str(unit_path))
            call('sudo','-n','systemctl','start',unit,timeout=15)
            cp=state/'connector-main-pid.txt';gp=state/'gateway-main-pid.txt'
            marker=state/'sftp-pids.txt'
            receipt=state/'recovery-receipt.json'
            wait_for(lambda:len(read_worker_pids(marker))==3 and
                     'upload:success' in sudo_read(marker),13,'SFTP upload did not complete inside service')
            pids={'connector':int(sudo_read(cp).strip()),'gateway':int(sudo_read(gp).strip()),
                  **read_worker_pids(marker)}
            if len(pids)!=5 or not all(alive(pid) and within_cgroup(pid,unit) for pid in pids.values()):
                raise AssertionError('Five processes must live inside one systemd cgroup')
            metadata=call('sudo','-n','systemctl','show',unit,
                          '--property=MainPID,User,KillMode,Restart,NoNewPrivileges,ProtectHome,ProtectSystem,StateDirectoryMode',
                          '--no-pager').stdout
            for text in ('KillMode=control-group','Restart=no','NoNewPrivileges=yes',
                         'ProtectHome=yes','ProtectSystem=strict','StateDirectoryMode=0700',
                         'User='+user):
                if text not in metadata:raise AssertionError('Missing required systemd control '+text)
            app_st=app.stat();state_st=state.stat()
            if app_st.st_uid!=0 or app_st.st_mode & 0o022 or state_st.st_mode & 0o077 or state_st.st_uid==0:
                raise AssertionError('Unexpected application/state ownership')
            # Create a sample owner-private, fail-closed configuration for static preflight.
            config={'devices':{
                'steamdeck':{'enabled':True,'allowed_tools':['list_devices','read_file']},
                'windows':{'enabled':False,'allowed_tools':[]}}}
            call('sudo','-n','-u',user,'/usr/bin/python3','-c',
                 'import json,os,sys;fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);'
                 'os.write(fd,json.dumps(json.loads(sys.argv[2])).encode());os.close(fd)',
                 str(state/'devices.json'),json.dumps(config))
            checked=call('sudo','-n','-u',user,'/usr/bin/python3',
                         str(app/'service/check_installation.py'),
                 '--app-root',str(app),'--state-root',str(state),
                 '--service-user',user,'--connector-entrypoint',str(wrapper)).stdout
            if 'PASS:' not in checked:raise AssertionError('Read-only preflight did not pass')
            before=sudo_read(receipt)
            if not before or 'commit_unknown' not in before:raise AssertionError('No durable synthetic receipt')
            if sudo_read(state/'disposable-uploaded.bin')!='DECK_COMMANDER_LOCAL_SFTP_TIMEOUT_FIXTURE':
                raise AssertionError('OpenSSH SFTP did not upload synthetic payload')
            # Only target the unique service main process after confirming its cgroup.
            mainpid=int(re.search(r'^MainPID=(\d+)',metadata,re.M).group(1))
            if mainpid!=pids['connector'] or not within_cgroup(mainpid,unit):
                raise AssertionError('Refusing kill of unexpected process')
            call('sudo','-n','kill','-KILL',str(mainpid))
            wait_for(lambda:all(not alive(pid) for pid in pids.values()),9,
                     'systemd failed to reap gateway and SFTP descendants')
            if other.poll() is not None:raise AssertionError('Unrelated process stopped')
            if sudo_read(receipt)!=before:raise AssertionError('Synthetic recovery receipt changed after crash')
            if sudo_read(state/'disposable-uploaded.bin')!='DECK_COMMANDER_LOCAL_SFTP_TIMEOUT_FIXTURE':
                raise AssertionError('Previously transferred bytes disappeared')
            print(json.dumps({'result':'PASS','account':'non_admin_nologin',
                'code_owner':'root_readonly','state_dir_mode':'0700',
                'connector_is_supervised_main':True,
                'subprocesses_in_service_cgroup':5,'cleaned_after_sigkill':5,
                'unrelated_process_untouched':True,'synthetic_receipt_unchanged':True,
                'preflight':'PASS','real_local_sftp':'PASS'}),flush=True)
        finally:
            if installed_unit:
                call('sudo','-n','systemctl','stop',unit,check=False)
                call('sudo','-n','systemctl','reset-failed',unit,check=False)
                call('sudo','-n','rm','-f',str(unit_path),check=False)
                call('sudo','-n','systemctl','daemon-reload',check=False)
            for pid in pids.values():
                if alive(pid) and within_cgroup(pid,unit):
                    call('sudo','-n','kill','-KILL',str(pid),check=False)
            # These names were generated internally from a random 8-hex suffix.
            if re.fullmatch(r'dc-ci-[0-9a-f]{8}',app.name):
                call('sudo','-n','rm','-rf','--',str(app),check=False)
            if re.fullmatch(r'dc-supervised-ci-[0-9a-f]{8}',state.name):
                call('sudo','-n','rm','-rf','--',str(state),check=False)
            if created_user:call('sudo','-n','userdel',user,check=False)
            if other.poll() is None:other.kill()
            other.communicate(timeout=3)


if __name__=='__main__':main()
