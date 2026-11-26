"""Live sessions (phase 2): one ClaudeSDKClient per active entry, events fanned out to every subscriber.

Events (docs/protocol.md): state, delta, message, permission_request, permission_resolved, error.
"""
import functools, subprocess
import asyncio, dataclasses, difflib, json, os, shutil, sys, time, uuid
from .logs import log, scrub
from . import patches, peers

# one message from Claude Code (a Read of a big screenshot is one line of base64); the SDK's 1 MB default kills the session
MAX_MESSAGE = 64 * 1024 * 1024
IDLE_SETTING = "idle_close_minutes"
IDLE_CHOICES = (0, 10, 30, 60, 240)
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_B64 = 7_000_000  # ~5 MB of image per attachment
MAX_IMAGES = 10
TOOLS = ("mcp__switchboard__inbox", "mcp__switchboard__sessions", "mcp__switchboard__message_session", "mcp__switchboard__call_user",
         "mcp__switchboard__watch")  # allowed without asking
AGENT_WATCHES, AGENT_MIN_MINUTES = 10, 5  # an agent's own watches: how many, how often at most (each wake costs a turn)
AGENT_IDEAS = 3  # watch ideas one session may send the user per day
AGENT_INBOX = 6  # inbox notes one session may leave per hour
CALL_GAP = 180  # seconds between calls from one session: a guardrail against a session ringing over and over
MODES = {"default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def scrub_env():
    """Sessions must not inherit CLAUDE* vars (e.g. a daemon started from inside a Claude session):
    drop them from the daemon's own environment once, so every child starts clean."""
    for k in [k for k in os.environ if k.startswith("CLAUDE") and k != "CLAUDE_CONFIG_DIR"]:
        del os.environ[k]
    # The bundled (PyInstaller one-file) daemon: its loader points LD_LIBRARY_PATH at the bundle's own libraries, and every
    # child inherited that, so system tools in sessions and hooks broke (adb, Python's curses: "GLIBCXX_3.4.31 not found").
    # The loader has already used it; give children the system's value back (it saves the original as *_ORIG).
    if getattr(sys, "frozen", False):
        orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            os.environ["LD_LIBRARY_PATH"] = orig
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)


def login_path(shell=None, current=None):
    """The PATH sessions get: the user's login shell's first (what a terminal has: nvm's node, ~/.cargo/bin, ...), then
    the daemon's own, keeping only folders that exist. A daemon started by systemd or the desktop app had neither nvm's
    node (plugin hooks like ponytail's run `node` and quietly did nothing) nor a clean PATH (an AppImage's /tmp mounts)."""
    if os.name == "nt":
        return None
    shell = shell or os.environ.get("SHELL") or "/bin/bash"
    current = os.environ.get("PATH", "") if current is None else current
    return _login_path(shell, current)


@functools.lru_cache(maxsize=8)  # once per daemon (tests start many apps)
def _login_path(shell, current):
    try:  # interactive + login: nvm and most PATH setup live in .bashrc / .zshrc; the markers skip anything rc files print
        out = subprocess.run([shell, "-ilc", 'printf "\n<<SBPATH:%s>>\n" "$PATH"'], capture_output=True, text=True,
                             timeout=10, stdin=subprocess.DEVNULL, env={**os.environ, "TERM": "dumb"}).stdout
        theirs = out.split("<<SBPATH:", 1)[1].split(">>", 1)[0]
    except Exception:
        theirs = ""
    seen, keep = set(), []
    for d in theirs.split(":") + current.split(":"):
        if d and d not in seen and os.path.isdir(d):
            seen.add(d); keep.append(d)
    return ":".join(keep) or None


def _keep_synthetic():
    """Claude Code adds notes of its own as user messages (after Read shows an image: "[Image: original 1080x2400,
    displayed at …]"), flagged isSynthetic. The SDK's parser drops the flag, so they showed as if the user had sent
    them; history never had them (the transcript flags them isMeta, which get_session_messages() skips)."""
    try:
        from claude_agent_sdk._internal import message_parser, client as internal
    except ImportError:
        return
    orig = message_parser.parse_message

    def parse(data):
        m = orig(data)
        if isinstance(data, dict) and data.get("isSynthetic"):
            try:
                m.synthetic = True
            except AttributeError:
                pass
        return m
    message_parser.parse_message = internal.parse_message = parse


_keep_synthetic()


def as_history(msg):
    """Live SDK message -> the same shape get_session_messages() returns, so the UI renders both alike."""
    name = type(msg).__name__
    if name not in ("AssistantMessage", "UserMessage") or getattr(msg, "synthetic", False):
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
    h = {"type": role, "uuid": getattr(msg, "uuid", None) or uuid.uuid4().hex, "message": {"role": role, "content": content},
         "parent_tool_use_id": getattr(msg, "parent_tool_use_id", None)}  # set on a subagent's messages
    if getattr(msg, "tool_use_result", None) and isinstance(content, list):  # an edit's hunks, for line numbers in its diff
        tid = next((b.get("tool_use_id") for b in content if isinstance(b, dict) and b.get("type") == "tool_result"), None)
        if p := patches.from_result(tid, msg.tool_use_result):
            h["patches"] = p
    return h


def pick(es, want, where=""):
    """The session `want` means among `es` ({id, name, group, title?}): its id, Switchboard name, Claude Code's own name
    (title) or its project, any capitalisation; else a word in the name. Returns (entry, None) or (None, why + the closest)."""
    w = want.strip().lower()
    for key in ("id", "name", "title", "group"):
        hits = [e for e in es if str(e.get(key) or "").lower() == w]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, f'{len(hits)} sessions{where} match "{want}": ' + ", ".join(f'"{e["name"]}" (id {e["id"]})' for e in hits) + ". Use one of their ids."
    hits = [e for e in es if w and (w in e["name"].lower() or w in str(e.get("title") or "").lower())]
    if len(hits) == 1:
        return hits[0], None
    names = {str(x).lower(): e for e in es for x in (e["name"], e.get("title"), e.get("group")) if x}
    near = list(dict.fromkeys(names[n]["name"] for n in difflib.get_close_matches(w, list(names), n=4, cutoff=0.4)))
    return None, (f'No session{where} called "{want}".' + (f' Closest: {", ".join(chr(34) + n + chr(34) for n in near)}.' if near else "")
                  + " The sessions tool lists them all.")


def _yours(queue):
    """The user's messages in a mirrored queue. Claude Code also queues its own: a background task or subagent finishing
    (<task-notification>…) and empty entries; those are tracked so dequeues line up, never shown."""
    return [t for t in queue if t and not t.startswith(("<task-notification>", "<cross-session-message"))]


@dataclasses.dataclass
class Live:
    entry_id: str
    client: object
    reader: asyncio.Task = None
    started: float = dataclasses.field(default_factory=time.time)  # when this process started: older subagents died with theirs
    working: bool = False
    pending: dict = dataclasses.field(default_factory=dict)  # request id -> Future
    asks: dict = dataclasses.field(default_factory=dict)  # request id -> its permission_request event (sent again to clients that connect later)
    said: bool = False  # did this turn produce any assistant text (built-in commands may only return a result)
    queue: list = dataclasses.field(default_factory=list)  # messages sent while working, sent in order afterwards
    origin: dict = None  # set when a watch (its reply goes to the inbox) or another session started this turn
    turn_text: list = dataclasses.field(default_factory=list)
    last_usage: dict = None  # usage of the latest main-thread API call: how full the context is now
    cost: float = 0.0  # API-equivalent cost of this live session so far
    restart: bool = False  # close after this turn (a setting that only applies at start changed)
    interrupted: bool = False  # this turn was interrupted on purpose: Claude Code then ends it as an error with no message
    last_active: float = dataclasses.field(default_factory=time.time)
    tasks: dict = dataclasses.field(default_factory=dict)  # background tasks (commands, agents) by id: shown, stoppable; running ones keep it open
    unread: list = dataclasses.field(default_factory=list)  # messages in Claude Code's queue, not yet read (mirrored from its transcript)
    qwatch: asyncio.Task = None
    sent: list = dataclasses.field(default_factory=list)  # (text, uuid) of messages handed to the CLI mid-turn: what withdraw() cancels
    withdrawn: int = 0  # withdrawn messages whose dequeue hasn't shown up in the transcript yet


@dataclasses.dataclass
class Side:
    """A /btw side chat: a fork of the session (Claude Code's /btw isn't available through the SDK), so it knows the
    conversation so far while the real session's history never sees it. Deleted when the side chat closes."""
    entry_id: str
    fork: str
    client: object
    reader: asyncio.Task = None


BTW_TITLE = "Switchboard /btw side chat"  # the fork's title: kept out of the list of sessions to add
BTW_ASK = "(/btw: a side question about the conversation above. Answer briefly from what you already know; don't use tools or start any work.)\n\n"


class Runner:
    def __init__(self, store, sdk, broadcast):
        self.store, self.sdk, self.broadcast = store, sdk, broadcast
        self.live: dict[str, Live] = {}
        self.cli_path = os.environ.get("SWITCHBOARD_CLAUDE") or shutil.which("claude")
        self.commands: list[str] = []  # slash commands from the last session start (init message)
        self._models = None
        self.command_info: dict[str, dict] = {}  # slash command -> {description, argumentHint}, from Claude Code (with the models)
        self.on_turn_end = None  # async (entry_id, reply_text, origin) for turns a watch started
        self.usage: dict[str, dict] = {}  # entry id -> last usage event (survives the session closing)
        self.plans: dict[str, dict] = {}  # account ("" = machine login) -> {five_hour, seven_day: {used %, resets_at}}
        self.side: dict[str, Side] = {}  # entry id -> its open /btw side chat
        self.elsewhere = lambda: set()  # {session id} a process outside this daemon holds (set by the app)
        self.last_call: dict[str, float] = {}  # entry id -> when it last rang the user
        self.machine = "this machine"  # its name in clients (set by the app): how other machines address its sessions
        self.inbox_sent: dict[str, list[float]] = {}  # entry id -> when it left inbox notes (a few an hour)
        self.watches = None  # the scheduler (set by it): next run times, rescheduling after an agent's edit
        self.ideas: dict[str, list[float]] = {}  # entry id -> when it sent watch ideas (a few a day)

    # ---- state ----
    def state(self, entry_id):
        lv = self.live.get(entry_id)
        if not lv:
            return None  # not live: the API decides stopped/terminal/missing
        return "waiting" if lv.pending else ("working" if lv.working else "ready")

    def open_asks(self):
        """Questions and permission requests still waiting for an answer: a client that connects (or reconnects: a
        phone drops its connection in the background) after one was asked gets it too, or the session sat waiting
        on a question nobody could see."""
        return [a for lv in self.live.values() for a in lv.asks.values()]

    def unread(self, entry_id):
        """Messages waiting in this session's queue, not yet read by Claude (what a client that opens it later shows)."""
        lv = self.live.get(entry_id)
        return _yours(lv.unread) if lv else []

    async def withdraw(self, entry_id, text):
        """Take a message the user sent mid-turn back out of the CLI's queue, unread. False if Claude already read it."""
        lv = self.live.get(entry_id)
        uid = next((u for t, u in (lv.sent if lv else []) if t == text and t in lv.unread), None)
        if not uid:
            return False
        try:
            r = await lv.client._query._send_control_request({"subtype": "cancel_async_message", "message_uuid": uid})
        except Exception:
            log.exception("withdraw")
            return False
        if not (r or {}).get("cancelled"):
            return False
        lv.sent = [s for s in lv.sent if s[1] != uid]
        lv.unread.remove(text)
        lv.withdrawn += 1
        await self.broadcast({"type": "unread", "entry": entry_id, "items": _yours(lv.unread), "dropped": []})
        return True

    async def _watch_queue(self, lv, session_id):
        """Mirror Claude Code's message queue from its transcript: enqueue adds, remove (read mid-turn) and dequeue (taken
        as the next turn) take one off, popAll empties it. Clients show what's still unread, with a clock, at the bottom."""
        from . import patches
        path, pos = None, None
        def text(c):
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text").strip() or "(image)"
            return str(c or "")
        while self.live.get(lv.entry_id) is lv:
            await asyncio.sleep(0.7)
            path = path or patches._transcript(session_id)
            if not path:
                continue
            try:
                size = os.path.getsize(path)
                if pos is None or size < pos:
                    pos = size  # from now on: what's queued before the session opened isn't ours to show
                    continue
                if size == pos:
                    continue
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
                end = chunk.rfind(b"\n") + 1  # whole lines only; the rest next time
                pos += end
                before, dropped = list(lv.unread), []
                for line in chunk[:end].splitlines():
                    if b"<cross-session-message" in line and b'"isMeta"' in line:  # another session's SendMessage: not
                        try:  # in the SDK's stream, so shown from here (history reads it the same way: patches.with_mid_turn)
                            d = json.loads(line)
                        except ValueError:
                            continue
                        if patches.peer_message(d):
                            await self.broadcast({"type": "message", "entry": lv.entry_id, "message": {
                                "type": "user", "uuid": d["uuid"], "message": {"role": "user", "content": d["message"]["content"]}}})
                        continue
                    if b'"queue-operation"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    op, t = d.get("operation"), text(d.get("content"))
                    if op == "enqueue":
                        lv.unread.append(t)
                    elif op == "dequeue" and lv.withdrawn:  # Claude Code logs a cancel as a dequeue; withdraw() already took it off
                        lv.withdrawn -= 1
                    elif op == "dequeue" and lv.unread:
                        lv.unread.pop(0)
                    elif op == "remove":
                        if t in lv.unread:
                            lv.unread.remove(t)
                        elif lv.unread:
                            lv.unread.pop(0)
                    elif op == "popAll":
                        dropped += lv.unread; lv.unread = []
                if _yours(lv.unread) != _yours(before) or _yours(dropped):
                    await self.broadcast({"type": "unread", "entry": lv.entry_id, "items": _yours(lv.unread), "dropped": _yours(dropped)})
            except OSError:
                continue

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
            # claude.ai Artifacts: off by default for SDK sessions; CLAUDE_CODE_ARTIFACT turns them on (not in the docs: found in
            # anthropics/claude-code#93796, checked on 2.1.328). No browser on the session's machine: the card in the chat links it.
            env={"CLAUDE_CODE_ARTIFACT": "1", "CLAUDE_CODE_ARTIFACT_AUTO_OPEN": "0", **acct},
            **({"effort": entry["effort"]} if entry.get("effort") else {}),
            # SWITCHBOARD_MODEL overrides everything (tests run on Haiku); else the entry's choice; else Claude Code's default
            **({"model": m} if (m := os.environ.get("SWITCHBOARD_MODEL") or entry.get("model")) else {}),
            **({"cli_path": self.cli_path} if self.cli_path else {}),
            # Claude Code's own system prompt (how it works, stays brief, uses tools): the SDK's default is an empty one
            # plus its own name, so it signs letters with it and other sessions can address it
            system_prompt={"type": "preset", "preset": "claude_code", "append": self._who(entry)},
            mcp_servers={"switchboard": self._tools(entry["id"])}, allowed_tools=list(TOOLS),
            max_buffer_size=MAX_MESSAGE,
            # Claude in Chrome: the terminal turns it on by itself, SDK sessions only with --chrome (checked on 2.1.327)
            extra_args={"chrome": None})
        client = self.sdk.ClaudeSDKClient(options=opts)
        await client.connect()
        log.info("session %s live (entry %s, model %s, account %s)", entry["session_id"], entry["id"], entry.get("model") or "default", entry.get("account") or "machine")
        lv = holder["lv"] = self.live[entry["id"]] = Live(entry["id"], client)
        lv.reader = asyncio.get_running_loop().create_task(self._read(lv))
        lv.qwatch = asyncio.get_running_loop().create_task(self._watch_queue(lv, entry["session_id"]))
        await self._title(lv, entry)  # Claude Code's own name for it (ListAgents, SendMessage) = its Switchboard name
        return lv

    def _who(self, entry):
        return (f'In Switchboard (the app the user runs you in) this session is called "{entry["name"]}", on the machine '
                f'"{self.machine}". Sessions on the user’s other machines address it as "{entry["name"]}@{self.machine}". '
                "If the user renames it, the switchboard sessions tool shows the new name.")

    async def _title(self, lv, entry):
        try:
            await asyncio.wait_for(lv.client._query._send_control_request(
                {"subtype": "rename_session", "title": entry["name"], "source": "host", "session_id": entry["session_id"]}), 10)
        except Exception:
            pass  # an older Claude Code, or the demo: the name still matches from the next start

    async def _local(self, skip):
        """This machine's sessions for the switchboard tools: {id, name, group, title (Claude Code's own name), ...entry}."""
        async def title(e):
            try:
                info = await asyncio.to_thread(self.sdk.get_session_info, e["session_id"])
                return getattr(info, "custom_title", None)
            except Exception:
                return None
        es = [e for e in self.store.entries() if e["id"] != skip]
        titles = await asyncio.gather(*(title(e) for e in es))
        return [{**e, "group": e["grp"], "title": t} for e, t in zip(es, titles)]

    async def rename(self, entry):
        """Renamed in Switchboard: rename it in Claude Code too, so ListAgents/SendMessage and other sessions agree."""
        if (lv := self.live.get(entry["id"])):
            return await self._title(lv, entry)
        try:
            await asyncio.to_thread(self.sdk.rename_session, entry["session_id"], entry["name"])
        except Exception:
            pass  # not on disk yet (no first message): it's named when it starts

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
            me = self.store.entry(entry_id) or {}
            others = await self._local(entry_id)
            lines = [f'You are "{me.get("name")}" on {self.machine} ("{me.get("name")}@{self.machine}" from other machines). '
                     "Address others by name (or Claude Code's name, project or id); on another machine add @machine.", ""]
            lines += [f'- "{e["name"]}" ({e["group"]}' + (f', Claude Code calls it "{e["title"]}"' if e["title"] and e["title"] != e["name"] else "")
                      + f"): {view(e)}" for e in others] or ["No other sessions in Switchboard on this machine."]
            for p in await peers.all_peers(self.store):  # the user's other machines (same relay account, or linked by hand)
                es = None if p.get("online") is False else await peers.entries(p)  # offline on the relay: don't wait for it
                lines += [f"- {p['name']}: can't be reached right now"] if es is None else \
                    [f'- "{e["name"]}@{p["name"]}" ({e.get("group", "")}, on {p["name"]}): {e.get("state", "")}' for e in es]
            return say("\n".join(lines))

        async def message(args):
            want, text = str(args.get("session", "")).strip(), str(args.get("text", ""))
            me = self.store.entry(entry_id)
            name, _, machine = want.rpartition("@")
            if name and (p := await peers.find(self.store, machine)):  # "session@machine": one of the user's other machines
                if not text.strip():
                    return say("The message is empty.", True)
                es = await peers.entries(p)
                if es is None:
                    return say(f"{p['name']} can't be reached right now.", True)
                hit, why = pick(es, name, f" on {p['name']}")
                if not hit:
                    return say(why, True)
                err = await peers.send(p, hit["id"], text, f'{me["name"]}@{self.machine}')
                return say(f'Couldn\'t deliver to "{want}": {err}', True) if err else say(f'Delivered to "{hit["name"]}" on {p["name"]}.')
            local = await self._local(None)
            hit, why = pick(local, want)
            if not hit:
                there = []  # no machine given: maybe it's on one of the user's other machines
                for p in await peers.all_peers(self.store):
                    es = None if p.get("online") is False else await peers.entries(p)
                    if es and (h := pick(es, want)[0]):
                        there.append(f'"{h["name"]}@{p["name"]}"')
                return say(why + (f" On your other machines: {', '.join(there)} (send to that, with the @machine)." if there else ""), True)
            to = self.store.entry(hit["id"])
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

        async def call(args):
            text = str(args.get("message", "")).strip()
            if not text:
                return say("Say what you're calling about.", True)
            from . import relay
            if not relay.address(self.store):
                return say("The user has no phone set up for calls (this machine isn't signed in to their account, so their phone can't ring). Tell them in the chat instead.", True)
            wait = CALL_GAP - (time.time() - self.last_call.get(entry_id, 0))
            if wait > 0:
                return say(f"You called less than {CALL_GAP // 60} minutes ago; try again in {int(wait)} seconds, or write in the chat.", True)
            self.last_call[entry_id] = time.time()
            await self.broadcast({"type": "call", "entry": entry_id, "message": text})
            return say("Their phone is ringing. If they answer, what they say arrives as your next message, spoken: answer briefly, warmly and "
                       "conversationally (upbeat, exclamation marks where they fit), no markdown or lists, as it is read aloud. End your turn now. If they decline, carry on in the chat.")

        async def watch(args):
            """An agent's own watches: add (wakes this session, or only tells the user), list, remove."""
            from . import watches as wm
            op = str(args.get("action") or "list")
            me = self.store.entry(entry_id) or {}
            mine = [w for w in self.store.watches() if w.get("entry_id") == entry_id]
            nxt = lambda w: wm.shown(self.watches.next_run(w)) if self.watches else "?"
            if op == "list":
                if not mine:
                    return say("You have no watches. (The user's other watches aren't listed.)")
                return say("\n".join(f'{w["id"]}: "{w["name"]}" ({w["kind"]} {json.dumps(w["config"])}, '
                                      f'{"wakes you" if w["action"] == "wake" else "tells the user"}'
                                      f'{"" if w.get("enabled", True) else ", paused or ended"}, fired {(w["state"] or {}).get("runs", 0)}x) '
                                      f'next: {nxt(w)}' for w in mine))
            if op in ("remove", "pause", "resume"):
                w = next((w for w in mine if w["id"] == str(args.get("id") or "")), None)
                if not w:
                    return say("No watch of yours with that id (see action list).", True)
                if op == "remove":
                    self.store.remove_watch(w["id"])
                    return say(f'Removed "{w["name"]}".')
                self.store.update_watch(w["id"], enabled=op == "resume")
                return say(f'{"Resumed" if op == "resume" else "Paused"} "{w["name"]}".')
            if op == "edit":  # in place: same id; only what's given changes
                w = next((w for w in mine if w["id"] == str(args.get("id") or "")), None)
                if not w:
                    return say("No watch of yours with that id (see action list).", True)
                cfg = args.get("config")
                if isinstance(cfg, str):
                    try:
                        cfg = json.loads(cfg)
                    except ValueError:
                        return say("config must be a JSON object.", True)
                ch = {k: v for k, v in {"name": args.get("name"), "config": cfg, "instructions": args.get("instructions"),
                                        "action": None if args.get("wake") is None else ("wake" if args["wake"] else "notify")}.items() if v is not None}
                if not ch:
                    return say("Give what to change: name, config (replaces it whole), instructions or wake.", True)
                if (ch.get("config") or {}).get("every_minutes") is not None and int(ch["config"]["every_minutes"]) < AGENT_MIN_MINUTES:
                    return say(f"every_minutes is at least {AGENT_MIN_MINUTES} for a watch you set up.", True)
                spec = {**ch, "kind": w["kind"], "config": ch.get("config", w["config"]), "entry_id": entry_id}
                if problem := wm.problem(spec, self.store, MODES, partial=True):
                    return say(f"That change isn't valid: {problem}.", True)
                if (ra := (ch.get("config") or {}).get("run_at")) and wm.when(ra) <= time.time():
                    return say("That change isn't valid: run_at_is_in_the_past.", True)
                w = self.store.update_watch(w["id"], **ch, **({"enabled": True} if "config" in ch else {}))
                if self.watches:
                    self.watches.due.pop(w["id"], None)  # rescheduled from the new settings
                return say(f'Updated "{w["name"]}" (id {w["id"]}). Next: {nxt(w)}.')
            if op == "suggest":  # nothing fits: tell the user what watch would have, so they can decide whether to build it
                idea = str(args.get("idea") or "").strip()
                if not idea:
                    return say("Describe the watch you'd want in idea: what to watch, why, and the kind/settings you imagine.", True)
                now = time.time()
                recent = self.ideas[entry_id] = [t for t in self.ideas.get(entry_id, []) if t > now - 86400]
                if len(recent) >= AGENT_IDEAS:
                    return say(f"You've sent {AGENT_IDEAS} watch ideas today; mention this one in the chat instead.", True)
                recent.append(now)
                item = self.store.add_inbox(me.get("name") or "A session", "Watch idea: " + idea.splitlines()[0][:80], idea[:2000],
                                            entry_id=entry_id, from_agent=True)
                await self.broadcast({"type": "inbox", "item": item})
                return say("Sent to the user's inbox as a watch idea. Carry on without it for now.")
            if op != "add":
                return say("action is add, list, edit, pause, resume, remove or suggest.", True)
            if len(mine) >= AGENT_WATCHES:
                return say(f"You already have {AGENT_WATCHES} watches; remove one first.", True)
            cfg = args.get("config") or {}
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except ValueError:
                    return say("config must be a JSON object.", True)
            if cfg.get("every_minutes") is not None and int(cfg["every_minutes"]) < AGENT_MIN_MINUTES:
                return say(f"every_minutes is at least {AGENT_MIN_MINUTES} for a watch you set up.", True)
            if args.get("kind") != "schedule" and not cfg.get("every_minutes"):
                cfg["every_minutes"] = 15
            action = "notify" if args.get("wake") is False else "wake"
            spec = {"name": str(args.get("name") or "").strip(), "kind": args.get("kind"), "config": cfg,
                    "action": action, "entry_id": entry_id, "instructions": str(args.get("instructions") or "") or None}
            if problem := wm.problem(spec, self.store, MODES):
                return say(f"That watch isn't valid: {problem}.", True)
            w = self.store.add_watch(spec["name"], spec["kind"], cfg, action, entry_id, spec["instructions"], None)
            # the user sees what agents set up (and can pause or delete it in Watches)
            item = self.store.add_inbox(me.get("name") or "A session", f'Set up a watch: {spec["name"]}',
                                        f'{spec["kind"]} {json.dumps(cfg)}: ' + ("wakes it" if action == "wake" else "tells you"),
                                        watch_id=w["id"], entry_id=entry_id, from_agent=True)
            await self.broadcast({"type": "inbox", "item": item})
            return say(f'Watch "{spec["name"]}" set up (id {w["id"]}); first run {nxt(w)}. '
                       + ("When it fires you're woken with the instructions you gave and what it found."
                          if action == "wake" else "When it fires the user gets it in their inbox."))

        async def inbox(args):
            """A note to the user's inbox (their phone is notified), from this session, at most a few an hour."""
            title, body = str(args.get("title") or "").strip(), str(args.get("body") or "").strip()
            if not title:
                return say("Give it a title (one line), and the details in body.", True)
            now = time.time()
            recent = self.inbox_sent[entry_id] = [t for t in self.inbox_sent.get(entry_id, []) if t > now - 3600]
            if len(recent) >= AGENT_INBOX:
                return say(f"You've sent {AGENT_INBOX} inbox notes this hour; put the rest in one note later, or in the chat.", True)
            recent.append(now)
            me = self.store.entry(entry_id) or {}
            item = self.store.add_inbox(me.get("name") or "A session", title[:200], body[:8000], entry_id=entry_id, from_agent=True)
            await self.broadcast({"type": "inbox", "item": item})
            return say("In the user's inbox (their phone is notified). Opening it takes them to this session.")

        return sdk.create_sdk_mcp_server("switchboard", tools=[
            sdk.tool("sessions", "Who you are, and the other Claude sessions in Switchboard by name (and Claude Code's own name, "
                     "when it differs), with their state: this machine's, and (as name@machine) those on the user's other machines. "
                     "Stopped ones are included: unlike SendMessage, message_session can reach them.", {})(sessions),
            sdk.tool("message_session", "Send a message to another Switchboard session: its name from the sessions tool, Claude "
                     "Code's name for it, its project or its id, any capitalisation (name@machine for one on another machine). "
                     "If nothing matches, it says the closest names. "
                     "A stopped session is resumed to receive it. It arrives as a new prompt; the reply comes back the same way only "
                     "if that session chooses to answer.", {"session": str, "text": str})(message),
            sdk.tool("inbox", "Leave the user a note in their Switchboard inbox: their phone gets a notification, and tapping it opens "
                     "this session. For something they should see when they look, not now: a finished report, a result while they're away, "
                     f"something to decide later. title: one line; body: the details (markdown). At most {AGENT_INBOX} an hour; for something "
                     "urgent use call_user, for a normal answer just reply in the chat.", {"title": str, "body": str})(inbox),
            sdk.tool("call_user", "Phone the user: their phone rings, and if they answer it reads your message aloud and you talk, turn by turn. "
                     "Only for something that really needs them now (blocked, a decision only they can make, something urgent); a normal "
                     "question can wait in the chat. Keep the message short and spoken, without markdown, and warm and upbeat: say hi, "
                     "lead with good news, use exclamation marks where they fit (they lift the voice).", {"message": str})(call),
            sdk.tool("watch", "Your own watches: Switchboard checks something for you while you're idle and wakes you (or only "
                     "tells the user) when it fires. action: add | list | edit | pause | resume | remove (by id) | suggest. To add: name, kind and "
                     "config, plus instructions (what to do when woken). edit (by id) changes name, config (replaced whole), instructions or "
                     "wake in place. Kinds and config: schedule {every_minutes: N}, {daily_at: \"HH:MM\"} (fires every time) or "
                     "{run_at: \"YYYY-MM-DD HH:MM\"} (once, then it switches itself off); command {command, every_minutes, fire_on: \"change\"|\"failure\"}; http {url, every_minutes} "
                     "(up <-> down); github {repo: \"owner/name\", events: [issues, pulls, failed_runs], every_minutes}. Any config "
                     "can add active: {from: \"HH:MM\", to: \"HH:MM\", days: [1-7]}; quiet: true makes you answer NOTHING_NEW when "
                     "there's nothing to report (it then never reaches the user); until: \"YYYY-MM-DD HH:MM\" and/or max_runs: N end it "
                     "(switched off, kept); instructions_file: \"/path\" or skill: \"name\" (+ args) instead of pasted instructions, read "
                     "each time it fires. Times are this machine's local time; add, edit and list say when it runs next. Switchboard runs "
                     "watches, not you: if you're busy when one fires, the wake waits until your turn ends; if you're stopped, you're "
                     "started; a time missed while the machine was off is skipped. Each wake's reply also goes to the user's inbox. "
                     "wake: false only tells the user. At most "
                     f"{AGENT_WATCHES} watches, checked at most every {AGENT_MIN_MINUTES} minutes: each wake costs a turn, so "
                     "only watch what's worth it. The user sees each one you set up and can remove it. If no kind or setting fits what you "
                     "need, use suggest with idea: what you'd want to watch, why, and the kind/settings you imagine; it goes to the "
                     "user as a watch idea (a few a day).",
                     {"type": "object", "properties": {
                         "action": {"type": "string", "enum": ["add", "list", "edit", "pause", "resume", "remove", "suggest"]},
                         "idea": {"type": "string", "description": "suggest: the watch you'd want, why, and how you imagine it"},
                         "name": {"type": "string"}, "kind": {"type": "string", "enum": ["schedule", "command", "http", "github"]},
                         "config": {"type": "object"}, "instructions": {"type": "string"}, "wake": {"type": "boolean"},
                         "id": {"type": "string", "description": "the watch to edit, pause, resume or remove"}},
                      "required": ["action"]})(watch),
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
        if lv.working and not origin and not lv.queue:
            # like Claude Code: the CLI reads a message sent mid-turn after its current step (a tool call finishing),
            # or as the next turn if this one has no step left. Clients move it into the chat now.
            lv.sent = [s for s in lv.sent if s[0] in lv.unread][-20:] + [(text, uid := str(uuid.uuid4()))]
            await self._query(lv, text, images, uid)  # clients keep it at the bottom, unread, until the queue says it was read
            from . import patches
            if not patches._transcript(entry["session_id"]):  # no transcript to watch (the demo): read as soon as handed over
                await self.broadcast({"type": "unread", "entry": entry["id"], "items": [text], "dropped": []})
                await self.broadcast({"type": "unread", "entry": entry["id"], "items": [], "dropped": []})
            return
        if lv.working:  # a watch's or another session's message waits for its own turn: its reply is that turn's text
            lv.queue.append((entry, text, images, origin))
            await self.broadcast({"type": "queued", "entry": entry["id"], "count": len(lv.queue)})
            return
        lv.working, lv.said, lv.interrupted, lv.last_active = True, False, False, time.time()
        lv.origin, lv.turn_text = origin, []
        await self._emit_state(entry["id"])
        if origin:  # clients show what they typed themselves; a watch's prompt has to be announced
            await self.broadcast({"type": "message", "entry": entry["id"], "message": {
                "type": "user", "uuid": uuid.uuid4().hex, "message": {"role": "user", "content": text}}})
        await self._query(lv, text, images)

    async def _query(self, lv, text, images, uid=None):
        if images or uid:  # a uuid lets withdraw() cancel it while it waits in the CLI's queue
            content = [{"type": "image", "source": {"type": "base64", "media_type": im["media_type"], "data": im["data"]}} for im in images or []]
            content.append({"type": "text", "text": text or "(image)"})

            async def one():
                # text alone as a string: Claude Code's queue log leaves out content given as blocks, and the mirror needs it
                yield {"type": "user", "message": {"role": "user", "content": content if images else text}, "parent_tool_use_id": None, "session_id": "default", **({"uuid": uid} if uid else {})}
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
                if sub in ("task_started", "task_progress", "task_notification", "task_updated") and data.get("task_id"):
                    self._task_event(lv, sub, data)
                    await self.broadcast({"type": "tasks", "entry": lv.entry_id, "tasks": self.tasks(lv.entry_id)})
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
        if (fut := (lv.origin or {}).get("reply")) and not fut.done():  # a call waits for what Claude says back
            fut.set_result("\n\n".join(lv.turn_text).strip())
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
        asyncio.get_running_loop().create_task(self._plan(lv))

    async def _plan(self, lv):
        """The plan's limits (5-hour and weekly use, %), as Claude Code's /usage shows them: asked after each turn, per
        account (a session's account, or "" for the machine login). Costs no tokens."""
        entry = self.store.entry(lv.entry_id) or {}
        acct = entry.get("account") or ""
        try:
            r = await asyncio.wait_for(lv.client._query._send_control_request({"subtype": "get_usage", "skip_behaviors": True}), 15)
        except Exception:
            return  # an older Claude Code, the demo, or the API didn't answer: the last numbers stay
        lim = (r or {}).get("rate_limits") or {}
        plan = {k: {"used": round(w["utilization"]), "resets_at": w.get("resets_at")}
                for k in ("five_hour", "seven_day") if isinstance(w := lim.get(k), dict) and w.get("utilization") is not None}
        if plan:
            self.plans[acct] = plan
            await self.broadcast({"type": "plan", "account": acct, "plan": plan})

    async def _ask(self, lv, tool, inp, ctx=None):
        """Three kinds of request, all through can_use_tool: Claude's questions (AskUserQuestion), plan approval
        (ExitPlanMode) and ordinary tool permission, which can be remembered for the session or for good."""
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        lv.pending[rid] = fut
        kind = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}.get(tool, "tool")
        suggestions = list(getattr(ctx, "suggestions", None) or [])
        remember = sorted({s.destination for s in suggestions if getattr(s, "destination", None) in ("session", "localSettings")})
        lv.asks[rid] = {"type": "permission_request", "entry": lv.entry_id, "request_id": rid, "tool": tool,
                        "kind": kind, "input": inp, "remember": remember if kind == "tool" else []}
        await self.broadcast(lv.asks[rid])
        await self._emit_state(lv.entry_id)
        try:
            a = await fut
        finally:
            lv.pending.pop(rid, None)
            lv.asks.pop(rid, None)
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
                self.command_info = {c["name"]: c for c in info.get("commands") or [] if isinstance(c, dict) and c.get("name")}
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

    # ---- background tasks (Bash run_in_background, background agents): what Claude Code reports about them ----
    KEEP_FINISHED = 20  # finished ones kept to look back on (the app lists them under Background tasks)

    def _task_event(self, lv, sub, data):
        tid = data["task_id"]
        t = lv.tasks.setdefault(tid, {"id": tid, "status": "running", "started": int(time.time() * 1000)})
        for k in ("description", "task_type", "tool_use_id", "last_tool_name", "summary", "output_file"):
            if data.get(k):
                t["type" if k == "task_type" else k] = data[k]
        status = data.get("status") if sub == "task_notification" else (data.get("patch") or {}).get("status") if sub == "task_updated" else None
        if status in ("completed", "failed", "stopped", "killed"):  # Claude Code says "killed" in task_updated, "stopped" elsewhere
            t["status"], t["ended"] = "stopped" if status == "killed" else status, int(time.time() * 1000)
        done = [x for x in lv.tasks.values() if x["status"] != "running"]
        for old in sorted(done, key=lambda x: x.get("ended", 0))[:-self.KEEP_FINISHED]:
            lv.tasks.pop(old["id"], None)

    def tasks(self, entry_id):
        lv = self.live.get(entry_id)
        return sorted(lv.tasks.values(), key=lambda t: t["started"]) if lv else []

    def running_tasks(self, lv):
        return [t for t in lv.tasks.values() if t["status"] == "running"]

    async def stop_task(self, entry_id, task_id):
        lv = self.live.get(entry_id)
        if not lv or task_id not in lv.tasks:
            raise LookupError("no_such_task")
        await lv.client.stop_task(task_id)  # Claude Code reports it ended with a task_updated

    async def stop(self, entry_id):
        lv = self.live.pop(entry_id, None)
        if not lv:
            return
        if lv.tasks:  # its tasks ended with the process
            await self.broadcast({"type": "tasks", "entry": entry_id, "tasks": []})
        log.info("session for entry %s closed", entry_id)
        for fut in lv.pending.values():
            if not fut.done():
                fut.set_result({"allow": False, "message": "Session stopped", "remember": None, "answers": None, "response": None})
        if lv.reader and not lv.reader.done():
            lv.reader.cancel()
        if lv.qwatch and not lv.qwatch.done():
            lv.qwatch.cancel()
        if lv.unread:  # still unread when the session closed: back to the user's message box
            dropped, lv.unread = _yours(lv.unread), []
            await self.broadcast({"type": "unread", "entry": entry_id, "items": [], "dropped": dropped})
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
            if not lv.working and not lv.pending and not self.running_tasks(lv) and not lv.queue and now - lv.last_active > minutes * 60:
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

    # ---- /btw side chats ----
    async def btw(self, entry_id, text):
        entry = self.store.entry(entry_id)
        sd = self.side.get(entry_id)
        if not sd:
            info = self.sdk.get_session_info(entry["session_id"])
            if info is None:
                raise LookupError("nothing_to_ask_about")  # a new session with nothing said yet
            fork = (await asyncio.to_thread(self.sdk.fork_session, entry["session_id"], None, None, BTW_TITLE)).session_id
            acct = self.store.account_env(entry["account"]) if entry.get("account") else {}

            async def no_tools(tool, inp, ctx):
                return self.sdk.PermissionResultDeny(message="Side questions don't use tools: answer from what you know.", interrupt=False)

            opts = self.sdk.ClaudeAgentOptions(
                resume=fork, cwd=info.cwd or None, include_partial_messages=True, permission_mode="default", can_use_tool=no_tools,
                **({"env": acct} if acct else {}),
                **({"model": m} if (m := os.environ.get("SWITCHBOARD_MODEL") or entry.get("model")) else {}),
                **({"cli_path": self.cli_path} if self.cli_path else {}), max_buffer_size=MAX_MESSAGE)
            client = self.sdk.ClaudeSDKClient(options=opts)
            await client.connect()
            sd = self.side[entry_id] = Side(entry_id, fork, client)
            sd.reader = asyncio.get_running_loop().create_task(self._read_side(sd))
            log.info("side chat for entry %s (fork %s)", entry_id, fork)
        await self.broadcast({"type": "btw", "entry": entry_id, "kind": "asked", "text": text})
        await sd.client.query(BTW_ASK + text)

    async def _read_side(self, sd):
        try:
            async for m in sd.client.receive_messages():
                n = type(m).__name__
                if n == "StreamEvent":
                    delta = (m.event.get("delta") or {}) if m.event.get("type") == "content_block_delta" else {}
                    if delta.get("type") == "text_delta":
                        await self.broadcast({"type": "btw", "entry": sd.entry_id, "kind": "delta", "text": delta.get("text", "")})
                elif n == "AssistantMessage" and not getattr(m, "parent_tool_use_id", None):
                    text = "".join(getattr(b, "text", "") for b in m.content if type(b).__name__ == "TextBlock")
                    if text:
                        await self.broadcast({"type": "btw", "entry": sd.entry_id, "kind": "answer", "text": text})
                elif n == "ResultMessage":
                    await self.broadcast({"type": "btw", "entry": sd.entry_id, "kind": "done", "error": bool(m.is_error)})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("side chat for entry %s failed: %s", sd.entry_id, scrub(str(e)))
            await self.broadcast({"type": "btw", "entry": sd.entry_id, "kind": "done", "error": True})

    async def close_btw(self, entry_id):
        sd = self.side.pop(entry_id, None)
        if not sd:
            return
        if sd.reader and not sd.reader.done():
            sd.reader.cancel()
        try:
            await sd.client.disconnect()
        except Exception:
            pass
        try:
            await asyncio.to_thread(self.sdk.delete_session, sd.fork)  # the side chat leaves nothing behind
        except Exception as e:
            log.warning("couldn't delete side chat fork %s: %s", sd.fork, e)
        await self.broadcast({"type": "btw", "entry": entry_id, "kind": "closed"})

    async def close_all(self):
        for eid in list(self.side):
            await self.close_btw(eid)
        for eid in list(self.live):
            await self.stop(eid)
