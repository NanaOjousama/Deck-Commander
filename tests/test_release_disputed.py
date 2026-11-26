"""Executable counterexamples for disputed review claims; no systemd calls."""
import ast
import multiprocessing as mp
import itertools
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
from service import release_transaction as rt

ROOT = Path(__file__).resolve().parents[1]

def unlocked_writer(root, old, target, barrier, results):
    original = rt.os.replace
    def replace(src, dst):
        barrier.wait(timeout=5)
        return original(src, dst)
    try:
        with mock.patch.object(rt.os, 'replace', side_effect=replace):
            result = rt._switch(root, old, dict(schema=1, revision=1, active=target, previous=None), verify_releases=False)
        results.put(('ok', result['active']))
    except Exception as exc:
        results.put(('error', repr(exc)))

def locked_writer(root, target, barrier, results):
    try:
        barrier.wait(timeout=5)
        result = rt.activate(root, target)
        results.put(('ok', result['revision']))
    except Exception as exc:
        results.put(('error', repr(exc)))

class DisputedClaims(unittest.TestCase):
    def test_cooperative_lock_preserves_both_updates(self):
        with tempfile.TemporaryDirectory(prefix='dc-release-sandbox-lock-control-') as directory:
            root = Path(directory)
            source = root / 'source'
            for name in rt.SOURCE_FILES:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            for name in ('r1', 'r2'):
                rt.stage(root, source, name)
            ctx = mp.get_context('fork')
            barrier, results = ctx.Barrier(2), ctx.Queue()
            workers = [ctx.Process(target=locked_writer, args=(root, target, barrier, results)) for target in ('r1','r2')]
            try:
                for worker in workers: worker.start()
                for worker in workers:
                    worker.join(8)
                    self.assertEqual(worker.exitcode, 0)
                outcomes = [results.get(timeout=2) for _ in workers]
                self.assertEqual(sorted(outcomes), [('ok',1),('ok',2)])
                final = rt.inspect(root)
                self.assertEqual(final['revision'], 2)
                self.assertEqual({final['active'],final['previous']},{'r1','r2'})
                print('CONTROL: cooperative flock preserved both updates; final revision=2', flush=True)
            finally:
                for worker in workers:
                    if worker.is_alive(): worker.terminate(); worker.join()

    def test_read_compare_replace_is_not_atomic_cas(self):
        with tempfile.TemporaryDirectory(prefix='dc-release-sandbox-cas-') as directory:
            root = Path(directory)
            old = rt._read_active(root, verify_releases=False)
            ctx = mp.get_context('fork')
            barrier, results = ctx.Barrier(2), ctx.Queue()
            workers = [ctx.Process(target=unlocked_writer, args=(root, old, target, barrier, results)) for target in ('r1','r2')]
            try:
                for worker in workers: worker.start()
                for worker in workers:
                    worker.join(8)
                    self.assertEqual(worker.exitcode, 0)
                outcomes = [results.get(timeout=2) for _ in workers]
                self.assertEqual(sorted(outcomes), [('ok','r1'),('ok','r2')])
                final = rt._read_active(root, verify_releases=False)
                self.assertEqual(final['revision'], 1)
                print('COUNTEREXAMPLE: both writers succeeded, final revision=1; one update lost', flush=True)
            finally:
                for worker in workers:
                    if worker.is_alive(): worker.terminate(); worker.join()

    def test_stale_same_version_health_rejected(self):
        source = (ROOT/'tests/service_upgrade_ci.py').read_text()
        tree = ast.parse(source)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'health')
        env = dict(time=types.SimpleNamespace(monotonic=lambda clock=itertools.count(): next(clock), sleep=lambda _: None),
                   status=lambda: 'active', read=lambda name: 'v2 old-invocation\n',
                   call=lambda *a, **k: types.SimpleNamespace(stdout='new-invocation\n' if 'InvocationID' in a else '12345\n'),
                   unit='fixture.service', Path=lambda _: types.SimpleNamespace(read_text=lambda: '0::/system.slice/fixture.service'))
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual health function>', 'exec'), env)
        # The previous v2 wrote the marker. The new PID has not initialized.
        self.assertFalse(env['health']('v2'))
        env['read'] = lambda name: 'v2 new-invocation\n'
        env['time'].monotonic = lambda clock=itertools.count(): next(clock)
        self.assertTrue(env['health']('v2'))

if __name__ == '__main__': unittest.main()
