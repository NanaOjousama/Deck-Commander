// Bounded Linux/POSIX transfer-process execution with no automatic command replay.
// Each command gets its own process group, so an SSH/SFTP grandchild can be
// terminated alongside its Python router parent. This does NOT kill remote
// detached commands or processes that deliberately create a new session.
import {spawn} from 'node:child_process';

function validatePositiveInteger(n, name, min=1, max=3600000) {
  if (!Number.isSafeInteger(n) || n<min || n>max) throw new RangeError('Invalid '+name);
}

function groupSignal(pgid, signal) {
  if (!Number.isSafeInteger(pgid)||pgid<=0) return;
  try {process.kill(-pgid,signal);}
  catch(error) {if(error.code!=='ESRCH') throw error;}
}
function groupExists(pgid) {
  try {process.kill(-pgid,0);return true;}
  catch(error) {if(error.code==='ESRCH') return false;throw error;}
}

export async function runIsolatedProcess(command,args,{timeoutMs=310000,
  graceMs=180,maxOutputBytes=20000,signal}={}) {
  if (process.platform !== 'linux') throw new Error('Isolated process group needs a Linux gateway');
  if (typeof command !== 'string' || !command.startsWith('/') || !Array.isArray(args) ||
      args.some(arg=>typeof arg!=='string')) throw new TypeError('Executable and args must be explicit');
  validatePositiveInteger(timeoutMs,'timeoutMs');
  validatePositiveInteger(graceMs,'graceMs',10,10000);
  validatePositiveInteger(maxOutputBytes,'maxOutputBytes',128,1024*1024);
  if (signal?.aborted) throw new Error('Transfer aborted before starting');
  return await new Promise((resolve,reject)=>{
    let child;
    try {child=spawn(command,args,{detached:true,stdio:['ignore','pipe','pipe']});}
    catch(error) {reject(error);return;}
    const pgid=child.pid;
    let stdout='',stderr='',reason=null,spawnError=null,closed=false;
    let escalationTimer=null;
    let timeoutTimer=null;
    const reasonError=()=> {
      if(reason==='timeout')return Error('Transfer timed out; remote commit may be uncertain; inspect recovery journals');
      if(reason==='abort')return Error('Transfer aborted; remote commit may be uncertain; inspect recovery journals');
      if(reason==='output')return Error('Transfer output limit exceeded; inspect recovery journals');
      return null;
    };
    const stop=(why)=>{
      if(reason===null)reason=why;
      if(!escalationTimer && pgid) {
        try {groupSignal(pgid,'SIGTERM');} catch(error) {spawnError ||= error;}
        escalationTimer=setTimeout(()=>{
          try {groupSignal(pgid,'SIGKILL');} catch(error){spawnError ||= error;}
        },graceMs);
        escalationTimer.unref();
      }
    };
    const output=(stream,b)=>{
      const decoded=b.toString('utf8');
      if(stream==='out')stdout+=decoded;
      else stderr+=decoded;
      if(Buffer.byteLength(stdout,'utf8')+Buffer.byteLength(stderr,'utf8')>maxOutputBytes){
        // Drop unbounded output immediately to avoid accumulating arbitrary data.
        stdout=stdout.slice(0,maxOutputBytes);
        stderr=stderr.slice(0,maxOutputBytes);
        stop('output');
      }
    };
    child.stdout?.on('data',b=>output('out',b));
    child.stderr?.on('data',b=>output('err',b));
    child.on('error',error=>{spawnError=error;stop('spawn');});
    const onAbort=()=>stop('abort');
    signal?.addEventListener('abort',onAbort,{once:true});
    timeoutTimer=setTimeout(()=>stop('timeout'),timeoutMs);
    child.on('exit',()=>{
      // Even a normally exiting router may leave SFTP grandchildren behind.
      // Do not treat backend process exit as permission for detached work.
      if(reason===null)stop('cleanup');
    });
    child.on('close',async(code,terminationSignal)=>{
      if(closed)return;closed=true;
      clearTimeout(timeoutTimer);
      signal?.removeEventListener('abort',onAbort);
      if(reason===null)stop('cleanup');
      try {
        // Give descendants a bounded TERM grace period, then SIGKILL any
        // remaining members of this isolated group before returning to caller.
        if(pgid && groupExists(pgid)) await new Promise(r=>setTimeout(r,graceMs));
        if(pgid)groupSignal(pgid,'SIGKILL');
      } catch(error) {spawnError ||= error;}
      if(escalationTimer)clearTimeout(escalationTimer);
      if(spawnError)reject(spawnError);
      else if(reasonError())reject(reasonError());
      else if(code!==0)reject(Error(stderr.trim()||'Transfer failed with exit '+code+' (signal '+(terminationSignal||'none')+')'));
      else resolve({stdout:stdout.trim(),stderr:stderr.trim(),code});
    });
  });
}
