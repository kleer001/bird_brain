"""Fast lane — the reflex answer.

One streaming Claude call per keypress. Latency-critical, so:
  - thinking off, and no tools at all — which also sidesteps the disabled-thinking
    failure mode where a tool call gets written as plain text and silently never runs
  - stable prefix (instructions + your background) carries the cache_control
    breakpoints; the transcript tail goes in the user turn, uncached
"""

from __future__ import annotations

import os
from pathlib import Path

from anthropic import AsyncAnthropic

MAX_TOKENS = 1024  # a spoken-length answer; raise if you want more

# Per-model request shape. The models disagree on more than their IDs, and every
# difference here is a 400 or a silent misconfiguration rather than a preference:
#
#   thinking  — Opus 5 thinks by default, so turning it off has to be explicit,
#               and "disabled" is only accepted at effort `high` or lower.
#               Haiku 4.5 predates that surface: it has no adaptive mode, and
#               omitting the field entirely is how you get no thinking.
#   effort    — Opus 5 takes low..max. Haiku 4.5 rejects the parameter outright.
#   cache_min — the shortest prefix that will cache at all. Below it nothing
#               caches, silently, on every press. Opus 5 has the lowest minimum
#               of any model; Haiku's is 8x higher, so a background file sized
#               for one will quietly stop caching on the other.
DEFAULT_MODEL = "claude-opus-5"
MODEL_PARAMS = {
    "claude-opus-5": {
        "request": {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}},
        "cache_minimum": 512,
    },
    "claude-haiku-4-5": {
        "request": {},
        "cache_minimum": 4096,
    },
}

NO_CREDENTIAL = "[fast] no credential — fast lane disabled (see INSTALL.md §3)"

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
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        self._model = model
        self._request = MODEL_PARAMS[model]["request"]
        self._cache_minimum = MODEL_PARAMS[model]["cache_minimum"]
        # The key is passed explicitly because config.load() may have removed it
        # from the environment to keep the deep lane on subscription auth (see
        # config.py). api_key=None falls through to the SDK's own credential
        # resolution — env vars, then an `ant auth login` profile.
        self._client = AsyncAnthropic(api_key=api_key)
        # A Claude Code subscription login does not reach this lane: that
        # credential is not one the SDK reads. Detect the no-credential case at
        # startup and say so once, rather than raising on every press. Mirrors
        # how the deep lane disables itself when its session can't open.
        #
        # All three of the SDK's resolution results count. `credentials` is the
        # one an `ant auth login` profile lands in — it resolves via
        # default_credentials() only when api_key and auth_token are both None,
        # so checking those two alone would disable the lane for exactly the
        # users who authenticated the cheapest way.
        self.available = bool(
            self._client.api_key
            or self._client.auth_token
            or self._client.credentials
        )
        background = _load_background()
        # Two stable breakpoints. Byte-identical across every press, so the
        # whole prefix is a cache read after the first call.
        self._system = [
            {
                "type": "text",
                "text": INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": f"<background>\n{background}\n</background>",
                "cache_control": {"type": "ephemeral"},
            },
        ]

    async def prewarm(self) -> None:
        """Write the cache before the first real press, so press #1 is a cache
        read instead of a cold write.

        max_tokens=0 runs prefill and returns immediately with empty content.
        Note it is rejected alongside stream=True or thinking type "enabled" —
        disabled is fine. No output_config here: nothing to constrain, and it
        keeps the request in the plainly-accepted shape.
        """
        if not self.available:
            print(NO_CREDENTIAL)
            return
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=0,
                system=self._system,
                messages=[{"role": "user", "content": "warmup"}],
                **self._request,
            )
        except Exception as exc:  # noqa: BLE001 — never let a warm-up be fatal
            print(f"[fast] prewarm skipped: {exc!r}")
            return

        # A cache write of zero means the stable prefix is under this model's
        # minimum cacheable length and nothing will ever cache — silently, on
        # every press. The instructions alone are ~200 tokens, so this is what
        # an unset or too-small BIRD_BRAIN_RESUME looks like.
        if resp.usage.cache_creation_input_tokens:
            print(
                f"[fast] cache warm: {resp.usage.cache_creation_input_tokens} tokens "
                f"({self._model})"
            )
        else:
            print(
                f"[fast] NOT CACHING: stable prefix is under {self._model}'s "
                f"{self._cache_minimum}-token minimum. Point BIRD_BRAIN_RESUME at a "
                "larger background file."
            )

    async def answer(self, window: str) -> str:
        """Stream an answer for the given transcript window. Returns full text."""
        if not self.available:
            print(NO_CREDENTIAL)
            return ""
        if not window.strip():
            print("[fast] transcript empty — nothing to answer")
            return ""

        parts: list[str] = []
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=self._system,
            messages=[
                {
                    "role": "user",
                    "content": f"<transcript>\n{window}\n</transcript>",
                }
            ],
            **self._request,
        ) as stream:
            async for text in stream.text_stream:
                parts.append(text)
                print(text, end="", flush=True)
            final = await stream.get_final_message()

        print()
        # Opus 5's safety classifiers can decline a request: HTTP 200, empty or
        # partial content, stop_reason "refusal". Without this the lane just
        # prints nothing and looks broken.
        if final.stop_reason == "refusal":
            category = getattr(final.stop_details, "category", None)
            print(f"[fast] declined by safety classifier (category={category})")

        u = final.usage
        print(
            f"[fast] in={u.input_tokens} out={u.output_tokens} "
            f"cache_read={u.cache_read_input_tokens} "
            f"cache_write={u.cache_creation_input_tokens}"
        )
        return "".join(parts)
