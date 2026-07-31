# bird_brain — working notes

A keypress-triggered voice copilot for Linux: capture both sides of a spoken
conversation, keep a rolling transcript, and on an explicit hotkey hand that
transcript to Claude. Two lanes — a fast reflex answer and a deep tool-using
research session. Prototype stage, but it runs: both lanes have been exercised
end to end and the latency numbers in the spec are measured rather than budgeted.

## Layout

```
src/config.py      startup config; the auth split (read the docstring first)
src/audio.py       parec capture of the default mic and the default sink's .monitor
src/stt.py         streaming STT — local faster-whisper, or Deepgram over raw WebSocket
src/transcript.py  rolling speaker-tagged buffer; window() returns the prompt tail
src/fast_lane.py   persistent Agent SDK session, no tools, one turn per press
src/deep_lane.py   persistent Agent SDK session, tools on
src/hotkey.py      FIFO trigger fed by a desktop custom shortcut
src/main.py        glue: two capture pipelines + the trigger loop
scripts/selftest.py     the preflight behind ./run.sh --check
bootstrap.sh            one-command install; seeds .env from .env.example verbatim
run.sh                  launcher, and --check
prompts/background.txt  default fast-lane background
docs/SPEC.md            component design, data shapes, measured latency, deferred work
docs/INSTALL.md         Ubuntu setup: PipeWire, hotkeys, venv, credentials
docs/MVP_TEST.md        the checks that need ears, not a script
```

`docs/SPEC.md` is the design of record. When behavior and spec disagree, decide which
one is wrong and fix that one — don't leave them out of sync.

## Run

```bash
./run.sh --check              # preflight — measures rather than assumes
./run.sh                      # then press the bound shortcuts
```

Setup is `docs/INSTALL.md`. Triggers can be exercised without touching the keyboard:

```bash
echo answer > /tmp/bird_brain.fifo    # fast lane
echo deep   > /tmp/bird_brain.fifo    # deep lane
```

Audio devices can be checked independently of the app:

```bash
pactl get-default-sink        # .monitor of this is the "them" source
pactl get-default-source      # often NOT the mic you talk into — see BIRD_BRAIN_MIC
parec --device="$(pactl get-default-sink).monitor" --rate=16000 --channels=1 --format=s16le | xxd | head
```

To check a device carries signal rather than just bytes, record a few seconds and
measure it — `parec ... > /tmp/x.raw`, then compute RMS over the `int16` samples.
A dead or wrong device yields a steady stream of near-zero samples, which looks
identical to a working one at the `xxd` level.

There is no unit-test suite. Verification is `./run.sh --check`, which measures
the mechanical parts — devices, signal levels, transcription, both sessions, the
trigger round-trip — and then `docs/MVP_TEST.md` for what a script cannot judge:
whether an answer is any good and whether it arrived fast enough to say out loud.

## Conventions

- Async throughout; `asyncio` tasks, no threads. Capture, STT, and both lanes
  run concurrently and are cancelled together on exit.
- One path, no fallbacks. Bad config raises at startup rather than degrading.
  The deliberate exception is the lanes themselves: both import the Agent SDK
  lazily and each disables itself with a printed reason if its session won't
  open, so a missing SDK or an unauthenticated CLI is a legible message at
  startup rather than an import crash. The lanes fail independently.
- A press while that lane is still working is dropped, not queued — a stale
  answer arriving late is worse than none.
- `snake_case` functions, `PascalCase` classes, stdlib → third-party → local
  imports. Comments explain why, not what.

## Load-bearing details

- **One credential, both lanes.** Both drive the Claude Code CLI through the
  Agent SDK, so `claude login` covers everything and nothing bills per token.
  Nothing in `src/` imports `anthropic` — don't reintroduce it.
- **Auth split.** `ANTHROPIC_API_KEY` is optional and opting into it bills
  *both* lanes: an explicit key outranks the Claude Code credential and the SDK's
  child processes inherit our environment. `DEEP_LANE_AUTH=subscription`
  (default) pops the key so both lanes fall through to the login. `config.load()`
  must therefore run before either lane's `start()`. See `docs/SPEC.md` §5.1.
  The pop is keyed on **presence, not truthiness**: an empty
  `ANTHROPIC_API_KEY=""` still holds its precedence slot and authenticates as an
  empty key.
- **No prompt-caching control.** `cache_control` breakpoints are not reachable
  through the Agent SDK — the CLI decides. The system prompt is therefore sized
  for content, not to clear a cache floor; a 17,000-character prefix measured the
  same as an 848-character one. Don't reintroduce a token-floor warning.
- **Fast lane has no tools** (`allowed_tools=[]`) — a conversation copilot has
  nothing to run, and tool definitions are prompt weight on a latency budget.
- **Both lanes pass `system_prompt` as a plain string**, which *replaces* Claude
  Code's preset rather than appending to it, and `setting_sources=[]`, so
  `~/.claude` rules written for interactive use cannot reshape answers here.
- **`.env.example` is copied to `.env` verbatim** by `bootstrap.sh`, so every
  uncommented line in it must be a working default. A placeholder on an active
  line reads as configured and fails later, somewhere else.
- **The deep lane's Bash gate is a `PreToolUse` hook, not `can_use_tool`.** The
  callback is only consulted when the CLI decides to ask, and headless it does
  not ask — Bash executes unprompted with the callback wired. The hook also
  needs `setting_sources=[]`, or `~/.claude/settings.json` allow rules apply
  ahead of it. See `docs/SPEC.md` §3.5.
- **Local Whisper runs with `vad_filter=True`.** On silence it otherwise invents
  dialogue that lands in the transcript as real speech. Drain the segment
  generator inside the worker thread, never on the event loop.
- **Nothing awaits the capture tasks until shutdown**, so `main._report_death`
  is what turns a dead pipeline into a visible error instead of an app that
  prints `[ready]` and transcribes silence forever.
- **The FIFO is opened `O_RDWR`** so a writer stays on the pipe and reads never
  hit EOF when a shortcut's `echo` exits.
- **No global key grab under Wayland.** The FIFO trigger is the workaround, not a
  placeholder for one, and it is desktop-agnostic — it works unchanged on X11 and
  KDE, where a global-hotkey library would also be an option. Alternatives are
  listed in `docs/INSTALL.md` §4.

## Claude models

Model IDs, pricing, and API parameters change. Look them up before editing
`fast_lane.py` or `deep_lane.py` rather than trusting what's in the file.
