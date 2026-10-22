"""Setup's "from now on" choice: only conversations with the agent, from that
moment. A chat from earlier on setup day, or one
in an unrelated folder afterwards, stays out."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[2])
import recall
S = sys.argv[1]
def tr(folder, ts):
    d = os.path.join(S, "tr", recall._dashed(folder)); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, ts.replace(":", "") + ".jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "user", "timestamp": ts, "message": {"content": "hello"}}) + "\n")
    return bool(recall.transcript_chunks(p))
print(json.dumps([tr(f"{S}/Sprite", "2026-10-21T09:00:00.000Z"),
                  tr(f"{S}/Sprite", "2026-10-21T13:00:00.000Z"),
                  tr(f"{S}/projects/garden", "2026-10-22T08:00:00.000Z"),
                  tr(f"{S}/elsewhere", "2026-10-22T08:00:00.000Z")]))
"""


class Since(unittest.TestCase):
    def test_time_and_scope(self):
        with tempfile.TemporaryDirectory() as S:
            os.makedirs(f"{S}/cfg"); os.makedirs(f"{S}/Sprite"); os.makedirs(f"{S}/projects")
            with open(f"{S}/cfg/settings.json", "w") as fh:
                json.dump({"name": "Sprite", "user": "Sam", "home": f"{S}/Sprite", "projects": f"{S}/projects"}, fh)
            with open(f"{S}/Sprite/recall.json", "w") as fh:
                json.dump({"since": "2026-10-21T12:00:00Z"}, fh)
            out = subprocess.run([sys.executable, "-c", PROBE, S, CODE], capture_output=True, text=True,
                                 env=dict(os.environ, TANGRAM_CONFIG=f"{S}/cfg"))
            self.assertEqual(json.loads(out.stdout or "null"), [False, True, True, False], out.stderr)


if __name__ == "__main__":
    unittest.main()
