"""Local, append-only audio recovery and durable transcript history."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import wave

RATE = 16000
SUCCESS_AUDIO_SECONDS = 48 * 3600
RECOVERY_SECONDS = 7 * 86400


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RecoveryStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(os.environ.get(
            "XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "blurt-linux"
        self.audio_dir = self.root / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.audio_dir, 0o700)
        self.db = self.root / "history.sqlite3"
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL,
                    duration REAL NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',
                    verbatim TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    settings TEXT NOT NULL DEFAULT '{}', trashed INTEGER NOT NULL DEFAULT 0,
                    completed REAL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                    created REAL NOT NULL, text TEXT NOT NULL, verbatim TEXT NOT NULL
                );
            ''')
            columns = {r[1] for r in conn.execute('PRAGMA table_info(sessions)')}
            if 'completed' not in columns:
                conn.execute('ALTER TABLE sessions ADD COLUMN completed REAL')
        os.chmod(self.db, 0o600)
        sync_directory(self.root)

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def path(self, session_id):
        if len(session_id) != 32 or any(c not in '0123456789abcdef' for c in session_id):
            raise ValueError("Invalid recording identifier")
        return self.audio_dir / (session_id + ".pcm")

    def begin(self, settings):
        session_id = uuid.uuid4().hex
        # No credentials, screen text, or clipboard content belong in this store.
        allowed = {k: settings[k] for k in ('source','language','polished','keyterms','instruction') if k in settings}
        with self.connect() as conn:
            conn.execute("INSERT INTO sessions(id,created,status,settings) VALUES(?,?,?,?)",
                         (session_id, time.time(), "recording", json.dumps(allowed)))
        try:
            return AudioJournal(self, session_id)
        except Exception:
            self.update(session_id, "failed", error="Could not create the recovery audio file.")
            raise

    def update(self, session_id, status, error=""):
        path = self.path(session_id)
        duration = path.stat().st_size / (RATE * 2) if path.exists() else 0
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET status=?,duration=?,error=? WHERE id=?",
                         (status, duration, error, session_id))

    def complete(self, session_id, result):
        # Commit both versions before any UI callback or paste can happen.
        with self.connect() as conn:
            conn.execute("INSERT INTO versions(session_id,created,text,verbatim) VALUES(?,?,?,?)",
                         (session_id, time.time(), result.text, result.verbatim))
            conn.execute("UPDATE sessions SET status='saved',text=?,verbatim=?,error='',completed=? WHERE id=?",
                         (result.text, result.verbatim, time.time(), session_id))

    def entries(self, trashed=False):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM sessions WHERE trashed=? ORDER BY created DESC", (int(trashed),))]

    def get(self, session_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise ValueError("Recording not found")
            return dict(row)

    def versions(self, session_id):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM versions WHERE session_id=? ORDER BY id DESC", (session_id,))]

    def audio(self, session_id):
        data = self.path(session_id).read_bytes()
        return data[:len(data) - len(data) % 2]

    def export_wav(self, session_id, path):
        data = self.audio(session_id)
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(data)

    def recover_interrupted(self):
        # Called once on startup, after the single-instance lock is established.
        with self.connect() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM sessions WHERE status IN ('recording','transcribing','stopping')")]
        for sid in ids:
            self.update(sid, "interrupted", error="Interrupted before completion. Saved audio is available to retry.")
        return len(ids)

    def trash(self, session_id, trashed=True):
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET trashed=? WHERE id=?", (int(trashed), session_id))

    def delete_permanently(self, session_id):
        row = self.get(session_id)
        if not row["trashed"]:
            raise ValueError("Move the recording to Trash before permanently deleting it.")
        self.path(session_id).unlink(missing_ok=True)
        sync_directory(self.audio_dir)
        with self.connect() as conn:
            conn.execute("DELETE FROM versions WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def import_texts(self, texts):
        """Preserve pre-upgrade memory-only text, without pretending audio exists."""
        for text in texts:
            if not text.strip():
                continue
            with self.connect() as conn:
                if conn.execute("SELECT 1 FROM sessions WHERE text=?", (text,)).fetchone():
                    continue
                conn.execute("INSERT INTO sessions(id,created,status,text) VALUES(?,?,?,?)",
                             (uuid.uuid4().hex, time.time(), "imported", text))

    def expiry(self, row):
        complete = row.get('completed')
        return ((complete + SUCCESS_AUDIO_SECONDS) if complete else row['created'] + RECOVERY_SECONDS,
                (complete or row['created']) + RECOVERY_SECONDS)

    def cleanup(self, now=None, protected=()):
        now = time.time() if now is None else now
        removed = 0
        for row in self.entries() + self.entries(True):
            sid = row['id']
            if sid in protected or row['status'] in ('recording', 'stopping', 'transcribing'):
                continue
            audio_expiry, text_expiry = self.expiry(row)
            if now >= audio_expiry:
                self.path(sid).unlink(missing_ok=True)
            if now >= text_expiry:
                with self.connect() as conn:
                    conn.execute('DELETE FROM versions WHERE session_id=?', (sid,))
                    conn.execute('DELETE FROM sessions WHERE id=?', (sid,))
                removed += 1
        return removed


class AudioJournal:
    """Unbuffered writes survive process death; fsync at least every 250 ms."""
    def __init__(self, store, session_id):
        self.store = store
        self.session_id = session_id
        self.lock = threading.Lock()
        self.fd = os.open(store.path(session_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.last_sync = time.monotonic()
        os.fsync(self.fd)
        sync_directory(store.audio_dir)

    def write(self, chunk):
        with self.lock:
            if self.fd is None:
                raise OSError("Recovery file was closed")
            view = memoryview(chunk)
            while view:
                n = os.write(self.fd, view)
                if n <= 0:
                    raise OSError("Recovery write failed")
                view = view[n:]
            if time.monotonic() - self.last_sync >= .25:
                os.fsync(self.fd)
                self.last_sync = time.monotonic()

    def close(self):
        with self.lock:
            if self.fd is not None:
                fd, self.fd = self.fd, None
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
