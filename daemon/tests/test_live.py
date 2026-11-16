"""Live sessions over the WebSocket, with the demo module standing in for the SDK (no Claude calls)."""
import asyncio, os, sys
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switchboard_daemon import app as appmod, demo
from switchboard_daemon.store import Store


async def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "sdk", demo)
    monkeypatch.setattr(appmod, "open_in_terminal", lambda: set())
    store = Store(str(tmp_path / "sb.db"))
    client = TestClient(TestServer(appmod.make_app(store, "t")))
    await client.start_server()
    token = store.redeem(store.new_pair_code(), "test")
    h = {"Authorization": f"Bearer {token}"}
    e = await (await client.post("/v1/entries", headers=h, json={"session_id": "demo-1"})).json()
    ws = await client.ws_connect("/v1/ws")
    await ws.send_json({"type": "auth", "token": token})
    assert (await ws.receive_json())["type"] == "hello"
    return client, store, token, h, e, ws


async def until(ws, pred, timeout=5):
    seen = []
    while True:
        m = await asyncio.wait_for(ws.receive_json(), timeout)
        seen.append(m)
        if pred(m):
            return m, seen


def test_ws_rejects_bad_token(tmp_path, monkeypatch):
    async def t():
        client, *_ = await setup(tmp_path, monkeypatch)
        ws = await client.ws_connect("/v1/ws")
        await ws.send_json({"type": "auth", "token": "nope"})
        msg = await ws.receive()
        assert ws.closed or msg.type.name in ("CLOSE", "CLOSED")
        await client.close()
    asyncio.run(t())


def test_send_stream_permission_and_state(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        await ws.send_json({"type": "send", "entry": eid, "text": "please run the checks"})
        _, seen = await until(ws, lambda m: m["type"] == "permission_request")
        assert {"type": "state", "entry": eid, "state": "working"} in seen
        assert any(m["type"] == "delta" for m in seen)
        req = seen[-1]
        assert req["tool"] == "Bash" and req["input"]["command"] == "git status"
        m, _ = await until(ws, lambda m: m["type"] == "state")
        assert m["state"] == "waiting"
        # the tree shows it too
        es = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert es[0]["state"] == "waiting"
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "allow": True})
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "allow": False})  # second answer ignored
        m, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        resolved = [x for x in seen if x["type"] == "permission_resolved"]
        assert len(resolved) == 1 and resolved[0]["allow"] is True
        results = [b for x in seen if x["type"] == "message" for b in x["message"]["message"]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        assert results and "nothing to commit" in results[0]["content"]
        assert any(x["type"] == "message" and x["message"]["type"] == "assistant" and "Done:" in str(x["message"]) for x in seen)
        await client.close()
    asyncio.run(t())


def test_deny_interrupt_mode_stop(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        await ws.send_json({"type": "send", "entry": eid, "text": "run it"})
        req, _ = await until(ws, lambda m: m["type"] == "permission_request")
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "allow": False, "message": "not now"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Permission denied" in str(x) for x in seen if x["type"] == "message")

        # busy while working
        await ws.send_json({"type": "send", "entry": eid, "text": "slow job"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "working")
        t0 = asyncio.get_running_loop().time()
        await ws.send_json({"type": "interrupt", "entry": eid})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert asyncio.get_running_loop().time() - t0 < 3

        # auto mode: no permission prompt
        await ws.send_json({"type": "set_mode", "entry": eid, "mode": "auto"})
        await until(ws, lambda m: m["type"] == "mode")
        assert store.entry(eid)["mode"] == "auto"
        await ws.send_json({"type": "send", "entry": eid, "text": "run again"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert not any(x["type"] == "permission_request" for x in seen)

        await ws.send_json({"type": "set_mode", "entry": eid, "mode": "yolo"})
        err, _ = await until(ws, lambda m: m["type"] == "error")
        assert "bad_mode" in err["message"]

        await ws.send_json({"type": "stop", "entry": eid})
        m, _ = await until(ws, lambda m: m["type"] == "state")
        assert m["state"] == "stopped"
        es = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert es[0]["state"] == "stopped"
        await client.close()
    asyncio.run(t())


def test_idle_close(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        runner = client.server.app[appmod.RUNNER]
        await ws.send_json({"type": "send", "entry": e["id"], "text": "hi"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        runner.live[e["id"]].last_active -= 10_000
        monkeypatch.setattr("switchboard_daemon.runner.IDLE_CLOSE", 1)
        task = asyncio.get_running_loop().create_task(runner.reap_idle())
        m, _ = await until(ws, lambda m: m["type"] == "state", timeout=5)
        task.cancel()
        assert m["state"] == "stopped" and e["id"] not in runner.live
        await client.close()
    asyncio.run(t())


def test_terminal_pty(tmp_path, monkeypatch):
    """terminal_open runs a real PTY; here a shell stands in for `claude` (cli_path)."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        runner = client.server.app[appmod.RUNNER]
        script = tmp_path / "fake-claude"
        script.write_text('#!/bin/sh\necho "args: $*"; stty size; read line; echo "got: $line"\n')
        script.chmod(0o755)
        runner.cli_path = str(script)
        await ws.send_json({"type": "terminal_open", "entry": e["id"], "cols": 120, "rows": 40})
        await until(ws, lambda m: m["type"] == "terminal_opened")
        out = ""
        while "40 120" not in out:
            m = await asyncio.wait_for(ws.receive_json(), 5)
            out += m.get("data", "")
        assert "--resume demo-1" in out
        await ws.send_json({"type": "terminal_input", "entry": e["id"], "data": "hello\r"})
        while "got: hello" not in out:
            m = await asyncio.wait_for(ws.receive_json(), 5)
            out += m.get("data", "")
        await until(ws, lambda m: m["type"] == "terminal_closed")  # the fake exits after one line
        await client.close()
    asyncio.run(t())


def test_images_commands_and_result_only_output(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        await ws.send_json({"type": "send", "entry": eid, "text": "what is this?", "images": [{"media_type": "image/png", "data": png}] * 2})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        cmds = [m for m in seen if m["type"] == "commands"]
        assert cmds and "compact" in cmds[0]["commands"]
        assert any("I can see 2 image(s)" in str(m) for m in seen if m["type"] == "message")
        # the menu now uses the session's real list, with skill/built-in descriptions where known
        r = await (await client.get("/v1/commands", headers=h)).json()
        assert r["from_session"] and {"name": "compact", "description": "Summarise older messages to free up context"} in r["commands"]
        # built-in command output that arrives only as the result is shown as a reply
        await ws.send_json({"type": "send", "entry": eid, "text": "/usage"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("12% of this week" in str(m) for m in seen if m["type"] == "message")
        # validation
        await ws.send_json({"type": "send", "entry": eid, "text": "x", "images": [{"media_type": "image/tiff", "data": png}]})
        err, _ = await until(ws, lambda m: m["type"] == "error")
        assert "unsupported_image_type" in err["message"]
        await client.close()
    asyncio.run(t())


def test_new_session(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        work = tmp_path / "proj"
        work.mkdir()
        r = await client.post("/v1/entries", headers=h, json={"new": {"cwd": str(work)}, "name": "Fresh start"})
        assert r.status == 201
        n = await r.json()
        assert n["state"] == "new" and n["group"] == "proj" and n["session"]["cwd"] == str(work)
        assert (await (await client.get(f"/v1/entries/{n['id']}/messages", headers=h)).json())["messages"] == []
        folders = (await (await client.get("/v1/folders", headers=h)).json())
        assert folders["home"]
        # first message starts it with its own id; afterwards it's an ordinary session that resumes
        await ws.send_json({"type": "send", "entry": n["id"], "text": "hello new"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Done: hello new" in str(m) for m in seen)
        runner = client.server.app[appmod.RUNNER]
        assert runner.live[n["id"]].client.options.session_id == n["session_id"]
        await ws.send_json({"type": "stop", "entry": n["id"]})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "stopped")
        es = {x["id"]: x for x in (await (await client.get("/v1/entries", headers=h)).json())["entries"]}
        assert es[n["id"]]["state"] == "stopped"
        await ws.send_json({"type": "send", "entry": n["id"], "text": "again"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert runner.live[n["id"]].client.options.resume == n["session_id"]
        await client.close()
    asyncio.run(t())


def test_queue_while_working(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        await ws.send_json({"type": "send", "entry": eid, "text": "first"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "working")
        await ws.send_json({"type": "send", "entry": eid, "text": "second"})
        q, _ = await until(ws, lambda m: m["type"] == "queued")
        assert q["count"] == 1
        _, seen = await until(ws, lambda m: m["type"] == "message" and "Done: second" in str(m))
        texts = [str(m) for m in seen if m["type"] == "message"]
        assert any("Done: first" in t for t in texts)  # first finished before second started
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        await client.close()
    asyncio.run(t())


def test_models(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        ms = (await (await client.get("/v1/models", headers=h)).json())["models"]
        assert [m["value"] for m in ms] == ["default", "sonnet", "haiku"]
        eid = e["id"]
        await ws.send_json({"type": "set_model", "entry": eid, "model": "sonnet"})
        await until(ws, lambda m: m["type"] == "model" and m["model"] == "sonnet")
        assert store.entry(eid)["model"] == "sonnet"
        await ws.send_json({"type": "send", "entry": eid, "text": "hi"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Done: hi [sonnet]" in str(m) for m in seen)  # the session started on the chosen model
        await ws.send_json({"type": "set_model", "entry": eid, "model": "haiku"})  # switch mid-session
        await until(ws, lambda m: m["type"] == "model")
        await ws.send_json({"type": "send", "entry": eid, "text": "again"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Done: again [haiku]" in str(m) for m in seen)
        es = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert es[0]["model"] == "haiku"
        await client.close()
    asyncio.run(t())
