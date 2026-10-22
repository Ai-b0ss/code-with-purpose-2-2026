"""Real loopback HTTP + process restart fault probe; provider is a controlled stub.

No external model, credentials or Google/VK calls. Never count this as model quality.
Run: python tests/real_http_recovery.py
"""
import concurrent.futures
from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_smoke import applicant
from test_evaluator import response


def main():
    data = applicant(application_id='synthetic-fault-probe').model_dump()
    good = json.dumps(response(applicant()), ensure_ascii=False)
    state = {'mode': 'ok', 'calls': 0, 'trap': 0, 'sent': []}
    entered = threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path == '/trap':
                state['trap'] += 1
            state['calls'] += 1
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['sent'].append(json.loads(body['messages'][1]['content'])['answers'])
            mode = state['mode']
            entered.set()
            if mode in ('slow', 'concurrent'):
                time.sleep(2 if mode == 'slow' else .2)
            if mode in ('401', '429', '503'):
                status, result = int(mode), {'error': 'SYNTHETIC_SECRET_PROVIDER_BODY'}
            elif mode == 'redirect':
                self.send_response(307)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/trap')
                self.end_headers()
                return
            elif mode == 'bad_envelope':
                status, result = 200, {'choices': []}
            else:
                status, result = 200, {'choices': [{'finish_reason': 'stop', 'message': {
                    'content': '{broken' if mode == 'bad_json' else good}}]}
            raw = json.dumps(result).encode()
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    provider.daemon_threads = True
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    token = 'synthetic-fault-token-more-than-32-characters'
    process = None
    checks = []

    def call(route, payload=None, auth=False):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(base + route,
            data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            result = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as exc:
            result = exc
        with result:
            raw = result.read().decode()
            assert 'SYNTHETIC_SECRET_PROVIDER_BODY' not in raw
            return result.status, json.loads(raw)

    def start():
        nonlocal process
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app:app',
            '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if call('/api/v1/health')[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(.05)
        raise AssertionError('Backend did not start')

    def stop(hard=False):
        if process is not None and process.poll() is None:
            process.kill() if hard else process.terminate()
            process.wait(timeout=10)

    try:
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            stack.callback(stop)
            db = str(Path(directory) / 'isolated.sqlite3')
            env = dict(os.environ, GIM_DB_PATH=db, GIM_EVALUATION_MODE='llm',
                GIM_ALLOW_EXTERNAL_LLM='1', GIM_COORDINATOR_TOKEN=token,
                GIM_LLM_ENDPOINT=f'http://127.0.0.1:{provider.server_port}/v1/chat/completions',
                GIM_LLM_MODEL='controlled-stub', GIM_LLM_API_KEY='', GIM_LLM_TIMEOUT='1.3',
                GIM_LLM_FALLBACK_TO_DEMO='0', GIM_GOOGLE_SPREADSHEET_ID='')
            start()
            for mode, code in [('401', 'provider_http_401'), ('429', 'provider_http_429'),
                    ('503', 'provider_http_503'), ('bad_envelope', 'invalid_provider_response'),
                    ('bad_json', 'invalid_model_json'), ('slow', 'provider_timeout'),
                    ('redirect', 'provider_http_error')]:
                state['mode'] = mode
                before = state['calls']
                status, result = call('/api/v1/applications', data)
                assert status == 503 and code in result['detail'], (mode, status, result)
                assert state['calls'] - before == (2 if mode == 'bad_json' else 1)
                with closing(sqlite3.connect(db)) as con:
                    assert con.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0
                    assert con.execute('SELECT COUNT(*) FROM application_reservations').fetchone()[0] == 0
                checks.append(mode + ': safe 503, no saved result, reservation released')
            assert state['trap'] == 0
            state['mode'] = 'concurrent'
            before = state['calls']
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: call('/api/v1/applications', data), range(2)))
            assert all(status == 201 for status, _ in results), results
            assert sorted(row['created'] for _, row in results) == [False, True]
            assert state['calls'] == before + 1
            assert all('evaluation' not in row for _, row in results)
            assert call('/api/v1/applications/' + data['application_id'])[0] == 401
            detail = call('/api/v1/applications/' + data['application_id'], auth=True)[1]
            assert detail['evaluation']['evaluation_mode'] == 'llm'
            assert detail['evaluation']['provenance']['model'] == 'controlled-stub'
            checks.append('concurrent duplicate: one provider call, one receipt created, protected detail')
            stop()
            start()
            before = state['calls']
            assert call('/api/v1/applications', data)[1]['created'] is False
            assert state['calls'] == before
            checks.append('process restart: saved evaluation reused without model call')
            crash = dict(data, application_id='synthetic-crash-reservation')
            state['mode'] = 'slow'
            entered.clear()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(call, '/api/v1/applications', crash)
                assert entered.wait(5)
                stop(hard=True)
                try:
                    pending.result()
                except (OSError, ValueError):
                    pass
            with closing(sqlite3.connect(db)) as con:
                assert con.execute('SELECT COUNT(*) FROM application_reservations').fetchone()[0] == 1
                # Exercise the documented 600s recovery boundary without waiting 10 minutes.
                con.execute('UPDATE application_reservations SET started_at=?', (time.time() - 601,))
                con.commit()
            state['mode'] = 'ok'
            start()
            assert call('/api/v1/applications', crash)[0] == 201
            with closing(sqlite3.connect(db)) as con:
                assert con.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 2
                assert con.execute('SELECT COUNT(*) FROM application_reservations').fetchone()[0] == 0
            checks.append('hard crash: stale reservation reclaimed after simulated 601s; one final record')
            for sent in state['sent']:
                assert not {'applicant_name', 'email', 'vk_url', 'birth_date', 'application_id'} & sent.keys()
            checks.append('all provider HTTP requests omit identifying fields')
        print(json.dumps({'checks': checks, 'provider_calls': state['calls'],
                'result': 'PASS', 'limitation': 'controlled local HTTP stub; no external model quality or Google/VK validation'},
                ensure_ascii=False, indent=2))
    finally:
        stop()
        provider.shutdown()
        provider.server_close()


if __name__ == '__main__':
    main()
