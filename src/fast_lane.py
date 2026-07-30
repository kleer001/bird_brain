"""Fast lane — the reflex answer.

One Agent SDK turn per keypress, on the same Claude Code credential the deep lane
uses. Nothing here bills per token.

The lane is latency-critical, so the session is opened once at startup and reused:
connecting costs about a second, and paying that on every press would double the
time to first word.

Three options do the shaping:
  - `system_prompt` as a plain string *replaces* Claude Code's preset rather than
    appending to it (the preset is opt-in via {"type": "preset"}). The lane gets
    its own instructions and background with no coding-agent persona underneath.
  - `allowed_tools=[]` — no tools at all. A conversation copilot has nothing to
    run, and tool definitions are prompt weight on a latency budget.
  - `setting_sources=[]` — ignore ~/.claude settings, so a rule written for
    interactive use can't reshape answers here.

Known tradeoff: the session is stateful, so each press sees the previous presses.
That is the cost of not paying the reconnect every time.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-4-5"

INSTRUCTIONS = """\
You are a live conversation copilot. You are shown the recent transcript of a \
spoken conversation between ME and THEM. Answer the most recent question or \
prompt directed at ME, as if I am about to say your words out loud.

Rules:
- Lead with the answer. No preamble, no "great question", no restating the ask.
- Speakable prose. Short sentences. No markdown, no bullets, no headers.
- Two to four sentences unless the question genuinely needs more.
- Ground answers in the background material below when it is relevant. If the \
background does not cover it, answer from general knowledge and do not invent \
specifics about me.
- If the transcript has no question aimed at ME, give the single most useful \
thing I could say next.
- Do not include internal or system XML tags in your response.
"""


def _load_background() -> str:
    path = os.environ.get("BIRD_BRAIN_RESUME", "")
    if path and Path(path).is_file():
        return Path(path).read_text()
    return "(no background file configured)"


class FastLane:
    """Wraps a long-lived ClaudeSDKClient. Import is lazy for the same reason the
    deep lane's is — the app should start and report the problem rather than fail
    at import when the Agent SDK isn't installed."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model
        self._client = None
        background = _load_background()
        self._system = f"{INSTRUCTIONS}\n<background>\n{background}\n</background>"

    async def start(self) -> bool:
        """Open the session. Runs at startup, concurrently with the deep lane's."""
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        except ImportError:
            print("[fast] claude-agent-sdk not installed — fast lane disabled")
            return False

        options = ClaudeAgentOptions(
            model=self._model,
            system_prompt=self._system,
            allowed_tools=[],
            setting_sources=[],
            max_turns=1,
            # Without this the SDK hands back whole AssistantMessages, so the
            # answer appears all at once after the full generation. On a lane
            # you read aloud, time-to-first-word is the number that matters, not
            # time-to-complete — partial events get words on screen as they land.
            include_partial_messages=True,
        )
        client = ClaudeSDKClient(options=options)
        try:
            await client.connect()
        except Exception as exc:  # noqa: BLE001 — the deep lane must survive this
            print(f"[fast] session failed to start ({exc!r}) — fast lane disabled")
            return False

        self._client = client
        print(f"[fast] session ready — {self._model} on the Claude Code credential")
        return True

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def answer(self, window: str) -> str:
        """Stream an answer for the given transcript window. Returns full text."""
        if self._client is None:
            print("[fast] unavailable")
            return ""
        if not window.strip():
            print("[fast] transcript empty — nothing to answer")
            return ""

        from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock

        started = time.monotonic()
        first_token = None
        parts: list[str] = []

        await self._client.query(f"<transcript>\n{window}\n</transcript>")
        async for message in self._client.receive_response():
            # Partial events carry the raw API stream shape: text arrives as
            # content_block_delta chunks. The complete AssistantMessage still
            # follows, so it is ignored here to avoid printing the answer twice.
            if isinstance(message, StreamEvent):
                delta = message.event.get("delta") or {}
                chunk = delta.get("text")
                if chunk:
                    if first_token is None:
                        first_token = time.monotonic() - started
                    parts.append(chunk)
                    print(chunk, end="", flush=True)
            elif isinstance(message, AssistantMessage) and not parts:
                # No partial events arrived (older CLI); fall back to the whole
                # message so the lane still answers.
                for block in message.content:
                    if isinstance(block, TextBlock):
                        if first_token is None:
                            first_token = time.monotonic() - started
                        parts.append(block.text)
                        print(block.text, end="", flush=True)

        print()
        # Time to first word is the number that decides whether this lane is
        # usable mid-conversation, so it gets printed on every press rather than
        # left to be measured separately.
        print(
            f"[fast] first word {first_token:.1f}s | total "
            f"{time.monotonic() - started:.1f}s"
            if first_token is not None
            else "[fast] no text returned"
        )
        return "".join(parts)
