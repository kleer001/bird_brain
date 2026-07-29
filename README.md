# bird_brain

A voice-driven, on-demand answer copilot for Linux. It listens to both sides of a
conversation (you + whoever you're talking to), keeps a rolling transcript, and — **only
when you press a key** — hands that transcript to Claude and streams an answer back.

Modeled on [Parakeet AI](https://www.parakeet-ai.com/)'s interaction (transcribe continuously,
respond on a hotkey — space bar in Parakeet), but built native on Ubuntu instead of in a
browser, and with a second "deep" lane that puts a full Claude Code / Agent SDK session in the
loop for anything that needs tools or research.

## Why native, not a browser extension

System-audio capture in the browser is a dead end on Linux:

| | Firefox | Chrome (Linux) |
|---|---|---|
| System audio via `getDisplayMedia` | ignored silently ([Bug 1541425](https://bugzilla.mozilla.org/show_bug.cgi?id=1541425)) | not supported on Linux ([explainer](https://github.com/eladalon1983/screen-share-explainers/blob/main/systemAudio_Explainer.md)) |
| Tab audio | no | one shared tab only |

PipeWire (default on Ubuntu 22.04+) exposes a `.monitor` source carrying everything you hear,
from any app, no permission prompt. That makes the capture problem — the hard part on macOS and
in browsers — nearly free here, and **browser-agnostic**: it doesn't matter whether the call is
in Firefox, Chrome, or a native Zoom/Teams app.

## Two lanes

```
  mic  ─┐                              ┌─ press SPACE ─▶ FAST lane ─▶ answer to overlay
        ├─▶ STT ─▶ rolling transcript ─┤   (1 Claude call, no tools, streaming, ~1–3s)
monitor ┘                              └─ press DEEP  ─▶ DEEP lane ─▶ answer whenever ready
                                           (persistent Claude Agent SDK session, tools on)
```

- **Fast lane** — the Parakeet-style reflex. One `messages.stream` call, thinking off, low
  effort, no tools, prompt-cached prefix. Answer starts arriving in seconds.
- **Deep lane** — the "Claude Code in the conversation" you actually asked for. A long-lived
  Agent SDK session you feed the same transcript window; it can grep, fetch, run things, and
  reply on its own schedule.

The trigger is explicit (a keypress per lane), so there's no turn-taking classifier and no
guessing when to speak.

## Prototype scope

Voice-only, terminal output. No overlay window, no TTS, no stealth/screen-share hiding — those
are later. See `SPEC.md` for what's deliberately deferred.

## Files

| File | What |
|---|---|
| `INSTALL.md` | Ubuntu setup: PipeWire, hotkeys, Python env, API keys |
| `SPEC.md` | Component-by-component design, data shapes, latency budget, deferred work |
| `requirements.txt` | Python deps |
| `.env.example` | Keys to fill in |
| `src/audio.py` | `parec` capture of monitor + mic |
| `src/stt.py` | Streaming speech-to-text (Deepgram; local Whisper alt noted) |
| `src/transcript.py` | Rolling, speaker-tagged transcript buffer |
| `src/fast_lane.py` | The reflex Claude call |
| `src/deep_lane.py` | Persistent Agent SDK session |
| `src/hotkey.py` | FIFO trigger fed by a GNOME custom shortcut |
| `src/main.py` | Glue: wire captures → STT → buffer → triggers |

## Prior art — don't start from zero

If you'd rather fork than greenfield, both already ship an overlay + local transcription and
you'd swap in the Claude lanes:

- [Natively](https://github.com/Natively-AI-assistant/natively-cluely-ai-assistant) — overlay,
  real-time transcription, stealth mode, local RAG, BYOK. Check its license: advertised free for
  personal/educational/non-commercial use, which is **not** permissive.
- [Meetily](https://openalternative.co/meetily) — MIT, fully local transcription, no meeting bot.

## Status / confidence

The PipeWire capture path and the browser limitations are well-documented and long-standing
(high confidence). The GNOME/Wayland global-shortcut situation moves between releases — this
project sidesteps it with a custom-shortcut → FIFO trigger (see `INSTALL.md`). STT and Claude
snippets are prototype-shaped: coherent and close to runnable, with `TODO` markers where your
keys and tuning go. Nothing here has been run end-to-end yet.
