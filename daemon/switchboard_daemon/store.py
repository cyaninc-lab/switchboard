"""The daemon's state: one SQLite file (paired clients, pairing codes, tree entries)."""
import hashlib, json, os, secrets, sqlite3, sys, time, uuid
from pathlib import Path

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L: read aloud or typed from a terminal
CODE_TTL = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (token_hash TEXT PRIMARY KEY, name TEXT, created REAL);
CREATE TABLE IF NOT EXISTS pair_codes (code TEXT PRIMARY KEY, expires REAL);
CREATE TABLE IF NOT EXISTS watches (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, entry_id TEXT, kind TEXT NOT NULL, config TEXT NOT NULL,
  action TEXT NOT NULL, instructions TEXT, mode TEXT, enabled INTEGER NOT NULL DEFAULT 1, state TEXT,
  last_fired REAL, created REAL);
CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, source TEXT, title TEXT, body TEXT,
  watch_id TEXT, entry_id TEXT, from_agent INTEGER NOT NULL DEFAULT 0, read INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, kind TEXT NOT NULL, secret TEXT NOT NULL, created REAL);
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
        try:
            os.chmod(path, 0o600)  # holds client token hashes and account tokens
        except OSError:
            pass
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

    # accounts: extra Claude logins (setup-token tokens or API keys); NULL on an entry = the machine's own login
    def add_account(self, name, secret, kind="oauth"):
        if kind not in ("oauth", "api_key") or not name.strip() or not secret.strip():
            raise ValueError("bad_account")
        self.db.execute("INSERT OR REPLACE INTO accounts VALUES (?, ?, ?, ?)", (name.strip(), kind, secret.strip(), time.time()))

    def accounts(self):
        return [dict(r) for r in self.db.execute("SELECT name, kind, created FROM accounts ORDER BY name")]

    def account_env(self, name):
        """Environment that makes Claude Code use this account, or None if there's no such account."""
        r = self.db.execute("SELECT kind, secret FROM accounts WHERE name = ?", (name,)).fetchone()
        if not r:
            return None
        return {"CLAUDE_CODE_OAUTH_TOKEN": r["secret"]} if r["kind"] == "oauth" else {"ANTHROPIC_API_KEY": r["secret"]}

    def remove_account(self, name):
        self.db.execute("UPDATE entries SET account = NULL WHERE account = ?", (name,))
        return self.db.execute("DELETE FROM accounts WHERE name = ?", (name,)).rowcount > 0

    # watches
    WATCH_FIELDS = ("name", "entry_id", "kind", "config", "action", "instructions", "mode", "enabled", "state", "last_fired")

    def _watch(self, r):
        d = dict(r)
        d["config"], d["state"], d["enabled"] = json.loads(d["config"] or "{}"), json.loads(d["state"] or "null"), bool(d["enabled"])
        return d

    def watches(self):
        return [self._watch(r) for r in self.db.execute("SELECT * FROM watches ORDER BY created")]

    def watch(self, wid):
        r = self.db.execute("SELECT * FROM watches WHERE id = ?", (wid,)).fetchone()
        return self._watch(r) if r else None

    def add_watch(self, name, kind, config, action="notify", entry_id=None, instructions=None, mode=None):
        wid = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO watches (id, name, entry_id, kind, config, action, instructions, mode, enabled, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                        (wid, name, entry_id, kind, json.dumps(config), action, instructions, mode, time.time()))
        return self.watch(wid)

    def update_watch(self, wid, **changes):
        changes = {k: (json.dumps(v) if k in ("config", "state") else v) for k, v in changes.items() if k in self.WATCH_FIELDS}
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)  # keys from WATCH_FIELDS only
            self.db.execute(f"UPDATE watches SET {sets} WHERE id = ?", (*changes.values(), wid))
        return self.watch(wid)

    def remove_watch(self, wid):
        return self.db.execute("DELETE FROM watches WHERE id = ?", (wid,)).rowcount > 0

    # inbox: what watches and woken agents have to tell you
    def add_inbox(self, source, title, body, watch_id=None, entry_id=None, from_agent=False):
        cur = self.db.execute("INSERT INTO inbox (created, source, title, body, watch_id, entry_id, from_agent) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (time.time(), source, title, body, watch_id, entry_id, int(from_agent)))
        self.db.execute("DELETE FROM inbox WHERE id NOT IN (SELECT id FROM inbox ORDER BY id DESC LIMIT 500)")  # keep the last 500
        return self.inbox_item(cur.lastrowid)

    def inbox_item(self, iid):
        r = self.db.execute("SELECT * FROM inbox WHERE id = ?", (iid,)).fetchone()
        return {**dict(r), "read": bool(r["read"]), "from_agent": bool(r["from_agent"])} if r else None

    def inbox(self, limit=100):
        return [{**dict(r), "read": bool(r["read"]), "from_agent": bool(r["from_agent"])}
                for r in self.db.execute("SELECT * FROM inbox ORDER BY id DESC LIMIT ?", (limit,))]

    def mark_read(self, iid=None):
        if iid is None:
            self.db.execute("UPDATE inbox SET read = 1")
        else:
            self.db.execute("UPDATE inbox SET read = 1 WHERE id = ?", (iid,))

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
