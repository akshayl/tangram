"""Log rotation, on scratch files."""
import gzip
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import logs


class Rotation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (logs.RUNS, logs.LIMIT)
        logs.RUNS, logs.LIMIT = self.tmp.name, 100
        os.makedirs(f"{self.tmp.name}/signal")
        self.log = f"{self.tmp.name}/signal/listen.log"

    def tearDown(self):
        logs.RUNS, logs.LIMIT = self.saved
        self.tmp.cleanup()

    def test_big_log_rotated_and_writer_keeps_writing(self):
        writer = open(self.log, "a")                 # a service holding it open
        for gen in range(5):
            writer.write(f"generation {gen}\n" + "x" * 200 + "\n")
            writer.flush()
            logs.main()
        writer.write("after\n")
        writer.flush()
        self.assertEqual(open(self.log).read(), "after\n")
        kept = sorted(f for f in os.listdir(os.path.dirname(self.log)) if f.endswith(".gz"))
        self.assertEqual(kept, ["listen.log.1.gz", "listen.log.2.gz", "listen.log.3.gz"])
        self.assertIn("generation 4", gzip.open(self.log + ".1.gz", "rt").read())
        self.assertIn("generation 2", gzip.open(self.log + ".3.gz", "rt").read())

    def test_small_log_left_alone(self):
        open(self.log, "w").write("small\n")
        logs.main()
        self.assertEqual(open(self.log).read(), "small\n")
        self.assertFalse(os.path.exists(self.log + ".1.gz"))


if __name__ == "__main__":
    unittest.main()
