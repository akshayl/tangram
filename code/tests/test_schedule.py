"""The scheduler, against a scratch table and real (short) jobs.

Run from code/: python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import schedule as s

T = lambda t: time.mktime(time.strptime(t, "%Y-%m-%d %H:%M"))
THU = T("2026-10-15 10:00")                          # a Thursday


class Timing(unittest.TestCase):
    def test_last_slot(self):
        cases = [({"at": ["06:00"]}, "2026-10-15 06:00"),
                 ({"at": ["12:00"]}, "2026-10-14 12:00"),
                 ({"at": ["08:00"], "days": ["mon"]}, "2026-10-12 08:00"),
                 ({"at": ["09:00"], "monthdays": [1]}, "2026-10-01 09:00")]
        for row, want in cases:
            self.assertEqual(s.last_slot(row, THU), T(want), row)

    def test_next_slot(self):
        self.assertEqual(s.next_slot({"at": ["08:00"], "days": ["mon"]}, THU), T("2026-10-19 08:00"))
        self.assertEqual(s.next_slot({"at": ["12:00"]}, THU), T("2026-10-15 12:00"))

    def test_due(self):
        mon = {"at": ["08:00"], "days": ["mon"]}
        self.assertFalse(s.due(mon, T("2026-10-12 08:00"), THU))
        self.assertTrue(s.due(mon, T("2026-10-11 08:00"), THU))
        self.assertTrue(s.due({"once": "2026-10-15 09:30"}, 0, THU))
        self.assertFalse(s.due({"once": "2026-10-15 10:30"}, 0, THU))
        self.assertFalse(s.due({"every_seconds": 1, "paused": True}, 0, THU))

    def test_problems(self):
        self.assertEqual(s.problems({"name": "a", "once": "2026-10-16 08:00", "run": ["true"]}), [])
        bad = s.problems({"name": "b", "at": ["25:00"], "days": ["funday"], "run": ["true"]})
        self.assertEqual(len(bad), 2)
        self.assertTrue(s.problems({"name": "c", "every_seconds": 5}))       # no run or ask


class Serving(unittest.TestCase):
    """A real scheduler, in its own process, over a scratch table. Never in this
    process: a scheduler thread left behind here once outlived its test while
    the module's paths were put back to the real table."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.table, self.runs = f"{d}/schedule.json", f"{d}/runs"
        os.makedirs(self.runs)
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
        self.tmp.cleanup()

    def write(self, rows):
        tmp = self.table + ".tmp"
        json.dump({"tasks": rows}, open(tmp, "w"))
        os.replace(tmp, self.table)

    def rows(self):
        return [t["name"] for t in json.load(open(self.table))["tasks"]]

    def serve(self, rows, state=None):
        self.write(rows)
        json.dump(state or {}, open(f"{self.runs}/state.json", "w"))
        # Its own empty config folder too: a job that fails for good is announced
        # on Signal, and a test must never find the real Signal settings.
        env = dict(os.environ, SCHEDULE_TABLE=self.table, SCHEDULE_RUNS=self.runs,
                   TANGRAM_CONFIG=self.tmp.name)
        self.proc = subprocess.Popen([sys.executable, s.__file__, "serve"], env=env)

    def history(self):
        try:
            return [json.loads(l) for l in open(f"{self.runs}/history.jsonl")]
        except OSError:
            return []

    def log(self):
        try:
            return open(f"{self.runs}/schedule.log").read()
        except OSError:
            return ""

    def wait_for(self, test, limit=15):
        end = time.time() + limit
        while time.time() < end:
            if test():
                return True
            time.sleep(0.2)
        return False

    def past(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 60))

    def test_failure_policies(self):
        d = self.tmp.name
        now = time.time()
        self.serve([
            {"name": "slow", "once": self.past(), "run": ["/bin/sleep", "30"], "timeout": 2},
            {"name": "flaky", "once": self.past(), "retries": 1, "retry_after": 1,
             "run": ["/bin/sh", "-c", f"test -f {d}/ok || (touch {d}/ok; exit 1)"]},
            {"name": "late", "at": [time.strftime("%H:%M", time.localtime(now - 120))],
             "skip_if_late": 30, "run": ["/usr/bin/true"]},
            {"name": "broken", "once": self.past(), "run": ["/bin/sh", "-c", "exit 3"]},
        ], {"late": now - 86400})
        self.assertTrue(self.wait_for(lambda: len(self.history()) >= 5), self.history())
        got = {(h["name"], h["outcome"].split(",")[0]) for h in self.history()}
        for want in [("slow", "timed out at 2s"), ("flaky", "exit 1"), ("flaky", "ok"),
                     ("late", "skipped"), ("broken", "exit 3")]:
            self.assertIn(want, got)
        self.assertIn("broken failed for good", self.log())
        self.assertIn("slow failed for good", self.log())
        self.assertNotIn("flaky failed for good", self.log())
        self.assertEqual(self.rows(), ["late"])                  # one-offs removed

    def test_new_row_runs_as_written(self):
        self.serve([{"name": "idle", "every_seconds": 3600, "run": ["/usr/bin/true"]}], {"idle": time.time()})
        self.assertTrue(self.wait_for(lambda: "scheduler up" in self.log()))
        time.sleep(0.5)
        start = time.time()
        self.write([{"name": "idle", "every_seconds": 3600, "run": ["/usr/bin/true"]},
                    {"name": "instant", "once": "2026-01-01 00:00", "run": ["/usr/bin/true"]}])
        self.assertTrue(self.wait_for(lambda: any(h["name"] == "instant" for h in self.history()), 3))
        self.assertLess(time.time() - start, 3)

    def test_adopts_a_job_left_running(self):
        job = subprocess.Popen(["/bin/sleep", "1"], start_new_session=True)
        threading.Thread(target=job.wait, daemon=True).start()       # reaped, as an orphan would be
        self.serve([{"name": "orphan", "every_seconds": 99999, "run": ["/usr/bin/true"]}],
                   {"orphan": time.time(), "_running": {"orphan": {
                       "pid": job.pid, "started": time.time(), "attempt": 1,
                       "task": {"name": "orphan", "run": ["/bin/sleep", "1"]}}}})
        self.assertTrue(self.wait_for(lambda: any(h["name"] == "orphan" for h in self.history())))
        self.assertTrue(self.history()[0]["outcome"].startswith("unknown"))


if __name__ == "__main__":
    unittest.main()
