"""Streaming speech-to-text.

Contract: `transcribe(chunks, on_final)` consumes an async generator of raw PCM
(s16le mono @ 16 kHz) and calls `on_final(text)` for each finalized segment.
Interim/partial results are dropped — the answer is keypress-gated, so partials
buy nothing.

Two backends:
  - deepgram: raw WebSocket, ~$0.45/hr streaming, P50 ~150 ms after endpointing
  - local:    faster-whisper / NVIDIA Parakeet, no per-hour cost, GPU preferred
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator, Awaitable, Callable

import websockets

OnFinal = Callable[[str], Awaitable[None] | None]

DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16&sample_rate=16000&channels=1"
    "&model=nova-3&punctuate=true&interim_results=false"
)


async def transcribe(chunks: AsyncIterator[bytes], on_final: OnFinal) -> None:
    """Dispatch to the configured backend."""
    backend = os.environ.get("STT_BACKEND", "deepgram")
    if backend == "deepgram":
        await _deepgram(chunks, on_final)
    elif backend == "local":
        await _local(chunks, on_final)
    else:
        raise ValueError(f"unknown STT_BACKEND: {backend}")


async def _deepgram(chunks: AsyncIterator[bytes], on_final: OnFinal) -> None:
    key = os.environ["DEEPGRAM_API_KEY"]
    headers = {"Authorization": f"Token {key}"}

    # Reconnect loop: a dropped socket shouldn't kill the session.
    while True:
        try:
            async with websockets.connect(
                DEEPGRAM_URL, additional_headers=headers
            ) as ws:

                async def pump() -> None:
                    async for chunk in chunks:
                        await ws.send(chunk)
                    await ws.send(json.dumps({"type": "CloseStream"}))

                async def drain() -> None:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") != "Results":
                            continue
                        alt = msg["channel"]["alternatives"][0]
                        text = alt.get("transcript", "").strip()
                        # interim_results=false, but gate anyway — cheap insurance
                        if text and msg.get("is_final", True):
                            result = on_final(text)
                            if asyncio.iscoroutine(result):
                                await result

                await asyncio.gather(pump(), drain())
        except Exception as exc:  # noqa: BLE001 — prototype: log and retry
            print(f"[stt] reconnecting after: {exc!r}")
            await asyncio.sleep(1.0)


async def _local(chunks: AsyncIterator[bytes], on_final: OnFinal) -> None:
    """Local STT via faster-whisper.

    Whisper is not a streaming model — it wants windows of audio. Simplest
    workable shape: accumulate ~5 s, transcribe, emit. Latency is worse than
    Deepgram but it costs nothing and never leaves the machine.

    TODO: replace the fixed window with VAD-based segmentation (silero) so
    segments break on pauses instead of arbitrary 5 s boundaries.
    """
    from faster_whisper import WhisperModel  # optional dep

    import numpy as np

    model = WhisperModel("base.en", device="auto", compute_type="int8")
    window_bytes = 16000 * 2 * 5  # 5 s of s16le mono @ 16 kHz
    buf = bytearray()

    async for chunk in chunks:
        buf.extend(chunk)
        if len(buf) < window_bytes:
            continue
        audio = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
        buf.clear()
        segments, _ = await asyncio.to_thread(model.transcribe, audio, language="en")
        text = " ".join(s.text for s in segments).strip()
        if text:
            result = on_final(text)
            if asyncio.iscoroutine(result):
                await result
