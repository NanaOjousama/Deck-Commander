import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {mkdtempSync,writeFileSync,readFileSync,copyFileSync,chmodSync,rmSync,existsSync,renameSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const repo=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const gateway=join(repo,'gateway.mjs');
const mock=join(repo,'tests/fixtures/mock_mcp_backend.mjs');
const routerFixture=join(repo,'tests/fixtures/timeout_sftp_router.py');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const text=x=>(x.content||[]).map(y=>y.text||'').join('\n');
function inspect(pid){
 try {const value=readFileSync(`/proc/${pid}/status`,'utf8').match(/^State:\s+([A-Z])/m);return value&&!['Z','X'].includes(value[1]);}
 catch{return false;}
}
function states(marker){
 if(!existsSync(marker))return {};
 const out={};
 for(const m of readFileSync(marker,'utf8').matchAll(/^(router|sftp|server):(\d+)$/gm))out[m[1]]=Number(m[2]);
 return out;
}
async function until(fn,timeout=1500){const end=Date.now()+timeout;while(Date.now()<end){if(fn())return;await pause(30);}assert(fn(),'condition never satisfied');}

test('real MCP device revocation terminates Python router, OpenSSH SFTP and server',{timeout:16000},async()=>{
 if(process.platform!=='linux')throw Error('Linux CI only');
 const root=mkdtempSync(join(tmpdir(),'dc-gateway-real-sftp-revocation-'));
 const marker=join(root,'sftp-pids.txt');
 const config=join(root,'devices.json');
 const sshFixture=join(root,'fake-ssh-launcher');
 const backends={steamdeck:{enabled:true,allowed_tools:['list_devices','copy_file_between_devices']},
                 windows:{enabled:true,allowed_tools:['list_devices','copy_file_between_devices']}};
 writeFileSync(config,JSON.stringify({devices:backends}),{mode:0o600});
 writeFileSync(sshFixture,'#!/bin/sh\nexec '+JSON.stringify(process.execPath)+' '+JSON.stringify(mock)+'\n',{mode:0o700});
 chmodSync(sshFixture,0o700);
 copyFileSync(routerFixture,join(root,'router.py'));
 let client,transport,pids={};
 try{
  client=new Client({name:'isolated-sftp-revoke-check',version:'0.0.0'},{capabilities:{}});
  transport=new StdioClientTransport({command:process.execPath,args:[gateway],env:{
    ...process.env,DC_HOME:root,DC_STEAMDECK_NODE:process.execPath,
    DC_STEAMDECK_ENTRYPOINT:mock,DC_WINDOWS_SSH_BINARY:sshFixture,
    DC_WINDOWS_HOSTNAME:'localhost',DC_TRANSFER_TIMEOUT_MS:'8000',
    DC_REQUIRE_TOOL_ALLOWLIST:'1'
  },stderr:'pipe'});
  await client.connect(transport);
  const tools=await client.listTools();
  assert(tools.tools.some(t=>t.name==='copy_file_between_devices'));
  const request=client.callTool({name:'copy_file_between_devices',arguments:{
    source_device:'steamdeck',source_path:marker,
    destination_device:'windows',destination_path:'C:/Synthetic/not-used.bin'
  }});
  // Attach a handler before the asynchronous revocation test settles.
  const outcome=Promise.allSettled([request]);
  await until(()=>Object.keys(states(marker)).length===3 &&
    readFileSync(marker,'utf8').includes('upload:success'),3200);
  pids=states(marker);
  const newConfig=join(root,'devices.next');
  writeFileSync(newConfig,JSON.stringify({devices:{
    ...backends,windows:{...backends.windows,enabled:false}
  }}),{mode:0o600});
  renameSync(newConfig,config);
  const settled=await outcome;
  assert.equal(settled[0].status,'fulfilled',String(settled[0].reason));
  const response=settled[0].value;
  assert.equal(response.isError,true,text(response));
  assert.match(text(response),/aborted/i);
  assert.match(text(response),/inspect recovery journals/i);
  assert.match(readFileSync(marker,'utf8'),/^upload:success$/m,
    'SFTP server must have performed a verified, disposable upload before timing out');
  assert.equal(readFileSync(join(root,'disposable-uploaded.bin'),'utf8'),
    'DECK_COMMANDER_LOCAL_SFTP_TIMEOUT_FIXTURE');
  await until(()=>Object.values(pids).every(pid=>!inspect(pid)),1700);
  assert.equal(Object.keys(pids).length,3);
  console.log('GATEWAY_SFTP_REVOCATION_PASS: device disable aborted live transfer, verified actual local SFTP upload, all 3 process generations stopped');
 } finally{
  await client?.close().catch(()=>{});
  await transport?.close().catch(()=>{});
  for(const pid of Object.values({...states(marker),...pids})){
   // Match the exact test fixture process identity before emergency cleanup.
   if(!inspect(pid))continue;
   try{
    const cmdline=readFileSync(`/proc/${pid}/cmdline`,'utf8');
    if(cmdline.includes('timeout_sftp_router.py')||cmdline.includes('/usr/bin/sftp')||cmdline.includes('sftp-server'))process.kill(pid,'SIGKILL');
   }catch{}
  }
  rmSync(root,{recursive:true,force:true});
 }
});
