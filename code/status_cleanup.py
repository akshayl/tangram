#!/usr/bin/env python3
"""Status cleanup: each folder's STATUS.md and TODO.md made true again, nightly.

`/save` asks a session to prune its status, but a session that never saves
leaves it as it was, and one that saves often only rewrites the top block: in
the setup Tangram came from, one STATUS grew to 241 lines of decisions months
old before anyone noticed. So each night, before the dream, a fresh session per
folder that saw activity reads the day — every conversation, every commit — and
brings the two files up to date: what is no longer true comes out, facts that
changed are corrected, finished to-dos are deleted. A separate step, run to
completion before the dream starts, so the dream reads current files.

**It can write only those two files.** Each session runs in `dontAsk` mode with
Edit allowed on that folder's `STATUS.md` and `TODO.md`, Bash denied. A stale
line in a `CLAUDE.md` is a defining file's business, so it is reported, never
edited. A session that fails puts both files back as they were on disk when it
started — not git's copy, which would lose anything written and not committed.

    python3 status_cleanup.py                 every folder active since the last run
    python3 status_cleanup.py --only DIR      one folder, whatever its activity
    python3 status_cleanup.py --days N        look back N days instead
    python3 status_cleanup.py --dry           list the folders it would visit; write nothing
"""
import glob
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dream
import home
import signal_chat

RUNS = os.path.join(home.RUNS, "status-cleanup")
STATE = os.path.join(RUNS, "state.json")
TO = dream.TO
AT_ONCE = 3

KEEP = """You are doing the nightly status cleanup in {folder}: making its STATUS.md and TODO.md
true again. Sessions are meant to keep them current and often don't; tonight
you catch what they missed.

What happened from {since} to {until} is in {material}: conversations.md (every
conversation, all folders — work on this folder may have happened in another),
commits.md, runs.md. Grep it for this folder's name and subjects; read what
matters. Read the folder's own files too, and the knowledge index at
{knowledge}/index.md, so you check claims against what is true now.

The rules (from {conventions}):
- STATUS.md says where things stand: what exists, what's decided, what's open.
  It starts with a **Resume Here** block: what was being done, the immediate
  next step, anything half-finished. No section lists next steps; that is
  TODO.md's job alone.
- TODO.md says what's left. Finished items are deleted, not struck through.

Every line in STATUS.md must pass one test: is it true today, and would a
session starting cold need it to act? Edit only STATUS.md and TODO.md in
{folder}:
1. Delete every line that fails the test: a decision since replaced, a gap
   since filled, a "next" that happened, reasoning that only explains how a
   decision was reached. A decided entry is the decision and its reason in a
   clause; how we got there is in the session logs and git, so cut the rest.
2. Correct facts that changed, citing where you saw it in brackets.
3. If a session worked on this folder after the Resume Here block was last
   written and didn't update it, rewrite the block from what happened, and
   end it "(written by the status cleanup from the transcript)".
4. Delete finished to-dos; add none — new work is decided by people.
5. Rewrite a section whole when it changes; never wedge a line into the
   middle of one. Where you keep text, keep its wording.
6. If you changed STATUS.md, set its "Last updated" line to {today}.
If nothing fails the test, change nothing.

Do not edit any other file. If a CLAUDE.md in this folder or one it imports
says something no longer true, report it instead.

Reply in at most four short lines: what you changed, or "nothing stale"; then,
if any, a line beginning "CLAUDE.md:" naming the stale claim and what is true."""


def folders():
    """The agent's folder and every project with a STATUS.md."""
    return sorted(os.path.dirname(p) for p in
                  glob.glob(os.path.join(home.AGENT, "STATUS.md")) +
                  glob.glob(os.path.join(home.PROJECTS, "*", "STATUS.md")))


def active(folder, material, since):
    """Touched since the last run: a commit in it, a conversation started in it,
    or its path mentioned in any conversation."""
    git = subprocess.run(["git", "-C", folder, "log", f"--since=@{int(since)}", "--format=%h",
                          "--", "."], capture_output=True, text=True).stdout.strip()
    if git:
        return True
    started = os.path.join(dream.TRANSCRIPTS, folder.replace("/", "-"))
    if any(os.path.getmtime(p) >= since for p in glob.glob(f"{started}/*.jsonl")):
        return True
    said = open(os.path.join(material, "conversations.md"), errors="replace").read()
    home = os.path.expanduser("~")
    return folder.replace(home, "~") + "/" in said or folder + "/" in said


def clean(folder, material, since, until):
    files = [os.path.join(folder, f) for f in ("STATUS.md", "TODO.md")]
    files = [f for f in files if os.path.exists(f)]
    before = {f: open(f).read() for f in files}
    name = os.path.relpath(folder, os.path.expanduser("~"))
    path = os.path.join(material, f"settings-{name.replace('/', '-')}.json")
    with open(path, "w") as fh:
        json.dump({"permissions": {"allow": ["Read", "Glob", "Grep"] + [f"Edit(/{f})" for f in files],
                                   "deny": ["Bash", "WebFetch", "WebSearch"]}}, fh)
    cmd = [dream.CLAUDE, "-p", KEEP.format(folder=folder, since=dream.stamp(since),
                                           until=dream.stamp(until), material=material,
                                           knowledge=dream.KNOWLEDGE, today=time.strftime("%Y-%m-%d"),
                                           conventions=os.path.join(home.AGENT, "conventions", "conventions.md")),
           "--model", dream.MODEL, "--permission-mode", "dontAsk", "--settings", path,
           "--no-session-persistence", "--add-dir", material, "--add-dir", folder,
           "--add-dir", dream.KNOWLEDGE]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=folder, env=signal_chat.env(),
                           stdin=subprocess.DEVNULL, timeout=1800)
        failed = r.returncode and (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}, no output")[-200:]
    except subprocess.TimeoutExpired:
        failed = "timed out"
    if failed:
        for f, text in before.items():
            with open(f, "w") as fh:
                fh.write(text)
        return name, f"failed, files put back: {failed}"
    changed = [f for f in files if open(f).read() != before[f]]
    if changed:
        subprocess.run(["git", "-C", folder, "commit", "-q", "-m",
                        f"Status cleanup: {name} made current", "--"] + changed)
    return name, r.stdout.strip() or "(no reply)"


def run(only=None, days=None, dry=False):
    state = dream.load(STATE, {})
    until = time.time()
    since = until - days * 86400 if days else state.get("last") or until - dream.FIRST
    os.makedirs(RUNS, exist_ok=True)
    material = os.path.join(RUNS, time.strftime("%Y-%m-%d-%H%M", time.localtime(until)))
    os.makedirs(material, exist_ok=True)
    for name, text in (("conversations.md", dream.conversations(since, until)),
                       ("commits.md", dream.commits(since)), ("runs.md", dream.runs(since))):
        with open(os.path.join(material, name), "w") as fh:
            fh.write(text or "(nothing)\n")
    todo = [os.path.abspath(os.path.expanduser(only))] if only else \
        [f for f in folders() if active(f, material, since)]
    if dry:
        print("\n".join(todo) or "(no folder active)")
        return
    with ThreadPoolExecutor(AT_ONCE) as pool:
        results = list(pool.map(lambda f: clean(f, material, since, until), todo))
    for name, said in results:
        print(f"{name}: {said}", flush=True)
    if only or days:
        return                                       # a hand run: the caller reads the output
    failed = [n for n, s in results if s.startswith("failed")]
    if not failed:
        state["last"] = until
        with open(STATE, "w") as fh:
            json.dump(state, fh)
    changed = [(n, s) for n, s in results if not s.lower().startswith("nothing stale")]
    if changed:
        signal_chat.send(TO, "🧹 **Last night's status cleanup**\n\n" +
                         "\n\n".join(f"**{n}**: {s}" for n, s in changed))
    if failed:
        raise SystemExit(f"status cleanup failed for: {', '.join(failed)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    days = float(args[args.index("--days") + 1]) if "--days" in args else None
    run(only, days, dry="--dry" in args)
