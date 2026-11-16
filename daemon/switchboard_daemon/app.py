"""HTTP API v1 (phase 1: pairing, machine, sessions, tree entries, history). See docs/protocol.md."""
import asyncio, dataclasses, glob, json, os, platform, socket
from pathlib import Path
from aiohttp import web
import claude_agent_sdk as sdk
from . import __version__
from .store import Store

STORE, NAME = web.AppKey("store", Store), web.AppKey("name", str)
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def _alive(pid, start):
    """True if pid is still the process that started at `start` (a reused pid doesn't count)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read().rsplit(")", 1)[1].split()
        return stat[0] != "Z" and stat[19] == str(start)
    except FileNotFoundError:
        if not os.path.exists("/proc/self"):  # ponytail: macOS/Windows get a bare pid check; port cs's GetProcessTimes for Windows if pid reuse bites
            try:
                os.kill(pid, 0) if os.name != "nt" else None
                return os.name != "nt"
            except OSError:
                return False
        return False
    except (OSError, IndexError):
        return False


def open_in_terminal():
    """{session id} of sessions a live Claude Code process holds (CLI, IDE, Remote Control)."""
    live = set()
    for path in glob.glob(str(CLAUDE_DIR / "sessions" / "*.json")):
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if _alive(d["pid"], d.get("procStart")):
                live.add(d["sessionId"])
        except (OSError, ValueError, KeyError):
            continue
    return live


def _default_group(cwd):
    p = Path(cwd or Path.home())
    return "home" if p == Path.home() or not p.name else p.name


def _plain(obj):
    return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj


async def _off(fn, *a, **kw):
    """The SDK's session readers are synchronous file scans: keep them off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a, **kw))


def _entry_view(entry, info, live):
    d = {k: entry[k] for k in ("id", "session_id", "name", "pinned", "account", "mode")}
    d["group"], d["pinned"] = entry["grp"], bool(entry["pinned"])
    d["state"] = "missing" if info is None else ("terminal" if entry["session_id"] in live else "stopped")
    d["session"] = _plain(info)
    return d


CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS"}


@web.middleware
async def cors(request, handler):
    """Clients are web UIs (the Tauri webview, later maybe a browser). Bearer-token auth, no cookies, so * is safe."""
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=CORS)
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        e.headers.update(CORS)
        raise
    resp.headers.update(CORS)
    return resp


@web.middleware
async def auth(request, handler):
    if request.path == "/v1/pair":
        return await handler(request)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or not request.app[STORE].client_for(token):
        raise web.HTTPUnauthorized(text=json.dumps({"code": "unauthorized"}), content_type="application/json")
    return await handler(request)


async def pair(request):
    body = await request.json()
    token = request.app[STORE].redeem(body.get("code"), body.get("client_name"))
    if not token:
        raise web.HTTPForbidden(text=json.dumps({"code": "bad_code"}), content_type="application/json")
    return web.json_response({"token": token, "machine": request.app[NAME]})


async def machine(request):
    return web.json_response({"name": request.app[NAME], "daemon": __version__, "sdk": sdk.__version__,
                              "os": platform.system(), "arch": platform.machine()})


async def sessions(request):
    q = request.query.get("q", "").lower()
    limit, offset = int(request.query.get("limit", 50)), int(request.query.get("offset", 0))
    found = await _off(sdk.list_sessions)
    if q:
        found = [s for s in found if q in (s.summary or "").lower() or q in (s.cwd or "").lower()]
    store, out = request.app[STORE], []
    for s in found[offset:offset + limit]:
        e = store.entry_by_session(s.session_id)
        out.append({**_plain(s), "entry_id": e["id"] if e else None})
    return web.json_response({"total": len(found), "sessions": out})


async def list_entries(request):
    live = await _off(open_in_terminal)
    out = []
    for e in request.app[STORE].entries():
        out.append(_entry_view(e, await _off(sdk.get_session_info, e["session_id"]), live))
    return web.json_response({"entries": out})


async def add_entry(request):
    body, store = await request.json(), request.app[STORE]
    if "session_id" not in body:
        raise web.HTTPNotImplemented(text=json.dumps({"code": "new_session_needs_phase_2"}), content_type="application/json")
    info = await _off(sdk.get_session_info, body["session_id"])
    if info is None:
        raise web.HTTPNotFound(text=json.dumps({"code": "no_such_session"}), content_type="application/json")
    if store.entry_by_session(info.session_id):
        raise web.HTTPConflict(text=json.dumps({"code": "already_in_tree"}), content_type="application/json")
    e = store.add_entry(info.session_id, body.get("name") or info.custom_title or info.summary or "Untitled",
                        body.get("group") or _default_group(info.cwd),
                        body.get("account"), body.get("mode"))
    return web.json_response(_entry_view(e, info, await _off(open_in_terminal)), status=201)


async def update_entry(request):
    body, store = await request.json(), request.app[STORE]
    if "group" in body:
        body["grp"] = body.pop("group")
    e = store.update_entry(request.match_info["id"], **body)
    if not e:
        raise web.HTTPNotFound()
    return web.json_response(_entry_view(e, await _off(sdk.get_session_info, e["session_id"]), await _off(open_in_terminal)))


async def remove_entry(request):
    if not request.app[STORE].remove_entry(request.match_info["id"]):
        raise web.HTTPNotFound()
    return web.Response(status=204)


async def messages(request):
    e = request.app[STORE].entry(request.match_info["id"])
    if not e:
        raise web.HTTPNotFound()
    limit, offset = int(request.query.get("limit", 200)), int(request.query.get("offset", 0))
    msgs = await _off(sdk.get_session_messages, e["session_id"], limit=limit, offset=offset)
    return web.json_response({"messages": [_plain(m) for m in msgs]}, dumps=lambda o: json.dumps(o, default=str))


def make_app(store: Store, name=None):
    app = web.Application(middlewares=[cors, auth])
    app[STORE], app[NAME] = store, name or socket.gethostname()
    app.add_routes([
        web.post("/v1/pair", pair), web.get("/v1/machine", machine), web.get("/v1/sessions", sessions),
        web.get("/v1/entries", list_entries), web.post("/v1/entries", add_entry),
        web.patch("/v1/entries/{id}", update_entry), web.delete("/v1/entries/{id}", remove_entry),
        web.get("/v1/entries/{id}/messages", messages),
    ])
    return app
