#!/usr/bin/env python3
"""Write the version in VERSION into every place that carries it (run before a release build)."""
import json, pathlib, re

root = pathlib.Path(__file__).resolve().parent.parent
v = (root / "VERSION").read_text().strip()
assert re.fullmatch(r"\d+\.\d+\.\d+", v), f"VERSION must be x.y.z, got {v!r}"
for rel in ("desktop/src-tauri/tauri.conf.json", "desktop/package.json"):
    p = root / rel
    d = json.loads(p.read_text())
    d["version"] = v
    p.write_text(json.dumps(d, indent=2) + "\n")
for rel in ("desktop/src-tauri/Cargo.toml", "daemon/pyproject.toml"):
    p = root / rel
    p.write_text(re.sub(r'(?m)^version = "[^"]+"', f'version = "{v}"', p.read_text(), count=1))
(root / "daemon/switchboard_daemon/__init__.py").write_text(f'__version__ = "{v}"\n')
print("stamped", v)
