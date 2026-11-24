#!/usr/bin/env python3
"""Render a REVIEW-ONLY systemd unit. Never install, enable or start it.

The connector entrypoint remains an operator-reviewed, separately authenticated
launch program; Deck-Commander cannot invent its provider-specific command.
"""
import argparse
import os
from pathlib import Path
import re
import sys

TEMPLATE = Path(__file__).with_name('deck-commander-connector.service.in')
PATH = re.compile(r'/[A-Za-z0-9_+./-]+')
NAME = re.compile(r'[a-z_][a-z0-9_-]{0,30}')
STATE = re.compile(r'[a-z][a-z0-9_-]{1,50}')


def safe_path(value, description):
    if not isinstance(value,str) or not PATH.fullmatch(value) or '//' in value or '/./' in value or '/../' in value or value.endswith('/..'):
        raise ValueError(description+' must be a plain, absolute path without shell/systemd syntax')
    result = Path(value)
    if result.is_symlink() or not result.exists():
        raise ValueError(description+' must exist and cannot be a symlink')
    return result


def render(app_root, service_user, state_name, connector_entrypoint):
    if not NAME.fullmatch(service_user) or not STATE.fullmatch(state_name):
        raise ValueError('Invalid service account or state-directory name')
    app=safe_path(app_root,'Application directory')
    connector=safe_path(connector_entrypoint,'Connector entrypoint')
    if not app.is_dir() or not connector.is_file() or not os.access(connector,os.X_OK):
        raise ValueError('Application must be a directory and connector must be executable')
    if app == Path('/'):raise ValueError('Application cannot be root filesystem')
    text=TEMPLATE.read_text(encoding='utf8')
    substitutions={'@APP_ROOT@':str(app),'@SERVICE_USER@':service_user,
                   '@STATE_NAME@':state_name,'@CONNECTOR_ENTRYPOINT@':str(connector)}
    for old,new in substitutions.items():text=text.replace(old,new)
    if re.search(r'@[A-Z_]+@',text):raise RuntimeError('Unresolved systemd template placeholder')
    return text


def main():
    parser=argparse.ArgumentParser(description='Generate a service design for review; no installation')
    parser.add_argument('--app-root',required=True)
    parser.add_argument('--service-user',required=True)
    parser.add_argument('--state-name',required=True)
    parser.add_argument('--connector-entrypoint',required=True)
    parser.add_argument('--output',required=True)
    options=parser.parse_args()
    text=render(options.app_root,options.service_user,options.state_name,options.connector_entrypoint)
    path=Path(options.output)
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError('Output must be a new file in an existing absolute directory')
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW
    fd=os.open(path,flags,0o644)
    with os.fdopen(fd,'w',encoding='utf8') as handle:
        handle.write(text)
        handle.flush();os.fsync(handle.fileno())
    print('Rendered review-only unit (not installed or enabled):',path)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError) as e:
        print('SERVICE RENDER REFUSED:',e,file=sys.stderr)
        sys.exit(1)
