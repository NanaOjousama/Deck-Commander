#!/usr/bin/env python3
"""Read-only preflight for proposed isolated systemd deployments.

Never creates accounts, changes permissions, starts services, prints secrets,
or installs anything. Do not mistake PASS for external connector authentication.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import stat
import sys


def require_private_file(path, uid, label):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_mode & 0o077:
        raise ValueError(label+' must be a private regular file owned by service account')


def check(app_root, state_root, service_user, connector_entrypoint):
    account=pwd.getpwnam(service_user)
    if account.pw_uid==0 or account.pw_shell not in ('/usr/sbin/nologin','/sbin/nologin','/bin/false'):
        raise ValueError('Dedicated service user must be unprivileged and non-login')
    app=Path(app_root);state=Path(state_root);launcher=Path(connector_entrypoint)
    if not all(x.is_absolute() for x in (app,state,launcher)):
        raise ValueError('All paths must be absolute')
    if app==state:
        raise ValueError('Application and state directories cannot be identical')
    app_info=app.lstat();state_info=state.lstat();launcher_info=launcher.lstat()
    if (not stat.S_ISDIR(app_info.st_mode) or app_info.st_uid!=0 or
            app_info.st_mode & 0o022 or app.resolve()!=app):
        raise ValueError('Application code root must be root-owned and not group-writable')
    if (not stat.S_ISDIR(state_info.st_mode) or state_info.st_uid!=account.pw_uid or
            state_info.st_mode & 0o077 or state.resolve()!=state):
        raise ValueError('Private state must be owned by service account (mode 0700)')
    if (not stat.S_ISREG(launcher_info.st_mode) or launcher_info.st_uid!=0 or
            launcher_info.st_mode & 0o022 or not launcher_info.st_mode & 0o111):
        raise ValueError('Connector launcher must be a root-owned, executable regular file')
    for name in ('gateway.mjs','service_layout.mjs','router.py','process_tree.mjs'):
        code=app/name
        info=code.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022):
            raise ValueError('Application source is writable or untrusted: '+name)
    config=state/'devices.json'
    require_private_file(config,account.pw_uid,'devices.json')
    obj=json.loads(config.read_text(encoding='utf8'))
    if not isinstance(obj,dict) or not isinstance(obj.get('devices'),dict):
        raise ValueError('Invalid device configuration format')
    for name in ('steamdeck','windows'):
        device=obj['devices'].get(name)
        if not isinstance(device,dict) or type(device.get('enabled')) is not bool:
            raise ValueError('Missing explicit device enabled setting')
        allow=device.get('allowed_tools')
        if not isinstance(allow,list) or len(allow)>200 or any(
            not isinstance(tool,str) or not tool or len(tool)>200 for tool in allow):
            raise ValueError('Device must have an explicit tool allowlist')
    if obj['devices']['windows']['enabled']:
        for name in ('ssh_config','known_hosts','keys/windows_ed25519'):
            require_private_file(state/name,account.pw_uid,name)
    return True


def main():
    parser=argparse.ArgumentParser(description='Read-only least-privilege service preflight')
    for name in ('app-root','state-root','service-user','connector-entrypoint'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    check(args.app_root,args.state_root,args.service_user,args.connector_entrypoint)
    print('PASS: file ownership, strict device allowlists and private state. Connector identity remains UNVERIFIED.')


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as error:
        print('REFUSED: least-privilege installation preflight failed ('+type(error).__name__+')',file=sys.stderr)
        sys.exit(1)
