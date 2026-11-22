// End-to-end stdio MCP test: real gateway, fake non-executing backends,
// temporary owner-private device policy; no SSH/network to any user machine.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {mkdtempSync,writeFileSync,readFileSync,renameSync,rmSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const repo=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const gateway=join(repo,'gateway.mjs');
const fixture=join(repo,'tests/fixtures/mock_mcp_backend.mjs');

function swapDevices(root,devices) {
  const temp=join(root,'devices.next');
  writeFileSync(temp,JSON.stringify({devices}),{mode:0o600});
  chmodSync(temp,0o600);
  renameSync(temp,join(root,'devices.json'));
}

function resultText(result) {return (result.content||[]).map(x=>x.text||'').join('\n');}

test('gateway never forwards disabled, revoked or prototype-device tool calls', {timeout:12000}, async t=>{
  const root=mkdtempSync(join(tmpdir(),'dc-gateway-runtime-'));
  const log=join(root,'backend_calls.txt');
  writeFileSync(log,'',{mode:0o600});
  const fakeSsh=join(root,'ssh-test-standin');
  const fakeDeck=join(root,'deck-test-standin');
  // Fake wrapper binaries supply synthetic-only log metadata. The MCP SDK
  // intentionally filters environment variables passed to subprocesses.
  const quote=val => '\''+String(val).replaceAll('\'','\'\\\'\'')+'\'';
  writeFileSync(fakeSsh,'#!/bin/sh\nexport DC_TEST_DEVICE=windows\nexport DC_TEST_BACKEND_LOG='+quote(log)+'\nexec '+quote(process.execPath)+' '+quote(fixture)+'\n',{mode:0o700});
  writeFileSync(fakeDeck,'#!/bin/sh\nexport DC_TEST_DEVICE=steamdeck\nexport DC_TEST_BACKEND_LOG='+quote(log)+'\nexec '+quote(process.execPath)+' "$@"\n',{mode:0o700});
  chmodSync(fakeSsh,0o700);chmodSync(fakeDeck,0o700);
  let client,transport;
  const isAlive = pid => {try {process.kill(pid,0);return true;}catch {return false;}};
  async function waitUntil(predicate,timeoutMs=1300) {
    const start=Date.now();
    while (!predicate() && Date.now()-start<timeoutMs)
      await new Promise(resolve=>setTimeout(resolve,30));
    assert(predicate(),'expected revocation event did not close cached backend');
  }
  const local={enabled:true,allowed_tools:['start_process','list_devices','copy_file_between_devices','copy_folder_between_devices','copy_resumable_file_between_devices']};
  const remote={enabled:true,allowed_tools:['start_process','list_devices']};
  try {
    swapDevices(root,{steamdeck:local,windows:{...remote,enabled:false}});
    client=new Client({name:'test-gateway-client',version:'0.0.0'},{capabilities:{}});
    transport=new StdioClientTransport({command:process.execPath,args:[gateway],env:{
      ...process.env,DC_HOME:root,DC_WINDOWS_HOSTNAME:'localhost',
      DC_STEAMDECK_NODE:fakeDeck,DC_STEAMDECK_ENTRYPOINT:fixture,
      DC_WINDOWS_SSH_BINARY:fakeSsh,DC_TEST_BACKEND_LOG:log,DC_REQUIRE_TOOL_ALLOWLIST:'1'
    },stderr:'pipe'});
    await client.connect(transport);
    const tools=await client.listTools();
    assert(tools.tools.some(x=>x.name==='start_process'));

    let res=await client.callTool({name:'start_process',arguments:{command:'not executed',device:'windows'}});
    assert.equal(res.isError,true); assert.match(resultText(res),/disabled or unpaired/);
    assert(!readFileSync(log,'utf8').includes('windows:'));

    for(const device of ['__proto__','constructor','toString',null,{},'Windows']) {
      res=await client.callTool({name:'start_process',arguments:{command:'never executed',device}});
      assert.equal(res.isError,true,JSON.stringify(device));
      assert.match(resultText(res),/Invalid device/);
    }
    res=await client.callTool({name:'start_process',arguments:{command:'not executed'}});
    assert.equal(res.isError,undefined);
    assert.match(resultText(res),/MOCK_steamdeck_start_process/);

    swapDevices(root,{steamdeck:local,windows:remote});
    res=await client.callTool({name:'start_process',arguments:{command:'not executed',device:'windows'}});
    assert.equal(res.isError,undefined,resultText(res));
    assert.match(resultText(res),/MOCK_windows_start_process/);
    assert.match(readFileSync(log,'utf8'),/windows:.*call:start_process/);

    // An enabled device can lose permission for one specific tool without
    // losing all its other permissions. Deny even if its backend is cached.
    swapDevices(root,{steamdeck:local,windows:{...remote,allowed_tools:['list_devices']}});
    res=await client.callTool({name:'start_process',arguments:{command:'not executed',device:'windows'}});
    assert.equal(res.isError,true);
    assert.match(resultText(res),/Tool not authorized/);
    swapDevices(root,{steamdeck:local,windows:remote});
    res=await client.callTool({name:'start_process',arguments:{command:'not executed',device:'windows'}});
    assert.equal(res.isError,undefined,resultText(res));
    assert.match(resultText(res),/MOCK_windows_start_process/);

    const priorLog=readFileSync(log,'utf8');
    const previousWindowsPids=[...priorLog.matchAll(/windows:(\d+):connected/g)];
    assert(previousWindowsPids.length>0,'fake Windows backend did not start');
    const activePid=Number(previousWindowsPids.at(-1)[1]);
    assert(isAlive(activePid),'the cached Windows backend exited prematurely');
    const prior=priorLog.split('windows:').length;
    swapDevices(root,{steamdeck:local,windows:{...remote,enabled:false}});
    // No further tool call: the gateway's fs.watch should drop the idle
    // cached Windows backend without relying on a future user request.
    await waitUntil(()=>!isAlive(activePid));
    res=await client.callTool({name:'start_process',arguments:{command:'not executed',device:'windows'}});
    assert.equal(res.isError,true);
    assert.match(resultText(res),/disabled or unpaired/);
    assert.equal(readFileSync(log,'utf8').split('windows:').length,prior,
      'revocation must block cached Windows backend dispatch');

    for(const name of ['copy_file_between_devices','copy_folder_between_devices','copy_resumable_file_between_devices']) {
      res=await client.callTool({name,arguments:{source_device:'steamdeck',source_path:'/tmp/test.bin',destination_device:'windows',destination_path:'C:/Test/test.bin'}});
      assert.equal(res.isError,true);
      assert.match(resultText(res),/disabled or unpaired/);
    }

    swapDevices(root,{steamdeck:{...local,enabled:false},windows:{...remote,enabled:false}});
    res=await client.callTool({name:'start_process',arguments:{command:'not executed'}});
    assert.equal(res.isError,true);
    assert.match(resultText(res),/disabled or unpaired/);
    assert.equal({}.polluted,undefined);
    // No transferred data or real commands executed by fixture.
    assert(!readFileSync(log,'utf8').includes('shell:'));
  } finally {
    await client?.close().catch(()=>{});
    await transport?.close().catch(()=>{});
    rmSync(root,{recursive:true,force:true});
  }
});
