"""Bound model context at learner-turn boundaries and repair old tool history."""

import json


def model_history(messages, limit=200):
    """Return a bounded API view; keep the durable history intact.

    The latest turn may exceed the soft limit: tool-call groups are indivisible.
    Repair old interrupted calls and omit old orphan results only in API context.
    """
    turns = []
    for message in messages:
        if message.get("role") == "user" or not turns:
            turns.append([])
        turns[-1].append(message)
    selected = []
    size = 0
    for turn in reversed(turns):
        if selected and size + len(turn) > limit:
            break
        selected.append(turn)
        size += len(turn)
    result = []
    pending = {}

    def complete_pending():
        for call_id in pending:
            result.append({"role": "tool", "tool_call_id": call_id,
                           "content": json.dumps({"ok": False, "cancelled": True,
                                                  "error": "历史工具调用已中断，未取得结果。"}, ensure_ascii=False)})
        pending.clear()

    for turn in reversed(selected):
        for message in turn:
            if message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if call_id in pending:
                    result.append(message)
                    del pending[call_id]
                continue
            complete_pending()
            result.append(message)
            if message.get("role") == "assistant":
                pending.update({call["id"]: True for call in message.get("tool_calls", []) if call.get("id")})
    complete_pending()
    return result
