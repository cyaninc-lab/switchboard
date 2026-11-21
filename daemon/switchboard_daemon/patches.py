"""Line numbers for Edit/Write diffs: Claude Code records each edit's hunks (structuredPatch) in the tool result it
writes to the transcript. get_session_messages() drops that part, so history reads it from the file itself."""
import glob, json, os


def _transcript(session_id):
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    found = glob.glob(os.path.join(base, "projects", "*", f"{glob.escape(session_id)}.jsonl"))
    return found[0] if found else None


def from_result(tool_use_id, result):
    """{tool_use_id: hunks} from one tool result (live: the SDK's tool_use_result), or {}."""
    hunks = result.get("structuredPatch") if isinstance(result, dict) else None
    return {tool_use_id: hunks} if tool_use_id and isinstance(hunks, list) and hunks else {}


def for_session(session_id, wanted=None):
    """{tool_use_id: [{oldStart, oldLines, newStart, newLines, lines}]} for the session's edits (only `wanted` ids if given)."""
    path, out = _transcript(session_id), {}
    if not path:
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"structuredPatch"' not in line:  # cheap skip: most lines aren't edit results
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            content = (d.get("message") or {}).get("content")
            tid = content[0].get("tool_use_id") if isinstance(content, list) and content and isinstance(content[0], dict) else None
            if wanted is None or tid in wanted:
                out |= from_result(tid, d.get("toolUseResult"))
    return out


def with_mid_turn(session_id, msgs):
    """Put back the messages the user sent while Claude was working. Claude Code reads those between steps and stores
    them as `queued_command` attachments, which get_session_messages() drops, so history read without this lost them.
    Each goes in after the message it followed in the conversation (attachments chain to it through parentUuid)."""
    path = _transcript(session_id)
    if not path:
        return msgs
    parent, queued = {}, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"attachment"' not in line:  # cheap skip: only attachments sit between the messages the SDK returns
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "attachment" or not d.get("uuid"):
                continue
            parent[d["uuid"]] = d.get("parentUuid")
            a = d.get("attachment") or {}
            if a.get("type") == "queued_command" and a.get("prompt"):
                queued.append((d["uuid"], a["prompt"]))
    if not queued:
        return msgs
    known = {m.get("uuid") for m in msgs if isinstance(m, dict)}
    after = {}  # message uuid -> the user's messages that came right after it
    for uid, prompt in queued:
        anchor, seen = parent.get(uid), set()
        while anchor and anchor not in known and anchor in parent and anchor not in seen:
            seen.add(anchor); anchor = parent[anchor]
        if anchor in known:
            after.setdefault(anchor, []).append({"type": "user", "uuid": uid, "session_id": session_id, "parent_tool_use_id": None,
                                                 "message": {"role": "user", "content": prompt}})
    out = []
    for m in msgs:
        out.append(m)
        out.extend(after.get(m.get("uuid") if isinstance(m, dict) else None, []))
    return out
