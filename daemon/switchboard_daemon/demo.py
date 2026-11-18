"""Demo mode (`serve --demo`): canned sessions and history instead of this machine's real ones.
For UI tests (repeatable, no Claude usage, nothing real touched), screenshots and trying the app."""
import dataclasses, os, time

__version__ = "demo"

NOW = int(time.time() * 1000)
H = 3600 * 1000


@dataclasses.dataclass
class DemoSession:
    session_id: str
    summary: str
    last_modified: int
    cwd: str
    git_branch: str = None
    custom_title: str = None
    first_prompt: str = None
    tag: str = None
    created_at: int = None
    file_size: int = None


SESSIONS = [
    DemoSession("demo-1", "Deploy #52 rolled back", NOW - 2 * 60 * 1000, "/srv/agents/shorts-app", "fix/feed-dash-url"),
    DemoSession("demo-2", "Bump Glance to 0.8.4", NOW - 3 * 24 * H, "/srv/agents/glance", "main"),
    DemoSession("demo-3", "Investigate slow feed", NOW - 7 * 24 * H, "/srv/agents/reddit-rss", "main"),
    DemoSession("demo-4", "Untitled session", NOW - 14 * 24 * H, "/home/demo"),
]

HISTORY = [
    {"type": "user", "uuid": "m1", "message": {"role": "user", "content": "Why did the deploy roll back?"}},
    {"type": "assistant", "uuid": "m2", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "gh run view 4410 --log-failed | tail -20"}}]}},
    {"type": "user", "uuid": "m3", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "health.sh: GET /api/feed -> 500\nKeyError: 'dash_url'"}]}},
    {"type": "assistant", "uuid": "m4", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "The new /api/feed handler expects dash_url, but older videos don't have it. The rollback worked; live is healthy."}]}},
    {"type": "assistant", "uuid": "m5", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t2", "name": "TodoWrite", "input": {"todos": [
            {"content": "Find the cause", "status": "completed", "activeForm": "Finding the cause"},
            {"content": "Fix the handler", "status": "in_progress", "activeForm": "Fixing the handler"},
            {"content": "Add a test", "status": "pending", "activeForm": "Adding a test"}]}}]}},
    {"type": "user", "uuid": "m6", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": "Todos have been modified successfully."}]}},
    {"type": "assistant", "uuid": "m7", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": "server/app.py",
            "old_string": "def feed(v):\n    dash = v['dash_url']\n    return dash", "new_string": "def feed(v):\n    dash = v.get('dash_url')\n    return dash"}}]}},
    {"type": "assistant", "uuid": "m8", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t4", "name": "Bash", "input": {"command": "pytest -q"}}]}},
    {"type": "user", "uuid": "m9", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t4", "content": "\n".join(f"test_{n} PASSED" for n in range(1, 21))}]}},
]


def list_sessions():
    return list(SESSIONS)


# subagents of demo-1: one spawned another, which spawned a third (the last two still running)
SUBAGENTS = {
    "demo-1": [
        {"id": "a1", "description": "Investigate the failed deploy", "type": "general-purpose", "parent": None, "tool_use_id": "t9", "finished": True, "stopped": False, "updated": NOW - 5 * 60 * 1000},
        {"id": "a2", "description": "Read the feed handler", "type": "Explore", "parent": "a1", "tool_use_id": "t10", "finished": False, "stopped": False, "updated": NOW - 4 * 60 * 1000},
        {"id": "a3", "description": "Check which videos lack dash_url", "type": "general-purpose", "parent": "a2", "tool_use_id": "t11", "finished": False, "stopped": False, "updated": NOW - 60 * 1000},
        {"id": "a4", "description": "Summarise the logs", "type": "Explore", "parent": None, "tool_use_id": "t12", "finished": True, "stopped": False, "updated": NOW - 3 * 60 * 1000},
    ],
}


def subagent_tree(session_id):
    # the unfinished ones are "still working": active now, as a real running subagent's transcript would be
    now = int(time.time() * 1000)
    return [a if a["finished"] or a["stopped"] else {**a, "updated": now} for a in SUBAGENTS.get(session_id, [])]


def get_subagent_messages(session_id, agent_id, limit=None, offset=0):
    sa = next((a for a in SUBAGENTS.get(session_id, []) if a["id"] == agent_id), None)
    if not sa:
        return []
    kids = [a for a in SUBAGENTS[session_id] if a["parent"] == agent_id]
    msgs = [{"type": "user", "uuid": f"{agent_id}-1", "parent_tool_use_id": sa["tool_use_id"], "message": {"role": "user", "content": sa["description"]}},
            {"type": "assistant", "uuid": f"{agent_id}-2", "parent_tool_use_id": sa["tool_use_id"], "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"{agent_id}-g", "name": "Grep", "input": {"pattern": "dash_url"}}]}},
            {"type": "user", "uuid": f"{agent_id}-3", "parent_tool_use_id": sa["tool_use_id"], "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"{agent_id}-g", "content": "server/app.py:212"}]}}]
    for k in kids:
        msgs.append({"type": "assistant", "uuid": f"{agent_id}-k{k['id']}", "parent_tool_use_id": sa["tool_use_id"], "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": k["tool_use_id"], "name": "Agent", "input": {"description": k["description"], "prompt": k["description"]}}]}})
    if sa["finished"]:
        msgs.append({"type": "assistant", "uuid": f"{agent_id}-9", "parent_tool_use_id": sa["tool_use_id"], "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"Finished: {sa['description']}."}]}})
    return msgs


def get_session_info(session_id):
    return next((s for s in SESSIONS if s.session_id == session_id), None)


LIVE_LOG = {}  # session id -> turns taken live: like a real transcript, history read later includes them


def get_session_messages(session_id, limit=None, offset=0):
    return (HISTORY + LIVE_LOG.get(session_id, []))[offset:offset + limit if limit else None] if get_session_info(session_id) else []


# ---- fake live sessions (phase 2): same class names the runner looks for ----
import asyncio


@dataclasses.dataclass
class TextBlock:
    text: str


@dataclasses.dataclass
class ThinkingBlock:
    thinking: str
    signature: str = ""


@dataclasses.dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclasses.dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclasses.dataclass
class AssistantMessage:
    content: list
    usage: dict = None
    parent_tool_use_id: str = None


@dataclasses.dataclass
class UserMessage:
    content: list
    parent_tool_use_id: str = None


@dataclasses.dataclass
class StreamEvent:
    event: dict


@dataclasses.dataclass
class SystemMessage:
    subtype: str
    data: dict


@dataclasses.dataclass
class ResultMessage:
    is_error: bool = False
    result: str = ""
    total_cost_usd: float = 0.0123
    model_usage: dict = dataclasses.field(default_factory=lambda: {"demo-model": {"contextWindow": 200000}})


@dataclasses.dataclass
class ClaudeAgentOptions:
    resume: str = None
    session_id: str = None
    cwd: str = None
    include_partial_messages: bool = False
    permission_mode: str = "default"
    can_use_tool: object = None
    cli_path: str = None
    model: str = None
    env: dict = None
    effort: str = None
    mcp_servers: dict = None
    allowed_tools: list = None
    max_buffer_size: int = None


def tool(name, description, schema):  # in-process MCP tools: the demo calls them directly
    return lambda fn: (name, fn)


def create_sdk_mcp_server(name, version="1.0.0", tools=None):
    return dict(tools or [])


@dataclasses.dataclass
class PermissionResultAllow:
    updated_input: dict = None
    updated_permissions: list = None
    behavior: str = "allow"


@dataclasses.dataclass
class _Suggestion:
    destination: str
    type: str = "addRules"


class _Ctx:
    suggestions = [_Suggestion("session"), _Suggestion("localSettings")]


@dataclasses.dataclass
class PermissionResultDeny:
    message: str = ""
    behavior: str = "deny"


class ClaudeSDKClient:
    """Scripted stand-in: streams a reply; "run" asks to use Bash; "slow" works for 30 s (interruptible)."""
    def __init__(self, options):
        self.options, self.prompt, self.interrupted, self.images, self.started = options, "", False, 0, False
        self.go = asyncio.Event()

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, text):
        self.interrupted, self.images = False, 0
        if isinstance(text, str):
            self.prompt = text
            self.go.set()
            return
        async for msg in text:  # a user message with content blocks (images)
            blocks = msg["message"]["content"]
            self.images = sum(b["type"] == "image" for b in blocks)
            self.prompt = " ".join(b.get("text", "") for b in blocks if b["type"] == "text")
        self.go.set()

    async def receive_messages(self):
        """Like the real client: everything the CLI says, including turns it takes on its own."""
        while True:
            await self.go.wait()
            self.go.clear()
            async for m in self.receive_response():
                yield m

    async def interrupt(self):
        self.interrupted = True

    async def set_permission_mode(self, mode):
        self.options.permission_mode = mode

    async def set_model(self, model):
        self.options.model = model

    async def get_server_info(self):
        return {"models": [{"value": "default", "displayName": "Default (recommended)", "description": "Opus 5.5", "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "max"]},
                           {"value": "sonnet", "displayName": "Sonnet 5.5", "description": "Most efficient for simpler tasks"},
                           {"value": "haiku", "displayName": "Haiku 5.5", "description": "Fastest"}]}

    async def receive_response(self):
        text = self.prompt
        if self.options.session_id and not get_session_info(self.options.session_id):  # a new session now exists on "disk"
            SESSIONS.append(DemoSession(self.options.session_id, text[:40] or "New session", int(time.time() * 1000), self.options.cwd))
        if text:  # the prompt is in the transcript from the start, as in a real session
            LIVE_LOG.setdefault(self.options.resume or self.options.session_id, []).append(
                {"type": "user", "uuid": f"live-{time.time_ns()}-u", "message": {"role": "user", "content": text}})
        if not self.started:
            self.started = True
            yield SystemMessage("init", {"slash_commands": ["compact", "clear", "usage", "deploy", "yt-add"]})
        if text.startswith("/usage"):  # built-in commands answer only with a result
            yield ResultMessage(result="**Plan usage:** 12% of this week's limit")
            return
        if self.images:
            yield AssistantMessage([TextBlock(f"I can see {self.images} image(s).")])
        tools = (self.options.mcp_servers or {}).get("switchboard", {})
        if text.startswith("tell ") and ":" in text:  # "tell <session>: <words>" uses message_session
            to, words = text[5:].split(":", 1)
            args = {"session": to.strip(), "text": words.strip()}
            yield AssistantMessage([ToolUseBlock("tm1", "mcp__switchboard__message_session", args)])
            r = await tools["message_session"](args)
            yield UserMessage([ToolResultBlock("tm1", r["content"][0]["text"], bool(r.get("is_error")))])
            yield AssistantMessage([TextBlock(r["content"][0]["text"])])
        if text == "list sessions":
            yield AssistantMessage([TextBlock((await tools["sessions"]({}))["content"][0]["text"])])
        for word in "Looking into it.".split(" "):
            yield StreamEvent({"type": "content_block_delta", "delta": {"type": "text_delta", "text": word + " "}})
            await asyncio.sleep(0.05)
        if "think" in text.lower():  # thinking, streamed then as a block
            for w in "Weighing the two options first.".split(" "):
                yield StreamEvent({"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": w + " "}})
                await asyncio.sleep(0.05)
            yield AssistantMessage([ThinkingBlock("Weighing the two options first.")])
        if "subagent" in text.lower():  # work delegated to a subagent: its messages carry parent_tool_use_id
            yield AssistantMessage([ToolUseBlock("ag1", "Agent", {"description": "Search the codebase", "prompt": "Find where dash_url is read"})])
            yield AssistantMessage([ToolUseBlock("sg1", "Grep", {"pattern": "dash_url"})], parent_tool_use_id="ag1")
            yield UserMessage([ToolResultBlock("sg1", "server/app.py:212")], parent_tool_use_id="ag1")
            yield AssistantMessage([TextBlock("Found it in server/app.py line 212.")], parent_tool_use_id="ag1")
            yield UserMessage([ToolResultBlock("ag1", "dash_url is read in server/app.py:212")])
        if self.options.effort and "effort" in text.lower():
            yield AssistantMessage([TextBlock(f"(effort: {self.options.effort})")])
        if "ask me" in text.lower():  # a clarifying question (AskUserQuestion)
            q = {"question": "Which database should I use?", "header": "Database", "multiSelect": False,
                 "options": [{"label": "SQLite", "description": "One file, no server"}, {"label": "Postgres", "description": "A real server"}]}
            r = await self.options.can_use_tool("AskUserQuestion", {"questions": [q]}, _Ctx())
            got = (r.updated_input or {}) if isinstance(r, PermissionResultAllow) else {}
            yield AssistantMessage([TextBlock(f"You picked: {got.get('answers') or got.get('response') or 'nothing (' + r.message + ')'}")])
            yield ResultMessage()
            return
        if "plan" in text.lower():  # plan mode: propose a plan for approval (ExitPlanMode)
            r = await self.options.can_use_tool("ExitPlanMode", {"plan": "## Plan\n1. Add the table\n2. Write the migration\n3. Test it"}, _Ctx())
            yield AssistantMessage([TextBlock("Plan approved, starting." if isinstance(r, PermissionResultAllow) else f"Revising the plan. ({r.message})")])
            yield ResultMessage()
            return
        if "run" in text.lower():
            r = None if self.options.permission_mode in ("auto", "bypassPermissions", "acceptEdits") else \
                await self.options.can_use_tool("Bash", {"command": "git status"}, _Ctx())
            ok = r is None or isinstance(r, PermissionResultAllow)
            if isinstance(r, PermissionResultAllow) and r.updated_permissions:
                yield AssistantMessage([TextBlock(f"(remembered for: {r.updated_permissions[0].destination})")])
            yield AssistantMessage([ToolUseBlock("t1", "Bash", {"command": "git status"})])
            yield UserMessage([ToolResultBlock("t1", "On branch main, nothing to commit" if ok else "Permission denied", not ok)])
        if "background" in text.lower():  # a background command: Claude reports back later, unprompted
            yield SystemMessage("task_started", {"task_id": "bg1", "task_type": "local_bash"})

            async def later():
                await asyncio.sleep(float(os.environ.get("SB_DEMO_BG_SECONDS", 1)))
                self.prompt = "background task finished"
                self.go.set()
            asyncio.get_running_loop().create_task(later())
        if text == "background task finished":
            yield SystemMessage("task_notification", {"task_id": "bg1", "status": "completed"})
        if "slow" in text.lower():
            for _ in range(300):
                if self.interrupted:
                    yield UserMessage([TextBlock("[Request interrupted by user]")])
                    yield ResultMessage(is_error=True, result=None)  # as real Claude Code ends it
                    return
                await asyncio.sleep(0.1)
        acct = " (token account)" if (self.options.env or {}).get("CLAUDE_CODE_OAUTH_TOKEN") else ""
        done = f"Done: {text}" + (f" [{self.options.model}]" if self.options.model else "") + acct
        LIVE_LOG.setdefault(self.options.resume or self.options.session_id, []).append(
            {"type": "assistant", "uuid": f"live-{time.time_ns()}-a", "message": {"role": "assistant", "content": [{"type": "text", "text": done}]}})
        yield AssistantMessage([TextBlock(done)],
                               usage={"input_tokens": 1200, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 800})
        yield ResultMessage()
