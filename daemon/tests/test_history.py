"""History details the SDK's reader drops, read from the transcript file itself."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switchboard_daemon import patches


def test_mid_turn_messages_come_back_where_they_were_read(tmp_path, monkeypatch):
    # as Claude Code writes it: the message sent mid-turn is an attachment chained after the tool result it followed
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = tmp_path / "projects" / "-p"; d.mkdir(parents=True)
    lines = [{"type": "user", "uuid": "u1", "parentUuid": None, "message": {"role": "user", "content": "run three"}},
             {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}},
             {"type": "user", "uuid": "r1", "parentUuid": "a1", "message": {"role": "user", "content": []}},
             {"type": "attachment", "uuid": "x1", "parentUuid": "r1", "attachment": {"type": "prompt_snapshot"}},
             {"type": "attachment", "uuid": "q1", "parentUuid": "x1", "attachment": {"type": "queued_command", "prompt": "also BANANA"}},
             {"type": "attachment", "uuid": "x2", "parentUuid": "q1", "attachment": {"type": "total_tokens_reminder"}},
             {"type": "assistant", "uuid": "a2", "parentUuid": "x2", "message": {"role": "assistant", "content": []}}]
    (d / "s1.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
    msgs = [{"type": l["type"], "uuid": l["uuid"], "message": l["message"]} for l in lines if l["type"] != "attachment"]
    out = patches.with_mid_turn("s1", msgs)
    assert [m["uuid"] for m in out] == ["u1", "a1", "r1", "q1", "a2"]
    assert out[3]["message"] == {"role": "user", "content": "also BANANA"}
    assert patches.with_mid_turn("missing", msgs) == msgs
    # Claude Code sometimes writes the same lines again further down: still put back once
    (d / "s1.jsonl").write_text("\n".join(json.dumps(l) for l in lines + lines[3:]))
    assert [m["uuid"] for m in patches.with_mid_turn("s1", msgs)] == ["u1", "a1", "r1", "q1", "a2"]


def test_other_sessions_sendmessage_shows(tmp_path, monkeypatch):
    # Claude Code's SendMessage between sessions arrives as a user line flagged isMeta, which the SDK's reader skips
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = tmp_path / "projects" / "-p"; d.mkdir(parents=True)
    peer = 'Another Claude session sent a message:\n<cross-session-message from="uds:/x.sock" from-name="a-42">\nping\n</cross-session-message>\n\nThis came from'
    lines = [{"type": "user", "uuid": "u1", "parentUuid": None, "message": {"role": "user", "content": "hi"}},
             {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}},
             {"type": "user", "uuid": "p1", "parentUuid": "a1", "isMeta": True, "message": {"role": "user", "content": peer}},
             {"type": "assistant", "uuid": "a2", "parentUuid": "p1", "message": {"role": "assistant", "content": []}}]
    (d / "s1.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
    msgs = [{"type": l["type"], "uuid": l["uuid"], "message": l["message"]} for l in lines if not l.get("isMeta")]
    out = patches.with_mid_turn("s1", msgs)
    assert [m["uuid"] for m in out] == ["u1", "a1", "p1", "a2"] and out[2]["message"]["content"] == peer


def test_claude_codes_own_notes_are_not_shown_as_the_users():
    from claude_agent_sdk._internal import message_parser
    from switchboard_daemon.runner import as_history
    note = message_parser.parse_message({"type": "user", "isSynthetic": True, "uuid": "n1", "session_id": "s", "parent_tool_use_id": None,
        "message": {"role": "user", "content": [{"type": "text", "text": "[Image: original 1080x2400, displayed at 900x2000.]"}]}})
    assert as_history(note) is None
    typed = message_parser.parse_message({"type": "user", "uuid": "u1", "session_id": "s", "parent_tool_use_id": None,
        "message": {"role": "user", "content": "hello"}})
    assert as_history(typed)["message"]["content"] == "hello"
