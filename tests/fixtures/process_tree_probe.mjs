// Disposable test-only 3-level process tree; no network, no user files.
import {spawn} from 'node:child_process';
import {appendFileSync} from 'node:fs';
const [marker, role, mode] = process.argv.slice(2);
if (!marker || !['parent','child','grandchild'].includes(role)) throw Error('bad test fixture');
const write = (detail='') => appendFileSync(marker, `${role}:${process.pid}:${detail}\n`);
const ignoreTerm = mode === 'ignore-term';
if (ignoreTerm) process.on('SIGTERM', () => {});
if (role === 'parent' || role === 'child') {
  const next = role === 'parent' ? 'child' : 'grandchild';
  // Pipes are intentionally inherited: the gateway must not hang on close.
  const kid = spawn(process.execPath, [new URL(import.meta.url).pathname, marker, next, mode],
    {stdio:['ignore','inherit','inherit']});
  write(`spawned=${kid.pid}`);
  if (role === 'parent' && mode === 'parent-exits')
    setTimeout(() => process.exit(0), 250);
} else {
  write('ready');
  if (mode === 'overflow') {
    const data='X'.repeat(65536);
    setInterval(() => process.stdout.write(data), 25);
  }
}
setInterval(() => {},1000);
