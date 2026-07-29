"""Audio capture via PipeWire's PulseAudio-compatible `parec`.

Two sources:
  - the microphone (default source) — "me"
  - the default sink's `.monitor` — "them" (everything you hear, from any app)

Both are captured as raw s16le mono @ 16 kHz, which is what the STT layer wants.
No in-process resampling: parec does it.
"""

from __future__ import annotations

import asyncio
import subprocess

RATE = 16000
CHUNK = 4096  # bytes per read (~128 ms at 16 kHz mono s16le)


def default_sink_monitor() -> str:
    """Monitor source carrying whatever is playing out the default sink."""
    sink = subprocess.check_output(["pactl", "get-default-sink"], text=True).strip()
    return f"{sink}.monitor"


def default_mic() -> str:
    """Default input source. Strip a trailing .monitor if the default is odd."""
    src = subprocess.check_output(["pactl", "get-default-source"], text=True).strip()
    return src.removesuffix(".monitor")


async def capture(device: str, *, rate: int = RATE):
    """Yield raw PCM chunks from `device`. Re-spawns parec if it exits (e.g. the
    default sink changed mid-call)."""
    while True:
        proc = await asyncio.create_subprocess_exec(
            "parec",
            "--device", device,
            "--format=s16le",
            f"--rate={rate}",
            "--channels=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while True:
                chunk = await proc.stdout.read(CHUNK)
                if not chunk:
                    break  # parec exited; fall through to re-spawn
                yield chunk
        finally:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()
        await asyncio.sleep(0.5)  # brief backoff before re-spawn
