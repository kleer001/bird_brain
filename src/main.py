"""bird_brain — wire capture -> STT -> transcript -> keypress-gated lanes.

    python -m src.main

Press your bound shortcuts (INSTALL.md §4):
    Super+Space -> fast answer
    Super+D     -> deep research
"""

from __future__ import annotations

import asyncio
import contextlib

from . import audio, config, hotkey, stt
from .deep_lane import DeepLane
from .fast_lane import FastLane
from .transcript import TranscriptBuffer


async def pipeline(device: str, speaker: str, buf: TranscriptBuffer) -> None:
    """Capture one device, transcribe it, tag finals with `speaker`."""

    async def on_final(text: str) -> None:
        await buf.append(speaker, text)
        print(f"  [{speaker}] {text}")

    await stt.transcribe(audio.capture(device), on_final)


async def main() -> None:
    # Must run before DeepLane.start(): in subscription mode this strips
    # ANTHROPIC_API_KEY from the environment so the Agent SDK's child process
    # falls through to the Claude Code credential instead of API billing.
    cfg = config.load()
    buf = TranscriptBuffer()

    mic = audio.default_mic()
    monitor = audio.default_sink_monitor()
    print(f"[audio] me   <- {mic}")
    print(f"[audio] them <- {monitor}")

    fast = FastLane(api_key=cfg.anthropic_api_key)
    deep = DeepLane()

    tasks = [
        asyncio.create_task(pipeline(mic, "me", buf), name="stt-me"),
        asyncio.create_task(pipeline(monitor, "them", buf), name="stt-them"),
    ]

    await fast.prewarm()
    await deep.start(auth=cfg.deep_auth)

    # One in-flight run per lane. A second press while a lane is busy is
    # dropped rather than queued — mid-conversation, a stale answer arriving
    # late is worse than no answer.
    inflight: dict[str, asyncio.Task] = {}

    def launch(lane: str, coro) -> None:
        running = inflight.get(lane)
        if running and not running.done():
            print(f"[{lane}] busy — press ignored")
            coro.close()
            return
        inflight[lane] = asyncio.create_task(coro, name=f"{lane}-run")

    print(f"[ready] listening. FIFO: {hotkey.fifo_path()}")
    try:
        async for token in hotkey.triggers():
            window = buf.window(cfg.window_chars)
            if token == "answer":
                print("\n--- FAST ---")
                launch("fast", fast.answer(window))
            elif token == "deep":
                print("\n--- DEEP ---")
                launch("deep", deep.ask(window))
    finally:
        for task in [*tasks, *inflight.values()]:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, *inflight.values(), return_exceptions=True)
        await deep.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
