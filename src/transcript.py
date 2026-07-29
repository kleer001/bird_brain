"""Rolling, speaker-tagged transcript buffer.

Two STT tasks (mic and monitor) append concurrently. `window()` returns the
formatted tail that goes into the Claude prompt — this is the *volatile* part of
the prompt and must never carry a cache_control breakpoint.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

LABELS = {"me": "ME", "them": "THEM"}


@dataclass
class Segment:
    speaker: str  # "me" | "them"
    text: str
    at: float = field(default_factory=time.monotonic)


class TranscriptBuffer:
    def __init__(self, max_segments: int = 500) -> None:
        self._segments: list[Segment] = []
        self._max = max_segments
        self._lock = asyncio.Lock()

    async def append(self, speaker: str, text: str) -> None:
        async with self._lock:
            self._segments.append(Segment(speaker, text))
            if len(self._segments) > self._max:
                del self._segments[: len(self._segments) - self._max]

    def window(self, n_chars: int = 4000) -> str:
        """Tail of the conversation, newest-last, capped at n_chars.

        Reads without the lock: appends are atomic list ops and a
        one-segment-stale read is fine here.
        """
        lines: list[str] = []
        total = 0
        for seg in reversed(self._segments):
            line = f"{LABELS.get(seg.speaker, seg.speaker)}: {seg.text}"
            total += len(line) + 1
            if total > n_chars:
                break
            lines.append(line)
        return "\n".join(reversed(lines))

    def __len__(self) -> int:
        return len(self._segments)
