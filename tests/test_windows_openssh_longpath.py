#!/usr/bin/env python3
"""Disposable Windows-only OpenSSH and long-path acceptance probe.

Runs on an ephemeral GitHub Windows runner only. Never connects to a remote
system; optional OpenSSH SFTP server uses the local `sftp -D` subprocess mode.
Records exact operations and failures without assuming 260-character support.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def binary(name):
    paths = [shutil.which(name),
             str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'OpenSSH' / name),
             str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'usr' / 'bin' / name)]
    return next((p for p in paths if p and Path(p).is_file()), None)


def sftp_quote(path):
    val = str(path).replace('\\', '/')
    if val[1:3] == ':/' and val[0].isalpha():
        val = '/' + val
    if any(x in val for x in ('\n', '\r', '"', '\0')):
        raise ValueError('Unsafe fixture path')
    return '"' + val + '"'


def test_sftp(root, sftp_client, sftp_server):
    remote, local = root / 'remote', root / 'local'
    remote.mkdir();local.mkdir()
    fixture = {'panel [01].txt': b'EXACT_BRACKET_FILE',
               'panel 0.txt': b'WRONG_ZERO_FILE',
               'panel 1.txt': b'WRONG_ONE_FILE'}
    for name, body in fixture.items():
        (remote/name).write_bytes(body)
    (local/'upload [01].txt').write_bytes(b'UPLOAD_WITH_BRACKETS')

    def command(text):
        result = subprocess.run([sftp_client, '-q', '-D', sftp_server, '-b', '-'],
                                input=(text+'\n').encode('utf-8'),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=25, check=False)
        return {'exit': result.returncode,
                'stderr': result.stderr.decode('utf-8', 'replace')[-350:]}

    download=local/'downloaded.txt'
    get=command('get ' + sftp_quote(remote/'panel [01].txt') + ' ' + sftp_quote(download))
    get['hash_ok']=download.is_file() and download.read_bytes()==fixture['panel [01].txt']

    resumed=local/'resumed.txt'
    resumed.write_bytes(fixture['panel [01].txt'][:6])
    reget=command('reget ' + sftp_quote(remote/'panel [01].txt') + ' ' + sftp_quote(resumed))
    reget['hash_ok']=resumed.read_bytes()==fixture['panel [01].txt']

    uploaded=remote/'new [01].txt'
    put=command('put ' + sftp_quote(local/'upload [01].txt') + ' ' + sftp_quote(uploaded))
    put['hash_ok']=uploaded.is_file() and uploaded.read_bytes()==b'UPLOAD_WITH_BRACKETS'
    intact=all((remote/name).read_bytes()==contents for name,contents in fixture.items())
    result={'get':get,'reget':reget,'put':put,'competing_files_unchanged':intact}
    # Both wrong-file success AND unexpected SFTP failure block this gate.
    if any(x['exit'] != 0 or not x['hash_ok'] for x in (get,reget,put)) or not intact:
        raise AssertionError('Windows SFTP bracketed path transfer failed or matched wrong file: '+repr(result))
    return result


def exact_length_path(root, desired):
    # One filename, never > 255 UTF-16 code units. CI temp root is short enough.
    prefix=str(root).replace('\\','/')+'/'
    remaining=desired-len(prefix)
    if not 1 <= remaining <= 207:  # 207 + 43-byte resume suffix fits one NTFS component
        raise ValueError('Cannot construct portable length fixture: '+repr((len(prefix),remaining)))
    path=root/('x'*remaining)
    assert len(str(path).replace('\\','/'))==desired
    return path


def test_longpaths(root, executable):
    import router
    import transfer_reliability as transfer
    original_ssh,original_run=router.ssh,router.run
    shell=binary(executable)
    if not shell:
        raise RuntimeError('PowerShell executable not found: '+executable)
    router.ssh=lambda: []
    def local_run(command,**kw):
        assert command[0]=='powershell.exe',command[0]
        return original_run([shell]+command[1:],**kw)
    router.run=local_run
    summary=[]
    try:
        for target_len in (220,230,250,265):
            target=exact_length_path(root,target_len)
            partial=Path(str(target)+'.dc-part-'+'0'*32)
            item={'target_utf16_length':target_len,'partial_utf16_length':len(str(partial)),
                  'python_create_partial':None,'ps_exists':None,'ps_sha256':None,
                  'ps_commit':None,'ps_cleanup':None,'partial_remaining':None}
            try:
                partial.write_bytes(('PAYLOAD_'+str(target_len)).encode('ascii'))
                item['python_create_partial']='pass'
            except (OSError,ValueError) as error:
                item['python_create_partial']=type(error).__name__+': '+str(error)[:180]
                summary.append(item)
                continue
            ppath=str(partial).replace('\\','/')
            tpath=str(target).replace('\\','/')
            try:
                item['ps_exists']=router.remote_exists(ppath)
            except Exception as error:
                item['ps_exists']=type(error).__name__+': '+str(error)[-180:]
            try:
                actual=router.remote_hash(ppath)
                item['ps_sha256']=actual==hashlib.sha256(('PAYLOAD_'+str(target_len)).encode('ascii')).hexdigest()
            except Exception as error:
                item['ps_sha256']=type(error).__name__+': '+str(error)[-180:]
            try:
                transfer._remote_commit_no_clobber(type('B',(),{'quote_ps':staticmethod(router.quote_ps),
                    'powershell':staticmethod(router.powershell)})(),ppath,tpath)
                item['ps_commit']=target.read_bytes()==('PAYLOAD_'+str(target_len)).encode('ascii')
            except Exception as error:
                item['ps_commit']=type(error).__name__+': '+str(error)[-180:]
            if partial.exists():
                try:
                    transfer._remote_cleanup(type('B',(),{'quote_ps':staticmethod(router.quote_ps),
                        'powershell':staticmethod(router.powershell)})(),ppath)
                    item['ps_cleanup']=not partial.exists()
                except Exception as error:
                    item['ps_cleanup']=type(error).__name__+': '+str(error)[-180:]
            item['partial_remaining']=partial.exists()
            summary.append(item)
    finally:
        router.ssh,router.run=original_ssh,original_run
    return summary


def test_sftp_and_powershell_longpath_consistency(root, executable, client, server):
    """SFTP-upload a real Windows partial, then hash and commit using actual PowerShell.

    A successful SFTP upload followed by a PowerShell hash/move/cleanup failure
    is a genuine stranded-partial compatibility risk, not a successful test.
    """
    import router
    import transfer_reliability as transfer
    shell=binary(executable)
    if not shell:
        raise RuntimeError('PowerShell binary missing: '+executable)
    original_ssh,original_run=router.ssh,router.run
    router.ssh=lambda: []
    def local_run(cmd,**kwargs):
        assert cmd[0]=='powershell.exe'
        return original_run([shell]+cmd[1:],**kwargs)
    router.run=local_run
    results=[];stranded=[]
    folder=root/'cross-longpaths'
    folder.mkdir()
    source=root/'local-upload.bin'
    try:
        for target_length in (220,230,250,265):
            target=exact_length_path(folder,target_length)
            partial=Path(str(target)+'.dc-part-'+'0'*32)
            body=('CROSS_SFTP_'+str(target_length)).encode('ascii')
            source.write_bytes(body)
            batch='put '+sftp_quote(source)+' '+sftp_quote(partial)+'\n'
            r=subprocess.run([client,'-q','-D',server,'-b','-'],input=batch.encode('utf-8'),
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25,check=False)
            item={'target_length':target_length,'partial_length':len(str(partial)),
                  'sftp_exit':r.returncode,'sftp_error':r.stderr.decode('utf-8','replace')[-180:],
                  'partial_sha256_ok':False,'powershell_sha256_ok':None,
                  'powershell_commit_ok':None,'cleanup_ok':None}
            item['partial_sha256_ok']=partial.is_file() and partial.read_bytes()==body
            if item['partial_sha256_ok']:
                backend=type('B',(),{'quote_ps':staticmethod(router.quote_ps),
                                    'powershell':staticmethod(router.powershell)})()
                try:
                    encoded_path=str(partial).replace('\\','/')
                    target_path=str(target).replace('\\','/')
                    item['powershell_sha256_ok']=router.remote_hash(encoded_path)==hashlib.sha256(body).hexdigest()
                    if item['powershell_sha256_ok']:
                        transfer._remote_commit_no_clobber(backend,encoded_path,target_path)
                        item['powershell_commit_ok']=target.is_file() and target.read_bytes()==body and not partial.exists()
                except Exception as error:
                    item['powershell_error']=type(error).__name__+': '+str(error)[-220:]
                if partial.exists():
                    try:
                        transfer._remote_cleanup(backend,str(partial).replace('\\','/'))
                        item['cleanup_ok']=not partial.exists()
                    except Exception as error:
                        item['cleanup_ok']=type(error).__name__+': '+str(error)[-220:]
                if not (item['powershell_sha256_ok'] and item['powershell_commit_ok']):
                    stranded.append(target_length)
            elif r.returncode==0:
                stranded.append(target_length)  # wrong-file success is never acceptable
            results.append(item)
    finally:
        router.ssh,router.run=original_ssh,original_run
    print('SFTP_POWERSHELL_CROSS_MATRIX='+json.dumps(results,ensure_ascii=True),flush=True)
    if stranded:
        raise AssertionError('SFTP and PowerShell disagree or corrupt at lengths: '+repr(stranded))
    return results


def test_failure_injected_remote_cleanup(root, executable, client, server):
    """Real Windows SFTP + PowerShell: corrupt partial and collision cleanup.

    Synthetic payloads only. A source file is never deleted and a preexisting
    destination must survive exactly; only our own .dc-part file is removed.
    """
    import router
    import transfer_reliability as transfer
    shell=binary(executable)
    oldssh,oldrun=router.ssh,router.run
    router.ssh=lambda: []
    def local_run(command,**kwargs):
        assert command[0]=='powershell.exe'
        return oldrun([shell]+command[1:],**kwargs)
    router.run=local_run
    result=[]
    folder=root/'failure-fixtures'
    folder.mkdir()
    source=root/'failure-source.bin'
    source.write_bytes(b'CANONICAL_SOURCE_CONTENT')
    try:
        backend=type('B',(),{'quote_ps':staticmethod(router.quote_ps),
                              'powershell':staticmethod(router.powershell)})()
        for scenario in ('corrupt_partial','destination_collision'):
            dest=exact_length_path(folder,250)
            partial=Path(str(dest)+'.dc-part-'+'0'*32)
            # File names are reused only after verifying cleanup and resetting fixtures.
            if dest.exists():dest.unlink()
            if partial.exists():partial.unlink()
            batch='put '+sftp_quote(source)+' '+sftp_quote(partial)+'\n'
            uploaded=subprocess.run([client,'-q','-D',server,'-b','-'],input=batch.encode('utf8'),
                                    capture_output=True,timeout=25)
            if uploaded.returncode or partial.read_bytes()!=source.read_bytes():
                raise AssertionError('Could not stage SFTP partial for '+scenario)
            qpartial=str(partial).replace('\\','/')
            qdest=str(dest).replace('\\','/')
            if scenario=='corrupt_partial':
                partial.write_bytes(b'INJECTED_CORRUPTION')
                if router.remote_hash(qpartial)==hashlib.sha256(source.read_bytes()).hexdigest():
                    raise AssertionError('Corrupted Windows partial incorrectly verified')
                transfer._remote_cleanup(backend,qpartial)
                good=(not partial.exists() and not dest.exists() and
                      source.read_bytes()==b'CANONICAL_SOURCE_CONTENT')
            else:
                dest.write_bytes(b'UNRELATED_PREEXISTING_DESTINATION')
                try:
                    transfer._remote_commit_no_clobber(backend,qpartial,qdest)
                except Exception:
                    pass
                else:
                    raise AssertionError('No-clobber move overwrote existing destination')
                transfer._remote_cleanup(backend,qpartial)
                good=(not partial.exists() and dest.read_bytes()==b'UNRELATED_PREEXISTING_DESTINATION'
                      and source.read_bytes()==b'CANONICAL_SOURCE_CONTENT')
            result.append({'scenario':scenario,'partial_length':len(str(partial)),
                           'validated':good,'source_untouched':source.read_bytes()==b'CANONICAL_SOURCE_CONTENT'})
            if not good:raise AssertionError('Failure-path cleanup or no-clobber failed: '+repr(result))
        print('FAILURE_INJECTED_WINDOWS_CLEANUP='+json.dumps(result,ensure_ascii=True),flush=True)
    finally:
        router.ssh,router.run=oldssh,oldrun


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--shell',choices=('powershell.exe','pwsh.exe'),required=True)
    args=parser.parse_args()
    if sys.platform!='win32':
        raise RuntimeError('Must run on a disposable Windows runner')
    sftp_client=binary('sftp.exe')
    sftp_server=binary('sftp-server.exe')
    print('INVENTORY='+json.dumps({'platform':sys.platform,'shell':args.shell,
        'sftp_client':sftp_client,'sftp_server':sftp_server,
        'long_paths_enabled_registry':'not checked (does not alone prove process longPathAware)'},ensure_ascii=True),flush=True)
    with tempfile.TemporaryDirectory(prefix='dc-open-ssh-long-') as d:
        base=Path(d)
        # An installed SFTP server is not guaranteed on Windows Server 2022.
        if sftp_client and sftp_server:
            try:
                sftp_result=test_sftp(base,sftp_client,sftp_server)
                print('SFTP_DIRECT='+json.dumps(sftp_result,ensure_ascii=True),flush=True)
                test_sftp_and_powershell_longpath_consistency(base,args.shell,sftp_client,sftp_server)
                test_failure_injected_remote_cleanup(base,args.shell,sftp_client,sftp_server)
            except Exception as error:
                print('SFTP_DIRECT_ERROR='+type(error).__name__+': '+str(error)[-800:],flush=True)
                raise
        else:
            print('SFTP_DIRECT_SKIPPED=Windows OpenSSH server/client binary not installed on runner',flush=True)
        path_root=base/'paths'
        path_root.mkdir()
        result=test_longpaths(path_root,args.shell)
        print('LONG_PATH_MATRIX='+json.dumps(result,ensure_ascii=True),flush=True)
    print('WINDOWS_PROBE_COMPLETED; inspect per-case evidence before approving long paths.',flush=True)


if __name__=='__main__':main()
