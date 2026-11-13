#!/usr/bin/env node
// MCP gateway: 25 identical Desktop Commander tools on two named computers.
// The pre-existing Steam Deck behaviors remain the default, and the Windows backend
// requires an explicit device:"windows" selection. Separate, hash-verified transfer.
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { spawn } from 'node:child_process';
import { isIP } from 'node:net';
import { networkInterfaces } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = process.env.DC_HOME || dirname(fileURLToPath(import.meta.url));
const router = resolve(root, 'router.py');
const windowsCmd = process.env.DC_WINDOWS_LAUNCHER || 'C:\\DeckCommander\\start-dc.cmd';
const backends = {
  steamdeck: {
    command: process.env.DC_STEAMDECK_NODE || process.execPath,
    args: [process.env.DC_STEAMDECK_ENTRYPOINT || resolve(root, 'node_modules/@wonderwhy-er/desktop-commander/dist/index.js'), '--no-onboarding'],
    client: null, connecting: null, transport: null, retryAfter: 0
  },
  windows: {
    command: '/usr/bin/ssh',
    args: ['-F', root + '/ssh_config', '-T', 'dc-windows', 'cmd.exe', '/d', '/c', windowsCmd],
    client: null, connecting: null, transport: null, retryAfter: 0
  }
};
const deviceNames = Object.keys(backends);
const server = new Server({name:'deck-commander-multidevice',version:'0.1.0'}, {capabilities:{tools:{}}});
let toolCache;
function log(...msg) { console.error('[multi-device]', ...msg); }

// A powered-off Windows desktop should not generate repeated SSH connection
// attempts. Retry on the next user request once this short cooldown expires.
// There is deliberately no background polling and no command replay.
const windowsOfflineRetryMs = 10000;
// A cold/offline Windows machine takes ~10 seconds to fail in libc name lookup.
// Use the SAME local IPv4 name resolver that SSH uses, but give it a bounded
// wait. On success, SSH still checks the configured pinned host key.
const windowsDnsTimeoutMs = 3500;
const windowsLocalName = process.env.DC_WINDOWS_HOSTNAME || ''; // e.g. windows-pc.local
// Last known permitted source IPv4 from the original Windows SSH firewall
// rule. A warning is advisory only: remote firewall may have changed.
const pairedDeckIPv4 = process.env.DC_DECK_FIREWALL_ALLOWED_IPV4 || ''; // optional advisory only
const deckWifiInterface = process.env.DC_DECK_NETWORK_INTERFACE || 'wlan0';
function deckNetworkHint() {
  if (!pairedDeckIPv4) return ''; // No last-known firewall source IP was configured.
  try {
    const ip = (networkInterfaces()[deckWifiInterface] || [])
      .find(x => x.family === 'IPv4' && !x.internal)?.address;
    if (!ip) return 'Steam Deck Wi-Fi has no IPv4 address; Windows LAN access may fail.';
    if (ip === pairedDeckIPv4) return '';
    return 'Steam Deck Wi-Fi IP is now ' + ip + ' (Windows SSH firewall last confirmed for ' + pairedDeckIPv4 + '). ' +
      'If Windows is unreachable, check the router DHCP reservation or its SSH firewall source-IP rule.';
  } catch (err) {
    return 'Steam Deck Wi-Fi address could not be checked.';
  }
}

function resolveWindowsForSsh() {
  if (!windowsLocalName) return Promise.reject(Error('Set DC_WINDOWS_HOSTNAME to the paired Windows LAN hostname'));
  return new Promise((resolve,reject) => {
    const child = spawn('/usr/bin/getent', ['ahostsv4', windowsLocalName], {stdio:['ignore','pipe','ignore']});
    let output = '', settled = false;
    const finish = (error, address) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(address);
    };
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(Error('Windows hostname not found on LAN within 3.5 seconds'));
    }, windowsDnsTimeoutMs);
    child.stdout.on('data',data => {output += data; if(output.length > 1024) child.kill('SIGKILL');});
    child.on('error',error => finish(error));
    child.on('exit',code => {
      const address = output.trim().split(/\s+/)[0];
      if (code === 0 && isIP(address) === 4) finish(null,address);
      else finish(Error('Windows local hostname could not be resolved'));
    });
  });
}

function markOffline(device, client, transport, retryMs = 0) {
  const b = backends[device];
  if (b.client === client) b.client = null;
  if (b.transport === transport) b.transport = null;
  if (device === 'windows' && retryMs > 0)
    b.retryAfter = Math.max(b.retryAfter, Date.now() + retryMs);
}
async function dropBackend(device, client, retryMs = 0) {
  const b = backends[device];
  if (b.client !== client) return;
  markOffline(device, client, b.transport, retryMs);
  await client.close().catch(() => {});
}
async function getBackend(device) {
  const b = backends[device];
  if (!b) throw new Error('Unknown device: ' + device);
  if (b.client) return b.client;
  if (b.connecting) return b.connecting;
  if (device === 'windows' && Date.now() < b.retryAfter) {
    const seconds = Math.ceil((b.retryAfter - Date.now()) / 1000);
    throw new Error('Windows desktop unavailable; retry in about ' + seconds + 's after it is powered on');
  }
  const pending = (async () => {
    // Resolve once for the SSH session, avoiding a second slow DNS lookup.
    // HostKeyAlias in ssh_config still pins the approved Windows host key.
    let backendArgs = b.args;
    if (device === 'windows') {
      let address;
      try { address = await resolveWindowsForSsh(); }
      catch (error) {
        b.retryAfter = Date.now() + windowsOfflineRetryMs;
        throw error;
      }
      backendArgs = ['-F', root + '/ssh_config', '-T', '-o', 'HostName=' + address,
        'dc-windows', 'cmd.exe', '/d', '/c', windowsCmd];
    }
    const client = new Client({name:'deck-commander-router-' + device, version:'0.1.0'}, {capabilities:{}});
    const transport = new StdioClientTransport({command:b.command,args:backendArgs,stderr:'pipe'});
    b.transport = transport;
    client.onclose = () => {
      if (b.client === client || b.transport === transport) {
        markOffline(device, client, transport, device === 'windows' ? 2500 : 0);
        log(device, 'backend transport closed; ready to reconnect');
      }
    };
    try {
      await client.connect(transport);
      b.client = client;
      b.retryAfter = 0;
      log(device, 'backend connected');
      return client;
    } catch (e) {
      log(device, 'backend connection failed:', e.message || String(e));
      markOffline(device, client, transport, device === 'windows' ? windowsOfflineRetryMs : 0);
      await transport.close().catch(() => {});
      throw e;
    }
  })();
  b.connecting = pending;
  try { return await pending; }
  finally { if (b.connecting === pending) b.connecting = null; }
}

const deviceParameter = {
  type:'string', enum:deviceNames, default:'steamdeck',
  description:'Target computer. Use steamdeck (default) or windows. Explicitly select windows for Windows files or terminal commands.'
};
async function exposedTools() {
  if (toolCache) return toolCache;
  const client = await getBackend('steamdeck');
  const result = await client.listTools();
  toolCache = result.tools.map(t => {
    const properties = {...(t.inputSchema?.properties || {}), device:deviceParameter};
    return {
      ...t,
      description: (t.description || '') + '\n\nMulti-device: this same command can run on steamdeck or windows. Supply device:"windows" to target the Windows desktop; omit device to use the Steam Deck.',
      inputSchema: {...t.inputSchema, type:'object', properties}
    };
  });
  toolCache.push({
    name:'list_devices',
    description:'Show the two configured computers and check each Desktop Commander backend. No changes are made.',
    inputSchema:{type:'object',properties:{}}
  });
  toolCache.push({
    name:'copy_file_between_devices',
    description:'Copy one file between Steam Deck and Windows, refusing overwrites and verifying SHA-256 on both sides. Specify explicit source and destination device and absolute file paths.',
    inputSchema:{
      type:'object',
      properties:{
        source_device:{type:'string',enum:deviceNames},
        source_path:{type:'string',description:'Absolute path of existing source file on source_device'},
        destination_device:{type:'string',enum:deviceNames},
        destination_path:{type:'string',description:'Absolute path of new destination file, using Windows C:/Users/... syntax when needed'}
      },
      required:['source_device','source_path','destination_device','destination_path']
    }
  });
  toolCache.push({
    name:'copy_folder_between_devices',
    description:'Copy a directory tree between the Steam Deck and Windows without overwriting existing files. After an interrupted operation, set resume:true with the same paths: existing files are SHA-256 verified and skipped, unfinished files are copied again. This is per-file resume, not byte-level resume. Up to 2000 files / 20 GiB per operation.',
    inputSchema:{type:'object',properties:{
      source_device:{type:'string',enum:deviceNames},
      source_path:{type:'string',description:'Absolute path of source directory on source_device'},
      destination_device:{type:'string',enum:deviceNames},
      destination_path:{type:'string',description:'Absolute path of destination directory, which must not already exist for a fresh operation'},
      resume:{type:'boolean',default:false,description:'Set true only to continue an interrupted operation with identical paths and source tree'}
    },required:['source_device','source_path','destination_device','destination_path']}
  });
  toolCache.push({
    name:'copy_resumable_file_between_devices',
    description:'Copy a single large file from Steam Deck to Windows or vice versa, with explicit byte-level continuation of an interrupted job. On a new copy omit resume or set false; after interruption retry the identical paths with resume:true. SHA-256 verification, source-prefix checking and no-overwrite commits. Never automatically replays commands.',
    inputSchema:{type:'object',properties:{
      source_device:{type:'string',enum:deviceNames},
      source_path:{type:'string',description:'Absolute source file path on source_device'},
      destination_device:{type:'string',enum:deviceNames},
      destination_path:{type:'string',description:'Absolute new destination file path'},
      resume:{type:'boolean',default:false,description:'True only to continue a previously interrupted matching resumable-file transfer'}
    },required:['source_device','source_path','destination_device','destination_path']}
  });
  return toolCache;
}

async function invokeRouter(args, folder=false, resumable=false) {
  return await new Promise((resolve,reject) => {
    const action = resumable ? 'resumable-copy' : (folder ? 'folder-copy' : 'copy');
    const cliArgs = [router,action,
      '--from-device',args.source_device,'--source',args.source_path,
      '--to-device',args.destination_device,'--destination',args.destination_path];
    if((folder || resumable) && args.resume===true) cliArgs.push('--resume');
    const c = spawn('/usr/bin/python3', cliArgs,{stdio:['ignore','pipe','pipe']});
    let stdout='',stderr='';
    let settled=false;
    const timer=setTimeout(() => {c.kill('SIGKILL');fail(Error('Copy timed out after 5 minutes'));},310000);
    function fail(err){if(settled)return;settled=true;clearTimeout(timer);reject(err);}
    c.stdout.on('data',b=>{stdout+=b; if(stdout.length>20000)c.kill();});
    c.stderr.on('data',b=>{stderr+=b; if(stderr.length>20000)c.kill();});
    c.on('error',fail);
    c.on('exit',code => {if(settled)return;clearTimeout(timer);settled=true;
      if(code!==0) reject(Error(stderr.trim() || 'Transfer failed with exit '+code));
      else resolve(stdout.trim());
    });
  });
}
server.setRequestHandler(ListToolsRequestSchema, async () => ({tools:await exposedTools()}));
server.setRequestHandler(CallToolRequestSchema, async request => {
  const name = request.params.name;
  try {
    if (name === 'list_devices') {
      const entries=[];
      for (const d of deviceNames) {
        try {
          const cl=await getBackend(d);
          entries.push(d + ': connected ('+(await cl.listTools()).tools.length+' tools)');
        } catch (e) {
          const cached = backends[d].client;
          if (cached) await dropBackend(d, cached, d === 'windows' ? 2500 : 0);
          entries.push(d + ': unavailable ('+(e.message || String(e))+')');
        }
      }
      const hint = deckNetworkHint();
      if (hint) entries.push('Network notice: ' + hint);
      return {content:[{type:'text',text:entries.join('\n')}]};
    }
    if(name === 'copy_file_between_devices' || name === 'copy_folder_between_devices' || name === 'copy_resumable_file_between_devices') {
      const a=request.params.arguments || {};
      const folder = name === 'copy_folder_between_devices';
      const resumable = name === 'copy_resumable_file_between_devices';
      if ((folder || resumable) && a.resume !== undefined && typeof a.resume !== 'boolean')
        throw new Error('resume must be a boolean');
      if (!folder && !resumable && a.resume !== undefined)
        throw new Error('Standard single-file copy does not support resume; use copy_resumable_file_between_devices');
      for (const k of ['source_device','source_path','destination_device','destination_path']) {
        if(typeof a[k] !== 'string' || a[k].length===0) throw new Error('Missing '+k);
      }
      if(!deviceNames.includes(a.source_device) || !deviceNames.includes(a.destination_device))
        throw new Error('Only paired steamdeck and windows are supported');
      // Check reachability before launching an expensive SSH/SFTP transfer.
      // Probe is read-only; the transfer itself still runs at most once.
      if (a.source_device === 'windows' || a.destination_device === 'windows') {
        try {
          const remote = await getBackend('windows');
          await remote.listTools();
        } catch (e) {
          const cached = backends.windows.client;
          if (cached) await dropBackend('windows', cached, 2500);
          throw new Error('Cannot transfer: Windows desktop unavailable (' + (e.message || String(e)) + ')');
        }
      }
      return {content:[{type:'text',text:await invokeRouter(a, folder, resumable)}]};
    }
    const known = (await exposedTools()).some(t=>t.name===name);
    if(!known) throw new Error('Unknown tool: '+name);
    const args={...(request.params.arguments||{})};
    const device=args.device || 'steamdeck';
    delete args.device;
    // Reduce unproductive chat-side polls without repeating any command.
    // This only adjusts a *wait window*: the process runs once, and
    // an interactive/long-running task can still return its PID.
    const isInteractive = cmd => /(?:^|[;&|]\s*)(?:watch\b|tail\b[^;\n]*\s-f\b|(?:python(?:3)?|node|bash|zsh|sh)\s+-i\b|ipython\b|jupyter\b|ssh\b[^;\n]*\s-N\b|npm\s+(?:run\s+dev|start)\b|(?:uvicorn|gunicorn)\b|(?:top|htop|less|more|vim|nano)\b)/i.test(String(cmd||''));
    if (name === 'start_process'
        && Number.isFinite(args.timeout_ms)
        && args.timeout_ms >= 1800 && args.timeout_ms <= 5000
        && !args.verbose_timing && !isInteractive(args.command)) {
      // Older workflows requested 2-3 seconds and then polled repeatedly.
      // A capped 10-second wait completes many routine one-shot commands.
      args.timeout_ms = 10000;
    } else if (name === 'read_process_output'
        && Number.isFinite(args.timeout_ms)
        && args.timeout_ms >= 1800 && args.timeout_ms <= 3500
        && (args.offset === undefined || args.offset === 0)
        && !args.verbose_timing) {
      // A single wait for additional output replaces repeated empty polls.
      // Non-zero offsets and explicit longer waits keep original semantics.
      args.timeout_ms = 10000;
    }
    const client=await getBackend(device);
    try {
      return await client.callTool({name,arguments:args});
    } catch (err) {
      // Never replay a command that may have had side effects. Clear stale
      // transports so a later explicit request reconnects safely instead.
      await dropBackend(device, client, device === 'windows' ? 2500 : 0);
      throw err;
    }
  } catch(e) {
    log('tool error',name, e.message || String(e));
    const targetsWindows = request.params.arguments?.device === 'windows' ||
      ((name === 'copy_file_between_devices' || name === 'copy_folder_between_devices' || name === 'copy_resumable_file_between_devices') &&
       (request.params.arguments?.source_device === 'windows' || request.params.arguments?.destination_device === 'windows'));
    const hint = targetsWindows ? deckNetworkHint() : '';
    return {isError:true,content:[{type:'text',text:'Multi-device command failed: '+
      (e.message||String(e)) + (hint ? '\nNetwork notice: '+hint : '')}]};
  }
});
process.on('SIGTERM',async()=>{
  for (const d of deviceNames) await backends[d].client?.close().catch(()=>{});
  process.exit(0);
});
await server.connect(new StdioServerTransport());
log('gateway initialized; Steam Deck default, Windows explicit');