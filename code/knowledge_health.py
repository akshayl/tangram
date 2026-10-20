#!/usr/bin/env python3
"""How healthy knowledge is: counted, not judged, and free.

Knowledge grows every night, and grown knowledge rots quietly — duplicates,
notes too long to arrive in one piece, claims nobody has confirmed in weeks,
notes no conversation ever needs. This counts each, so maintenance is measured
rather than hoped for.

- **notes**: how many, and per subject.
- **long**: notes over 200 words — they are stored in pieces and cost more
  context when they arrive whole.
- **twins**: pairs of notes so alike they are probably one fact twice.
- **stale**: notes whose `Last confirmed` is over two weeks old.
- **unused**: notes no real search has returned since the log began
  (`runs/recall/searches.jsonl`), out of all notes — only meaningful after a
  few weeks of the log.

    python3 knowledge_health.py        print the counts
"""
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home
import recall

LONG = 200            # words
TWIN = 0.93           # similarity of two notes' openings
STALE = 14 * 86400


def health():
    notes = [p for p in glob.glob(os.path.join(home.KNOWLEDGE, "*", "*.md"))]
    by_subject, long_, stale = {}, [], []
    now = time.time()
    for p in notes:
        subject = os.path.basename(os.path.dirname(p))
        by_subject[subject] = by_subject.get(subject, 0) + 1
        text = open(p, errors="replace").read()
        body = text.split("## Notes")[0]
        if len(body.split()) > LONG and not p.endswith("summary.md"):
            long_.append(p)
        m = re.search(r"Last confirmed: (\d{4}-\d{2}-\d{2})", text)
        if m and now - time.mktime(time.strptime(m.group(1), "%Y-%m-%d")) > STALE:
            stale.append(p)
    # Twins: compare each note's first stored piece, by meaning.
    recall.load()
    con = recall.db()
    first = {}
    for pid, path in con.execute("SELECT id, path FROM passages WHERE path LIKE ? ORDER BY id",
                                 (os.path.join(home.KNOWLEDGE, "") + "%",)):
        if path not in first and pid in recall._index and not path.endswith(("summary.md", "index.md")):
            first[path] = recall._matrix[recall._index[pid]]
    con.close()
    paths = list(first)
    twins = []
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if float(first[a] @ first[b]) > TWIN:
                twins.append((a, b))
    used = set()
    try:
        for line in open(recall.SEARCHES):
            for h in json.loads(line).get("hits", []):
                used.add(os.path.join(os.path.expanduser("~"), h["path"]))
    except (OSError, ValueError):
        pass
    unused = [p for p in notes if p not in used]
    return {"notes": len(notes), "subjects": len(by_subject), "long": long_, "twins": twins,
            "stale": stale, "unused": len(unused), "by_subject": by_subject}


def main():
    h = health()
    rel = lambda p: os.path.relpath(p, home.KNOWLEDGE)
    print(f"  notes {h['notes']} in {h['subjects']} subjects   long {len(h['long'])}   "
          f"twins {len(h['twins'])}   stale {len(h['stale'])}   unused {h['unused']}/{h['notes']}")
    print("  per subject: " + ", ".join(f"{s} {n}" for s, n in sorted(h["by_subject"].items(), key=lambda x: -x[1])))
    if "-v" in sys.argv:
        for a, b in h["twins"]:
            print(f"  twin: {rel(a)}  ~  {rel(b)}")
        for p in h["long"]:
            print(f"  long: {rel(p)}")


if __name__ == "__main__":
    main()
