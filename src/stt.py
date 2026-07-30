"""Streaming speech-to-text.

Contract: `transcribe(chunks, on_final, cfg)` consumes an async generator of raw
PCM (s16le mono @ 16 kHz) and calls `on_final(text)` for each finalized segment.
Interim/partial results are dropped — the answer is keypress-gated, so partials
buy nothing.

Two backends:
  - deepgram: raw WebSocket, ~$0.45/hr streaming, P50 ~150 ms after endpointing
  - local:    faster-whisper / NVIDIA Parakeet, no per-hour cost, GPU preferred
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Awaitable, Callable

import websockets

from .config import Config

OnFinal = Callable[[str], Awaitable[None] | None]

DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16&sample_rate=16000&channels=1"
    "&model=nova-3&punctuate=true&interim_results=false"
)


async def transcribe(
    chunks: AsyncIterator[bytes], on_final: OnFinal, cfg: Config
) -> None:
    """Dispatch on the backend config.load() has already validated against
    VALID_STT_BACKENDS — no second whitelist here."""
    if cfg.stt_backend == "deepgram":
        await _deepgram(chunks, on_final, cfg.deepgram_api_key)
    else:
        await _local(chunks, on_final, cfg.whisper_model)


async def _deepgram(
    chunks: AsyncIterator[bytes], on_final: OnFinal, api_key: str | None
) -> None:
    headers = {"Authorization": f"Token {api_key}"}

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


_model_lock = asyncio.Lock()
_model: Any = None


async def _get_model(name: str) -> Any:
    """The one WhisperModel, shared by both capture pipelines.

    Both pipelines call this; the second gets the cached instance. A second copy
    would double VRAM — trivial at base.en, ~3 GB at large-v3 — and the two
    instances would serialize on the GPU anyway without sharing a worker pool.
    `num_workers=2` is the library's supported way to let both pipelines decode
    concurrently against one instance.

    Loaded in a thread: the import and the load together block for ~1 s warm and
    far longer on a cold cache, where the weights are downloaded.
    """
    global _model
    async with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = await asyncio.to_thread(
                WhisperModel,
                name,
                device="auto",
                compute_type="default",  # per-device fastest; int8 hobbles the GPU
                num_workers=2,
            )
    return _model


async def _local(
    chunks: AsyncIterator[bytes], on_final: OnFinal, model_name: str
) -> None:
    """Local STT via faster-whisper.

    Whisper is not a streaming model — it wants windows of audio. Simplest
    workable shape: accumulate ~5 s, transcribe, emit. Latency is worse than
    Deepgram but it costs nothing and never leaves the machine.

    `vad_filter` is load-bearing, not a tuning knob: fed a silent window,
    Whisper reliably invents dialogue ("Thank you very much.", "You") and that
    fabrication lands in the transcript both lanes read. Silero drops non-speech
    before decoding, which also makes idle windows ~50x cheaper — and both
    capture streams are idle most of the time.

    TODO: use VAD for segmentation too, so segments break on pauses instead of
    arbitrary 5 s boundaries.
    """
    # Imported here rather than at module scope so the deepgram backend doesn't
    # pay for loading a deep-learning stack it never calls.
    import numpy as np

    # Start the load and the capture together instead of awaiting the model
    # first: `chunks` is a lazy generator, so parec is not spawned until it is
    # first iterated, and a blocking load here would drop the opening seconds of
    # the conversation on the floor. The first window needs 5 s to fill, which
    # the load comfortably finishes inside.
    loading = asyncio.create_task(_get_model(model_name))

    window_bytes = 16000 * 2 * 5  # 5 s of s16le mono @ 16 kHz
    buf = bytearray()

    def decode(model, pcm) -> str:
        # transcribe() returns a lazy generator: consume it *inside* the thread.
        # Draining it on the event loop would run the decode there and stall
        # capture and both lanes for the duration.
        segments, _ = model.transcribe(pcm, language="en", vad_filter=True)
        return " ".join(s.text for s in segments).strip()

    async for chunk in chunks:
        buf.extend(chunk)
        if len(buf) < window_bytes:
            continue
        audio = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
        buf.clear()
        model = await loading
        text = await asyncio.to_thread(decode, model, audio)
        if text:
            result = on_final(text)
            if asyncio.iscoroutine(result):
                await result
