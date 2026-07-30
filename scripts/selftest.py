"""Preflight checks, in dependency order: tools, config, devices, signal, STT,
credentials, trigger.

    ./run.sh --check              everything, including a 6 s mic recording
    ./run.sh --check --no-mic     skip the one step that needs a person

Each check prints PASS or FAIL with the number or name it decided on, so a
failure names one component instead of "nothing works". A FAIL in an early
check makes the later ones meaningless, so the run stops there.

What this cannot check is whether an answer is any good — that needs ears. See
docs/MVP_TEST.md for the steps that need a person.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from src import audio, config

# A dead device streams zeros forever and looks identical to a live one under
# `xxd`, so every audio check is a level measurement rather than a byte count.
SILENT_RMS = 50.0
CLIPPING_PEAK = 32000


class Failed(Exception):
    """A check that later checks depend on."""


FAILED: list[str] = []


def report(name: str, ok: bool, detail: str) -> bool:
    if not ok:
        FAILED.append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {detail}")
    return ok


def rms_peak(raw: bytes) -> tuple[float, int]:
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if not len(a):
        return 0.0, 0
    return float(np.sqrt((a**2).mean())), int(np.abs(a).max())


def record_for(device: str, seconds: float) -> bytes:
    """parec runs until killed, so record by killing it on a timer and keeping
    what it wrote."""
    proc = subprocess.Popen(
        ["parec", f"--device={device}", "--rate=16000", "--channels=1",
         "--format=s16le"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        return proc.stdout.read(int(16000 * 2 * seconds))
    finally:
        proc.terminate()
        proc.wait()


def check_tools() -> None:
    needed = ("pactl", "parec", "pw-play", "sox")
    missing = [t for t in needed if shutil.which(t) is None]
    ok = report(
        "audio tools",
        not missing,
        "all present" if not missing
        else f"missing {', '.join(missing)} — apt install pulseaudio-utils sox pipewire-bin",
    )
    if not ok:
        raise Failed


def check_config() -> config.Config:
    try:
        cfg = config.load()
    except Exception as exc:  # noqa: BLE001 — the point of the check
        report("config", False, repr(exc))
        raise Failed from exc
    report(
        "config",
        True,
        f"stt={cfg.stt_backend} deep_auth={cfg.deep_auth} window={cfg.window_chars}",
    )
    return cfg


def check_devices(cfg: config.Config) -> tuple[str, str]:
    mic = cfg.mic_device or audio.default_mic()
    monitor = cfg.monitor_device or audio.default_sink_monitor()
    report("mic device", True, mic)
    report("monitor device", True, monitor)
    return mic, monitor


def check_monitor_signal(monitor: str) -> None:
    """Play a tone out the default sink and confirm the monitor hears it. This
    is the "them" capture path, end to end, with no person involved."""
    with tempfile.TemporaryDirectory() as tmp:
        tone = Path(tmp) / "tone.wav"
        subprocess.run(
            ["sox", "-n", "-r", "48000", "-c", "2", str(tone),
             "synth", "3", "sine", "440", "vol", "0.3"],
            check=True,
            capture_output=True,
        )
        player = subprocess.Popen(
            ["pw-play", str(tone)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            raw = record_for(monitor, 2.0)
        finally:
            player.wait(timeout=10)

    level, peak = rms_peak(raw)
    ok = level >= SILENT_RMS
    report(
        "monitor carries audio",
        ok,
        f"RMS {level:.0f} peak {peak}"
        + ("" if ok else " — playback is not reaching this monitor"),
    )
    if not ok:
        raise Failed


def check_mic(mic: str) -> bytes:
    input("\n  Press Enter, then speak normally for 6 seconds...")
    print("  recording...")
    raw = record_for(mic, 6.0)
    level, peak = rms_peak(raw)
    ok = level >= SILENT_RMS
    note = ""
    if peak >= CLIPPING_PEAK:
        note = " — clipping, turn the input gain down"
    elif not ok:
        note = " — wrong device, or muted. Set BIRD_BRAIN_MIC"
    report("mic carries audio", ok, f"RMS {level:.0f} peak {peak}{note}")
    if not ok:
        raise Failed
    return raw


def check_stt(cfg: config.Config, mic_audio: bytes | None) -> None:
    if cfg.stt_backend == "deepgram":
        report("deepgram key", bool(cfg.deepgram_api_key), "set")
        return

    from faster_whisper import WhisperModel

    model = WhisperModel(cfg.whisper_model, device="auto", compute_type="default")
    report("whisper model", True, f"{cfg.whisper_model} loaded")

    if mic_audio is None:
        return
    pcm = np.frombuffer(mic_audio, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(pcm, language="en", vad_filter=True)
    text = " ".join(s.text for s in segments).strip()
    # Whisper fed silence invents dialogue, so empty is the honest result here
    # and a wrong-but-confident sentence is the failure this surfaces.
    report("transcribes speech", bool(text), text or "(nothing decoded)")


def check_fast_lane(cfg: config.Config) -> None:
    """Both lanes run on the Claude Code credential now, so this opens the real
    session rather than inspecting a key that no longer exists."""
    import asyncio

    from src.fast_lane import FastLane

    lane = FastLane(model=cfg.fast_model)

    async def open_and_close() -> bool:
        ok = await lane.start()
        await lane.stop()
        return ok

    ok = asyncio.run(open_and_close())
    report("fast lane session", ok, cfg.fast_model if ok else "session would not open")


def check_deep_credential(cfg: config.Config) -> None:
    if shutil.which("claude") is None:
        report("deep lane credential", False, "claude CLI not on PATH")
        return
    proc = subprocess.run(
        ["claude", "-p", "say ok"], capture_output=True, text=True, timeout=120
    )
    ok = proc.returncode == 0
    report(
        "deep lane credential",
        ok,
        f"{cfg.deep_auth} — CLI answered" if ok
        else f"{cfg.deep_auth} — `claude -p` exited {proc.returncode}: "
             f"{proc.stderr.strip()[:120]}",
    )


def check_trigger() -> None:
    """Round-trip a token through the real FIFO reader, which is what a bound
    shortcut writes to."""
    import asyncio

    from src import hotkey

    async def roundtrip() -> str:
        triggers = hotkey.triggers()
        task = asyncio.ensure_future(triggers.__anext__())
        await asyncio.sleep(0.2)  # let the reader attach before writing
        with open(hotkey.fifo_path(), "w") as fifo:
            fifo.write("deep\n")
        try:
            return await asyncio.wait_for(task, timeout=5)
        finally:
            await triggers.aclose()

    try:
        token = asyncio.run(roundtrip())
    except Exception as exc:  # noqa: BLE001 — the point of the check
        report("trigger fifo", False, repr(exc))
        return
    report("trigger fifo", token == "deep", f"{hotkey.fifo_path()} -> {token!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-mic",
        action="store_true",
        help="skip the mic recording, the one check that needs a person",
    )
    args = parser.parse_args()

    print("bird_brain preflight\n")
    try:
        check_tools()
        cfg = check_config()
        mic, monitor = check_devices(cfg)
        check_monitor_signal(monitor)
        mic_audio = None if args.no_mic else check_mic(mic)
        check_stt(cfg, mic_audio)
        check_fast_lane(cfg)
        check_deep_credential(cfg)
        check_trigger()
    except Failed:
        print(f"\nStopped at {FAILED[-1]}: the remaining checks depend on it.")
        return 1

    if FAILED:
        print(f"\n{len(FAILED)} failed: {', '.join(FAILED)}.")
        return 1

    print("\nPreflight clean. What's left needs ears — see docs/MVP_TEST.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
