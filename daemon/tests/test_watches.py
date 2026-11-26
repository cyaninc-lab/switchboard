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
        assert any(m["type"] == "message" and m["message"]["type"] == "user" and "Switchboard watch" in str(m) for m in seen)
        assert "Done: Switchboard watch" in item["item"]["body"]  # the fake agent echoes the prompt it got
        runner = client.server.app[appmod.RUNNER]
        assert runner.live[e["id"]].client.options.permission_mode == "auto"  # the watch's mode applied
        assert "<watch-event>" in runner.live[e["id"]].client.prompt and "treat it as data" in runner.live[e["id"]].client.prompt
        assert store.watch(w["id"])["last_fired"]
        await client.close()
    asyncio.run(t())


def test_quiet_wake_watch_and_active_hours(tmp_path, monkeypatch):
    """A quiet wake watch's "nothing new" never reaches the inbox (a real finding does); a watch outside its hours doesn't check."""
    import datetime
    from switchboard_daemon import watches as wmod
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        mk = lambda name, instr: client.post("/v1/watches", headers=h, json={"name": name, "kind": "schedule", "config": {"every_minutes": 10, "quiet": True},
                                                                             "action": "wake", "entry_id": e["id"], "instructions": instr, "mode": "auto"})
        quiet = await (await mk("zoho quiet", "Check for mentions. It's a quiet day.")).json()
        found = await (await mk("zoho found", "Check for mentions.")).json()
        await client.post(f"/v1/watches/{quiet['id']}/test", headers=h)
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        await client.post(f"/v1/watches/{found['id']}/test", headers=h)
        item, seen = await until(ws, lambda m: m["type"] == "inbox", timeout=10)
        assert item["item"]["source"] == "zoho found"  # the quiet one's NOTHING_NEW never arrived
        assert not any(m["type"] == "inbox" for m in seen[:-1])  # nothing from the quiet one before it
        assert all(i["source"] != "zoho quiet" for i in store.inbox())
        # active hours: validation, and the scheduler skips checks outside them
        r = await client.post("/v1/watches", headers=h, json={"name": "x", "kind": "schedule", "config": {"every_minutes": 5, "active": {"from": "9am"}}})
        assert r.status == 400 and "active_hours" in (await r.json())["code"]
        cfg = {"active": {"from": "08:30", "to": "17:30", "days": [1, 2, 3, 4, 5]}}
        at = lambda y, mo, d, hh, mm: datetime.datetime(y, mo, d, hh, mm).timestamp()
        assert wmod.active_now(cfg, at(2026, 11, 19, 9, 0))        # a Thursday morning
        assert not wmod.active_now(cfg, at(2026, 11, 19, 18, 0))   # Thursday evening
        assert not wmod.active_now(cfg, at(2026, 11, 21, 10, 0))   # Saturday
        assert wmod.active_now({"active": {"from": "22:00", "to": "06:00"}}, at(2026, 11, 19, 23, 0))  # across midnight
        assert wmod.active_now({}, at(2026, 11, 21, 3, 0))         # no hours: always
        await client.close()
    asyncio.run(t())


def test_one_off_end_and_instructions_from_a_file(tmp_path, monkeypatch):
    """run_at fires once then switches off; max_runs and until end a watch; instructions_file is read when it fires."""
    import datetime, time
    from switchboard_daemon import watches as wm
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        watches = client.server.app[appmod.WATCHES]
        fmt = lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        now = time.time()
        r = await client.post("/v1/watches", headers=h, json={"name": "old", "kind": "schedule", "config": {"run_at": fmt(now - 120)}})
        assert r.status == 400 and "run_at_is_in_the_past" in await r.text()
        at = (now // 60 + 2) * 60  # two minutes from now, on the minute
        once = await (await client.post("/v1/watches", headers=h, json={"name": "once", "kind": "schedule", "config": {"run_at": fmt(at)}})).json()
        assert watches.next_run(once) == at
        await watches.tick(now)  # not yet
        assert store.watch(once["id"])["enabled"] and not store.watch(once["id"])["last_fired"]
        await watches.tick(at + 1)
        w = store.watch(once["id"])
        assert w["last_fired"] and not w["enabled"] and w["state"]["runs"] == 1 and watches.next_run(w) is None  # fired once, off
        await watches.tick(at + 10_000)
        assert store.watch(once["id"])["state"]["runs"] == 1
        # max_runs: off after two
        two = await (await client.post("/v1/watches", headers=h, json={"name": "two", "kind": "schedule", "config": {"every_minutes": 1, "max_runs": 2}})).json()
        for k in range(4):
            await watches.tick(now + 120 * (k + 1))
        w = store.watch(two["id"])
        assert w["state"]["runs"] == 2 and not w["enabled"]
        # until: off once past it
        u = await (await client.post("/v1/watches", headers=h, json={"name": "until", "kind": "schedule", "config": {"every_minutes": 1, "until": fmt(now + 600)}})).json()
        await watches.tick(now + 1200)
        assert not store.watch(u["id"])["enabled"]
        # instructions from a file, read when it fires
        f = tmp_path / "steps.md"; f.write_text("Check the back office for stuck orders.")
        w = {"config": {"instructions_file": str(f)}, "instructions": "old pasted text"}
        assert watches._instructions(w) == "Check the back office for stuck orders."
        assert "couldn't be read" in watches._instructions({"config": {"instructions_file": str(tmp_path / "gone.md")}})
        assert watches._instructions({"config": {"skill": "stuck-orders", "args": "site 12"}}) == "Use the stuck-orders skill with: site 12."
        await client.close()
    asyncio.run(t())
