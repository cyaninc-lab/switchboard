"""HTTP API v1 (phase 1: pairing, machine, sessions, tree entries, history). See docs/protocol.md."""
import asyncio, dataclasses, glob, itertools, json, os, platform, socket, time, uuid
from pathlib import Path
import aiohttp
from aiohttp import web
import claude_agent_sdk as sdk
from . import __version__, peers
from .store import Store
from .runner import Runner, scrub_env, login_path, BTW_TITLE
from .tty import Terminals
from .procs import alive
from . import files as filesmod
from . import github
from . import webui
from . import patches as patchmod
from .net import Network, SETTING as NET_SETTING
from .watches import Watches, KINDS, ACTIONS
from . import watches as watchmod
from .runner import MODES, EFFORTS, IDLE_SETTING, IDLE_CHOICES
from . import logs
from . import push
from . import relay as relaymod
from .logs import log

STORE, NAME = web.AppKey("store", Store), web.AppKey("name", str)
RUNNER, SOCKETS = web.AppKey("runner", Runner), web.AppKey("sockets", set)
REAPER = web.AppKey("reaper", asyncio.Task)
TTYS = web.AppKey("ttys", Terminals)
NET = web.AppKey("net", Network)
WATCHES, WATCH_TASK = web.AppKey("watches", Watches), web.AppKey("watch_task", asyncio.Task)
PUSH_STATE = web.AppKey("push_state", dict)  # entry id -> last state, to notice a turn finishing
DESK = web.AppKey("desk", object)
SENT_REFS = web.AppKey("sent_refs", dict)
HISTORY = web.AppKey("history", dict)  # (session id, limit) -> (transcript mtime+size, the answer): kept until it changes  # refs of messages already taken (insertion-ordered, the last 500)  # is the user in a desktop window: phone notifications pause
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def open_in_terminal():
    """{session id} of sessions a live Claude Code process holds (CLI, IDE, Remote Control)."""
    live = set()
    for path in glob.glob(str(CLAUDE_DIR / "sessions" / "*.json")):
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if alive(d["pid"], d.get("procStart")):
                live.add(d["sessionId"])
        except (OSError, ValueError, KeyError):
            continue
    return live


def subagent_tree(session_id):
    """The session's subagents, from Claude Code's sidecars (projects/<p>/<session>/subagents/**/agent-<id>.meta.json).
    parent is the subagent that spawned it (nested), None for ones the session spawned itself."""
    out = []
    for meta in glob.glob(str(CLAUDE_DIR / "projects" / "*" / session_id / "subagents" / "**" / "agent-*.meta.json"), recursive=True):
        try:
            d = json.loads(Path(meta).read_text(encoding="utf-8"))
            log_file = Path(meta[: -len(".meta.json")] + ".jsonl")
            updated = log_file.stat().st_mtime
        except (OSError, ValueError):
            continue
        out.append({"id": Path(meta).name[len("agent-"): -len(".meta.json")], "description": d.get("description") or d.get("name") or "subagent",
                    "type": d.get("agentType"), "parent": d.get("parentAgentId"), "tool_use_id": d.get("toolUseId"),
                    "finished": bool(d.get("finished")), "stopped": bool(d.get("stoppedByUser")), "updated": int(updated * 1000)})
    return sorted(out, key=lambda x: x["updated"])


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
    d = {k: entry.get(k) for k in ("id", "session_id", "name", "pinned", "account", "mode", "model", "effort")}
    d["group"], d["pinned"] = entry["grp"], bool(entry["pinned"])
    held = runner.state(entry["id"]) if runner else None
    d["usage"] = runner.usage.get(entry["id"]) if runner else None
    d["tasks"] = runner.tasks(entry["id"]) if runner else []
    if info is None and entry.get("cwd"):  # created here, no message yet
        d["state"] = held or "new"
        d["session"] = {"session_id": entry["session_id"], "summary": entry["name"], "cwd": entry["cwd"], "git_branch": None,
                        "last_modified": int(entry["created"] * 1000), "custom_title": None}
        return d
    d["state"] = held or ("missing" if info is None else ("terminal" if entry["session_id"] in live else "stopped"))
    d["session"] = _plain(info)
    return d


CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS"}


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


def _relay_device(request):
    """The device asking, when the request came through this machine's relay tunnel (the relay checked it's an approved
    device of the same account), else None. ponytail: trusts the relay; end-to-end keys replace that."""
    if not request.headers.get(relaymod.RELAYED) or not relaymod.address(request.app[STORE]):
        return None
    try:
        d = json.loads(request.headers.get(relaymod.DEVICE) or "")
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("id") else None


@web.middleware
async def auth(request, handler):
    if request.path in ("/v1/pair", "/v1/ws") or not request.path.startswith("/v1/"):  # /v1/ws authenticates with its first message;
        return await handler(request)  # outside /v1 is the web app's public files
    if _relay_device(request):  # one of this machine's account's approved devices, through its relay
        return await handler(request)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or not request.app[STORE].client_for(token):
        raise web.HTTPUnauthorized(text=json.dumps({"code": "unauthorized"}), content_type="application/json")
    return await handler(request)


PAIR_FAILS: list[float] = []  # recent wrong codes: short codes reachable through a relay mustn't be guessable


async def pair(request):
    body = await request.json()
    now = time.time()
    PAIR_FAILS[:] = [t for t in PAIR_FAILS if t > now - 600]
    if len(PAIR_FAILS) >= 10:  # 10 wrong codes in 10 minutes: no pairing at all for a while
        raise web.HTTPTooManyRequests(text=json.dumps({"code": "too_many_tries"}), content_type="application/json")
    token = request.app[STORE].redeem(str(body.get("code") or "").upper().replace("-", "").replace(" ", ""), body.get("client_name"))
    log.info("pairing %s: %s", "accepted" if token else "rejected", body.get("client_name"))
    if not token:
        PAIR_FAILS.append(now)
        raise web.HTTPForbidden(text=json.dumps({"code": "bad_code"}), content_type="application/json")
    return web.json_response({"token": token, "machine": request.app[NAME]})


async def machine(request):
    return web.json_response({"name": request.app[NAME], "daemon": __version__, "sdk": sdk.__version__,
                              "os": platform.system(), "arch": platform.machine(), "relay": relaymod.address(request.app[STORE])})


async def sessions(request):
    q = request.query.get("q", "").lower()
    limit, offset = int(request.query.get("limit", 50)), int(request.query.get("offset", 0))
    found = [s for s in await _off(sdk.list_sessions) if getattr(s, "custom_title", None) != BTW_TITLE]  # side-chat forks aren't sessions to add
    if q:
        found = [s for s in found if q in (s.summary or "").lower() or q in (s.cwd or "").lower()]
    store, out = request.app[STORE], []
    for s in found[offset:offset + limit]:
        e = store.entry_by_session(s.session_id)
        out.append({**_plain(s), "entry_id": e["id"] if e and not e["removed"] else None})  # a removed one can be added (restored)
    return web.json_response({"total": len(found), "sessions": out})


async def list_entries(request):
    live, runner = await _live(request), request.app[RUNNER]
    out = []
    for e in request.app[STORE].entries():
        v = _entry_view(e, await _off(sdk.get_session_info, e["session_id"]), live, runner)
        v["subagents"] = await _off(subagent_tree, e["session_id"])
        # running = unfinished and active since this session's process started: a subagent from an earlier process died
        # with it, and older Claude Code versions never wrote `finished`, so old ones would otherwise look alive forever
        lv = runner.live.get(e["id"])
        for a in v["subagents"]:
            a["running"] = bool(lv) and not a["finished"] and not a["stopped"] and a["updated"] >= lv.started * 1000
        out.append(v)
    return web.json_response({"entries": out})


NEW_SESSION = "new_session"  # defaults for sessions added from now on: {mode, model, effort, account}; None = Claude Code's own
DEFAULT_KEYS = ("mode", "model", "effort", "account")


def _defaults(store):
    return json.loads(store.setting(NEW_SESSION, "{}") or "{}")


def _claude_default_mode():
    """The permission mode Claude Code starts in on this machine (its settings' defaultMode), for showing what "default" means."""
    try:
        cfg = json.loads((Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json").read_text())
        return (cfg.get("permissions") or {}).get("defaultMode") or "default"
    except (OSError, ValueError):
        return "default"


async def add_entry(request):
    body, store = await request.json(), request.app[STORE]
    d = _defaults(store)  # what the request leaves out comes from the machine's defaults for new sessions
    for k in DEFAULT_KEYS:
        if not body.get(k) and d.get(k):
            body[k] = d[k]
    if "new" in body:
        cwd = os.path.abspath(os.path.expanduser(body["new"].get("cwd") or "~"))
        if not os.path.isdir(cwd):
            raise web.HTTPBadRequest(text=json.dumps({"code": "no_such_folder"}), content_type="application/json")
        e = store.add_entry(str(uuid.uuid4()), body.get("name") or "New session", body.get("group") or _default_group(cwd),
                            body.get("account"), body.get("mode"), cwd=cwd, model=body.get("model") or None)
        if body.get("effort"):
            e = store.update_entry(e["id"], effort=body["effort"])
        return web.json_response(_entry_view(e, None, set(), request.app[RUNNER]), status=201)
    if "session_id" not in body:
        raise web.HTTPBadRequest(text=json.dumps({"code": "session_id_or_new"}), content_type="application/json")
    info = await _off(sdk.get_session_info, body["session_id"])
    if info is None:
        raise web.HTTPNotFound(text=json.dumps({"code": "no_such_session"}), content_type="application/json")
    if (old := store.entry_by_session(info.session_id)):
        if not old["removed"]:
            raise web.HTTPConflict(text=json.dumps({"code": "already_in_tree"}), content_type="application/json")
        # added again after being removed: + Add's name and project apply (as for any add), its settings come back
        # (Restore, from the Removed list, puts it back exactly as it was)
        e = store.restore_entry(old["id"])
        e = store.update_entry(e["id"], name=body.get("name") or e["name"], grp=body.get("group") or _default_group(info.cwd))
        return web.json_response(_entry_view(e, info, await _live(request), request.app[RUNNER]), status=200)
    e = store.add_entry(info.session_id, body.get("name") or info.custom_title or info.summary or "Untitled",
                        body.get("group") or _default_group(info.cwd),
                        body.get("account") or None, body.get("mode"))
    if body.get("model") or body.get("effort"):
        e = store.update_entry(e["id"], model=body.get("model") or None, effort=body.get("effort") or None)
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
    """Off the tree (the session stays on disk), or with ?forever=1 the session itself is deleted from this machine
    (its transcript and subagent transcripts; can't be undone). Refused while it's open in a terminal elsewhere."""
    eid = request.match_info["id"]
    e = request.app[STORE].entry(eid)
    if not e:
        raise web.HTTPNotFound()
    forever = request.query.get("forever") == "1"
    if e["removed"] and not forever:
        raise web.HTTPNotFound()  # already off the tree (Delete for good still works from the Removed list)
    if forever and e["session_id"] in await _live(request):
        raise web.HTTPConflict(text=json.dumps({"code": "open_elsewhere"}), content_type="application/json")
    await request.app[RUNNER].stop(eid)
    await request.app[TTYS].close(eid)  # its real terminal here, if open
    if forever:
        try:
            await _off(sdk.delete_session, e["session_id"])
        except FileNotFoundError:
            pass  # a new session that never ran has no transcript yet
        log.info("deleted session %s for good (entry %s)", e["session_id"], eid)
        request.app[STORE].purge_entry(eid)
    else:
        request.app[STORE].remove_entry(eid)  # kept in the Removed list
    return web.Response(status=204)


async def removed_entries(request):
    """Sessions taken off the tree, newest first, to restore or delete for good."""
    out = []
    for e in request.app[STORE].removed_entries():
        v = _entry_view(e, await _off(sdk.get_session_info, e["session_id"]), set())
        v["removed"] = int(e["removed"] * 1000)
        out.append(v)
    return web.json_response({"entries": out})


async def restore_entry(request):
    e = request.app[STORE].entry(request.match_info["id"])
    if not e:
        raise web.HTTPNotFound()
    e = request.app[STORE].restore_entry(e["id"])
    return web.json_response(_entry_view(e, await _off(sdk.get_session_info, e["session_id"]), await _live(request), request.app[RUNNER]))


def _bad(code):
    return web.HTTPBadRequest(text=json.dumps({"code": code}), content_type="application/json")


def _check_watch(body, store, partial=False):
    if code := watchmod.problem(body, store, MODES, partial):
        raise _bad(code)


async def list_watches(request):
    return web.json_response({"watches": request.app[STORE].watches()})


async def add_watch(request):
    body, store = await request.json(), request.app[STORE]
    _check_watch(body, store)
    w = store.add_watch(body["name"].strip(), body["kind"], body.get("config") or {}, body.get("action", "notify"),
                        body.get("entry_id"), body.get("instructions"), body.get("mode"))
    return web.json_response(w, status=201)


async def update_watch(request):
    body, store, wid = await request.json(), request.app[STORE], request.match_info["id"]
    w = store.watch(wid)
    if not w:
        raise web.HTTPNotFound()
    _check_watch({**w, **body}, store, partial=True)
    if "config" in body or "kind" in body:
        body["state"] = None  # a changed check starts from a fresh baseline
        request.app[WATCHES].due.pop(wid, None)
    return web.json_response(store.update_watch(wid, **body))


async def remove_watch(request):
    if not request.app[STORE].remove_watch(request.match_info["id"]):
        raise web.HTTPNotFound()
    return web.Response(status=204)


async def test_watch(request):
    """Run the check now and fire it even if nothing changed: to try a watch out."""
    w = request.app[STORE].watch(request.match_info["id"])
    if not w:
        raise web.HTTPNotFound()
    fired = await request.app[WATCHES].check_one(w, force_fire=True)
    return web.json_response({"fired": fired})


async def list_inbox(request):
    return web.json_response({"items": request.app[STORE].inbox(int(request.query.get("limit", 100)))})


async def delete_inbox(request):
    iid = int(request.match_info["id"])
    if not request.app[STORE].delete_inbox(iid):
        raise web.HTTPNotFound()
    await _broadcast(request.app, {"type": "inbox_removed", "id": iid})  # every device drops it
    return web.Response(status=204)


async def read_inbox(request):
    iid = request.match_info.get("id")
    request.app[STORE].mark_read(int(iid) if iid else None)
    await _broadcast(request.app, {"type": "inbox_read", "id": int(iid) if iid else None})
    return web.Response(status=204)


def _local_only(request):
    """Settings that change who can reach this daemon, and pairing codes, only from the machine itself."""
    if request.remote not in ("127.0.0.1", "::1") or request.headers.get(relaymod.RELAYED):  # through a relay isn't "here"
        raise web.HTTPForbidden(text=json.dumps({"code": "only_from_this_machine"}), content_type="application/json")


# ---- bug reports to GitHub (this machine only: the sign-in belongs to whoever sits at it) ----
async def github_status(request):
    _local_only(request)
    return web.json_response(github.status(request.app[STORE]))


async def github_start(request):
    _local_only(request)
    try:
        return web.json_response(await github.start())
    except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise web.HTTPBadGateway(text=json.dumps({"code": "github_unavailable", "message": str(e)}), content_type="application/json")


async def github_poll(request):
    _local_only(request)
    body = await request.json()
    return web.json_response(await github.poll(request.app[STORE], body.get("device_code", "")))


async def github_sign_out(request):
    _local_only(request)
    github.sign_out(request.app[STORE])
    return web.json_response(github.status(request.app[STORE]))


async def github_issue(request):
    _local_only(request)
    body = await request.json()
    try:
        return web.json_response(await github.file_issue(request.app[STORE], body.get("title", ""), body.get("body", "")))
    except PermissionError:
        raise web.HTTPUnauthorized(text=json.dumps({"code": "not_signed_in"}), content_type="application/json")
    except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise web.HTTPBadGateway(text=json.dumps({"code": "github_error", "message": str(e)}), content_type="application/json")


async def get_network(request):
    return web.json_response(await request.app[NET].status())


async def set_network(request):
    _local_only(request)
    body = await request.json()
    request.app[STORE].set_setting(NET_SETTING, "1" if body.get("listen_tailscale") else "0")
    await request.app[NET].apply()
    return web.json_response(await request.app[NET].status())


PROJECT_ORDER = "project_order"


async def get_settings(request):
    st = request.app[STORE]
    return web.json_response({"idle_close_minutes": int(st.setting(IDLE_SETTING, "0") or 0),
                              "project_order": json.loads(st.setting(PROJECT_ORDER, "[]")),
                              "new_session": _defaults(st), "claude_default_mode": _claude_default_mode()})


async def set_settings(request):
    body = await request.json()
    if "idle_close_minutes" in body:
        if body["idle_close_minutes"] not in IDLE_CHOICES:
            raise web.HTTPBadRequest(text=json.dumps({"code": "bad_idle_close"}), content_type="application/json")
        request.app[STORE].set_setting(IDLE_SETTING, str(body["idle_close_minutes"]))
    if "new_session" in body:  # only sessions added after this get them
        d = body["new_session"] or {}
        if not isinstance(d, dict) or set(d) - set(DEFAULT_KEYS) or (d.get("mode") and d["mode"] not in MODES) \
                or (d.get("effort") and d["effort"] not in EFFORTS) or (d.get("account") and d["account"] not in {a["name"] for a in request.app[STORE].accounts()}):
            raise web.HTTPBadRequest(text=json.dumps({"code": "bad_new_session_defaults"}), content_type="application/json")
        request.app[STORE].set_setting(NEW_SESSION, json.dumps({k: v for k, v in d.items() if v}))
    if "project_order" in body:  # project names, top first (projects not listed follow, as before)
        order = body["project_order"]
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order) or len(order) > 1000:
            raise web.HTTPBadRequest(text=json.dumps({"code": "bad_project_order"}), content_type="application/json")
        request.app[STORE].set_setting(PROJECT_ORDER, json.dumps(order))
        await _broadcast(request.app, {"type": "settings"})  # other clients re-read the order
    return await get_settings(request)


async def get_push(request):
    """The phone call services (voice, listening) and "pause while I'm in Switchboard on a computer". Notifications
    themselves go through the account's relay: nothing to set here."""
    st = request.app[STORE]
    return web.json_response({"voice": st.setting(push.VOICE, ""), "listen": st.setting(push.LISTEN, ""),
                              "pause_at_desk": st.setting(push.PAUSE, "1") != "0", "at_desk": request.app[DESK].present()})


async def set_push(request):
    """{voice, listen}: where a call gets its voice and is understood; {pause_at_desk}."""
    body, st = await request.json(), request.app[STORE]
    def ok_url(u):
        return isinstance(u, str) and u.startswith(("http://", "https://")) and len(u) < 500
    for k, key in (("voice", push.VOICE), ("listen", push.LISTEN)):  # calls: where the phone gets a voice and is understood
        if k in body:
            if body[k] and not ok_url(body[k]):
                raise web.HTTPBadRequest(text=json.dumps({"code": f"bad_{k}_server"}), content_type="application/json")
            st.set_setting(key, (body[k] or "").rstrip("/"))
    if "pause_at_desk" in body:  # no phone notifications while the user is in a desktop window (on by default)
        st.set_setting(push.PAUSE, "1" if body["pause_at_desk"] else "0")
    return await get_push(request)


async def answer_request(request):
    """Allow/Deny or answer a question from a phone notification (the WebSocket's permission_reply, over plain HTTP)."""
    b = await request.json()
    ok = await request.app[RUNNER].answer(b.get("request_id"), b.get("allow"), b.get("message"), by="phone", answers=b.get("answers"))
    return web.json_response({"answered": ok}, status=200 if ok else 409)


async def send_message(request):
    """A reply from a phone notification: send it to the session (the WebSocket's send, over plain HTTP)."""
    st, b = request.app[STORE], await request.json()
    entry = st.entry(request.match_info["id"])
    if not entry or not isinstance(b.get("text"), str) or not b["text"].strip():
        raise web.HTTPBadRequest(text=json.dumps({"code": "bad_send"}), content_type="application/json")
    if entry["session_id"] in await _off(open_in_terminal) - request.app[RUNNER].held_sessions():
        raise web.HTTPConflict(text=json.dumps({"code": "open_in_terminal"}), content_type="application/json")
    sender = b.get("from")
    if isinstance(sender, str) and sender.strip():  # a session on another of the user's machines (cross-machine messages)
        sender = sender.strip()[:200]
        await request.app[RUNNER].send(entry, f'Message from the session "{sender}" (on another machine, sent through Switchboard; '
                                              f'to answer, use the switchboard message_session tool with session "{sender}"):\n\n{b["text"]}',
                                       origin={"from": sender})
        return web.json_response({"sent": True})
    if not b.get("wait"):
        await request.app[RUNNER].send(entry, b["text"])
        return web.json_response({"sent": True})
    # a phone call: hold the request until Claude's turn ends, and answer with what it said (read aloud on the phone)
    fut = asyncio.get_running_loop().create_future()
    await request.app[RUNNER].send(entry, b["text"], origin={"reply": fut, "via": "call"})
    try:
        return web.json_response({"sent": True, "reply": await asyncio.wait_for(fut, 600)})
    except asyncio.TimeoutError:
        return web.json_response({"sent": True, "reply": None})


async def peer_token(request):
    """{machine}: a token for that other daemon of the user's, so its sessions can message this one's (the app links them)."""
    b = await request.json()
    if not isinstance(b.get("machine"), str) or not b["machine"].strip():
        raise web.HTTPBadRequest(text=json.dumps({"code": "bad_machine"}), content_type="application/json")
    return web.json_response({"token": request.app[STORE].peer_token(b["machine"].strip()[:100]), "machine": request.app[NAME]})


async def get_peers(request):
    return web.json_response({"peers": [{"name": p["name"], "url": p["url"]} for p in peers.peers(request.app[STORE])]})


async def set_peers(request):
    """{peers: [{name, url, token}]}: the user's other machines this one's sessions may message (replaces the list)."""
    ps = (await request.json()).get("peers")
    ok = lambda p: isinstance(p, dict) and all(isinstance(p.get(k), str) and p[k] for k in ("name", "url", "token")) \
        and p["url"].startswith(("http://", "https://"))
    if not isinstance(ps, list) or not all(map(ok, ps)):
        raise web.HTTPBadRequest(text=json.dumps({"code": "bad_peers"}), content_type="application/json")
    peers.set_peers(request.app[STORE], ps)
    return await get_peers(request)


async def pair_code(request):
    """A one-time code for pairing another device, from the app instead of the command line."""
    _local_only(request)
    return web.json_response({"code": request.app[STORE].new_pair_code(), "expires_in": 600})


# ---- this machine on a relay (Settings → Account in the desktop app; this machine only) ----
RELAY = web.AppKey("relay", dict)  # {"listen": (host, port), "tunnel": task, "signin": {...}, "error": str}


def start_tunnel(app):
    r = app[RELAY]
    if r.get("tunnel") and not r["tunnel"].done():
        r["tunnel"].cancel()
    if relaymod.address(app[STORE]) and r.get("listen"):
        r["tunnel"] = asyncio.get_running_loop().create_task(relaymod.run(app[STORE], r["listen"][1], r["listen"][0]))


async def get_relay(request):
    _local_only(request)
    st, r = request.app[STORE], request.app[RELAY]
    return web.json_response({"url": st.setting(relaymod.URL, "") or None, "address": relaymod.address(st),
                              "id": st.setting(relaymod.ID, "") or None, "secret": st.setting(relaymod.SECRET, "") or None,
                              "signin": r.get("signin"), "error": r.get("error")})


async def relay_signin(request):
    """Sign this machine in to a relay with GitHub: returns the code to enter; finishes in the background."""
    _local_only(request)
    url = str((await request.json()).get("url") or "").rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise web.HTTPBadRequest(text=json.dumps({"code": "bad_relay"}), content_type="application/json")
    app, r = request.app, request.app[RELAY]
    try:
        d = await relaymod.signin_start(url, app[NAME], screen=True)  # from the desktop app: it can approve devices
    except Exception as e:
        raise web.HTTPBadGateway(text=json.dumps({"code": "relay_unavailable", "message": str(e)[:200]}), content_type="application/json")
    r["signin"], r["error"] = {"user_code": d["user_code"], "verification_uri": d["verification_uri"]}, None

    async def finish():
        try:
            await relaymod.signin_finish(app[STORE], url, d)
            start_tunnel(app)
        except Exception as e:
            r["error"] = str(e)[:200]
        finally:
            r["signin"] = None
            await _broadcast(app, {"type": "relay"})
    asyncio.get_running_loop().create_task(finish())
    return web.json_response(r["signin"])


async def relay_leave(request):
    _local_only(request)
    st, r = request.app[STORE], request.app[RELAY]
    if relaymod.address(st):
        await relaymod.account(st, "DELETE", f"/v1/devices/{st.setting(relaymod.ID, '')}")  # off the account too
    for k in (relaymod.URL, relaymod.ID, relaymod.SECRET):
        st.set_setting(k, "")
    if r.get("tunnel"):
        r["tunnel"].cancel()
    return web.json_response({"ok": True})


async def relay_devices(request):
    """This machine's account: its devices (GET), approve one (POST …/approve), remove one (DELETE)."""
    _local_only(request)
    if not relaymod.address(request.app[STORE]):
        raise web.HTTPNotFound(text=json.dumps({"code": "not_on_a_relay"}), content_type="application/json")
    did = request.match_info.get("id")
    status, body = await relaymod.account(request.app[STORE], request.method,
                                          f"/v1/devices/{did}/approve" if request.method == "POST" else f"/v1/devices/{did}" if did else "/v1/devices")
    return web.json_response(body, status=status)


async def get_logs(request):
    return web.json_response({"log": logs.tail(int(request.query.get("lines", 300)))})


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


async def entry_files(request):
    """Files in the session's folder matching ?q= (for @-mentions in the message box)."""
    e = request.app[STORE].entry(request.match_info["id"])
    if not e:
        raise web.HTTPNotFound()
    info = await _off(sdk.get_session_info, e["session_id"])
    cwd = e.get("cwd") or getattr(info, "cwd", None)
    if not cwd or not os.path.isdir(cwd):
        return web.json_response({"files": []})
    return web.json_response({"files": await _off(filesmod.search, cwd, request.query.get("q", ""))})


async def messages(request):
    e = request.app[STORE].entry(request.match_info["id"])
    if not e:
        raise web.HTTPNotFound()
    limit = max(1, int(request.query.get("limit", 200)))
    if e.get("cwd") and await _off(sdk.get_session_info, e["session_id"]) is None:
        return web.json_response({"messages": [], "total": 0})  # new, nothing said yet
    # the answer is kept until the transcript changes: reading a long session's file (100+ MB) took a second or two on
    # every visit. ponytail: the last 32 answers, in memory; a running session's file changes, so it's read again
    path = patchmod._transcript(e["session_id"])
    try:
        st = os.stat(path) if path else None
        stamp = (st.st_mtime_ns, st.st_size) if st else None
    except OSError:
        stamp = None
    cache, key = request.app[HISTORY], (e["session_id"], limit)
    if stamp and (hit := cache.get(key)) and hit[0] == stamp:
        return web.Response(body=hit[1], content_type="application/json")
    # the most recent `limit` messages: the SDK's offset counts from the start, which showed long sessions only their
    # beginning (it parses the whole file either way, so reading everything costs nothing extra)
    msgs = [_plain(m) for m in (await _off(sdk.get_session_messages, e["session_id"]))]
    msgs = await _off(patchmod.with_mid_turn, e["session_id"], msgs)  # what the user sent mid-turn (the SDK drops it)
    shown = msgs[-limit:]
    blocks = lambda m: (m.get("message") or {}).get("content") if isinstance(m, dict) and isinstance(m.get("message"), dict) else None
    edits = {b.get("id") for m in shown for b in (blocks(m) if isinstance(blocks(m), list) else []) if isinstance(b, dict) and b.get("type") == "tool_use"}
    patches = await _off(patchmod.for_session, e["session_id"], edits) if edits else {}
    body = json.dumps({"messages": shown, "total": len(msgs), "patches": patches}, default=str).encode()
    if stamp:
        cache.pop(key, None); cache[key] = (stamp, body)
        while len(cache) > 32:
            cache.pop(next(iter(cache)))
    return web.Response(body=body, content_type="application/json")


async def subagent_messages(request):
    """What a subagent did (read-only: subagents can't be messaged). Latest `limit` messages, like a session's history."""
    e = request.app[STORE].entry(request.match_info["id"])
    if not e:
        raise web.HTTPNotFound()
    limit = max(1, int(request.query.get("limit", 400)))
    msgs = [_plain(m) for m in await _off(sdk.get_subagent_messages, e["session_id"], request.match_info["agent"])]
    for m in msgs:  # shown as its own conversation, not nested under the parent's Agent call
        m["parent_tool_use_id"] = None
    return web.json_response({"messages": msgs[-limit:], "total": len(msgs)}, dumps=lambda o: json.dumps(o, default=str))


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
    if first.get("type") == "auth" and (dev := _relay_device(request)):
        client = f"{dev.get('name') or 'device'} (via relay)"
    if not client:
        await ws.close(code=4001, message=b"unauthorized")
        return ws
    request.app[SOCKETS].add(ws)
    log.info("client connected: %s", client)
    await ws.send_json({"type": "hello", "machine": request.app[NAME], "ping": True})  # ping: this daemon answers heartbeats
    for ask in runner.open_asks():  # asked before this client connected
        await ws.send_json(ask)
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                m = json.loads(msg.data)
                t, eid = m.get("type"), m.get("entry")
                entry = store.entry(eid) if eid else None
                if t in ("send", "interrupt", "stop", "stop_task", "btw", "btw_close", "set_mode", "set_model", "set_account", "set_effort") or t.startswith("terminal_"):
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
                    # a message carries a ref: acknowledged once taken, so a client whose connection died unnoticed sends it
                    # again after reconnecting; a ref seen before is acknowledged but not sent twice
                    ref, seen = m.get("ref"), request.app[SENT_REFS]
                    if not (ref and ref in seen):
                        if entry["session_id"] in await _off(open_in_terminal) - runner.held_sessions():
                            raise RuntimeError("open_in_terminal")
                        await (runner.send_now if m.get("now") else runner.send)(entry, m.get("text", ""), m.get("images"))
                        if ref:
                            seen[ref] = None
                            while len(seen) > 500:
                                seen.pop(next(iter(seen)))
                    if ref:
                        await ws.send_json({"type": "ack", "ref": ref})
                elif t == "ping":  # the client's heartbeat: a connection that died unnoticed shows up within seconds
                    await ws.send_json({"type": "pong"})
                elif t == "interrupt":
                    await runner.interrupt(eid)
                elif t == "stop":
                    await runner.stop(eid)
                elif t == "stop_task":
                    await runner.stop_task(eid, m.get("task_id"))
                elif t == "btw":
                    await runner.btw(eid, str(m.get("text", ""))[:20000])
                elif t == "btw_close":
                    await runner.close_btw(eid)
                elif t == "set_mode":
                    await runner.set_mode(eid, m["mode"])
                    await _broadcast(request.app, {"type": "mode", "entry": eid, "mode": m["mode"]})
                elif t == "set_account":
                    await runner.set_account(eid, m.get("account"))
                    await _broadcast(request.app, {"type": "account", "entry": eid, "account": m.get("account") or None})
                elif t == "set_effort":
                    await runner.set_effort(eid, m.get("effort"))
                    await _broadcast(request.app, {"type": "effort", "entry": eid, "effort": m.get("effort") or None})
                elif t == "set_model":
                    await runner.set_model(eid, m.get("model"))
                    await _broadcast(request.app, {"type": "model", "entry": eid, "model": m.get("model") or None})
                elif t == "presence":  # a desktop window: the user is in it (focused, touched lately) or not
                    request.app[DESK].report(ws, bool(m.get("here")))
                elif t == "permission_reply":
                    await runner.answer(m["request_id"], m.get("allow"), m.get("message"), by=client,
                                        remember=m.get("remember"), answers=m.get("answers"), response=m.get("response"))
            except Exception as e:
                log.warning("ws %s failed: %s: %s", m.get("type") if isinstance(m, dict) else "?", type(e).__name__, logs.scrub(str(e)))
                await ws.send_json({"type": "error", "entry": m.get("entry") if isinstance(m, dict) else None,
                                    "code": type(e).__name__, "message": str(e)})
    finally:
        request.app[SOCKETS].discard(ws)
        request.app[DESK].gone(ws)
    return ws


async def _broadcast(app, event):
    note = push.notice(app[STORE], event, app[PUSH_STATE])
    if note and (note := app[DESK].filter(note)):  # phones hear about it through the relay, in the background (a slow push server mustn't hold the daemon)
        asyncio.get_running_loop().create_task(push.send(app[STORE], note))
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
    app[PUSH_STATE] = {}
    app[RELAY] = {}
    app[DESK] = push.Desk(store, lambda st, n: push.send(st, n))
    app[SENT_REFS] = {}
    app[HISTORY] = {}  # late-bound: tests stand in for push.send
    app[RUNNER] = Runner(store, sdk, lambda ev: _broadcast(app, ev))
    app[RUNNER].machine = app[NAME]
    app[TTYS] = Terminals(lambda ev: _broadcast(app, ev))
    app[RUNNER].elsewhere = lambda: open_in_terminal() - app[RUNNER].held_sessions() - app[TTYS].held_sessions()
    app[NET] = Network(store)
    app[WATCHES] = Watches(store, app[RUNNER], lambda ev: _broadcast(app, ev))

    async def start(app):
        scrub_env()
        if (path := await asyncio.get_running_loop().run_in_executor(None, login_path)):
            os.environ["PATH"] = path  # sessions get the user's terminal PATH (nvm's node for plugin hooks, ...)
        app[WATCH_TASK] = asyncio.get_running_loop().create_task(app[WATCHES].run())
        app[REAPER] = asyncio.get_running_loop().create_task(app[RUNNER].reap_loop())

    async def stop(app):
        app[WATCH_TASK].cancel()
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
        web.get("/v1/relay", get_relay), web.post("/v1/relay/signin", relay_signin), web.delete("/v1/relay", relay_leave),
        web.get("/v1/relay/devices", relay_devices), web.post("/v1/relay/devices/{id}/approve", relay_devices),
        web.delete("/v1/relay/devices/{id}", relay_devices),
        web.get("/v1/entries/removed", removed_entries), web.post("/v1/entries/{id}/restore", restore_entry),
        web.patch("/v1/entries/{id}", update_entry), web.delete("/v1/entries/{id}", remove_entry),
        web.get("/v1/entries/{id}/messages", messages), web.get("/v1/entries/{id}/files", entry_files), web.get("/v1/entries/{id}/subagents/{agent}/messages", subagent_messages), web.get("/v1/ws", ws_handler), web.get("/v1/commands", commands), web.get("/v1/folders", folders), web.get("/v1/models", models), web.get("/v1/logs", get_logs),
        web.get("/v1/network", get_network), web.put("/v1/network", set_network),
        web.get("/v1/settings", get_settings), web.put("/v1/settings", set_settings),
        web.get("/v1/push", get_push), web.put("/v1/push", set_push),
        web.post("/v1/entries/{id}/answer", answer_request), web.post("/v1/entries/{id}/send", send_message),
        web.get("/v1/github", github_status), web.post("/v1/github/device", github_start), web.post("/v1/github/poll", github_poll),
        web.delete("/v1/github", github_sign_out), web.post("/v1/github/issue", github_issue), web.post("/v1/pair_code", pair_code),
        web.post("/v1/peer_token", peer_token), web.get("/v1/peers", get_peers), web.put("/v1/peers", set_peers),
        web.get("/v1/watches", list_watches), web.post("/v1/watches", add_watch), web.patch("/v1/watches/{id}", update_watch),
        web.delete("/v1/watches/{id}", remove_watch), web.post("/v1/watches/{id}/test", test_watch),
        web.get("/v1/inbox", list_inbox), web.post("/v1/inbox/read", read_inbox), web.post("/v1/inbox/{id}/read", read_inbox), web.delete("/v1/inbox/{id}", delete_inbox),
        web.get("/v1/accounts", list_accounts), web.post("/v1/accounts", add_account), web.delete("/v1/accounts/{name}", remove_account),
    ])
    app.router.add_get("/{tail:(?!v1/).*}", webui.handler(webui.find()))  # the web app, last: the API routes come first
    return app
