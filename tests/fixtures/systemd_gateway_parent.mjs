// Ephemeral CI-only main service process for abrupt gateway crash testing.
// Uses the SAME production process-tree runner as the MCP gateway.
// All I/O is inside a synthetic temporary test directory; no network SSH.
import {writeFileSync} from 'node:fs';
import {resolve, join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {runIsolatedProcess} from '../../process_tree.mjs';
const folder=resolve(process.argv[2] || '');
if(!folder || !folder.includes('dc-supervised-')) throw Error('Unsafe test folder');
const source=resolve(dirname(fileURLToPath(import.meta.url)), 'timeout_sftp_router.py');
const marker=join(folder,'sftp-pids.txt');
writeFileSync(join(folder,'gateway-main-pid.txt'),String(process.pid)+'\n', {flag:'wx',mode:0o600});
const receipt=join(folder,'recovery-receipt.json');
writeFileSync(receipt,JSON.stringify({schema:1,kind:'disposable_transfer',state:'commit_unknown',autoReplay:false})+'\n',{flag:'wx',mode:0o600});
await runIsolatedProcess('/usr/bin/python3',[source,'copy','--source',marker],{
  timeoutMs:40000,graceMs:120,maxOutputBytes:8192
});
