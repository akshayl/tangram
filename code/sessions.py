#!/usr/bin/env python3
"""What other sessions are doing: what they committed, and where they are now.

Sessions run in parallel and see nothing of each other. One rebuilt the
scheduler while another was building search, and the second found out by
tripping over the commits an hour later. Git knew the whole time.

Two things, delivered the same way — before every message, wherever the agent
wakes up:

**News.** Commits in the agent's repos since this session last ran, other
sessions' only. Said once: the next turn starts from now.

**Presence.** Another session working in the same project right now, named by
its Signal chat when it is one — so two sessions do not edit the same files
blind.

Both are silent unless something happened. A standing status report at the top
of every message is one everybody learns to skip.

    python3 sessions.py news              what this session has missed
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home

STATE = os.path.join(home.STATE, "sessions")
SEEN = os.path.join(STATE, "seen.json")
REPOS = home.REPOS
QUIET = 8 * 3600        # a session idle longer than this hears only the last few


def _load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(STATE, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1)


# --- news ------------------------------------------------------------------

def news(session, since=None):
    """Commits from other sessions since this one last looked."""
    seen = _load(SEEN, {})
    last = since or seen.get(session)
    now = time.time()
    if last is None:                          # first turn: start the clock, say nothing
        seen[session] = now
        _save(SEEN, seen)
        return []
    last = max(last, now - QUIET)
    mine = own_trailers(session)
    out = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last))
    for repo in REPOS:
        path = os.path.expanduser(repo)
        # One record per commit, ended by \x1e: a trailer value ends in a
        # newline, which split by lines read as a commit with no subject.
        r = subprocess.run(
            ["git", "-C", path, "log", f"--since={stamp}", "--no-merges",
             "--pretty=format:%h\x1f%s\x1f%(trailers:key=Claude-Session,valueonly,separator=%x2C)\x1e"],
            capture_output=True, text=True)
        if r.returncode or not r.stdout.strip():
            continue
        for record in r.stdout.split("\x1e"):
            _, _, rest = record.strip().partition("\x1f")
            subject, _, trailer = rest.partition("\x1f")
            if not subject.strip():
                continue
            if session and (session in trailer or any(t in trailer for t in mine)):
                continue                      # this session's own work
            out.append((os.path.basename(path.rstrip(os.sep)) or path, subject.strip()))
    seen[session] = now
    _save(SEEN, seen)
    return out


def own_trailers(session):
    """The Claude-Session values this session commits under.

    A commit's trailer carries the claude.ai session, not the id the hook is
    given, so comparing the two never matched and a session was told its own
    commits came from somebody else. The harness tells each session which
    trailer to use; its transcript holds that instruction."""
    if not session:
        return set()
    found = set()
    for path in glob.glob(os.path.join(TRANSCRIPTS, "*", f"{session}.jsonl")):
        try:
            text = open(path, errors="ignore").read()
        except OSError:
            continue
        found |= set(re.findall(r"End git commit messages with:.{0,300}?Claude-Session: "
                                r"(https://claude\.ai/code/session_\w+)", text, re.S))
    return found


# --- who else is awake ------------------------------------------------------

TRANSCRIPTS = home.TRANSCRIPTS
LIVE = 10 * 60          # appended to this recently: somebody is working


def live(session=None, within=LIVE):
    """Other sessions working right now, by project: (project, seconds ago, what).

    Live means a message was said in it lately — the last user or assistant
    line's own timestamp, not the file's modification time, because Claude Code
    appends bookkeeping to idle sessions (Signal chats kept warm looked live all
    day). `what` names a Signal chat's session by its chat. It exists to stop
    two sessions editing the same thing blind."""
    now, out = time.time(), {}
    chats = {v: k for k, v in _load(os.path.join(home.RUNS, "signal", "threads.json"), {}).items()}
    for folder in os.listdir(TRANSCRIPTS) if os.path.isdir(TRANSCRIPTS) else []:
        path = os.path.join(TRANSCRIPTS, folder)
        for name in os.listdir(path):
            if not name.endswith(".jsonl") or (session and name.startswith(session)):
                continue
            file = os.path.join(path, name)
            if now - os.path.getmtime(file) > within:
                continue                          # cheap first cut: untouched is idle
            age = now - _last_said(file)
            if age < within:
                where, sid = project_of(folder), name[:-6]
                what = f"the Signal {chats[sid]} chat" if sid in chats else "a session"
                if where not in out or age < out[where][0]:
                    out[where] = (age, what)
    return sorted(((p, a, w) for p, (a, w) in out.items()), key=lambda r: r[1])


def _last_said(file):
    """When the last message in a transcript was said, from its tail."""
    from datetime import datetime
    with open(file, "rb") as fh:
        fh.seek(max(0, os.path.getsize(file) - 400_000))
        lines = fh.read().decode("utf-8", "ignore").splitlines()
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") in ("user", "assistant") and r.get("timestamp"):
            return datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp()
    return 0


# A transcript's folder is the directory it was opened in, every character
# that is not a letter or digit turned into a dash — so a project's folder name
# is what follows its parent's, whoever's laptop this is.
def _dashed(name):
    return re.sub(r"[^A-Za-z0-9]", "-", name)


def _folder(path):
    return _dashed(os.path.abspath(path))


HOME = _folder(os.path.expanduser("~"))


def project_of(folder):
    """The project a transcript folder belongs to, as the basename of the
    directory a session in it works in: the agent's own home answers to its
    folder name, a project to its own."""
    for parent in (home.AGENT, home.PROJECTS):     # the agent's first, in case it sits inside projects
        prefix = _folder(parent) + "-"
        if folder.startswith(prefix):
            return folder[len(prefix):]
    if folder == _folder(home.AGENT):
        return _dashed(os.path.basename(home.AGENT.rstrip(os.sep)))
    if folder == HOME:
        return "home"
    return folder[len(HOME) + 1:] if folder.startswith(HOME + "-") else folder


def briefing(session, cwd):
    """The whole of it, in as few lines as it can be said."""
    lines = []
    changes = news(session)
    if changes:
        lines.append("Another session has been working since your last turn:")
        seen = set()
        for repo, subject in changes[:6]:
            if subject not in seen:
                seen.add(subject)
                lines.append(f"  · {repo}: {subject}")
        if len(changes) > 6:
            lines.append(f"  · and {len(changes) - 6} more commits")
    # Only sessions working where this one is working. Anything else is a
    # standing status line at the top of every message, which everybody learns
    # to skip; two sessions in the same files is the case that actually bites.
    here = os.path.basename(os.path.abspath(os.path.expanduser(cwd or ".")))
    # Compared as transcript folders spell it: "my_notes" is filed as "my-notes".
    alongside = [(a, w) for p, a, w in live(session) if p == _dashed(here)]
    if alongside:
        age, what = alongside[0]
        lines.append(f"{what[0].upper() + what[1:]} is also working in {here} "
                     f"(last message {int(age // 60)}m ago) — check before editing what it may be holding.")
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["news"]:
        for repo, subject in news(args[1] if len(args) > 1 else "cli"):
            print(f"{repo}: {subject}")
    else:
        print(__doc__)
