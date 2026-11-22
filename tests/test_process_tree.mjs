// Linux-only real process-group lifetime tests using disposable fixtures.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import {mkdtempSync,readFileSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const folder=resolve(dirname(fileURLToPath(import.meta.url)),'fixtures');
const fixture=join(folder,'process_tree_probe.mjs');
const moduleFile=resolve(dirname(fileURLToPath(import.meta.url)),'../process_tree.mjs');
let runIsolatedProcess;
try {({runIsolatedProcess}=await import(moduleFile));}
catch (error) {if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;}

function active(pid) {
  if(!Number.isInteger(pid)||pid<=0)return false;
  try {
    const status=readFileSync(`/proc/${pid}/status`,'utf8');
    const m=status.match(/^State:\s+([A-Z])/m);
    return m ? !['Z','X'].includes(m[1]) : false;
  } catch {return false;}
}
async function awaitCondition(fn,timeout=1600){
  const start=Date.now();
  while(Date.now()-start<timeout){if(fn())return;await new Promise(r=>setTimeout(r,25));}
  assert(fn(),'condition was not reached within bounded timeout');
}
function children(marker){
  if(!existsSync(marker))return {};
  const found={};
  for(const [_,role,pid] of readFileSync(marker,'utf8').matchAll(/^(parent|child|grandchild):(\d+):/gm))found[role]=Number(pid);
  return found;
}
const cases=[
  {title:'timeout kills parent child and grandchild (even TERM-ignoring children)',opts:{timeoutMs:850,graceMs:130},mode:'ignore-term',error:/timed out/i},
  {title:'abort signal cancels complete tree with no command replay',opts:{timeoutMs:2500,graceMs:130},mode:'ignore-term',abort:true,error:/aborted/i},
  {title:'oversized output cancels complete tree without buffering indefinitely',opts:{timeoutMs:2500,graceMs:130,maxOutputBytes:8192},mode:'overflow',error:/output limit/i}
];

for(const entry of cases) test(entry.title,{timeout:7500},async()=>{
  assert.equal(process.platform,'linux');
  const dir=mkdtempSync(join(tmpdir(),'dc-tree-test-'));
  const marker=join(dir,'pids.txt');
  const controller=new AbortController();
  let done;let pids={};
  try{
    assert.equal(typeof runIsolatedProcess,'function','missing process-group runner');
    const task=runIsolatedProcess(process.execPath,[fixture,marker,'parent',entry.mode],
      {...entry.opts,signal:controller.signal});
    await awaitCondition(()=>Object.keys(children(marker)).length===3,1200);
    pids=children(marker);
    for(const role of ['parent','child','grandchild'])assert(active(pids[role]),role+' never started');
    if(entry.abort)controller.abort();
    await assert.rejects(task,entry.error);
    done=true;
    await awaitCondition(()=>Object.values(pids).every(pid=>!active(pid)),1400);
  } finally {
    // Even on test assertion failures, never leave a disposable worker running.
    for(const pid of Object.values({...children(marker),...pids})){
      if(Number.isInteger(pid)&&pid>0)try{process.kill(pid,'SIGKILL');}catch{}
    }
    rmSync(dir,{recursive:true,force:true});
  }
  assert(done);
});

test('normal parent exit also stops descendants with inherited pipes', {timeout:6000}, async()=>{
  const dir=mkdtempSync(join(tmpdir(),'dc-tree-parent-exit-'));
  const marker=join(dir,'pids.txt');
  let pids={};
  try{
    const task=runIsolatedProcess(process.execPath,[fixture,marker,'parent','parent-exits'],
       {timeoutMs:2800,graceMs:150});
    await awaitCondition(()=>Object.keys(children(marker)).length===3,1000);
    pids=children(marker);
    const result=await task;
    assert.equal(result.code,0);
    await awaitCondition(()=>Object.values(pids).every(pid=>!active(pid)),1600);
  } finally {
    for(const pid of Object.values({...children(marker),...pids})){
      if(Number.isInteger(pid)&&pid>0)try{process.kill(pid,'SIGKILL');}catch{}
    }
    rmSync(dir,{recursive:true,force:true});
  }
});

test('cancelled group does not kill an unrelated process', {timeout:6500}, async()=>{
  const dir=mkdtempSync(join(tmpdir(),'dc-tree-unrelated-'));
  const marker=join(dir,'pids.txt');
  const unrelated=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
  let pids={};
  try {
    const task=runIsolatedProcess(process.execPath,[fixture,marker,'parent','ignore-term'],
       {timeoutMs:900,graceMs:150});
    await awaitCondition(()=>Object.keys(children(marker)).length===3,1200);
    pids=children(marker);
    await assert.rejects(task,/timed out/i);
    await awaitCondition(()=>Object.values(pids).every(pid=>!active(pid)),1200);
    assert(active(unrelated.pid),'unrelated process was mistakenly signalled');
  } finally {
    for(const pid of Object.values({...children(marker),...pids})){
      if(Number.isInteger(pid)&&pid>0)try{process.kill(pid,'SIGKILL');}catch{}
    }
    unrelated.kill('SIGKILL');
    rmSync(dir,{recursive:true,force:true});
  }
});

test('successful command returns stdout and does not relaunch on failure', {timeout:3500}, async()=>{
  assert.equal(typeof runIsolatedProcess,'function');
  const command = process.execPath;
  const run=()=>runIsolatedProcess(command,['-e','process.stdout.write("OK")'],{timeoutMs:800,graceMs:100});
  assert.equal((await run()).stdout,'OK');
  await assert.rejects(()=>runIsolatedProcess(command,['-e','process.exit(19)'],{timeoutMs:800,graceMs:100}),/exit 19/);
});
