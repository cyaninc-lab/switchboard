"""Daemon API tests with the SDK's session readers faked (no Claude calls, no real ~/.claude)."""
import asyncio, dataclasses, json, os, sys
import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switchboard_daemon import app as appmod
from switchboard_daemon.store import Store


@dataclasses.dataclass
class Info:
    session_id: str
    summary: str
    last_modified: int
    cwd: str
    custom_title: str = None
    git_branch: str = None


SESSIONS = [Info("s1", "Deploy rolled back", 3, "/w/shorts-app", git_branch="fix"),
            Info("s2", "Bump Glance", 2, "/w/glance"),
            Info("s3", "Untitled", 1, "/home/u")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod.sdk, "list_sessions", lambda: list(SESSIONS))
    monkeypatch.setattr(appmod.sdk, "get_session_info", lambda sid: next((s for s in SESSIONS if s.session_id == sid), None))
    monkeypatch.setattr(appmod.sdk, "get_session_messages", lambda sid, limit, offset: [{"type": "user", "uuid": "u1", "message": "hi"}])
    monkeypatch.setattr(appmod, "CLAUDE_DIR", tmp_path / "claude")
    store = Store(str(tmp_path / "sb.db"))
    return store, tmp_path


def run(store, fn):
    async def go():
        client = TestClient(TestServer(appmod.make_app(store, "testbox")))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return asyncio.run(go())


async def paired(client, store):
    code = store.new_pair_code()
    r = await client.post("/v1/pair", json={"code": code.lower(), "client_name": "laptop"})  # codes are case-insensitive
    assert r.status == 200
    return {"Authorization": "Bearer " + (await r.json())["token"]}


def test_auth_and_pairing(env):
    store, _ = env

    async def t(c):
        assert (await c.get("/v1/machine")).status == 401
        code = store.new_pair_code()
        ok = await c.post("/v1/pair", json={"code": code, "client_name": "laptop"})
        assert ok.status == 200
        again = await c.post("/v1/pair", json={"code": code})  # single use
        assert again.status == 403
        h = {"Authorization": "Bearer " + (await ok.json())["token"]}
        m = await (await c.get("/v1/machine", headers=h)).json()
        assert m["name"] == "testbox" and m["sdk"]
        assert (await c.get("/v1/machine", headers={"Authorization": "Bearer nope"})).status == 401
    run(store, t)
    assert store.clients()[0]["name"] == "laptop"
    assert store.revoke(store.clients()[0]["token_hash"][:10]) == 1


def test_expired_code_rejected(env):
    store, _ = env
    code = store.new_pair_code()
    store.db.execute("UPDATE pair_codes SET expires = 0")
    assert store.redeem(code, "x") is None


def test_tree_flow(env):
    store, tmp = env

    async def t(c):
        h = await paired(c, store)
        s = await (await c.get("/v1/sessions?q=glance", headers=h)).json()
        assert s["total"] == 1 and s["sessions"][0]["entry_id"] is None

        r = await c.post("/v1/entries", headers=h, json={"session_id": "s2"})
        assert r.status == 201
        e = await r.json()
        assert e["name"] == "Bump Glance" and e["group"] == "glance" and e["state"] == "stopped"

        assert (await c.post("/v1/entries", headers=h, json={"session_id": "s2"})).status == 409
        assert appmod._default_group(os.path.expanduser("~")) == "home"
        assert (await c.post("/v1/entries", headers=h, json={"session_id": "nope"})).status == 404
        assert (await c.post("/v1/entries", headers=h, json={"new": {"cwd": "/w"}})).status == 501

        s = await (await c.get("/v1/sessions", headers=h)).json()
        assert [x["entry_id"] is not None for x in s["sessions"]] == [False, True, False]

        p = await (await c.patch(f"/v1/entries/{e['id']}", headers=h, json={"name": "Glance updates", "group": "infra", "pinned": True, "id": "hack"})).json()
        assert p["name"] == "Glance updates" and p["group"] == "infra" and p["pinned"] and p["id"] == e["id"]

        m = await (await c.get(f"/v1/entries/{e['id']}/messages", headers=h)).json()
        assert m["messages"][0]["uuid"] == "u1"

        # a live Claude Code process holding the session -> "terminal"
        (tmp / "claude" / "sessions").mkdir(parents=True)
        with open(f"/proc/{os.getpid()}/stat") as f:
            start = f.read().rsplit(")", 1)[1].split()[19]
        (tmp / "claude" / "sessions" / "1.json").write_text(json.dumps({"pid": os.getpid(), "procStart": start, "sessionId": "s2"}))
        (tmp / "claude" / "sessions" / "2.json").write_text(json.dumps({"pid": os.getpid(), "procStart": "1", "sessionId": "s1"}))  # stale pid reuse
        es = (await (await c.get("/v1/entries", headers=h)).json())["entries"]
        assert es[0]["state"] == "terminal"

        assert (await c.delete(f"/v1/entries/{e['id']}", headers=h)).status == 204
        assert (await (await c.get("/v1/entries", headers=h)).json())["entries"] == []
        assert (await c.delete(f"/v1/entries/{e['id']}", headers=h)).status == 404
    run(store, t)
