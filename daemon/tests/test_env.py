"""What sessions inherit from the daemon's environment."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switchboard_daemon.runner import scrub_env


def test_children_get_a_clean_environment(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1"); monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/keep")
    # the bundled daemon: PyInstaller's loader pointed LD_LIBRARY_PATH at its own libraries
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIabc"); monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    scrub_env()
    assert "CLAUDECODE" not in os.environ and os.environ["CLAUDE_CONFIG_DIR"] == "/keep"
    assert "LD_LIBRARY_PATH" not in os.environ  # there was none before the loader: none for children
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIabc"); monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/mine")
    scrub_env()
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/mine" and "LD_LIBRARY_PATH_ORIG" not in os.environ  # the user's own, back


def test_not_bundled_leaves_it_alone(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/mine")
    scrub_env()
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/mine"


def test_sessions_get_the_login_shells_path_first(tmp_path):
    """A daemon run by systemd or the desktop app lacked what a terminal has on PATH (nvm's node: plugin hooks that run
    `node` quietly did nothing) and carried dead AppImage mounts. Sessions get the login shell's PATH, then the daemon's."""
    if os.name == "nt":
        return
    from switchboard_daemon.runner import login_path
    nvm, mine = tmp_path / "nvm-bin", tmp_path / "local-bin"
    nvm.mkdir(); mine.mkdir()
    sh = tmp_path / "fakeshell"  # an rc file that prints things, then the PATH a terminal would have
    sh.write_text(f'#!/bin/sh\necho "welcome back!"\nPATH="{nvm}:/usr/bin:/tmp/.mount_gone/usr/bin"\nexec /bin/sh -c "$2"\n')
    sh.chmod(0o755)
    got = login_path(shell=str(sh), current=f"/tmp/.mount_SwitchX/usr/bin:{mine}:/usr/bin")
    assert got == f"{nvm}:/usr/bin:{mine}"  # theirs first, no duplicates, folders that don't exist dropped
    assert login_path(shell="/nonexistent/shell", current=f"{mine}:/nope") == str(mine)  # no shell: own PATH, cleaned
