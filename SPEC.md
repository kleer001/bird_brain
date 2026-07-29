# bird_brain — Technical Spec

## 1. Goal

Continuous transcription of a two-party voice conversation on Ubuntu, with an **on-demand**
LLM answer triggered by a keypress. Two response lanes: a low-latency reflex (fast) and a
tool-capable session (deep). Voice-in, text-out for the prototype.

## 2. Architecture

```
┌────────────┐   PCM    ┌────────────┐  finals  ┌──────────────────┐
│ parec: mic │ ───────▶ │ STT (me)   │ ───────▶ │                  │
└────────────┘          └────────────┘          │  TranscriptBuffer │──┐
┌────────────┐   PCM    ┌────────────┐  finals  │  (rolling, tagged)│  │
│ parec:.mon │ ───────▶ │ STT (them) │ ───────▶ │                  │  │
└────────────┘          └────────────┘          └──────────────────┘  │
                                                                        │ window()
   GNOME custom shortcut ──▶ FIFO ──▶ hotkey listener ──▶ trigger ──────┤
                                                                        ▼
                                                        ┌──────────────────────┐
                                            SPACE ─────▶│ fast_lane.answer()    │──▶ stdout
                                                        │  1 messages.stream    │
                                                        └──────────────────────┘
                                                        ┌──────────────────────┐
                                            DEEP  ─────▶│ deep_lane.ask()       │──▶ stdout
                                                        │  ClaudeSDKClient       │  (async)
                                                        └──────────────────────┘
```

Single Python process, `asyncio`. Four long-lived tasks: two capture→STT pipelines, one hotkey
listener, and the deep-lane session. The fast lane is spawned per keypress.

## 3. Components

### 3.1 Audio capture (`audio.py`)

- Two `parec` subprocesses, one per source, raw `s16le` mono @ 16 kHz (what STT wants).
- **Mic** → `pactl get-default-source` (strip any `.monitor`).
- **Them** → `pactl get-default-sink` + `.monitor`.
- Each yields ~4 KB chunks over an async generator. No resampling in-process — `parec` does it.

Failure modes to handle: no default sink/source (headless), monitor disabled in the profile,
`parec` exiting when the sink changes mid-call (re-spawn on EOF).

### 3.2 Speech-to-text (`stt.py`)

Interface: an async function taking a chunk generator + a callback `on_final(text)`. Two
implementations:

- **Deepgram streaming** (default reference) — raw WebSocket to
  `wss://api.deepgram.com/v1/listen`, `Authorization: Token <key>`. ~$0.45/hr, P50 ~150 ms after
  endpointing. Read `channel.alternatives[0].transcript`, gate on `is_final`.
- **Local** (no cloud, no per-hour cost) — `faster-whisper` or NVIDIA `parakeet` via
  `nemo`/`whisper.cpp`. Higher setup cost, GPU strongly preferred. Same `on_final` contract.

Only **final** segments enter the buffer. Interim/partial results are ignored for now — the
answer only fires on a keypress, so partials add nothing.

### 3.3 Transcript buffer (`transcript.py`)

- Append-only list of `(speaker, text)` where `speaker ∈ {"me", "them"}`.
- `window(n_chars)` → the tail formatted with speaker labels, capped so the Claude prompt stays
  bounded. This tail is the **volatile** part of the prompt — it must **not** be cached.
- Thread/task-safe append (both STT tasks write concurrently).

### 3.4 Fast lane (`fast_lane.py`)

One `client.messages.stream` call:

- `model="claude-opus-5"`.
- `thinking={"type": "disabled"}` + `output_config={"effort": "low"}` — latency-critical, no
  tools, so we skip the thinking phase and start emitting text immediately. (On Opus 5, disabled
  thinking is allowed at effort `high` or lower.)
- **No tools** — this sidesteps the disabled-thinking "tool call as plain text" failure mode
  entirely; the only residual risk is stray `<thinking>` tags, guarded by a generic
  "no internal/system XML tags" line in the system prompt.
- **Prompt caching**: system instructions + your resume/knowledge base are two stable
  `cache_control` breakpoints. The transcript window goes in the **user** turn (uncached).
- Stream tokens straight to stdout.

Prefix cache stays warm as long as presses are < 5 min apart. Optionally pre-warm once at
startup with a `max_tokens: 0` request against the stable prefix (see `fast_lane.prewarm`).

**Latency budget** (target, warm cache):

| Stage | Budget |
|---|---|
| Keypress → FIFO → listener | < 20 ms |
| Build window + request | < 30 ms |
| Claude TTFT (cached prefix, no thinking) | ~0.5–2 s |
| First useful sentence streamed | ~1–3 s total |

### 3.5 Deep lane (`deep_lane.py`)

A persistent `ClaudeSDKClient` (Agent SDK) in streaming-input mode, opened once and reused. On
DEEP trigger, `query()` the current transcript window plus a standing instruction ("research /
verify / look this up"); stream `AssistantMessage`/`TextBlock` text to stdout as it lands. This
is the lane with tools, files, MCP — the "Claude Code in the conversation."

Turns here run seconds to minutes; the deep lane never blocks the fast lane.

### 3.6 Hotkey (`hotkey.py`)

Wayland has no global key grab, so we don't try. A **GNOME custom shortcut** runs a one-liner
that writes a token to a FIFO; the listener reads lines and pushes triggers onto an asyncio
queue. Tokens: `answer` (fast), `deep`. See `INSTALL.md` for binding the keys.

Alternatives if you outgrow this: `xdg-desktop-portal` GlobalShortcuts (compositor-version
dependent), `/dev/input` via evdev (needs `input` group), or an X11 session with a normal
hotkey lib.

## 4. Data shapes

```python
# transcript segment
("them", "so how would you handle a schema migration with zero downtime?")

# trigger, from FIFO
"answer" | "deep"

# fast-lane prompt assembly
system = [ {instructions, cache_control}, {resume, cache_control} ]   # stable, cached
user   = window(n_chars=4000)                                        # volatile, uncached
```

## 5. Config (`.env`)

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude (both lanes) |
| `DEEPGRAM_API_KEY` | STT (omit if using local Whisper) |
| `BIRD_BRAIN_FIFO` | FIFO path, default `/tmp/bird_brain.fifo` |
| `BIRD_BRAIN_RESUME` | path to a text file of your background/knowledge base |

## 6. Deliberately deferred

- **Overlay window** (always-on-top, click-through). Terminal is fine for the prototype.
- **Screen-share hiding / stealth.** Later; on Linux this is compositor-dependent.
- **TTS.** Text-out only — cheaper and lower latency than speech-out. (There is no realtime
  speech-to-speech Claude API; a text overlay skips that entirely.)
- **Speaker diarization beyond mic-vs-monitor.** Two streams already separate you from them.
- **Interim transcript display.** Only finals matter when the answer is keypress-gated.
- **Multi-party.** The mic/monitor split assumes two parties.

## 7. Open questions to resolve while building

1. Does your default sink's monitor actually carry the call audio, or does the app open its own
   sink? (`pavucontrol` → Recording tab to verify.)
2. Deepgram vs local: is the per-hour cost or the local GPU/setup the bigger cost for you?
3. How much transcript tail does a good answer need? Start at ~4 KB, tune.
