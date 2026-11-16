#!/usr/bin/env python3
"""Interrupt, done properly: a slow command Claude Code doesn't block (a python sleep, not `sleep N &&`).
Proves interrupt() stops a command that is really running. Run with CLAUDE* env stripped."""
import asyncio, os, tempfile, time
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, UserMessage, ToolResultBlock, TextBlock

WORK = tempfile.mkdtemp(prefix="sb-int-")
CMD = "python3 -c \"import time,pathlib; pathlib.Path('started').touch(); time.sleep(30); pathlib.Path('late-file').touch()\""

async def main():
    o = ClaudeAgentOptions(model="claude-haiku-5-5", cwd=WORK, permission_mode="bypassPermissions", setting_sources=[])
    async with ClaudeSDKClient(options=o) as c:
        await c.query(f"Use the Bash tool to run exactly this command in the foreground and wait for it: {CMD}")
        t0 = time.time()
        while not os.path.exists(os.path.join(WORK, "started")) and time.time() - t0 < 60:
            await asyncio.sleep(0.5)
        running = os.path.exists(os.path.join(WORK, "started"))
        await c.interrupt()
        async for m in c.receive_response():
            pass
        took = time.time() - t0
        await asyncio.sleep(35)
        late = os.path.exists(os.path.join(WORK, "late-file"))
        ok = running and not late
        print(f"{'PASS' if ok else 'FAIL'}  interrupt  command was running: {running}, killed: {not late}, interrupt returned after {took:.1f}s")
        await c.query("Reply with just: STILL-HERE")
        txt = ""
        async for m in c.receive_response():
            if isinstance(m, AssistantMessage):
                txt += "".join(b.text for b in m.content if isinstance(b, TextBlock))
        print(f"{'PASS' if 'STILL-HERE' in txt else 'FAIL'}  usable after interrupt  {txt[:40]!r}")

asyncio.run(main())
