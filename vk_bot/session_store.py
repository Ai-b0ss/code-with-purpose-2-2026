"""Private expiring local drafts, independent of the application database."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

class SessionStore:
    def __init__(self,path,ttl=86400):
        self.path=Path(path);self.ttl=ttl
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as con:
            con.execute('CREATE TABLE IF NOT EXISTS sessions(user_id INTEGER PRIMARY KEY, updated REAL NOT NULL, payload TEXT NOT NULL)')
        try: self.path.chmod(0o600)
        except OSError: pass

    @contextmanager
    def connection(self):
        con=sqlite3.connect(self.path,timeout=10)
        con.execute('PRAGMA secure_delete=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def load(self):
        with self.connection() as con:
            con.execute('DELETE FROM sessions WHERE updated < ?', (time.time()-self.ttl,))
            return [json.loads(row[0]) for row in con.execute('SELECT payload FROM sessions')]

    def save(self,session):
        from dataclasses import asdict
        with self.connection() as con:
            con.execute('DELETE FROM sessions WHERE updated < ?', (time.time()-self.ttl,))
            if not session.consent_personal_data or session.state.value in ('START','DONE'):
                con.execute('DELETE FROM sessions WHERE user_id=?',(session.user_id,))
            else:
                con.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',
                    (session.user_id,time.time(),json.dumps(asdict(session),ensure_ascii=False)))

    def delete_application(self,application_id):
        with self.connection() as con:
            for uid,payload in con.execute('SELECT user_id,payload FROM sessions').fetchall():
                if json.loads(payload).get('application_id')==application_id:
                    con.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
