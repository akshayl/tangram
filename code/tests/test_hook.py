"""The hook runs before every message in every session, so a mistake in it
fails everywhere at once, quietly. A line one Python version could not parse
once broke it for forty minutes; the recall test never noticed, because it
calls recall directly rather than through the hook.

So these run the hook as Claude Code does — a fresh process, an event on stdin
— in a home folder of their own, against a stand-in recall service and a local
file for Tangram's version. Nothing real is read, and nothing leaves the laptop.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hook.py")


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.agent = os.path.join(self.tmp, "Sprite")
        self.projects = os.path.join(self.tmp, "projects")
        config = os.path.join(self.tmp, "config")
        for d in (self.agent, self.projects, config):
            os.makedirs(d)
        with open(os.path.join(config, "settings.json"), "w") as fh:
            json.dump({"name": "Sprite", "user": "Sam", "home": self.agent,
                       "projects": self.projects}, fh)
        with open(os.path.join(self.agent, "CLAUDE.md"), "w") as fh:
            fh.write("# Sprite\n")
        self.upstream = os.path.join(self.tmp, "upstream-VERSION")

        asked = self.asked = []

        class Recall(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                asked.append(self.path)
                body = json.dumps({"context": "Possibly relevant: a recalled passage"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), Recall)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = dict(os.environ, HOME=self.tmp, TANGRAM_CONFIG=config,
                        RECALL_SERVICE=f"http://127.0.0.1:{self.server.server_port}/search",
                        TANGRAM_VERSION_URL="file://" + self.upstream)
        for knob in ("RECALL_OFF", "RECALL_AS_CWD", "RECALL_PREVIOUS", "RECALL_NO_KNOWLEDGE"):
            self.env.pop(knob, None)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hook(self, cwd, prompt="what did we decide about the garden fence"):
        event = {"prompt": prompt, "cwd": cwd, "session_id": "test"}
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(event), env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return None
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "UserPromptSubmit")
        return out["additionalContext"]

    def project(self, name, claude_md):
        path = os.path.join(self.projects, name)
        os.makedirs(path)
        with open(os.path.join(path, "CLAUDE.md"), "w") as fh:
            fh.write(claude_md)
        return path

    def test_runs_and_answers_in_its_format(self):
        self.assertIn("a recalled passage", self.hook(self.agent))

    def test_silent_outside_the_agents_folders(self):
        elsewhere = os.path.join(self.tmp, "someone-elses-repo")
        os.makedirs(elsewhere)
        self.assertIsNone(self.hook(elsewhere))
        self.assertIsNone(self.hook(self.project("loose", "# Not the agent's\n@notes.md\n")))
        self.assertEqual(self.asked, [])

    def test_works_in_a_project_that_imports_the_agent(self):
        garden = self.project("garden", "# Garden\n\n@../../Sprite/CLAUDE.md\n")
        self.assertIn("a recalled passage", self.hook(garden))
        sub = os.path.join(garden, "plans")
        os.makedirs(sub)
        self.assertIn("a recalled passage", self.hook(sub))      # under it counts too

    def test_newer_version_is_said_once(self):
        with open(os.path.join(self.agent, "VERSION"), "w") as fh:
            fh.write("0.9\n")
        with open(self.upstream, "w") as fh:
            fh.write("0.10\n")                    # newer as numbers, older as text
        self.assertNotIn("Tangram", self.hook(self.agent))       # asks, in the background
        newer = os.path.join(self.tmp, ".local", "state", "tangram", "update", "newer")
        for _ in range(100):
            if os.path.exists(newer):
                break
            time.sleep(0.1)
        notice = ("Tangram has a newer version (0.10; this setup is at 0.9). Read its "
                  "CHANGELOG.md for what changed since 0.9 and propose each change to the "
                  "user, one at a time, at a natural moment.")
        self.assertIn(notice, self.hook(self.agent))
        self.assertNotIn("Tangram", self.hook(self.agent))

    def test_no_notice_when_up_to_date(self):
        for path in (os.path.join(self.agent, "VERSION"), self.upstream):
            with open(path, "w") as fh:
                fh.write("1.2\n")
        self.hook(self.agent)
        time.sleep(1.5)
        self.assertNotIn("Tangram", self.hook(self.agent))


if __name__ == "__main__":
    unittest.main()
