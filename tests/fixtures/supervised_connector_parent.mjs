// CI-only stand-in for a third-party authenticated connector. No network/API.
// Runs as the systemd service main process, spawns the gateway test fixture as
// a child, and never uses the real user's tunnel credentials or configuration.
import {spawn} from 'node:child_process';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {writeFileSync} from 'node:fs';
import {resolveLayout,checkStrictLayout} from '../../service_layout.mjs';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const layout=checkStrictLayout(resolveLayout(process.env,app),process.env);
if(!layout.strict || !layout.stateRoot.startsWith('/var/lib/dc-supervised-ci-'))
  throw Error('Fixture refused non-disposable supervised service');
writeFileSync(join(layout.stateRoot,'connector-main-pid.txt'),String(process.pid)+'\n',
  {mode:0o600,flag:'wx'});
const gatewayFixture=join(app,'tests/fixtures/systemd_gateway_parent.mjs');
const child=spawn(process.execPath,[gatewayFixture,layout.stateRoot],{
  stdio:['ignore','ignore','inherit']
});
child.on('error',error=>{console.error('CI fixture child error',error.message);process.exit(52);});
child.on('exit',()=>process.exit(53));
setInterval(()=>{},1000);
