# bird_brain — Install & Setup (Ubuntu 22.04+)

## 1. System audio prerequisites

Ubuntu 22.04+ ships PipeWire with PulseAudio-compatible tooling. Verify and grab the tools:

```bash
# PulseAudio CLI works against PipeWire via pipewire-pulse
pactl info | grep 'Server Name'      # should mention PipeWire
sudo apt install -y pulseaudio-utils pavucontrol   # parec, pactl, mixer GUI
```

Find your capture devices:

```bash
pactl get-default-sink                 # e.g. alsa_output.pci-0000_00_1f.3.analog-stereo
pactl get-default-source               # your mic
pactl list sources short | grep monitor
```

The **monitor** of your default sink is what carries the far-side voice:

```bash
echo "$(pactl get-default-sink).monitor"
```

Sanity-check that the call audio really flows through it — start playing anything, then:

```bash
parec --device="$(pactl get-default-sink).monitor" --format=s16le --rate=16000 --channels=1 \
  | pv > /dev/null      # should show bytes flowing (apt install pv)
```

If it's silent, open `pavucontrol` → **Recording** tab while `parec` runs and confirm it's
attached to the right monitor; some apps create their own sink.

## 2. Python environment

```bash
cd bird_brain
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pulls the Claude SDK, a WebSocket client for Deepgram, and dotenv. The Agent
SDK (deep lane) and `faster-whisper` (local STT) are optional — see comments in that file.

The Claude Agent SDK also needs the Claude Code CLI on PATH:

```bash
npm install -g @anthropic-ai/claude-code     # provides the runtime the Agent SDK drives
```

## 3. API keys and the two billing paths

```bash
cp .env.example .env
$EDITOR .env      # fill ANTHROPIC_API_KEY, and DEEPGRAM_API_KEY if using cloud STT
```

Point `BIRD_BRAIN_RESUME` at a plain-text file of your background — the knowledge base the fast
lane answers from.

The two lanes can bill differently, and `DEEP_LANE_AUTH` picks which:

| `DEEP_LANE_AUTH` | Fast lane | Deep lane | You need |
|---|---|---|---|
| `subscription` (default) | API key, per-token | Claude Code login — **not** API credit | `claude login` once |
| `api` | API key, per-token | same API key, per-token | nothing extra |

In `subscription` mode the app removes `ANTHROPIC_API_KEY` from its own environment before
starting the Agent SDK, because an explicit key outranks a subscription credential and the SDK's
child process inherits the environment. The fast lane still gets the key — passed directly. So:

```bash
claude login          # once, if using DEEP_LANE_AUTH=subscription
claude -p 'say ok'    # confirm the CLI is authenticated
```

If the deep lane can't authenticate it says so at startup, disables itself, and the fast lane
keeps working.

**`claude login` does not cover the fast lane.** The two lanes hit different surfaces: the fast
lane calls the Messages API through the Python `anthropic` SDK, which cannot read the credential
`claude login` writes to `~/.claude/.credentials.json`. So a subscription login alone gives you a
working deep lane and a fast lane that 401s on the first press. The fast lane needs one of:

| Option | How | Billing |
|---|---|---|
| API key | `ANTHROPIC_API_KEY` in `.env` | Per-token against the key |
| OAuth profile | `ant auth login` → profile in `~/.config/anthropic/` | Per that profile's credential |
| Auth token | `ANTHROPIC_AUTH_TOKEN` in the environment | Per that token |

Check what the SDK can currently see:

```bash
python -c "from anthropic import Anthropic; c=Anthropic(); print('api_key:', bool(c.api_key), 'auth_token:', bool(c.auth_token))"
```

Both `False` means the fast lane has no credential. If you take the `ant auth login` route, note it
can conflict with Claude Code's own login — keep one, and see `ant auth status` to check which
credential source is winning.

## 4. The hotkey (the one Linux-specific gotcha)

Wayland (Ubuntu's default) deliberately has no global key-grab, so the trigger is a **desktop
custom shortcut** that writes a token to a FIFO. That route is desktop-agnostic: it works
unchanged on GNOME, KDE, Wayland, and X11, which is why it's the default even on sessions where a
global-hotkey library would also work. Create the FIFO first:

```bash
mkfifo /tmp/bird_brain.fifo     # matches BIRD_BRAIN_FIFO default
```

Then bind two commands, wherever your desktop keeps custom shortcuts:

- **GNOME** — Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts → +
- **KDE Plasma** — System Settings → Keyboard → Shortcuts → Add New → Command/URL

| Name | Command | Suggested key |
|---|---|---|
| bird_brain answer | `bash -c 'echo answer > /tmp/bird_brain.fifo'` | `Super+Space` |
| bird_brain deep | `bash -c 'echo deep > /tmp/bird_brain.fifo'` | `Super+D` |

Check which session you're in with `echo $XDG_SESSION_TYPE` / `echo $XDG_CURRENT_DESKTOP` — it
decides which panel above applies, and whether the X11 alternative below is open to you.

> Avoid a bare `Space` binding — it'll fight every text field. A `Super`-modified chord stays
> live while another window has focus, which is the whole point.

Test without the app running:

```bash
cat /tmp/bird_brain.fifo &      # reader
# press Super+Space → "answer" prints
```

### If custom shortcuts don't fit

- **X11 session** (pick "Ubuntu on Xorg" at login) → any global-hotkey lib (`pynput`) works.
- **evdev** — read `/dev/input` directly; add yourself to the `input` group
  (`sudo usermod -aG input $USER`, re-login). Works under Wayland, slightly invasive.
- **`xdg-desktop-portal` GlobalShortcuts** — the "correct" API, but support varies by GNOME
  version; try the custom-shortcut route first.

## 5. Run

```bash
source .venv/bin/activate
python -m src.main
```

Talk. When you want an answer, press `Super+Space` (fast) or `Super+D` (deep). Output streams to
the terminal.

## 6. Local STT (the default, no cloud)

`STT_BACKEND=local` is the default and `faster-whisper` is in `requirements.txt`, so this needs no
extra step — no key, no per-hour cost, and no audio leaves the machine.

Model size is `BIRD_BRAIN_WHISPER_MODEL` (`tiny.en` → `base.en` → `small.en` → `medium.en` →
`large-v3`). `base.en` on a CUDA GPU decodes a 5 s window in roughly 0.2 s; the same window takes
about 1 s on CPU, which is still ahead of the 5 s it took to record.

The device is chosen automatically. Confirm the GPU is actually being used rather than assumed:

```bash
python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count(), 'CUDA device(s)')"
```

Zero means it will run on CPU. GPU inference also needs cuDNN alongside cuBLAS; if loading a model
raises a missing-library error, `pip install nvidia-cudnn-cu12` supplies it.

To use Deepgram instead, set `STT_BACKEND=deepgram` and `DEEPGRAM_API_KEY` — startup refuses the
combination without the key. Note "Parakeet" the ASR model is unrelated to Parakeet AI the
product — just a naming collision.

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Monitor stream silent | App opened its own sink; check `pavucontrol` Recording tab |
| `[me]` lines never appear | Default source isn't the mic you talk into. Set `BIRD_BRAIN_MIC` |
| `parec` dies mid-call | Default sink changed; the app re-spawns on EOF, but check logs |
| `[audio] parec produced nothing` | Bad device name, or `pulseaudio-utils` not installed |
| Hotkey does nothing | FIFO missing, or shortcut bound but app not reading; `cat` the FIFO to test |
| Claude answer slow to start | Cold cache (first press) or effort too high; confirm `effort=low` |
| `[fast] NOT CACHING` at startup | `BIRD_BRAIN_RESUME` too small — the prefix is under 512 tokens |
| Fast lane 401s, deep lane fine | The fast lane needs its own credential; a `claude login` doesn't reach it (§3) |
| Transcript contains things nobody said | Whisper hallucinating on silence — `vad_filter` must stay on |
| STT gibberish | Wrong sample rate — must be 16 kHz mono `s16le` end to end |
