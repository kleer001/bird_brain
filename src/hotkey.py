"""Trigger input via FIFO.

Wayland has no global key grab, so we don't try. A GNOME custom shortcut runs
`echo answer > /tmp/bird_brain.fifo` and we read lines from the other end. See
INSTALL.md §4 for binding the keys, and for the X11 / evdev / portal
alternatives if you outgrow this.

The FIFO is opened O_RDWR rather than O_RDONLY: that keeps a writer on the pipe
permanently, so reads never hit EOF when a shortcut's `echo` exits, and we avoid
a reopen loop.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

DEFAULT_FIFO = "/tmp/bird_brain.fifo"
VALID = {"answer", "deep"}


def fifo_path() -> str:
    return os.environ.get("BIRD_BRAIN_FIFO", DEFAULT_FIFO)


def ensure_fifo(path: str) -> None:
    if not os.path.exists(path):
        os.mkfifo(path, 0o600)


async def triggers() -> AsyncIterator[str]:
    """Yield trigger tokens ("answer" | "deep") as they arrive on the FIFO."""
    path = fifo_path()
    ensure_fifo(path)
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()
    buf = bytearray()

    def on_readable() -> None:
        try:
            data = os.read(fd, 1024)
        except BlockingIOError:
            return
        buf.extend(data)
        while b"\n" in buf:
            line, _, rest = bytes(buf).partition(b"\n")
            buf.clear()
            buf.extend(rest)
            token = line.decode(errors="replace").strip()
            if token in VALID:
                queue.put_nowait(token)
            elif token:
                print(f"[hotkey] ignoring unknown trigger: {token!r}")

    loop.add_reader(fd, on_readable)
    try:
        while True:
            yield await queue.get()
    finally:
        loop.remove_reader(fd)
        os.close(fd)
