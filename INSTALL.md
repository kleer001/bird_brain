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

The trigger must fire while *another* window has focus — during a call you are looking at the
call, not at this terminal. That rules out reading keys from the app's own terminal, and it is
also why the app's stdin stays free for the deep lane's `y/N` Bash gate.

So the trigger is a **desktop custom shortcut** that writes a token to a FIFO. That route is
desktop-agnostic — GNOME, KDE, Wayland, X11, unchanged — and it stays scriptable, which is how
the lanes get exercised without touching the keyboard. Create the FIFO first:

```bash
mkfifo /tmp/bird_brain.fifo     # matches BIRD_BRAIN_FIFO default
```

Find your desktop and session, which decides both the panel to use and whether the in-app
alternative at the end of this section is open to you:

```bash
echo "$XDG_CURRENT_DESKTOP / $XDG_SESSION_TYPE"
```

**KDE Plasma** — System Settings → Shortcuts → Custom Shortcuts → Edit → New → Global Shortcut →
Command/URL. Set the command on the **Action** tab and the key on **Trigger**.

**GNOME** — Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts → +

| Name | Command | Suggested key |
|---|---|---|
| bird_brain answer | `bash -c 'echo answer > /tmp/bird_brain.fifo'` | `Meta+Space` |
| bird_brain deep | `bash -c 'echo deep > /tmp/bird_brain.fifo'` | `Meta+D` |

`Meta` is the Windows/Command key (Mod4), not `Alt` (Mod1) — desktops bind the two very
differently, and most of the free chords are on `Meta`.

**Check the chord is free before binding it.** A collision is silent in the worst way: KDE's
kglobalaccel refuses the duplicate and stores an *empty* key rather than reporting an error, so
the shortcut simply never fires and the config looks almost right. On KDE:

```bash
grep -inE 'Meta\+Space|Meta\+D' ~/.config/kglobalshortcutsrc ~/.config/khotkeysrc
```

Anything returned is already taken — `Alt+Space` is commonly KRunner, and `Meta+D` is commonly
Show Desktop or a tiling script such as Krohnkite. Note that a tiling script re-registers its
defaults every time KWin loads, so clearing its key in the file does not stick.

**Reassign contested keys in the GUI, not by editing the files.** kglobalaccel owns
`kglobalshortcutsrc` while it runs and rewrites it from memory, so hand-edits are silently
reverted. Only the System Settings shortcut editor unregisters the previous owner — it prompts
to *Reassign* when a key is taken, which is the step no amount of file editing reproduces. Back
the files up before touching them at all:

```bash
cp ~/.config/khotkeysrc ~/.config/kglobalshortcutsrc ~/some-backup-dir/
```

**After changing a binding, restart `kded5`** — khotkeys keeps the old wiring otherwise, and a
module reload is not enough. The config can be completely correct while the shortcut still fires
the wrong action, or nothing at all:

```bash
kquitapp5 kded5 && (setsid kded5 &)
```

> Avoid a bare `Space` binding — it'll fight every text field. A modified chord stays live while
> another window has focus, which is the whole point. Avoid `Alt+D` too: browsers use it for the
> address bar, and a global grab takes it from all of them.

Test without the app running:

```bash
cat /tmp/bird_brain.fifo &      # reader must be started FIRST
# press Meta+Space → "answer" prints
```

Start the reader before pressing anything. With no reader on the pipe, `echo answer > fifo`
blocks instead of failing, and every press leaves a writer parked there; they all flush the
moment a reader appears, tagged with whatever action was bound *when each was pressed*. A burst
of stale tokens looks exactly like a shortcut firing repeatedly, and it will send you chasing a
bug that isn't there. `./run.sh --check` exercises this path correctly. The running app is never
affected — `hotkey.py` holds the FIFO open `O_RDWR` for its whole lifetime, so a reader is always
present.

### If custom shortcuts don't fit

- **X11 session** → any global-hotkey lib (`pynput`) can grab the keys in-process, with no
  desktop configuration at all. Wayland deliberately offers no global key grab, so this is an
  X11-only route and the app stops responding to the hotkey if the session ever moves to Wayland.
- **evdev** — read `/dev/input` directly; add yourself to the `input` group
  (`sudo usermod -aG input $USER`, re-login). Works under Wayland, slightly invasive.
- **`xdg-desktop-portal` GlobalShortcuts** — the "correct" API, but support varies by GNOME
  version; try the custom-shortcut route first.

## 5. Run

```bash
source .venv/bin/activate
python -m src.main
```

Talk. When you want an answer, press `Meta+Space` (fast) or `Meta+D` (deep). Output streams to
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
