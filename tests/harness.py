"""Integration-test helpers (identical copy in every project). Standard library only.

Starts the real server as a subprocess on a free port with a throwaway database,
and talks to it over HTTP with a cookie-aware client.
"""
import http.cookiejar, json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'CorrectHorse1!'


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class Server:
    def __init__(self, db_path):
        self.db, self.port, self.proc = db_path, free_port(), None

    def start(self, *args):
        env = dict(os.environ, DB_PATH=str(self.db)); env.pop('APP_ENV', None)
        self.proc = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', str(self.port), *args], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            if self.proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/health', timeout=1); return self
            except Exception:
                time.sleep(0.1)
        self.proc.kill(); raise RuntimeError('Server failed to start:\n' + self.proc.stdout.read())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate(); self.proc.wait(10)
        if self.proc and self.proc.stdout:
            self.proc.stdout.close()


class Client:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ''

    def call(self, method, path, body=None, csrf=True, raw=False, headers=None):
        h = dict(headers or {}); data = None
        if method != 'GET':
            h['Content-Type'] = 'application/json'; data = json.dumps(body or {}).encode()
            if csrf:
                h['X-CSRF-Token'] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            r = self.opener.open(req, timeout=15); status, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        if raw:
            return status, payload.decode('utf-8-sig')
        obj = json.loads(payload or b'null')
        if isinstance(obj, dict) and obj.get('csrf'):
            self.csrf = obj['csrf']
        return status, obj

    def get(self, path, **kw): return self.call('GET', path, **kw)
    def post(self, path, body=None, **kw): return self.call('POST', path, body, **kw)
    def put(self, path, body=None, **kw): return self.call('PUT', path, body, **kw)
    def delete(self, path, **kw): return self.call('DELETE', path, **kw)
    def register(self, email): return self.post('/api/register', {'email': email, 'password': PASSWORD})
    def login(self, email, password=PASSWORD): return self.post('/api/login', {'email': email, 'password': password})


class AppTestCase(unittest.TestCase):
    """Starts one server per test class; subclasses add domain tests."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = Server(Path(cls.tmp.name) / 'test.sqlite3').start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop(); cls.tmp.cleanup()

    def client(self, email=None):
        c = Client(self.server.port)
        if email:
            s, _ = c.register(email)
            self.assertEqual(s, 201)
        return c

    def assertStatus(self, result, status):
        self.assertEqual(result[0], status, result[1]); return result[1]

    # ---------- shared security checks ----------
    def test_auth_session_csrf_and_rate_limit(self):
        c = self.client()
        self.assertStatus(c.get('/api/session'), 401)
        bad = self.assertStatus(c.post('/api/register', {'email': 'nope', 'password': 'short'}), 400)
        self.assertIn('email', bad['fields']); self.assertIn('password', bad['fields'])
        self.assertStatus(c.register('auth@example.com'), 201)
        self.assertStatus(c.register('auth@example.com'), 409)
        self.assertStatus(c.get('/api/session'), 200)
        name, token = next((ck.name, ck.value) for ck in c.jar)
        self.assertStatus(c.post('/api/logout', csrf=False), 403)  # CSRF token required for mutations
        self.assertStatus(c.post('/api/logout', headers={'Origin': 'http://evil.example'}), 403)
        self.assertStatus(c.post('/api/logout'), 200)
        self.assertStatus(c.get('/api/session'), 401)
        stolen = Client(self.server.port)  # replaying the old cookie must fail after logout
        self.assertStatus(stolen.get('/api/session', headers={'Cookie': f'{name}={token}'}), 401)
        for _ in range(5):
            self.assertStatus(c.login('auth@example.com', 'wrong-password!'), 401)
        self.assertStatus(c.login('auth@example.com'), 429)

    def test_unknown_routes_and_bad_json(self):
        c = self.client('misc@example.com')
        e = self.assertStatus(c.get('/api/does-not-exist'), 404)
        self.assertEqual(set(e), {'error', 'code', 'fields'})
        status, _ = c.call('GET', '/../server.py', raw=True)
        self.assertEqual(status, 404)
