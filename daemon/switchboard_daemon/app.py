"""HTTP API v1 (phase 1: pairing, machine, sessions, tree entries, history). See docs/protocol.md."""
import asyncio, dataclasses, glob, itertools, json, os, platform, socket, uuid
from pathlib import Path
from aiohttp import web
import claude_agent_sdk as sdk
from . import __version__
from .store import Store
from .runner import Runner, scrub_env
from .tty import Terminals

STORE, NAME = web.AppKey("store", Store), web.AppKey("name", str)
RUNNER, SOCKETS, REAPER = web.AppKey("runner", Runner), web.AppKey("sockets", set), web.AppKey("reaper", asyncio.Task)
TTYS = web.AppKey("ttys", Terminals)
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


async def _live(request):
    """Sessions a Claude Code process holds, minus the ones this daemon holds itself."""
    return await _off(open_in_terminal) - request.app[RUNNER].held_sessions() - request.app[TTYS].held_sessions()


async def _off(fn, *a, **kw):
    """The SDK's session readers are synchronous file scans: keep them off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a, **kw))


def _entry_view(entry, info, live, runner=None):
    d = {k: entry.get(k) for k in ("id", "session_id", "name", "pinned", "account", "mode", "model")}
    d["group"], d["pinned"] = entry["grp"], bool(entry["pinned"])
    held = runner.state(entry["id"]) if runner else None
    if info is None and entry.get("cwd"):  # created here, no message yet
        d["state"] = held or "new"
        d["session"] = {"session_id": entry["session_id"], "summary": entry["name"], "cwd": entry["cwd"], "git_branch": None,
                        "last_modified": int(entry["created"] * 1000), "custom_title": None}
        return d
    d["state"] = held or ("missing" if info is None else ("terminal" if entry["session_id"] in live else "stopped"))
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
    if request.path in ("/v1/pair", "/v1/ws"):  # /v1/ws authenticates with its first message
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
    live, runner = await _live(request), request.app[RUNNER]
    out = []
    for e in request.app[STORE].entries():
        out.append(_entry_view(e, await _off(sdk.get_session_info, e["session_id"]), live, runner))
    return web.json_response({"entries": out})


async def add_entry(request):
    body, store = await request.json(), request.app[STORE]
    if "new" in body:
        cwd = os.path.abspath(os.path.expanduser(body["new"].get("cwd") or "~"))
        if not os.path.isdir(cwd):
            raise web.HTTPBadRequest(text=json.dumps({"code": "no_such_folder"}), content_type="application/json")
        e = store.add_entry(str(uuid.uuid4()), body.get("name") or "New session", body.get("group") or _default_group(cwd),
                            body.get("account"), body.get("mode"), cwd=cwd, model=body.get("model") or None)
        return web.json_response(_entry_view(e, None, set(), request.app[RUNNER]), status=201)
    if "session_id" not in body:
        raise web.HTTPBadRequest(text=json.dumps({"code": "session_id_or_new"}), content_type="application/json")
    info = await _off(sdk.get_session_info, body["session_id"])
    if info is None:
        raise web.HTTPNotFound(text=json.dumps({"code": "no_such_session"}), content_type="application/json")
    if store.entry_by_session(info.session_id):
        raise web.HTTPConflict(text=json.dumps({"code": "already_in_tree"}), content_type="application/json")
    e = store.add_entry(info.session_id, body.get("name") or info.custom_title or info.summary or "Untitled",
                        body.get("group") or _default_group(info.cwd),
                        body.get("account") or None, body.get("mode"))
    return web.json_response(_entry_view(e, info, await _live(request), request.app[RUNNER]), status=201)


async def update_entry(request):
    body, store = await request.json(), request.app[STORE]
    if "group" in body:
        body["grp"] = body.pop("group")
    e = store.update_entry(request.match_info["id"], **body)
    if not e:
        raise web.HTTPNotFound()
    return web.json_response(_entry_view(e, await _off(sdk.get_session_info, e["session_id"]), await _live(request), request.app[RUNNER]))


async def remove_entry(request):
    await request.app[RUNNER].stop(request.match_info["id"])
    if not request.app[STORE].remove_entry(request.match_info["id"]):
        raise web.HTTPNotFound()
    return web.Response(status=204)


async def list_accounts(request):
    return web.json_response({"accounts": request.app[STORE].accounts()})


async def add_account(request):
    body = await request.json()
    try:
        request.app[STORE].add_account(body.get("name", ""), body.get("token", ""), body.get("kind", "oauth"))
    except ValueError as e:
        raise web.HTTPBadRequest(text=json.dumps({"code": str(e)}), content_type="application/json")
    return web.json_response({"accounts": request.app[STORE].accounts()}, status=201)  # the token is never sent back


async def remove_account(request):
    name = request.match_info["name"]
    for e in request.app[STORE].entries():  # sessions on that account close; they fall back to the machine login
        if e["account"] == name:
            await request.app[RUNNER].stop(e["id"])
    if not request.app[STORE].remove_account(name):
        raise web.HTTPNotFound()
    return web.Response(status=204)


async def models(request):
    try:
        return web.json_response({"models": await request.app[RUNNER].models()})
    except Exception as e:  # e.g. no claude installed: the picker just stays on "default"
        return web.json_response({"models": [], "error": str(e)[:200]})


async def folders(request):
    """Folder suggestions for a new session: where sessions on this machine have run, most recent first."""
    seen, out = set(), []
    for s in await _off(sdk.list_sessions):
        if s.cwd and s.cwd not in seen and os.path.isdir(s.cwd) and not s.cwd.startswith("/tmp/"):
            seen.add(s.cwd)
            out.append(s.cwd)
    home = str(Path.home())
    return web.json_response({"home": home, "folders": ([home] if home not in seen else []) + out[:30]})


async def messages(request):
    e = request.app[STORE].entry(request.match_info["id"])
    if not e:
        raise web.HTTPNotFound()
    limit, offset = int(request.query.get("limit", 200)), int(request.query.get("offset", 0))
    if e.get("cwd") and await _off(sdk.get_session_info, e["session_id"]) is None:
        return web.json_response({"messages": []})  # new, nothing said yet
    msgs = await _off(sdk.get_session_messages, e["session_id"], limit=limit, offset=offset)
    return web.json_response({"messages": [_plain(m) for m in msgs]}, dumps=lambda o: json.dumps(o, default=str))


async def ws_handler(request):
    """One socket per client. First message must be {"type":"auth","token":...} (browsers can't set headers on a WebSocket)."""
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=80 * 1024 * 1024)  # image attachments
    await ws.prepare(request)
    store, runner = request.app[STORE], request.app[RUNNER]
    try:
        first = await ws.receive_json(timeout=10)
    except Exception:
        await ws.close(code=4001, message=b"auth")
        return ws
    client = store.client_for(first.get("token", "")) if first.get("type") == "auth" else None
    if not client:
        await ws.close(code=4001, message=b"unauthorized")
        return ws
    request.app[SOCKETS].add(ws)
    await ws.send_json({"type": "hello", "machine": request.app[NAME]})
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                m = json.loads(msg.data)
                t, eid = m.get("type"), m.get("entry")
                entry = store.entry(eid) if eid else None
                if t in ("send", "interrupt", "stop", "set_mode", "set_model", "set_account") or t.startswith("terminal_"):
                    if not entry:
                        raise LookupError("no_such_entry")
                if t == "terminal_open":
                    await runner.stop(eid)  # one writer per session: chat closes while the TUI has it
                    info = await _off(sdk.get_session_info, entry["session_id"])
                    argv = [runner.cli_path or "claude", "--resume" if info else "--session-id", entry["session_id"]]
                    if os.environ.get("SWITCHBOARD_MODEL"):
                        argv += ["--model", os.environ["SWITCHBOARD_MODEL"]]
                    env = store.account_env(entry["account"]) if entry.get("account") else None
                    await request.app[TTYS].start(eid, entry["session_id"], info.cwd if info else entry.get("cwd"), argv,
                                                  m.get("cols", 100), m.get("rows", 30), env)
                elif t == "terminal_input":
                    request.app[TTYS].write(eid, m["data"])
                elif t == "terminal_resize":
                    request.app[TTYS].resize(eid, m["cols"], m["rows"])
                elif t == "terminal_close":
                    await request.app[TTYS].close(eid)
                elif t == "send":
                    if entry["session_id"] in await _off(open_in_terminal) - runner.held_sessions():
                        raise RuntimeError("open_in_terminal")
                    await runner.send(entry, m.get("text", ""), m.get("images"))
                elif t == "interrupt":
                    await runner.interrupt(eid)
                elif t == "stop":
                    await runner.stop(eid)
                elif t == "set_mode":
                    await runner.set_mode(eid, m["mode"])
                    await _broadcast(request.app, {"type": "mode", "entry": eid, "mode": m["mode"]})
                elif t == "set_account":
                    await runner.set_account(eid, m.get("account"))
                    await _broadcast(request.app, {"type": "account", "entry": eid, "account": m.get("account") or None})
                elif t == "set_model":
                    await runner.set_model(eid, m.get("model"))
                    await _broadcast(request.app, {"type": "model", "entry": eid, "model": m.get("model") or None})
                elif t == "permission_reply":
                    await runner.answer(m["request_id"], m.get("allow"), m.get("message"), by=client)
            except Exception as e:
                await ws.send_json({"type": "error", "entry": m.get("entry") if isinstance(m, dict) else None,
                                    "code": type(e).__name__, "message": str(e)})
    finally:
        request.app[SOCKETS].discard(ws)
    return ws


async def _broadcast(app, event):
    for ws in list(app[SOCKETS]):
        try:
            await ws.send_json(event)
        except Exception:
            app[SOCKETS].discard(ws)


BUILTIN_COMMANDS = {
    "compact": "Summarise older messages to free up context", "clear": "Start over with an empty context",
    "context": "Show what is using the context window", "usage": "Show plan usage and limits",
    "init": "Write a CLAUDE.md for this project", "review": "Review a pull request",
}


def _skill_descriptions():
    """name -> description from the user's skills and command files (the init list has names only)."""
    out, home = {}, Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    plugin_skills = list(home.glob("plugins/cache/*/*/*/skills/*/SKILL.md"))  # <marketplace>/<plugin>/<version>
    for f in list(home.glob("skills/*/SKILL.md")) + list(home.glob("commands/*.md")) + plugin_skills:
        name = f.parent.name if f.name == "SKILL.md" else f.stem
        if f in plugin_skills:
            name = f"{f.parents[3].name}:{name}"  # plugin:skill, as the session lists it
        try:
            head = f.read_text(encoding="utf-8", errors="replace")[:3000]
        except OSError:
            continue
        lines = head.splitlines()
        desc = ""
        for i, l in enumerate(lines):
            if l.startswith("description:"):
                desc = l.split(":", 1)[1].strip().strip('"')
                if desc in (">", "|", ">-", "|-"):  # YAML block scalar: the text is on the indented lines below
                    block = itertools.takewhile(lambda x: x.startswith((" ", "\t")) or not x.strip(), lines[i + 1:])
                    desc = " ".join(x.strip() for x in block).strip().split("  ")[0]  # first paragraph
                break
        out[name] = desc
    return out


async def commands(request):
    """Slash commands for the menu: the session's real list once one has started here, else built-ins + the user's skills."""
    descs = {**BUILTIN_COMMANDS, **await _off(_skill_descriptions)}
    names = request.app[RUNNER].commands or sorted(descs)
    return web.json_response({"commands": [{"name": n, "description": descs.get(n, "")} for n in names],
                              "from_session": bool(request.app[RUNNER].commands)})


def make_app(store: Store, name=None):
    app = web.Application(middlewares=[cors, auth])
    app[STORE], app[NAME] = store, name or socket.gethostname()
    app[SOCKETS] = set()
    app[RUNNER] = Runner(store, sdk, lambda ev: _broadcast(app, ev))
    app[TTYS] = Terminals(lambda ev: _broadcast(app, ev))

    async def start(app):
        scrub_env()
        app[REAPER] = asyncio.get_running_loop().create_task(app[RUNNER].reap_idle())

    async def stop(app):
        app[REAPER].cancel()
        await app[TTYS].close_all()
        await app[RUNNER].close_all()
    async def close_sockets(app):  # open WebSockets would otherwise hold a graceful shutdown for its full timeout
        for ws in list(app[SOCKETS]):
            await ws.close(code=1001, message=b"daemon stopping")
    app.on_startup.append(start)
    app.on_shutdown.append(close_sockets)
    app.on_cleanup.append(stop)
    app.add_routes([
        web.post("/v1/pair", pair), web.get("/v1/machine", machine), web.get("/v1/sessions", sessions),
        web.get("/v1/entries", list_entries), web.post("/v1/entries", add_entry),
        web.patch("/v1/entries/{id}", update_entry), web.delete("/v1/entries/{id}", remove_entry),
        web.get("/v1/entries/{id}/messages", messages), web.get("/v1/ws", ws_handler), web.get("/v1/commands", commands), web.get("/v1/folders", folders), web.get("/v1/models", models),
        web.get("/v1/accounts", list_accounts), web.post("/v1/accounts", add_account), web.delete("/v1/accounts/{name}", remove_account),
    ])
    return app
