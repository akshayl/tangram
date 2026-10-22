#!/usr/bin/env python3
"""The agent's scheduler: one resident process that runs everything on a clock.

The alternative is a launchd entry per periodic job, where adding one means
writing a plist, installing it and remembering it exists. Instead there is one
daemon, and a config table saying what runs how often. Adding a job is a row in
`schedule.json`, in the agent's folder.

Only two other things stay resident, and both because they hold something open:
the Signal listener keeps its connection so a message is answered in seconds,
and the memory server keeps its index in RAM. Everything else is periodic and
belongs here.

**Overdue beats missed.** macOS silently drops an interval job whose turn came
while the laptop was asleep, so a job on fixed times could simply not happen.
A resident loop has no such problem: it notices on wake that a slot went
by and runs the job once — not once per slot missed.

**A new job is not an overdue one.** The first time a name appears it is
recorded and left until its next turn, so adding a row does not fire it.

**A slow job does not block a fast one.** Each runs as its own process and the
scheduler never starts a second copy of the same job while the first is alive,
so a job that takes twelve minutes and one that runs every minute coexist.

**It sleeps until something is due, and no longer.** One kqueue wait covers the
next job's moment, the table changing, and a running job exiting — so a job
starts on its second, a new row takes effect as it is written, and nothing
polls. The timer is macOS's continuous one, which keeps counting while the Mac
sleeps: a moment that passed during sleep fires on wake. (A plain sleep stops
counting with the Mac and wakes as late as the lid was shut.) A five-minute
ceiling on any wait is the net under all of it.

**Restarting it does not kill what it is running.** Each job is its own process
group, recorded with its pid, and a scheduler that comes back adopts what is
still alive instead of starting a second copy. (Without that, a restart takes
every job down with it.)

**Every run is recorded** in `runs/schedule/history.jsonl` — when, how long,
how it ended, what it last said — and `schedule.py history` reads it.

**A job can be told how to fail.** Per row: `"timeout"` in seconds (default an
hour; the whole job is stopped), `"retries"` and `"retry_after"` (default none,
five minutes apart), and `"skip_if_late"` in seconds — a timed run that comes
due later than that, because the Mac slept, is skipped and recorded rather than
run at the wrong time. A job that fails for good says so on Signal, in the chat
the table names as `"alerts"` — the direct chat if it names none.

Nothing watches this process except launchd, which restarts it if it dies. If
it stops for a reason launchd cannot fix, everything periodic stops with it —
that would be equally true of a launchd entry per job, only quieter.

**A row says when, and what.** When is one of:

    "every_seconds": 300                    repeating
    "at": ["06:00", "18:00"]                every day at those times, or only on
      "days": ["mon", "thu"]                  these days of the week
      "monthdays": [1, 15]                    these days of the month
    "once": "2026-10-16 18:00"              one time; the row removes itself after

What is either a command, `"run": [...]` with an optional `"dir"`, or a request
to the agent: `"ask": "Look at my week and send me a summary."` with `"to"`, the
Signal chat it belongs in (`"direct"`, or a group's topic). A request runs as a
fresh session of its own in that chat's folder — same agent, same memory and
recall, but never blocking or cluttering the chat — and its answer is sent to
the chat labelled as scheduled — or, with `"email": "<address>"`, emailed there
from the agent's own inbox (`mail.py`), `"to"` still naming the folder it runs
in. The chat's session is then told what was sent,
so a reply to it lands in context. Write a request to stand on its own: the job
does not see the conversation it was set up in. A reminder is a one-off request.

A one-off is exempt from "a new job waits": it was written to happen at a time,
and if that time went by while the Mac slept, late beats never.

    python3 schedule.py serve     the daemon; what launchd runs
    python3 schedule.py list      the table, and when each last ran
    python3 schedule.py run NAME  run one job now, due or not
    python3 schedule.py history [NAME] [N]   the last N runs, of one job or all
    python3 schedule.py add '{ROW AS JSON}'   checked, then added
    python3 schedule.py remove|pause|resume NAME
    python3 schedule.py check                every row, checked

A row with `"paused": true` stays in the table and does not run.
"""
import json
import math
import os
import select
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home

# Overridable so a test can run a whole scheduler against a scratch table, in a
# process of its own that can never see the real one.
CONFIG = os.environ.get("SCHEDULE_TABLE") or os.path.join(home.AGENT, "schedule.json")
RUNS = os.environ.get("SCHEDULE_RUNS") or os.path.join(home.RUNS, "schedule")
SIGNAL = os.path.join(home.CODE, "signal_chat.py")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
STATE = f"{RUNS}/state.json"
LOG = f"{RUNS}/schedule.log"
HISTORY = f"{RUNS}/history.jsonl"
NET = 300          # the longest it ever sleeps, whatever it is waiting for
TIMEOUT = 3600     # a job's default limit, when its row names none
RETRY_AFTER = 300


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(state):
    os.makedirs(RUNS, exist_ok=True)
    tmp = f"{STATE}.tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE)


def note(line):
    os.makedirs(RUNS, exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def record(entry):
    """One line of history per run."""
    os.makedirs(RUNS, exist_ok=True)
    with open(HISTORY, "a") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def table():
    return load(CONFIG, {})


def tasks():
    return table().get("tasks", [])


def argv(task):
    if "ask" in task:                                # the agent, in a session of its own
        email = ["--email", task["email"]] if task.get("email") else []
        return [sys.executable, SIGNAL, "job", *email, task["to"], task["name"], task["ask"]]
    return [os.path.expanduser(a) for a in task["run"]]


def last_slot(task, now):
    """The most recent slot of a timed row at or before `now`, as epoch seconds,
    honouring its days of the week or month — or 0 if none in the last 31 days.

    Earlier days count, so a job scheduled only at 06:00 is still correctly
    overdue at 05:00 the next morning, and a Monday job on a Wednesday."""
    days, monthdays = task.get("days"), task.get("monthdays")
    local = time.localtime(now)
    for back in range(32):
        day = time.localtime(time.mktime((local.tm_year, local.tm_mon, local.tm_mday - back,
                                          12, 0, 0, 0, 0, -1)))
        if days and DAYS[day.tm_wday] not in days:
            continue
        if monthdays and day.tm_mday not in monthdays:
            continue
        slots = []
        for hhmm in task["at"]:
            hour, minute = (int(part) for part in hhmm.split(":"))
            slots.append(time.mktime((day.tm_year, day.tm_mon, day.tm_mday,
                                      hour, minute, 0, 0, 0, -1)))
        past = [s for s in slots if s <= now]
        if past:
            return max(past)
    return 0


def when_once(task):
    return time.mktime(time.strptime(task["once"], "%Y-%m-%d %H:%M"))


def due(task, last, now):
    if task.get("paused"):
        return False
    if "every_seconds" in task:
        return now - last >= task["every_seconds"]
    if "at" in task:
        return last < last_slot(task, now)
    if "once" in task:
        return now >= when_once(task)
    return False


def slot(task, now):
    """When this run was meant to happen, for judging lateness."""
    if "at" in task:
        return last_slot(task, now)
    if "once" in task:
        return when_once(task)
    return now


def next_slot(task, now):
    """The first slot of a timed row after `now`, or 0 if none in 32 days."""
    days, monthdays = task.get("days"), task.get("monthdays")
    local = time.localtime(now)
    for ahead in range(33):
        day = time.localtime(time.mktime((local.tm_year, local.tm_mon, local.tm_mday + ahead,
                                          12, 0, 0, 0, 0, -1)))
        if days and DAYS[day.tm_wday] not in days:
            continue
        if monthdays and day.tm_mday not in monthdays:
            continue
        later = [time.mktime((day.tm_year, day.tm_mon, day.tm_mday, *map(int, hhmm.split(":")),
                              0, 0, 0, -1)) for hhmm in task["at"]]
        later = [t for t in later if t > now]
        if later:
            return min(later)
    return 0


def next_due(task, last, now):
    """When this row next wants to run, as epoch seconds; `now` if already due."""
    if task.get("paused"):
        return None
    if due(task, last, now):
        return now
    if "every_seconds" in task:
        return last + task["every_seconds"]
    if "at" in task:
        return next_slot(task, now) or None
    if "once" in task:
        return when_once(task)
    return None


def problems(task):
    """What is wrong with a row, in words — empty if nothing."""
    out = []
    if not isinstance(task.get("name"), str) or not task["name"]:
        out.append("no name")
    kinds = [k for k in ("every_seconds", "at", "once") if k in task]
    if len(kinds) != 1:
        out.append("needs exactly one of every_seconds, at, once")
    if "every_seconds" in task and not (isinstance(task["every_seconds"], int) and task["every_seconds"] > 0):
        out.append("every_seconds must be a positive whole number")
    for hhmm in (task["at"] if isinstance(task["at"], list) else ["not a list"]) if "at" in task else []:
        try:
            h, m = map(int, hhmm.split(":"))
            assert 0 <= h < 24 and 0 <= m < 60
        except Exception:
            out.append(f"at: {hhmm!r} is not HH:MM")
    if any(d not in DAYS for d in task.get("days", [])):
        out.append(f"days must be among {', '.join(DAYS)}")
    if any(not (isinstance(d, int) and 1 <= d <= 31) for d in task.get("monthdays", [])):
        out.append("monthdays must be 1 to 31")
    if "once" in task:
        try:
            when_once(task)
        except Exception:
            out.append(f"once: {task['once']!r} is not YYYY-MM-DD HH:MM")
    if ("run" in task) == ("ask" in task):
        out.append("needs exactly one of run, ask")
    if "ask" in task:
        sys.path.insert(0, os.path.dirname(SIGNAL))
        import signal_chat
        if not signal_chat.configured(task.get("to", "")):
            out.append(f"to: {task.get('to')!r} is not a Signal chat")
    if "email" in task and ("ask" not in task or "@" not in str(task["email"])):
        out.append("email: an address, and only on an ask")
    if "run" in task and not (isinstance(task["run"], list) and task["run"]):
        out.append("run must be a list: the command and its arguments")
    return out


def write_table(t):
    tmp = f"{CONFIG}.tmp"
    with open(tmp, "w") as fh:
        json.dump(t, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, CONFIG)


def remove(name):
    """Drop a finished one-off from the table, re-reading it first so a row the
    agent added a moment ago is not lost."""
    t = table()
    t["tasks"] = [x for x in t.get("tasks", []) if x.get("name") != name]
    write_table(t)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start(task, running, state, attempt=1):
    name = task["name"]
    os.makedirs(RUNS, exist_ok=True)
    # Output goes to a file rather than a pipe: a long job writing more than
    # the pipe buffer would otherwise block for ever waiting to be read.
    out = open(f"{RUNS}/{name}.out", "w")
    # Its own process group: a timeout stops the whole job, children included,
    # and restarting the scheduler leaves it running.
    proc = subprocess.Popen(argv(task), stdout=out, stderr=subprocess.STDOUT,
                            cwd=os.path.expanduser(task.get("dir", "~")), start_new_session=True)
    running[name] = {"proc": proc, "out": out, "started": time.time(), "attempt": attempt,
                     "timeout": task.get("timeout", TIMEOUT), "task": task}
    # The row travels with the run: a one-off's row is gone by the time it ends.
    state.setdefault("_running", {})[name] = {"pid": proc.pid, "started": time.time(),
                                              "attempt": attempt, "task": task}
    save(state)


def stop(pid, proc=None):
    """The whole job: politely, then not. With the Popen in hand, its exit is
    seen at once; a dead child we have not reaped still answers `kill 0`."""
    gone = (lambda: proc.poll() is not None) if proc else (lambda: not alive(pid))
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(40):
        if gone():
            break
        time.sleep(0.25)
    try:                                          # the group too: a child can outlive its leader
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def said(name):
    try:
        with open(f"{RUNS}/{name}.out") as fh:
            return " | ".join(fh.read().split("\n"))[-400:].strip(" |")
    except OSError:
        return ""


def alert(name, text):
    """A job that failed for good, said on Signal where the table points."""
    to = table().get("alerts") or "direct"
    note(f"{name} failed for good: {text[:200]}")
    try:
        sys.path.insert(0, os.path.dirname(SIGNAL))
        import signal_chat
        signal_chat.send(to, f"⚠️ **Scheduled job failed: {name.replace('-', ' ')}**\n\n{text}")
    except Exception as e:
        note(f"{name} alert not sent: {e}")


def finished(task, outcome, took, attempt, out, state):
    """Record the run; on failure, retry if the row allows it, else say so."""
    name = task["name"]
    record({"name": name, "ended": time.strftime("%Y-%m-%d %H:%M:%S"), "took": round(took),
            "outcome": outcome, "attempt": attempt, "said": out[-300:]})
    # A silent success is the normal case; anything else is worth a line.
    if outcome != "ok" or out:
        note(f"{name} {outcome} {took:.0f}s {out[:400]}")
    if outcome in ("ok", "unknown"):
        state.get("_retry", {}).pop(name, None)
        return
    if attempt <= task.get("retries", 0):
        state.setdefault("_retry", {})[name] = {"at": time.time() + task.get("retry_after", RETRY_AFTER),
                                                "attempt": attempt + 1, "task": task}
        note(f"{name} will retry, attempt {attempt + 1}")
        return
    state.get("_retry", {}).pop(name, None)
    tries = f" after {attempt} tries" if attempt > 1 else ""
    alert(name, f"{outcome}{tries}, {took / 60:.0f} min. Last output: {out[-300:] or 'nothing'}")


def reap(running, state):
    for name, r in list(running.items()):
        proc = r["proc"]
        took = time.time() - r["started"]
        if proc and proc.poll() is None:
            if took > r["timeout"]:
                stop(proc.pid, proc)
                proc.wait()
                limit = r["timeout"]
                r["outcome"] = f"timed out at {limit // 60} min" if limit >= 120 else f"timed out at {limit}s"
            continue
        if proc is None:                             # adopted after a restart
            if alive(r["pid"]):
                if took > r["timeout"]:
                    stop(r["pid"])
                continue
            outcome = "unknown"                      # it ended while we were away
        else:
            r["out"].close()
            outcome = r.get("outcome") or ("ok" if proc.returncode == 0 else f"exit {proc.returncode}")
        del running[name]
        state.get("_running", {}).pop(name, None)
        finished(r["task"], outcome, took, r["attempt"], said(name), state)
        save(state)


def adopt(running, state):
    """Jobs a previous scheduler started that are still alive: watched, not restarted."""
    for name, r in list(state.get("_running", {}).items()):
        task = r.get("task") or {"name": name}
        if alive(r["pid"]):
            running[name] = {"proc": None, "pid": r["pid"], "started": r["started"],
                             "attempt": r["attempt"], "task": task,
                             "timeout": task.get("timeout", TIMEOUT)}
            note(f"{name} still running from before, pid {r['pid']}; adopted")
        else:
            state["_running"].pop(name)
            record({"name": name, "ended": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "outcome": "unknown — ended while the scheduler was down",
                    "attempt": r["attempt"]})
    save(state)


def serve():
    note("scheduler up")
    running, state = {}, load(STATE, {})
    adopt(running, state)
    watcher = Watcher()
    while True:
        reap(running, state)
        state = load(STATE, {}) | {k: state[k] for k in ("_running", "_retry") if k in state}
        now = time.time()
        # Retries first, from the row as it was — a one-off's is gone from the table.
        for name, retry in list(state.get("_retry", {}).items()):
            if name not in running and now >= retry["at"]:
                state["_retry"].pop(name)
                start(retry["task"], running, state, retry["attempt"])
        for task in tasks():
            name = task["name"]
            if name in running or name in state.get("_retry", {}):
                continue
            try:
                # A job seen for the first time is not overdue, it is new. Without
                # this, adding a row for something on fixed times fires it on the
                # spot — on first install, every fixed-time job at once.
                if name not in state and "once" not in task:
                    state[name] = now
                    save(state)
                    note(f"{name} registered, first run at its next turn")
                    continue
                if not due(task, state.get(name, 0), now):
                    continue
                # Stamped before the run, so a job that dies on every attempt does
                # not retry every twenty seconds for ever.
                state[name] = now
                save(state)
                late = now - slot(task, now)
                if "skip_if_late" in task and late > task["skip_if_late"]:
                    record({"name": name, "ended": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "outcome": f"skipped, {late / 60:.0f} min late", "attempt": 0})
                    note(f"{name} skipped, {late / 60:.0f} min late")
                else:
                    start(task, running, state)
                if "once" in task:
                    remove(name)
                    state.pop(name, None)
                    save(state)
            except Exception as e:                   # one bad row must not stop the rest
                note(f"{name} skipped: {e}")
        watcher.wait(running, state)


# kqueue constants from <sys/event.h>; Python does not name the timer ones.
NOTE_SECONDS, NOTE_ABSOLUTE, NOTE_MACH_CONTINUOUS_TIME = 0x1, 0x8, 0x80


class Watcher:
    """One kqueue for the scheduler's life. Kept, not remade each loop, so a
    change landing between two waits is held for the next one rather than lost:
    a table is written as a temporary file and then swapped in, and waking on
    the first step only to re-watch after the second misses it."""

    def __init__(self):
        self.kq, self.fds = select.kqueue(), {}

    def _watch_files(self):
        changes = []
        for path in (os.path.dirname(CONFIG), CONFIG):
            try:
                ino = os.stat(path).st_ino
            except OSError:
                continue
            fd, seen = self.fds.get(path, (None, None))
            if seen == ino:
                continue
            if fd is not None:                       # the file was replaced: watch the new one
                os.close(fd)
            fd = os.open(path, 0x8000)               # O_EVTONLY: watch without holding it open
            self.fds[path] = (fd, ino)
            changes.append(select.kevent(fd, select.KQ_FILTER_VNODE,
                                         select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                         select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE
                                         | select.KQ_NOTE_RENAME | select.KQ_NOTE_EXTEND))
        return changes

    def wait(self, running, state):
        """Sleep until the next thing worth waking for: a row coming due, a
        retry, a job's time limit, a job exiting, or the table changing."""
        now = time.time()
        moments = [now + NET]
        for task in tasks():
            if task["name"] not in running and task["name"] in state:
                when = next_due(task, state[task["name"]], now)
                if when:
                    moments.append(when)
            elif "once" in task and not task.get("paused"):
                moments.append(when_once(task))
        moments += [r["at"] for r in state.get("_retry", {}).values()]
        moments += [r["started"] + r["timeout"] for r in running.values()]
        until = max(math.ceil(min(moments)), math.floor(now))
        changes = self._watch_files()
        changes.append(select.kevent(1, select.KQ_FILTER_TIMER, select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                     NOTE_SECONDS | NOTE_ABSOLUTE | NOTE_MACH_CONTINUOUS_TIME, until))
        for r in running.values():
            pid = r["proc"].pid if r["proc"] else r["pid"]
            changes.append(select.kevent(pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                         select.KQ_NOTE_EXIT))
        try:
            got = self.kq.control(changes, 16, NET + 5)
        except OSError:                              # a job exited before we watched it
            return
        if any(e.filter == select.KQ_FILTER_VNODE for e in got):
            time.sleep(0.2)                          # let a write in progress finish


def describe(task):
    when = task.get("every_seconds") and f"every {task['every_seconds']}s" \
        or task.get("once") and f"once at {task['once']}" \
        or "at " + ",".join(task.get("at", [])) + \
        "".join(f" on {','.join(map(str, task[k]))}" for k in ("days", "monthdays") if k in task)
    return when


def main():
    args = sys.argv[1:]
    if args[:1] == ["serve"]:
        serve()
    elif args[:1] == ["list"]:
        state = load(STATE, {})
        now = time.time()
        for task in tasks():
            last = state.get(task["name"])
            ago = f"{int(now - last)}s ago" if last else "never"
            flag = "running" if task["name"] in state.get("_running", {}) else \
                "retrying" if task["name"] in state.get("_retry", {}) else \
                "new" if task["name"] not in state and "once" not in task else \
                "due" if due(task, state.get(task["name"], 0), now) else ""
            nxt = next_due(task, state.get(task["name"], now), now)
            when = "paused" if task.get("paused") else \
                time.strftime("%a %d %b %H:%M", time.localtime(nxt)) if nxt else "—"
            print(f"{task['name']:16} {describe(task):30} last {ago:12} next {when:16} {flag}")
    elif args[:1] == ["history"]:
        name = next((a for a in args[1:] if not a.isdigit()), None)
        n = int(next((a for a in args[1:] if a.isdigit()), 20))
        try:
            with open(HISTORY) as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        except OSError:
            rows = []
        for r in [r for r in rows if not name or r["name"] == name][-n:]:
            took = f"{r['took'] // 60}m{r['took'] % 60:02d}s" if "took" in r else ""
            print(f"{r['ended']}  {r['name']:16} {r['outcome']:28} {took:>7}  {r.get('said', '')[:60]}")
    elif args[:1] == ["add"] and len(args) == 2:
        row = json.loads(args[1])
        wrong = problems(row)
        if any(t["name"] == row.get("name") for t in tasks()):
            wrong.append(f"a job called {row['name']!r} already exists")
        if wrong:
            raise SystemExit("not added: " + "; ".join(wrong))
        t = table()
        t.setdefault("tasks", []).append(row)
        write_table(t)
        print(f"added {row['name']}: {describe(row)}")
    elif args[:1] in (["remove"], ["pause"], ["resume"]) and len(args) == 2:
        t = table()
        row = next((x for x in t.get("tasks", []) if x["name"] == args[1]), None)
        if not row:
            raise SystemExit(f"no job {args[1]!r}")
        if args[0] == "remove":
            t["tasks"].remove(row)
        else:
            row.pop("paused", None)
            if args[0] == "pause":
                row["paused"] = True
        write_table(t)
        print(f"{args[0]}d {args[1]}" if args[0] != "pause" else f"paused {args[1]}")
    elif args[:1] == ["check"]:
        bad = 0
        for task in tasks():
            for p in problems(task):
                print(f"{task.get('name')}: {p}")
                bad += 1
        print("all rows sound" if not bad else f"{bad} problem(s)")
        raise SystemExit(1 if bad else 0)
    elif args[:1] == ["run"] and len(args) > 1:
        for task in tasks():
            if task["name"] == args[1]:
                raise SystemExit(subprocess.run(
                    argv(task), cwd=os.path.expanduser(task.get("dir", "~"))).returncode)
        raise SystemExit(f"no job {args[1]!r}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
