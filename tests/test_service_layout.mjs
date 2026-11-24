import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolveLayout,checkStrictLayout} from '../service_layout.mjs';
import {mkdtempSync,mkdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
function withDirs(work){const root=mkdtempSync(join(tmpdir(),'dc-layout-'));try{
 const app=join(root,'app'),state=join(root,'state');mkdirSync(app);mkdirSync(state);
 return work({app,state});
 }finally{rmSync(root,{recursive:true,force:true});}}
const st=(owner,mode)=>({uid:owner,mode,isDirectory:()=>true,isSymbolicLink:()=>false});
test('legacy DC_HOME preserves source layout and original single-root behavior',()=>{
 assert.deepEqual(resolveLayout({DC_HOME:'/tmp/legacy'},'/opt/code'),{
  appRoot:'/tmp/legacy',stateRoot:'/tmp/legacy',strict:false});
 assert.equal(resolveLayout({},'/opt/code').appRoot,'/opt/code');
});
test('explicit app root separates immutable code and SSH/state paths',()=>{
 const x=resolveLayout({DC_APP_ROOT:'/opt/deck-commander',DC_HOME:'/var/lib/deck-commander'},'/other');
 assert.equal(x.appRoot,'/opt/deck-commander');assert.equal(x.stateRoot,'/var/lib/deck-commander');
});
test('strict mode requires an explicit allowlist and two absolute distinct paths',()=>withDirs(({app,state})=>{
 for(const env of [
  {DC_APP_ROOT:app,DC_HOME:state,DC_STRICT_DEPLOYMENT:'1'},
  {DC_APP_ROOT:app,DC_HOME:app,DC_STRICT_DEPLOYMENT:'1',DC_REQUIRE_TOOL_ALLOWLIST:'1'},
  {DC_APP_ROOT:'relative',DC_HOME:state,DC_STRICT_DEPLOYMENT:'1',DC_REQUIRE_TOOL_ALLOWLIST:'1'}
 ])assert.throws(()=>checkStrictLayout(resolveLayout(env,app),env),/requires|separate/);
}));
test('strict mode rejects service-owned code, writable code and public state',()=>withDirs(({app,state})=>{
 const env={DC_APP_ROOT:app,DC_HOME:state,DC_STRICT_DEPLOYMENT:'1',DC_REQUIRE_TOOL_ALLOWLIST:'1'};
 const layout=resolveLayout(env,app);
 let lstat=path=>path===app?st(1000,0o40755):st(1000,0o40700);
 assert.throws(()=>checkStrictLayout(layout,env,{uid:1000,lstat,realpath:p=>p}),/Application directory/);
 lstat=path=>path===app?st(0,0o40775):st(1000,0o40700);
 assert.throws(()=>checkStrictLayout(layout,env,{uid:1000,lstat,realpath:p=>p}),/Application directory/);
 lstat=path=>path===app?st(0,0o40755):st(1000,0o40750);
 assert.throws(()=>checkStrictLayout(layout,env,{uid:1000,lstat,realpath:p=>p}),/State directory/);
 lstat=path=>path===app?st(0,0o40755):st(1000,0o40700);
 assert.doesNotThrow(()=>checkStrictLayout(layout,env,{uid:1000,lstat,realpath:p=>p}));
}));
test('strict mode rejects symlink substitution of trusted app or state',()=>withDirs(({app,state})=>{
 const env={DC_APP_ROOT:app,DC_HOME:state,DC_STRICT_DEPLOYMENT:'1',DC_REQUIRE_TOOL_ALLOWLIST:'1'};
 const layout=resolveLayout(env,app);
 const lstat=path=>path===app?st(0,0o40755):st(1000,0o40700);
 assert.throws(()=>checkStrictLayout(layout,env,{uid:1000,lstat,realpath:p=>p===app?state:p}),/real directory/);
}));
