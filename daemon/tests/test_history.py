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
