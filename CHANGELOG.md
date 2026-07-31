# Changelog

Notable changes to bird_brain. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [semantic](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-07-31

First release.

### Added

- **Dual-source capture.** Two `parec` pipelines — the default mic and the
  default sink's `.monitor` — give a speaker-tagged transcript of both sides of a
  conversation, from any app, with no permission prompt. `parec` re-spawns if the
  default sink changes mid-call.
- **Local speech-to-text by default.** `faster-whisper` on your own machine: no
  key, no per-hour cost, nothing uploaded. One model instance shared by both
  pipelines, loaded alongside capture so the opening seconds aren't lost to a
  cold start. Deepgram is available as a cloud alternative.
- **Two answer lanes on two hotkeys.** A fast reflex lane (Haiku, no tools,
  streaming) for "give me something to say", and a deep research lane (Agent SDK,
  tools on) for "go check that". Both read the same transcript window; a press
  while a lane is busy is dropped rather than queued.
- **One credential for both lanes.** Both drive the Claude Code CLI through the
  Agent SDK, so `claude login` covers everything and nothing bills per token.
- **Gated shell access in the deep lane.** `Bash` is withheld from the
  auto-approved tools and gated on an explicit `y/N`, because the transcript this
  lane reasons over contains the other party's speech.
- **FIFO trigger.** Wayland offers no global key grab, so a desktop custom
  shortcut writes a token to a FIFO. Desktop-agnostic, and scriptable — the lanes
  can be exercised without touching the keyboard.
- **One-command installer** (`bootstrap.sh`) and a **preflight** (`./run.sh
  --check`) that measures rather than assumes: it plays a tone and listens on the
  monitor, records and transcribes your voice, opens both lane sessions, and
  round-trips a token through the real FIFO.
- Documentation: [SPEC](docs/SPEC.md) (design of record, measured latency,
  deferred work), [INSTALL](docs/INSTALL.md), and [MVP_TEST](docs/MVP_TEST.md)
  for the checks that need ears rather than a script.

### Fixed

- `.env.example` shipped `STT_BACKEND=deepgram` with a placeholder key. Since
  `bootstrap.sh` copies that file to `.env` verbatim, a fresh install came up on
  the cloud backend with a non-functional key — passing startup validation and
  the preflight, then failing at runtime — while the documented default is local
  transcription with no key at all. Every uncommented line in `.env.example` is
  now a working default.
- Removed the unused `anthropic` dependency. Nothing in `src/` has called the
  Messages API since the fast lane moved to the Agent SDK.

### Changed

- Documentation corrected to match the Agent SDK migration. `.env.example`,
  `INSTALL.md`, `MVP_TEST.md`, `CLAUDE.md` and the `config.py` docstrings still
  described the fast lane as a direct Messages API call needing its own key, and
  documented `cache_control` breakpoints and a token floor that are not reachable
  through the Agent SDK.

### Known limitations

- First word on the fast lane measures 3–16 s through the CLI hop, with high and
  so-far-unexplained variance. See [SPEC §3.4](docs/SPEC.md) for what has been
  ruled out and the levers not yet tried.
- Local STT segments on fixed 5-second windows, so lines break mid-sentence.
- Fast-lane sessions are stateful: each press sees the presses before it.
- Two parties only — the mic/monitor split is the speaker separation.

[0.1.0]: https://github.com/kleer001/bird_brain/releases/tag/v0.1.0
