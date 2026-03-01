## Discord Tool Guide

### Modes

- **send** — post a message to a channel or thread. Returns a `message_id` for use with edit or react.
- **history** — fetch recent messages (default 20, max 100). Each message includes its id and timestamp. Useful for finding message IDs before reacting or for understanding conversation context.
- **edit** — update a previously sent message by `message_id`. Good for live status updates.
- **react** — add an emoji reaction to any message by `message_id`. Standard Unicode emoji only (no custom server emoji).

### Parameters

- **mode** — required. One of: `send`, `history`, `edit`, `react`.
- **message** — required for send mode. The message text to post.
- **content** — required for edit mode. The new message text.
- **emoji** — required for react mode. Standard Unicode emoji (e.g., `✅`, `👍`, `🎉`).
- **message_id** — required for edit and react modes. The ID of the message to modify.
- **thread_id** — optional. The Discord thread ID. Omit to target the main channel.
- **limit** — optional for history mode. Number of messages to fetch (1–100, default 20).

### Workflow

**To react to a message:**
1. Determine if the message is in a thread or the main channel.
2. If in a thread: call `discord(history, thread_id=<thread_id>, limit=N)` to list messages in that thread and find the `message_id`.
3. If in the main channel: call `discord(history, limit=N)` to list messages and find the `message_id`.
4. Call `discord(react, message_id=<message_id>, emoji=✅)` (main channel) or `discord(react, message_id=<message_id>, thread_id=<thread_id>, emoji=✅)` (thread).

**To send or edit:**
- Use `send` to post a new message (returns the new message_id).
- Use `edit` to update an existing message by its `message_id`.
- Always pass `thread_id` when sending to a thread.

### Important Notes

- **Thread messages:** Always pass `thread_id` when sending, editing, reacting to, or reading messages in a thread. If you don't know the thread_id, use `history` to show messages in the channel and the thread_id of the thread they start, then use history again with the thread_id to find message IDs within the thread.
- **Reactions:** When reacting to a message in a thread, you must pass both `message_id` and `thread_id` so the bot knows which thread to look in.
- **Unicode emoji only:** Custom server emoji (e.g., `<:emoji_name:id>`) are not supported. Use standard emoji like `✅`, `👍`, `❌`, `🎉`, etc.
