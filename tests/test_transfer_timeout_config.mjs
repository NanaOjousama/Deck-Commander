import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawnSync} from 'node:child_process';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const gateway=resolve(dirname(fileURLToPath(import.meta.url)),'../gateway.mjs');
test('invalid timeout overrides fail before gateway starts MCP tools',{timeout:7000},()=>{
  for(const value of ['banana','310001','249','-1','Infinity','1e3']) {
    const r=spawnSync(process.execPath,[gateway],{
      env:{...process.env,DC_TRANSFER_TIMEOUT_MS:value},timeout:1800,encoding:'utf8'
    });
    assert.notEqual(r.status,0,value);
    assert.match(r.stderr,/Invalid DC_TRANSFER_TIMEOUT_MS|must be 250\.\.310000/,value);
  }
});
