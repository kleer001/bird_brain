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

- **Deepgram streaming** (cloud alternative) — raw WebSocket to
  `wss://api.deepgram.com/v1/listen`, `Authorization: Token <key>`. ~$0.45/hr, P50 ~150 ms after
  endpointing. Read `channel.alternatives[0].transcript`, gate on `is_final`.
- **Local** (default; no cloud, no per-hour cost) — `faster-whisper`. GPU strongly preferred.
  Same `on_final` contract.

Only **final** segments enter the buffer. Interim/partial results are ignored for now — the
answer only fires on a keypress, so partials add nothing.

`transcribe()` takes the backend as an argument; `config.load()` picks and validates it, so
selecting `deepgram` without `DEEPGRAM_API_KEY` fails at startup rather than killing a capture
task on its first chunk.

**The local backend runs with `vad_filter=True`, and that is load-bearing.** Handed a window of
silence, Whisper reliably invents dialogue — `"Thank you very much."`, `"You"` — and that
fabrication enters the transcript both lanes read as real speech. Silero drops non-speech before
decoding, which also makes idle windows roughly 50x cheaper; both capture streams are idle most
of the time. Two further consequences of the fixed-window design:

- `model.transcribe()` returns a lazy generator. It must be drained **inside** the worker thread;
  draining it on the event loop moves the decode there and stalls capture and both lanes.
- `compute_type` is left at `default` rather than `int8`, which would hobble a GPU.

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
- `stop_reason == "refusal"` is checked before trusting the streamed text. Opus 5's safety
  classifiers can decline a request and return HTTP 200 with empty content; unchecked, the lane
  prints nothing and looks broken.

Prefix cache stays warm as long as presses are < 5 min apart. Pre-warm once at startup with a
`max_tokens: 0` request against the stable prefix (see `fast_lane.prewarm`).

**The prefix has to clear 512 tokens or nothing caches at all.** That is Opus 5's minimum
cacheable prefix, and `INSTRUCTIONS` alone is ~200 tokens — so the first breakpoint can never
cache on its own, and the second only does if `BIRD_BRAIN_RESUME` points at roughly 1250+
characters of background. Below that the cache silently never engages, on every press. `prewarm`
reports `cache_creation_input_tokens` and prints a `NOT CACHING` warning when it comes back zero,
so this shows up at startup instead of as unexplained latency.

**Latency budget** (target, warm cache):

| Stage | Budget |
|---|---|
| Keypress → FIFO → listener | < 20 ms |
| Build window + request | < 30 ms |
| Claude TTFT (cached prefix, no thinking) | ~0.5–2 s |
| First useful sentence streamed | ~1–3 s total |

### 3.5 Deep lane (`deep_lane.py`)

A persistent `ClaudeSDKClient` (Agent SDK) in streaming-input mode, opened once via `connect()`
and reused. On DEEP trigger, `query()` the current transcript window plus a standing instruction
("research / verify / look this up"); stream `AssistantMessage`/`TextBlock` text to stdout as it
lands. This is the lane with tools, files, MCP — the "Claude Code in the conversation."

Turns here run seconds to minutes; the deep lane never blocks the fast lane.

**Tool posture.** Read-only tools (`Read`, `Grep`, `Glob`, `WebSearch`, `WebFetch`) are
auto-approved via `allowed_tools`. `Bash` is not, and is gated on an explicit `y/N` at the
terminal, because the transcript this lane reasons over contains the *other* party's speech — a
shell command here can be shaped by input we do not control.

Two non-obvious requirements make that gate actually hold:

- **The gate is a `PreToolUse` hook, not `can_use_tool`.** A `can_use_tool` callback is only
  consulted when the CLI decides to ask, and in SDK/headless mode it does not ask: with the
  callback wired and `Bash` absent from `allowed_tools`, Bash still executes unprompted — for a
  command matching a settings allow rule and for one matching none. A `PreToolUse` hook matched on
  `Bash` runs on every call and its `permissionDecision` is honored.
- **`setting_sources=[]`.** Otherwise the session loads `~/.claude/settings.json`, whose
  `permissions.allow` rules are applied ahead of any gate. A `Bash(echo:*)` grant made for
  interactive use is not a grant for commands derived from a phone call. The cost is that the
  session also skips project `CLAUDE.md`; the standing instruction covers what it needs.

The confirmation prompt reads from stdin in a worker thread, so waiting on the keyboard does not
stall capture or the fast lane.

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
| `ANTHROPIC_API_KEY` | Fast lane. Blank → fall back to an `ant auth login` profile |
| `DEEP_LANE_AUTH` | `subscription` (default) or `api` — see §5.1 |
| `DEEPGRAM_API_KEY` | STT. Required when `STT_BACKEND=deepgram`, validated at startup |
| `STT_BACKEND` | `local` (default) or `deepgram` |
| `BIRD_BRAIN_WHISPER_MODEL` | local STT model size, default `base.en` |
| `BIRD_BRAIN_MIC` | override the "me" source; unset → `pactl get-default-source` |
| `BIRD_BRAIN_MONITOR` | override the "them" source; unset → default sink's `.monitor` |
| `BIRD_BRAIN_FIFO` | FIFO path, default `/tmp/bird_brain.fifo` |
| `BIRD_BRAIN_RESUME` | path to a text file of your background/knowledge base |
| `BIRD_BRAIN_WINDOW_CHARS` | transcript tail size, default 4000 |

### 5.1 Auth split — why the two lanes can bill differently

The lanes talk to different surfaces:

| Lane | Surface | Credential |
|---|---|---|
| Fast | Anthropic API (`messages.stream`) | API key, or an `ant auth login` profile — always per-token |
| Deep | Claude Agent SDK → Claude Code CLI | Claude Code's own login (subscription) **or** an API key |

The trap: an explicit `ANTHROPIC_API_KEY` in the environment outranks an OAuth /
subscription credential, and the Agent SDK spawns a child process that inherits our
environment. So simply having the key set routes the deep lane to per-token billing whether you
wanted that or not — silently.

`config.load()` handles it, and **must run before `DeepLane.start()`**:

- `DEEP_LANE_AUTH=subscription` (default) — capture the key, `os.environ.pop` it, pass it to
  `FastLane(api_key=...)` explicitly. The child process sees no key and falls through to
  whatever `claude login` established. Requires `claude login` once.
- `DEEP_LANE_AUTH=api` — leave the environment untouched; both lanes bill to the API key.

The pop is keyed on the variable being **present**, not on it being non-empty. An empty
`ANTHROPIC_API_KEY=""` still occupies its slot in the credential precedence order — ahead of the
Claude Code login — and authenticates as an empty key, so leaving it in place breaks the deep
lane in exactly the setup subscription mode exists to serve.

`ANTHROPIC_AUTH_TOKEN` is deliberately never touched: if it's set, it was set on purpose and is
a legitimate credential.

**The fast lane needs its own credential.** It calls the Messages API directly, so a Claude Code
login does not reach it: that credential lives in `~/.claude/.credentials.json`, which the Python
`anthropic` SDK does not read. The fast lane needs `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or
an `ant auth login` profile under `~/.config/anthropic/`. With none of those the fast lane detects
it at startup, announces itself disabled, and every press is a no-op; the deep lane is unaffected.

If the Agent SDK session fails to open (no CLI, not logged in), the deep lane prints why,
disables itself, and the fast lane continues unaffected.

## 6. Deliberately deferred

- **Overlay window** (always-on-top, click-through). Terminal is fine for the prototype.
- **Screen-share hiding / stealth.** Later; on Linux this is compositor-dependent.
- **TTS.** Text-out only — cheaper and lower latency than speech-out. (There is no realtime
  speech-to-speech Claude API; a text overlay skips that entirely.)
- **Speaker diarization beyond mic-vs-monitor.** Two streams already separate you from them.
- **Interim transcript display.** Only finals matter when the answer is keypress-gated.
- **Multi-party.** The mic/monitor split assumes two parties.

## 7. Open questions to resolve while building

1. **Answered: yes.** A libpulse client reading `<default-sink>.monitor` captures playback at full
   level. Verified by recording the monitor while playing a tone into the default sink: rms 1319
   against an rms 1.9 silent baseline. Note `pw-record --target <sink>` is *not* a substitute — it
   does not tap monitor ports (rms 1.8 under the same tone), so the pulse `.monitor` source name is
   required and `parec` is the supported path.
2. How much transcript tail does a good answer need? Start at ~4 KB, tune.
3. Does the 5 s fixed window cut too many sentences in half to be worth keeping over
   VAD-driven segmentation?

Note the default *source* is a separate trap from the sink monitor: a USB headset is commonly the
default sink while the default source stays on the motherboard analog input, so
`pactl get-default-source` returns a device that hears hum rather than your voice.
`BIRD_BRAIN_MIC` overrides it without changing system state.
