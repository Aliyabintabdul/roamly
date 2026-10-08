"""Shared backend foundation (standard library only).

An identical copy of this file lives in every project so each app stays
independently runnable. It provides: routing, JSON errors, SQLite migrations
with automatic backups, PBKDF2 password hashing, hashed expiring sessions,
CSRF tokens, origin checks, login rate limiting, input validation, CSV export
with formula-injection protection, optional demo seeding and static files.
"""
import argparse, csv, hashlib, hmac, io, json, math, mimetypes, os, re, secrets, shutil, sqlite3, sys, time, traceback
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SESSION_TTL = int(os.environ.get('SESSION_TTL_SECONDS', 8 * 3600))
PBKDF2_ROUNDS = 200_000
MAX_BODY = 64 * 1024
EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$')
DEMO_EMAIL = 'demo@example.com'

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, salt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset INTEGER NOT NULL);
"""
SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def today():
    return date.today().isoformat()


def hash_password(password, salt_hex):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), PBKDF2_ROUNDS).hex()


def rows(cursor):
    return [dict(r) for r in cursor]


class ApiError(Exception):
    def __init__(self, status, message, code='bad_request', fields=None):
        super().__init__(message)
        self.status, self.message, self.code, self.fields = status, message, code, fields or {}


def not_found(what='Record'):
    return ApiError(404, f'{what} not found.', 'not_found')


def one(c, sql, args, what='Record'):
    r = c.execute(sql, args).fetchone()
    if not r:
        raise not_found(what)
    return dict(r)


def safe_cell(v):
    """Neutralise spreadsheet formulas in exported text cells."""
    if v is None:
        return ''
    if isinstance(v, str) and v.lstrip()[:1] in ('=', '+', '-', '@', '\t', '\r', '|'):
        return "'" + v
    return v


class Csv:
    def __init__(self, filename, header, data):
        self.filename, self.header, self.data = filename, header, data

    def render(self):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(self.header)
        for r in self.data:
            w.writerow([safe_cell(v) for v in r])
        return buf.getvalue().encode('utf-8-sig')


class Form:
    """Collects field-level validation errors and raises one 400 response."""

    def __init__(self, data):
        self.data = data if isinstance(data, dict) else {}
        self.errors, self.values = {}, {}

    def _raw(self, key):
        v = self.data.get(key)
        return v.strip() if isinstance(v, str) else v

    def _set(self, key, value, error=None):
        if error and key not in self.errors:
            self.errors[key] = error
        self.values[key] = value
        return value

    def text(self, key, required=False, max_len=200):
        v = self._raw(key)
        if v is None or v == '':
            return self._set(key, '', 'This field is required.' if required else None)
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            return self._set(key, '', 'Enter text.')
        v = str(v)
        return self._set(key, v, f'Use {max_len} characters or fewer.' if len(v) > max_len else None)

    def choice(self, key, options, default=None):
        v = self._raw(key)
        v = default if v in (None, '') else v
        return self._set(key, v, None if v in options else 'Choose one of the listed options.')

    def number(self, key, required=False, minimum=0, maximum=1e9, integer=False, default=None):
        v = self._raw(key)
        if v is None or v == '':
            return self._set(key, default, 'This field is required.' if required else None)
        try:
            if isinstance(v, bool):
                raise ValueError
            n = float(v)
            if not math.isfinite(n):
                raise ValueError
        except (TypeError, ValueError):
            return self._set(key, default, 'Enter a number.')
        if integer and not n.is_integer():
            return self._set(key, default, 'Enter a whole number.')
        if n < minimum or n > maximum:
            return self._set(key, default, f'Enter a value between {minimum:,g} and {maximum:,g}.')
        return self._set(key, int(n) if integer else round(n, 2))

    def _pattern(self, key, required, parse, message):
        v = self._raw(key)
        if v is None or v == '':
            return self._set(key, None, 'This field is required.' if required else None)
        try:
            parse(str(v))
            return self._set(key, str(v))
        except ValueError:
            return self._set(key, None, message)

    def date(self, key, required=False):
        return self._pattern(key, required, lambda s: date.fromisoformat(s) if len(s) == 10 else int('x'), 'Enter a date as YYYY-MM-DD.')

    def time(self, key, required=False):
        return self._pattern(key, required, lambda s: datetime.strptime(s, '%H:%M'), 'Enter a time as HH:MM.')

    def datetime(self, key, required=False):
        return self._pattern(key, required, lambda s: datetime.strptime(s, '%Y-%m-%dT%H:%M'), 'Enter a date and time.')

    def email(self, key, required=False):
        v = self.text(key, required, 254)
        if v and not EMAIL_RE.match(v):
            self._set(key, v, 'Enter a valid email address.')
        return v

    def url(self, key, required=False):
        v = self.text(key, required, 500)
        if v and not re.match(r'^https?://[^\s]+\.[^\s]+$', v, re.I):
            self._set(key, v, 'Enter a full link starting with http:// or https://.')
        return v

    def check(self, ok, key, message):
        if not ok and key not in self.errors:
            self.errors[key] = message

    def done(self):
        if self.errors:
            raise ApiError(400, 'Please correct the highlighted fields.', 'validation_error', self.errors)
        return self.values


class Ctx:
    def __init__(self, app, db, params, query, body, ip):
        self.app, self.db, self.params, self.query, self.body, self.ip = app, db, params, query, body, ip
        self.user = None
        self.cookie = None

    @property
    def uid(self):
        return self.user['id']

    def q(self, key, default=''):
        return self.query.get(key, [default])[0].strip()


class App:
    def __init__(self, name, slug, base, migrations, seed=None):
        self.name, self.slug = name, slug
        self.base = Path(base).resolve()
        self.static = self.base / 'frontend'
        self.db_path = Path(os.environ.get('DB_PATH') or self.base / 'data.sqlite3')
        self.migrations, self.seed = migrations, seed
        self.routes = []
        self.production = os.environ.get('APP_ENV', '').lower() == 'production'
        self.cookie_name = f'{slug}_sid'  # cookies are shared across localhost ports, so names must differ
        self._auth_routes()

    # ---------- routing ----------
    def route(self, method, pattern, public=False):
        rx = re.compile('^' + re.sub(r'\{(\w+)\}', r'(?P<\1>\\d+)', pattern) + '$')

        def deco(fn):
            self.routes.append((method, rx, fn, public))
            return fn
        return deco

    # ---------- database ----------
    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=5000')
        return c

    def init_db(self):
        existed = self.db_path.exists()
        c = self.connect()
        try:
            version = c.execute('PRAGMA user_version').fetchone()[0]
            if existed and version < len(self.migrations):
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                backup = self.db_path.with_name(f'{self.db_path.stem}.backup-v{version}-{stamp}.sqlite3')
                shutil.copy2(self.db_path, backup)
                print(f'Backed up database to {backup.name} before migrating.', flush=True)
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(CORE_SCHEMA)
            cols = [r[1] for r in c.execute('PRAGMA table_info(sessions)')]
            if cols and 'token_hash' not in cols:  # legacy plain-token sessions: drop (forces re-login)
                c.execute('DROP TABLE sessions')
            c.executescript(SESSION_SCHEMA)
            ucols = [r[1] for r in c.execute('PRAGMA table_info(users)')]
            if 'created' not in ucols:
                c.execute("ALTER TABLE users ADD COLUMN created TEXT NOT NULL DEFAULT ''")
            if 'is_demo' not in ucols:
                c.execute('ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0')
                c.execute('UPDATE users SET is_demo=1 WHERE email=?', (DEMO_EMAIL,))
            for i in range(version, len(self.migrations)):
                c.execute('BEGIN IMMEDIATE')
                try:
                    m = self.migrations[i]
                    if callable(m):
                        m(c)
                    else:
                        for stmt in [s for s in m.split(';') if s.strip()]:
                            c.execute(stmt)
                    c.execute(f'PRAGMA user_version={i + 1}')
                    c.execute('COMMIT')
                except Exception:
                    c.execute('ROLLBACK')
                    raise
            c.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
            c.execute('DELETE FROM login_attempts WHERE reset<?', (int(time.time()),))
        finally:
            c.close()

    def seed_demo(self):
        if self.production:
            sys.exit('Demo seeding is disabled when APP_ENV=production.')
        password = os.environ.get('DEMO_PASSWORD', 'DemoPass123!')
        c = self.connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM users WHERE email=?', (DEMO_EMAIL,)).fetchone():
                c.execute('ROLLBACK')
                print('Demo account already exists; its data was left unchanged.', flush=True)
                return
            salt = secrets.token_hex(16)
            uid = c.execute('INSERT INTO users(email,password,salt,created,is_demo) VALUES(?,?,?,?,1)',
                            (DEMO_EMAIL, hash_password(password, salt), salt, now_iso())).lastrowid
            if self.seed:
                self.seed(c, uid)
            c.execute('COMMIT')
            print(f'Seeded fictional demo workspace: {DEMO_EMAIL} (local demo only).', flush=True)
        finally:
            c.close()

    # ---------- auth ----------
    def _throttled(self, c, key, limit):
        r = c.execute('SELECT count,reset FROM login_attempts WHERE key=?', (key,)).fetchone()
        now = int(time.time())
        if r and r['reset'] > now and r['count'] >= limit:
            mins = math.ceil((r['reset'] - now) / 60)
            return {'error': f'Too many attempts. Try again in {mins} minute{"s" if mins != 1 else ""}.', 'code': 'rate_limited', 'fields': {}}
        return None

    def _bump(self, c, key, window):
        now = int(time.time())
        c.execute("""INSERT INTO login_attempts(key,count,reset) VALUES(?,1,?)
                     ON CONFLICT(key) DO UPDATE SET count=CASE WHEN reset<=? THEN 1 ELSE count+1 END,
                     reset=CASE WHEN reset<=? THEN excluded.reset ELSE reset END""", (key, now + window, now, now))

    def _start_session(self, ctx, uid):
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(24), int(time.time())
        ctx.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), uid, csrf, now, now + SESSION_TTL))
        ctx.cookie = f'{self.cookie_name}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL}' + ('; Secure' if self.production else '')
        u = ctx.db.execute('SELECT email,is_demo FROM users WHERE id=?', (uid,)).fetchone()
        return {'user': {'email': u['email'], 'is_demo': bool(u['is_demo'])}, 'csrf': csrf}

    def load_session(self, c, cookie_header):
        token = ''
        for part in (cookie_header or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == self.cookie_name:
                token = v
        if not token:
            return None
        return c.execute("""SELECT s.token_hash,s.csrf,u.id,u.email,u.is_demo FROM sessions s JOIN users u ON u.id=s.user_id
                            WHERE s.token_hash=? AND s.expires>?""", (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()

    def _auth_routes(self):
        def credentials(ctx):
            f = Form(ctx.body)
            email = f.email('email', True).lower()
            pw = ctx.body.get('password') if isinstance(ctx.body.get('password'), str) else ''
            f.check(10 <= len(pw) <= 128, 'password', 'Use 10–128 characters.')
            f.done()
            return email, pw

        @self.route('POST', '/api/register', public=True)
        def register(ctx):
            key = 'register:' + ctx.ip
            limited = self._throttled(ctx.db, key, 10)
            if limited:
                return 429, limited
            self._bump(ctx.db, key, 3600)
            email, pw = credentials(ctx)
            if ctx.db.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
                raise ApiError(409, 'An account with this email already exists. Sign in instead.', 'conflict', {'email': 'Already registered.'})
            salt = secrets.token_hex(16)
            uid = ctx.db.execute('INSERT INTO users(email,password,salt,created) VALUES(?,?,?,?)',
                                 (email, hash_password(pw, salt), salt, now_iso())).lastrowid
            return 201, self._start_session(ctx, uid)

        @self.route('POST', '/api/login', public=True)
        def login(ctx):
            email, pw = credentials(ctx)
            keys = [('login:' + ctx.ip + '|' + email, 5), ('login-ip:' + ctx.ip, 30)]
            for key, limit in keys:
                limited = self._throttled(ctx.db, key, limit)
                if limited:
                    return 429, limited
            u = ctx.db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            candidate = hash_password(pw, u['salt'] if u else '00' * 16)  # constant work for unknown emails
            if not u or not hmac.compare_digest(u['password'], candidate):
                for key, _ in keys:
                    self._bump(ctx.db, key, 900)
                return 401, {'error': 'Email or password is incorrect.', 'code': 'invalid_credentials', 'fields': {}}
            ctx.db.execute('DELETE FROM login_attempts WHERE key=?', (keys[0][0],))
            return self._start_session(ctx, u['id'])

        @self.route('POST', '/api/logout')
        def logout(ctx):
            ctx.db.execute('DELETE FROM sessions WHERE token_hash=?', (ctx.user['token_hash'],))
            ctx.cookie = f'{self.cookie_name}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'
            return {'ok': True}

        @self.route('GET', '/api/session')
        def session(ctx):
            return {'user': {'email': ctx.user['email'], 'is_demo': bool(ctx.user['is_demo'])}, 'csrf': ctx.user['csrf']}

        @self.route('GET', '/api/health', public=True)
        def health(ctx):
            return {'ok': True, 'app': self.name, 'schema_version': ctx.db.execute('PRAGMA user_version').fetchone()[0]}

    # ---------- HTTP ----------
    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = app.name
            sys_version = ''

            def log_message(self, fmt, *args):
                if os.environ.get('LOG_REQUESTS'):
                    super().log_message(fmt, *args)

            def _headers(self, status, ctype, length, extra=()):
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(length))
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'same-origin')
                self.send_header('X-Frame-Options', 'DENY')
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()

            def send_json(self, status, obj, cookie=None):
                data = json.dumps(obj, allow_nan=False).encode()
                extra = [('Cache-Control', 'no-store')] + ([('Set-Cookie', cookie)] if cookie else [])
                self._headers(status, 'application/json; charset=utf-8', len(data), extra)
                self.wfile.write(data)

            def send_static(self, path):
                rel = 'index.html' if path in ('/', '') else path.lstrip('/')
                target = (app.static / rel).resolve()
                if not target.is_relative_to(app.static) or not target.is_file():
                    target = None
                if self.command not in ('GET', 'HEAD') or not target:
                    raise ApiError(404, 'Page not found.', 'not_found')
                data = target.read_bytes()
                ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                if ctype.startswith('text/') or ctype.endswith('javascript'):
                    ctype += '; charset=utf-8'
                self._headers(200, ctype, len(data), [('Cache-Control', 'no-cache'), ('Content-Security-Policy',
                              "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                              "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
                if self.command == 'GET':
                    self.wfile.write(data)

            def read_body(self):
                if 'application/json' not in self.headers.get('Content-Type', ''):
                    raise ApiError(415, 'Send requests as JSON.', 'unsupported_media_type')
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                except ValueError:
                    raise ApiError(400, 'Invalid Content-Length.')
                if n > MAX_BODY:
                    raise ApiError(413, 'Request is too large.', 'too_large')
                try:
                    body = json.loads(self.rfile.read(n) or b'{}')
                except (ValueError, UnicodeDecodeError):
                    raise ApiError(400, 'Request body is not valid JSON.', 'invalid_json')
                if not isinstance(body, dict):
                    raise ApiError(400, 'Request body must be a JSON object.', 'invalid_json')
                return body

            def check_origin(self):
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    raise ApiError(403, 'Cross-site request blocked.', 'forbidden')
                origin = self.headers.get('Origin')
                if origin and urlparse(origin).netloc != self.headers.get('Host'):
                    raise ApiError(403, 'Request origin rejected.', 'forbidden')

            def dispatch(self):
                url = urlparse(self.path)
                if not url.path.startswith('/api/'):
                    return self.send_static(url.path)
                found = [(rx.match(url.path), fn, public, m) for m, rx, fn, public in app.routes if rx.match(url.path)]
                route = next((x for x in found if x[3] == self.command), None)
                if not route:
                    raise ApiError(405, 'Method not allowed.', 'method_not_allowed') if found else ApiError(404, 'Endpoint not found.', 'not_found')
                match, fn, public, _ = route
                mutating = self.command in ('POST', 'PUT', 'PATCH', 'DELETE')
                if mutating:
                    self.check_origin()
                body = self.read_body() if self.command in ('POST', 'PUT', 'PATCH') else {}
                c = app.connect()
                ctx = Ctx(app, c, {k: int(v) for k, v in match.groupdict().items()}, parse_qs(url.query), body, self.client_address[0])
                try:
                    if not public:
                        ctx.user = app.load_session(c, self.headers.get('Cookie'))
                        if not ctx.user:
                            raise ApiError(401, 'Your session has ended. Please sign in.', 'unauthenticated')
                        if mutating and not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), ctx.user['csrf']):
                            raise ApiError(403, 'Security token missing or expired. Reload the page.', 'csrf_failed')
                    if mutating:
                        c.execute('BEGIN IMMEDIATE')
                    result = fn(ctx)
                    if c.in_transaction:
                        c.execute('COMMIT')
                except Exception:
                    if c.in_transaction:
                        c.execute('ROLLBACK')
                    raise
                finally:
                    c.close()
                if isinstance(result, Csv):
                    data = result.render()
                    return self._headers(200, 'text/csv; charset=utf-8', len(data), [
                        ('Cache-Control', 'no-store'), ('Content-Disposition', f'attachment; filename="{result.filename}"')]) or self.wfile.write(data)
                status, obj = result if isinstance(result, tuple) else (200, result)
                self.send_json(status, obj, ctx.cookie)

            def handle_any(self):
                try:
                    self.dispatch()
                except ApiError as e:
                    self.send_json(e.status, {'error': e.message, 'code': e.code, 'fields': e.fields})
                except sqlite3.IntegrityError:
                    self.send_json(409, {'error': 'That change conflicts with existing data.', 'code': 'conflict', 'fields': {}})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    traceback.print_exc()
                    self.send_json(500, {'error': 'Unexpected server error.', 'code': 'server_error', 'fields': {}})

            do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any
        return Handler

    def main(self, default_port=8000):
        p = argparse.ArgumentParser(description=f'Run {self.name}.')
        p.add_argument('--port', type=int, default=int(os.environ.get('PORT', default_port)))
        p.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'))
        p.add_argument('--seed-demo', action='store_true', help='create a clearly labelled fictional demo account (not allowed in production)')
        args = p.parse_args()
        self.init_db()
        if args.seed_demo:
            self.seed_demo()
        server = ThreadingHTTPServer((args.host, args.port), self.handler())
        print(f'{self.name} running at http://{"localhost" if args.host == "127.0.0.1" else args.host}:{args.port}'
              f' ({"production" if self.production else "development"} mode)', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
