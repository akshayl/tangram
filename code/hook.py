#!/usr/bin/env python3
"""Recall, news and updates, before every message.

Claude Code hands this hook the message on stdin; whatever it prints as
`additionalContext` is put in front of the agent for that turn. It asks the
recall service for passages worth remembering and prints them, labelled.

Only where the agent works: its own home, and a project whose `CLAUDE.md`
imports the agent's. Everywhere else Claude Code is plain Claude Code and this
does nothing at all — the hook runs for every session, and a stranger's repo
should not be handed the agent's memories.

Failure is silence: if the service is down, or slow, or finds nothing worth
saying, the agent simply works as it did before. Never block a message.

    hook.py                 the hook itself: an event on stdin
    hook.py check-update    the background half of the update check
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home  # noqa: E402

# RECALL_SERVICE points a test at its own stand-in, never the live index.
SERVICE = os.environ.get("RECALL_SERVICE") or f"http://127.0.0.1:{home.RECALL_PORT}/search"

# What this setup is at, and what Tangram is at now. The setup's code is a copy,
# not a git checkout, so the only way to hear of a newer version is to ask.
VERSION = os.path.join(home.AGENT, "VERSION")
UPSTREAM = (os.environ.get("TANGRAM_VERSION_URL")      # tests: a local file, never the network
            or "https://raw.githubusercontent.com/akshayl/tangram/main/VERSION")
UPDATES = os.path.join(home.STATE, "update")
CHECKED = os.path.join(UPDATES, "checked")    # its mtime: when upstream was last asked
NEWER = os.path.join(UPDATES, "newer")        # the newer version found
TOLD = os.path.join(UPDATES, "told")          # the version last told, so each is said once
EVERY = 4 * 3600


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    prompt = (event.get("prompt") or "").strip()
    if os.environ.get("RECALL_AS_CWD"):         # the memory test, run from a copy: search as the original
        event["cwd"] = os.path.expanduser(os.environ["RECALL_AS_CWD"])
    cwd = event.get("cwd") or os.getcwd()
    if not the_agents(cwd):
        return
    blocks = []

    # News costs a few git calls and is worth it on every message — it is how a
    # session learns that another one has moved the ground.
    try:
        import sessions
        brief = sessions.briefing(event.get("session_id") or "", cwd)
        if brief:
            blocks.append(brief)
    except Exception:
        pass

    try:
        notice = update_notice()
        if notice:
            blocks.append(notice)
    except Exception:
        pass

    try:
        from recall import worth_searching
    except Exception:
        worth_searching = lambda m: len(m.split()) >= 5
    if not worth_searching(prompt) or os.environ.get("RECALL_OFF"):   # "ok"; or the test's level 1
        return say(blocks)
    # The project this session is in gets ranked higher, not exclusively.
    prefer = os.path.basename(cwd)
    previous = os.environ.get("RECALL_PREVIOUS") or last_message(event.get("transcript_path"), prompt)
    query = {"q": prompt[:2000], "prefer": prefer, "previous": previous[:500],
             "cwd": cwd, "session": event.get("session_id") or ""}
    if os.environ.get("RECALL_NO_KNOWLEDGE"):   # the memory test, measuring knowledge's worth
        query["knowledge"] = "0"
    url = f"{SERVICE}?{urllib.parse.urlencode(query)}"
    context = ""
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            context = json.load(r).get("context") or ""
    except Exception:
        pass
    if context:
        blocks.append(context)
    say(blocks)


def the_agents(cwd):
    """Whether a session in `cwd` is the agent's: inside its home, or under a
    `CLAUDE.md` that imports the agent's `CLAUDE.md` with an `@` line.

    The import counts if it resolves to the agent's file, or failing that if it
    ends in the home folder's name and `/CLAUDE.md` — so a project whose import
    was written through a link, or before a move, still counts."""
    agent = os.path.realpath(os.path.expanduser(home.AGENT))
    target = os.path.join(agent, "CLAUDE.md")
    named = os.path.basename(agent) + "/CLAUDE.md"
    d = os.path.realpath(os.path.expanduser(cwd))
    if d == agent or d.startswith(agent + os.sep):
        return True
    while True:
        try:
            text = open(os.path.join(d, "CLAUDE.md"), errors="replace").read()
        except OSError:
            text = ""
        for m in re.finditer(r"^@(\S+)", text, re.M):
            ref = os.path.expanduser(m.group(1))
            ref = ref if os.path.isabs(ref) else os.path.join(d, ref)
            if os.path.realpath(ref) == target or m.group(1).endswith(named):
                return True
        if d == os.path.dirname(d):
            return False
        d = os.path.dirname(d)


# --- updates -----------------------------------------------------------------

def _version(text):
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def _newer(theirs, ours):
    """Numbers compared as numbers, so 1.10 comes after 1.9; a version without
    numbers is newer merely by being different."""
    a, b = _version(theirs), _version(ours)
    return a > b if a and b else bool(theirs) and theirs != ours


def _read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text + "\n")


def update_notice():
    """One line when Tangram has moved on, said once per new version — and, at
    most every four hours, a look at whether it has.

    The look runs in another process because it is a network call, and a reply
    must never wait on one. It leaves its answer in a file read here on a later
    message, so a new version is heard about one message after it is found."""
    ours = _read(VERSION)
    if not ours:
        return ""                             # no version recorded: nothing to compare against
    try:
        due = time.time() - os.path.getmtime(CHECKED) > EVERY
    except OSError:
        due = True
    if due:
        _write(CHECKED, time.strftime("%Y-%m-%d %H:%M:%S"))   # first, so two messages don't both ask
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "check-update"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    theirs = _read(NEWER)
    if not theirs or not _newer(theirs, ours) or theirs == _read(TOLD):
        return ""
    _write(TOLD, theirs)
    return (f"Tangram has a newer version ({theirs}; this setup is at {ours}). Read its "
            f"CHANGELOG.md for what changed since {ours} and propose each change to the "
            f"user, one at a time, at a natural moment.")


def check_update():
    """Ask upstream what Tangram is at, and note it if it is ahead of this setup."""
    try:
        with urllib.request.urlopen(UPSTREAM, timeout=5) as r:
            theirs = r.read(100).decode("utf-8", "ignore").strip()
    except Exception:
        return
    if theirs and _newer(theirs, _read(VERSION)):
        _write(NEWER, theirs)


# --- recall ------------------------------------------------------------------

def last_message(transcript, prompt):
    """The user's message before this one, so a follow-up like "and the blue
    one?" is searched with what it follows. Read from the end of the transcript
    only."""
    if not transcript or not os.path.exists(transcript):
        return ""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(max(0, os.path.getsize(transcript) - 400_000))
            lines = fh.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") != "user" or r.get("isSidechain"):
            continue
        c = (r.get("message") or {}).get("content")
        text = c if isinstance(c, str) else " ".join(
            x.get("text", "") for x in c or [] if isinstance(x, dict) and x.get("type") == "text")
        text = text.strip()
        if text and text != prompt.strip() and not text.startswith(("<", "[", "Caveat:", "Another Claude")):
            return text
    return ""


def say(blocks):
    if blocks:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(blocks)}}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["check-update"]:
        check_update()
    else:
        main()
