"""Deep lane — Claude Code in the conversation.

A persistent Agent SDK session (streaming-input mode) opened once and reused.
Unlike the fast lane it has tools, files, and web access, and its turns run
seconds to minutes. It never blocks the fast lane.

Note on the Python SDK: `receive_response()` returns at the first result
message, so a persistent session means one query()/receive_response() pair per
trigger — which is exactly the shape we want here.
"""

from __future__ import annotations

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
        self._ctx = None

    async def start(self) -> bool:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        except ImportError:
            print("[deep] claude-agent-sdk not installed — deep lane disabled")
            return False

        options = ClaudeAgentOptions(
            max_turns=30,
            allowed_tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch", "Bash"],
        )
        self._ctx = ClaudeSDKClient(options)
        self._client = await self._ctx.__aenter__()
        print("[deep] session ready")
        return True

    async def stop(self) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = self._client = None

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
