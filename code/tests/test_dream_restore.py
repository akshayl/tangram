"""A failed upkeep session leaves nothing behind.

A stand-in for the agent's CLI writes into the folder it was given, then fails
in each way a real session can: an error, a silent non-zero exit, running out
of time. Each time the folder must come back exactly as it was on disk —
including content that was never committed.
"""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dream
import status_cleanup

FAKE = """#!/bin/bash
echo "half done" > "$FAKE_DIR/note.md"
echo "new" > "$FAKE_DIR/new-note.md"
case "$FAKE_MODE" in
  error)  echo "something broke"; exit 1 ;;
  silent) exit 3 ;;
  slow)   sleep 30 ;;
esac
"""


class Restore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.folder = os.path.join(self.tmp, "subject")
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "note.md"), "w") as fh:
            fh.write("uncommitted, and must survive\n")
        fake = os.path.join(self.tmp, "fake-claude")
        with open(fake, "w") as fh:
            fh.write(FAKE)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        self.saved = dream.CLAUDE
        dream.CLAUDE = fake
        os.environ["FAKE_DIR"] = self.folder

    def tearDown(self):
        dream.CLAUDE = self.saved

    def unchanged(self):
        self.assertEqual(sorted(os.listdir(self.folder)), ["note.md"])
        with open(os.path.join(self.folder, "note.md")) as fh:
            self.assertEqual(fh.read(), "uncommitted, and must survive\n")

    def run_mode(self, mode, timeout=1800):
        os.environ["FAKE_MODE"] = mode
        with self.assertRaises(RuntimeError) as caught:
            dream.claude("x", self.tmp, "t", writable=self.folder, timeout=timeout)
        return str(caught.exception)

    def test_error(self):
        self.assertIn("something broke", self.run_mode("error"))
        self.unchanged()

    def test_silent_failure(self):
        self.assertIn("exit 3, no output", self.run_mode("silent"))
        self.unchanged()

    def test_timeout(self):
        self.assertIn("timed out", self.run_mode("slow", timeout=2))
        self.unchanged()

    def test_success_keeps_writes(self):
        os.environ["FAKE_MODE"] = "ok"
        dream.claude("x", self.tmp, "t", writable=self.folder)
        self.assertIn("new-note.md", os.listdir(self.folder))

    def test_status_cleanup_silent_failure(self):
        os.environ["FAKE_MODE"] = "silent"
        for name in ("STATUS.md", "TODO.md"):
            with open(os.path.join(self.folder, name), "w") as fh:
                fh.write(f"{name} as it was\n")
        os.environ["FAKE_DIR"] = self.folder
        with open(os.path.join(self.tmp, "conversations.md"), "w") as fh:
            fh.write("")
        fake_status = FAKE.replace('"$FAKE_DIR/note.md"', '"$FAKE_DIR/STATUS.md"')
        with open(dream.CLAUDE, "w") as fh:
            fh.write(fake_status)
        name, said = status_cleanup.clean(self.folder, self.tmp, 0, 1)
        self.assertTrue(said.startswith("failed"), said)
        with open(os.path.join(self.folder, "STATUS.md")) as fh:
            self.assertEqual(fh.read(), "STATUS.md as it was\n")


class PruneRestore(Restore):
    """Emptying memory: a failed session puts back memory and knowledge both."""

    def test_prune_timeout(self):
        memory = os.path.join(self.tmp, "MEMORY.md")
        with open(memory, "w") as fh:
            fh.write("# Memory\n\n## Fact\nkept\n")
        saved = dream.KNOWLEDGE, dream.HOME, dream.PRUNE_TIMEOUT, dream.build_index, dream.commit
        dream.KNOWLEDGE, dream.HOME, dream.PRUNE_TIMEOUT = self.folder, self.tmp, 2
        dream.build_index, dream.commit = (lambda: None), (lambda *a: None)
        os.environ["FAKE_MODE"] = "slow"
        try:
            said = dream.prune(self.tmp, files=[memory])
        finally:
            dream.KNOWLEDGE, dream.HOME, dream.PRUNE_TIMEOUT, dream.build_index, dream.commit = saved
        self.assertIn("timed out", said)
        self.unchanged()
        with open(memory) as fh:
            self.assertEqual(fh.read(), "# Memory\n\n## Fact\nkept\n")


class WholeEntries(unittest.TestCase):
    BEFORE = "# Memory\nintro\n\n## A\na1\na2\n\n## B\nb1\n"

    def test_whole_entry_removed(self):
        self.assertTrue(dream.trimmed(self.BEFORE, "# Memory\nintro\n\n## A\na1\na2\n"))

    def test_part_of_entry_removed(self):
        self.assertFalse(dream.trimmed(self.BEFORE, "# Memory\nintro\n\n## A\na1\n\n## B\nb1\n"))

    def test_header_changed(self):
        self.assertFalse(dream.trimmed(self.BEFORE, "# Memory\n\n## A\na1\na2\n\n## B\nb1\n"))

    def test_entry_added(self):
        self.assertFalse(dream.trimmed(self.BEFORE, self.BEFORE + "\n## C\nc1\n"))


if __name__ == "__main__":
    unittest.main()
