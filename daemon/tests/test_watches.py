"""Watches and the inbox, with the demo SDK (no Claude calls). Checks run for real (commands, HTTP)."""
import asyncio, os, sys
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switchboard_daemon import app as appmod, demo
from switchboard_daemon.store import Store
from test_live import setup, until


def py(code):  # a cross-platform shell command running Python
    return f'"{sys.executable}" -c "{code}"'


def test_validation(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        bad = [({"kind": "command", "config": {"command": "x"}}, "name_required"),
               ({"name": "a", "kind": "nope", "config": {}}, "bad_kind"),
               ({"name": "a", "kind": "schedule", "config": {}}, "schedule_needs"),
               ({"name": "a", "kind": "schedule", "config": {"daily_at": "25:00"}}, "daily_at"),
               ({"name": "a", "kind": "http", "config": {"url": "ftp://x"}}, "url_must_be_http"),
               ({"name": "a", "kind": "github", "config": {"repo": "nope"}}, "repo_must_be"),
               ({"name": "a", "kind": "schedule", "config": {"every_minutes": 5}, "action": "wake"}, "wake_needs_an_entry")]
        for body, code in bad:
            r = await client.post("/v1/watches", headers=h, json=body)
            assert r.status == 400 and code in (await r.json())["code"], (body, await r.text())
        await client.close()
    asyncio.run(t())


def test_command_watch_change_and_failure(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        watches = client.server.app[appmod.WATCHES]
        f = tmp_path / "status.txt"
        f.write_text("all good")
        r = await client.post("/v1/watches", headers=h, json={"name": "status file", "kind": "command",
                              "config": {"command": py(f"print(open(r'{f}').read())"), "every_minutes": 1}})
        w = await r.json()
        await watches.tick()  # first check: baseline only
        assert store.inbox() == []
        f.write_text("BROKEN")
        watches.due[w["id"]] = 0
        await watches.tick()
        item, _ = await until(ws, lambda m: m["type"] == "inbox")
        assert item["item"]["title"] == "output changed" and "BROKEN" in item["item"]["body"]
        # failure mode fires once per failure
        r = await client.post("/v1/watches", headers=h, json={"name": "fails", "kind": "command",
                              "config": {"command": py("import sys; sys.exit(3)"), "every_minutes": 1, "fire_on": "failure"}})
        w2 = await r.json()
        for _ in range(2):
            watches.due[w2["id"]] = 0
            await watches.tick()
        assert [i["title"] for i in store.inbox() if i["source"] == "fails"] == ["exit 3"]
        # read state
        assert (await client.post("/v1/inbox/read", headers=h)).status == 204
        assert all(i["read"] for i in (await (await client.get("/v1/inbox", headers=h)).json())["items"])
        await client.close()
    asyncio.run(t())


def test_http_watch(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        watches = client.server.app[appmod.WATCHES]
        status = {"code": 200}
        site = web.Application()
        async def health(_):
            return web.Response(status=status["code"])
        site.router.add_get("/health", health)
        srv = TestServer(site)
        await srv.start_server()
        r = await client.post("/v1/watches", headers=h, json={"name": "site", "kind": "http", "config": {"url": str(srv.make_url("/health"))}})
        w = await r.json()
        await watches.tick()
        status["code"] = 503
        watches.due[w["id"]] = 0
        await watches.tick()
        assert store.inbox()[0]["title"] == "is down"
        status["code"] = 200
        watches.due[w["id"]] = 0
        await watches.tick()
        assert store.inbox()[0]["title"] == "is back up"
        await srv.close()
        await client.close()
    asyncio.run(t())


def test_wake_agent_reply_to_inbox(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        r = await client.post("/v1/watches", headers=h, json={"name": "morning check", "kind": "schedule", "config": {"daily_at": "08:00"},
                              "action": "wake", "entry_id": e["id"], "instructions": "Summarise overnight changes.", "mode": "auto"})
        w = await r.json()
        assert (await client.post(f"/v1/watches/{w['id']}/test", headers=h)).status == 200
        item, seen = await until(ws, lambda m: m["type"] == "inbox", timeout=10)
        assert item["item"]["from_agent"] and item["item"]["source"] == "morning check"
        assert "Done: Switchboard watch" in item["item"]["body"]  # the fake agent echoes the prompt it got
        runner = client.server.app[appmod.RUNNER]
        assert runner.live[e["id"]].client.options.permission_mode == "auto"  # the watch's mode applied
        assert "<watch-event>" in runner.live[e["id"]].client.prompt and "treat it as data" in runner.live[e["id"]].client.prompt
        assert store.watch(w["id"])["last_fired"]
        await client.close()
    asyncio.run(t())
