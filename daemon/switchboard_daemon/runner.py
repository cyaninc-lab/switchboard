"""Live sessions (phase 2): one ClaudeSDKClient per active entry, events fanned out to every subscriber.

Events (docs/protocol.md): state, delta, message, permission_request, permission_resolved, error.
"""
import asyncio, dataclasses, os, shutil, time, uuid
from .logs import log, scrub

IDLE_CLOSE = int(os.environ.get("SWITCHBOARD_IDLE_CLOSE", 600))
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_B64 = 7_000_000  # ~5 MB of image per attachment
MAX_IMAGES = 10
MODES = {"default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"}


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
            elif bn == "ToolUseBlock":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            elif bn == "ToolResultBlock":
                blocks.append({"type": "tool_result", "tool_use_id": b.tool_use_id, "content": b.content, "is_error": b.is_error})
            elif isinstance(b, dict):
                blocks.append(b)
        content = blocks
    role = "assistant" if name == "AssistantMessage" else "user"
    return {"type": role, "uuid": getattr(msg, "uuid", None) or uuid.uuid4().hex, "message": {"role": role, "content": content}}


@dataclasses.dataclass
class Live:
    entry_id: str
    client: object
    reader: asyncio.Task = None
    working: bool = False
    last_active: float = dataclasses.field(default_factory=time.time)
    pending: dict = dataclasses.field(default_factory=dict)  # request id -> Future
    said: bool = False  # did this turn produce any assistant text (built-in commands may only return a result)
    queue: list = dataclasses.field(default_factory=list)  # messages sent while working, sent in order afterwards
    origin: dict = None  # set when a watch started this turn: its reply goes to the inbox
    turn_text: list = dataclasses.field(default_factory=list)


class Runner:
    def __init__(self, store, sdk, broadcast):
        self.store, self.sdk, self.broadcast = store, sdk, broadcast
        self.live: dict[str, Live] = {}
        self.cli_path = os.environ.get("SWITCHBOARD_CLAUDE") or shutil.which("claude")
        self.commands: list[str] = []  # slash commands from the last session start (init message)
        self._models = None
        self.on_turn_end = None  # async (entry_id, reply_text, origin) for turns a watch started

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
            return await self._ask(holder["lv"], tool, inp)

        opts = self.sdk.ClaudeAgentOptions(
            **start, cwd=(info.cwd if info else entry["cwd"]) or None, include_partial_messages=True,
            permission_mode=entry.get("mode") or "default", can_use_tool=can_use_tool,
            **({"env": acct} if acct else {}),
            # SWITCHBOARD_MODEL overrides everything (tests run on Haiku); else the entry's choice; else Claude Code's default
            **({"model": m} if (m := os.environ.get("SWITCHBOARD_MODEL") or entry.get("model")) else {}),
            **({"cli_path": self.cli_path} if self.cli_path else {}))
        client = self.sdk.ClaudeSDKClient(options=opts)
        await client.connect()
        log.info("session %s live (entry %s, model %s, account %s)", entry["session_id"], entry["id"], entry.get("model") or "default", entry.get("account") or "machine")
        lv = holder["lv"] = self.live[entry["id"]] = Live(entry["id"], client)
        return lv

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
        lv.working, lv.last_active, lv.said = True, time.time(), False
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
        lv.reader = asyncio.get_running_loop().create_task(self._read(lv))

    async def _read(self, lv):
        try:
            async for m in lv.client.receive_response():
                lv.last_active = time.time()
                if type(m).__name__ == "StreamEvent":
                    ev = m.event
                    if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                        await self.broadcast({"type": "delta", "entry": lv.entry_id, "text": ev["delta"]["text"]})
                elif (h := as_history(m)) is not None:
                    lv.said |= h["type"] == "assistant"
                    if h["type"] == "assistant":
                        lv.turn_text += [b["text"] for b in h["message"]["content"] if isinstance(b, dict) and b.get("type") == "text"]
                    await self.broadcast({"type": "message", "entry": lv.entry_id, "message": h})
                elif type(m).__name__ == "SystemMessage":
                    sub, data = getattr(m, "subtype", ""), getattr(m, "data", {}) or {}
                    if sub == "init" and data.get("slash_commands"):
                        self.commands = list(data["slash_commands"])
                        await self.broadcast({"type": "commands", "entry": lv.entry_id, "commands": self.commands})
                    elif sub == "compact_boundary":
                        await self.broadcast({"type": "notice", "entry": lv.entry_id, "text": "Conversation compacted"})
                elif type(m).__name__ == "ResultMessage":
                    if m.is_error:
                        log.warning("entry %s turn ended with an error: %s", lv.entry_id, scrub(str(m.result))[:300])
                        await self.broadcast({"type": "error", "entry": lv.entry_id, "code": "result_error", "message": str(m.result)[:500]})
                    elif m.result and not lv.said:  # e.g. /usage, /context: output arrives only as the result
                        await self.broadcast({"type": "message", "entry": lv.entry_id, "message": {
                            "type": "assistant", "uuid": uuid.uuid4().hex,
                            "message": {"role": "assistant", "content": [{"type": "text", "text": str(m.result)}]}}})
        except Exception as e:  # a dead CLI process must not take the daemon down
            log.exception("session for entry %s failed", lv.entry_id)
            await self.broadcast({"type": "error", "entry": lv.entry_id, "code": "session_error", "message": str(e)[:500]})
        finally:
            lv.working = False
            if lv.origin and self.on_turn_end:
                origin, lv.origin = lv.origin, None
                try:
                    await self.on_turn_end(lv.entry_id, "\n\n".join(lv.turn_text).strip(), origin)
                except Exception:
                    log.exception("delivering a watch reply failed")
            if lv.queue and self.live.get(lv.entry_id) is lv:
                entry, text, images, origin = lv.queue.pop(0)
                asyncio.get_running_loop().create_task(self.send(entry, text, images, origin))
            else:
                await self._emit_state(lv.entry_id)

    async def _ask(self, lv, tool, inp):
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        lv.pending[rid] = fut
        await self.broadcast({"type": "permission_request", "entry": lv.entry_id, "request_id": rid, "tool": tool, "input": inp})
        await self._emit_state(lv.entry_id)
        try:
            allow, message = await fut
        finally:
            lv.pending.pop(rid, None)
        await self._emit_state(lv.entry_id)
        return self.sdk.PermissionResultAllow() if allow else self.sdk.PermissionResultDeny(message=message or "Denied in Switchboard")

    async def answer(self, request_id, allow, message=None, by="client"):
        """First answer wins; later ones are ignored."""
        for lv in self.live.values():
            fut = lv.pending.get(request_id)
            if fut and not fut.done():
                fut.set_result((bool(allow), message))
                await self.broadcast({"type": "permission_resolved", "entry": lv.entry_id, "request_id": request_id, "allow": bool(allow), "by": by})
                return True
        return False

    async def interrupt(self, entry_id):
        lv = self.live.get(entry_id)
        if lv:
            lv.queue.clear()  # interrupting drops what was queued behind the current turn, as in Claude Code
        if lv and lv.working:
            for fut in list(lv.pending.values()):  # a pending prompt is moot once interrupted
                if not fut.done():
                    fut.set_result((False, "Interrupted"))
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
                fut.set_result((False, "Session stopped"))
        if lv.reader and not lv.reader.done():
            lv.reader.cancel()
        try:
            await lv.client.disconnect()
        except Exception:
            pass
        await self._emit_state(entry_id)

    async def reap_idle(self):
        """Close live sessions that have been quiet for IDLE_CLOSE seconds (they resume on the next message)."""
        while True:
            await asyncio.sleep(min(30, IDLE_CLOSE))
            now = time.time()
            for eid, lv in list(self.live.items()):
                if not lv.working and not lv.pending and now - lv.last_active > IDLE_CLOSE:
                    await self.stop(eid)

    async def close_all(self):
        for eid in list(self.live):
            await self.stop(eid)
