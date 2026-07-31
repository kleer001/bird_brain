# bird_brain — live MVP test

Everything mechanical is in the preflight script. What is left here is what a
script cannot judge: whether an answer is any good, whether it arrived fast
enough to say out loud, and whether the transcript kept up while a lane was
thinking.

```bash
./run.sh --check          # tools, config, devices, signal levels, STT, credentials, trigger
./run.sh --check --no-mic # same, minus the one step that needs you to talk
```

Fix anything it reports before going further — it stops at the first failure the
later checks depend on, and names the component. Then run the app and work down
this page.

```bash
./run.sh
```

You need a second voice for steps 2–4: a call, a video, anything playing out the
default sink.

---

## 1. Both speakers land in the transcript

Talk. Then let the far side talk.

**Pass:** `[me]` and `[them]` lines, correctly attributed. Local STT emits one
line per 5-second window, so expect a few seconds of lag and mid-sentence
breaks — that is the current segmentation, not a fault.

**Watch for:** lines nobody said. That means `vad_filter` got turned off;
Whisper fed silence invents dialogue, and it lands in the transcript both lanes
read as though it were real speech.

**Watch for:** domain words consistently mangled — `base.en` is small, and
proper nouns are where it shows. If it matters for your conversations, raise
`BIRD_BRAIN_WHISPER_MODEL` to `small.en` and repeat this step.

---

## 2. Fast lane — is it fast enough to speak?

Have the far side ask something answerable from your background file. Press
`Meta+Space`.

**Pass:** the answer reads as a reply to what was actually said, and is short
enough to say out loud without editing. That last part is the real test — a
correct answer you would never read aloud is a failed answer here.

**On speed, read the number rather than judging it.** Every press prints its own
measurement:

```
[fast] first word 4.2s | total 4.6s
```

Measured range through the CLI hop is 3–16 s to first word, with high and
so-far-unexplained variance (SPEC §3.4). Anything in that band is the current
architecture, not a fault on your machine. The gap between first word and
complete should stay under a second — if *that* stretches, the answer is running
long past the two-to-four sentences the instructions ask for, and length is
wall-clock on a lane you read aloud.

**Then press it twice in quick succession.** The second press must print
`[fast] busy — press ignored`. Dropping it is intended: mid-conversation, a
stale answer arriving late is worse than none.

**The session is stateful**, so each press sees the presses before it. That is a
deliberate trade — reconnecting costs about a second — and it is the first thing
to suspect if answers start drifting toward earlier questions over a long run.

---

## 3. Deep lane — does it research, and does it stay out of the way?

Have the far side make a checkable factual claim: a version number, a library
behavior, a benchmark figure. Press `Meta+D`.

**Pass:** it uses real tools and reports back naming what it checked against.
Tens of seconds is expected and is the point of the lane.

**Pass, equally:** given a transcript with nothing checkable in it, it says so
instead of inventing something to research.

**Keep talking while it works.** `[me]` / `[them]` lines must keep appearing. If
the transcript freezes until the answer lands, the lane is blocking the event
loop and capture is stopping — that is a failure even though the answer arrives.

---

## 4. The Bash gate

If the deep lane decides it needs a shell, it must stop and ask:

```
[deep] !! Bash wants to run:
    <command>
[deep] allow? (y/N)
```

**Pass:** anything other than `y` denies, and the session continues rather than
dying. Deny is the default because the transcript contains the *other* party's
speech, so a command can be shaped by input you do not control. This is why
`Bash` is absent from the auto-approved tool list.

---

## 5. Shutdown

`Ctrl-C`. Both capture tasks and any in-flight lane run are cancelled together,
and the deep-lane session disconnects.

To stop a backgrounded run, kill the PID you started:

```bash
./run.sh & echo $! > /tmp/bird_brain.pid
kill "$(cat /tmp/bird_brain.pid)"
```

**Do not** use `pkill -f src.main`. `-f` matches every process whose full argv
contains that string, and `python -m src.main` is a common enough entrypoint
that unrelated projects on the same machine answer to it — the pattern reaches
past bird_brain and kills them too.
