"""The Signal listener, with signal-cli and claude replaced by stand-ins.

Run from code/: python3 -m unittest tests/test_signal_chat.py
"""
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import signal_chat as s

CFG = {"account": "+1999", "people": {"+1222": "Sam"}, "direct": {"folder": "/nonexistent/agent"},
       "topics": {"garden": {"group": "G1", "folder": "/nonexistent/garden"}}}
DM = "direct:sam"


def settle():
    threading.Event().wait(0.3)


def until(condition, seconds=5):
    """Wait for what the worker thread does, not for a fixed time: a fixed
    0.3 s made these tests fail now and then on a busy laptop."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        threading.Event().wait(0.05)
    return condition()


class Stub:
    """A session that records what it was told."""
    told = []

    def __init__(self, *a):
        self.proc = "P"

    def warm(self): pass
    def refresh(self): pass
    def kill(self): pass

    def tell(self, text):
        Stub.told.append(text)


REAL_SLEEP = __import__("time").sleep               # put back after a test stubs it out
SINK = tempfile.mkdtemp()                            # where finished tests' threads write


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: getattr(s, k) for k in ("rpc", "config", "Session", "RUNS", "INBOX", "OUTBOX", "STATE", "FRESH")}
        s.RUNS = self.tmp.name
        s.INBOX, s.STATE = os.path.join(s.RUNS, "inbox"), os.path.join(s.RUNS, "threads.json")
        s.FRESH = os.path.join(s.RUNS, "fresh")
        s.OUTBOX = os.path.join(s.RUNS, "outbox")
        s.config = lambda: CFG
        self.fail_sends, self.calls = 0, []

        def rpc(method, **p):
            self.calls.append((method, p))
            if method == "send":
                if self.fail_sends:
                    self.fail_sends -= 1
                    return {"timestamp": 1, "results": [{"type": "NETWORK_FAILURE"}]}
                return {"timestamp": 1000 + len(self.calls), "results": [{"type": "SUCCESS"}]}
            return {}
        s.rpc = rpc
        s.time_sleep, s.SAID = None, {}
        self.saved["HOLD"], s.HOLD = s.HOLD, 0.1        # a held last message goes almost at once
        Stub.told = []

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(s, k, v)
        # Workers are daemon threads that outlive their test; their typing
        # loop would otherwise reach the real rpc, and through it a real daemon.
        s.rpc = lambda method, **p: None
        # Nor may they write to the real runs folder: a send that fails there
        # keeps its reply in the real outbox, for the live listener to send.
        s.RUNS = SINK
        s.INBOX, s.OUTBOX, s.STATE, s.FRESH = (os.path.join(SINK, n) for n in ("inbox", "outbox", "threads.json", "fresh"))
        self.tmp.cleanup()

    def sends(self):
        return [p["message"] for m, p in self.calls if m == "send"]


class Incoming(Base):
    by_group = {"G1": "garden"}

    def env(self, **kw):
        return dict({"sourceNumber": "+1222"}, **kw)

    def test_direct_message_reaches_the_main_agent(self):
        topic, item = s.incoming(self.env(dataMessage={"timestamp": 5, "message": "hi"}), CFG, self.by_group)
        self.assertEqual((topic, item[4]), (DM, "hi"))

    def test_group_message(self):
        topic, _ = s.incoming(self.env(dataMessage={"timestamp": 6, "message": "yo",
                                                    "groupInfo": {"groupId": "G1"}}), CFG, self.by_group)
        self.assertEqual(topic, "garden")

    def test_ignored_kinds(self):
        for env in (self.env(dataMessage={"timestamp": 7, "message": "x", "groupInfo": {"groupId": "G9"}}),
                    {"sourceNumber": "+1555", "dataMessage": {"timestamp": 8, "message": "x"}},
                    self.env(receiptMessage={}),
                    {"sourceNumber": "+1999", "dataMessage": {"message": "x"}},
                    self.env(dataMessage={"timestamp": 9, "sticker": {}})):
            self.assertIsNone(s.incoming(env, CFG, self.by_group))

    def test_edit_answers_the_original(self):
        _, item = s.incoming(self.env(editMessage={"targetSentTimestamp": 5,
                                                   "dataMessage": {"timestamp": 10, "message": "fixed"}}),
                             CFG, self.by_group)
        self.assertEqual(item[3], 5)
        self.assertIn("edited", item[4])

    def test_reaction_says_what_it_is_to(self):
        ts = s.send(DM, "Shall I build it?")["timestamp"]
        _, item = s.incoming(self.env(dataMessage={"timestamp": 11, "reaction": {
            "emoji": "👍", "targetSentTimestamp": ts}}), CFG, self.by_group)
        self.assertIn('👍 on your message: "Shall I build it?"', item[4])
        self.assertIsNone(item[3])                   # a reaction is not quoted


class Sending(Base):
    def setUp(self):
        super().setUp()
        s.time.sleep = lambda x: None
        self.addCleanup(setattr, s.time, "sleep", REAL_SLEEP)

    def test_retried_once_then_given_up(self):
        self.fail_sends = 1
        self.assertTrue(s.send("garden", "a"))
        self.fail_sends = 2
        self.assertIsNone(s.send("garden", "b"))

    def test_direct_address(self):
        self.assertEqual(s.address(s.spec_of(CFG, DM)), {"recipient": ["+1222"]})


class Worker(Base):
    def start(self):
        Stub.told = []                   # shared by every test's worker: start clean
        s.Session = Stub
        self.q = queue.Queue()
        threading.Thread(target=s.worker, args=(DM, s.spec_of(CFG, DM), self.q), daemon=True).start()

    def message(self, text):
        path = s.hold(DM, {"kind": "in", "who": "Sam", "author": "+1222", "stamp": 5,
                           "text": text, "files": []})
        self.q.put(("in", "Sam", "+1222", 5, text, [], path))
        return path

    def say(self, text):
        self.q.put(("out", {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}))

    def test_undelivered_reply_is_owned_up_to(self):
        self.start()
        self.message("hi")
        settle()
        self.fail_sends = 2
        self.say("lost")
        self.say("second")
        for _ in range(30):                          # each failed reply is written to disk first
            if self.sends()[-1:] == ["second"]:
                break
            settle()
        self.assertIn("didn't go through", self.sends()[-2], self.sends())
        self.assertEqual(self.sends()[-1], "second")

    def test_unsent_reply_survives_a_restart(self):
        s.keep_unsent(DM, ["from before the restart"])     # a listener that died with it unsent
        self.start()
        self.message("hi")
        settle()
        self.say("new")
        for _ in range(30):
            if self.sends()[-1:] == ["new"]:
                break
            settle()
        self.assertIn("from before the restart", self.sends()[-2])
        self.assertEqual(s.unsent(DM), [])

    def test_inbox_copy_kept_until_answered(self):
        self.start()
        path = self.message("hi")
        settle()
        self.assertTrue(os.path.exists(path))
        self.q.put(("out", {"type": "result"}))
        settle()
        self.assertFalse(os.path.exists(path))

    def test_crash_retries_once_then_sets_aside(self):
        self.start()
        path = self.message("crashy")
        settle()
        self.q.put(("died", -9, "P"))
        self.assertTrue(until(lambda: any(s.CRASHED.strip()[:30] in t for t in Stub.told)))
        self.assertTrue(os.path.exists(path))
        self.q.put(("died", -9, "P"))
        self.assertTrue(until(lambda: not os.path.exists(path)))
        self.assertTrue(until(lambda: self.sends() and "set it aside" in self.sends()[-1]))

    def test_note_waits_for_the_turn_and_stays_silent(self):
        self.start()
        self.message("hello")
        settle()
        self.q.put(("out", {"type": "system", "subtype": "init"}))
        self.q.put(("note", "NOTE", None))
        settle()
        self.assertNotIn("NOTE", Stub.told)
        self.say("answer")
        self.q.put(("out", {"type": "result"}))
        settle()
        self.assertEqual(Stub.told[-1], "NOTE")
        self.say("chatter during the note")
        self.q.put(("out", {"type": "result"}))
        settle()
        self.assertEqual(len(self.sends()), 1)
        self.assertRegex(self.sends()[0], r"^answer\n\n\(\d+s\)$")   # the turn's last message says how long

    def test_last_message_ends_with_how_long_it_took(self):
        self.start()
        self.message("hi")
        settle()
        self.say("working on it")
        self.say("done")
        self.q.put(("out", {"type": "result"}))
        self.assertTrue(until(lambda: len(self.sends()) == 2))
        self.assertEqual(self.sends()[0], "working on it")
        self.assertRegex(self.sends()[1], r"^done\n\n\(\d+s\)$")

    def test_failure_with_nobody_waiting_stays_off_the_phone(self):
        self.start()
        self.q.put(("out", {"type": "result", "is_error": True, "subtype": "error_during_execution"}))
        settle()
        self.assertEqual(self.sends(), [])

    def test_took(self):
        self.assertEqual((s.took(42.4), s.took(134)), ("42s", "2m 14s"))


class State(Base):
    def test_threads_written_whole(self):
        s._remember("x", "abc")
        self.assertEqual(json.load(open(s.STATE)), {"x": "abc"})
        self.assertFalse(os.path.exists(s.STATE + ".tmp"))

    def test_restart_item_is_flagged(self):
        path = s.hold(DM, {"kind": "in", "who": "A", "author": "+1222", "stamp": 9, "text": "waiting", "files": []})
        self.assertTrue(s.as_item(s.held(path), path, s.RESTARTED)[4].startswith("[This arrived before"))

    def test_fresh_start_forgets_every_conversation(self):
        s._remember("mycroft", "old")
        s._remember(DM, "older")
        s.start_fresh()
        self.assertEqual(json.load(open(s.STATE)), {})

    def test_fresh_start_retires_a_running_session(self):
        class Proc:
            killed = False
            def poll(self): return None if not self.killed else 0
            def kill(self): self.killed = True
            def wait(self): return 0
        session = s.Session("mycroft", "/tmp", queue.Queue())
        session.proc, session.since, session.loaded = Proc(), time.time() - 60, s.rules("/tmp")
        s._remember("mycroft", "old")
        session.refresh()
        self.assertFalse(session.proc.killed)          # nothing newer to start over for
        s.start_fresh()
        s._remember("mycroft", "old")                  # as if written back meanwhile
        session.refresh()
        self.assertTrue(session.proc.killed)
        self.assertNotIn("mycroft", s._threads())

    def test_attachment_keeper_not_shadowed(self):
        self.assertEqual(s.keep.__code__.co_varnames[0], "a")


if __name__ == "__main__":
    unittest.main()
