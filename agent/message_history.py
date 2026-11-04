"""Bound model context at learner-turn boundaries and repair old tool history."""

import json
from copy import deepcopy


def _encoded_size(messages):
    return len(json.dumps(messages, ensure_ascii=False))


def _compact_group(group, content_limit):
    """Shorten only the API copy, retaining complete call/result relationships."""
    compact = deepcopy(group)
    for message in compact:
        content = message.get("content")
        if isinstance(content, str) and len(content) > content_limit:
            message["content"] = content[:content_limit] + "\n[工具或回复正文已按上下文预算截断]"
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            arguments = function.get("arguments")
            if isinstance(arguments, str) and len(arguments) > content_limit:
                function["arguments"] = json.dumps({"context_truncated": True})
    return compact


def _bound_chars(messages, max_chars):
    if _encoded_size(messages) <= max_chars:
        return messages
    starts = [i for i, message in enumerate(messages) if message.get("role") == "user"]
    # Discard complete older learner turns before shortening the current turn.
    while len(starts) > 1 and _encoded_size(messages) > max_chars:
        messages = messages[starts[1]:]
        starts = [i for i, message in enumerate(messages) if message.get("role") == "user"]
    if _encoded_size(messages) <= max_chars:
        return messages
    user = messages[0] if messages and messages[0].get("role") == "user" else None
    remainder = messages[1:] if user else messages
    groups = []
    for message in remainder:
        if message.get("role") == "tool" and groups and groups[-1][0].get("tool_calls"):
            groups[-1].append(message)
        else:
            groups.append([message])
    selected = []
    prefix = [user] if user else []
    # A submitted learner request is kept intact even for a deliberately tiny
    # custom budget. Normal requests are capped at 20,000 chars by AgentSession.
    available = max_chars - _encoded_size(prefix)
    for group in reversed(groups):
        candidate = group
        if _encoded_size(candidate) > available:
            candidate = _compact_group(group, max(32, available // max(2, len(group) * 2)))
        if _encoded_size(candidate) > available:
            break
        selected.insert(0, candidate)
        available -= _encoded_size(candidate)
    return prefix + [message for group in selected for message in group]


def model_history(messages, limit=200, *, max_chars=120_000):
    """Return a bounded API view; keep the durable history intact.

    The latest turn may exceed the soft limit: tool-call groups are indivisible.
    Repair old interrupted calls and omit old orphan results only in API context.
    """
    if type(max_chars) is not int or max_chars < 256:
        raise ValueError("max_chars must be an integer of at least 256.")
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
    return _bound_chars(result, max_chars)
