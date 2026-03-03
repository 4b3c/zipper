# Self-Development Improvement Ideas

Analysis of what makes a self-developing agent genuinely strong, and what Zipper is currently missing.

Three pillars: **orientation** (understanding its own state before acting), **precision** (making changes correctly), and **verification** (knowing if the change worked).

---

## Current Gaps — By Severity

### Tier 1: Actually blocking strong self-development

~~**1. Default model is Haiku**
`llm.py:33` — anything without the word "opus" or "sonnet" in the user message uses Haiku. Haiku is being used for code edits. This is a serious capability ceiling. Self-development tasks should default to Sonnet, with Haiku only for trivial retrieval work. The keyword-matching approach (`if "opus" in last.lower()`) is also fragile — "music made with opus software" would trigger Opus.~~ ✓ done — replaced with self-rating system: Zipper appends `{{c:X, d:X, a:X}}` to each response; total score selects the next model (>11=Opus, >6=Sonnet, else Haiku). First turn still uses Haiku.

~~**2. No grep / search-in-files tool**
The `file` tool can only `list` (one flat directory) or `read` (one file). There's no way to search across files for a symbol, function name, or pattern. Every cross-file navigation requires knowing to use bash with the right flags, and the agent has to rediscover this every session. This is the single biggest friction point in codebase navigation.~~ ✓ done

**3. Memory is not auto-loaded**
`memory.json` exists but the agent has to proactively call the `task` or `file` tool to look at it. It's never injected into the system prompt. So every session, Zipper starts with zero knowledge of its own past decisions, known bugs, or architectural notes — unless it happens to remember to check.

~~**4. The `file edit` tool is fragile**
`tools/file.py:49` — it does `original.replace(search, replace, 1)`. No validation that `search` is unique, no diff preview, no error if it appears 0 or 3+ times in a slightly different form. One bad whitespace difference in the search string silently fails or corrupts wrong. This is the most failure-prone tool in the stack.~~ ✓ done

---

### Tier 2: High impact on reliability

~~**5. No pre-loaded codebase orientation**
Every session starts blind. The system prompt tells Zipper where it lives but not what's in it. The agent spends the first several turns re-reading files it already knows. A maintained architecture snapshot (file tree + one-line role per file) in the system prompt would eliminate this cold-start problem.~~ ✓ done (solved via per-conversation tool onboarding)

**6. Compaction threshold is too low and too lossy**
`COMPACTION_THRESHOLD = 20` messages. A real self-development session — read file, think, edit, run tests, fix error, retry — uses 20+ turns before the core work is even done. Then Sonnet summarizes and loses the crucial details: "I tried X but it failed because the import path was wrong." Debugging context doesn't survive compaction well.

**7. No behavioral test infrastructure**
The restart→healthcheck loop verifies the service starts. It doesn't verify that the behavior Zipper just changed actually works. There's no way to say "after restart, run these checks" without writing ad-hoc bash. Without structured post-change verification, Zipper can ship a change that starts fine but is functionally broken.

**8. Recent logs not available at session start**
If Zipper crashed or had errors since the last session, the new session has no idea. The trace log exists but isn't surfaced. A "here's what happened since you were last active" injection would mean Zipper can act on problems without being told about them.

---

### Tier 3: Would make it substantially more powerful

**9. Git awareness**
Zipper can push to GitHub via bash, but there's nothing in the tooling or system prompt that tells it to check `git status` and `git diff` before committing. A `git` tool (or at minimum, git status injected into context at session start) would catch accidental partial changes.

**10. No structured failure→repair pipeline**
Ad-hoc self-repair is fine for simple bugs, but there's no structure for: detect anomaly → diagnose root cause → propose fix → implement → verify → log outcome. Right now self-repair only happens when a human asks.

**11. 30s bash timeout is too short for builds**
If Zipper ever needs to install dependencies, run a full test suite, or do a build, it'll silently time out. The tool should support longer timeouts for known-slow operations.

---

## Priority Build Order

| # | What | Why it unblocks |
|---|------|----------------|
| ~~1~~ | ~~**Upgrade default model to Sonnet**~~ | ✓ done — self-rating routes to Opus/Sonnet/Haiku dynamically |
| ~~2~~ | ~~**`grep` / search-in-files tool**~~ | ✓ done |
| 3 | **Auto-inject memory + codebase map into system prompt** | Eliminates cold-start rediscovery every session |
| ~~4~~ | ~~**Strengthen `file edit`** — uniqueness check, return diff~~ | ✓ done |
| ~~5~~ | ~~**Per-conversation tool onboarding**~~ | ✓ done — fires on first use of each tool per session |
| 6 | **Raise compaction threshold + preserve code-detail context** | Debugging context survives long sessions |
| 7 | **Session start: inject recent logs + git status** | Zipper wakes up knowing what's wrong |
| 8 | **Structured repair pipeline** — anomaly → diagnosis → fix loop | Makes self-repair proactive, not reactive |

Items 1–4 would move the needle the most. Items 1 and 2 together are probably 80% of the gap between "can build itself if guided" and "can genuinely build itself autonomously."
