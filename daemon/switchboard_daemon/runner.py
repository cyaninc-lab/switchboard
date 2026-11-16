"""Live sessions (phase 2): one ClaudeSDKClient per active entry, events fanned out to every subscriber.

Events (docs/protocol.md): state, delta, message, permission_request, permission_resolved, error.
"""
import asyncio, dataclasses, os, shutil, time, uuid

IDLE_CLOSE = int(os.environ.get("SWITCHBOARD_IDLE_CLOSE", 600))
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


class Runner:
    def __init__(self, store, sdk, broadcast):
        self.store, self.sdk, self.broadcast = store, sdk, broadcast
        self.live: dict[str, Live] = {}
        self.cli_path = os.environ.get("SWITCHBOARD_CLAUDE") or shutil.which("claude")

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
        if info is None:
            raise LookupError("no_such_session")
        holder = {}

        async def can_use_tool(tool, inp, ctx):
            return await self._ask(holder["lv"], tool, inp)

        opts = self.sdk.ClaudeAgentOptions(
            resume=entry["session_id"], cwd=info.cwd or None, include_partial_messages=True,
            permission_mode=entry.get("mode") or "default", can_use_tool=can_use_tool,
            **({"model": os.environ["SWITCHBOARD_MODEL"]} if os.environ.get("SWITCHBOARD_MODEL") else {}),  # ponytail: per-entry model setting later
            **({"cli_path": self.cli_path} if self.cli_path else {}))
        client = self.sdk.ClaudeSDKClient(options=opts)
        await client.connect()
        lv = holder["lv"] = self.live[entry["id"]] = Live(entry["id"], client)
        return lv

    async def send(self, entry, text):
        lv = await self._open(entry)
        if lv.working:
            raise RuntimeError("busy")
        lv.working, lv.last_active = True, time.time()
        await self._emit_state(entry["id"])
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
                    await self.broadcast({"type": "message", "entry": lv.entry_id, "message": h})
                elif type(m).__name__ == "ResultMessage" and m.is_error:
                    await self.broadcast({"type": "error", "entry": lv.entry_id, "code": "result_error", "message": str(m.result)[:500]})
        except Exception as e:  # a dead CLI process must not take the daemon down
            await self.broadcast({"type": "error", "entry": lv.entry_id, "code": "session_error", "message": str(e)[:500]})
        finally:
            lv.working = False
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
        if lv and lv.working:
            for fut in list(lv.pending.values()):  # a pending prompt is moot once interrupted
                if not fut.done():
                    fut.set_result((False, "Interrupted"))
            await lv.client.interrupt()

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
