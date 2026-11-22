// Local gateway device gate. NOT client authentication: trusted MCP stdio
// access must be authenticated and revoked by the independent connector/tunnel.
import { closeSync, constants, fstatSync, openSync, readFileSync, watch } from 'node:fs';
import { basename, dirname } from 'node:path';

export const KNOWN_DEVICES = Object.freeze(['steamdeck', 'windows']);
export const TRANSFER_TOOL_NAMES = Object.freeze([
  'copy_file_between_devices',
  'copy_folder_between_devices',
  'copy_resumable_file_between_devices'
]);
const transferTools = new Set(TRANSFER_TOOL_NAMES);
const maxConfigurationBytes = 64 * 1024;

export class DeviceAccessDenied extends Error {
  constructor(reason) { super(reason); this.name = 'DeviceAccessDenied'; }
}

export function requireDeviceName(device) {
  // Using explicit string comparison prevents access to Object.prototype keys.
  if (typeof device !== 'string' || !KNOWN_DEVICES.includes(device))
    throw new DeviceAccessDenied('Invalid device selection');
  return device;
}

export function devicesForCall(name, args) {
  const value = args === undefined ? {} : args;
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new DeviceAccessDenied('Invalid tool arguments');
  if (name === 'list_devices') return [];
  if (transferTools.has(name)) {
    const source = requireDeviceName(value.source_device);
    const destination = requireDeviceName(value.destination_device);
    if (source === destination) throw new DeviceAccessDenied('Transfer needs two different devices');
    return [source, destination];
  }
  return [requireDeviceName(value.device === undefined ? 'steamdeck' : value.device)];
}

export function readDeviceState(configPath) {
  let fd;
  try {
    // O_NOFOLLOW + fstat on the opened descriptor prevents symlink swaps.
    fd = openSync(configPath, constants.O_RDONLY | constants.O_NOFOLLOW);
    const st = fstatSync(fd);
    if (!st.isFile() || st.size > maxConfigurationBytes || st.size < 2)
      throw new DeviceAccessDenied('Invalid device configuration file');
    if ((st.mode & 0o077) !== 0)
      throw new DeviceAccessDenied('Device configuration must be private (chmod 600)');
    if (typeof process.getuid === 'function' && st.uid !== process.getuid())
      throw new DeviceAccessDenied('Device configuration owner mismatch');
    const body = readFileSync(fd, {encoding:'utf8'});
    let parsed;
    try { parsed = JSON.parse(body); }
    catch { throw new DeviceAccessDenied('Invalid device configuration JSON'); }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) ||
        !Object.hasOwn(parsed, 'devices') || !parsed.devices ||
        typeof parsed.devices !== 'object' || Array.isArray(parsed.devices))
      throw new DeviceAccessDenied('Invalid device configuration structure');
    return parsed.devices;
  } catch (err) {
    if (err?.code === 'ENOENT') return null;
    if (err instanceof DeviceAccessDenied) throw err;
    throw new DeviceAccessDenied('Device configuration cannot be read securely');
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

export function assertDeviceEnabled(device, configPath, toolName = undefined, requireAllowlist = false) {
  requireDeviceName(device);
  const devices = readDeviceState(configPath);
  // Legacy single-device gateway may run without pairing. Windows must NEVER
  // work without an explicit current, private configuration enabling it.
  if (devices === null && device === 'steamdeck' && !requireAllowlist) return;
  const entry = devices && Object.hasOwn(devices, device) ? devices[device] : null;
  if (!entry || typeof entry !== 'object' || Array.isArray(entry) || entry.enabled !== true)
    throw new DeviceAccessDenied('Device ' + device + ' is disabled or unpaired');
  // Per-installation tool permissions (NOT per-client authorization). A tool
  // such as start_process can still execute arbitrary code with OS privileges.
  // Commercial/customer deployments should require explicit allowlists.
  if (toolName !== undefined) {
    if (typeof toolName !== 'string' || !toolName || toolName.length > 200)
      throw new DeviceAccessDenied('Invalid tool name');
    const allowed = entry.allowed_tools;
    if (allowed === undefined) {
      if (requireAllowlist) throw new DeviceAccessDenied('Tool permission allowlist is required');
    } else if (!Array.isArray(allowed) ||
               allowed.some(tool => typeof tool !== 'string' || !tool || tool.length > 200) ||
               allowed.length > 200 || !allowed.includes(toolName)) {
      throw new DeviceAccessDenied('Tool not authorized for device ' + device);
    }
  }
}

// Best-effort event notification for idle cached SSH sessions on atomic config
// rename. Every request must independently call assertDeviceEnabled because
// filesystem watches may be delayed, dropped or unavailable.
export function watchDeviceRevocation(configPath, onChange, onError = () => {}) {
  let watcher;
  try {
    watcher = watch(dirname(configPath), {persistent:false}, (_event, filename) => {
      if (filename === null || filename?.toString() === basename(configPath)) {
        Promise.resolve().then(onChange).catch(onError);
      }
    });
    watcher.on('error', onError);
    return watcher;
  } catch (error) { onError(error); return null; }
}
