"""Summarize tool — condense long text with an optional focus direction."""

import os

import anthropic


def run(args: dict) -> str:
    text = args.get("text", "").strip()
    direction = args.get("direction", "").strip()

    if not text:
        return "error: text is required"

    if direction:
        system = (
            f"Summarize the following text with this specific focus: {direction}\n"
            "Preserve detail relevant to that focus. Be concise and omit unrelated content."
        )
    else:
        system = "Summarize the following text concisely. Preserve key facts, decisions, and outcomes."

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    return response.content[0].text


SCHEMA = {
    "name": "summarize",
    "description": "Summarize a long text, optionally focused on a specific aspect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to summarize.",
            },
            "direction": {
                "type": "string",
                "description": (
                    "Optional focus for the summary, e.g. 'how button clicks are handled' or "
                    "'error handling patterns'. If omitted, produces a general summary. "
                    "With a direction the summary preserves relevant detail and is lossy elsewhere."
                ),
            },
        },
        "required": ["text"],
    },
}
