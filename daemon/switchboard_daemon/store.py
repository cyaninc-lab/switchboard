"""The daemon's state: one SQLite file (paired clients, pairing codes, tree entries)."""
import hashlib, os, secrets, sqlite3, sys, time, uuid
from pathlib import Path

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L: read aloud or typed from a terminal
CODE_TTL = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (token_hash TEXT PRIMARY KEY, name TEXT, created REAL);
CREATE TABLE IF NOT EXISTS pair_codes (code TEXT PRIMARY KEY, expires REAL);
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL, grp TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0, account TEXT, mode TEXT, created REAL);
"""
ENTRY_FIELDS = ("name", "grp", "pinned", "account", "mode", "model")


def default_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "switchboard"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "switchboard"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "switchboard"


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit; the CLI and server share the file
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(entries)")}
        if "cwd" not in cols:
            self.db.execute("ALTER TABLE entries ADD COLUMN cwd TEXT")  # folder of a session created in Switchboard
        if "model" not in cols:
            self.db.execute("ALTER TABLE entries ADD COLUMN model TEXT")  # None = Claude Code's default

    # pairing
    def new_pair_code(self):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        self.db.execute("INSERT INTO pair_codes VALUES (?, ?)", (code, time.time() + CODE_TTL))
        return code

    def redeem(self, code, client_name):
        """Single-use code -> long-lived token (only its hash is stored), or None."""
        self.db.execute("DELETE FROM pair_codes WHERE expires < ?", (time.time(),))
        if not self.db.execute("DELETE FROM pair_codes WHERE code = ?", ((code or "").strip().upper(),)).rowcount:
            return None
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO clients VALUES (?, ?, ?)", (_hash(token), client_name or "client", time.time()))
        return token

    def client_for(self, token):
        row = self.db.execute("SELECT name FROM clients WHERE token_hash = ?", (_hash(token),)).fetchone()
        return row["name"] if row else None

    def clients(self):
        return [dict(r) for r in self.db.execute("SELECT name, created, token_hash FROM clients ORDER BY created")]

    def revoke(self, prefix):
        return self.db.execute("DELETE FROM clients WHERE token_hash LIKE ?", (prefix + "%",)).rowcount

    # tree entries
    def entries(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM entries ORDER BY pinned DESC, grp, name")]

    def entry(self, entry_id):
        r = self.db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(r) if r else None

    def entry_by_session(self, session_id):
        r = self.db.execute("SELECT * FROM entries WHERE session_id = ?", (session_id,)).fetchone()
        return dict(r) if r else None

    def add_entry(self, session_id, name, grp, account=None, mode=None, cwd=None, model=None):
        eid = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO entries (id, session_id, name, grp, pinned, account, mode, created, cwd, model) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                        (eid, session_id, name, grp, account, mode, time.time(), cwd, model))
        return self.entry(eid)

    def update_entry(self, entry_id, **changes):
        changes = {k: v for k, v in changes.items() if k in ENTRY_FIELDS}
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)  # keys come from ENTRY_FIELDS only
            self.db.execute(f"UPDATE entries SET {sets} WHERE id = ?", (*changes.values(), entry_id))
        return self.entry(entry_id)

    def remove_entry(self, entry_id):
        return self.db.execute("DELETE FROM entries WHERE id = ?", (entry_id,)).rowcount > 0
