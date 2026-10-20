#!/usr/bin/env python3
"""Signal: a direct chat with the agent, plus a group per project if wanted.

The agent has its own Signal number. A direct message reaches the agent in its
own folder. Each optional group is a topic — one per project — and
`config.json` says which folder answers it. The folder decides the rules: a
project's `CLAUDE.md` imports the agent's, so a message in a project's group
wakes the agent in that project's folder with that project's rules on top.
Adding a topic is a group and a row, not code.

Signal rather than Telegram because it is end-to-end encrypted: Telegram keeps a
readable copy of every bot chat on its servers, which is fine for some things
and not for money or health.

**Each topic keeps one session running.** It takes messages as a stream, so one
sent mid-task is folded into the work under way rather than queued behind it,
and a reply costs no start-up. Everything the agent says is forwarded as it
says it, not only its last word. A session that dies or hangs is resumed by ID
on the next message, so the conversation outlives any one process. Topics
answer in parallel.

signal-cli holds the account and allows one process at a time, so the listener
runs it as a daemon and everything else — a scheduled job announcing what it
found — talks to that daemon over HTTP on localhost.

**A direct message reaches the agent.** `direct.folder` in the config names its
home; each person on the `people` list gets their own conversation with it,
apart from any group's.

Each message gets a 👀 as it lands, because silence looks like nothing arrived.
An edit arrives as a correction and a reaction as what it points at — on a
phone a 👍 is often the whole reply. Nothing is dropped silently: whatever is
not answered is logged with the reason. A reply Signal fails to deliver is
tried once more, then owned up to in the next one that gets through.

Voice messages are transcribed on this computer by whisper.cpp. A photo or file
is saved under `runs/signal/media/` and its path handed to the session, which
opens it the way it opens any other file.

Config and credentials live in the private config folder (`home.secret`), never
a repo. Log and conversation state are in the agent's `runs/signal/`.

    {"account": "+15550100", "daemon": "127.0.0.1:7583",
     "people": {"+15550123": "Sam"}, "direct": {"folder": "~/Sprite"},
     "topics": {"garden": {"group": "<group id>", "folder": "~/projects/garden"}}}

    python3 signal_chat.py listen               run the daemon and answer
    python3 signal_chat.py topics               what is configured
    python3 signal_chat.py send direct:sam hi   say something in a topic
"""
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home

CONFIG = home.secret("signal", "config.json")
RUNS = os.path.join(home.RUNS, "signal")
STATE = os.path.join(RUNS, "threads.json")
SIGNAL_CLI = "/opt/homebrew/bin/signal-cli"
CLAUDE = home.CLAUDE
MODEL = "claude-opus-5"
PATIENCE = 3600      # a net for a stuck run, not a limit on real work
ATTACHMENTS = os.path.expanduser("~/.local/share/signal-cli/attachments")
MEDIA = os.path.join(RUNS, "media")
WHISPER = "/opt/homebrew/bin/whisper-cli"
WHISPER_MODEL = os.path.expanduser("~/.local/share/whisper/ggml-large-v3-turbo-q5_0.bin")
FFMPEG = "/opt/homebrew/bin/ffmpeg"

ASK = """A message arrived in {where} from {who}.

MESSAGE: {text}

This is the same conversation as a terminal session, only asynchronous: work
exactly as you would there. The one difference is the channel — replies go to a
phone. Bold, italic, strikethrough, code and headings come through as Signal
styles; tables do not render."""


def config():
    try:
        with open(CONFIG) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def configured(topic):
    return spec_of(config(), topic) is not None


DIRECT = "direct:"                                   # a topic that is a person, not a group


def spec_of(cfg, topic):
    """A topic's group and folder, or for a direct chat its person and the
    agent's own folder. Plain "direct" is the user's direct chat — the first
    person listed — so a job table or an alert can name it without knowing who
    the user is."""
    if not cfg:
        return None
    if topic in cfg.get("topics", {}):
        return cfg["topics"][topic]
    folder = (cfg.get("direct") or {}).get("folder") or home.AGENT
    people = list(cfg.get("people", {}).items())
    if topic == "direct" and people:
        return {"recipient": people[0][0], "folder": folder}
    if topic.startswith(DIRECT):
        for number, who in people:
            if DIRECT + who.lower() == topic:
                return {"recipient": number, "folder": folder}
    return None


def address(spec):
    """Where a message to this topic goes, in signal-cli's terms."""
    return {"recipient": [spec["recipient"]]} if "recipient" in spec else {"groupId": spec["group"]}


def where(topic):
    return "a direct Signal chat" if topic == "direct" or topic.startswith(DIRECT) else f'the Signal group "{topic}"'


def rpc(method, **params):
    """One call to the daemon. Returns the result, or None if it is not there —
    a sweep that cannot talk has not failed."""
    cfg = config() or {}
    body = json.dumps({"jsonrpc": "2.0", "id": str(uuid.uuid4()),
                       "method": method, "params": params}).encode()
    req = urllib.request.Request(f"http://{cfg.get('daemon')}/api/v1/rpc", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.load(r)
    except Exception as e:
        print(f"  ! signal {method}: {e}", flush=True)
        return None
    if "error" in out:
        print(f"  ! signal {method}: {out['error']}", flush=True)
        return None
    return out.get("result")


# Signal has styles but no markdown: a style is a character range, counted in
# UTF-16 code units. So markdown is converted rather than sent literally.
_MD = re.compile(
    r"```[^\n]*\n(?P<fence>.*?)```"
    r"|`(?P<code>[^`\n]+)`"
    r"|^#{1,6}[ \t]+(?P<head>[^\n]+)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|~~(?P<strike>.+?)~~"
    r"|(?<![\w*])\*(?!\s)(?P<ital>[^*\n]+?)(?<!\s)\*(?!\w)"
    r"|(?<!\w)_(?!\s)(?P<ital2>[^_\n]+?)(?<!\s)_(?!\w)"
    r"|\[(?P<ltext>[^\]\n]+)\]\((?P<url>https?://[^)\s]+)\)",
    re.S | re.M)
_STYLE = {"fence": "MONOSPACE", "code": "MONOSPACE", "head": "BOLD", "bold": "BOLD",
          "strike": "STRIKETHROUGH", "ital": "ITALIC", "ital2": "ITALIC"}


def _u16(text):
    return len(text.encode("utf-16-le")) // 2


def styled(md):
    """Markdown to (plain text, Signal text styles)."""
    out, styles, pos = [], [], 0
    for m in _MD.finditer(md):
        out.append(md[pos:m.start()])
        pos = m.end()
        at = _u16("".join(out))
        kind = m.lastgroup if m.lastgroup != "url" else "link"
        if m.group("ltext"):
            out.append(f"{m.group('ltext')} ({m.group('url')})")
            continue
        inner = m.group(kind)
        if kind in ("fence", "code"):
            text, inner_styles = inner.rstrip("\n"), []
        else:
            text, inner_styles = styled(inner)
        out.append(text)
        styles.append(f"{at}:{_u16(text)}:{_STYLE[kind]}")
        for st in inner_styles:
            start, length, name = st.split(":")
            styles.append(f"{int(start) + at}:{length}:{name}")
    out.append(md[pos:])
    return "".join(out), styles


SAID = {}                                            # timestamp -> text, so a reaction can say what it is to


def delivered(result):
    """Whether signal-cli got it to anyone. It answers per recipient, and a
    failure there still comes back as a result rather than an error."""
    return bool(result) and any(r.get("type") == "SUCCESS" for r in result.get("results") or [])


def send(topic, text, quote=None):
    """Say something in a topic. `quote` is (author, timestamp) to reply to.
    Returns signal-cli's result, or None if it did not get through twice."""
    spec = spec_of(config(), topic)
    if not spec:
        return None
    body, styles = styled(text)
    params = dict(address(spec), message=body)
    if styles:
        params["textStyle"] = styles
    if quote:
        params.update(quoteAuthor=quote[0], quoteTimestamp=quote[1])
    for attempt in range(2):
        result = rpc("send", **params)
        if delivered(result):
            SAID[result.get("timestamp")] = body
            while len(SAID) > 500:
                SAID.pop(next(iter(SAID)))
            return result
        if attempt == 0:
            time.sleep(5)                            # most failures are a blip
    print(f"  ! [{topic}] not delivered: {body[:90]}", flush=True)
    return None


# --- listening -------------------------------------------------------------

_lock = threading.Lock()


def _threads():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _remember(topic, session):
    with _lock:
        st = _threads()
        st[topic] = session
        os.makedirs(RUNS, exist_ok=True)
        # Swapped in whole: a crash mid-write must not cut every chat off from
        # its conversation.
        with open(STATE + ".tmp", "w") as fh:
            json.dump(st, fh, indent=1)
        os.replace(STATE + ".tmp", STATE)


# --- the inbox on disk ------------------------------------------------------
#
# Signal hands a message over once. Until its reply is sent, the only copy is
# ours, so it is written here before anything else happens and removed when the
# turn that answered it ends. A restart finds what was left and answers it.

INBOX = os.path.join(RUNS, "inbox")


def _box(topic):
    return os.path.join(INBOX, topic.replace(":", "_"))


def hold(topic, entry):
    """Write a message or note to the topic's inbox; returns its path."""
    os.makedirs(_box(topic), exist_ok=True)
    path = os.path.join(_box(topic), f"{time.time():.6f}.json")
    with open(path + ".tmp", "w") as fh:
        json.dump(dict(entry, tries=entry.get("tries", 0)), fh, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    return path


def held(path):
    with open(path) as fh:
        return json.load(fh)


def answered(paths):
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def as_item(entry, path, prefix=""):
    """An inbox entry as the worker takes it."""
    if entry["kind"] == "note":
        return ("note", entry["text"], path)
    return ("in", entry["who"], entry["author"], entry["stamp"], prefix + entry["text"],
            entry["files"], path)


TOKEN = home.secret("claude", "token")


def env():
    """The environment claude runs in. With a long-lived token from
    `claude setup-token` on disk it uses that; a laptop login expires under a
    session nobody is watching."""
    e = dict(os.environ)
    try:
        with open(TOKEN) as fh:
            token = fh.read().strip()
        if token:
            e["CLAUDE_CODE_OAUTH_TOKEN"] = token
    except OSError:
        pass
    return e


EXPIRED = ("My login has expired. Run /login in Claude Code on the computer I run on, "
           "then send this again.")


def expired(why):
    """Whether claude failed because its login ran out, which only the person can fix."""
    return "Failed to authenticate" in why or "OAuth" in why


def answer(topic, folder, who, text, ask=None):
    """Wake whoever answers in `folder`, in this topic's conversation.

    `ask` lets another channel borrow this: the session bookkeeping and the
    resume are not Signal's, only the default wording is."""
    thread = _threads().get(topic)
    session = ["--resume", thread] if thread else ["--session-id", str(uuid.uuid4())]
    cmd = [CLAUDE, "-p", (ask or ASK).format(topic=topic, where=where(topic), who=who, text=text),
           "--model", MODEL] + session
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=folder, env=env(),
                           stdin=subprocess.DEVNULL, timeout=PATIENCE)
    except subprocess.TimeoutExpired:
        return f"That ran past {PATIENCE // 60} minutes, so I stopped it."
    if r.returncode:
        # claude -p reports an API failure on stdout, not stderr.
        why = (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")[:200]
        return EXPIRED if expired(why) else f"I could not answer that: {why}"
    if not thread:
        _remember(topic, session[1])
    return (r.stdout or "").strip() or "I had nothing to say, which is a bug."


def transcribe(path):
    """A voice message as text, on this machine — the audio goes nowhere."""
    wav = path + ".wav"
    try:
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", path,
                        "-ar", "16000", "-ac", "1", wav], check=True, timeout=120)
        r = subprocess.run([WHISPER, "-m", WHISPER_MODEL, "-f", wav, "-nt", "-np"],
                           capture_output=True, text=True, timeout=600, check=True)
        return " ".join(r.stdout.split())
    except Exception as e:
        print(f"  ! transcribe {path}: {e}", flush=True)
        return None
    finally:
        if os.path.exists(wav):
            os.remove(wav)


def keep(a):
    """An attachment under a name with its extension, so a session can open it.
    signal-cli files are named by id alone, and `Read` decides what a file is
    from its extension."""
    src = os.path.join(ATTACHMENTS, a["id"])
    if not os.path.exists(src):
        return None
    ext = os.path.splitext(a.get("filename") or "")[1] or \
        mimetypes.guess_extension(a.get("contentType", "") or "") or ".bin"
    os.makedirs(MEDIA, exist_ok=True)
    kept = os.path.join(MEDIA, a["id"] + ext)
    if not os.path.exists(kept):
        shutil.copyfile(src, kept)
    return kept


def read(text, attachments):
    """What the message says, and what it carried that cannot be read yet.

    A photo or file is handed over as a path rather than described: the session
    opens it the same way it would a file in the project."""
    parts, unread = [text] if text else [], []
    for a in attachments:
        kind = a.get("contentType", "")
        path = keep(a)
        if kind.startswith("audio/"):
            heard = path and transcribe(path)
            parts.append(f"[voice message, transcribed on this computer; may be misheard] {heard}"
                         if heard else None)
            if not heard:
                parts.pop()
                unread.append("a voice message I could not transcribe")
        elif path:
            what = {"image": "photo", "video": "video"}.get(kind.split("/")[0], "file")
            parts.append(f"[{what} attached, saved at {path} — open it to see it]")
        else:
            unread.append("an attachment that did not arrive")
    return "\n\n".join(parts), unread


def rules(folder):
    """The rule files a session in `folder` loads — every CLAUDE.md from there up,
    the global one, and whatever they @-import — with their modification times."""
    todo, seen = [os.path.expanduser("~/.claude/CLAUDE.md")], {}
    d = folder
    while True:
        todo.append(os.path.join(d, "CLAUDE.md"))
        if d == os.path.dirname(d):
            break
        d = os.path.dirname(d)
    while todo:
        path = os.path.realpath(todo.pop())
        if path in seen or not os.path.isfile(path):
            continue
        seen[path] = os.path.getmtime(path)
        with open(path) as fh:
            for m in re.finditer(r"^@(\S+)", fh.read(), re.M):
                todo.append(os.path.join(os.path.dirname(path), os.path.expanduser(m.group(1))))
    return seen


class Session:
    """One topic's claude, kept running. Its output lands on `inbox` alongside
    incoming messages, so the worker has one queue to watch.

    A running claude never re-reads its rule files, so a session whose rules
    changed is restarted before it is given the next message."""

    def __init__(self, topic, folder, inbox):
        self.topic, self.folder, self.inbox, self.proc, self.loaded = topic, folder, inbox, None, {}

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def refresh(self):
        """Restart if the rules moved since this session read them. Only between turns."""
        if self.alive() and rules(self.folder) != self.loaded:
            print(f"[{self.topic}] rules changed; restarting the session", flush=True)
            self.kill()

    def warm(self):
        """Start the session before a message needs it, so a restart is paid while
        nobody waits."""
        self.refresh()
        if not self.alive():
            self.start()

    def start(self):
        self.loaded = rules(self.folder)
        thread = _threads().get(self.topic)
        resume = ["--resume", thread] if thread else ["--session-id", str(uuid.uuid4())]
        self.proc = subprocess.Popen(
            [CLAUDE, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
             "--verbose", "--model", MODEL] + resume,
            cwd=self.folder, env=env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1)
        if not thread:
            _remember(self.topic, resume[1])
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                self.inbox.put(("out", json.loads(line)))
            except ValueError:
                pass
        self.inbox.put(("died", proc.wait(), proc))

    def tell(self, text):
        msg = json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        for _ in range(2):                           # once more if it died as we wrote
            if not self.alive():
                self.start()
            try:
                self.proc.stdin.write(msg + "\n")
                self.proc.stdin.flush()
                return
            except (BrokenPipeError, OSError):
                self.proc.wait()
        raise RuntimeError("the session would not take the message")

    def kill(self):
        if self.alive():
            self.proc.kill()
            self.proc.wait()


def worker(topic, spec, inbox):
    to, folder = address(spec), os.path.expanduser(spec["folder"])
    session = Session(topic, folder, inbox)
    busy, quiet = threading.Event(), threading.Event()  # quiet: a note's turn, never shown
    quote, spoke, last, missed, notes = None, False, time.time(), [], []
    turn = []                                        # inbox files the turn under way answers

    def typing():                                    # an indicator lasts 15s
        while True:
            busy.wait()
            if quiet.is_set():
                time.sleep(1)
                continue
            rpc("sendTyping", **to)
            time.sleep(10)
            if not busy.is_set():
                rpc("sendTyping", stop=True, **to)

    def keep_warm():                                 # pay start-up while nobody waits
        while True:
            time.sleep(30)
            if not busy.is_set():
                try:
                    session.warm()
                except Exception as e:
                    print(f"  ! {topic} warm: {e}", flush=True)

    threading.Thread(target=typing, daemon=True).start()
    threading.Thread(target=keep_warm, daemon=True).start()

    def say(text):
        nonlocal quote, spoke
        if missed and send(topic, "One of my earlier replies didn't go through. Here it is:\n\n"
                           + "\n\n".join(missed)):
            missed.clear()
        if send(topic, text, quote=quote):
            print(f"[{topic}] -> {text[:90]}", flush=True)
        else:
            missed.append(text)                      # owned up to in the next one that lands
        quote, spoke = None, True                    # quote only the first reply

    while True:
        try:
            kind, *item = inbox.get(timeout=60)
        except queue.Empty:
            if busy.is_set() and time.time() - last > PATIENCE:
                busy.clear()                         # so "died" doesn't report it twice
                session.kill()
                say(f"Nothing happened for {PATIENCE // 60} minutes, so I stopped it.")
                answered(turn)
                turn.clear()
            continue
        last = time.time()

        if kind == "note":                           # told between turns, never mid-one
            notes.append((item[0], item[1] if len(item) > 1 else None))
        elif kind == "in":
            who, author, stamp, text, attachments, path = (list(item) + [None])[:6]
            text, unread = read(text, attachments)
            if unread:
                send(topic, f"I can't open {', '.join(unread)} here yet.",
                     quote=(author, stamp) if stamp else None)
                print(f"[{topic}] {who}: unread {unread}", flush=True)
            if not text:
                answered([path] if path else [])
                continue
            print(f"[{topic}] {who}: {text[:90]}", flush=True)
            quote = quote or ((author, stamp) if stamp else None)
            if not busy.is_set():
                spoke = False
                session.refresh()
            busy.set()
            if path:
                turn.append(path)
            try:
                session.tell(ASK.format(where=where(topic), who=who, text=text))
            except Exception as e:                   # the worker must outlive this
                say(f"I could not answer that: {str(e)[:200]}")
                busy.clear()
                answered(turn)
                turn.clear()

        elif kind == "out":
            ev = item[0]
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                busy.set()                           # a turn has begun
            elif ev.get("type") == "assistant":
                for c in ev["message"].get("content", []):
                    text = c.get("text", "").strip() if c.get("type") == "text" else ""
                    if text and text != "No response requested." and not quiet.is_set():
                        say(text)
            elif ev.get("type") == "result":
                why = str(ev.get("result") or ev.get("subtype"))[:200]
                if ev.get("is_error") and expired(why):
                    # A running claude keeps the login it started with, so the
                    # next message must find a fresh one.
                    say(EXPIRED)
                    session.kill()
                elif ev.get("is_error") and not spoke and not quiet.is_set():
                    # claude reports an API failure in the result, not stderr.
                    say(f"I could not answer that: {why}")
                busy.clear()
                quiet.clear()
                spoke = False
                answered(turn)                           # answered: the inbox copies can go
                turn.clear()

        elif kind == "died":
            if item[1] is not session.proc:          # one we replaced on purpose
                continue
            print(f"[{topic}] session exited {item[0]}", flush=True)
            # What it was answering goes round once more, flagged; a message that
            # takes the session down twice is set aside rather than looped on.
            for path in turn:
                try:
                    entry = held(path)
                except (OSError, ValueError):
                    continue
                if entry.get("tries", 0) >= 1:
                    if entry["kind"] == "in":
                        say(f"My session ended twice while handling this, so I've set it aside: "
                            f"\"{entry['text'][:200]}\"")
                    answered([path])
                    continue
                entry["tries"] = entry.get("tries", 0) + 1
                with open(path + ".tmp", "w") as fh:
                    json.dump(entry, fh, ensure_ascii=False)
                os.replace(path + ".tmp", path)
                inbox.put(as_item(entry, path, CRASHED))
            turn.clear()
            busy.clear()
            quiet.clear()
            spoke = False

        if notes and not busy.is_set():              # nothing under way: tell it now
            note, path = notes.pop(0)
            session.refresh()
            quiet.set()
            busy.set()
            if path:
                turn.append(path)
            try:
                session.tell(note)
            except Exception as e:                   # its file stays for the next start
                print(f"  ! [{topic}] note: {e}", flush=True)
                turn.clear()
                busy.clear()
                quiet.clear()


def events(daemon):
    """Incoming envelopes from the daemon's event stream, forever."""
    with urllib.request.urlopen(f"http://{daemon}/api/v1/events", timeout=None) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            ev = ev.get("params", ev)                # plain or JSON-RPC wrapped
            ev = ev.get("result", ev)
            if "envelope" in ev:
                yield ev["envelope"]


NOTES = os.path.join(RUNS, "notes")
RESTARTED = ("[This arrived before a restart of the chat and may not have been fully "
             "answered. Check what was already done before redoing anything.] ")
CRASHED = ("[Your session ended while you were handling this. Check what was already "
           "done before redoing anything.] ")

JOB = """This is a job scheduled earlier, running now in its own session with
nobody watching. The request, as it was written down:

{ask}

Do what it asks. Your final reply is sent as-is to {who} in {where} — write it
as that message, not as a report about having done it. If there is genuinely
nothing worth sending, reply with exactly NOTHING."""

NOTE = """[Not a message — a note for you. A scheduled job ("{name}") just sent
this in this chat, so a reply to it may follow:]

{text}

Reply with exactly: No response requested."""


def run_job(topic, name, ask, email=None):
    """A scheduled request: a fresh session in the chat's folder does the work,
    its answer goes to the chat, and the chat's own session is told it went —
    so the chat is never blocked or cluttered by the work, and can still take
    a reply to it."""
    cfg = config()
    if topic == "direct" and (cfg or {}).get("people"):     # its real name, so the chat's session hears of it
        topic = DIRECT + next(iter(cfg["people"].values())).lower()
    spec = spec_of(cfg, topic)
    if not spec:
        raise SystemExit(f"no Signal chat called {topic}")
    who = (cfg.get("people") or {}).get(spec.get("recipient")) or home.USER
    cmd = [CLAUDE, "-p", JOB.format(ask=ask, who=who, where=where(topic)), "--model", MODEL]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env(),
                           cwd=os.path.expanduser(spec["folder"]),
                           stdin=subprocess.DEVNULL, timeout=PATIENCE)
        text = (r.stdout or "").strip()
        if r.returncode:
            why = (r.stderr.strip() or text or f"exit {r.returncode}")[:200]
            text = EXPIRED if expired(why) else f'My scheduled job "{name}" failed: {why}'
    except subprocess.TimeoutExpired:
        text = f'My scheduled job "{name}" ran past {PATIENCE // 60} minutes, so I stopped it.'
    if not text or text == "NOTHING":
        print(f"{name}: nothing to send")
        return
    if email:                                        # by email: a subject says what it is
        import mail
        mail.send(email, name.replace("-", " ").capitalize(), text)
        sent = {"timestamp": int(time.time() * 1000)}
        text = f"⏰ **Scheduled: {name.replace('-', ' ')}** (emailed to {email})\n\n{text}"
    else:
        # Labelled, so it never reads as an answer to whatever was said last.
        text = f"⏰ **Scheduled: {name.replace('-', ' ')}**\n\n{text}"
        sent = send(topic, text)
    if not sent:
        raise SystemExit(f"{name}: could not deliver to {topic}")
    os.makedirs(NOTES, exist_ok=True)
    path = os.path.join(NOTES, f"{time.time():.3f}-{name}.json")
    with open(path + ".tmp", "w") as fh:
        json.dump({"to": topic, "name": name, "text": text, "sent": sent.get("timestamp")}, fh)
    os.replace(path + ".tmp", path)                  # whole or not at all


def collect_notes(inboxes):
    """Tell a chat's session what a scheduled job sent there. A file stays until
    its chat exists to take it."""
    while True:
        try:
            for name in sorted(os.listdir(NOTES)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(NOTES, name)
                with open(path) as fh:
                    note = json.load(fh)
                if note.get("to") in inboxes:
                    SAID[note.get("sent")] = note["text"]    # so a reaction to it can say what it is to
                    text = NOTE.format(name=note["name"], text=note["text"])
                    kept_at = hold(note["to"], {"kind": "note", "text": text})
                    os.remove(path)
                    inboxes[note["to"]].put(("note", text, kept_at))
                else:
                    os.replace(path, path + ".unclaimed")
                    print(f"  ! note from '{note.get('name')}' is for {note.get('to')}, "
                          "which this listener does not serve", flush=True)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"  ! scheduled notes: {e}", flush=True)
        time.sleep(5)


def incoming(env, cfg, by_group):
    """(topic, inbox item) for a message worth answering, or None — logged with
    the reason unless it is only a receipt or a typing indicator."""
    number = env.get("sourceNumber")
    if number == cfg["account"] or not (env.get("dataMessage") or env.get("editMessage")):
        return None                                  # our own echo, a receipt, typing
    edit = env.get("editMessage") or {}
    dm = edit.get("dataMessage") or env.get("dataMessage") or {}
    group = (dm.get("groupInfo") or {}).get("groupId")
    who = cfg["people"].get(number)
    if group:
        topic = by_group.get(group)
    else:
        topic = DIRECT + who.lower() if who else None
    if not who:
        print(f"  · ignored: a message from {number or 'an unknown sender'}, not on the people list",
              flush=True)
        return None
    if not topic:
        print(f"  · ignored: {who} wrote in a {'group' if group else 'direct chat'} "
              "this listener does not serve", flush=True)
        return None

    text, files = dm.get("message") or "", dm.get("attachments") or []
    stamp = edit.get("targetSentTimestamp") or dm.get("timestamp")   # an edit answers to the original
    reaction = dm.get("reaction")
    if reaction:
        to = SAID.get(reaction.get("targetSentTimestamp"))
        what = f'your message: "{to[:300]}"' if to else "an earlier message"
        verb = "took back their" if reaction.get("isRemove") else "reacted"
        text = f"[{verb} {reaction.get('emoji')} on {what}]"
    elif edit:
        text = f"[edited their earlier message; it now says:] {text}"
    elif not (text or files):
        print(f"  · ignored: {who} sent something with no text or file "
              f"({', '.join(k for k in dm if k not in ('timestamp', 'groupInfo')) or 'empty'})",
              flush=True)
        return None
    if not reaction:
        # 👀 the moment it lands: silence is indistinguishable from not having arrived.
        spec = {"group": group} if group else {"recipient": number}
        threading.Thread(target=rpc, args=("sendReaction",), daemon=True,
                         kwargs=dict(address(spec), emoji="👀", targetAuthor=number,
                                     targetTimestamp=stamp)).start()
    return topic, ("in", who, number, None if reaction else stamp, text, files)


def listen():
    cfg = config()
    if not cfg:
        raise SystemExit(f"no Signal config at {CONFIG}")
    daemon = cfg["daemon"]
    proc = subprocess.Popen([SIGNAL_CLI, "-a", cfg["account"], "daemon",
                             "--http", daemon, "--receive-mode", "on-connection",
                             "--no-receive-stdout"])
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://{daemon}/api/v1/check", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    raise SystemExit("signal-cli daemon exited")
                time.sleep(1)

        by_group, inboxes = {}, {}
        topics = dict(cfg.get("topics") or {})       # groups are optional
        for who in cfg.get("people", {}).values():     # every person has a direct chat
            topics[DIRECT + who.lower()] = spec_of(cfg, DIRECT + who.lower())
        for topic, spec in topics.items():
            if "group" in spec:
                by_group[spec["group"]] = topic
            inboxes[topic] = queue.Queue()
            for name in sorted(os.listdir(_box(topic))) if os.path.isdir(_box(topic)) else []:
                if name.endswith(".json"):
                    path = os.path.join(_box(topic), name)
                    inboxes[topic].put(as_item(held(path), path, RESTARTED))
                    print(f"[{topic}] picking up what was waiting before the restart", flush=True)
            threading.Thread(target=worker, args=(topic, spec, inboxes[topic]),
                             daemon=True).start()
        print(f"listening on {cfg['account']}: {', '.join(topics)}", flush=True)
        threading.Thread(target=collect_notes, args=(inboxes,), daemon=True).start()

        while proc.poll() is None:
            try:
                for env in events(daemon):
                    got = incoming(env, cfg, by_group)
                    if got:
                        topic, (_, who, author, stamp, text, files) = got
                        path = hold(topic, {"kind": "in", "who": who, "author": author,
                                            "stamp": stamp, "text": text, "files": files})
                        inboxes[topic].put(("in", who, author, stamp, text, files, path))
            except Exception as e:                   # deaf is worse than wrong
                print(f"  ! event stream: {e}", flush=True)
                time.sleep(5)
        raise SystemExit("signal-cli daemon exited")
    finally:
        proc.terminate()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["listen"]:
        listen()
    elif args[:1] == ["topics"]:
        for name, spec in ((config() or {}).get("topics") or {}).items():
            print(f"{name:10} {spec['folder']}")
    elif args[:1] == ["send"] and len(args) >= 3:
        print(send(args[1], " ".join(args[2:])))
    elif args[:1] == ["job"] and len(args) >= 4:
        email = None
        if args[1] == "--email":
            email, args = args[2], args[2:]
        run_job(args[1], args[2], " ".join(args[3:]), email)
    else:
        print(__doc__)
