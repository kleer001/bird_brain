# bird_brain — live MVP test

A run-through of the whole path, in order of dependency: audio → STT → transcript
→ each lane. There is no test suite; this is the verification. Each step states
what passing looks like, so a failure localizes to one component instead of
"nothing works".

Needs a working install (`INSTALL.md`) and about ten minutes. Steps 1–2 are solo;
from step 4 on you need a second voice — a call, a video, anything playing out the
default sink.

---

## 0. Preflight

```bash
pactl info | grep 'Server Name'          # PipeWire
which pactl parec                        # both, or: sudo apt install pulseaudio-utils
pactl get-default-sink                   # ".monitor" of this is the "them" source
pactl get-default-source                 # often NOT the mic you talk into
```

If the default source is not your headset mic, set `BIRD_BRAIN_MIC` in `.env` to
the right source name from `pactl list sources short`. This is the single most
common reason `[me]` lines never appear.

Check what credential each lane can see:

```bash
.venv/bin/python -c "from anthropic import Anthropic; c=Anthropic(); print('fast lane — api_key:', bool(c.api_key), 'auth_token:', bool(c.auth_token))"
claude -p 'say ok'                       # deep lane, in subscription mode
```

**Pass:** the first prints at least one `True`; the second prints `ok`. Two
`False` means the fast lane will disable itself at startup — expected if you only
ran `claude login`, and step 5 will not apply (see `INSTALL.md` §3).

---

## 1. Both sources carry signal, not just bytes

A wrong or dead device streams zeros forever and looks identical to a working one
under `xxd`. Measure it. Talk during the first command; play something during the
second.

```bash
parec --device="$(pactl get-default-source)" --rate=16000 --channels=1 --format=s16le > /tmp/me.raw &
sleep 8; kill %1

parec --device="$(pactl get-default-sink).monitor" --rate=16000 --channels=1 --format=s16le > /tmp/them.raw &
sleep 8; kill %1

python3 -c "
import numpy as np
for f in ('/tmp/me.raw','/tmp/them.raw'):
    a = np.fromfile(f, dtype=np.int16).astype(np.float32)
    print(f, 'RMS', round(float(np.sqrt((a**2).mean())), 1), 'peak', int(np.abs(a).max()))
"
```

**Pass:** RMS in the hundreds and peak in the thousands for both. RMS under ~20 is
a dead device — fix that before going further. Peak pinned at 32767 is clipping;
turn the input gain down.

---

## 2. STT transcribes your own voice

```bash
./run.sh
```

**Pass:** startup prints the two resolved devices, the STT backend, a deep-lane
line, and `[ready] listening`. Then talk for ten seconds and watch `[me]` lines
appear with roughly what you said. Local STT emits one line per 5-second window,
so expect a few seconds of lag and mid-sentence breaks — that is the current
segmentation, not a fault.

If lines appear that nobody said, `vad_filter` has been turned off; it must stay
on (`src/stt.py`).

---

## 3. Triggers arrive

The FIFO can be exercised without touching the keyboard, which separates "the
trigger is broken" from "the shortcut is unbound":

```bash
echo answer > /tmp/bird_brain.fifo       # in a second terminal
echo deep   > /tmp/bird_brain.fifo
```

**Pass:** `--- FAST ---` / `--- DEEP ---` appear in the app's terminal.

Now press the bound shortcuts (`INSTALL.md` §4) and confirm the same two headers
appear. If the `echo` works and the keypress does not, the binding is the problem,
not the app.

---

## 4. The far side lands as `[them]`

Play a voice out the default sink — a call, a video, a recording of yourself.

**Pass:** transcript lines tagged `[them]`, interleaved with your `[me]` lines.
Both tags in one session is the core of the whole app; if only one appears, go
back to step 1 for the source that is missing.

---

## 5. Fast lane

Have the far side ask something answerable from your background file, then press
`Super+Space`.

**Pass:** an answer starts streaming in about a second and reads as a reply to what
was actually said. A closing `[fast] in=… out=… cache_read=…` line reports usage.

Two specific things to check:

- On the **first** press of a session, `cache_read` is 0 and `cache_write` is
  large. On the **second**, `cache_read` should be large. If it stays 0, the
  stable prefix is under the 512-token minimum — startup would also have printed
  `[fast] NOT CACHING`. Point `BIRD_BRAIN_RESUME` at a bigger file.
- Press twice in quick succession. The second press must print
  `[fast] busy — press ignored`. Dropping a press is intended: mid-conversation a
  stale answer arriving late is worse than none.

---

## 6. Deep lane

Have the far side make a checkable factual claim — a version number, a library
behavior, a benchmark figure — then press `Super+D`.

**Pass:** the lane researches with real tools and reports back in a few sentences,
naming what it checked against. Expect tens of seconds, not one second; that is
the lane's whole purpose. It runs concurrently, so `[me]` / `[them]` lines must
keep appearing while it works — if the transcript freezes, the lane is blocking
the event loop.

Given a transcript with nothing checkable in it, the correct behavior is to say so
rather than invent something to research.

### The Bash gate

If the lane decides it needs a shell, it must stop and ask:

```
[deep] !! Bash wants to run:
    <command>
[deep] allow? (y/N)
```

**Pass:** anything other than `y` denies, and the session continues instead of
dying. Deny is the default because the transcript contains the *other* party's
speech, so a command can be shaped by input you do not control. This gate is the
reason `Bash` is absent from the auto-approved tool list.

---

## 7. Shutdown

`Ctrl-C` in the app's terminal. Both capture tasks and any in-flight lane run are
cancelled together, and the deep-lane session disconnects.

To stop a backgrounded run, kill the specific PID you started:

```bash
./run.sh & echo $! > /tmp/bird_brain.pid
kill "$(cat /tmp/bird_brain.pid)"
```

**Do not** stop it with `pkill -f src.main`. `-f` matches every process whose full
argv contains that string, and `python -m src.main` is a common enough entrypoint
that unrelated projects on the same machine answer to it — the pattern reaches
past bird_brain and kills them too.
