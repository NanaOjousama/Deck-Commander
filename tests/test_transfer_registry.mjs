import assert from 'node:assert/strict';
import {test} from 'node:test';
import {TransferRegistry} from '../transfer_registry.mjs';
import {runIsolatedProcess} from '../process_tree.mjs';
import {mkdtempSync,rmSync,existsSync,readFileSync} from 'node:fs';
import {join,dirname,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
const probe=resolve(dirname(fileURLToPath(import.meta.url)),'fixtures/process_tree_probe.mjs');
const delay=ms=>new Promise(r=>setTimeout(r,ms));

function active(pid){
  try{const s=readFileSync(`/proc/${pid}/status`,'utf8');return !/State:\s+[ZX]/.test(s);}
  catch{return false;}
}
function readPids(marker){
  if(!existsSync(marker))return {};
  const out={};for(const m of readFileSync(marker,'utf8').matchAll(/^(parent|child|grandchild):(\d+):/gm))out[m[1]]=+m[2];return out;
}
async function waitFor(fn,timeout=1800){const until=Date.now()+timeout;while(Date.now()<until){if(fn())return;await delay(25);}assert(fn(),'timed out waiting for process state');}

for(const by of ['windows','steamdeck']) test('revoking '+by+' cancels tracked process tree, not another device\'s task',{timeout:6500},async()=>{
  const dir=mkdtempSync(join(tmpdir(),'dc-revoke-process-'));
  const marker=join(dir,'pids.txt');
  const registry=new TransferRegistry();
  let pids={};
  try{
    const task=registry.run([by],'copy_file_between_devices',signal=>runIsolatedProcess(process.execPath,
      [probe,marker,'parent','ignore-term'],{timeoutMs:2800,graceMs:150,signal}));
    await waitFor(()=>Object.keys(readPids(marker)).length===3);
    pids=readPids(marker);
    assert.equal(registry.activeCount,1);
    assert.equal(registry.abortDevice(by==='windows'?'steamdeck':'windows'),0);
    assert.equal(registry.activeCount,1);
    assert.equal(registry.abortDevice(by),1);
    await assert.rejects(task,/aborted/i);
    await waitFor(()=>Object.values(pids).every(pid=>!active(pid)),1700);
    assert.equal(registry.activeCount,0);
  } finally {
    registry.abortAll();await registry.drain();
    for(const pid of Object.values({...readPids(marker),...pids}))try{process.kill(pid,'SIGKILL');}catch{}
    rmSync(dir,{recursive:true,force:true});
  }
});

test('gateway shutdown aborts and drains all registered operations',{timeout:6800},async()=>{
  const dir=mkdtempSync(join(tmpdir(),'dc-stop-process-'));
  const markers=['a','b'].map(n=>join(dir,n));
  const registry=new TransferRegistry();let tasks=[];
  try{
    tasks=markers.map((mark,i)=>registry.run([i?'windows':'steamdeck'],'copy_folder_between_devices',signal=>
      runIsolatedProcess(process.execPath,[probe,mark,'parent','ignore-term'],
        {timeoutMs:3100,graceMs:180,signal})));
    // Attach rejection handlers before cancelling either running task.
    const outcomesPromise=Promise.allSettled(tasks);
    await waitFor(()=>markers.every(mark=>Object.keys(readPids(mark)).length===3),2000);
    assert.equal(registry.activeCount,2);
    assert.equal(registry.abortAll(),2);
    await registry.drain();
    const outcomes=await outcomesPromise;
    assert(outcomes.every(x=>x.status==='rejected' && /aborted/i.test(x.reason.message)));
    assert.equal(registry.activeCount,0);
    await waitFor(()=>markers.flatMap(m=>Object.values(readPids(m))).every(pid=>!active(pid)),1600);
  } finally {
    registry.abortAll();await registry.drain();
    for(const mark of markers)for(const pid of Object.values(readPids(mark)))try{process.kill(pid,'SIGKILL');}catch{}
    rmSync(dir,{recursive:true,force:true});
  }
});


test('permission rejection of another tool does not cancel an authorized transfer', {timeout:6000}, async()=>{
  const dir=mkdtempSync(join(tmpdir(),'dc-permission-reconcile-'));
  const marker=join(dir,'pids.txt');
  const registry=new TransferRegistry();
  let pids={};
  try {
    const task=registry.run(['windows'],'copy_file_between_devices',signal=>
      runIsolatedProcess(process.execPath,[probe,marker,'parent','ignore-term'],
        {timeoutMs:2600,graceMs:150,signal}));
    const settled=Promise.allSettled([task]);
    await waitFor(()=>Object.keys(readPids(marker)).length===3,1500);
    pids=readPids(marker);
    assert.equal(registry.reconcile((device,tool)=>{
      if(device==='windows' && tool!=='copy_file_between_devices')throw Error('not allowed');
    }),0);
    assert(Object.values(pids).every(active),'authorized transfer ended unexpectedly');
    assert.equal(registry.reconcile((device,tool)=>{
      if(device==='windows' && tool==='copy_file_between_devices')throw Error('revoked');
    }),1);
    const outcomes=await settled;
    assert.equal(outcomes[0].status,'rejected');
    assert.match(outcomes[0].reason.message,/aborted/i);
    await waitFor(()=>Object.values(pids).every(pid=>!active(pid)),1500);
  } finally {
    registry.abortAll();await registry.drain();
    for(const pid of Object.values({...readPids(marker),...pids}))try{process.kill(pid,'SIGKILL');}catch{}
    rmSync(dir,{recursive:true,force:true});
  }
});
