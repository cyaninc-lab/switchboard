#!/usr/bin/env python3
"""Phase 0 spike: prove the Agent SDK does what Switchboard's daemon needs.

Checks: list_sessions, live multi-turn session, can_use_tool allow + deny, interrupt,
close + resume with context, memory of the claude process. Optional: resume under another
account (set SPIKE_ALT_TOKEN to a `claude setup-token` token).

Run with CLAUDE* env vars stripped (a claude spawned from inside a session inherits them):
  env $(env | grep -o '^CLAUDE[A-Z_]*' | sed 's/^/-u /') .venv/bin/python spike/spike.py
"""
import asyncio, os, subprocess, sys, tempfile, time
from claude_agent_sdk import (ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, ResultMessage,
                              TextBlock, ToolUseBlock, PermissionResultAllow, PermissionResultDeny,
                              list_sessions, get_session_messages)

MODEL = os.environ.get("SPIKE_MODEL", "claude-haiku-5-5")  # ponytail: cheapest model, the spike tests plumbing not smarts
WORK = tempfile.mkdtemp(prefix="sb-spike-")
results = {}
asked = []  # (tool, input) seen by can_use_tool


def check(name, ok, detail=""):
    results[name] = ok = bool(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def can_use_tool(tool, inp, ctx):
    asked.append((tool, inp))
    cmd = inp.get("command", "")
    if "touch denied" in cmd:
        return PermissionResultDeny(message="Denied by the Switchboard spike")
    return PermissionResultAllow()


def opts(**kw):
    return ClaudeAgentOptions(model=MODEL, cwd=WORK, permission_mode="default",
                              setting_sources=[],  # isolate from the user's allow rules so the callback fires
                              can_use_tool=can_use_tool, **kw)


async def turn(client, prompt):
    """Send one message, return (text, session_id, tool_uses)."""
    await client.query(prompt)
    text, sid, tools = [], None, []
    async for m in client.receive_response():
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, TextBlock): text.append(b.text)
                elif isinstance(b, ToolUseBlock): tools.append(b.name)
        elif isinstance(m, ResultMessage):
            sid = m.session_id
    return " ".join(text), sid, tools


def claude_rss_mib():
    out = subprocess.run(["ps", "-eo", "rss,args"], capture_output=True, text=True).stdout
    rows = [int(l.split()[0]) for l in out.splitlines()[1:] if "_bundled/claude" in l and "grep" not in l]
    return sum(rows) / 1024, len(rows)


async def main():
    s = list_sessions(limit=5)
    check("list_sessions", isinstance(s, list), f"{len(s)} recent, newest: {s[0].summary[:50]!r}" if s else "none")

    async with ClaudeSDKClient(options=opts()) as c:
        t0 = time.time()
        # echo is read-only, so Claude Code auto-approves it without asking can_use_tool
        text, sid, tools = await turn(c, "Use the Bash tool to run exactly: echo spike-ok-7731 . Then reply with just DONE.")
        check("first turn", "Bash" in tools and sid, f"{time.time()-t0:.1f}s, sid {sid}")
        before = len(asked)
        await turn(c, "Use the Bash tool to run exactly: mkdir allowed-dir . Then reply with just DONE.")
        check("allow via can_use_tool", len(asked) > before and os.path.isdir(os.path.join(WORK, "allowed-dir")),
              f"asked for: {[t for t, _ in asked[before:]]}")
        rss, n = claude_rss_mib()
        check("memory measured", n > 0, f"{rss:.0f} MiB across {n} claude process(es)")

        text2, _, _ = await turn(c, "What exact string did that command print? Reply with only the string.")
        check("multi-turn context", "spike-ok-7731" in text2, repr(text2[:60]))

        before = len(asked)
        await turn(c, "Use the Bash tool to run exactly: touch denied.txt . If it is refused, reply REFUSED.")
        check("deny via can_use_tool", len(asked) > before and not os.path.exists(os.path.join(WORK, "denied.txt")))

        # interrupt mid-task (not `sleep N &&`: Claude Code blocks long foreground sleeps, which fakes a pass): start a slow command, interrupt after a few seconds, drain the buffer
        await c.query("Use the Bash tool to run exactly: python3 -c \"import time,pathlib; time.sleep(25); pathlib.Path('late-file').touch()\" . Then report the output.")
        t0 = time.time()
        await asyncio.sleep(6)
        await c.interrupt()
        async for m in c.receive_response():  # drain the interrupted task (SDK docs: required before the next query)
            pass
        took = time.time() - t0
        await asyncio.sleep(25)  # past when the command would have finished
        late = os.path.exists(os.path.join(WORK, "late-file"))
        check("interrupt", took < 20 and not late, f"stopped after {took:.1f}s, command killed: {not late}")

        text3, _, _ = await turn(c, "Reply with just: STILL-HERE")
        check("usable after interrupt", "STILL-HERE" in text3, repr(text3[:40]))

    # resume in a fresh client = the daemon's idle-close then wake-up path
    async with ClaudeSDKClient(options=opts(resume=sid)) as c:
        text4, sid4, _ = await turn(c, "What exact string did the first echo command print? Reply with only the string.")
        check("resume after close", "spike-ok-7731" in text4, f"{text4[:40]!r}, same session id: {sid4 == sid}")

    msgs = get_session_messages(sid, directory=WORK)
    check("get_session_messages", len(msgs) > 4, f"{len(msgs)} messages readable without resuming")

    alt = os.environ.get("SPIKE_ALT_TOKEN")
    if alt:
        async with ClaudeSDKClient(options=opts(resume=sid, env={"CLAUDE_CODE_OAUTH_TOKEN": alt})) as c:
            text5, _, _ = await turn(c, "What exact string did the first echo command print? Reply with only the string.")
            check("resume under second account", "spike-ok-7731" in text5, repr(text5[:40]))
    else:
        print("SKIP  resume under second account  (set SPIKE_ALT_TOKEN)")

    print(f"\n{sum(results.values())}/{len(results)} passed · workdir {WORK}")
    sys.exit(0 if all(results.values()) else 1)


asyncio.run(main())
