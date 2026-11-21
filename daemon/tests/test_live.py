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


def test_message_while_working_goes_straight_in(tmp_path, monkeypatch):
    """Like Claude Code: a message sent while Claude works goes to the CLI at once (it reads it after its current
    step), not held until the turn ends. Clients are told it's sent right away."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid = e["id"]
        await ws.send_json({"type": "send", "entry": eid, "text": "first"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "working")
        await ws.send_json({"type": "send", "entry": eid, "text": "second"})
        q, seen = await until(ws, lambda m: m["type"] == "queued")
        assert q["count"] == 0 and q["sent"] == 1
        assert not any("Done: first" in str(m) for m in seen)  # before the first turn ended
        _, seen = await until(ws, lambda m: m["type"] == "message" and "Done: second" in str(m))
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
        env = runner.live[eid].client.options.env  # the account's token, and claude.ai Artifacts on (off by default in SDK sessions)
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-secret" and env["CLAUDE_CODE_ARTIFACT"] == "1"
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
        assert req["kind"] == "question" and req["input"]["questions"][0]["header"] == "DB choice" and req["remember"] == []
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


def test_session_options(tmp_path, monkeypatch):
    """A Read of a big screenshot is one multi-MB line from Claude Code; the SDK's 1 MB default killed the session."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        runner = client.server.app[appmod.RUNNER]
        await ws.send_json({"type": "send", "entry": e["id"], "text": "hi"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        assert runner.live[e["id"]].client.options.max_buffer_size >= 32 * 1024 * 1024
        assert "chrome" in runner.live[e["id"]].client.options.extra_args  # Claude in Chrome, as in the terminal
        await client.close()
    asyncio.run(t())


def test_send_now_interrupts_and_sends_first(tmp_path, monkeypatch):
    """Ctrl+Enter (send now): the running turn is interrupted and this message is sent."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        await ws.send_json({"type": "send", "entry": e["id"], "text": "a slow job"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "working")
        await ws.send_json({"type": "send", "entry": e["id"], "text": "jump the queue", "now": True})
        _, seen = await until(ws, lambda m: m["type"] == "message" and "Done: jump the queue" in str(m), timeout=8)
        assert not any("Done: a slow job" in str(m) for m in seen)  # the slow job was cut short
        await client.close()
    asyncio.run(t())


def test_interrupting_is_not_an_error(tmp_path, monkeypatch):
    """Claude Code ends an interrupted turn as an error with no message; that was shown as an error saying "None"."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        await ws.send_json({"type": "send", "entry": e["id"], "text": "a slow job"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "working")
        await ws.send_json({"type": "interrupt", "entry": e["id"]})
        _, seen = await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready", timeout=5)
        assert not [m for m in seen if m["type"] == "error"]
        await client.close()
    asyncio.run(t())


def test_message_while_waiting_on_a_prompt(tmp_path, monkeypatch):
    """Sent while Claude waits for a permission answer: it goes in at once too, and is answered after."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        await ws.send_json({"type": "send", "entry": e["id"], "text": "please run the checks"})
        req, _ = await until(ws, lambda m: m["type"] == "permission_request")
        await ws.send_json({"type": "send", "entry": e["id"], "text": "next"})
        await until(ws, lambda m: m["type"] == "queued" and m["count"] == 0 and m.get("sent") == 1)
        await ws.send_json({"type": "permission_reply", "request_id": req["request_id"], "allow": True})
        await until(ws, lambda m: m["type"] == "message" and "Done: next" in str(m))
        await client.close()
    asyncio.run(t())


def test_idle_close_is_opt_in(tmp_path, monkeypatch):
    """Off by default; when set, a quiet session closes, but never one working, waiting or with a background task."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid, runner = e["id"], client.app[appmod.RUNNER]
        assert (await (await client.get("/v1/settings", headers=h)).json())["idle_close_minutes"] == 0
        await ws.send_json({"type": "send", "entry": eid, "text": "hello"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        later = time.time() + 10 * 3600
        await runner.reap_idle(later)
        assert eid in runner.live  # off: stays open however long it's quiet
        assert (await client.put("/v1/settings", headers=h, json={"idle_close_minutes": 7})).status == 400
        r = await client.put("/v1/settings", headers=h, json={"idle_close_minutes": 10})
        assert (await r.json())["idle_close_minutes"] == 10
        await runner.reap_idle(time.time() + 5 * 60)
        assert eid in runner.live  # not quiet for long enough
        runner.live[eid].tasks["bg"] = {"id": "bg", "status": "running", "started": 0}
        await runner.reap_idle(later)
        assert eid in runner.live  # a background task keeps it open
        runner.live[eid].tasks.clear()
        await runner.reap_idle(later)
        assert eid not in runner.live
        await until(ws, lambda m: m["type"] == "state" and m["entry"] == eid and m["state"] not in ("ready", "working"))
        # it resumes on the next message
        await ws.send_json({"type": "send", "entry": eid, "text": "back again"})
        await until(ws, lambda m: m["type"] == "message" and "Done: back again" in str(m))
        await client.close()
    asyncio.run(t())


def test_background_tasks_shown_and_stoppable(tmp_path, monkeypatch):
    """Background tasks reach the apps with what they are and how they ended; one can be stopped."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid, runner = e["id"], client.app[appmod.RUNNER]
        monkeypatch.setenv("SB_DEMO_BG_SECONDS", "1")
        await ws.send_json({"type": "send", "entry": eid, "text": "start the long background build"})
        m, _ = await until(ws, lambda m: m["type"] == "tasks" and len(m["tasks"]) == 2)
        assert [(x["id"], x["description"], x["status"]) for x in m["tasks"]] == [("bg1", "npm run build", "running"), ("bg2", "npm run dev", "running")]
        # the entry carries them too (an app opening later)
        es = (await (await client.get("/v1/entries", headers=h)).json())["entries"]
        assert len(next(x for x in es if x["id"] == eid)["tasks"]) == 2
        # bg1 finishes by itself, with Claude Code's summary and output file
        m, _ = await until(ws, lambda m: m["type"] == "tasks" and any(x["id"] == "bg1" and x["status"] == "completed" for x in m["tasks"]))
        bg1 = next(x for x in m["tasks"] if x["id"] == "bg1")
        assert bg1["summary"] == "Build finished, 0 warnings" and bg1["output_file"] == "/tmp/demo-bg1.output" and bg1["ended"]
        assert len(runner.running_tasks(runner.live[eid])) == 1  # bg2 still keeps it from idle-closing
        # stop bg2 from the app
        await ws.send_json({"type": "stop_task", "entry": eid, "task_id": "bg2"})
        m, _ = await until(ws, lambda m: m["type"] == "tasks" and any(x["id"] == "bg2" and x["status"] == "stopped" for x in m["tasks"]))
        assert runner.running_tasks(runner.live[eid]) == []
        await ws.send_json({"type": "stop_task", "entry": eid, "task_id": "nope"})
        await until(ws, lambda m: m["type"] == "error")
        # closing the session clears them
        await ws.send_json({"type": "stop", "entry": eid})
        await until(ws, lambda m: m["type"] == "tasks" and m["tasks"] == [])
        await client.close()
    asyncio.run(t())


def test_project_order_setting(tmp_path, monkeypatch):
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        assert (await (await client.get("/v1/settings", headers=h)).json())["project_order"] == []
        assert (await client.put("/v1/settings", headers=h, json={"project_order": "nope"})).status == 400
        assert (await client.put("/v1/settings", headers=h, json={"project_order": [1, 2]})).status == 400
        r = await (await client.put("/v1/settings", headers=h, json={"project_order": ["b", "a"]})).json()
        assert r["project_order"] == ["b", "a"] and r["idle_close_minutes"] == 0
        await until(ws, lambda m: m["type"] == "settings")  # other clients hear about it
        assert (await (await client.get("/v1/settings", headers=h)).json())["project_order"] == ["b", "a"]
        await client.close()
    asyncio.run(t())


def test_files_for_at_mentions(tmp_path, monkeypatch):
    """Files in the session's folder, best matches first; heavy folders skipped, git's ignore rules honoured."""
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        d = tmp_path / "proj"
        (d / "src").mkdir(parents=True); (d / "node_modules" / "x").mkdir(parents=True)
        for f in ["README.md", "src/readme_helper.py", "src/app.py", "node_modules/x/readme.md", "notes/old-readme.txt"]:
            (d / f).parent.mkdir(parents=True, exist_ok=True); (d / f).write_text("x")
        new = await (await client.post("/v1/entries", headers=h, json={"new": {"cwd": str(d)}, "name": "files"})).json()
        got = (await (await client.get(f"/v1/entries/{new['id']}/files?q=readme", headers=h)).json())["files"]
        assert got == ["README.md", "src/readme_helper.py", "notes/old-readme.txt"]  # name starts with > name contains; node_modules skipped
        assert "src/app.py" in (await (await client.get(f"/v1/entries/{new['id']}/files?q=", headers=h)).json())["files"]
        # a git checkout: .gitignore'd files stay out
        import subprocess
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        (d / ".gitignore").write_text("notes/\n")
        from switchboard_daemon import files as filesmod
        filesmod._cache.clear()
        got = (await (await client.get(f"/v1/entries/{new['id']}/files?q=readme", headers=h)).json())["files"]
        assert got == ["README.md", "src/readme_helper.py"]
        await client.close()
    asyncio.run(t())


def test_remove_from_tree_or_delete_for_good(tmp_path, monkeypatch):
    async def t():
        monkeypatch.setattr(demo, "SESSIONS", list(demo.SESSIONS))  # this test deletes one; others need them all
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        listed = lambda: client.get("/v1/sessions", headers=h)
        sid = e["session_id"]
        # off the tree only: the session stays on the machine, and can be added again
        assert (await client.delete(f"/v1/entries/{e['id']}", headers=h)).status == 204
        assert any(s["session_id"] == sid for s in (await (await listed()).json())["sessions"])
        e2 = await (await client.post("/v1/entries", headers=h, json={"session_id": sid})).json()
        # for good, but it's open in a terminal elsewhere: refused, nothing changes
        monkeypatch.setattr(appmod, "open_in_terminal", lambda: {sid})
        r = await client.delete(f"/v1/entries/{e2['id']}?forever=1", headers=h)
        assert r.status == 409 and (await r.json())["code"] == "open_elsewhere"
        assert store.entry(e2["id"])
        # for good: gone from the tree and from the machine
        monkeypatch.setattr(appmod, "open_in_terminal", lambda: set())
        assert (await client.delete(f"/v1/entries/{e2['id']}?forever=1", headers=h)).status == 204
        assert not store.entry(e2["id"])
        assert not any(s["session_id"] == sid for s in (await (await listed()).json())["sessions"])
        # a new session that never ran has nothing on disk: deleting it just takes it off
        new = await (await client.post("/v1/entries", headers=h, json={"new": {"cwd": str(tmp_path)}})).json()
        assert (await client.delete(f"/v1/entries/{new['id']}?forever=1", headers=h)).status == 204
        await client.close()
    asyncio.run(t())


def test_clear_moves_the_entry_to_the_new_session(tmp_path, monkeypatch):
    """/clear starts a new session (real Claude Code does): the entry follows it, so history and a later resume
    are the new conversation, not the cleared one."""
    async def t():
        monkeypatch.setattr(demo, "SESSIONS", list(demo.SESSIONS))
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid, old = e["id"], e["session_id"]
        await ws.send_json({"type": "send", "entry": eid, "text": "remember magenta"})
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        await ws.send_json({"type": "send", "entry": eid, "text": "/clear"})
        m, _ = await until(ws, lambda m: m["type"] == "session_reset")
        new = m["session_id"]
        assert m["entry"] == eid and new != old and store.entry(eid)["session_id"] == new
        await until(ws, lambda m: m["type"] == "state" and m["state"] == "ready")
        await ws.send_json({"type": "send", "entry": eid, "text": "after the clear"})
        await until(ws, lambda m: m["type"] == "message" and "Done: after the clear" in str(m))
        hist = str(await (await client.get(f"/v1/entries/{eid}/messages", headers=h)).json())
        assert "after the clear" in hist and "remember magenta" not in hist  # the new conversation, not the old
        # stop and resume: it resumes the new session
        await ws.send_json({"type": "stop", "entry": eid})
        await until(ws, lambda m: m["type"] == "state" and m["entry"] == eid and m["state"] not in ("ready", "working"))
        await ws.send_json({"type": "send", "entry": eid, "text": "still here"})
        await until(ws, lambda m: m["type"] == "message" and "Done: still here" in str(m))
        assert client.app[appmod.RUNNER].live[eid].client.options.resume == new
        await client.close()
    asyncio.run(t())


def test_github_device_sign_in_and_issue(tmp_path, monkeypatch):
    """Device sign-in against a stand-in GitHub, then an issue filed as the person; signing out forgets the token."""
    from aiohttp import web as aweb
    from switchboard_daemon import github
    seen = {"polls": 0, "issue": None}
    async def device_code(r):
        assert (await r.post())["client_id"] == github.CLIENT_ID
        return aweb.json_response({"device_code": "dc1", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5})
    async def access_token(r):
        seen["polls"] += 1
        return aweb.json_response({"error": "authorization_pending"} if seen["polls"] == 1 else {"access_token": "gho_test", "token_type": "bearer"})
    async def user(r):
        assert r.headers["Authorization"] == "Bearer gho_test"
        return aweb.json_response({"login": "someone"})
    async def issues(r):
        assert r.headers["Authorization"] == "Bearer gho_test"
        seen["issue"] = await r.json()
        return aweb.json_response({"html_url": "https://github.com/cyaninc-lab/switchboard/issues/99", "number": 99}, status=201)
    async def t():
        gh = aweb.Application()
        gh.router.add_post("/login/device/code", device_code); gh.router.add_post("/login/oauth/access_token", access_token)
        gh.router.add_get("/user", user); gh.router.add_post("/repos/cyaninc-lab/switchboard/issues", issues)
        fake = TestServer(gh); await fake.start_server()
        monkeypatch.setattr(github, "WEB", str(fake.make_url("")).rstrip("/")); monkeypatch.setattr(github, "API", str(fake.make_url("")).rstrip("/"))
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        assert (await (await client.get("/v1/github", headers=h)).json()) == {"signed_in": False, "login": None}
        assert (await client.post("/v1/github/issue", headers=h, json={"title": "x", "body": "y"})).status == 401
        d = await (await client.post("/v1/github/device", headers=h)).json()
        assert d["user_code"] == "ABCD-1234"
        assert (await (await client.post("/v1/github/poll", headers=h, json={"device_code": d["device_code"]})).json())["status"] == "pending"
        assert (await (await client.post("/v1/github/poll", headers=h, json={"device_code": d["device_code"]})).json()) == {"status": "done", "login": "someone"}
        assert (await (await client.get("/v1/github", headers=h)).json()) == {"signed_in": True, "login": "someone"}
        r = await (await client.post("/v1/github/issue", headers=h, json={"title": "Crash on start", "body": "x" * 70000})).json()
        assert r == {"url": "https://github.com/cyaninc-lab/switchboard/issues/99", "number": 99}
        assert seen["issue"]["title"] == "Crash on start" and seen["issue"]["labels"] == ["bug"] and len(seen["issue"]["body"]) < 65536
        await client.delete("/v1/github", headers=h)
        assert (await (await client.get("/v1/github", headers=h)).json())["signed_in"] is False
        await client.close(); await fake.close()
    asyncio.run(t())


def test_new_session_defaults(tmp_path, monkeypatch):
    """Defaults apply to sessions added after they're set (new or from disk), not to ones already there; a request's own choice wins."""
    async def t():
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
        (tmp_path / "cc").mkdir()
        (tmp_path / "cc" / "settings.json").write_text('{"permissions": {"defaultMode": "acceptEdits"}}')
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        st = await (await client.get("/v1/settings", headers=h)).json()
        assert st["new_session"] == {} and st["claude_default_mode"] == "acceptEdits"
        assert (await client.put("/v1/settings", headers=h, json={"new_session": {"mode": "yolo"}})).status == 400
        assert (await client.put("/v1/settings", headers=h, json={"new_session": {"account": "nobody"}})).status == 400
        r = await (await client.put("/v1/settings", headers=h, json={"new_session": {"mode": "plan", "model": "haiku", "effort": "low", "account": None}})).json()
        assert r["new_session"] == {"mode": "plan", "model": "haiku", "effort": "low"}
        new = await (await client.post("/v1/entries", headers=h, json={"new": {"cwd": str(tmp_path)}})).json()
        assert (new["mode"], new["model"], new["effort"]) == ("plan", "haiku", "low")
        mine = await (await client.post("/v1/entries", headers=h, json={"new": {"cwd": str(tmp_path)}, "mode": "auto"})).json()
        assert mine["mode"] == "auto" and mine["model"] == "haiku"  # its own choice wins, the rest from defaults
        added = await (await client.post("/v1/entries", headers=h, json={"session_id": "demo-2"})).json()
        assert (added["mode"], added["model"], added["effort"]) == ("plan", "haiku", "low")
        assert store.entry(e["id"])["mode"] is None  # the session added before keeps its own
        await client.close()
    asyncio.run(t())


def test_removed_list_restore_and_add_again(tmp_path, monkeypatch):
    """Remove keeps a session's name, project and settings in a Removed list: restore it, or add it again (it comes back
    as it was), or delete it for good from there."""
    async def t():
        monkeypatch.setattr(demo, "SESSIONS", list(demo.SESSIONS))
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        await client.patch(f"/v1/entries/{e['id']}", headers=h, json={"name": "My deploy", "group": "ops", "mode": "plan"})
        assert (await client.delete(f"/v1/entries/{e['id']}", headers=h)).status == 204
        assert (await (await client.get("/v1/entries", headers=h)).json())["entries"] == []
        gone = (await (await client.get("/v1/entries/removed", headers=h)).json())["entries"]
        assert [(x["name"], x["group"], x["mode"]) for x in gone] == [("My deploy", "ops", "plan")] and gone[0]["removed"]
        sessions = (await (await client.get("/v1/sessions", headers=h)).json())["sessions"]
        assert next(x for x in sessions if x["session_id"] == e["session_id"])["entry_id"] is None  # addable again
        # restore: back in the tree as it was
        r = await (await client.post(f"/v1/entries/{e['id']}/restore", headers=h)).json()
        assert (r["id"], r["name"], r["group"], r["mode"]) == (e["id"], "My deploy", "ops", "plan")
        # remove, then add it again with + Add: same entry, same settings
        await client.delete(f"/v1/entries/{e['id']}", headers=h)
        r = await client.post("/v1/entries", headers=h, json={"session_id": e["session_id"]})
        again = await r.json()
        assert r.status == 200 and (again["id"], again["name"], again["mode"]) == (e["id"], "My deploy", "plan")
        assert again["group"] != "ops"  # + Add's project: the folder's, as for any add (Restore keeps the old one)
        await client.delete(f"/v1/entries/{e['id']}", headers=h)
        again = await (await client.post("/v1/entries", headers=h, json={"session_id": e["session_id"], "name": "Renamed on add", "group": "new home"})).json()
        assert (again["name"], again["group"], again["mode"]) == ("Renamed on add", "new home", "plan")  # picked now wins; settings kept
        # delete for good from the Removed list
        await client.delete(f"/v1/entries/{e['id']}", headers=h)
        assert (await client.delete(f"/v1/entries/{e['id']}", headers=h)).status == 404  # already removed
        assert (await client.delete(f"/v1/entries/{e['id']}?forever=1", headers=h)).status == 204
        assert (await (await client.get("/v1/entries/removed", headers=h)).json())["entries"] == []
        assert store.entry(e["id"]) is None
        await client.close()
    asyncio.run(t())


def test_btw_side_chat(tmp_path, monkeypatch):
    """/btw: a side chat on a fork of the session. Answers stream back; the session's own history never sees it; the
    fork stays out of the sessions list and is deleted when the side chat closes."""
    async def t():
        monkeypatch.setattr(demo, "SESSIONS", list(demo.SESSIONS))
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        eid, runner = e["id"], client.app[appmod.RUNNER]
        await ws.send_json({"type": "btw", "entry": eid, "text": "which file has the bug?"})
        m, seen = await until(ws, lambda m: m["type"] == "btw" and m["kind"] == "done")
        assert [x["kind"] for x in seen if x["type"] == "btw"][0] == "asked"
        assert any(x["type"] == "btw" and x["kind"] == "answer" and "Side answer: which file has the bug?" in x["text"] for x in seen)
        assert not any(x["type"] == "message" for x in seen)  # nothing reached the session's own chat
        fork = runner.side[eid].fork
        sessions = (await (await client.get("/v1/sessions", headers=h)).json())["sessions"]
        assert all(s["session_id"] != fork for s in sessions)  # not offered in + Add
        hist = str(await (await client.get(f"/v1/entries/{eid}/messages", headers=h)).json())
        assert "which file has the bug" not in hist  # the real history is untouched
        # a follow-up uses the same side chat
        await ws.send_json({"type": "btw", "entry": eid, "text": "and why?"})
        await until(ws, lambda m: m["type"] == "btw" and m["kind"] == "done")
        assert runner.side[eid].fork == fork
        # close: the fork is deleted
        await ws.send_json({"type": "btw_close", "entry": eid})
        await until(ws, lambda m: m["type"] == "btw" and m["kind"] == "closed")
        assert eid not in runner.side and demo.get_session_info(fork) is None
        # a brand-new session has nothing to ask about yet
        new = await (await client.post("/v1/entries", headers=h, json={"new": {"cwd": str(tmp_path)}})).json()
        await ws.send_json({"type": "btw", "entry": new["id"], "text": "hi"})
        m, _ = await until(ws, lambda m: m["type"] == "error")
        assert "nothing_to_ask_about" in str(m)
        await client.close()
    asyncio.run(t())


def test_daemon_serves_the_web_app(tmp_path, monkeypatch):
    """Phones and other browsers open the app from the daemon's address: its files are public, the API isn't."""
    web_dir = tmp_path / "webui"; (web_dir / "_app" / "immutable").mkdir(parents=True)
    (web_dir / "index.html").write_text("<title>Switchboard</title>")
    (web_dir / "_app" / "immutable" / "a.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setenv("SWITCHBOARD_WEBUI", str(web_dir))
    async def t():
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        r = await client.get("/")
        assert r.status == 200 and "<title>Switchboard</title>" in await r.text() and r.headers["Cache-Control"] == "no-cache"
        r = await client.get("/_app/immutable/a.js")
        assert await r.text() == "console.log(1)" and "immutable" in r.headers["Cache-Control"]
        assert "<title>Switchboard</title>" in await (await client.get("/some/app/route")).text()  # single page: its routes
        assert "nope" not in await (await client.get("/../secret.txt")).text()
        assert "nope" not in await (await client.get("/%2e%2e/secret.txt")).text()
        assert (await client.get("/v1/entries")).status == 401  # the API still needs a paired token
        assert (await client.get("/v1/entries", headers=h)).status == 200
        await client.close()
    asyncio.run(t())
