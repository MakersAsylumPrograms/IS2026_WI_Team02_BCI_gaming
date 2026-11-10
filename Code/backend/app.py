"""Single-process LAN prototype. Durable requests, ephemeral device navigation."""
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

TYPES = ('WATER', 'TOILET_ASSISTANCE', 'REPOSITIONING', 'CALL_NURSE')
COMMANDS = ('NEXT', 'PREVIOUS', 'SELECT')


def utc():
    return datetime.now(timezone.utc).isoformat()


def matches(left, right):
    return hmac.compare_digest(left.encode('utf-8'), right.encode('utf-8'))


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('EQUALCARE_SECRET', secrets.token_hex(32)),
        DATABASE=str(Path(__file__).parent / 'instance' / 'equalcare.sqlite3'),
        PATIENT_PASSWORD=os.environ.get('EQUALCARE_PATIENT_PASSWORD'),
        STAFF_PASSWORD=os.environ.get('EQUALCARE_STAFF_PASSWORD'),
        DEVICE_TOKEN=os.environ.get('EQUALCARE_DEVICE_TOKEN'),
        DEVICE_ID=os.environ.get('EQUALCARE_DEVICE_ID', 'esp32-01'),
        MAX_CONTENT_LENGTH=4096, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict', CLOCK=time.monotonic,
    )
    if config:
        app.config.update(config)
    if any(not app.config[k] for k in ('PATIENT_PASSWORD', 'STAFF_PASSWORD', 'DEVICE_TOKEN')):
        raise RuntimeError('Set EQUALCARE_PATIENT_PASSWORD, EQUALCARE_STAFF_PASSWORD and EQUALCARE_DEVICE_TOKEN.')
    if app.config['PATIENT_PASSWORD'] == app.config['STAFF_PASSWORD']:
        raise RuntimeError('Patient and staff passwords must differ.')
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)

    def connect():
        db = sqlite3.connect(app.config['DATABASE'], timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    db = connect()
    db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS requests (
          id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          acknowledged_at TEXT, resolved_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS active_type ON requests(type)
          WHERE status != 'RESOLVED';
        CREATE TABLE IF NOT EXISTS idempotency (
          key TEXT PRIMARY KEY, type TEXT NOT NULL,
          request_id TEXT NOT NULL REFERENCES requests(id));
    ''')
    db.close()
    lock = threading.RLock()
    clock = app.config['CLOCK']
    state = dict(events=deque(maxlen=128), cursor=0, epoch=str(uuid.uuid4()),
                 boot=None, seq=-1, retired=set(), last_seen=None, consumer=None, lease=0)
    app.extensions['input_state'] = state

    def error(code, message, status):
        return jsonify(error=dict(code=code, message=message)), status

    def body():
        value = request.get_json(silent=True)
        return value if isinstance(value, dict) else {}

    def identifier(value):
        return isinstance(value, str) and 1 <= len(value) <= 128 and value.isascii() and all(c.isalnum() or c in '-_.' for c in value)

    def require(*roles):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if session.get('role') not in roles:
                    return error('UNAUTHORIZED', 'Sign in with the appropriate role.', 401)
                if request.method != 'GET' and not matches(request.headers.get('X-CSRF-Token', ''), session.get('csrf', '!')):
                    return error('CSRF', 'Missing or invalid session CSRF token.', 403)
                return fn(*args, **kwargs)
            return wrapped
        return decorator

    def device(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not matches(request.headers.get('Authorization', ''), 'Bearer ' + app.config['DEVICE_TOKEN']):
                return error('UNAUTHORIZED', 'Invalid device token.', 401)
            return fn(*args, **kwargs)
        return wrapped

    @app.errorhandler(sqlite3.Error)
    def database_error(exc):
        app.logger.exception('Database operation failed')
        return error('STORAGE_UNAVAILABLE', 'Storage unavailable; retry with the same idempotency key.', 503)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return error(exc.name.upper().replace(' ', '_'), exc.description, exc.code)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get('/api/v1/health')
    def health():
        db = connect()
        try:
            db.execute('SELECT count(*) FROM requests').fetchone()
        finally:
            db.close()
        return jsonify(status='ok', version='1')

    @app.post('/api/v1/session')
    def login():
        # Custom header prevents cross-origin form login; CORS is intentionally disabled.
        if request.headers.get('X-EqualCare-Client') != 'dashboard':
            return error('INVALID_CLIENT', 'Dashboard header required.', 403)
        data = body()
        role = data.get('role')
        password = data.get('password')
        if role not in ('patient', 'staff') or not isinstance(password, str) or not matches(password, app.config[role.upper() + '_PASSWORD']):
            return error('UNAUTHORIZED', 'Invalid credentials.', 401)
        session.clear()
        session.update(role=role, csrf=secrets.token_hex(24), sid=secrets.token_hex(24))
        return jsonify(role=role, csrf=session['csrf'])

    @app.get('/api/v1/session')
    @require('patient', 'staff')
    def get_session():
        return jsonify(role=session['role'], csrf=session['csrf'])

    @app.delete('/api/v1/session')
    @require('patient', 'staff')
    def logout():
        with lock:
            if state['consumer'] and state['consumer'].startswith(session['sid'] + ':'):
                state.update(consumer=None, lease=0)
        session.clear()
        return jsonify(ok=True)

    @app.post('/api/v1/heartbeat')
    @device
    def heartbeat():
        if body().get('device_id') != app.config['DEVICE_ID']:
            return error('INVALID_DEVICE', 'Unknown device_id.', 400)
        with lock:
            state['last_seen'] = clock()
        return jsonify(ok=True)

    @app.post('/api/v1/input-events')
    @device
    def input_event():
        data = body()
        if (data.get('device_id') != app.config['DEVICE_ID'] or not identifier(data.get('boot_id'))
            or type(data.get('seq')) is not int or not 0 <= data['seq'] <= 2147483647
            or data.get('source') not in ('joystick', 'eog') or data.get('command') not in COMMANDS):
            return error('INVALID_EVENT', 'Invalid device, boot_id, seq, source or command.', 400)
        with lock:
            state['last_seen'] = clock()
            boot = data['boot_id']
            if boot in state['retired']:
                return jsonify(accepted=False, reason='retired_boot')
            if boot != state['boot']:
                if len(state['retired']) >= 1024:
                    return error('BOOT_LIMIT', 'Restart backend after excessive device reboots.', 503)
                if state['boot'] is not None:
                    state['retired'].add(state['boot'])
                state.update(boot=boot, seq=-1)
                state['events'].clear()
            if data['seq'] <= state['seq']:
                return jsonify(accepted=False, reason='duplicate_or_out_of_order')
            state['seq'] = data['seq']
            state['cursor'] += 1
            event = {k: data[k] for k in ('device_id', 'boot_id', 'seq', 'source', 'command')}
            event.update(cursor=state['cursor'], received=clock())
            state['events'].append(event)
            return jsonify(accepted=True, cursor=state['cursor'])

    @app.get('/api/v1/input-events')
    @require('patient')
    def events():
        after = request.args.get('after')
        if after is not None and (not after.isdigit() or len(after) > 16):
            return error('INVALID_CURSOR', 'after must be a non-negative integer.', 400)
        with lock:
            now = clock()
            owner = session['sid'] + ':' + request.headers.get('X-Consumer-ID', '')
            if not identifier(request.headers.get('X-Consumer-ID')):
                return error('INVALID_CONSUMER', 'Provide X-Consumer-ID for this page instance.', 400)
            if state['consumer'] != owner and state['lease'] > now:
                return error('CONSUMER_BUSY', 'Another patient screen controls device input.', 409)
            fresh = state['consumer'] != owner or state['lease'] <= now
            state.update(consumer=owner, lease=now + 3)
            reset = fresh or after is None or request.args.get('epoch') != state['epoch'] or int(after) > state['cursor']
            if not reset and state['events']:
                # Never deliver SELECT after silently losing earlier navigation.
                reset = int(after) < state['events'][0]['cursor'] - 1 or any(
                    e['cursor'] > int(after) and now - e['received'] >= 2 for e in state['events'])
            selected = [] if reset else [
                {k: v for k, v in e.items() if k != 'received'} for e in state['events']
                if e['cursor'] > int(after) and now - e['received'] < 2]
            return jsonify(events=selected, cursor=state['cursor'], epoch=state['epoch'], reset=reset)

    @app.get('/api/v1/device')
    @require('patient', 'staff')
    def device_status():
        with lock:
            age = None if state['last_seen'] is None else clock() - state['last_seen']
            return jsonify(device_id=app.config['DEVICE_ID'], connected=age is not None and age < 15,
                           seconds_since_seen=age)

    @app.get('/api/v1/requests')
    @require('patient', 'staff')
    def requests_list():
        db = connect()
        try:
            rows = db.execute('SELECT * FROM requests ORDER BY created_at, id').fetchall()
            return jsonify(requests=[dict(row) for row in rows])
        finally:
            db.close()

    @app.post('/api/v1/requests')
    @require('patient')
    def new_request():
        data = body()
        kind, key = data.get('type'), data.get('idempotency_key')
        if kind not in TYPES or not identifier(key):
            return error('INVALID_REQUEST', 'Valid type and idempotency_key are required.', 400)
        db = connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM idempotency WHERE key=?', (key,)).fetchone()
            if prior and prior['type'] != kind:
                return error('IDEMPOTENCY_CONFLICT', 'Key was used for a different request type.', 409)
            row = db.execute('SELECT * FROM requests WHERE id=?', (prior['request_id'],)).fetchone() if prior else db.execute("SELECT * FROM requests WHERE type=? AND status!='RESOLVED'", (kind,)).fetchone()
            created = row is None
            if created:
                rid, now = str(uuid.uuid4()), utc()
                db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)', (rid, kind, 'PENDING', now, now, None, None))
                row = db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
            if not prior:
                db.execute('INSERT INTO idempotency VALUES (?,?,?)', (key, kind, row['id']))
            db.commit()
            return jsonify(request=dict(row)), 201 if created else 200
        finally:
            db.close()

    @app.post('/api/v1/requests/<rid>/<action>')
    @require('staff')
    def transition(rid, action):
        if action not in ('acknowledge', 'resolve'):
            return error('NOT_FOUND', 'Unknown action.', 404)
        db = connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
            if row is None:
                return error('NOT_FOUND', 'Unknown request.', 404)
            status = row['status']
            if action == 'resolve' and status == 'PENDING':
                return error('INVALID_TRANSITION', 'Acknowledge before resolving.', 409)
            if action == 'acknowledge' and status == 'PENDING':
                now = utc()
                db.execute("UPDATE requests SET status='ACKNOWLEDGED', acknowledged_at=?, updated_at=? WHERE id=?", (now, now, rid))
            if action == 'resolve' and status == 'ACKNOWLEDGED':
                now = utc()
                db.execute("UPDATE requests SET status='RESOLVED', resolved_at=?, updated_at=? WHERE id=?", (now, now, rid))
            db.commit()
            return jsonify(request=dict(db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()))
        finally:
            db.close()

    frontend = Path(__file__).parent.parent / 'frontend'

    @app.get('/')
    def home():
        return send_from_directory(frontend, 'index.html')

    @app.get('/<role>/')
    def dashboard(role):
        if role not in ('patient', 'staff'):
            return error('NOT_FOUND', 'Unknown dashboard.', 404)
        return send_from_directory(frontend / role, 'index.html')

    @app.get('/assets/<path:name>')
    def assets(name):
        return send_from_directory(frontend / 'shared', name)

    return app


if __name__ == '__main__':
    from waitress import serve
    serve(create_app(), host='0.0.0.0', port=8000, threads=8)
