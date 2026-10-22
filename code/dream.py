#!/usr/bin/env python3
"""Dreaming: what happened, kept as knowledge.

Each night everything since the last dream is gathered — what was said in every
conversation, terminal and Signal alike; what was committed in every repo; how
the scheduled jobs went. Then three steps:

1. **Which subjects did it touch?** A subject is something the user would ask
   "where does it stand" about: a project, a part of the agent, a person, a
   recurring thing. Never a sub-point — a finer grain splits one thing into
   confetti, which is what an earlier setup's first backfill did.
2. **Rewrite each one.** A fresh session per subject reads that subject's folder,
   the day's material, and what recall finds about it across *all* history, and
   brings the folder up to date: small notes, one fact, decision or lesson each,
   and a short summary of where the subject stands. Knowledge, not a log:
   newer wins, and what is superseded is deleted. Git keeps the history.
3. **Report.** Only what needs the user: rules they might want changed, judging
   by where they corrected the agent, and contradictions only they can settle.
   Most nights, nothing.

Notes are ~200 words so each is one recall passage and arrives whole. The index
— one line per subject — is built here from each summary's first lines, and
loads into every session through the agent's `CLAUDE.md`.

**It can write nowhere else.** Each session runs in `dontAsk` mode with writes
allowed only inside its own subject's folder, Bash denied. Memory, status and
rules are what agents act on, and a bad dream must not change behaviour.

The knowledge is the agent's to run, end to end; the user never hears about
subjects.

    python3 dream.py             the status cleanup (status_cleanup.py), then dream over
                                 everything since the last dream, then push
                                 to GitHub if the repos have a copy there
    python3 dream.py --now       don't wait for the Mac to be idle (30 min untouched)
    python3 dream.py --dry       gather the material and list the subjects; write nothing
    python3 dream.py --backfill  find every subject in all history and write each once
    python3 dream.py --replay [D] walk past conversations a few days at a time, as if
                                 the nightly dream had always run (one-off catch-up),
                                 from date D or the oldest conversation
    python3 dream.py --prune     remove from memory what knowledge now holds
    python3 dream.py --sweep A B every concrete detail said between dates A and B,
                                 made sure of in knowledge
    python3 dream.py --source-check  ten random notes checked claim by claim against their sources
    python3 dream.py --review    the weekly knowledge review: stale subjects rewritten, then the
                                 whole folder checked for duplicates and contradictions
"""
import calendar
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home
import recall
import signal_chat

KNOWLEDGE = home.KNOWLEDGE
INDEX = os.path.join(KNOWLEDGE, "index.md")
HOME = os.path.expanduser(os.environ.get("DREAM_HOME") or home.AGENT)   # where its sessions run
RUNS = os.path.join(home.RUNS, "dream")
STATE = os.path.join(RUNS, "state.json")
TRANSCRIPTS = home.TRANSCRIPTS
REPOS = home.REPOS
HISTORY = os.path.join(home.RUNS, "schedule", "history.jsonl")
TO = "direct"                        # the user's direct Signal chat
FIRST = 2 * 86400          # how far back a first dream looks
CLAUDE = home.CLAUDE
MODEL = os.environ.get("DREAM_MODEL") or "claude-opus-5-5"
AT_ONCE = 3                # subjects rewritten in parallel

GRAIN = f"""A subject is something {home.USER} would ask "where does it stand" about:
- a project (one per folder in {home.PROJECTS}, e.g. a house search),
- a part of the agent itself (signal, scheduler, recall, dreaming, the agent as
  a whole),
- a person ({home.USER}, and the people in their life who come up),
- a recurring thing with no folder (e.g. a laptop decision),
- `lessons`: general habits and gotchas that belong to no other subject.
Never a sub-point of one of those: a decision, a document or a bug belongs
inside its subject. Names are short lowercase slugs, like folder names."""

TOUCHED = """Below is the knowledge index, then material from {since} to {until} in
{material} (conversations.md, commits.md, runs.md). Read the material.

{grain}

Existing subjects are in the index. List every subject this material says
something durable about — a fact, decision, change of state, or lesson worth
knowing a month from now. Use an existing subject's name where one fits; propose
a new one only at the grain above. Routine noise (a job ran fine, a typo fixed)
touches nothing.

Reply with only a JSON array, nothing else:
[{{"subject": "house-search", "about": "the house search: what we want, what's live"}}]
An empty array if nothing durable happened.

INDEX:
{index}"""

DISCOVER = """You are finding every subject in {user}'s history, from the first
conversation on, to write knowledge about each. Look at:
- {projects}/* (each folder's STATUS.md, MEMORY.md, CLAUDE.md),
- {agent}/STATUS.md, MEMORY.md and TODO.md,
- the folders of {transcripts} (one per place a conversation happened),
- `{counts}`, how much recall holds per project.

{grain}

Prefer fewer, real subjects over many thin ones. Skip anything with nothing
durable to say. Reply with only a JSON array, nothing else:
[{{"subject": "house-search", "about": "the house search: what we want, what's live"}}]"""

WRITE = """You keep the knowledge about one subject: **{subject}** ({about}).
Its folder is {folder}. {state}

Material:
- {recalled} — what recall finds about this subject across all history, best
  first, each passage dated and sourced. Leads, not proof: follow a lead to its
  file when it matters.
{today}- You may Read and Grep anything under ~: {projects}, {agent}, and the
  transcripts in {transcripts} (JSONL, one message per line).{extra}

Bring the folder up to date. It holds:

1. `summary.md` — where the subject stands now. Exactly this shape:
   ```
   # <Name> — <what it is, one line>
   Last confirmed: <date> · Edited: {today_date} · Related: <other subjects, comma-separated>

   <Where it stands, at most 200 words. Every claim followed by its source and
   date in brackets: (house-search STATUS, 10/13), (trip session 9/29),
   (Signal direct 10/06), (commit 5a0fece).>

   ## Notes
   - `<file>.md` — <its point, one line>
   ```
2. Small notes, `<point-in-a-few-words>.md`, one fact, decision or lesson each:
   ```
   # <Name>: <the point, stated as a claim>
   <one line: why it matters>
   Last confirmed: <date> · Edited: {today_date}

   <At most 200 words. What is true and why; who decided and what was rejected;
   for a lesson, how the problem shows up, in the words someone would use
   describing the symptom. Sources in brackets after each claim.>
   ```

Rules:
- A subject is what {user} means by it, not where its files happen to sit: a
  house search includes the mortgage, even though that is filed under money.
  Read the summaries of the Related subjects in {knowledge}, and their projects,
  so "where does it stand" gets the whole answer here.
- Knowledge, not a log: "the budget is $2M", not "on 9/29 we decided". Never
  write a superseded value, not even to say it is old — the old number next to
  the new one is how a reader picks up the wrong one. Git keeps the history.
- **Whose word wins.** {user}'s own words — a decision, a correction, a fact
  about their life — outrank any agent's summary, account or inference,
  whatever their dates; only a newer word from them, or something observed (a
  file, a command's output, a commit), replaces theirs. Among sources of equal
  standing, newer wins. Mark their words in the citation: ({user}, Signal
  10/17). If an agent's account contradicts their decision and nothing
  observed settles it, keep theirs and add a Disputed line.
- **Last confirmed is when a source last showed the claim holds** — {user}
  saying it, a file or a command's output showing it — never just today.
  Rewording or reorganising a note is an edit: set Edited, and leave Last
  confirmed as it was unless tonight's material shows the fact again. A
  summary's is the date its main claim was last shown.
- Keep a note that is still true, update one that changed, delete one that no
  longer holds (write it empty: the run removes empty notes). Merge
  duplicates. Don't split one point across notes.
- A matter in this subject that has closed — decided against, done, or past —
  settles at once: one outcome note (what it was, what happened, why, when, and
  the path of its full record), plus any lesson that carries over, filed where
  it belongs; delete its other notes. Knowledge holds what is live or reusable.
- Nothing from what an agent should *do*: rules live elsewhere. To-dos live in
  TODO.md files; a note never holds a to-do list.
- If {user} corrected a claim and it isn't settled, keep the note but add a line
  `Disputed: <what they said, date>` under Last confirmed. A `Disputed:` line
  already there that the sources settle: correct the claim and remove the line.
- Keep the specifics: exact numbers, names, dates, addresses, models, prices —
  "the boat's mooring fee is $85 a month", not "a mooring fee". A note that
  says a thing exists without saying what it is answers nothing.
- **A fact someone would ask about on its own gets its own small note**, titled
  with the thing it is about in the words they'd use: "Car: the loan
  payment is $85 a month on the Civic". A summary that lists it among a dozen
  others matches no single question well, so it is never the only home for a
  specific. The summary points at the note.
- Things only ever said in conversation count as much as what was written:
  a decision made in chat and never filed is exactly what these notes are for.
- Where a fact comes from a document — a PDF, a sheet, a file in Drive, a
  saved archive — give its path or location, so it can be opened when the
  detail is needed. Documents themselves are not indexed; the note points.
- Only what a source shows. Unsure? Leave it out.
- Write only inside {folder}.

Reply in one line: what changed, e.g. "3 notes added, 1 updated, summary rewritten"."""

REVIEW = """You are reviewing the whole knowledge folder, {knowledge}, once a week.
Nightly rewrites look at one subject at a time; this looks across all of them.
Read index.md, every summary.md, and the notes.

Fix, in place:
- the same point in two notes, within a subject or across subjects: merge into
  the one where it belongs, delete the other (write it empty: the run removes
  empty notes), fix the summaries' Notes lists.
  The health check found these likely pairs: {twins}
- a note over 200 words: it has grown past one point. Keep its point in it,
  move each other point into its own small note (or the note that already
  holds it), so every note is one point and arrives in one piece. These are
  over: {long}
- a matter that has closed — decided against, done, or past, judged from the
  notes, the conversations and the project's STATUS — settles:
  one outcome note (what it was, what happened, why, when; and the path of the
  full record, e.g. `{projects}/house-search/offers/12-elm-street.md`),
  plus any lesson that carries over to the next such matter, filed with what it
  is about; the rest of its notes are deleted. Knowledge holds what is live or
  reusable; the detail of a closed matter stays in its own record, pointed to.
  Settle it as soon as it closes: a closed matter left as live notes reads as
  live. If it reopens, the record and the conversations are still there to
  rebuild from.
- a note filed under the wrong subject: move it;
- two summaries or notes that contradict each other: keep what the newer source
  says, if the sources settle it; check them;
- a subject too thin to stand alone: fold it into the subject it belongs to;
- a `Disputed:` line the sources now settle: settle it.

{grain}

Do not touch index.md; it is rebuilt after you. Write only inside {knowledge}.
Reply in one line with what you changed, then, only if something needs {user}
themselves — a contradiction between things *they* said, which no source
settles — a second line starting QUESTION: with it, short enough for a phone."""

PRUNE = """You are emptying one short-term memory file into long-term knowledge.
Memory is written during work and loaded into every session, so it must stay
short; what lasts belongs in {knowledge}, by subject (read its index.md).

The file: {file}

For each `##` entry:
1. **Keep it** only if a session needs it before it acts: an address (where
   something lives, an ID, a number, a URL, an account), or a gotcha that bites
   at once, before anyone would think to look it up. Leave a kept entry as it is.
2. **Otherwise move it.** Find the subject it belongs to. Make sure every
   specific in the entry — numbers, names, paths, dates, the reason — is in that
   subject's knowledge: add to the note that covers it, or write a new small
   note in that folder (`# <Subject>: <the point>`, a one-line why, `Last
   confirmed: <the date the entry gives for the fact, else {today}> · Edited:
   {today}`, at most 200 words, sources in brackets — cite this memory file), and add it to the subject's `summary.md` Notes list. Then delete the
   whole entry from the memory file.

In the memory file, only ever delete whole entries — never add, reword or move
text there; a check afterwards puts back any memory file changed another way.
Leave its header alone. Write knowledge only inside {knowledge}.
Reply in one line: entries kept, entries moved, notes written or extended."""

STALE = 14 * 86400         # a summary unedited this long is rewritten by the weekly pass

REPORT = """You are reviewing tonight's dream. Material from {since} to {until} is in
{material}; the knowledge notes changed tonight are listed in {changed}.

Find only what needs {user} themselves:
1. Where they corrected the agent — told it it was wrong, or how to behave — and
   no rule in ~/.claude/CLAUDE.md or {agent}/CLAUDE.md already covers it. Propose
   the rule, worded in a sentence, with its reason as a clause.
2. Two things *they* said that contradict each other, which only they can settle.

Not routine progress, not what the notes now say, not subjects. If nothing,
reply with exactly NOTHING. Otherwise at most six short numbered lines, for a
phone."""


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))


_NOISE = re.compile(r"<system-reminder>.*?</system-reminder>|<task-notification>.*?</task-notification>", re.S)
_OURS = ("You are dreaming:", "You keep the knowledge about", "You are reviewing tonight",
         "Below is the knowledge index", "You are finding every subject",
         "You are reviewing the whole knowledge")


def said(content):
    """The human-readable text of one message, without the harness's asides."""
    if isinstance(content, str):
        text = content
    else:
        text = "\n".join(c.get("text", "") for c in content or [] if c.get("type") == "text")
    return _NOISE.sub("", text).strip()


def conversations(since, until):
    """Every conversation's words in the window, oldest first per file."""
    out = []
    for path in sorted(glob.glob(f"{TRANSCRIPTS}/*/*.jsonl"), key=os.path.getmtime):
        if os.path.getmtime(path) < since:
            continue
        lines = []
        with open(path, errors="ignore") as fh:
            for raw in fh:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if r.get("type") not in ("user", "assistant") or r.get("isSidechain"):
                    continue
                ts = r.get("timestamp", "")
                if not (iso(since) <= ts <= iso(until)):
                    continue
                text = said((r.get("message") or {}).get("content"))
                if text.startswith(_OURS):
                    lines = []                   # a dream does not dream about itself
                    break
                if not text:
                    continue
                who = home.USER if r["type"] == "user" else "Agent"
                lines.append(f"**{who}** ({ts[:16].replace('T', ' ')} UTC): {text}")
        if lines:
            folder = os.path.basename(os.path.dirname(path))
            out.append(f"## {folder} — {os.path.basename(path)[:8]}\n\n" + "\n\n".join(lines))
    return "\n\n".join(out)


def commits(since):
    out = []
    for repo in REPOS:
        path = os.path.expanduser(repo)
        if not os.path.isdir(os.path.join(path, ".git")):
            continue
        log = subprocess.run(["git", "-C", path, "log", f"--since=@{int(since)}", "--stat",
                              "--format=%n### %h %ad%n%B", "--date=format:%Y-%m-%d %H:%M"],
                             capture_output=True, text=True).stdout.strip()
        if log:
            out.append(f"## {repo}\n{log}")
    return "\n\n".join(out)


def runs(since):
    rows = []
    try:
        with open(HISTORY) as fh:
            for raw in fh:
                r = json.loads(raw)
                if r.get("ended", "") >= stamp(since):
                    rows.append(r)
    except (OSError, ValueError):
        pass
    quiet = sum(1 for r in rows if r.get("outcome") == "ok" and not r.get("said"))
    seen = {}                                        # the same word every five minutes, said once
    for r in rows:
        if r.get("outcome") == "ok" and not r.get("said"):
            continue
        key = (r["name"], r["outcome"], r.get("said", "")[:200])
        first, count = seen.get(key, (r["ended"], 0))
        seen[key] = (first, count + 1)
    lines = [f"- {first} {name}: {outcome}" + (f" — {said}" if said else "") + (f" (×{n})" if n > 1 else "")
             for (name, outcome, said), (first, n) in seen.items()]
    return f"{quiet} runs succeeded silently.\n" + "\n".join(lines)


def gather(since, until):
    material = os.path.join(RUNS, time.strftime("%Y-%m-%d-%H%M", time.localtime(until)))
    os.makedirs(material, exist_ok=True)
    for name, text in (("conversations.md", conversations(since, until)),
                       ("commits.md", commits(since)), ("runs.md", runs(since))):
        with open(os.path.join(material, name), "w") as fh:
            fh.write(text or "(nothing)\n")
    return material


def settings(material, name, writable=None):
    """Read anything; write only `writable`. `//` marks an absolute path.

    Bash denied outright: Claude Code lets commands it judges read-only through
    even when Bash is not allowed (the first dream ran ls and cat), and a fence
    should not rest on that judgement."""
    allow = ["Read", "Glob", "Grep"]
    if writable:
        allow += [f"Edit(/{writable}/**)"]       # Edit rules cover Write too
    path = os.path.join(material, f"settings-{name}.json")
    with open(path, "w") as fh:
        json.dump({"permissions": {"allow": allow, "deny": ["Bash", "WebFetch", "WebSearch"]}}, fh)
    return path


def snapshot(folder):
    """Every file under folder, as it is on disk now."""
    snap = {}
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            p = os.path.join(root, n)
            with open(p, "rb") as fh:
                snap[p] = fh.read()
    return snap


KEPT = os.path.join(RUNS, "put-back")   # what a put-back replaced, in case it wasn't the failed run's


def keep(path):
    """Save a file as it is now before a put-back replaces or removes it. A put-back
    can't tell the failed run's edits from someone else's made meanwhile — a chat
    writing during the night — so nothing it replaces is lost. Returns where it went."""
    dest = os.path.join(KEPT, time.strftime("%Y-%m-%d-%H%M%S"), path.lstrip(os.sep))   # its full path, mirrored
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(path, dest)
    return dest


def restore(folder, snap):
    """Put folder back to snap: files it made go, files it changed or removed
    come back. A failed session leaves nothing behind;
    whatever is replaced is kept first. Returns the kept copies."""
    kept = []
    for p in set(snapshot(folder)) - set(snap):
        kept.append(keep(p))
        os.remove(p)
    for p, data in snap.items():
        try:
            with open(p, "rb") as fh:
                if fh.read() == data:
                    continue
            kept.append(keep(p))
        except OSError:
            os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    return kept


def merged(before, now):
    """A memory file put back after a failed emptying: every entry it had, plus any
    entry added since. The run may only delete, so an added entry is someone
    else's — a chat that learned a fact mid-run — and stays."""
    old, new = entries(before)[1], entries(now)[1]
    added = [e for e in new if e not in old]
    return before.rstrip("\n") + "".join("\n\n" + e for e in added) + "\n" if added else before


def claude(prompt, material, name, writable=None, timeout=1800):
    """One fenced session. If it fails or runs out of time, what it may write is
    put back as it was on disk before it started, so no half-done edit stays."""
    cmd = [CLAUDE, "-p", prompt, "--model", MODEL, "--permission-mode", "dontAsk",
           "--settings", settings(material, name, writable), "--add-dir", material,
           "--no-session-persistence"]            # a dream is not a conversation to recall
    if writable:
        cmd += ["--add-dir", writable]
    before = snapshot(writable) if writable else None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=HOME, env=signal_chat.env(),
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        if writable:
            restore(writable, before)
        raise RuntimeError(f"{name}: timed out after {timeout} s; its writes put back")
    if r.returncode:
        if writable:
            restore(writable, before)
        # stderr carries warnings; the cause is usually in stdout
        why = r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}, no output"
        raise RuntimeError(f"{name}: {why[-300:]}; its writes put back")
    return (r.stdout or "").strip()


def subjects_in(reply):
    """The JSON array a session was asked for, however it was wrapped."""
    m = re.search(r"\[.*\]", reply, re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except ValueError:
        return []
    out = []
    for it in items:
        slug = re.sub(r"[^a-z0-9-]", "", str(it.get("subject", "")).lower().replace(" ", "-"))
        if slug and slug not in [o["subject"] for o in out]:
            out.append({"subject": slug, "about": str(it.get("about", ""))[:200]})
    return out


def recalled(subject, about, material):
    """What recall holds on a subject across all history, written out in full."""
    seen, hits = set(), []
    for q in (f"{subject}: {about}", about, subject):
        for h in recall.search(q, k=40, floor=0.5, crowd=20):
            key = (h["path"], h["text"][:80])
            if key not in seen and not h["path"].startswith(KNOWLEDGE):
                seen.add(key)
                hits.append(h)
    hits.sort(key=lambda h: -h["score"])
    path = os.path.join(material, f"recalled-{subject}.md")
    with open(path, "w") as fh:
        for h in hits[:80]:
            where = os.path.relpath(h["path"], os.path.expanduser("~"))
            fh.write(f"## {h['date']} · {h['kind']} · {where}\n\n{h['text']}\n\n")
    return path


def rewrite(item, material, today=None):
    subject, about = item["subject"], item["about"]
    folder = os.path.join(KNOWLEDGE, subject)
    summary = os.path.join(folder, "summary.md")
    news = ""
    if os.path.exists(summary):               # what it is, from itself; the item says what's new
        title = next((l[2:].strip() for l in open(summary, errors="replace") if l.startswith("# ")), "")
        if " — " in title:
            news, about = about, title.split(" — ", 1)[1]
    exists = os.path.isdir(folder)
    os.makedirs(folder, exist_ok=True)
    extra = ""
    project = os.path.join(home.PROJECTS, subject)
    if os.path.isdir(project):
        extra = (f"\n- {project}/ — the project itself: its STATUS.md, MEMORY.md, "
                 f"TODO.md and sessions/ are the written record.")
    else:                                         # the agent's parts, people, lessons
        extra = (f"\n- {home.AGENT}/ (STATUS.md, MEMORY.md, TODO.md, sessions/) and "
                 f"{home.CODE}/ (each script's docstring says what it does and why) are "
                 f"the written record of the agent.")
    prompt = WRITE.format(
        subject=subject, about=about, folder=folder,
        state=("It already has notes: read every one first." if exists and os.listdir(folder)
               else "It is new: start it."),
        recalled=recalled(subject, about, material),
        today=(f"- {today} — what happened since the last dream; why this subject is "
               f"being rewritten tonight" + (f" ({news})" if news else "") + ".\n" if today else ""),
        extra=extra, today_date=time.strftime("%Y-%m-%d"), knowledge=KNOWLEDGE,
        user=home.USER, projects=home.PROJECTS, agent=home.AGENT, transcripts=TRANSCRIPTS)
    try:
        reply = claude(prompt, material, f"write-{subject}", writable=folder)
    except Exception as e:
        if not os.listdir(folder):
            os.rmdir(folder)
        return subject, f"failed: {e}"
    if not os.listdir(folder):
        os.rmdir(folder)                          # nothing durable after all
    return subject, reply.splitlines()[-1] if reply else ""


def build_index():
    """One line per subject, from each summary's own first lines."""
    lines = []
    for folder in sorted(glob.glob(os.path.join(KNOWLEDGE, "*", "summary.md"))):
        subject = os.path.basename(os.path.dirname(folder))
        head = open(folder, errors="replace").read().splitlines()
        title = next((l[2:].strip() for l in head if l.startswith("# ")), subject)
        what = title.split(" — ", 1)[-1]
        confirmed = next((l.split("Last confirmed:")[1].split("·")[0].strip()
                          for l in head if l.startswith("Last confirmed:")), "")
        lines.append(f"- `{subject}/` — {what}" + (f" (confirmed {confirmed})" if confirmed else ""))
    with open(INDEX, "w") as fh:
        fh.write("# Knowledge — what I know, by subject\n\n"
                 "One line per subject: its folder under `knowledge/`, and what it\n"
                 "is. Each folder holds a `summary.md` of where it stands and small notes.\n"
                 "The dream keeps this file; nobody edits it by hand.\n\n" + "\n".join(lines) + "\n")


def commit(summary):
    """Sessions cannot delete (Bash is denied), so a note written empty is a
    deletion, carried out here."""
    rel = os.path.relpath(KNOWLEDGE, home.AGENT)
    for path in glob.glob(os.path.join(KNOWLEDGE, "*", "*.md")):
        if not open(path).read().strip():
            os.remove(path)
    subprocess.run(["git", "-C", home.AGENT, "add", "-A", "--", rel], check=True)
    if subprocess.run(["git", "-C", home.AGENT, "diff", "--cached", "--quiet", "--", rel]).returncode == 0:
        return False
    subprocess.run(["git", "-C", home.AGENT, "commit", "-q", "-m", f"Dream: {summary}", "--", rel], check=True)
    return True


def changed_since(head):
    out = subprocess.run(["git", "-C", home.AGENT, "diff", "--name-only", head, "--",
                          os.path.relpath(KNOWLEDGE, home.AGENT)],
                         capture_output=True, text=True).stdout.split()
    return out


def nightly(dry=False):
    state = load(STATE, {})
    until = time.time()
    since = state.get("last") or until - FIRST
    os.makedirs(KNOWLEDGE, exist_ok=True)
    material = gather(since, until)
    index = open(INDEX).read() if os.path.exists(INDEX) else "(empty)"
    touched = subjects_in(claude(TOUCHED.format(since=stamp(since), until=stamp(until),
                                                material=material, grain=GRAIN, index=index),
                                 material, "touched"))
    if dry:
        print(material)
        print(json.dumps(touched, indent=1))
        return
    head = subprocess.run(["git", "-C", home.AGENT, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    with ThreadPoolExecutor(AT_ONCE) as pool:
        results = list(pool.map(lambda it: rewrite(it, material, os.path.join(material, "conversations.md")),
                                touched))
    failed = [s for s, r in results if r.startswith("failed")]
    try:
        print(prune(material), flush=True)
    except Exception as e:
        print(f"prune failed: {e}", flush=True)       # memory stays as it was; not worth failing the night
    build_index()
    done = [s for s, r in results if not r.startswith("failed")]
    commit(f"{len(done)} subject(s) — {', '.join(done)}" if done else "index only")
    report = "NOTHING"
    changed = changed_since(head)
    with open(os.path.join(material, "changed.txt"), "w") as fh:
        fh.write("\n".join(changed) or "(none)")
    try:
        report = claude(REPORT.format(since=stamp(since), until=stamp(until), material=material,
                                      changed=os.path.join(material, "changed.txt"),
                                      user=home.USER, agent=home.AGENT),
                        material, "report")
    except Exception as e:
        failed.append(f"report ({e})")
    if failed:
        raise SystemExit(f"the dream failed for: {', '.join(failed)}")
    state["last"] = until                            # only once every step has run
    os.makedirs(RUNS, exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(state, fh)
    if report and report != "NOTHING":
        signal_chat.send(TO, f"🌙 **From last night's dream**\n\n{report}")
    print(f"{len(done)} subject(s): " + "; ".join(f"{s}: {r}" for s, r in results))
    print(report)


def push():
    """Push what the night committed, so the GitHub copy holds it by morning.
    Only repos that have one: the copy is optional, and without a remote there is
    nothing to do. Durability otherwise rests on a session saving, and
    the status cleanup and the dream commit with nobody there. Never forced: if GitHub
    has commits this Mac lacks — a session elsewhere pushed — say so and leave it."""
    failed = []
    for repo in REPOS:
        if not subprocess.run(["git", "-C", repo, "remote"], capture_output=True, text=True).stdout.strip():
            continue
        try:
            r = subprocess.run(["git", "-C", repo, "push", "-q"],
                               capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
            if r.returncode and "rejected" in r.stderr:
                failed.append(f"{repo}: GitHub has commits this Mac doesn't; nothing was overwritten")
            elif r.returncode:
                failed.append(f"{repo}: {(r.stderr or r.stdout).strip().splitlines()[-1][-200:]}")
        except subprocess.TimeoutExpired:
            failed.append(f"{repo}: timed out")
    for line in failed:
        print(f"push failed: {line}", flush=True)
    if failed:
        signal_chat.send(TO, "⚠️ **Last night's push to GitHub didn't go through**\n\n" + "\n".join(failed))
    return failed


def memories():
    """The agent's memory, and each project's."""
    return sorted(glob.glob(os.path.join(home.AGENT, "MEMORY.md")) +
                  glob.glob(os.path.join(home.PROJECTS, "*", "MEMORY.md")))


def entries(text):
    """A memory file's header, and its entries: each `##` heading with what
    follows it up to the next."""
    head, *rest = re.split(r"(?m)^(?=## )", text)
    return head.strip(), [e.strip() for e in rest]


def trimmed(before, after):
    """True if after is before with whole entries removed, the header untouched,
    and nothing added, reworded or cut from inside an entry (a
    line-by-line check let part of an entry go)."""
    (hb, eb), (ha, ea) = entries(before), entries(after)
    left = iter(eb)
    return hb == ha and all(e in left for e in ea)


PRUNE_TIMEOUT = 3000       # seconds one memory file's session may take


def prune(material, files=None):
    """Empty each memory file into knowledge, one session per file. In memory,
    deletions only, checked: a file that gained or reworded a line is put back.
    Checked against the file as it was on disk when the prune started, not
    against git: a session may have written a fact and ended without committing,
    and putting back git's copy would lose it."""
    files = files or memories()
    before = {f: open(f).read() for f in files}

    def one(f):
        path = os.path.join(material, f"settings-prune-{abs(hash(f))}.json")
        with open(path, "w") as fh:
            json.dump({"permissions": {"allow": ["Read", "Glob", "Grep", f"Edit(/{f})",
                                                 f"Edit(/{KNOWLEDGE}/**)"],
                                       "deny": ["Bash", "WebFetch", "WebSearch"]}}, fh)
        cmd = [CLAUDE, "-p", PRUNE.format(knowledge=KNOWLEDGE, file=f, today=time.strftime("%Y-%m-%d")),
               "--model", MODEL, "--permission-mode", "dontAsk", "--settings", path,
               "--no-session-persistence", "--add-dir", KNOWLEDGE, "--add-dir", os.path.dirname(f)]
        name = os.path.relpath(f, os.path.expanduser('~'))
        knowledge = snapshot(KNOWLEDGE)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=HOME, env=signal_chat.env(),
                               stdin=subprocess.DEVNULL, timeout=PRUNE_TIMEOUT)
            why = r.returncode and (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}, no output")
        except subprocess.TimeoutExpired:
            why = "timed out"
        if why:                                   # failed partway: memory and knowledge as they were
            with open(f) as fh:
                now = fh.read()
            with open(f, "w") as fh:
                fh.write(merged(before[f], now))  # its deletions undone; entries added since kept
            failed.add(f)
            restore(KNOWLEDGE, knowledge)
            return f"{name}: failed, memory and knowledge put back: {why[-200:]}"
        return f"{name}: {(r.stdout or r.stderr).strip().splitlines()[-1:] or ''}"

    def put_back(f):
        with open(f, "w") as fh:
            fh.write(before[f])

    failed = set()
    said = [one(f) for f in files]                # one at a time: two files can feed one subject
    undone = []
    for f in files:
        if f not in failed and not trimmed(before[f], open(f).read()):   # it added or reworded: not allowed
            kept = keep(f)
            put_back(f)
            undone.append(f"{os.path.relpath(f, os.path.expanduser('~'))} (what it held is kept at {kept})")

    for repo in (home.AGENT, home.PROJECTS):
        mine = [f for f in files if f.startswith(repo + os.sep)]
        changed = [f for f in mine if subprocess.run(
            ["git", "-C", repo, "diff", "--quiet", "--", f]).returncode]
        if changed:
            subprocess.run(["git", "-C", repo, "commit", "-q", "-m",
                            "Dream: memory emptied into knowledge", "--"] + changed)
    build_index()
    commit("what memory held, moved into knowledge")
    return "\n".join(said) + (f"\nput back (changed, not just trimmed): {', '.join(undone)}" if undone else "")


def review():
    """Stale subjects rewritten from all history, then one look across everything."""
    material = os.path.join(RUNS, "review-" + time.strftime("%Y-%m-%d-%H%M"))
    os.makedirs(material, exist_ok=True)
    stale = []
    for summary in glob.glob(os.path.join(KNOWLEDGE, "*", "summary.md")):
        head = open(summary, errors="replace").read().splitlines()
        title = next((l[2:].strip() for l in head if l.startswith("# ")), "")
        line = next((l for l in head if l.startswith("Last confirmed:")), "")
        m = re.search(r"Edited: (\d{4}-\d{2}-\d{2})", line) or re.search(r"Last confirmed: (\d{4}-\d{2}-\d{2})", line)
        try:                                     # stale means not looked at, so the edit date
            age = time.time() - time.mktime(time.strptime(m.group(1) if m else "", "%Y-%m-%d"))
        except ValueError:
            age = STALE
        if age >= STALE:
            stale.append({"subject": os.path.basename(os.path.dirname(summary)),
                          "about": title.split(" — ", 1)[-1]})
    with ThreadPoolExecutor(AT_ONCE) as pool:
        results = list(pool.map(lambda it: rewrite(it, material), stale))
    import knowledge_health
    h = knowledge_health.health()
    rel = lambda p: os.path.relpath(p, KNOWLEDGE)
    twins = "; ".join(f"{rel(a)} ~ {rel(b)}" for a, b in h["twins"]) or "none"
    long_ = ", ".join(rel(p) for p in h["long"]) or "none"
    reply = claude(REVIEW.format(knowledge=KNOWLEDGE, grain=GRAIN, twins=twins, long=long_,
                               user=home.USER, projects=home.PROJECTS), material, "review",
                   writable=KNOWLEDGE, timeout=3000)
    build_index()
    commit(f"knowledge review — {len(stale)} stale subject(s) rewritten; {reply.splitlines()[0][:80]}")
    question = next((l[9:].strip() for l in reply.splitlines() if l.startswith("QUESTION:")), "")
    if question:
        signal_chat.send(TO, f"🌙 **From the weekly knowledge review**\n\n{question}")
    print("; ".join(f"{s}: {r}" for s, r in results))
    print(reply)


SWEEP = """You are making sure no concrete detail said in these conversations is lost.
The conversations are in {material}/conversations.md ({since} to {until}). The
knowledge is in {knowledge}: read index.md, then Grep it as you go.

Go through the conversations and list every concrete detail someone might ask
about later: names of people, companies, products and places; numbers, prices,
amounts, dates, deadlines; IDs, tracking numbers, addresses, file locations;
what was chosen, bought, sold, booked, cancelled, recommended or decided.

For each, Grep {knowledge} for it. If knowledge already holds it, move on. If
not, add it where it belongs:
- to the note that covers it, as a sentence with its source in brackets, or
- as a new small note in the subject's folder: `# <Subject>: <the fact, stated
  plainly in the words someone would ask with>`, a one-line why, `Last
  confirmed: <the date it was said> · Edited: {today}`, at most 200 words,
  sources in brackets; and list it in
  the subject's summary.md Notes.

Only what is still true: if knowledge, or a later message in these
conversations, shows a detail was superseded, leave it out. Never write a
superseded value. Skip the trivial (a typo fixed, a command run) and anything
that only mattered in the moment. Write only inside {knowledge}.

Reply in one line: details found, already known, added (and to which notes)."""

SOURCE_CHECK = """You are checking whether knowledge is right. Below are knowledge notes
picked at random. For each, check every claim against the source it cites —
the file, the session log, the commit (git -C is not available; read the files),
or the conversation transcript in {transcripts} (Grep it). Sources are in
brackets after each claim.

For each claim: right (the source says it), wrong (the source says otherwise),
unsupported (no source, or the source doesn't say it), or superseded (a newer
source says otherwise).

Where a claim is wrong or superseded, add under the note's `Last confirmed`
line: `Disputed: <what the source says instead, and where>`. Change nothing else.

Reply with only JSON:
{{"notes": [{{"note": "<path>", "claims": <n>, "right": <n>, "wrong": <n>,
  "unsupported": <n>, "superseded": <n>, "worst": "<the worst error, a few words>"}}]}}

NOTES:
{notes}"""

SOURCE_ALERT = 0.10        # above this share wrong, the dream itself needs looking at
LOCK = os.path.join(RUNS, "lock")
IDLE = 30 * 60             # untouched this long, the Mac is nobody's but the dream's


def idle():
    """Seconds since anyone touched the keyboard or mouse."""
    out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True).stdout
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else IDLE
CHUNK = 400_000            # characters of conversation a replay window may hold


def locked():
    """One dream at a time: the nightly run, the review and a replay all rewrite
    the same folders. A second one waits for the first."""
    import fcntl
    os.makedirs(RUNS, exist_ok=True)
    fh = open(LOCK, "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def first_said():
    """When the oldest conversation began: the earliest first timestamp of any
    transcript. A file's mtime is when it last changed, not when it began."""
    first = time.time()
    for path in glob.glob(f"{TRANSCRIPTS}/*/*.jsonl"):
        with open(path, errors="ignore") as fh:
            for raw in fh:
                try:
                    ts = json.loads(raw).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if ts:                                   # ISO, UTC, like iso() writes
                    try:
                        first = min(first, calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")))
                    except ValueError:
                        pass
                    break
    return first


def replay(start=None):
    """The nightly path over history, so what was only ever said gets kept.

    The backfill wrote each subject from what recall finds about it, which
    never surfaces a passing chat — a car payment mentioned once. Nightly
    dreams read whole days. This reads the past the same way: windows of a few
    days, oldest first, each as large as fits, its touched subjects rewritten.
    It starts at `start` (YYYY-MM-DD), or at the oldest conversation."""
    t0 = time.mktime(time.strptime(start, "%Y-%m-%d")) if start else first_said()
    end = load(STATE, {}).get("last") or time.time()
    day, since, done = 86400, t0, 0
    while since < end:
        until = since
        while until < end:                     # grow the window a day at a time
            nxt = min(until + day, end)
            if until > since and len(conversations(since, nxt)) > CHUNK:
                break
            until = nxt
        material = os.path.join(RUNS, "replay-" + time.strftime("%Y-%m-%d", time.localtime(since)))
        os.makedirs(material, exist_ok=True)
        for name, text in (("conversations.md", conversations(since, until)),
                           ("commits.md", commits(since)), ("runs.md", "(not replayed)\n")):
            with open(os.path.join(material, name), "w") as fh:
                fh.write(text or "(nothing)\n")
        index = open(INDEX).read() if os.path.exists(INDEX) else "(empty)"
        try:
            touched = subjects_in(claude(TOUCHED.format(since=stamp(since), until=stamp(until),
                                                        material=material, grain=GRAIN, index=index),
                                         material, "touched"))
        except Exception as e:
            print(f"{stamp(since)}: touched failed: {e}", flush=True)
            touched = []
        with ThreadPoolExecutor(AT_ONCE) as pool:
            results = list(pool.map(lambda it: rewrite(it, material, os.path.join(material, "conversations.md")),
                                    touched))
        build_index()
        commit(f"replay {stamp(since)[:10]} to {stamp(until)[:10]} — {', '.join(s for s, _ in results) or 'nothing'}")
        print(f"{stamp(since)} → {stamp(until)}: " + "; ".join(f"{s}: {r[:80]}" for s, r in results), flush=True)
        since, done = until, done + 1
    print(f"{done} windows replayed")


def sweep(start, end):
    """Every concrete detail said between start and end, made sure of in
    knowledge. A rewrite searches by subject and keeps the gist; a detail said
    once in passing never surfaces that way."""
    t0 = time.mktime(time.strptime(start, "%Y-%m-%d"))
    t1 = time.mktime(time.strptime(end, "%Y-%m-%d"))
    since = t0
    while since < t1:
        until = since
        while until < t1:
            nxt = min(until + 86400, t1)
            if until > since and len(conversations(since, nxt)) > CHUNK:
                break
            until = nxt
        material = os.path.join(RUNS, "sweep-" + time.strftime("%Y-%m-%d", time.localtime(since)))
        os.makedirs(material, exist_ok=True)
        with open(os.path.join(material, "conversations.md"), "w") as fh:
            fh.write(conversations(since, until) or "(nothing)\n")
        reply = claude(SWEEP.format(material=material, since=stamp(since), until=stamp(until),
                                    knowledge=KNOWLEDGE, today=time.strftime("%Y-%m-%d")),
                       material, "sweep", writable=KNOWLEDGE, timeout=3000)
        build_index()
        commit(f"fact sweep {stamp(since)[:10]} to {stamp(until)[:10]}")
        print(f"{stamp(since)} → {stamp(until)}: {reply.splitlines()[-1] if reply else ''}", flush=True)
        since = until


def source_check(n=10):
    """Is knowledge right? n random notes checked claim by claim against their
    sources; errors marked disputed, and their subjects rewritten from sources at
    once; the rate logged. Nothing goes to the user: there is nothing for them to
    do. A high rate means the dream writes badly, which is the agent's to fix, so
    it goes on the agent's TODO for its next session."""
    import random
    notes = [p for p in glob.glob(os.path.join(KNOWLEDGE, "*", "*.md")) if not p.endswith("summary.md")]
    pick = random.sample(notes, min(n, len(notes)))
    material = os.path.join(RUNS, "source-check-" + time.strftime("%Y-%m-%d-%H%M"))
    os.makedirs(material, exist_ok=True)
    reply = claude(SOURCE_CHECK.format(notes="\n".join(pick), transcripts=TRANSCRIPTS), material, "source-check", writable=KNOWLEDGE, timeout=3000)
    m = re.search(r"\{.*\}", reply, re.S)
    rows = json.loads(m.group(0))["notes"] if m else []
    total = {k: sum(r.get(k, 0) for r in rows) for k in ("claims", "right", "wrong", "unsupported", "superseded")}
    with open(os.path.join(RUNS, "source-check.jsonl"), "a") as fh:
        fh.write(json.dumps({"at": stamp(time.time()), **total, "notes": rows}) + "\n")
    commit(f"source check: {total['wrong']} wrong, {total['superseded']} superseded of {total['claims']} claims marked disputed")
    bad = total["wrong"] + total["superseded"]
    subjects = sorted({os.path.basename(os.path.dirname(r["note"])) for r in rows
                       if r.get("wrong") or r.get("superseded")})
    if subjects:
        with ThreadPoolExecutor(AT_ONCE) as pool:
            list(pool.map(lambda s: rewrite({"subject": s, "about": s}, material), subjects))
        build_index()
        commit(f"source check: {', '.join(subjects)} rewritten to settle what it disputed")
    if total["claims"] and bad / total["claims"] > SOURCE_ALERT:
        todo = os.path.join(home.AGENT, "TODO.md")
        with open(todo, "a") as fh:
            fh.write(f"\n- **The weekly source check found {bad} of {total['claims']} claims wrong or out of date** "
                     f"({time.strftime('%Y-%m-%d')}, `runs/dream/source-check.jsonl`). Find what the dream gets wrong "
                     f"and fix its instructions.\n")
        subprocess.run(["git", "-C", home.AGENT, "commit", "-q", "-m", "TODO: the source check's error rate is high", "--", todo])
    print(f"{total['claims']} claims in {len(rows)} notes: {total['right']} right, {total['wrong']} wrong, "
          f"{total['superseded']} superseded, {total['unsupported']} unsupported")
    for r in rows:
        if r.get("wrong") or r.get("superseded"):
            print(f"  {os.path.relpath(r['note'], KNOWLEDGE)}: {r.get('worst', '')}")
    return total


def backfill():
    """Every subject in all history, each written once from the whole of it.

    Not day by day: an earlier setup tried replay and got a sparser, churned result,
    when the whole corpus is available at once anyway."""
    until = time.time()
    material = os.path.join(RUNS, "backfill-" + time.strftime("%Y-%m-%d-%H%M"))
    os.makedirs(material, exist_ok=True)
    counts = os.path.join(material, "counts.txt")
    con = recall.db()
    with open(counts, "w") as fh:
        for kind, project, n in con.execute("SELECT kind, project, COUNT(*) FROM passages "
                                            "GROUP BY kind, project ORDER BY 3 DESC"):
            fh.write(f"{n:>6}  {kind:<11} {project}\n")
    con.close()
    items = subjects_in(claude(DISCOVER.format(counts=counts, grain=GRAIN, user=home.USER,
                                               projects=home.PROJECTS, agent=home.AGENT,
                                               transcripts=TRANSCRIPTS), material, "discover"))
    print("subjects: " + ", ".join(i["subject"] for i in items), flush=True)
    with ThreadPoolExecutor(AT_ONCE) as pool:
        results = list(pool.map(lambda it: rewrite(it, material), items))
    build_index()
    done = [s for s, r in results if not r.startswith("failed")]
    commit(f"backfill, {len(done)} subjects from all history")
    state = load(STATE, {})
    state["last"] = until
    with open(STATE, "w") as fh:
        json.dump(state, fh)
    for s, r in results:
        print(f"  {s}: {r}")


if __name__ == "__main__":
    recall.load()
    # The nightly run and the review wait for a quiet Mac. Hardly anyone writes
    # between 2 and 6am, so 03:30 is normally idle; a night the Mac slept, the
    # scheduler runs it late, and it should not start the moment the user opens
    # the laptop. Exiting non-zero hands it back to the
    # scheduler, which tries again in half an hour.
    unattended = not any(a in sys.argv for a in ("--backfill", "--replay", "--prune", "--sweep", "--dry"))
    if unattended and "--now" not in sys.argv and idle() < IDLE:
        raise SystemExit(f"deferred: the Mac was in use {idle() / 60:.0f} min ago; trying again later")
    with locked():
        if "--backfill" in sys.argv:
            backfill()
        elif "--replay" in sys.argv:
            i = sys.argv.index("--replay")
            nxt = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
            replay(nxt if re.fullmatch(r"\d{4}-\d{2}-\d{2}", nxt) else None)
        elif "--sweep" in sys.argv:
            i = sys.argv.index("--sweep")
            sweep(sys.argv[i + 1], sys.argv[i + 2])
        elif "--source-check" in sys.argv or "--audit" in sys.argv:   # --audit: its old name
            source_check()
        elif "--prune" in sys.argv:
            m = os.path.join(RUNS, "prune-" + time.strftime("%Y-%m-%d-%H%M"))
            os.makedirs(m, exist_ok=True)
            print(prune(m))
        elif "--review" in sys.argv or "--tidy" in sys.argv:          # --tidy: its old name
            review()
        else:
            if "--dry" not in sys.argv:
                # The status cleanup first, to the end, so the dream reads current status
                # files. Its failure doesn't stop the dream: the day still has
                # to reach knowledge.
                import status_cleanup
                try:
                    status_cleanup.run()
                except BaseException as e:
                    print(f"status cleanup failed: {e}", flush=True)
            try:
                nightly(dry="--dry" in sys.argv)
            finally:
                if "--dry" not in sys.argv:
                    push()                           # whatever got committed, even if the dream failed
