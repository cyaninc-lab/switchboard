"""When the app goes away, the daemon's Claude processes go with it (they'd otherwise hold their sessions)."""
import os, subprocess, sys, time
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows path is taskkill /T, checked on the work PC")
def test_children_die_with_the_daemon(tmp_path):
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # stands in for the desktop app
    # a group leader (as the app starts it) that spawns a long-lived child (as a session's Claude process), then watches the app
    script = ("import asyncio, subprocess, sys; from switchboard_daemon.__main__ import _watch\n"
              f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); open(r'{tmp_path}/child', 'w').write(str(c.pid))\n"
              f"async def m():\n    await _watch({app.pid})\n    await asyncio.sleep(30)\nasyncio.run(m())")
    d = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, process_group=0)
    for _ in range(50):
        if (tmp_path / "child").exists() and (tmp_path / "child").read_text():
            break
        time.sleep(0.1)
    child = int((tmp_path / "child").read_text())
    app.kill(); app.wait()
    d.wait(timeout=10)
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
