"""Live sessions (phase 2): one ClaudeSDKClient per active entry, events fanned out to every subscriber.

Events (docs/protocol.md): state, delta, message, permission_request, permission_resolved, error.
"""
import asyncio, dataclasses, os, shutil, time, uuid
from .logs import log, scrub

# one message from Claude Code (a Read of a big screenshot is one line of base64); the SDK's 1 MB default kills the session
MAX_MESSAGE = 64 * 1024 * 1024
IDLE_SETTING = "idle_close_minutes"
IDLE_CHOICES = (0, 10, 30, 60, 240)
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_B64 = 7_000_000  # ~5 MB of image per attachment
MAX_IMAGES = 10
TOOLS = ("mcp__switchboard__sessions", "mcp__switchboard__message_session")  # allowed without asking, like SendMessage
MODES = {"default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def scrub_env():
    """Sessions must not inherit CLAUDE* vars (e.g. a daemon started from inside a Claude session):
    drop them from the daemon's own environment once, so every child starts clean."""
    for k in [k for k in os.environ if k.startswith("CLAUDE") and k != "CLAUDE_CONFIG_DIR"]:
        del os.environ[k]


def as_history(msg):
    """Live SDK message -> the same shape get_session_messages() returns, so the UI renders both alike."""
    name = type(msg).__name__
    if name not in ("AssistantMessage", "UserMessage"):
        return None
    content = msg.content
    if not isinstance(content, str):
        blocks = []
        for b in content:
            bn = type(b).__name__
            if bn == "TextBlock":
                blocks.append({"type": "text", "text": b.text})
            elif bn == "ThinkingBlock":
                blocks.append({"type": "thinking", "thinking": b.thinking})
            elif bn == "ToolUseBlock":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            elif bn == "ToolResultBlock":
                blocks.append({"type": "tool_result", "tool_use_id": b.tool_use_id, "content": b.content, "is_error": b.is_error})
            elif isinstance(b, dict):
                blocks.append(b)
        content = blocks
    role = "assistant" if name == "AssistantMessage" else "user"
    return {"type": role, "uuid": getattr(msg, "uuid", None) or uuid.uuid4().hex, "message": {"role": role, "content": content},
            "parent_tool_use_id": getattr(msg, "parent_tool_use_id", None)}  # set on a subagent's messages


@dataclasses.dataclass
class Live:
    entry_id: str
    client: object
    reader: asyncio.Task = None
    started: float = dataclasses.field(default_factory=time.time)  # when this process started: older subagents died with theirs
    working: bool = False
    pending: dict = dataclasses.field(default_factory=dict)  # request id -> Future
    said: bool = False  # did this turn produce any assistant text (built-in commands may only return a result)
    queue: list = dataclasses.field(default_factory=list)  # messages sent while working, sent in order afterwards
    origin: dict = None  # set when a watch (its reply goes to the inbox) or another session started this turn
    turn_text: list = dataclasses.field(default_factory=list)
    last_usage: dict = None  # usage of the latest main-thread API call: how full the context is now
    cost: float = 0.0  # API-equivalent cost of this live session so far
    restart: bool = False  # close after this turn (a setting that only applies at start changed)
    interrupted: bool = False  # this turn was interrupted on purpose: Claude Code then ends it as an error with no message
    last_active: float = dataclasses.field(default_factory=time.time)
    tasks: set = dataclasses.field(default_factory=set)  # background subagents/commands still running: never idle-close then


class Runner:
    def __init__(self, store, sdk, broadcast):
        self.store, self.sdk, self.broadcast = store, sdk, broadcast
        self.live: dict[str, Live] = {}
        self.cli_path = os.environ.get("SWITCHBOARD_CLAUDE") or shutil.which("claude")
        self.commands: list[str] = []  # slash commands from the last session start (init message)
        self._models = None
        self.on_turn_end = None  # async (entry_id, reply_text, origin) for turns a watch started
        self.usage: dict[str, dict] = {}  # entry id -> last usage event (survives the session closing)
        self.elsewhere = lambda: set()  # {session id} a process outside this daemon holds (set by the app)

    # ---- state ----
    def state(self, entry_id):
        lv = self.live.get(entry_id)
        if not lv:
            return None  # not live: the API decides stopped/terminal/missing
        return "waiting" if lv.pending else ("working" if lv.working else "ready")

    def held_sessions(self):
        return {self.store.entry(e)["session_id"] for e in self.live if self.store.entry(e)}

    async def _emit_state(self, entry_id):
        await self.broadcast({"type": "state", "entry": entry_id, "state": self.state(entry_id) or "stopped"})

    # ---- lifecycle ----
    async def _open(self, entry):
        lv = self.live.get(entry["id"])
        if lv:
            return lv
        info = self.sdk.get_session_info(entry["session_id"])
        if info is None and not entry.get("cwd"):
            raise LookupError("no_such_session")
        acct = {}
        if entry.get("account"):
            acct = self.store.account_env(entry["account"])
            if acct is None:
                raise LookupError("no_such_account")
        # a session created in Switchboard doesn't exist on disk until its first message: start it with its own id
        start = {"resume": entry["session_id"]} if info else {"session_id": entry["session_id"]}
        holder = {}

        async def can_use_tool(tool, inp, ctx):
            return await self._ask(holder["lv"], tool, inp, ctx)

        opts = self.sdk.ClaudeAgentOptions(
            **start, cwd=(info.cwd if info else entry["cwd"]) or None, include_partial_messages=True,
            permission_mode=entry.get("mode") or "default", can_use_tool=can_use_tool,
            **({"env": acct} if acct else {}),
            **({"effort": entry["effort"]} if entry.get("effort") else {}),
            # SWITCHBOARD_MODEL overrides everything (tests run on Haiku); else the entry's choice; else Claude Code's default
            **({"model": m} if (m := os.environ.get("SWITCHBOARD_MODEL") or entry.get("model")) else {}),
            **({"cli_path": self.cli_path} if self.cli_path else {}),
            mcp_servers={"switchboard": self._tools(entry["id"])}, allowed_tools=list(TOOLS),
            max_buffer_size=MAX_MESSAGE,
            # Claude in Chrome: the terminal turns it on by itself, SDK sessions only with --chrome (checked on 2.1.327)
            extra_args={"chrome": None})
        client = self.sdk.ClaudeSDKClient(options=opts)
        await client.connect()
        log.info("session %s live (entry %s, model %s, account %s)", entry["session_id"], entry["id"], entry.get("model") or "default", entry.get("account") or "machine")
        lv = holder["lv"] = self.live[entry["id"]] = Live(entry["id"], client)
        lv.reader = asyncio.get_running_loop().create_task(self._read(lv))
        return lv

    # ---- messaging between tree sessions: Claude Code's own SendMessage only reaches running ones ----
    def _tools(self, entry_id):
        sdk = self.sdk
        say = lambda text, err=False: {"content": [{"type": "text", "text": text}], **({"is_error": True} if err else {})}

        def view(e):
            held = self.state(e["id"])
            if held:
                return held
            return "open in a terminal outside Switchboard" if e["session_id"] in self.elsewhere() else "stopped (a message resumes it)"

        async def sessions(args):
            others = [e for e in self.store.entries() if e["id"] != entry_id]
            return say("\n".join(f'- "{e["name"]}" ({e["grp"]}): {view(e)}' for e in others) or "No other sessions in Switchboard on this machine.")

        async def message(args):
            want, text = str(args.get("session", "")).strip(), str(args.get("text", ""))
            me = self.store.entry(entry_id)
            hits = [e for e in self.store.entries() if e["id"] == want] or [e for e in self.store.entries() if e["name"].lower() == want.lower()]
            if not hits:
                return say(f'No session named "{want}". Use the sessions tool to see their names.', True)
            if len(hits) > 1:
                return say(f'{len(hits)} sessions are named "{want}"; use one of these ids instead: {", ".join(e["id"] for e in hits)}', True)
            to = hits[0]
            if to["id"] == entry_id:
                return say("That's this session.", True)
            if not text.strip():
                return say("The message is empty.", True)
            if to["session_id"] in self.elsewhere():
                return say(f'"{to["name"]}" is open in a terminal outside Switchboard, so Switchboard can\'t message it; use SendMessage to reach it there.', True)
            was = self.state(to["id"])
            prompt = (f'Message from the session "{me["name"]}" (sent through Switchboard; to answer, use the switchboard '
                      f'message_session tool with session "{me["name"]}"):\n\n{text}')
            try:
                await self.send(to, prompt, origin={"from": me["name"]})
            except Exception as e:
                return say(f'Couldn\'t deliver to "{to["name"]}": {e}', True)
            return say(f'Delivered to "{to["name"]}"' + {None: " (it was stopped and is resuming now).", "working": " (it's busy; it gets the message when its turn ends)."}.get(was, "."))

        return sdk.create_sdk_mcp_server("switchboard", tools=[
            sdk.tool("sessions", "List the other Claude sessions in Switchboard on this machine, by name, with their state. "
                     "Stopped ones are included: unlike SendMessage, message_session can reach them.", {})(sessions),
            sdk.tool("message_session", "Send a message to another Switchboard session on this machine, by its name from the sessions tool. "
                     "A stopped session is resumed to receive it. It arrives as a new prompt; the reply comes back the same way only "
                     "if that session chooses to answer.", {"session": str, "text": str})(message),
        ])

    async def send(self, entry, text, images=None, origin=None):
        images = images or []
        if len(images) > MAX_IMAGES:
            raise ValueError("too_many_images")
        for im in images:
            if im.get("media_type") not in IMAGE_TYPES:
                raise ValueError("unsupported_image_type")
            if len(im.get("data", "")) > MAX_IMAGE_B64:
                raise ValueError("image_too_large")
        lv = await self._open(entry)
        if lv.working:  # like Claude Code: a message typed while it works goes out when the turn ends
            lv.queue.append((entry, text, images, origin))
            await self.broadcast({"type": "queued", "entry": entry["id"], "count": len(lv.queue)})
            return
        lv.working, lv.said, lv.interrupted, lv.last_active = True, False, False, time.time()
        lv.origin, lv.turn_text = origin, []
        await self._emit_state(entry["id"])
        if origin:  # clients show what they typed themselves; a watch's prompt has to be announced
            await self.broadcast({"type": "message", "entry": entry["id"], "message": {
                "type": "user", "uuid": uuid.uuid4().hex, "message": {"role": "user", "content": text}}})
        if images:
            content = [{"type": "image", "source": {"type": "base64", "media_type": im["media_type"], "data": im["data"]}} for im in images]
            content.append({"type": "text", "text": text or "(image)"})

            async def one():
                yield {"type": "user", "message": {"role": "user", "content": content}}
            await lv.client.query(one())
        else:
            await lv.client.query(text)

    async def _read(self, lv):
        """One reader for the session's whole life, not one per message: Claude also takes turns nobody asked for
        (a background command finishing, a scheduled wake-up). Read per message, those replies sat unread and each
        message then got the previous answer."""
        try:
            async for m in lv.client.receive_messages():
                lv.last_active = time.time()
                sub, data = getattr(m, "subtype", None), getattr(m, "data", None) or {}
                if sub in ("task_started", "task_notification", "task_updated") and data.get("task_id"):
                    status = (data.get("patch") or {}).get("status") if sub == "task_updated" else None
                    if sub == "task_started":
                        lv.tasks.add(data["task_id"])
                    elif sub == "task_notification" or status in ("completed", "failed", "stopped", "killed"):
                        lv.tasks.discard(data["task_id"])
                    await self.broadcast({"type": "subagents", "entry": lv.entry_id})  # the tree re-reads its subagents
                    continue
                if not lv.working and type(m).__name__ in ("AssistantMessage", "UserMessage", "StreamEvent"):  # a turn Claude started on its own
                    lv.working, lv.said, lv.origin, lv.turn_text, lv.interrupted = True, False, None, [], False
                    await self._emit_state(lv.entry_id)
                if type(m).__name__ == "StreamEvent":
                    ev = m.event
                    delta = ev.get("delta", {}) if ev.get("type") == "content_block_delta" else {}
                    if delta.get("type") == "text_delta":
                        await self.broadcast({"type": "delta", "entry": lv.entry_id, "text": delta["text"]})
                    elif delta.get("type") == "thinking_delta":
                        await self.broadcast({"type": "delta", "entry": lv.entry_id, "text": delta.get("thinking", ""), "kind": "thinking"})
                elif (h := as_history(m)) is not None:
                    if type(m).__name__ == "AssistantMessage" and getattr(m, "usage", None) and not getattr(m, "parent_tool_use_id", None):
                        lv.last_usage = m.usage
                    lv.said |= h["type"] == "assistant"
                    if h["type"] == "assistant":
                        lv.turn_text += [b["text"] for b in h["message"]["content"] if isinstance(b, dict) and b.get("type") == "text"]
                    await self.broadcast({"type": "message", "entry": lv.entry_id, "message": h})
                elif type(m).__name__ == "SystemMessage":
                    sub, data = getattr(m, "subtype", ""), getattr(m, "data", {}) or {}
                    e = self.store.entry(lv.entry_id)
                    if sub == "init" and e and data.get("session_id") and data["session_id"] != e["session_id"]:
                        # /clear (or another reset) started a new session: the entry follows it, or a later resume
                        # would bring the cleared conversation back and strand what came after
                        log.info("entry %s moved to session %s (conversation reset)", lv.entry_id, data["session_id"])
                        self.store.set_session(lv.entry_id, data["session_id"])
                        lv.cost, lv.last_usage = 0.0, None
                        await self.broadcast({"type": "session_reset", "entry": lv.entry_id, "session_id": data["session_id"]})
                    if sub == "init" and data.get("slash_commands"):
                        self.commands = list(data["slash_commands"])
                        await self.broadcast({"type": "commands", "entry": lv.entry_id, "commands": self.commands})
                    elif sub == "compact_boundary":
                        await self.broadcast({"type": "notice", "entry": lv.entry_id, "text": "Conversation compacted"})
                elif type(m).__name__ == "ResultMessage":
                    await self._usage(lv, m)
                    if m.is_error and lv.interrupted:
                        log.info("entry %s turn interrupted", lv.entry_id)  # asked for, not an error to show
                    elif m.is_error:
                        log.warning("entry %s turn ended with an error: %s", lv.entry_id, scrub(str(m.result))[:300])
                        await self.broadcast({"type": "error", "entry": lv.entry_id, "code": "result_error", "message": str(m.result)[:500]})
                    elif m.result and not lv.said:  # e.g. /usage, /context: output arrives only as the result
                        await self.broadcast({"type": "message", "entry": lv.entry_id, "message": {
                            "type": "assistant", "uuid": uuid.uuid4().hex,
                            "message": {"role": "assistant", "content": [{"type": "text", "text": str(m.result)}]}}})
                    await self._turn_end(lv)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a dead CLI process must not take the daemon down
            log.exception("session for entry %s failed", lv.entry_id)
            await self.broadcast({"type": "error", "entry": lv.entry_id, "code": "session_error", "message": str(e)[:500]})
        # the CLI process ended (or failed): drop it, so the next message opens the session afresh
        if self.live.get(lv.entry_id) is lv:
            asyncio.get_running_loop().create_task(self.stop(lv.entry_id))
        if lv.working:
            await self._turn_end(lv)

    async def _turn_end(self, lv):
        lv.working = False
        if lv.origin and self.on_turn_end:
            origin, lv.origin = lv.origin, None
            try:
                await self.on_turn_end(lv.entry_id, "\n\n".join(lv.turn_text).strip(), origin)
            except Exception:
                log.exception("delivering a watch reply failed")
        if lv.restart and not lv.queue:
            asyncio.get_running_loop().create_task(self.stop(lv.entry_id))
        elif lv.queue and self.live.get(lv.entry_id) is lv:
            entry, text, images, origin = lv.queue.pop(0)
            await self.broadcast({"type": "queued", "entry": lv.entry_id, "count": len(lv.queue), "sent": 1})  # clients move it into the chat
            asyncio.get_running_loop().create_task(self.send(entry, text, images, origin))
        else:
            await self._emit_state(lv.entry_id)

    async def _usage(self, lv, m):
        u = lv.last_usage or {}
        used = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        windows = [int(v.get("contextWindow") or 0) for v in (getattr(m, "model_usage", None) or {}).values()]
        lv.cost += float(getattr(m, "total_cost_usd", None) or 0)
        ev = {"type": "usage", "entry": lv.entry_id, "context_used": used, "context_window": max(windows) if windows else None,
              "turn_cost": getattr(m, "total_cost_usd", None), "session_cost": round(lv.cost, 4)}
        self.usage[lv.entry_id] = ev
        await self.broadcast(ev)

    async def _ask(self, lv, tool, inp, ctx=None):
        """Three kinds of request, all through can_use_tool: Claude's questions (AskUserQuestion), plan approval
        (ExitPlanMode) and ordinary tool permission, which can be remembered for the session or for good."""
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        lv.pending[rid] = fut
        kind = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}.get(tool, "tool")
        suggestions = list(getattr(ctx, "suggestions", None) or [])
        remember = sorted({s.destination for s in suggestions if getattr(s, "destination", None) in ("session", "localSettings")})
        await self.broadcast({"type": "permission_request", "entry": lv.entry_id, "request_id": rid, "tool": tool,
                              "kind": kind, "input": inp, "remember": remember if kind == "tool" else []})
        await self._emit_state(lv.entry_id)
        try:
            a = await fut
        finally:
            lv.pending.pop(rid, None)
        await self._emit_state(lv.entry_id)
        Allow, Deny = self.sdk.PermissionResultAllow, self.sdk.PermissionResultDeny
        if kind == "question":
            if a["answers"] or a["response"]:
                extra = {"answers": a["answers"]} if a["answers"] else {"response": a["response"]}
                return Allow(updated_input={"questions": inp.get("questions", []), **extra})
            return Deny(message=a["message"] or "The user dismissed the question.")
        if kind == "plan":
            if a["allow"]:
                return Allow(updated_input=inp)
            return Deny(message=f"Keep planning. The user says: {a['message']}" if a["message"] else "Keep planning: the user didn't approve this plan yet.")
        if not a["allow"]:
            return Deny(message=a["message"] or "Denied in Switchboard")
        keep = [s for s in suggestions if a["remember"] and getattr(s, "destination", None) == a["remember"]]
        return Allow(updated_input=inp, **({"updated_permissions": keep} if keep else {}))

    async def answer(self, request_id, allow, message=None, by="client", remember=None, answers=None, response=None):
        """First answer wins; later ones are ignored."""
        for lv in self.live.values():
            fut = lv.pending.get(request_id)
            if fut and not fut.done():
                fut.set_result({"allow": bool(allow) or bool(answers) or bool(response), "message": message,
                                "remember": remember, "answers": answers, "response": response})
                await self.broadcast({"type": "permission_resolved", "entry": lv.entry_id, "request_id": request_id, "allow": bool(allow), "by": by})
                return True
        return False

    async def send_now(self, entry, text, images=None):
        """Claude Code's Ctrl+Enter: interrupt the running turn and send this, ahead of anything already queued."""
        lv = self.live.get(entry["id"])
        if not lv or not lv.working:
            return await self.send(entry, text, images)
        lv.queue.insert(0, (entry, text, images or [], None))
        await self.broadcast({"type": "queued", "entry": entry["id"], "count": len(lv.queue)})
        await self.interrupt(entry["id"], keep_queue=True)

    async def interrupt(self, entry_id, keep_queue=False):
        lv = self.live.get(entry_id)
        if lv and not keep_queue and lv.queue:
            lv.queue.clear()  # interrupting drops what was queued behind the current turn, as in Claude Code
            await self.broadcast({"type": "queued", "entry": entry_id, "count": 0, "dropped": True})  # clients put it back in the box
        if lv and lv.working:
            for fut in list(lv.pending.values()):  # a pending prompt is moot once interrupted
                if not fut.done():
                    fut.set_result({"allow": False, "message": "Interrupted", "remember": None, "answers": None, "response": None})
            lv.interrupted = True
            await lv.client.interrupt()

    async def models(self):
        """The models this machine's Claude Code offers (names, descriptions, effort levels), asked once and cached."""
        if self._models is None:
            client = self.sdk.ClaudeSDKClient(options=self.sdk.ClaudeAgentOptions(
                **({"cli_path": self.cli_path} if self.cli_path else {})))
            await client.connect()
            try:
                info = await client.get_server_info() or {}
                self._models = info.get("models") or []
            finally:
                await client.disconnect()
        return self._models

    async def set_model(self, entry_id, model):
        model = model or None  # "" / None = back to Claude Code's default
        self.store.update_entry(entry_id, model=model)
        lv = self.live.get(entry_id)
        if lv:
            await lv.client.set_model(model)

    async def set_account(self, entry_id, account):
        """The login can't change under a running process: close it; the next message resumes under the new account."""
        if account and self.store.account_env(account) is None:
            raise LookupError("no_such_account")
        self.store.update_entry(entry_id, account=account or None)
        await self.stop(entry_id)

    async def set_effort(self, entry_id, effort):
        """Effort only applies when a session starts: close it now (or after the running turn); it resumes at the new effort."""
        if effort and effort not in EFFORTS:
            raise ValueError("bad_effort")
        self.store.update_entry(entry_id, effort=effort or None)
        lv = self.live.get(entry_id)
        if lv and lv.working:
            lv.restart = True
        elif lv:
            await self.stop(entry_id)

    async def set_mode(self, entry_id, mode):
        if mode not in MODES:
            raise ValueError("bad_mode")
        self.store.update_entry(entry_id, mode=mode)
        lv = self.live.get(entry_id)
        if lv:
            await lv.client.set_permission_mode(mode)

    async def stop(self, entry_id):
        lv = self.live.pop(entry_id, None)
        if not lv:
            return
        log.info("session for entry %s closed", entry_id)
        for fut in lv.pending.values():
            if not fut.done():
                fut.set_result({"allow": False, "message": "Session stopped", "remember": None, "answers": None, "response": None})
        if lv.reader and not lv.reader.done():
            lv.reader.cancel()
        try:
            await lv.client.disconnect()
        except Exception:
            pass
        await self._emit_state(entry_id)

    # ---- idle close: off unless the user sets it (Settings; the daemon-wide setting IDLE_SETTING, in minutes) ----
    async def reap_idle(self, now=None):
        """Close live sessions quiet for longer than the setting (they resume on the next message, with their history)."""
        minutes = int(self.store.setting(IDLE_SETTING, "0") or 0)
        if not minutes:
            return
        now = now or time.time()
        for eid, lv in list(self.live.items()):
            if not lv.working and not lv.pending and not lv.tasks and not lv.queue and now - lv.last_active > minutes * 60:
                # ponytail: a background shell that never ends (a dev server) keeps its session open until closed by hand
                log.info("closing idle session for entry %s", eid)
                await self.stop(eid)

    async def reap_loop(self):
        while True:
            await asyncio.sleep(30)
            try:
                await self.reap_idle()
            except Exception:
                log.exception("idle close")

    async def close_all(self):
        for eid in list(self.live):
            await self.stop(eid)
