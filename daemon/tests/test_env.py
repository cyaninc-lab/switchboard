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
