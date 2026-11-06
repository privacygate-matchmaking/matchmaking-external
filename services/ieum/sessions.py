import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .core import deny


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Sessions:
    def __init__(self, path, key):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(key)
        self.refresh_lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS login_flows (state_hash TEXT PRIMARY KEY, payload BLOB NOT NULL, expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, pseudo_id TEXT NOT NULL,
              csrf_token TEXT NOT NULL, payload BLOB NOT NULL, created_at REAL NOT NULL, last_seen_at REAL NOT NULL, absolute_expires_at REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def encode(self, value):
        return self.cipher.encrypt(json.dumps(value).encode())

    def decode(self, value):
        try:
            return json.loads(self.cipher.decrypt(value))
        except (InvalidToken, ValueError):
            deny('AUTH_REQUIRED', 'session_corrupt')

    def flow(self, state, payload):
        with self.db() as db:
            db.execute('DELETE FROM login_flows WHERE expires_at<?', (time.time(),))
            db.execute('INSERT INTO login_flows VALUES (?,?,?)', (hashed(state), self.encode(payload), time.time()+600))

    def consume(self, state):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM login_flows WHERE state_hash=?', (hashed(state),)).fetchone()
            db.execute('DELETE FROM login_flows WHERE state_hash=?', (hashed(state),))
        if not row or row['expires_at'] <= time.time():
            deny('AUTH_REQUIRED', 'login_expired')
        return self.decode(row['payload'])

    def create(self, user, tokens):
        sid, csrf, timestamp = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        tokens = dict(tokens, access_expires_at=timestamp+int(tokens['expires_in']))
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE absolute_expires_at<?', (timestamp,))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)',
                       (hashed(sid), user['user_id'], user['pseudo_id'], csrf, self.encode(tokens), timestamp, timestamp, timestamp+28800))
        return sid

    def get(self, sid):
        if not sid:
            deny('AUTH_REQUIRED', 'no_session')
        timestamp = time.time()
        with self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE session_hash=?', (hashed(sid),)).fetchone()
            if not row or row['absolute_expires_at'] <= timestamp or row['last_seen_at']+1800 <= timestamp:
                db.execute('DELETE FROM sessions WHERE session_hash=?', (hashed(sid),))
                invalid = True
            else:
                invalid = False
                db.execute('UPDATE sessions SET last_seen_at=? WHERE session_hash=?', (timestamp, hashed(sid)))
        if invalid:
            deny('AUTH_REQUIRED', 'session_expired')
        return dict(row, tokens=self.decode(row['payload']))

    def delete(self, sid):
        if sid:
            with self.db() as db:
                db.execute('DELETE FROM sessions WHERE session_hash=?', (hashed(sid),))

    def replace(self, sid, old_tokens, new_tokens):
        tokens = {**old_tokens, **new_tokens, 'access_expires_at':time.time()+int(new_tokens['expires_in'])}
        with self.db() as db:
            changed = db.execute('UPDATE sessions SET payload=? WHERE session_hash=?', (self.encode(tokens), hashed(sid))).rowcount
        if not changed:
            deny('AUTH_REQUIRED', 'session_gone')
        return tokens
