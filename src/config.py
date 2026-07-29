"""Startup config — and the fast/deep auth split.

The two lanes talk to different surfaces, which can mean different billing:

  fast lane -> Anthropic API (Messages API), always per-token against a key
  deep lane -> Claude Agent SDK, i.e. Claude Code, which can authenticate
               against a Claude subscription login instead

The catch: an explicit `ANTHROPIC_API_KEY` in the environment outranks an OAuth
profile / subscription credential, and the Agent SDK's child process inherits
our environment. So merely having the key set silently routes the deep lane to
per-token API billing too.

`DEEP_LANE_AUTH=subscription` (the default) removes the key from the environment
after we've captured it, and hands it to the fast lane explicitly. The deep lane
then falls through to whatever credential Claude Code has (`claude login`, or an
`ant auth login` profile).

Set `DEEP_LANE_AUTH=api` to leave the environment alone and bill both lanes to
the API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

VALID_DEEP_AUTH = {"subscription", "api"}
DEFAULT_WINDOW_CHARS = 4000


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str | None
    """Key for the fast lane. None means "let the SDK resolve credentials
    itself" — e.g. an `ant auth login` profile."""

    deep_auth: str
    """"subscription" | "api" — see module docstring."""

    window_chars: int
    """How much transcript tail the lanes receive."""


def load() -> Config:
    load_dotenv()

    key = os.environ.get("ANTHROPIC_API_KEY") or None

    deep_auth = os.environ.get("DEEP_LANE_AUTH", "subscription").strip().lower()
    if deep_auth not in VALID_DEEP_AUTH:
        raise ValueError(
            f"DEEP_LANE_AUTH must be one of {sorted(VALID_DEEP_AUTH)}, got {deep_auth!r}"
        )

    if deep_auth == "subscription" and key:
        # Hide it from the Agent SDK's child process. The fast lane gets it
        # passed explicitly, so nothing else breaks.
        #
        # ANTHROPIC_AUTH_TOKEN is deliberately left alone: if you set that,
        # you set it on purpose, and it's a legitimate way to authenticate.
        os.environ.pop("ANTHROPIC_API_KEY", None)

    try:
        window_chars = int(
            os.environ.get("BIRD_BRAIN_WINDOW_CHARS", DEFAULT_WINDOW_CHARS)
        )
    except ValueError as exc:
        raise ValueError("BIRD_BRAIN_WINDOW_CHARS must be an integer") from exc

    return Config(
        anthropic_api_key=key,
        deep_auth=deep_auth,
        window_chars=window_chars,
    )
