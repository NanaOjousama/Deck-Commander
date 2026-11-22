import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, writeFileSync, chmodSync, rmSync, symlinkSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { assertDeviceEnabled, devicesForCall, requireDeviceName, watchDeviceRevocation } from '../gateway_access.mjs';

function withConfig(testBody) {
  const dir = mkdtempSync(join(tmpdir(), 'dc-gateway-access-'));
  const path = join(dir, 'devices.json');
  const save = devices => writeFileSync(path, JSON.stringify({devices}), {mode:0o600});
  try { return testBody({dir, path, save}); }
  finally { rmSync(dir,{recursive:true,force:true}); }
}

const enabled = {steamdeck:{enabled:true}, windows:{enabled:true}};

test('Windows must be explicitly paired; local Steam Deck default is preserved', () => withConfig(({path}) => {
  assert.doesNotThrow(() => assertDeviceEnabled('steamdeck', path));
  assert.throws(() => assertDeviceEnabled('windows', path), /disabled or unpaired/);
}));

test('Windows revocation is evaluated on EVERY call without a stale cache', () => withConfig(({path,save}) => {
  save(enabled);
  for (let i=0;i<3;i++) assert.doesNotThrow(() => assertDeviceEnabled('windows',path));
  save({...enabled, windows:{enabled:false}});
  for (let i=0;i<3;i++) assert.throws(() => assertDeviceEnabled('windows',path), /disabled or unpaired/);
  save(enabled);
  assert.doesNotThrow(() => assertDeviceEnabled('windows',path));
}));

test('a device disabled via atomic rename is denied', () => withConfig(({dir,path,save}) => {
  save(enabled);
  const next = join(dir,'next');
  writeFileSync(next,JSON.stringify({devices:{...enabled,windows:{enabled:false}}}),{mode:0o600});
  renameSync(next,path);
  assert.throws(() => assertDeviceEnabled('windows',path), /disabled or unpaired/);
}));

test('private config is mandatory; malformed or missing Windows policy fails closed', () => withConfig(({path,save}) => {
  for (const bad of ['bad json','[]','{}','{"devices":[]}', '{"devices":{"windows":{"enabled":"true"}}}']) {
    writeFileSync(path,bad,{mode:0o600});
    assert.throws(() => assertDeviceEnabled('windows',path));
  }
  save(enabled);
  chmodSync(path,0o644);
  assert.throws(() => assertDeviceEnabled('windows',path),/private/);
  assert.throws(() => assertDeviceEnabled('steamdeck',path),/private/);
}));

test('symlink-based replacement of devices.json is rejected', () => withConfig(({dir,path,save}) => {
  const real=join(dir,'real.json');
  writeFileSync(real,JSON.stringify({devices:enabled}),{mode:0o600});
  symlinkSync(real,path);
  assert.throws(() => assertDeviceEnabled('windows',path));
}));

test('hostile prototype property names and malformed device selections rejected', () => withConfig(({path,save}) => {
  save(enabled);
  for (const device of ['__proto__','constructor','toString','Windows',null,0,{},['windows']]) {
    assert.throws(() => requireDeviceName(device),/Invalid device/);
    assert.throws(() => assertDeviceEnabled(device,path),/Invalid device/);
    assert.throws(() => devicesForCall('start_process',{device}),/Invalid device/);
  }
  assert.equal({}.polluted,undefined);
}));

test('all transfer types authorize BOTH endpoints, not caller-selected default', () => withConfig(({path,save}) => {
  save({...enabled,windows:{enabled:false}});
  for (const name of ['copy_file_between_devices','copy_folder_between_devices','copy_resumable_file_between_devices']) {
    const targets=devicesForCall(name,{source_device:'steamdeck',destination_device:'windows'});
    assert.deepEqual(targets,['steamdeck','windows']);
    assert.throws(() => targets.forEach(d=>assertDeviceEnabled(d,path)),/disabled or unpaired/);
    assert.deepEqual(devicesForCall(name,{source_device:'windows',destination_device:'steamdeck'}),['windows','steamdeck']);
  }
  assert.deepEqual(devicesForCall('start_process',{}),['steamdeck']);
  assert.deepEqual(devicesForCall('start_process',{device:'windows'}),['windows']);
}));

test('disabled Steam Deck blocks local tool access when explicitly configured', () => withConfig(({path,save}) => {
  save({...enabled,steamdeck:{enabled:false}});
  assert.throws(() => assertDeviceEnabled('steamdeck',path),/disabled or unpaired/);
  assert.doesNotThrow(() => assertDeviceEnabled('windows',path));
}));

test('unexpected argument shapes are denied', () => {
  for (const bad of [null,12,'windows',[],false])
    assert.throws(() => devicesForCall('start_process',bad),/Invalid tool arguments/);
  assert.throws(() => devicesForCall('copy_file_between_devices',{}),/Invalid device/);
  assert.throws(() => devicesForCall('copy_file_between_devices',{source_device:'steamdeck',destination_device:'steamdeck'}),/two different/);
});

test('per-device allowlist denies non-granted tools and prevents cross-device escalation', () => withConfig(({path,save}) => {
  save({steamdeck:{enabled:true,allowed_tools:['read_file']},
        windows:{enabled:true,allowed_tools:['read_file','list_devices']}});
  for (const device of ['steamdeck','windows']) {
    assert.doesNotThrow(()=>assertDeviceEnabled(device,path,'read_file',true));
    assert.throws(()=>assertDeviceEnabled(device,path,'start_process',true),/not authorized/);
    assert.throws(()=>assertDeviceEnabled(device,path,'copy_file_between_devices',true),/not authorized/);
  }
  assert.throws(()=>assertDeviceEnabled('steamdeck',path,'list_devices',true),/not authorized/);
}));

test('customer-style strict mode fails closed without a valid allowlist', () => withConfig(({path,save}) => {
  save(enabled);
  for (const device of ['steamdeck','windows']) {
    assert.throws(()=>assertDeviceEnabled(device,path,'start_process',true),/allowlist is required/);
  }
  save({steamdeck:{enabled:true,allowed_tools:['read_file',42]},
        windows:{enabled:true,allowed_tools:'start_process'}});
  for (const device of ['steamdeck','windows']) {
    assert.throws(()=>assertDeviceEnabled(device,path,'start_process',true),/not authorized/);
  }
}));

test('filesystem watcher notices atomic revocation; call checks remain mandatory', async () => {
  const dir=mkdtempSync(join(tmpdir(),'dc-gateway-watch-'));
  const path=join(dir,'devices.json');
  writeFileSync(path,JSON.stringify({devices:enabled}),{mode:0o600});
  let watcher;
  try {
    const changed=new Promise((resolve,reject) => {
      watcher=watchDeviceRevocation(path,resolve,reject);
      if (!watcher) reject(Error('No filesystem watcher available'));
    });
    const temp=join(dir,'next');
    writeFileSync(temp,JSON.stringify({devices:{...enabled,windows:{enabled:false}}}),{mode:0o600});
    renameSync(temp,path);
    await Promise.race([changed,new Promise((_,reject)=>setTimeout(()=>reject(Error('revocation watcher timeout')),1200))]);
    assert.throws(()=>assertDeviceEnabled('windows',path),/disabled or unpaired/);
  } finally {watcher?.close(); rmSync(dir,{recursive:true,force:true});}
});
