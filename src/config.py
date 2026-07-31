"""Startup config — and the auth split.

Both lanes drive the Claude Code CLI through the Agent SDK, so both authenticate
the same way: whatever `claude login` established. No API key is required and
nothing bills per token.

`ANTHROPIC_API_KEY` is therefore opt-in, and opting in bills *both* lanes: an
explicit key outranks the Claude Code credential, and the SDK's child processes
inherit our environment. Merely having one set silently routes everything to
per-token API billing.

`DEEP_LANE_AUTH=subscription` (the default) removes the key from the environment
so both lanes fall through to the Claude Code login.

Set `DEEP_LANE_AUTH=api` to leave the environment alone and bill both lanes to
the key instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

VALID_DEEP_AUTH = {"subscription", "api"}
VALID_STT_BACKENDS = {"deepgram", "local"}
DEFAULT_WINDOW_CHARS = 4000


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str | None
    """Captured before the environment is stripped, so `api` mode can tell
    whether a key was actually supplied. None means no key was set, which is the
    normal case — both lanes run on the Claude Code credential."""

    fast_model: str
    """Model for the fast lane. Not validated here: the lane runs through the
    Agent SDK, so the Claude Code CLI owns which model strings are legal."""

    deep_auth: str
    """"subscription" | "api" — see module docstring."""

    stt_backend: str
    """"local" | "deepgram". Validated here so a missing key fails at startup
    instead of killing a capture task mid-run."""

    deepgram_api_key: str | None
    """Only set when stt_backend is "deepgram", where startup has already
    required it."""

    whisper_model: str
    """faster-whisper size: tiny.en | base.en | small.en | medium.en | large-v3.
    Not validated here — the name is the library's to reject, and it does so on
    the first model load."""

    window_chars: int
    """How much transcript tail the lanes receive."""

    mic_device: str | None
    """Override for the "me" source. None means ask pactl for the default —
    which is not necessarily the mic you talk into."""

    monitor_device: str | None
    """Override for the "them" source. None means the default sink's .monitor."""


def load() -> Config:
    load_dotenv()

    key = os.environ.get("ANTHROPIC_API_KEY") or None

    deep_auth = os.environ.get("DEEP_LANE_AUTH", "subscription").strip().lower()
    if deep_auth not in VALID_DEEP_AUTH:
        raise ValueError(
            f"DEEP_LANE_AUTH must be one of {sorted(VALID_DEEP_AUTH)}, got {deep_auth!r}"
        )

    if deep_auth == "subscription" and "ANTHROPIC_API_KEY" in os.environ:
        # Presence, not truthiness: an empty ANTHROPIC_API_KEY still occupies
        # its slot in the credential precedence order, ahead of the Claude Code
        # login, and authenticates as an empty key. `key` is already None in
        # that case, so popping costs the fast lane nothing.
        #
        # ANTHROPIC_AUTH_TOKEN is deliberately left alone: if you set that,
        # you set it on purpose, and it's a legitimate way to authenticate.
        os.environ.pop("ANTHROPIC_API_KEY")

    # Not validated against a list: the fast lane runs through the Agent SDK, so
    # the Claude Code CLI owns which model strings are legal, and an unknown one
    # fails when the session opens rather than silently misbehaving.
    from .fast_lane import DEFAULT_MODEL

    fast_model = os.environ.get("BIRD_BRAIN_FAST_MODEL", DEFAULT_MODEL).strip()

    stt_backend = os.environ.get("STT_BACKEND", "local").strip().lower()
    if stt_backend not in VALID_STT_BACKENDS:
        raise ValueError(
            f"STT_BACKEND must be one of {sorted(VALID_STT_BACKENDS)}, got {stt_backend!r}"
        )
    if stt_backend == "deepgram" and not os.environ.get("DEEPGRAM_API_KEY"):
        raise ValueError("STT_BACKEND=deepgram needs DEEPGRAM_API_KEY set")

    try:
        window_chars = int(
            os.environ.get("BIRD_BRAIN_WINDOW_CHARS", DEFAULT_WINDOW_CHARS)
        )
    except ValueError as exc:
        raise ValueError("BIRD_BRAIN_WINDOW_CHARS must be an integer") from exc

    return Config(
        anthropic_api_key=key,
        fast_model=fast_model,
        deep_auth=deep_auth,
        stt_backend=stt_backend,
        deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY"),
        whisper_model=os.environ.get("BIRD_BRAIN_WHISPER_MODEL", "base.en"),
        window_chars=window_chars,
        mic_device=os.environ.get("BIRD_BRAIN_MIC"),
        monitor_device=os.environ.get("BIRD_BRAIN_MONITOR"),
    )
