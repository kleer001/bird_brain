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

## 4. The hotkey (the one Linux-specific gotcha)

Wayland (Ubuntu's default) deliberately has no global key-grab, so we drive the trigger through
a **GNOME custom shortcut** that writes to a FIFO. Create the FIFO first:

```bash
mkfifo /tmp/bird_brain.fifo     # matches BIRD_BRAIN_FIFO default
```

Then: **Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts → +**

| Name | Command | Suggested key |
|---|---|---|
| bird_brain answer | `bash -c 'echo answer > /tmp/bird_brain.fifo'` | `Super+Space` |
| bird_brain deep | `bash -c 'echo deep > /tmp/bird_brain.fifo'` | `Super+D` |

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

## 6. Local STT (optional, no cloud)

Skip Deepgram entirely:

```bash
pip install faster-whisper          # CPU works; GPU (CUDA) much faster
# or NVIDIA Parakeet via NeMo for lower WER on English
```

Set `STT_BACKEND=local` in `.env` and see `src/stt.py` for the local hook. Note "Parakeet" the
ASR model is unrelated to Parakeet AI the product — just a naming collision.

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Monitor stream silent | App opened its own sink; check `pavucontrol` Recording tab |
| `parec` dies mid-call | Default sink changed; the app re-spawns on EOF, but check logs |
| Hotkey does nothing | FIFO missing, or shortcut bound but app not reading; `cat` the FIFO to test |
| Claude answer slow to start | Cold cache (first press) or effort too high; confirm `effort=low` |
| STT gibberish | Wrong sample rate — must be 16 kHz mono `s16le` end to end |
