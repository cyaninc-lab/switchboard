"""Self-update of pip-installed (headless) daemons."""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from types import SimpleNamespace
from switchboard_daemon import update


def test_newer():
    assert update.newer("v0.1.26", "0.1.25") and update.newer("v0.2.0", "0.1.99") and update.newer("v0.1.10", "0.1.9")
    assert not update.newer("v0.1.25", "0.1.25") and not update.newer("v0.1.24", "0.1.25") and not update.newer("nightly", "0.1.25")


def test_waits_for_busy_sessions_then_installs_and_quits(monkeypatch):
    lv = SimpleNamespace(working=True, pending={}, queue=[])
    runner = SimpleNamespace(live={"e1": lv}, running_tasks=lambda lv: [])
    events = []
    async def latest(): return "v99.0.0"
    async def install(tag): events.append(("install", tag, lv.working)); return True
    async def nap(_):
        events.append("wait"); lv.working = False  # the turn ends while we wait
    monkeypatch.setattr(update, "latest", latest); monkeypatch.setattr(update, "install", install)
    monkeypatch.setattr(update.asyncio, "sleep", nap)
    assert asyncio.run(update.check(runner, quit=lambda: events.append("quit"))) == "v99.0.0"
    assert events == ["wait", ("install", "v99.0.0", False), "quit"]  # never installs mid-turn


def test_up_to_date_or_failed_install_keeps_running(monkeypatch):
    runner = SimpleNamespace(live={}, running_tasks=lambda lv: [])
    quits = []
    async def same(): return "v" + update.__version__
    monkeypatch.setattr(update, "latest", same)
    assert asyncio.run(update.check(runner, quit=lambda: quits.append(1))) is None
    async def new(): return "v99.0.0"
    async def broken(tag): return False
    monkeypatch.setattr(update, "latest", new); monkeypatch.setattr(update, "install", broken)
    assert asyncio.run(update.check(runner, quit=lambda: quits.append(1))) is None
    assert quits == []


def test_serve_starts_with_auto_update(tmp_path):
    """The daemon must come up with --auto-update (0.1.26 crashed: a later `import sys` in main() made sys local)."""
    import socket, subprocess, time
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "switchboard_daemon", "--data", str(tmp_path), "serve", "--port", str(port), "--auto-update"],
                         cwd=os.path.join(os.path.dirname(__file__), ".."), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline, out = time.time() + 20, ""
        while time.time() < deadline and "Running on" not in out and p.poll() is None:
            out += p.stdout.readline()
        assert "Running on" in out, out
    finally:
        p.terminate(); p.wait(10)
