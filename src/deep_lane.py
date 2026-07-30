"""Deep lane — Claude Code in the conversation.

A persistent Agent SDK session (streaming-input mode) opened once and reused.
Unlike the fast lane it has tools, files, and web access, and its turns run
seconds to minutes. It never blocks the fast lane.

Note on the Python SDK: `receive_response()` returns at the first result
message, so a persistent session means one query()/receive_response() pair per
trigger — which is exactly the shape we want here.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Auto-approved: read-only, and safe to run against transcript text we did not
# author. Bash is deliberately absent here — it is gated by the PreToolUse hook.
READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch", "WebFetch"]

STANDING_INSTRUCTION = """\
You are the research lane of a live conversation copilot. Below is the recent \
transcript of a spoken conversation between ME and THEM.

Investigate the most recent claim, question, or task worth checking. Use your \
tools: read files in the working directory, grep the codebase, search the web. \
Then report back in a few sentences of plain speakable prose — lead with the \
finding, then the evidence. If you verified something against a source or a \
file, say which.
"""


class DeepLane:
    """Wraps a long-lived ClaudeSDKClient. Import is lazy so the fast lane still
    works if the Agent SDK / Claude Code CLI isn't installed."""

    def __init__(self) -> None:
        self._client = None

    async def start(self, auth: str = "subscription") -> bool:
        """Open the session. `auth` is informational — the actual routing was
        decided in config.load(), which either left ANTHROPIC_API_KEY in the
        environment (api mode) or removed it (subscription mode)."""
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
        except ImportError:
            print("[deep] claude-agent-sdk not installed — deep lane disabled")
            return False

        options = ClaudeAgentOptions(
            max_turns=30,
            allowed_tools=READ_ONLY_TOOLS,
            # The Bash gate is a PreToolUse hook, not can_use_tool. A callback is
            # only consulted when the CLI decides to ask, and in SDK/headless
            # mode it does not ask — verified: Bash ran unprompted with the
            # callback wired, both for a command matching an allow rule and one
            # matching none. A PreToolUse hook runs on every matching call.
            hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[self._confirm_bash])]},
            # Load no settings files: otherwise ~/.claude/settings.json's
            # permissions.allow rules apply here too, and a `Bash(echo:*)` grant
            # made for interactive use is not a grant for commands shaped by the
            # far side of a conversation. Cost: the session also skips project
            # CLAUDE.md, which the standing instruction covers.
            setting_sources=[],
        )
        client = ClaudeSDKClient(options=options)
        try:
            await client.connect()
        except Exception as exc:  # noqa: BLE001 — fast lane must survive this
            self._client = None
            print(f"[deep] session failed to start ({exc!r}) — deep lane disabled")
            if auth == "subscription":
                print(
                    "[deep] subscription mode: run `claude login` (or set "
                    "DEEP_LANE_AUTH=api to bill this lane to your API key)"
                )
            return False

        self._client = client
        if auth == "subscription":
            print("[deep] session ready — Claude Code credential (subscription)")
        else:
            print("[deep] session ready — ANTHROPIC_API_KEY (per-token billing)")
        return True

    async def _confirm_bash(
        self, payload: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """PreToolUse hook: gate every Bash call on an explicit y/N.

        The transcript this lane reasons over contains the *other* party's
        speech, so a shell command can be shaped by input we do not control.
        Deny is the default for every answer that isn't "y".

        input() runs in a thread: blocking the event loop here would stall
        capture and the fast lane while we wait on the keyboard.
        """
        command = payload.get("tool_input", {}).get("command", "")
        print(f"\n[deep] !! Bash wants to run:\n    {command}")
        answer = (await asyncio.to_thread(input, "[deep] allow? (y/N) ")).strip().lower()
        decision = "allow" if answer == "y" else "deny"
        if decision == "deny":
            print("[deep] denied")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": f"bird_brain operator answered {answer!r}",
            }
        }

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def ask(self, window: str) -> None:
        """Hand the transcript window to the session; stream text as it lands."""
        if self._client is None:
            print("[deep] unavailable")
            return
        if not window.strip():
            print("[deep] transcript empty — nothing to research")
            return

        from claude_agent_sdk import AssistantMessage, TextBlock

        prompt = f"{STANDING_INSTRUCTION}\n\n<transcript>\n{window}\n</transcript>"
        await self._client.query(prompt)
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
        print()
