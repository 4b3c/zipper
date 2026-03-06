"""Message sanitization and serialization utilities for the LLM loop."""


def _has_tool_use(content) -> bool:
    if isinstance(content, list):
        return any(
            (b.get("type") == "tool_use" if isinstance(b, dict) else getattr(b, "type", None) == "tool_use")
            for b in content
        )
    return False


def _is_tool_result_message(msg: dict) -> bool:
    content = msg.get("content", [])
    return (
        msg.get("role") == "user"
        and isinstance(content, list)
        and bool(content)
        and (content[0].get("type") == "tool_result" if isinstance(content[0], dict) else False)
    )


def _sanitize_messages(messages: list) -> list:
    """Remove orphaned tool_use/tool_result pairs that would cause API 400 errors."""
    if not messages:
        return messages

    msgs = list(messages)

    # drop trailing assistant message ending in tool_use (restart killed process before result)
    if msgs and msgs[-1].get("role") == "assistant" and _has_tool_use(msgs[-1].get("content", [])):
        msgs = msgs[:-1]

    # drop trailing user tool_result with no preceding assistant tool_use
    if msgs and _is_tool_result_message(msgs[-1]):
        if len(msgs) < 2 or not _has_tool_use(msgs[-2].get("content", [])):
            msgs = msgs[:-1]

    # ensure conversation starts with a proper user message — strip leading tool_results
    # and any assistant messages that become leading after those are removed
    while msgs and (msgs[0].get("role") == "assistant" or _is_tool_result_message(msgs[0])):
        msgs = msgs[1:]

    # fix consecutive user messages: insert empty assistant turn in between
    # (represents an interrupted response — the assistant didn't get to say anything)
    i = 0
    while i < len(msgs) - 1:
        if msgs[i].get("role") == "user" and not _is_tool_result_message(msgs[i]) \
                and msgs[i + 1].get("role") == "user":
            msgs.insert(i + 1, {"role": "assistant", "content": [{"type": "text", "text": "[interrupted]"}]})
        i += 1

    # fix consecutive assistant messages: merge into one
    i = 0
    while i < len(msgs) - 1:
        if msgs[i].get("role") == "assistant" and msgs[i + 1].get("role") == "assistant":
            a = msgs[i].get("content", [])
            b = msgs[i + 1].get("content", [])
            if isinstance(a, list) and isinstance(b, list):
                msgs[i] = {"role": "assistant", "content": a + b}
            msgs.pop(i + 1)
        else:
            i += 1

    return msgs


def serialize_content(content) -> list:
    """Whitelist only API-accepted fields to avoid 400s from internal SDK fields."""
    result = []
    for block in content:
        if isinstance(block, dict):
            result.append(block)
            continue
        t = getattr(block, "type", None)
        if t == "text":
            result.append({"type": "text", "text": block.text})
        elif t == "tool_use":
            result.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        elif t == "code_execution_result":
            # Programmatic tool calling: code execution output block
            result.append({"type": "code_execution_result", "content": getattr(block, "content", [])})
        elif hasattr(block, "model_dump"):
            result.append(block.model_dump())
        else:
            result.append(block)
    return result
