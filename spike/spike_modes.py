#!/usr/bin/env python3
"""Spike part 2: auto permission mode, switching mode mid-session, and partial-message streaming.
Run like spike.py (CLAUDE* env stripped)."""
import asyncio, os, tempfile, time
from claude_agent_sdk import (ClaudeSDKClient, ClaudeAgentOptions, ResultMessage, PermissionResultAllow)
from claude_agent_sdk.types import StreamEvent

MODEL = os.environ.get("SPIKE_MODEL", "claude-haiku-5-5")
WORK = tempfile.mkdtemp(prefix="sb-spike2-")
asked = []


async def can_use_tool(tool, inp, ctx):
    asked.append(tool)
    return PermissionResultAllow()


async def run(client, prompt):
    await client.query(prompt)
    deltas, err = 0, None
    async for m in client.receive_response():
        if isinstance(m, StreamEvent):
            deltas += m.event.get("type") == "content_block_delta"
        elif isinstance(m, ResultMessage):
            err = m.is_error
    return deltas, err


async def main():
    o = ClaudeAgentOptions(model=MODEL, cwd=WORK, permission_mode="default", setting_sources=[],
                           can_use_tool=can_use_tool, include_partial_messages=True)
    async with ClaudeSDKClient(options=o) as c:
        d, _ = await run(c, "Write three short sentences about switchboards. No tools.")
        print(f"{'PASS' if d > 3 else 'FAIL'}  partial streaming  {d} text deltas")

        await c.set_permission_mode("acceptEdits")
        n = len(asked)
        await run(c, "Use the Write tool to create notes.txt containing hi. Reply DONE.")
        ok = os.path.exists(os.path.join(WORK, "notes.txt")) and len(asked) == n
        print(f"{'PASS' if ok else 'FAIL'}  switch to acceptEdits mid-session  file written without asking: {ok}")

        try:
            await c.set_permission_mode("auto")
            n = len(asked)
            _, err = await run(c, "Use the Bash tool to run exactly: mkdir auto-dir . Reply DONE.")
            made = os.path.isdir(os.path.join(WORK, "auto-dir"))
            print(f"{'PASS' if made else 'FAIL'}  auto mode  dir made: {made}, can_use_tool asked: {len(asked) > n}, error: {err}")
        except Exception as e:
            print(f"FAIL  auto mode  {type(e).__name__}: {e}")


asyncio.run(main())
