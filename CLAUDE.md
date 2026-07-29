# bird_brain — working notes

A keypress-triggered voice copilot for Linux: capture both sides of a spoken
conversation, keep a rolling transcript, and on an explicit hotkey hand that
transcript to Claude. Two lanes — a fast reflex answer and a deep tool-using
research session. Prototype stage: the code is coherent and close to runnable,
with `TODO` markers, and has not been run end to end.

## Layout

```
src/config.py      startup config; the fast/deep auth split (read the docstring first)
src/audio.py       parec capture of the default mic and the default sink's .monitor
src/stt.py         streaming STT — Deepgram over raw WebSocket, or local faster-whisper
src/transcript.py  rolling speaker-tagged buffer; window() returns the prompt tail
src/fast_lane.py   one streaming Messages API call per press, no tools
src/deep_lane.py   persistent Claude Agent SDK session, tools on
src/hotkey.py      FIFO trigger fed by a GNOME custom shortcut
src/main.py        glue: two capture pipelines + the trigger loop
SPEC.md            component design, data shapes, latency budget, deferred work
INSTALL.md         Ubuntu setup: PipeWire, hotkeys, venv, keys
```

`SPEC.md` is the design of record. When behavior and spec disagree, decide which
one is wrong and fix that one — don't leave them out of sync.

## Run

```bash
source .venv/bin/activate
python -m src.main            # then press the bound shortcuts
```

Setup is `INSTALL.md`. Triggers can be exercised without touching the keyboard:

```bash
echo answer > /tmp/bird_brain.fifo    # fast lane
echo deep   > /tmp/bird_brain.fifo    # deep lane
```

Audio devices can be checked independently of the app:

```bash
pactl get-default-sink        # .monitor of this is the "them" source
parec --device="$(pactl get-default-sink).monitor" --rate=16000 --channels=1 --format=s16le | xxd | head
```

There is no test suite. Verification is running the thing and watching the
`[me]` / `[them]` lines and lane output.

## Conventions

- Async throughout; `asyncio` tasks, no threads. Capture, STT, and both lanes
  run concurrently and are cancelled together on exit.
- One path, no fallbacks. Bad config raises at startup rather than degrading.
  The one deliberate exception is the deep lane's lazy import, so the fast lane
  still works without the Agent SDK installed.
- A press while that lane is still working is dropped, not queued — a stale
  answer arriving late is worse than none.
- `snake_case` functions, `PascalCase` classes, stdlib → third-party → local
  imports. Comments explain why, not what.

## Load-bearing details

- **Auth split.** `ANTHROPIC_API_KEY` in the environment outranks a Claude Code
  subscription credential, and the Agent SDK's child process inherits our
  environment. `DEEP_LANE_AUTH=subscription` (default) pops the key after
  capturing it and passes it to the fast lane explicitly. `config.load()` must
  therefore run before `DeepLane.start()`. See `SPEC.md` §5.1.
- **Prompt caching.** The stable prefix (instructions + background) carries the
  `cache_control` breakpoints; the transcript tail is volatile and must never
  carry one.
- **Fast lane has no tools** — with thinking disabled, a tool call can be
  emitted as plain text and silently never run.
- **The FIFO is opened `O_RDWR`** so a writer stays on the pipe and reads never
  hit EOF when a shortcut's `echo` exits.
- **Wayland has no global key grab.** The FIFO trigger is the workaround, not a
  placeholder for one; alternatives are listed in `INSTALL.md` §4.

## Claude models

Model IDs, pricing, and API parameters change. Look them up before editing
`fast_lane.py` or `deep_lane.py` rather than trusting what's in the file.
