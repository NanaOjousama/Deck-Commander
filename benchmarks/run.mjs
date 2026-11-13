#!/usr/bin/env node
// Synthetic MCP-client benchmark. No user project reads, secrets or gateway edits.
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {performance} from 'node:perf_hooks';
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const sdk=process.env.DC_MCP_SDK_ROOT;
const moduleName = name => sdk ? pathToFileURL(join(sdk,'dist/esm',name)).href : '@modelcontextprotocol/sdk/'+name;
const [{Client},{StdioClientTransport}]=await Promise.all([
  import(moduleName('client/index.js')),import(moduleName('client/stdio.js'))
]);
const samples=Number((process.argv.find(x=>x.startsWith('--samples='))||'--samples=3').split('=')[1]);
if(!Number.isInteger(samples)||samples<1||samples>10)throw Error('samples must be 1 to 10');
const node=process.env.DC_NODE||process.execPath;
const gateway=process.env.DC_GATEWAY||join(project,'gateway.mjs');
const temp=mkdtempSync(join(tmpdir(),'dc-benchmark-'));
const client=new Client({name:'dc-efficiency-client',version:'1.0.0'},{capabilities:{}});
const transport=new StdioClientTransport({command:node,args:[gateway],stderr:'pipe'});
const quote=s=>"'"+String(s).replaceAll("'","'\\''")+"'";
const median=v=>percentile(v,.5);
const percentile=(v,p)=>{if(!v.length)return null;const a=[...v].sort((x,y)=>x-y),i=p*(a.length-1),l=Math.floor(i),h=Math.ceil(i);return Math.round((a[l]+(a[h]-a[l])*(i-l))*100)/100};
const cases=[];
let connected=false;
function fixtures(){
 const notes=join(temp,'notes');mkdirSync(notes);
 for(let i=0;i<8;i++){
  let lines=[];
  for(let j=0;j<24;j++)lines.push('NOTE_'+String(i).padStart(3,'0')+' LINE_'+String(j).padStart(3,'0')+' synthetic status OK');
  writeFileSync(join(notes,'note_'+String(i).padStart(3,'0')+'.txt'),lines.join('\n')+'\n');
 }
 const log=[];
 for(let i=0;i<600;i++)log.push('2026-01-01 INFO synthetic_event_'+String(i).padStart(4,'0'));
 log.push('LATEST_STATUS=HEALTHY_0600');
 writeFileSync(join(temp,'service.log'),log.join('\n')+'\n');
 for(const count of [100,1000]){
  const dir=join(temp,'tree'+count);mkdirSync(dir);
  for(let i=0;i<count;i++)writeFileSync(join(dir,'file_'+String(i).padStart(4,'0')+'.dat'),'DATA_'+i+'\n');
 }
}
function tracker(){
 const m={calls:0,text_bytes:0,protocol_bytes:0,errors:0,retries:0,empty_polls:0,duplicate_reads:0,call_elapsed_ms:0};
 const seen=new Set();
 return {m,async call(name,args){
   if(name==='read_file'){
    const k=JSON.stringify([args.path,args.offset||0,args.length||1000]);
    if(seen.has(k))m.duplicate_reads++;else seen.add(k);
   }
   const start=performance.now();m.calls++;
   let result;
   try{result=await client.callTool({name,arguments:args})}
   catch(e){m.errors++;throw Error('MCP transport '+name+': '+String(e.message||e))}
   finally{m.call_elapsed_ms+=performance.now()-start}
   if(result.isError)m.errors++;
   const out=(result.content||[]).filter(x=>x.type==='text').map(x=>x.text||'').join('\n');
   m.text_bytes+=Buffer.byteLength(out,'utf8');
   m.protocol_bytes+=Buffer.byteLength(JSON.stringify(result),'utf8');
   if(name==='read_process_output'&&/Reading 0 new lines|No output in requested range/i.test(out))m.empty_polls++;
   if(result.isError)throw Error('MCP result '+name+': '+out.slice(0,200));
   return out;
 }};
}
async function runCommand(t,cmd,wait){
 const initial=await t.call('start_process',{device:'steamdeck',command:cmd,timeout_ms:wait});
 if(/finished execution|Process completed with exit code/.test(initial))return initial;
 const pid=Number(initial.match(/Process started with PID (\d+)/)?.[1]);
 if(!Number.isSafeInteger(pid))throw Error('missing process PID');
 let all=initial;
 for(let i=0;i<20;i++){
  const x=await t.call('read_process_output',{device:'steamdeck',pid,timeout_ms:700});
  all+='\n'+x;
  if(/finished execution|Process completed with exit code/.test(x))return all;
  await new Promise(done=>setTimeout(done,100));
 }
 throw Error('process still unfinished after bounded polling');
}
async function test(name,variant,fn){
 const results=[];
 for(let i=0;i<samples;i++){
  const t=tracker();const start=performance.now();let ok=false,error=null;
  try{ok=Boolean(await fn(t));if(!ok)error='Correctness check returned false'}
  catch(e){error=String(e.message||e).slice(0,220)}
  const elapsed_ms=Math.round((performance.now()-start)*100)/100;
  results.push({task_run_id:[name,variant,'sample'+(i+1),Date.now().toString(36)].join(':'),...t.m,ok,error,elapsed_ms,approx_text_tokens:Math.ceil(t.m.text_bytes/4)});
  if(!ok)console.error('FAILED',name,variant,i+1,error);
 }
 const passed=results.filter(x=>x.ok),basis=passed.length?passed:results;
 const entry={scenario:name,variant,samples,passed:passed.length,all_pass:passed.length===samples,
  calls_p50:median(basis.map(x=>x.calls)),
  text_bytes_p50:median(basis.map(x=>x.text_bytes)),
  protocol_bytes_p50:median(basis.map(x=>x.protocol_bytes)),
  approx_text_tokens_p50:median(basis.map(x=>x.approx_text_tokens)),
  elapsed_ms_p50:median(basis.map(x=>x.elapsed_ms)),
  elapsed_ms_p95:percentile(basis.map(x=>x.elapsed_ms),.95),
  errors_total:results.reduce((n,x)=>n+x.errors,0),
  empty_polls_total:results.reduce((n,x)=>n+x.empty_polls,0),
  duplicate_reads_total:results.reduce((n,x)=>n+x.duplicate_reads,0),
  samples_detail:results
 };
 cases.push(entry);
 console.log('CASE',name,variant,JSON.stringify({passed:entry.passed,of:samples,calls:entry.calls_p50,bytes:entry.text_bytes_p50,ms:entry.elapsed_ms_p50}));
}
try{
 fixtures();await client.connect(transport);connected=true;
 const tools=new Set((await client.listTools()).tools.map(x=>x.name));
 for(const name of ['read_file','list_directory','start_process','read_process_output'])if(!tools.has(name))throw Error('Missing tool '+name);
 await runCommand(tracker(),'printf BENCH_WARMUP_OK',2500);
 await test('short_command','direct',async t=>(await runCommand(t,'printf SHORT_OK',2500)).includes('SHORT_OK'));
 // Explicit duplicate-read control, valid only for immutable synthetic fixtures.
 await test('repeat_unchanged_read','two_identical_reads',async t=>{
   const file=join(temp,'notes','note_000.txt');
   const first=await t.call('read_file',{device:'steamdeck',path:file});
   const second=await t.call('read_file',{device:'steamdeck',path:file});
   return first===second && first.includes('LINE_023');
 });
 await test('repeat_unchanged_read','one_read_known_immutable_fixture',async t=>{
   const file=join(temp,'notes','note_000.txt');
   return (await t.call('read_file',{device:'steamdeck',path:file})).includes('LINE_023');
 });
 await test('eight_file_review','individual_reads',async t=>{
  let good=0;
  for(let i=0;i<8;i++){
   const file=join(temp,'notes','note_'+String(i).padStart(3,'0')+'.txt');
   const x=await t.call('read_file',{device:'steamdeck',path:file});
   if(x.includes('NOTE_'+String(i).padStart(3,'0'))&&x.includes('LINE_023'))good++;
  }
  return good===8;
 });
 await test('eight_file_review','bounded_summary',async t=>{
  const py='from pathlib import Path;import sys;fs=list(Path(sys.argv[1]).glob("*.txt"));print("COUNT="+str(len(fs)));print("MATCHED="+str(sum("LINE_023" in f.read_text() for f in fs)))';
  const x=await runCommand(t,'python3 -c '+quote(py)+' '+quote(join(temp,'notes')),2500);
  return x.includes('COUNT=8')&&x.includes('MATCHED=8');
 });
 await test('latest_log_status','read_whole_log',async t=>(await t.call('read_file',{device:'steamdeck',path:join(temp,'service.log'),length:1000})).includes('LATEST_STATUS=HEALTHY_0600'));
 await test('latest_log_status','bounded_tail',async t=>(await runCommand(t,'tail -n 1 '+quote(join(temp,'service.log')),2500)).includes('LATEST_STATUS=HEALTHY_0600'));
 for(const count of [100,1000]){
  await test('tree_'+count,'list_directory',async t=>{
   const x=await t.call('list_directory',{device:'steamdeck',path:join(temp,'tree'+count),depth:1});
   return x.includes('file_0000.dat')&&x.includes('file_'+String(count-1).padStart(4,'0')+'.dat');
  });
  await test('tree_'+count,'bounded_count',async t=>{
   const py='import os,sys;xs=list(os.scandir(sys.argv[1]));print("FILES="+str(sum(x.is_file(follow_symlinks=False) for x in xs)))';
   const x=await runCommand(t,'python3 -c '+quote(py)+' '+quote(join(temp,'tree'+count)),2500);
   return x.includes('FILES='+count);
  });
 }
 const delay='python3 -u -c '+quote('import time;time.sleep(1.35);print("DELAY_DONE")');
 await test('delayed_process','short_wait_poll',async t=>(await runCommand(t,delay,700)).includes('DELAY_DONE'));
 await test('delayed_process','smart_wait',async t=>(await runCommand(t,delay,2500)).includes('DELAY_DONE'));
 const report={
  schema_version:1,generated_utc:new Date().toISOString(),sampling:String(samples)+' repetitions per variant; isolated fresh MCP client connected to a separate gateway child',run_id:'local-'+Date.now().toString(36),
  measurement_scope:'MCP client callTool only, synthetic fixtures on Steam Deck',
  measured:['direct MCP client calls','returned UTF-8 tool text bytes','serialized tool result bytes','elapsed time','explicit errors','empty process output polls','duplicate file reads'],
  estimated_tokens:'ceil(returned UTF-8 text bytes / 4), rough proxy, not a platform token count',
  unmeasured:['ChatGPT internal context tokens','assistant cognitive effort','gateway->backend MCP calls','SSH subprocess count','total user-facing overhead'],
  fixture:{notes:8,log_lines:601,trees:[100,1000],real_user_files:false},
  all_pass:cases.every(x=>x.all_pass),scenarios:cases,
 };
 const dir=join(project,'benchmarks','results');mkdirSync(dir,{recursive:true});
 const file=join(dir,'baseline-'+new Date().toISOString().slice(0,10)+'.json');
 writeFileSync(file,JSON.stringify(report,null,2)+'\n',{mode:0o600});
 console.log('RESULT_FILE',file);
 console.log('OVERALL_PASS',report.all_pass);
 if(!report.all_pass)process.exitCode=2;
}catch(e){console.error('BENCH_FATAL',String(e.stack||e));process.exitCode=3}
finally{if(connected)await client.close().catch(()=>{});rmSync(temp,{recursive:true,force:true})}