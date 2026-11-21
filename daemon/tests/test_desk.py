"""Phone notifications pause while the user is in a Switchboard desktop window."""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switchboard_daemon import push
from switchboard_daemon.store import Store

Q = {"kind": "question", "entry": "e1", "request_id": "r1", "title": "x needs you"}
DONE = {"kind": "finished", "entry": "e1", "title": "x finished"}


def test_away_everything_goes_through(tmp_path):
    d = push.Desk(Store(str(tmp_path / "s.db")), None)
    assert d.filter(Q) == Q and d.filter(DONE) == DONE


def test_at_desk_finished_dropped_questions_held_then_sent_after_leaving(tmp_path, monkeypatch):
    async def t():
        sent = []
        async def send(st, n): sent.append(n)
        d = push.Desk(Store(str(tmp_path / "s.db")), send)
        monkeypatch.setattr(push.Desk, "GRACE", 0.05)
        d.report("ws1", True)
        assert d.filter(DONE) is None and d.filter(Q) is None  # you're looking: nothing on the phone
        assert d.filter({"kind": "call", "entry": "e1"})  # a call always rings
        await asyncio.sleep(0.1)
        assert sent == []  # still here
        d.report("ws1", False)
        await asyncio.sleep(0.2)
        assert sent == [Q]  # left with it unanswered: now it goes to the phone
    asyncio.run(t())


def test_answered_at_desk_never_reaches_the_phone(tmp_path, monkeypatch):
    async def t():
        sent = []
        async def send(st, n): sent.append(n)
        d = push.Desk(Store(str(tmp_path / "s.db")), send)
        monkeypatch.setattr(push.Desk, "GRACE", 0.05)
        d.report("ws1", True)
        d.filter(Q)
        d.filter({"kind": "resolved", "entry": "e1", "request_id": "r1"})
        d.gone("ws1")  # window closed
        await asyncio.sleep(0.2)
        assert sent == []
    asyncio.run(t())


def test_report_runs_out_and_setting_turns_it_off(tmp_path):
    st = Store(str(tmp_path / "s.db"))
    d = push.Desk(st, None)
    d.report("ws1", True, now=1000)
    assert d.present(now=1000 + 30) and not d.present(now=1000 + push.Desk.TTL + 1)  # the app stopped reporting
    d.report("ws1", True)
    st.set_setting(push.PAUSE, "0")
    assert d.filter(DONE) == DONE
