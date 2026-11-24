// Explicit code/state split for operator-controlled systemd deployments.
// Legacy DC_HOME-only personal installations are unchanged unless strict mode
// is enabled. Security checks never mutate or migrate existing files.
import {lstatSync, realpathSync} from 'node:fs';
import {homedir} from 'node:os';
import {isAbsolute, resolve} from 'node:path';

export function resolveLayout(env, sourceRoot) {
  const appRoot=env.DC_APP_ROOT || env.DC_HOME || sourceRoot;
  const stateRoot=env.DC_HOME || resolve(homedir(),'.local/share/deck-commander-multidevice');
  const strict=env.DC_STRICT_DEPLOYMENT==='1';
  return {appRoot,stateRoot,strict};
}

export function checkStrictLayout(layout, env, {uid=process.getuid?.(),lstat=lstatSync,realpath=realpathSync}={}) {
  if(!layout.strict)return layout;
  if(env.DC_REQUIRE_TOOL_ALLOWLIST!=='1')throw Error('Strict service requires DC_REQUIRE_TOOL_ALLOWLIST=1');
  if(typeof env.DC_APP_ROOT!=='string'||typeof env.DC_HOME!=='string'||
      !isAbsolute(env.DC_APP_ROOT)||!isAbsolute(env.DC_HOME))
    throw Error('Strict service requires absolute DC_APP_ROOT and DC_HOME');
  if(resolve(layout.appRoot)===resolve(layout.stateRoot))
    throw Error('Application code and private state must use separate directories');
  for(const [kind,path] of [['application',layout.appRoot],['state',layout.stateRoot]]){
    const info=lstat(path);
    if(!info.isDirectory()||info.isSymbolicLink()||realpath(path)!==resolve(path))
      throw Error('Strict '+kind+' directory is not a real directory');
    if(kind==='application'){
      if(uid===undefined||info.uid===uid||(info.mode&0o022))
        throw Error('Application directory must be owned by another account and not group/other writable');
    }else if(uid===undefined||info.uid!==uid||(info.mode&0o077)){
      throw Error('State directory must be owned by service account and private');
    }
  }
  // A private directory alone is not enough if one code file is service-
  // writable. Require root-owned, immutable entrypoints and routing modules.
  for(const name of ['gateway.mjs','gateway_access.mjs','service_layout.mjs',
    'process_tree.mjs','transfer_registry.mjs','router.py','package.json']) {
    const path=resolve(layout.appRoot,name);
    const info=lstat(path);
    if(!info.isFile() || info.isSymbolicLink() || info.uid!==0 ||
       (info.mode&0o022) || realpath(path)!==path)
      throw Error('Strict application source must be root-owned and read-only: '+name);
  }
  return layout;
}
