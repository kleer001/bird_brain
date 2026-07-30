"""bird_brain — wire capture -> STT -> transcript -> keypress-gated lanes.

    python -m src.main

Press your bound shortcuts (docs/INSTALL.md §4):
    Meta+Space -> fast answer
    Meta+D     -> deep research
"""

from __future__ import annotations

import asyncio
import contextlib

from . import audio, config, hotkey, stt
from .deep_lane import DeepLane
from .fast_lane import FastLane
from .transcript import TranscriptBuffer


async def pipeline(
    device: str, speaker: str, buf: TranscriptBuffer, cfg: config.Config
) -> None:
    """Capture one device, transcribe it, tag finals with `speaker`."""

    async def on_final(text: str) -> None:
        await buf.append(speaker, text)
        print(f"  [{speaker}] {text}")

    await stt.transcribe(audio.capture(device), on_final, cfg)


def _report_death(task: asyncio.Task) -> None:
    """Nothing awaits the capture tasks until shutdown, so an exception in one
    would otherwise vanish: the app prints "[ready] listening" and transcribes
    silence forever. Surface it the moment it happens."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"\n[fatal] task {task.get_name()!r} died: {exc!r}")


async def main() -> None:
    # Must run before DeepLane.start(): in subscription mode this strips
    # ANTHROPIC_API_KEY from the environment so the Agent SDK's child process
    # falls through to the Claude Code credential instead of API billing.
    cfg = config.load()
    buf = TranscriptBuffer()

    audio.require_tools()

    # The default *source* is often not the mic you talk into — a headset can be
    # the default sink while the default source stays on the motherboard input.
    # BIRD_BRAIN_MIC / BIRD_BRAIN_MONITOR override without touching system state.
    mic = cfg.mic_device or audio.default_mic()
    monitor = cfg.monitor_device or audio.default_sink_monitor()
    print(f"[audio] me   <- {mic}")
    print(f"[audio] them <- {monitor}")
    print(f"[stt]   backend: {cfg.stt_backend}")

    fast = FastLane(model=cfg.fast_model)
    deep = DeepLane()

    tasks = [
        asyncio.create_task(pipeline(mic, "me", buf, cfg), name="stt-me"),
        asyncio.create_task(pipeline(monitor, "them", buf, cfg), name="stt-them"),
    ]
    for task in tasks:
        task.add_done_callback(_report_death)

    # Both lanes spawn their own Claude Code CLI session; overlapped, startup
    # costs the slower of the two rather than the sum. They cannot share one
    # session — a deep-lane turn runs for minutes and would block every press.
    await asyncio.gather(fast.start(), deep.start(auth=cfg.deep_auth))

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
        task = asyncio.create_task(coro, name=f"{lane}-run")
        task.add_done_callback(_report_death)
        inflight[lane] = task

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
        await asyncio.gather(fast.stop(), deep.stop())


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
