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
