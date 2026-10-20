#!/usr/bin/env python3
"""What the agent notices while nobody is looking.

A job that runs unattended fails unattended: a failure in the evening can sit
unseen until someone happens to ask hours later. This is the fix, and it belongs
to the agent rather than inside any one project: one script, every five minutes,
that knows what is supposed to be running and says so when it is not.

What it knows is `watch.json`, in the agent's folder:

    "jobs":   launchd services that should stay up — "label", "what", "broken",
              and "always_on" if a process is expected at all times
    "urls":   endpoints that should answer — "url", "what", "broken"
    "repos":  folders whose work should be in git and pushed — "dir", "what",
              "after_hours" (how long unpushed work may sit; default 8)
    "runs":   logs of something run outside the scheduler — "dir", "glob",
              "what", "every_hours"; a log says "exit N" when its run ends
    "alerts": the Signal chat it speaks in; the direct chat if unset

A job, url or run can carry "after_minutes", how long its fault must last
before it is said.

**It watches what should be alive, not how jobs turned out.** Whether a
scheduled job succeeded is the scheduler's to say — it knows each run's exit and
alerts when one fails for good — so a scheduled job does not belong under
"runs". The watch checks the services, the endpoints and the repos.

**It reports and never fixes.** Deciding what to do about a broken thing is a
conversation; this is the thing that starts it.

**Most of what it sees is not worth a phone buzzing.** A run that failed once
runs again in a few hours; a repo that has not been pushed is the agent's
problem, not the person's. So every check declares how long a fault must last
before they hear about it, and `quiet` checks never reach them at all — they are
the picture the agent is supposed to be holding, and it reads them itself. What
reaches a phone is what has stopped working *for the person*, or has failed long
enough that it plainly is not fixing itself.

**It says each thing once.** At 288 checks a day, a fault that spoke every time
would be worse than silence. State lives in `runs/watch/state.json`: a fault is
announced when it appears and again when it clears, and nothing in between.

**Sleep is not failure.** A laptop shut all day misses its runs, and a missed
fixed-time job runs on wake. So the script notices its own gap — more than
ten minutes since the last check means the machine was asleep — and gives
everything twenty minutes to catch up before calling it late.

    python3 watch.py            check once, speak if anything changed
    python3 watch.py --quiet    check once, print but say nothing
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home
import signal_chat

CONFIG = os.path.join(home.AGENT, "watch.json")
STATE = os.path.join(home.RUNS, "watch", "state.json")
ASLEEP = 600          # a gap this long means the machine was away
GRACE = 1200          # and everything gets this long to catch up
QUIET = None          # a fault the agent holds and the person is never told


def load(path, default):
    try:
        with open(os.path.expanduser(path)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=1)


def job_loaded(label):
    """Is this launchd job loaded, and does it have a process if it should?"""
    uid = os.getuid()
    r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                       capture_output=True, text=True)
    if r.returncode:
        return "not loaded"
    if "state = running" not in r.stdout and "pid = " not in r.stdout:
        return None                       # loaded, waiting for its next time
    return None


def job_running(label):
    uid = os.getuid()
    r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                       capture_output=True, text=True)
    if r.returncode:
        return "not loaded"
    return None if "pid = " in r.stdout else "not running"


def recent(spec, n):
    """Exit codes of the last `n` runs, newest first — one failure is weather."""
    logs = sorted(glob.glob(os.path.expanduser(os.path.join(spec["dir"], spec["glob"]))),
                  key=os.path.getmtime)[-n:]
    out = []
    for log in reversed(logs):
        for line in reversed(open(log, errors="replace").read().splitlines()):
            m = re.search(r"\bexit (-?\d+)", line)
            if m:
                out.append(int(m.group(1)))
                break
    return out


def last_run(spec):
    """The newest log for a scheduled run: (path, age in seconds, exit code)."""
    logs = sorted(glob.glob(os.path.expanduser(os.path.join(spec["dir"], spec["glob"]))),
                  key=os.path.getmtime)
    if not logs:
        return None, None, None
    log = logs[-1]
    code = None
    for line in reversed(open(log, errors="replace").read().splitlines()):
        m = re.search(r"\bexit (-?\d+)", line)
        if m:
            code = int(m.group(1))
            break
    return log, time.time() - os.path.getmtime(log), code


def on_battery():
    """Whether the Mac is running on its battery now."""
    r = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True)
    return "Battery Power" in r.stdout


def check(cfg, late_ok):
    """Every fault right now, as {key: one line for a phone}."""
    faults = {}
    now = time.time()
    # On battery the Mac will sleep, and everything stops until it wakes —
    # which from a phone looks like the agent broke. Said once, after half an
    # hour unplugged, while the Mac is still awake to say it.
    if on_battery():
        faults["power:battery"] = (
            "Your Mac has been on battery for a while. When it sleeps I'll stop "
            "answering until it wakes — plug it in to keep me reachable.", 30)
    for job in cfg.get("jobs", []):
        bad = job_running(job["label"]) if job.get("always_on") else job_loaded(job["label"])
        if bad:
            faults[f"job:{job['label']}"] = (f"{job['what']} {job['broken']}.",
                                             job.get("after_minutes", 15))

    for url in cfg.get("urls", []):
        try:
            urllib.request.urlopen(url["url"], timeout=5)
        except Exception:
            faults[f"url:{url['url']}"] = (url["broken"], url.get("after_minutes", 15))

    for repo in cfg.get("repos", []):
        path = os.path.expanduser(repo["dir"])
        name = repo["what"]
        def git(*args):
            r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else None
        # A folder sitting inside another repo counts only if its files are
        # actually tracked there — an untracked one inside a repo looks fine.
        if not git("ls-files"):
            faults[f"repo:{name}"] = (f"{name} isn't saved anywhere — nothing of it is in git.", QUIET)
            continue
        if not git("remote"):
            continue                      # a copy on GitHub is optional; git here is for undo
        # No fetch: the remote ref moves when we push, so this is free and
        # answers the question that matters — is today's work anywhere else.
        ahead = git("rev-list", "--count", "@{upstream}..HEAD")
        if ahead and ahead != "0":
            # Not while we are working: unpushed is normal for an hour. It
            # becomes news when the work has been sitting there overnight.
            oldest = git("log", "-1", "--format=%ct", "@{upstream}..HEAD")
            if oldest and now - int(oldest) > repo.get("after_hours", 8) * 3600:
                plural = "" if ahead == "1" else "s"
                faults[f"unpushed:{name}"] = (
                    f"{name} has {ahead} change{plural} from more than "
                    f"{repo.get('after_hours', 8)} hours ago that are still only "
                    f"on this laptop.", QUIET)

    for spec in cfg.get("runs", []):
        log, age, code = last_run(spec)
        name = spec["what"]
        if log is None:
            faults[f"run:{name}"] = (f"{name} has never run.", QUIET)
            continue
        if code not in (0, None):
            # One failure is not news: the next run is hours away and usually
            # fixes it. Silence only becomes wrong when it keeps failing.
            runs = recent(spec, 3)
            broken = [c for c in runs if c not in (0, None)]
            quiet = QUIET if len(broken) < 2 or len(runs) < 2 else 0
            count = {2: "twice", 3: "three times"}.get(len(broken), f"{len(broken)} times")
            faults[f"exit:{name}"] = (
                f"{name} failed at {when(log)} — {why(log)}." if quiet is QUIET
                else f"{name} has now failed {count} in a row — {why(log)}.", quiet)
        if age > spec["every_hours"] * 3600 + GRACE and not late_ok:
            faults[f"late:{name}"] = (
                f"{name} hasn't run since {when(log)}, and it should run every "
                f"{spec['every_hours']} hours.", spec.get("after_minutes", 60))
    return faults


# Plain English, because these arrive on a phone and a file name is not news.
# Each pattern is something a run has actually said; the text is what it means.
REASONS = [
    (r"Timeout \d+ms exceeded|TimeoutError|net::ERR_TIMED_OUT", "a website didn't load in time"),
    (r"ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|Temporary failure in name resolution",
     "the laptop had no internet"),
    (r"bot|captcha|403|Forbidden", "a website refused to answer"),
    (r"quota|rate limit|OVER_QUERY_LIMIT", "a paid service hit its limit"),
    (r"credential|invalid_grant|unauthorized|401", "a sign-in had expired"),
    (r"No space left|Disk quota", "the disk is full"),
]


def why(log, lines=60):
    """Why a run failed, in the words of someone who wasn't watching it."""
    text = open(log, errors="replace").read()
    for pattern, plain in REASONS:
        if re.search(pattern, text[-8000:], re.I):
            return plain
    return "it stopped part-way through"


def when(path):
    """The run's own clock time, e.g. "6pm", from a log named like
    `2026-09-17-1801-backup.log`; "last" if the name carries no time."""
    m = re.search(r"-(\d{2})(\d{2})-", os.path.basename(path))
    if not m:
        return "last"
    h, mi = int(m.group(1)), int(m.group(2))
    ampm = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{ampm}" if mi < 8 else f"{h12}:{mi:02d}{ampm}"


# What the end of each kind of fault sounds like.
CLEARED = {
    "power": "Your Mac is plugged in again.",
    "job": "{what} is back.",
    "url": "{what} is back.",
    "repo": "{what} is in git now.",
    "backup": "{what} is backed up on GitHub now.",
    "unpushed": "{what} is pushed.",
    "exit": "{what} worked this time.",
    "late": "{what} has run.",
    "run": "{what} has run.",
}


def recovered(key, cfg):
    """The other half of saying something once: saying it is over."""
    kind, what = key.split(":", 1)
    for job in cfg.get("jobs", []):
        if job["label"] == what:
            what = job["what"]
    for url in cfg.get("urls", []):
        if url["url"] == what:
            what = url.get("what", what)
    return CLEARED.get(kind, "{what} is working again.").format(what=what)


def main():
    cfg = load(CONFIG, {})
    if not cfg:
        raise SystemExit(f"no watch config at {CONFIG}")
    state = load(STATE, {})
    now = time.time()

    # A long gap since the last check means the machine was asleep, and missed
    # fixed-time jobs run on wake — so nothing is late for a while yet.
    gap = now - state.get("checked", now)
    if gap > ASLEEP:
        state["woke"] = now
    late_ok = now - state.get("woke", 0) < GRACE

    faults = check(cfg, late_ok)
    since = {k: state.get("since", {}).get(k, now) for k in faults}
    told = state.get("told", [])

    say, now_told = [], []
    for key, (text, after) in faults.items():
        if after is QUIET:
            continue
        if now - since[key] < after * 60:
            continue                      # give it a chance to right itself
        now_told.append(key)
        if key not in told:
            say.append(text)
    say += [recovered(key, cfg) for key in told if key not in faults]

    state.update(told=now_told, since=since, checked=now)
    save(state)

    for key, (text, after) in faults.items():
        print(f"{'!' if key in now_told else '·'} {text}", flush=True)
    if say and "--quiet" not in sys.argv:
        signal_chat.send(cfg.get("alerts") or "direct", "\n".join(say))
    if not faults:
        print("all well", flush=True)


if __name__ == "__main__":
    main()
