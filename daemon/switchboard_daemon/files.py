"""File names under a session's folder, for @-mentions in the message box (Claude Code expands "@path" itself)."""
import os, subprocess, time

SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build", "target", ".svelte-kit", ".next"}
LIMIT = 20000
_cache: dict[str, tuple[float, list[str]]] = {}  # folder -> (when, files); ponytail: 15 s, one folder's list per entry


def _list(cwd):
    try:  # a git checkout: tracked + untracked, minus what .gitignore hides
        out = subprocess.run(["git", "-C", cwd, "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:  # heavy folders stay out even when .gitignore doesn't name them
            return [f for f in out.stdout.splitlines() if not SKIP.intersection(f.split("/")[:-1])][:LIMIT]
    except (OSError, subprocess.TimeoutExpired):
        pass
    found = []
    for root, dirs, names in os.walk(cwd):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
        rel = os.path.relpath(root, cwd)
        found += [n if rel == "." else os.path.join(rel, n) for n in sorted(names)]
        if len(found) >= LIMIT:
            break
    return found[:LIMIT]


def search(cwd, q, limit=30):
    """Best matches first: the name starts with q, then the name contains it, then the path does."""
    hit = _cache.get(cwd)
    if not hit or time.time() - hit[0] > 15:
        hit = _cache[cwd] = (time.time(), [f.replace("\\", "/") for f in _list(cwd)])
    q = q.lower()
    def rank(f):
        name = f.rsplit("/", 1)[-1].lower()
        return 0 if name.startswith(q) else 1 if q in name else 2 if q in f.lower() else None
    ranked = [(r, len(f), f) for f in hit[1] if (r := rank(f)) is not None]
    return [f for _, _, f in sorted(ranked)[:limit]]
