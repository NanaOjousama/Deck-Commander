#!/usr/bin/env python3
"""Disposable local OpenAI tunnel-client HTTP auth forwarding acceptance.

Tests the REAL tunnel-client binary's `dev proxy` against a synthetic protected
HTTP MCP endpoint on 127.0.0.1. No real tunnels, accounts, keys or devices.
The fake tokens are generated in RAM and never logged or saved. This does NOT
validate OpenAI-hosted authorization or complete OAuth 2.1.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def probe(client_binary):
    if not Path(client_binary).is_file():
        raise RuntimeError('Operator-supplied tunnel-client executable absent')
    token_a, token_b, token_rotated = (secrets.token_urlsafe(22) for _ in range(3))
    accepted = {token_a}
    request_counts = {'allowed': 0, 'denied': 0, 'tool_effects': 0}
    lock = threading.Lock()

    class MCP(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No test secrets/paths/headers in logs.

        def do_GET(self):
            # OAuth discovery for a plain synthetic protected MCP server may
            # legitimately be absent: 404, not a retryable 501 error.
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 32768:
                self.send_error(413)
                return
            try:
                request = json.loads(self.rfile.read(size))
                method = request.get('method', '')
            except (ValueError, AttributeError, TypeError):
                self.send_error(400)
                return
            bearer = self.headers.get('Authorization', '')
            credential = bearer.removeprefix('Bearer ') if bearer.startswith('Bearer ') else ''
            with lock:
                authorized = credential in accepted
                if method != 'initialize':
                    request_counts['allowed' if authorized else 'denied'] += 1
                    if authorized and method == 'tools/call':
                        request_counts['tool_effects'] += 1
            if method != 'initialize' and not authorized:
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Bearer realm="disposable-fixture"')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if method == 'initialize':
                result = {'protocolVersion': request.get('params', {}).get('protocolVersion', '2025-06-18'),
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'synthetic-auth-fixture', 'version': '0.0.0'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': 'synthetic_ping', 'description': 'Disposable only',
                                     'inputSchema': {'type': 'object', 'properties': {}}}]}
            elif method == 'tools/call':
                result = {'content': [{'type': 'text', 'text': 'synthetic pong'}]}
            else:
                result = {}
            body = json.dumps({'jsonrpc': '2.0', 'id': request.get('id', 1),
                               'result': result}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with tempfile.TemporaryDirectory(prefix='dc-tunnel-auth-disposable-') as root:
        tmp = Path(root)
        server = ThreadingHTTPServer(('127.0.0.1', 0), MCP)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        backend = f'http://127.0.0.1:{server.server_address[1]}/mcp'
        metadata = tmp / 'local-proxy.json'
        # Empty HOME, XDG and environment prevent accidental live-profile and
        # API-key use. `dev proxy` runs its control plane in-process, locally.
        env = {'HOME': str(tmp), 'XDG_CONFIG_HOME': str(tmp), 'PATH': '/usr/bin:/bin'}
        command = [str(client_binary), 'dev', 'proxy', '--listen', '127.0.0.1:0',
                   '--mcp-server-url', backend, '--url-file', str(metadata),
                   '--duration', '25s', '--readiness-timeout', '12s']
        child = subprocess.Popen(command, cwd=root, env=env,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 13
            while time.monotonic() < deadline and not metadata.exists():
                if child.poll() is not None:
                    raise AssertionError('Isolated dev proxy exited before local readiness')
                time.sleep(.06)
            if not metadata.exists():
                raise AssertionError('Isolated dev proxy failed to become ready')
            endpoint = json.loads(metadata.read_text())['mcp_url']
            parsed = urlsplit(endpoint)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
                raise AssertionError('Refusing to contact a non-loopback dev proxy')

            def invoke(token, method):
                headers = {'Content-Type': 'application/json',
                           'Accept': 'application/json, text/event-stream'}
                if token is not None:
                    headers['Authorization'] = 'Bearer ' + token
                message = {'jsonrpc': '2.0', 'id': 1, 'method': method,
                           'params': {} if method != 'initialize' else {
                               'protocolVersion': '2025-06-18',
                               'clientInfo': {'name': 'synthetic-fixture', 'version': '0.0.0'},
                               'capabilities': {}}}
                request = Request(endpoint, data=json.dumps(message).encode(),
                                  headers=headers, method='POST')
                try:
                    with urlopen(request, timeout=5) as response:
                        return response.status, response.read(4096).decode(errors='replace')
                except HTTPError as err:
                    return err.code, err.read(4096).decode(errors='replace')

            results = {}

            def expect(label, token, method, expected_code, should_see_tools=False):
                code, body = invoke(token, method)
                if code != expected_code or ('synthetic_ping' in body) != should_see_tools:
                    raise AssertionError(f'{label}: unexpected status/tool visibility ({code})')
                results[label] = 'PASS'

            expect('A_initial_tools', token_a, 'tools/list', 200, True)
            expect('B_denied_before_discovery', token_b, 'tools/list', 401)
            expect('anonymous_denied_before_discovery', None, 'tools/list', 401)
            expect('A_initial_tool_call', token_a, 'tools/call', 200)
            before_revocation = request_counts['tool_effects']
            with lock:
                accepted.clear()
                accepted.add(token_rotated)
            expect('A_revoked_tools', token_a, 'tools/list', 401)
            expect('A_revoked_tool_call', token_a, 'tools/call', 401)
            expect('A_rotated_tools', token_rotated, 'tools/list', 200, True)
            expect('B_still_denied', token_b, 'tools/list', 401)
            if request_counts['tool_effects'] != before_revocation:
                raise AssertionError('Revoked client unexpectedly executed a protected tool')
            return {'status': 'PASS', 'transport': 'real_local_tunnel_client_dev_proxy',
                    'test_cases': len(results), 'denied_before_tool_dispatch': True,
                    'revoked_identity_rejected': True, 'rotated_identity_accepted': True,
                    'real_chatgpt_identity_validated': False,
                    'hosted_control_plane_validated': False}
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.communicate(timeout=4)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.communicate(timeout=4)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tunnel-client', required=True,
                        help='Explicit path to isolated or installed official tunnel-client binary')
    args = parser.parse_args()
    print(json.dumps(probe(args.tunnel_client), sort_keys=True))


if __name__ == '__main__':
    main()
