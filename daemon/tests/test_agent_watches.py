"""Agents set up their own watches (the watch tool): only theirs, within limits, and the user hears about each one."""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import claude_agent_sdk as sdk
from switchboard_daemon.runner import Runner, AGENT_WATCHES
from switchboard_daemon.store import Store


def test_agents_watch_tool(tmp_path, monkeypatch):
    tools = {}
    monkeypatch.setattr(sdk, "create_sdk_mcp_server", lambda name, tools=(): {t.name: t for t in tools})
    store = Store(str(tmp_path / "s.db"))
    me = store.add_entry("sess-1", "emochi", "home", None, None, cwd="/tmp")
    other = store.add_entry("sess-2", "tube", "home", None, None, cwd="/tmp")
    events = []
    async def broadcast(ev): events.append(ev)
    runner = Runner(store, sdk, broadcast)
    tools.update(runner._tools(me["id"]))
    run = lambda **a: asyncio.run(tools["watch"].handler(a))
    text = lambda r: r["content"][0]["text"]

    r = run(action="add", name="feed check", kind="http", config={"url": "https://example.com", "every_minutes": 2})
    assert r.get("is_error") and "at least 5" in text(r)  # not too often: each wake costs a turn
    r = run(action="add", name="feed check", kind="http", config={"url": "https://example.com", "every_minutes": 10},
            instructions="Tell me what changed")
    assert not r.get("is_error"), text(r)
    w = next(x for x in store.watches() if x["name"] == "feed check")
    assert w["entry_id"] == me["id"] and w["action"] == "wake" and w["instructions"] == "Tell me what changed"
    assert events and events[-1]["type"] == "inbox" and "Set up a watch: feed check" in events[-1]["item"]["title"]  # the user sees it
    r = run(action="add", name="nightly", kind="schedule", config={"daily_at": "03:00"}, wake=False)
    assert not r.get("is_error") and next(x for x in store.watches() if x["name"] == "nightly")["action"] == "notify"
    assert run(action="add", name="bad", kind="github", config={"repo": "nope"}).get("is_error")  # validated as in the app
    # someone else's watch: not listed, can't be removed
    theirs = store.add_watch("tube's", "schedule", {"every_minutes": 60}, "wake", other["id"])
    listed = text(run(action="list"))
    assert "feed check" in listed and "nightly" in listed and "tube's" not in listed
    assert run(action="remove", id=theirs["id"]).get("is_error")
    assert run(action="pause", id=theirs["id"]).get("is_error")  # not theirs to pause either
    assert not run(action="pause", id=w["id"]).get("is_error") and store.watch(w["id"])["enabled"] is False
    assert "paused" in text(run(action="list"))
    # edit in place: same id, rescheduled; next run times in replies
    r = run(action="edit", id=w["id"], config={"url": "https://example.org", "every_minutes": 30}, instructions="Only outages")
    assert not r.get("is_error") and "Next:" in text(r), text(r)
    e2 = store.watch(w["id"])
    assert e2["config"]["url"] == "https://example.org" and e2["instructions"] == "Only outages" and e2["enabled"]
    assert run(action="edit", id=w["id"], config={"url": "https://x.org", "every_minutes": 1}).get("is_error")  # limits still apply
    assert run(action="edit", id=theirs["id"], name="mine now").get("is_error")
    r = run(action="add", name="one-off", kind="schedule", config={"run_at": "2099-01-01 22:00"})
    assert not r.get("is_error") and "first run" in text(r)
    assert "next:" in text(run(action="list"))
    run(action="remove", id=next(x for x in store.watches() if x["name"] == "one-off")["id"])
    assert not run(action="resume", id=w["id"]).get("is_error") and store.watch(w["id"])["enabled"] is True
    assert not run(action="remove", id=w["id"]).get("is_error") and all(x["id"] != w["id"] for x in store.watches())
    # nothing fits: the idea goes to the user's inbox, a few a day
    assert run(action="suggest").get("is_error")
    r = run(action="suggest", idea="Watch a Discord channel for new messages\nso I can answer support questions")
    assert not r.get("is_error") and events[-1]["item"]["title"] == "Watch idea: Watch a Discord channel for new messages"
    run(action="suggest", idea="two"); run(action="suggest", idea="three")
    assert "today" in text(run(action="suggest", idea="four"))
    # at most AGENT_WATCHES each
    for i in range(AGENT_WATCHES - 1):
        assert not run(action="add", name=f"w{i}", kind="schedule", config={"every_minutes": 60}).get("is_error")
    assert "already have" in text(run(action="add", name="one too many", kind="schedule", config={"every_minutes": 60}))


def test_unread_messages_follow_claude_codes_queue(tmp_path, monkeypatch):
    """The daemon mirrors Claude Code's queue from the transcript: queued messages stay unread (a clock in the app) until
    read mid-turn (remove) or taken as the next turn (dequeue); an interrupt (popAll) gives them back."""
    import json, time
    from types import SimpleNamespace
    from switchboard_daemon.runner import Live
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = tmp_path / "projects" / "-p"; d.mkdir(parents=True)
    f = d / "s1.jsonl"; f.write_text('{"type":"user"}\n')
    store = Store(str(tmp_path / "s.db"))
    events = []
    async def broadcast(ev): events.append(ev)
    runner = Runner(store, sdk, broadcast)
    lv = Live("e1", client=None)
    runner.live["e1"] = lv
    def add(*ops):
        with open(f, "a") as h:
            for op in ops: h.write(json.dumps({"type": "queue-operation", **op}) + "\n")
    async def t():
        task = asyncio.create_task(runner._watch_queue(lv, "s1"))
        await asyncio.sleep(0.9)  # it starts from the end of the file
        add({"operation": "enqueue", "content": "one"}, {"operation": "enqueue", "content": "two"}, {"operation": "enqueue", "content": "three"})
        await asyncio.sleep(1)
        assert runner.unread("e1") == ["one", "two", "three"] and events[-1]["items"] == ["one", "two", "three"]
        add({"operation": "enqueue", "content": "<task-notification>\n<task-id>a1</task-id>"}, {"operation": "enqueue"})  # Claude's own: never shown
        await asyncio.sleep(1)
        assert runner.unread("e1") == ["one", "two", "three"]
        add({"operation": "remove", "content": "two", "reason": "absorbed_mid_turn"})  # read between steps
        await asyncio.sleep(1)
        assert runner.unread("e1") == ["one", "three"]
        add({"operation": "dequeue"})  # taken as the next turn
        await asyncio.sleep(1)
        assert runner.unread("e1") == ["three"]
        # the user takes "four" back before Claude reads it: cancelled by uuid; Claude Code logs that as a dequeue
        calls = []
        async def control(req): calls.append(req); return {"cancelled": True}
        lv.client = SimpleNamespace(_query=SimpleNamespace(_send_control_request=control))
        add({"operation": "enqueue", "content": "four"}); lv.sent = [("four", "u4")]
        await asyncio.sleep(1)
        assert await runner.withdraw("e1", "three") is False  # not sent by this daemon mid-turn: nothing to cancel
        assert await runner.withdraw("e1", "four") is True and calls == [{"subtype": "cancel_async_message", "message_uuid": "u4"}]
        assert runner.unread("e1") == ["three"] and events[-1]["items"] == ["three"]
        add({"operation": "dequeue"})
        await asyncio.sleep(1)
        assert runner.unread("e1") == ["three"]  # the cancel's dequeue didn't take "three"
        add({"operation": "popAll", "content": "three"})  # interrupted: back to the user
        await asyncio.sleep(1)
        assert runner.unread("e1") == [] and events[-1]["dropped"] == ["three"]
        runner.live.pop("e1"); task.cancel()
    asyncio.run(t())


def test_sessions_are_found_by_any_of_their_names():
    """message_session: id, Switchboard name, Claude Code's own name or project, any capitalisation; else the closest."""
    from switchboard_daemon.runner import pick
    es = [{"id": "e1", "name": "Nuvola Support", "title": "Guides RIG", "group": "rig-guides"},
          {"id": "e2", "name": "Menus Import", "title": None, "group": "menus"},
          {"id": "e3", "name": "Menus Export", "title": None, "group": "menus"}]
    for want in ("e1", "nuvola support", "GUIDES rig", "rig-guides", "nuvola"):
        assert pick(es, want)[0]["id"] == "e1", want
    hit, why = pick(es, "menus")  # a project with two sessions: say which
    assert hit is None and '"Menus Import" (id e2)' in why and '"Menus Export" (id e3)' in why
    hit, why = pick(es, "Nuvla Suport")
    assert hit is None and 'Closest: "Nuvola Support"' in why
