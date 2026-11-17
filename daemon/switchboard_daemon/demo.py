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


def get_session_info(session_id):
    return next((s for s in SESSIONS if s.session_id == session_id), None)


def get_session_messages(session_id, limit=None, offset=0):
    return HISTORY[offset:offset + limit if limit else None] if get_session_info(session_id) else []


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
        if not self.started:
            self.started = True
            yield SystemMessage("init", {"slash_commands": ["compact", "clear", "usage", "deploy", "yt-add"]})
        if text.startswith("/usage"):  # built-in commands answer only with a result
            yield ResultMessage(result="**Plan usage:** 12% of this week's limit")
            return
        if self.images:
            yield AssistantMessage([TextBlock(f"I can see {self.images} image(s).")])
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
                    yield ResultMessage(is_error=False, result="interrupted")
                    return
                await asyncio.sleep(0.1)
        acct = " (token account)" if (self.options.env or {}).get("CLAUDE_CODE_OAUTH_TOKEN") else ""
        yield AssistantMessage([TextBlock(f"Done: {text}" + (f" [{self.options.model}]" if self.options.model else "") + acct)],
                               usage={"input_tokens": 1200, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 800})
        yield ResultMessage()
