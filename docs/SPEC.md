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
  desktop custom shortcut ──▶ FIFO ──▶ hotkey listener ──▶ trigger ──────┤
                                                                        ▼
                                                        ┌──────────────────────┐
                                       Meta+Space ─────▶│ fast_lane.answer()    │──▶ stdout
                                                        │  ClaudeSDKClient      │
                                                        └──────────────────────┘
                                                        ┌──────────────────────┐
                                           Meta+D ─────▶│ deep_lane.ask()       │──▶ stdout
                                                        │  ClaudeSDKClient       │  (async)
                                                        └──────────────────────┘
```

Single Python process, `asyncio`. Long-lived: two capture→STT pipelines, the hotkey listener, and
a CLI session per lane — both are opened once at startup and reused, since connecting costs about
a second and the fast lane cannot afford to pay it per press. Only the per-press *turn* is spawned
on a keypress, one in flight per lane.

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

One Agent SDK turn per press, on a session opened once at startup and reused:

- `model="claude-haiku-4-5"` by default (`BIRD_BRAIN_FAST_MODEL`).
- `system_prompt` as a **plain string**, which replaces Claude Code's preset rather than
  appending to it — the preset is opt-in via `{"type": "preset"}`. The lane gets its own
  instructions and background with no coding-agent persona underneath.
- **No tools** (`allowed_tools=[]`) — a copilot has nothing to run, and tool definitions are
  prompt weight on a latency budget.
- `setting_sources=[]` — a rule written for interactive Claude Code use must not reshape
  answers here.
- `include_partial_messages=True`, because the SDK otherwise hands back whole messages and the
  answer would appear only after generation finished. Time-to-first-word is the number that
  matters on a lane you read aloud.
- Stream deltas straight to stdout, and print the measured first-word time on every press.

**No prompt caching control.** `cache_control` breakpoints are not reachable through the Agent
SDK; the CLI decides. Measurement showed a 17,000-character system prompt costs approximately
nothing against the variance of the CLI hop itself, so the prefix is sized for content rather
than for a cache floor.

**The session is stateful.** Reconnecting costs about a second, and paying that per press would
double time-to-first-word — so presses share a session and each one sees the ones before it.
That is a deliberate trade, and the thing to revisit first if answers start drifting.
**Latency — measured, not budgeted.** The original target was a first word inside one second.
The Agent SDK path does not reach it, and the gap is the point of the table:

| Stage | Measured |
|---|---|
| Keypress → FIFO → listener | < 20 ms |
| Build window + query | < 30 ms |
| Session connect | ~1 s, once at startup |
| First word | **3–16 s**, high variance |
| First word → complete | ~0.4 s |

A no-tools turn through the same CLI costs about 4 s on the same machine (measured against the
deep lane given nothing to research), so ~3–4 s is the floor this architecture offers and the
spread above it is unexplained. The variance is not the prompt: an 848-character system prompt
and a 17,000-character one measured the same within noise.

Levers not yet tried, in the order worth trying: tighten the length instruction (answers run
well past the two-to-four sentences asked for, and long output is wall-clock), reset the session
per press to test whether accumulated history is the cost, and the SDK's own `effort` and
`thinking` options.

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
system_prompt = instructions + <background>…</background>            # one string
user   = window(n_chars=4000)                                        # volatile, uncached
```

## 5. Config (`.env`)

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Optional, and opt-in to per-token billing for **both** lanes — see §5.1 |
| `DEEP_LANE_AUTH` | `subscription` (default) or `api` — see §5.1 |
| `DEEPGRAM_API_KEY` | STT. Required when `STT_BACKEND=deepgram`, validated at startup |
| `STT_BACKEND` | `local` (default) or `deepgram` |
| `BIRD_BRAIN_WHISPER_MODEL` | local STT model size, default `base.en` |
| `BIRD_BRAIN_MIC` | override the "me" source; unset → `pactl get-default-source` |
| `BIRD_BRAIN_MONITOR` | override the "them" source; unset → default sink's `.monitor` |
| `BIRD_BRAIN_FIFO` | FIFO path, default `/tmp/bird_brain.fifo` |
| `BIRD_BRAIN_RESUME` | path to a text file of your background/knowledge base |
| `BIRD_BRAIN_WINDOW_CHARS` | transcript tail size, default 4000 |

### 5.1 Credentials — one surface, one login

Both lanes drive the Claude Code CLI through the Agent SDK, so both authenticate the same way:
whatever `claude login` established. **No API key is required, and nothing bills per token.**

Each lane opens its own session. They cannot share one — a deep-lane turn runs for minutes and
would block every press — so startup spawns two CLI children and overlaps their connects.

The environment still matters, because an explicit `ANTHROPIC_API_KEY` outranks a subscription
credential and the SDK's child processes inherit our environment. Setting one silently routes
**both** lanes to per-token billing. `config.load()` decides:

- `DEEP_LANE_AUTH=subscription` (default) — pop `ANTHROPIC_API_KEY` from the environment so the
  children fall through to the Claude Code login. Requires `claude login` once.
- `DEEP_LANE_AUTH=api` — leave the environment untouched and bill to the key.

The pop is keyed on the variable being **present**, not on it being non-empty. An empty
`ANTHROPIC_API_KEY=""` still occupies its slot in the precedence order — ahead of the Claude Code
login — and authenticates as an empty key, which breaks exactly the setup subscription mode
exists to serve.

`ANTHROPIC_AUTH_TOKEN` is deliberately never touched: if it's set, it was set on purpose.

If a session fails to open (no CLI, not logged in), that lane prints why and disables itself.
The other is unaffected — the lanes fail independently.

*Historical note: the fast lane originally called the Messages API directly, which the Python
`anthropic` SDK cannot authenticate from a Claude Code login (that credential lives in
`~/.claude/.credentials.json`, which the SDK does not read). That split is why `DEEP_LANE_AUTH`
exists; it survives because the key-precedence trap it guards against is still real.*

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
