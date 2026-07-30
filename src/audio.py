"""Audio capture via PipeWire's PulseAudio-compatible `parec`.

Two sources:
  - the microphone (default source) — "me"
  - the default sink's `.monitor` — "them" (everything you hear, from any app)

Both are captured as raw s16le mono @ 16 kHz, which is what the STT layer wants.
No in-process resampling: parec does it.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

RATE = 16000
CHUNK = 4096  # bytes per read (~128 ms at 16 kHz mono s16le)


def require_tools() -> None:
    """PipeWire can be running fine while the PulseAudio CLI tools are absent —
    they ship separately. Say which package, rather than surfacing a bare
    FileNotFoundError from the first subprocess call."""
    missing = [t for t in ("pactl", "parec") if shutil.which(t) is None]
    if missing:
        raise RuntimeError(
            f"missing audio tool(s): {', '.join(missing)} — "
            "install with: sudo apt install pulseaudio-utils"
        )


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
    default sink changed mid-call).

    parec's stderr is surfaced rather than discarded: a bad device name makes it
    exit immediately, and this loop would otherwise re-spawn it forever with no
    hint as to why no audio is arriving.
    """
    while True:
        proc = await asyncio.create_subprocess_exec(
            "parec",
            "--device", device,
            "--format=s16le",
            f"--rate={rate}",
            "--channels=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        got_audio = False
        try:
            while True:
                chunk = await proc.stdout.read(CHUNK)
                if not chunk:
                    break  # parec exited; fall through to re-spawn
                got_audio = True
                yield chunk
        finally:
            if proc.returncode is None:
                proc.terminate()
            await proc.wait()
        if not got_audio:
            # Read stderr only on the branch that reports it — the process has
            # exited, so what it wrote is still sitting in the pipe buffer.
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            detail = f": {err}" if err else ""
            print(
                f"[audio] parec produced nothing for {device!r} "
                f"(rc={proc.returncode}){detail}"
            )
        await asyncio.sleep(0.5)  # brief backoff before re-spawn
