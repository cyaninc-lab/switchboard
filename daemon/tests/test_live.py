"""Live sessions over the WebSocket, with the demo module standing in for the SDK (no Claude calls)."""
import asyncio, os, sys, time
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


def test_terminal_pty(tmp_path, monkeypatch):
    """terminal_open runs a real PTY; here a shell stands in for `claude` (cli_path)."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        runner = client.server.app[appmod.RUNNER]
        body = tmp_path / "fake_claude.py"  # stands in for `claude`: shows its args and window size, echoes a line
        body.write_text('import os, sys\nprint("args:", " ".join(sys.argv[1:]))\nsz = os.get_terminal_size()\n'
                        'print(sz.lines, sz.columns)\nprint("got:", input())\n')
        if os.name == "nt":
            script = tmp_path / "fake-claude.cmd"
            script.write_text(f'@"{sys.executable}" "{body}" %*\r\n')
        else:
            script = tmp_path / "fake-claude"
            script.write_text(f'#!{sys.executable}\n' + body.read_text())
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


def test_accounts(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        r = await client.post("/v1/accounts", headers=h, json={"name": "work", "token": "sk-ant-oat-secret"})
        assert r.status == 201
        listed = (await (await client.get("/v1/accounts", headers=h)).json())["accounts"]
        assert listed[0]["name"] == "work" and "secret" not in str(listed) and "sk-ant" not in str(listed)
        assert (await client.post("/v1/accounts", headers=h, json={"name": "x", "token": ""})).status == 400
        await ws.send_json({"type": "set_account", "entry": eid, "account": "nope"})
        err, _ = await until(ws, lambda m: m["type"] == "error")
        assert "no_such_account" in err["message"]
        await ws.send_json({"type": "send", "entry": eid, "text": "as me"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Done: as me" in str(m) and "token account" not in str(m) for m in seen if m["type"] == "message")
        # switching account closes the live session; the next message resumes under the new login
        await ws.send_json({"type": "set_account", "entry": eid, "account": "work"})
        await until(ws, lambda m: m["type"] == "account" and m["account"] == "work")
        await ws.send_json({"type": "send", "entry": eid, "text": "as work"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Done: as work (token account)" in str(m) for m in seen)
        runner = client.server.app[appmod.RUNNER]
        assert runner.live[eid].client.options.env == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-secret"}
        # removing the account falls the entry back to the machine login
        assert (await client.delete("/v1/accounts/work", headers=h)).status == 204
        assert store.entry(eid)["account"] is None and eid not in runner.live
        await client.close()
    asyncio.run(t())


def test_account_cli(tmp_path):
    import subprocess
    exe = [sys.executable, "-m", "switchboard_daemon", "--data", str(tmp_path)]
    cwd = os.path.join(os.path.dirname(__file__), "..")
    out = subprocess.run(exe + ["account", "add", "work"], input="sk-ant-oat-xyz\n", capture_output=True, text=True, cwd=cwd)
    assert "added account work" in out.stdout, out.stderr
    listed = subprocess.run(exe + ["account", "list"], capture_output=True, text=True, cwd=cwd).stdout
    assert "work" in listed and "sk-ant" not in listed
    if os.name != "nt":  # Windows has no Unix mode bits; the file sits in the user's own profile
        assert oct(os.stat(tmp_path / "switchboard.db").st_mode & 0o777) == "0o600"


def test_questions_plans_and_remember(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]

        async def ask(text):
            await ws.send_json({"type": "send", "entry": eid, "text": text})
            req, _ = await until(ws, lambda m: m["type"] == "permission_request")
            return req

        # a clarifying question: pick an option
        req = await ask("ask me something")
        assert req["kind"] == "question" and req["input"]["questions"][0]["header"] == "Database" and req["remember"] == []
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "answers": {"Which database should I use?": "Postgres"}})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("You picked: {'Which database should I use?': 'Postgres'}" in str(m) for m in seen)
        # ...or answer in your own words
        req = await ask("ask me again")
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "response": "whatever is simplest"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("You picked: whatever is simplest" in str(m) for m in seen)

        # plan approval: keep planning with feedback, then approve
        req = await ask("make a plan")
        assert req["kind"] == "plan" and "Write the migration" in req["input"]["plan"]
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "allow": False, "message": "use Alembic"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Keep planning. The user says: use Alembic" in str(m) for m in seen)
        req = await ask("plan again")
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "allow": True})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("Plan approved, starting." in str(m) for m in seen)

        # tool permission remembered for the session
        req = await ask("run it")
        assert req["kind"] == "tool" and req["remember"] == ["localSettings", "session"]
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "allow": True, "remember": "session"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("(remembered for: session)" in str(m) for m in seen)
        await client.close()
    asyncio.run(t())


def test_usage_meter(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        for n in (1, 2):
            await ws.send_json({"type": "send", "entry": e["id"], "text": f"hello {n}"})
            u, _ = await until(ws, lambda m: m["type"] == "usage")
        assert u["context_used"] == 52000 and u["context_window"] == 200000
        assert u["turn_cost"] == 0.0123 and u["session_cost"] == round(2 * 0.0123, 4)
        es = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert es[0]["usage"]["context_used"] == 52000  # reopening a session still shows the meter
        await client.close()
    asyncio.run(t())


def test_thinking_subagents_effort(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        await ws.send_json({"type": "send", "entry": eid, "text": "think, then use a subagent"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any(m["type"] == "delta" and m.get("kind") == "thinking" for m in seen)
        msgs = [m["message"] for m in seen if m["type"] == "message"]
        assert any(b.get("type") == "thinking" for m in msgs for b in m["message"]["content"] if isinstance(b, dict))
        children = [m for m in msgs if m.get("parent_tool_use_id") == "ag1"]
        assert len(children) == 3 and not any(m.get("parent_tool_use_id") for m in msgs if m not in children)
        # effort applies at start: changing it closes the idle session, the next message reopens at that effort
        await ws.send_json({"type": "set_effort", "entry": eid, "effort": "low"})
        _, seen = await until(ws, lambda m: m["type"] == "effort")
        assert {"type": "state", "entry": eid, "state": "stopped"} in seen  # the idle session closed first
        await ws.send_json({"type": "send", "entry": eid, "text": "what effort"})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert any("(effort: low)" in str(m) for m in seen)
        await ws.send_json({"type": "set_effort", "entry": eid, "effort": "turbo"})
        err, _ = await until(ws, lambda m: m["type"] == "error")
        assert "bad_effort" in err["message"]
        await client.close()
    asyncio.run(t())


def test_turns_claude_takes_on_its_own(tmp_path, monkeypatch):
    """A background command finishing makes Claude reply unprompted: that reply shows at once, and the next
    message gets its own answer (not the previous one: the "one behind" bug)."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        await ws.send_json({"type": "send", "entry": eid, "text": "start the background build"})
        await until(ws, lambda m: m["type"] == "message" and "Done: start the background build" in str(m))
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        # nobody sends anything: the unprompted turn arrives by itself
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "working")
        await until(ws, lambda m: m["type"] == "message" and "Done: background task finished" in str(m))
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        await ws.send_json({"type": "send", "entry": eid, "text": "next question"})
        m, _ = await until(ws, lambda m: m["type"] == "message" and m["message"]["type"] == "assistant" and "Done:" in str(m))
        assert "Done: next question" in str(m)
        await client.close()
    asyncio.run(t())


def test_subagent_tree_from_sidecars(tmp_path, monkeypatch):
    """Nested subagents come from Claude Code's meta sidecars, at any depth; their messages are served read-only."""
    import json
    d = tmp_path / "claude" / "projects" / "-p" / "sess-1" / "subagents"
    (d / "workflows" / "r1").mkdir(parents=True)
    for folder, aid, meta in [(d, "aa", {"description": "Top", "agentType": "general-purpose", "toolUseId": "tu1", "finished": True}),
                              (d, "bb", {"description": "Child", "agentType": "Explore", "toolUseId": "tu2", "parentAgentId": "aa"}),
                              (d / "workflows" / "r1", "cc", {"description": "Grandchild", "toolUseId": "tu3", "parentAgentId": "bb"})]:
        (folder / f"agent-{aid}.meta.json").write_text(json.dumps(meta))
        (folder / f"agent-{aid}.jsonl").write_text("")
    (d / "agent-broken.meta.json").write_text("{not json")
    monkeypatch.setattr(appmod, "CLAUDE_DIR", tmp_path / "claude")
    t = {a["id"]: a for a in appmod.subagent_tree("sess-1")}
    assert set(t) == {"aa", "bb", "cc"}
    assert t["aa"]["parent"] is None and t["aa"]["finished"] and t["bb"]["parent"] == "aa" and t["cc"]["parent"] == "bb"
    assert t["cc"]["description"] == "Grandchild" and not t["cc"]["finished"]
    assert appmod.subagent_tree("no-such-session") == []

    async def t2():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        monkeypatch.setattr(appmod, "subagent_tree", demo.subagent_tree)
        entries = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert sorted(a["id"] for a in next(x for x in entries if x["id"] == e["id"])["subagents"]) == ["a1", "a2", "a3", "a4"]
        m = await (await client.get(f"/v1/entries/{e['id']}/subagents/a2/messages", headers=h)).json()
        assert m["messages"][0]["message"]["content"] == "Read the feed handler"
        assert all(x["parent_tool_use_id"] is None for x in m["messages"])  # its own conversation in the view
        assert any("Check which videos lack dash_url" in json.dumps(x) for x in m["messages"])  # its nested Agent call
        await client.close()
    asyncio.run(t2())


def test_sessions_message_each_other_and_wake_a_stopped_one(tmp_path, monkeypatch):
    """The switchboard message_session tool reaches any tree session; a stopped one is resumed to receive it."""
    async def t():
        client, store, token, h, a, ws = await setup(tmp_path, monkeypatch)
        runner = client.server.app[appmod.RUNNER]
        b = await (await client.post("/v1/entries", headers=h, json={"session_id": "demo-3", "name": "Builder"})).json()
        assert b["id"] not in runner.live  # stopped

        await ws.send_json({"type": "send", "entry": a["id"], "text": "list sessions"})
        m, _ = await until(ws, lambda m: m["type"] == "message" and m["entry"] == a["id"] and "Builder" in str(m))
        assert "stopped (a message resumes it)" in str(m)
        while runner.state(a["id"]) != "ready":  # its ready event may already be behind us
            await asyncio.sleep(0.05)

        await ws.send_json({"type": "send", "entry": a["id"], "text": "tell builder: hello there"})
        _, seen = await until(ws, lambda m: m["type"] == "message" and m["entry"] == b["id"] and "Done:" in str(m), timeout=10)
        got = [m for m in seen if m["type"] == "message" and m["entry"] == b["id"] and m["message"]["type"] == "user"]
        assert 'Message from the session "' in str(got[0]) and "hello there" in str(got[0])  # announced to clients
        assert any(m["type"] == "message" and m["entry"] == a["id"] and "is resuming now" in str(m) for m in seen)
        assert b["id"] in runner.live
        assert not any(m["type"] == "inbox" for m in seen)  # not a watch: nothing lands in the inbox

        while runner.state(a["id"]) != "ready":  # its ready event may already be behind us
            await asyncio.sleep(0.05)
        await ws.send_json({"type": "send", "entry": a["id"], "text": "tell nobody: hi"})
        await until(ws, lambda m: m["type"] == "message" and 'No session named "nobody"' in str(m))

        # open in a terminal elsewhere: refused, pointing at SendMessage
        while runner.state(a["id"]) != "ready":  # its ready event may already be behind us
            await asyncio.sleep(0.05)
        await runner.stop(b["id"])
        monkeypatch.setattr(appmod, "open_in_terminal", lambda: {"demo-3"})
        await ws.send_json({"type": "send", "entry": a["id"], "text": "tell Builder: hi"})
        await until(ws, lambda m: m["type"] == "message" and "use SendMessage" in str(m))
        assert b["id"] not in runner.live
        await client.close()
    asyncio.run(t())


def test_old_unfinished_subagents_are_not_running(tmp_path, monkeypatch):
    """Older Claude Code never wrote `finished`: only subagents active since the session's process started count as running."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        now = lambda: int(time.time() * 1000)
        subs = lambda sid: [{"id": "old", "description": "47 days ago", "type": None, "parent": None, "tool_use_id": None, "finished": False, "stopped": False, "updated": now() - 47 * 86400_000},
                            {"id": "new", "description": "just now", "type": None, "parent": None, "tool_use_id": None, "finished": False, "stopped": False, "updated": now() + 1000},
                            {"id": "done", "description": "finished", "type": None, "parent": None, "tool_use_id": None, "finished": True, "stopped": False, "updated": now() + 1000}]
        monkeypatch.setattr(appmod, "subagent_tree", subs)
        running = lambda es: {a["id"]: a["running"] for a in es[0]["subagents"]}
        es = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert running(es) == {"old": False, "new": False, "done": False}  # not live: nothing runs
        await ws.send_json({"type": "send", "entry": e["id"], "text": "hi"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        es = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert running(es) == {"old": False, "new": True, "done": False}
        await client.close()
    asyncio.run(t())
