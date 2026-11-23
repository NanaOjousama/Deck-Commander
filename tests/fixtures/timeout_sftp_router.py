#!/usr/bin/env python3
"""Disposable router fixture that really uploads to local OpenSSH SFTP.

Not production transfer logic. No network, no existing private files or keys.
The gateway must terminate this Python router, sftp client and SFTP server.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

args=sys.argv[1:]
marker=Path(args[args.index('--source')+1]).resolve()
root=marker.parent
root.mkdir(parents=True,exist_ok=True)
sftp=shutil.which('sftp')
server=next((x for x in ['/usr/lib/ssh/sftp-server','/usr/lib/openssh/sftp-server','/usr/libexec/openssh/sftp-server'] if Path(x).is_file()),None)
if not sftp or not server:
 raise RuntimeError('Disposable OpenSSH SFTP server/client missing from CI runner')
source=root/'disposable-source.bin'
target=root/'disposable-uploaded.bin'
source.write_bytes(b'DECK_COMMANDER_LOCAL_SFTP_TIMEOUT_FIXTURE')
child=subprocess.Popen([sftp,'-q','-D',server],stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE)
with marker.open('w',encoding='utf8') as f:
 f.write(f'router:{os.getpid()}\nsftp:{child.pid}\n')
 f.flush();os.fsync(f.fileno())
child.stdin.write(('put "'+str(source)+'" "'+str(target)+'"\n').encode('utf8'))
child.stdin.flush()
for _ in range(80):
 if child.poll() is not None: raise RuntimeError('SFTP exited unexpectedly')
 try:
  kids=(Path('/proc')/str(child.pid)/'task'/str(child.pid)/'children').read_text().split()
  if kids:
   with marker.open('a',encoding='utf8') as f: f.write('server:'+kids[0]+'\n')
   break
 except OSError: pass
 time.sleep(.02)
for _ in range(80):
 if target.exists() and target.read_bytes()==source.read_bytes():
  with marker.open('a',encoding='utf8') as f: f.write('upload:success\n')
  break
 time.sleep(.02)
else:raise RuntimeError('Actual SFTP upload was not observed')
# Deliberately keep all three real subprocesses alive beyond the gateway timeout.
while True:time.sleep(1)
