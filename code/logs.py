#!/usr/bin/env python3
"""Keep the agent's logs from growing for ever.

Every log under the agent's `runs/` folder larger than `LIMIT` is copied to
`<name>.1.gz` and emptied, older copies shifting to `.2.gz` and `.3.gz` and the
oldest dropped. Copy-then-empty rather than rename, because the services writing these
— launchd's own stdout files among them — keep the file open, and would go on
writing into a renamed one that nobody reads.

A scheduler row runs it daily.

    python3 logs.py          rotate what has grown past the limit
"""
import glob
import gzip
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home

RUNS = os.environ.get("LOGS_RUNS") or home.RUNS
LIMIT = 2 * 1024 * 1024
KEEP = 3


def candidates():
    found = glob.glob(f"{RUNS}/**/*.log", recursive=True) + glob.glob(f"{RUNS}/**/*.jsonl", recursive=True)
    return [p for p in found if os.path.isfile(p)]


def rotate(path):
    for n in range(KEEP, 1, -1):                     # .2 -> .3, .1 -> .2; the oldest goes
        older = f"{path}.{n - 1}.gz"
        if os.path.exists(older):
            os.replace(older, f"{path}.{n}.gz")
    with open(path, "rb") as src, gzip.open(f"{path}.1.gz.tmp", "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.replace(f"{path}.1.gz.tmp", f"{path}.1.gz")
    with open(path, "r+b") as fh:                    # emptied in place: writers keep their handle
        fh.truncate(0)


def main():
    for path in candidates():
        if os.path.getsize(path) > LIMIT:
            rotate(path)
            print(f"rotated {os.path.relpath(path, RUNS)}")


if __name__ == "__main__":
    sys.exit(main())
