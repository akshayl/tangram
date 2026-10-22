#!/usr/bin/env python3
"""Recall: what the user and the agent have written and said, searchable from anywhere.

Memory is what the agent already holds — the handful of facts in its
`MEMORY.md`, loaded every session. Recall is going and finding what it does
not hold: everything either of them has written or said.

Every session — a terminal, a Signal chat, an unattended run — has only what is
in its own files and its own conversation. This is the shared recall: everything
either of them has written or said, searchable from anywhere, so a decision made
in one chat in October is findable from another in January.

**One interface, two searches.** `search()` is the only way in. Underneath it,
words and meaning, merged:

- keyword search (SQLite's own, BM25) catches the exact things — an address, a
  number, a name — which a vector model blurs.
- vector search catches the same idea in other words: "are we still waiting to
  buy" finding "agreed to hold off until spring", which share no words at all.

Neither is enough alone, and the pair is swappable behind the interface — a real
vector database becomes worth it when the index no longer fits in memory.

**Embeddings, briefly.** A small local model (bge-small, 384 numbers per
passage) places text in a space where similar meanings sit close together.
Searching is then arithmetic: turn the question into numbers, multiply against
every passage at once, keep the highest. Nothing leaves the laptop.

**What goes in.** Markdown in the agent's home and the projects folder, the
global `~/.claude/CLAUDE.md` and slash commands, the agent's own code's
docstrings, the git history of both folders, and what was actually *said* in
session transcripts — never tool output, which is most of their bulk and none of
their meaning. Each passage is stamped with its project, file and date, so
anything recalled can be labelled and filtered.

`recall.json` in the agent's home is optional. Whatever it sets overrides the
defaults below, except `sources` and `exclude`, which add to them — a folder of
saved reading nobody wants recalled is one line there, not a code change.

    python3 recall.py index         build or update the index
    python3 recall.py search "..."  ask it something
    python3 recall.py serve         the resident service, reindexing as it goes
    python3 recall.py stats         what it holds
"""
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import subprocess
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home  # noqa: E402

CONFIG = os.path.join(home.AGENT, "recall.json")
HOME = os.path.join(home.STATE, "recall")
# RECALL_MODEL and RECALL_DB build a second index beside the live one, to
# compare a model before switching to it.
DB = os.environ.get("RECALL_DB") or os.path.join(HOME, "index.sqlite")
MODEL = os.environ.get("RECALL_MODEL") or "BAAI/bge-small-en-v1.5"
DIM = {"BAAI/bge-small-en-v1.5": 384, "BAAI/bge-base-en-v1.5": 768,
       "BAAI/bge-large-en-v1.5": 1024}[MODEL]
WORDS = 220                     # per passage: comfortably inside the model's window
PORT = home.RECALL_PORT
# Homebrew's, by full path. launchd's PATH finds /usr/bin/git, which on a Mac
# is Xcode's shim and can break with an Xcode update; when it did, every commit
# went unindexed for weeks before anyone noticed, because a failed `git log`
# reads as "no commits".
GIT = next((g for g in ("/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git")
            if os.path.exists(g)), shutil.which("git") or "git")

_model = None
_matrix = None                  # every passage's numbers, in memory
_ids = []
_index = {}
_lock = threading.Lock()


_AGENT = os.path.basename(home.AGENT.rstrip(os.sep))     # the agent's home, as a project name
SOURCES = [
    {"kind": "markdown", "root": home.AGENT, "project": _AGENT},
    {"kind": "markdown", "root": home.PROJECTS},       # project: the folder under it
    {"kind": "markdown", "root": "~/.claude/commands", "project": "claude"},
    {"kind": "markdown", "root": "~/.claude", "project": "claude", "top_only": True},
    {"kind": "code", "root": home.CODE, "project": _AGENT},
    {"kind": "transcripts", "root": home.TRANSCRIPTS},
] + [{"kind": "git", "root": repo, "project": os.path.basename(repo.rstrip(os.sep))}
     for repo in home.REPOS]
# Folders never walked: dependencies, generated files, logs. Bulk without meaning.
EXCLUDE = ["node_modules", "__pycache__", ".venv", "assets", "fonts", "data", "runs",
           "site-packages"]
DEFAULTS = {"floor": 0.63, "top_k": 4, "every_minutes": 5, "crowd": 2}


def config():
    """The defaults, with `recall.json` on top: its sources and excludes add to
    these, anything else it sets replaces the default."""
    try:
        with open(CONFIG) as fh:
            extra = json.load(fh)
    except (OSError, ValueError):
        extra = {}
    cfg = dict(DEFAULTS, sources=SOURCES + extra.pop("sources", []),
               exclude=EXCLUDE + extra.pop("exclude", []))
    cfg.update(extra)
    return cfg


def db():
    os.makedirs(HOME, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS passages (
            id INTEGER PRIMARY KEY, path TEXT, project TEXT, agent TEXT,
            date TEXT, kind TEXT, text TEXT, vec BLOB);
        CREATE INDEX IF NOT EXISTS passages_path ON passages(path);
        CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, seen REAL);
        CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts
            USING fts5(text, content='passages', content_rowid='id', tokenize='porter unicode61');
    """)
    # Stemmed — "cars" finds "car", "shipped" finds "shipping". On a scorecard of
    # real questions: found 102 -> 104 of 121, noise 53% -> 49%. An index built
    # unstemmed is rebuilt once, in seconds; the vectors are untouched.
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='passages_fts'").fetchone()[0]
    if "porter" not in sql:
        with con:
            con.execute("DROP TABLE passages_fts")
            con.execute("CREATE VIRTUAL TABLE passages_fts USING fts5(text, content='passages', "
                        "content_rowid='id', tokenize='porter unicode61')")
            con.execute("INSERT INTO passages_fts(passages_fts) VALUES('rebuild')")
    return con


def model():
    """Loaded once, then held — the reason this wants to be a resident process."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL)
    return _model


def embed(texts):
    """Small batches, because onnxruntime keeps whatever a batch made it grow to.

    At fastembed's default of 256 passages a batch, the service reached 9.5 GB
    and held it; the arena is sized by the largest batch it has ever seen."""
    import numpy as np
    V = np.array(list(model().embed(list(texts), batch_size=16)), dtype="float32")
    V /= np.linalg.norm(V, axis=1, keepdims=True)    # so similarity is one multiply
    return V


# --- what goes in ----------------------------------------------------------

def chunks(text, size=WORDS, lead=""):
    """Markdown split at its own headings, then trimmed to fit the model.

    `lead` goes in front of every piece: a knowledge note's subject, so its
    passages match questions about that subject. (The file's title in front of
    every section was tried for all markdown and scored no better.)"""
    parts, current = [], []
    for line in text.splitlines():
        if line.startswith("#") and current:
            parts.append("\n".join(current))
            current = []
        current.append(line)
    parts.append("\n".join(current))
    out = []
    for part in parts:
        words = part.split()
        if not words:
            continue
        # A long section split in pieces: each later piece carries the section's
        # heading. Without it a figure arrived bare, though its heading said it
        # had been superseded.
        first = part.splitlines()[0].strip()
        heading = first + " … " if first.startswith("#") else ""
        for i in range(0, len(words), size):
            piece = " ".join(words[i:i + size])
            if len(piece) > 40:
                out.append(lead + (heading if i else "") + piece)
    return out


# Passwords and access codes never reach the index: a code pasted into one chat
# would otherwise be searchable from every other, and could be quoted back in a
# Signal reply. Matched by shape, so nothing depends
# on an agent remembering. A bare word password with no label can't be told
# from ordinary text and gets through.
SECRET = "[secret removed]"
_SECRETS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk-ant-|sk-proj-|sk-|ghp_|gho_|ghs_|ghu_|github_pat_|xox[abprs]-|ya29\.|AIza|AKIA|glpat-|am_)"
               r"[A-Za-z0-9_\-.]{12,}"),
    # long and random: mixed case and digits, 32+ characters, no slashes (paths)
    re.compile(r"(?<![A-Za-z0-9_\-+/=])(?=[A-Za-z0-9_\-+=]*[a-z])(?=[A-Za-z0-9_\-+=]*[A-Z])"
               r"(?=[A-Za-z0-9_\-+=]*[0-9])[A-Za-z0-9_\-+=]{32,}(?![A-Za-z0-9_\-+/=])"),
]
# what follows "password", "PIN" and the like, when it looks like a value
_LABELLED = re.compile(r"(?i)\b(password|passcode|passphrase|pin|api[ _-]?key|secret|token)"
                       r"(\s*(?:is|was|:|=)\s*[`'\"]?)((?=\S*\d)[^\s`'\"]{4,})")


def _random(m):
    """A long mixed string is a code unless it reads like words (a file name
    has runs of lowercase letters) or is a Drive file ID, which is an address."""
    tok = m.group(0)
    if re.search(r"[a-z]{6}", tok) or re.fullmatch(r"1[A-Za-z0-9_\-]{32}|1[A-Za-z0-9_\-]{43}", tok):
        return tok
    return SECRET


def redact(text):
    for pattern in _SECRETS[:2]:
        text = pattern.sub(SECRET, text)
    text = _SECRETS[2].sub(_random, text)
    return _LABELLED.sub(lambda m: m.group(1) + m.group(2) + SECRET, text)


AUTOMATED = ("You are dreaming:", "You keep the knowledge about", "You are reviewing tonight",
             "Below is the knowledge index", "You are finding every subject",
             "You are tidying the whole knowledge", "You are reviewing the whole knowledge",
             "You are doing the nightly status cleanup", "Answer from what you know")


def command_texts():
    """The opening of each slash command's instructions. When the user types
    `/save`, the transcript holds the command's text as their turn; it is the
    command speaking, not them."""
    out = []
    for path in glob.glob(os.path.expanduser("~/.claude/commands/*.md")):
        body = open(path, errors="replace").read()
        if body.startswith("---"):
            body = body.split("---", 2)[-1]
        if body.strip():
            out.append(" ".join(body.split())[:80])
    return tuple(out)


def transcript_chunks(path):
    """What was said, never what a tool returned.

    A session transcript is mostly file contents and command output — the bulk of
    285 MB and the least worth recalling. Only the human's words and the agent's
    replies come through, paired so a question keeps its answer.

    `"since": "YYYY-MM-DD"` in `recall.json` leaves out anything said before
    that day: setup asks whether past Claude Code conversations come in, or
    only conversations from the day the agent was made."""
    said, commands = [], command_texts()
    since = config().get("since") or ""
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") not in ("user", "assistant"):
                continue
            if since and (row.get("timestamp") or "")[:10] < since:
                continue
            content = (row.get("message") or {}).get("content")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n".join(b.get("text", "") for b in content
                                 if isinstance(b, dict) and b.get("type") == "text")
            text = text.strip()
            # The harness's own messages ride in the user's turn: reminders,
            # background-task notices, command echoes. None of it was said.
            text = re.sub(r"<(system-reminder|task-notification)>.*?</\1>", "", text, flags=re.S).strip()
            if text and " ".join(text.split()).startswith(commands):
                continue                      # a slash command's own instructions
            if not text or text.startswith(("<system-reminder", "<command-name", "<local-command",
                                            "<task-notification", "[SYSTEM NOTIFICATION",
                                            "Another Claude session sent a message",
                                            "Caveat:")):
                continue
            said.append((home.USER if row["type"] == "user" else home.NAME, text[:4000]))
    # A dream's or the memory test's own session is not a conversation.
    if said and said[0][1].startswith(AUTOMATED):
        return []
    out, buf = [], []
    for who, text in said:
        buf.append(f"{who}: {text}")
        if len(" ".join(buf).split()) > WORDS:
            out.append("\n".join(buf))
            buf = buf[-1:]                    # keep the last turn for context
    if buf:
        out.append("\n".join(buf))
    return out


def docstrings(source):
    """What a script says it is for, and nothing else.

    Our own code explains itself in its module docstring — what the watch
    checks, what the listener does. The code around it would only add noise, so
    only the prose comes in."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in [tree] + [n for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.ClassDef))]:
        text = ast.get_docstring(node)
        if text and len(text.split()) > 25:
            name = getattr(node, "name", "")
            out += chunks((f"{name}: " if name else "") + text)
    return out


def commits(repo):
    """Every commit as one passage: when, what, and which files moved.

    Dates live in git and nowhere else, so a question about when something first
    appeared used to cost several shell calls against prose that carried no date
    at all. One passage per commit puts that answer one search away.

    Long file lists are cut short: past a few names they are noise that matches
    everything."""
    import subprocess
    r = subprocess.run([GIT, "-C", repo, "log", "--date=short", "--name-only",
                        "--pretty=format:%x00%ad %s%n%b"],
                       capture_output=True, text=True)
    if r.returncode:
        return []
    out = []
    for block in r.stdout.split("\x00"):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        message = [l for l in lines if not re.match(r"^[\w./-]+$", l)
                   or l.startswith(("Co-Authored", "Claude-Session"))]
        files = [l for l in lines if l not in message]
        message = [l for l in message
                   if not l.startswith(("Co-Authored-By:", "Claude-Session:"))]
        text = " ".join(" ".join(message).split())
        if files:
            text += "  [files: " + ", ".join(files[:8])
            text += f", and {len(files) - 8} more]" if len(files) > 8 else "]"
        out.append(text[:1500])
    return out


def lead_of(path):
    """A knowledge note's subject, put in front of each of its passages."""
    for k in KNOWLEDGE:
        if path.startswith(k):
            subject = os.path.relpath(path, k).split(os.sep)[0]
            return "" if subject.endswith(".md") else f"[{subject}] "
    return ""


def _dashed(path):
    """A directory as Claude Code names its transcript folder: every character
    that is not a letter or digit turned to a dash, the leading one dropped."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.expanduser(path).rstrip(os.sep)).lstrip("-")


def project_of(folder):
    """`-Users-<you>-projects-garden` is the garden project, whatever it is called.

    A transcript's folder is the directory it was opened in, with slashes turned
    to dashes — so the project is whatever follows the projects folder, and a
    session in the agent's own home belongs to the agent."""
    name, projects, agent = folder.lstrip("-"), _dashed(home.PROJECTS), _dashed(home.AGENT)
    if name.startswith(projects + "-"):
        return name[len(projects) + 1:]
    if name == agent or name.startswith(agent + "-"):
        return _AGENT
    user = _dashed("~")                       # whoever's laptop this is
    if name in (user, projects):
        return "loose"
    return name.replace(f"{user}-", "")


def sources(cfg):
    """Every file worth indexing, with where it came from."""
    skip = set(cfg["exclude"])
    for src in cfg["sources"]:
        root = os.path.expanduser(src["root"])
        if src["kind"] == "git":
            head = os.path.join(root, ".git", "logs", "HEAD")
            if os.path.exists(head):          # its mtime is "has anything landed"
                yield head, src.get("project") or os.path.basename(root), "git"
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [] if src.get("top_only") else [
                d for d in dirnames if d not in skip and not d.startswith(".git")]
            for name in filenames:
                path = os.path.join(dirpath, name)
                if src["kind"] == "markdown" and name.endswith(".md"):
                    rel = os.path.relpath(path, root)
                    project = src.get("project") or rel.split(os.sep)[0]
                    yield path, project, "markdown"
                elif src["kind"] == "code" and name.endswith(".py"):
                    rel = os.path.relpath(path, root)
                    yield path, src.get("project") or rel.split(os.sep)[0], "code"
                elif src["kind"] == "transcripts" and name.endswith(".jsonl"):
                    yield path, project_of(os.path.basename(dirpath)), "transcript"


def index(verbose=True):
    """Add what changed since last time. Cheap enough to run every five minutes."""
    cfg = config()
    con = db()
    seen = dict(con.execute("SELECT path, seen FROM files"))
    added = files = 0
    for path, project, kind in sources(cfg):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if seen.get(path, 0) >= mtime:
            continue
        try:
            if kind == "git":
                pieces = commits(os.path.dirname(os.path.dirname(path)))
            elif kind == "transcript":
                pieces = transcript_chunks(path)
            elif kind == "code":
                pieces = docstrings(open(path, errors="replace").read())
            else:
                pieces = chunks(open(path, errors="replace").read(), lead=lead_of(path))
        except OSError:
            continue
        pieces = [redact(p) for p in pieces]
        if not pieces:                        # nothing left worth holding: forget what was
            with con:
                con.execute("INSERT INTO passages_fts(passages_fts, rowid, text) "
                            "SELECT 'delete', id, text FROM passages WHERE path=?", (path,))
                con.execute("DELETE FROM passages WHERE path=?", (path,))
                con.execute("REPLACE INTO files VALUES (?,?)", (path, mtime))
            continue
        date = time.strftime("%Y-%m-%d", time.localtime(mtime))
        dated = kind == "git"                 # each commit carries its own date
        agent = home.NAME
        # A file that changed is mostly what it was: a transcript gains a turn,
        # a repo gains a commit. Only text not already held is embedded — before
        # this, every new message re-embedded its whole conversation.
        known = {t: v for t, v in con.execute(
            "SELECT text, vec FROM passages WHERE path=?", (path,))}
        fresh = [p for p in dict.fromkeys(pieces) if p not in known]
        if fresh:
            for p, v in zip(fresh, embed(fresh)):
                known[p] = v.tobytes()
        with con:
            old = [r[0] for r in con.execute("SELECT id FROM passages WHERE path=?", (path,))]
            for i in old:
                con.execute("INSERT INTO passages_fts(passages_fts, rowid, text) "
                            "VALUES('delete', ?, (SELECT text FROM passages WHERE id=?))", (i, i))
            con.execute("DELETE FROM passages WHERE path=?", (path,))
            for piece in pieces:
                when = piece[:10] if dated and piece[:4].isdigit() else date
                cur = con.execute(
                    "INSERT INTO passages (path, project, agent, date, kind, text, vec) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (path, project, agent, when, kind, piece, known[piece]))
                con.execute("INSERT INTO passages_fts(rowid, text) VALUES (?,?)",
                            (cur.lastrowid, piece))
            con.execute("REPLACE INTO files VALUES (?,?)", (path, mtime))
        added += len(fresh)
        files += 1
        if verbose and files % 20 == 0:
            print(f"  {files} files, {added} passages", flush=True)
    pruned = prune(con, cfg)
    con.close()
    if verbose:
        print(f"indexed {files} changed file(s), {added} new passage(s), "
              f"dropped {pruned} stale", flush=True)
    return files, added, pruned


def prune(con, cfg):
    """Forget what no longer exists, except conversations nothing else holds.

    A moved or deleted file leaves its passages behind, found by searches and
    pointing nowhere — a project's old name lingered this way after a rename.
    Those go. A transcript is different: Claude Code deletes old ones on its own,
    and then these passages are the only copy of what was said. One goes only if
    the same session now lives under another folder."""
    live = {path for path, _, _ in sources(cfg)}
    moved = {os.path.basename(p) for p in live if p.endswith(".jsonl")}
    stale = [p for (p, kind) in con.execute("SELECT DISTINCT path, kind FROM passages")
             if p not in live and (kind != "transcript" or os.path.basename(p) in moved)]
    with con:
        for p in stale:
            con.execute("INSERT INTO passages_fts(passages_fts, rowid, text) "
                        "SELECT 'delete', id, text FROM passages WHERE path=?", (p,))
            con.execute("DELETE FROM passages WHERE path=?", (p,))
            con.execute("DELETE FROM files WHERE path=?", (p,))
    return len(stale)


def load(force=False):
    """Every passage's numbers, as one array in memory.

    Streamed row by row into an array sized up front. Reading them all and
    joining them first held three copies at the peak, every five minutes, and
    Python seldom hands freed memory back — so the service crept upwards."""
    global _matrix, _ids, _index
    import numpy as np
    with _lock:
        if _matrix is not None and not force:
            return
        con = db()
        n = con.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        matrix, ids = np.empty((n, DIM), "float32"), []
        for i, (pid, vec) in enumerate(con.execute("SELECT id, vec FROM passages")):
            if i >= n:
                break                         # rows added since the count wait for next time
            matrix[i] = np.frombuffer(vec, dtype="float32")
            ids.append(pid)
        con.close()
        _ids, _matrix = ids, matrix[:len(ids)]
        _index = {pid: i for i, pid in enumerate(ids)}
        con = db()
        load_entities(con)
        con.close()


# --- the one interface -----------------------------------------------------

KNOWLEDGE = [os.path.join(home.KNOWLEDGE, "")]   # what dreaming writes
# Ranking, each measured against a scorecard of real questions before it changed.
SHORTLIST = 4                  # candidates by meaning, per passage wanted
KEYWORD_WEIGHT = 0.5           # a keyword hit's lift, divided by its rank + 1
DISTINCTIVE_ONLY = True        # a keyword-only hit must match a number, name or long word
BOOST_PROJECT = 0.05           # the project the session is in
BOOST_WRITTEN = 0.08           # a file over a conversation
BOOST_KNOWLEDGE = 0.1          # a knowledge note over everything
AGE_PER_DAY = 0.004            # what a conversation loses per day of age
AGE_CAP = 0.15                 # ...up to this, about forty days
NOTES = 2
FOLLOWUP = True                # search a short follow-up with the message before it
SPLIT = True                   # search a long message in its parts as well
DISPUTED = 0.15                # a note he has corrected ranks lower until fixed
NOTE_FLOOR = 0.85              # a stricter cut-off for notes, which arrive whole; boosts included
# Tried on three subjects: notes tagged with what they are about
# ("About:" lines), matched to the question. It reached the right notes, but
# they lacked the detail asked for, so nothing more was found and noise rose
# 52% -> 58%. Off; the code stays for when the notes are fuller.
ENTITIES = False               # add notes whose About line the question names
ENTITY_COMMON = 15             # a word on more notes than this matches nothing                       # knowledge notes added on top of the k passages


ACKNOWLEDGEMENTS = set("""ok okay k kk yes yep yeah yup no nope sure thanks thank thx ty great
good nice cool perfect fine done go ahead lgtm got it sounds right agreed right correct wait hmm
lol haha ha oh ah oops sorry please continue next engage ship and so""".split())


def _stem(word):
    word = word.lower().strip("'")
    for end in ("'s", "ies", "es", "s"):
        if word.endswith(end) and len(word) > len(end) + 2:
            return word[:-len(end)] + ("y" if end == "ies" else "")
    return word


def _words(text):
    return {_stem(w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'.-]*", text) if len(w) > 2}


_entities = {}                  # note path -> (passage id, [(name words, kind words)])
_common = set()                 # entity words on so many notes they say nothing


def load_entities(con):
    """Each knowledge note's `About:` line — the things it is about and their
    kinds — read into memory. MindCraft's idea, borrowed: a question
    that names a thing, or its kind ("cars"), reaches the notes about it even
    when the wording differs."""
    global _entities, _common
    found = {}
    for pid, path, text in con.execute("SELECT id, path, text FROM passages WHERE path LIKE ? "
                                       "ORDER BY id", (KNOWLEDGE[0] + "%",)):
        m = re.search(r"About: (.+?)(?: Last confirmed:| [A-Z][a-z]+ [a-z]| #|$)", text)
        if path in found or not m:
            continue
        items = []
        for part in m.group(1).split(";"):
            name, _, kinds = part.partition("(")
            items.append((_words(name), _words(kinds.rstrip(") "))))
        found[path] = (pid, items)
    counts = {}
    for _, items in found.values():
        for w in {w for n, k in items for w in n | k}:
            counts[w] = counts.get(w, 0) + 1
    _entities, _common = found, {w for w, n in counts.items() if n > ENTITY_COMMON}


def entity_notes(question, qv=None):
    """Notes whose things or kinds the question names, best first; ties go to
    the note closer in meaning to the question."""
    asked = _words(question) - _common
    scored = []
    for path, (pid, items) in _entities.items():
        score = 0
        for name, kinds in items:
            name = name - _common
            if name and len(name & asked) * 2 >= len(name):
                score += 2                      # names the thing itself
            elif kinds - _common & asked:
                score += 1                      # names its kind: "cars", "watch"
        if score:
            near = float(_matrix[_index[pid]] @ qv) if qv is not None and pid in _index else 0
            scored.append((score, near, pid))
    return [pid for _, _, pid in sorted(scored, reverse=True)]


def worth_searching(message):
    """Whether a message could need anything remembered.

    Only a pure acknowledgement — "ok", "thanks", "go ahead", "sounds good" —
    cannot. The rule was once "under five words", which also skipped "when's
    the dentist?": short questions are questions."""
    words = re.findall(r"[a-z0-9']+", message.lower())
    return bool(words) and not all(w in ACKNOWLEDGEMENTS for w in words)


def age_days(date):
    try:
        return (time.time() - time.mktime(time.strptime(date, "%Y-%m-%d"))) / 86400
    except (TypeError, ValueError):
        return 0


def loaded(cwd):
    """The files a session in `cwd` already holds in full: every CLAUDE.md from
    there up, the global one, and whatever they import with `@`.

    Recall never returns these. One search spent three of its four places on
    the agent's own `CLAUDE.md`, a file already in front of it."""
    files = []

    def add(path):
        path = os.path.realpath(os.path.expanduser(path))
        if path in files or not os.path.isfile(path):
            return
        files.append(path)
        try:
            text = open(path, errors="replace").read()
        except OSError:
            return
        for m in re.finditer(r"^@(\S+)", text, re.M):
            ref = os.path.expanduser(m.group(1))
            add(ref if os.path.isabs(ref) else os.path.join(os.path.dirname(path), ref))

    add("~/.claude/CLAUDE.md")
    d = os.path.realpath(os.path.expanduser(cwd or "~"))
    while True:
        add(os.path.join(d, "CLAUDE.md"))
        if d == os.path.dirname(d):
            break
        d = os.path.dirname(d)
    return files


def search(question, k=4, prefer=None, floor=None, cwd=None, session=None, crowd=None,
           knowledge=True):
    """Passages worth putting in front of an agent, best first.

    Words and meaning both searched, merged by rank. `prefer` is a project whose
    passages rank higher without excluding anything else — a question asked in
    one project should reach that project's material first, and still be able
    to surface what another project decided.
    """
    import numpy as np
    cfg = config()
    floor = cfg["floor"] if floor is None else floor
    load()
    con = db()
    hits = {}
    held = set(loaded(cwd)) if cwd else set()

    if len(_ids):
        q = embed([question])[0]
        scores = _matrix @ q
        for i in np.argsort(scores)[::-1][:k * SHORTLIST]:
            if scores[i] >= floor:
                hits[_ids[i]] = {"vector": float(scores[i])}

    # Keyword search earns its place on the distinctive words — a street name, a
    # sum of money, a surname. On ordinary words it would match everything, so a
    # passage found only this way has to contain one of those.
    words = re.findall(r"[A-Za-z0-9$'./-]{3,}", question)
    distinctive = [w for w in words
                   if any(c.isdigit() for c in w) or w[0].isupper() or len(w) >= 8]
    if words:
        # Dropping filler words ("can you tell me") first was tried and found
        # one fewer fact on the scorecard; BM25 already discounts them.
        fts = " OR ".join(f'"{w}"' for w in words[:12])
        try:
            for rank, (pid, _score) in enumerate(con.execute(
                    "SELECT rowid, bm25(passages_fts) FROM passages_fts "
                    "WHERE passages_fts MATCH ? ORDER BY bm25(passages_fts) LIMIT ?",
                    (fts, k * 4))):
                hits.setdefault(pid, {})["keyword"] = rank
        except sqlite3.OperationalError:
            pass

    out = []
    for pid, how in hits.items():
        row = con.execute("SELECT path, project, agent, date, kind, text "
                          "FROM passages WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        path, project, agent, date, kind, text = row
        # Not what the session already holds: its own conversation, which it
        # can see, and the files it loaded whole.
        if session and kind == "transcript" and os.path.basename(path).startswith(session):
            continue
        if os.path.realpath(path) in held:
            continue
        if not knowledge and any(path.startswith(k) for k in KNOWLEDGE):
            continue                       # for measuring what knowledge adds
        if "vector" not in how and DISTINCTIVE_ONLY:
            low = text.lower()
            if not any(w.lower() in low for w in distinctive):
                continue                       # matched only on ordinary words
        # Rank fusion: a passage found both ways beats one found either way.
        score = how.get("vector", floor) + (KEYWORD_WEIGHT / (1 + how.get("keyword", 99)))
        if prefer and project == prefer:
            score += BOOST_PROJECT
        # Written-down beats overheard: measured against the test set, the right
        # answer is markdown three times out of four, because a status file says
        # a thing once and deliberately while a conversation circles it.
        if kind == "markdown":
            score += BOOST_WRITTEN
        if "\nDisputed:" in text or " Disputed: " in text:
            score -= DISPUTED                  # the user corrected it; the dream hasn't yet
        # Newer beats older in what was said. A file is edited to today's value;
        # a conversation keeps whatever was true when it was said, so an old one
        # hands over a superseded budget or rate as if it still held. Measured:
        # with recall on, 8 of 17 superseded values were presented as current;
        # with files alone, none.
        if kind in ("transcript", "git"):
            score -= min(AGE_CAP, AGE_PER_DAY * max(0, age_days(date)))
        # A dream's note is a lesson already drawn from the raw material, so it
        # goes first. Only the agent's own knowledge folder: "knowledge" in a
        # path proves nothing, and a project may well be called knowledge.
        if any(path.startswith(k) for k in KNOWLEDGE):
            score += BOOST_KNOWLEDGE
        out.append({"id": pid, "text": text, "path": path, "project": project, "agent": agent,
                    "date": date, "kind": kind, "score": round(score, 3),
                    "how": "both" if len(how) > 1 else next(iter(how))})
    con.close()
    # The floor again, on the merged score: unrelated passages sit around 0.59
    # (measured against nonsense questions), real answers at 0.7 and up. Without
    # it every message would drag in its three nearest passages regardless.
    out = [h for h in out if h["score"] >= floor]  # merged score, floor again
    out.sort(key=lambda h: -h["score"])
    # One passage per crowd: two chunks of the same conversation, or three
    # commits, push out every other source and say less between them than one
    # written file does. Files are the exception — they were written to be read.
    # Knowledge comes on top of the evidence, never instead of it: up to
    # NOTES notes, then k passages of what was written and said. Measured:
    # when notes took the k places, the raw passage holding the
    # exact detail was pushed out, and real sessions answered worse with
    # knowledge (24 of 37) than without (28).
    notes, have = [], set()
    for h in out:                            # one piece per note: a second is noise
        if h["score"] < NOTE_FLOOR:          # a note arrives whole, so it must matter
            continue
        if any(h["path"].startswith(p) for p in KNOWLEDGE) and h["path"] not in have:
            have.add(h["path"])
            notes.append(h)
    if ENTITIES and knowledge:
        have = {h["path"] for h in notes}
        con = db()
        for pid in entity_notes(question, q if len(_ids) else None):
            row = con.execute("SELECT path, project, agent, date, kind, text FROM passages "
                              "WHERE id=?", (pid,)).fetchone()
            if row and row[0] not in have and os.path.realpath(row[0]) not in held:
                have.add(row[0])
                notes.append({"id": pid, "text": row[5], "path": row[0], "project": row[1],
                              "agent": row[2], "date": row[3], "kind": row[4],
                              "score": 0, "how": "entity"})
        con.close()
    # Notes found by search first, then by what they are about. Two kinds of
    # evidence, interleaved, so neither crowds the other out.
    found_notes = [h for h in notes if h["how"] != "entity"]
    by_entity = [h for h in notes if h["how"] == "entity"]
    notes = [h for pair in zip(found_notes, by_entity) for h in pair]
    notes += found_notes[len(by_entity):] + by_entity[len(found_notes):]
    notes = notes[:NOTES]
    # A note arrives whole: a long one is stored in pieces, and the piece that
    # matched need not hold the detail asked for.
    if notes:
        con = db()
        for h in notes:
            parts = [t for (t,) in con.execute("SELECT text FROM passages WHERE path=? ORDER BY id",
                                               (h["path"],))]
            lead = parts[0].split("] ", 1)[0] + "] " if parts[0].startswith("[") else ""
            body = [parts[0]] + [re.sub(r"^#[^…]*… ", "", p[len(lead):]) for p in parts[1:]]
            text = " ".join(body)
            # A summary's list of its notes is for reading the folder, not for
            # recall: one summary was 5,000 characters, mostly the list.
            text = text.split("## Notes")[0].rstrip()
            h["text"] = text
        con.close()
    kept, seen, crowd = [], {}, crowd or cfg.get("crowd", 1)
    for h in out:
        if any(h["path"].startswith(p) for p in KNOWLEDGE):
            continue
        if h["kind"] in ("transcript", "git"):
            if seen.get(h["kind"], 0) >= crowd:
                continue
            seen[h["kind"]] = seen.get(h["kind"], 0) + 1
        kept.append(h)
        if len(kept) == k:
            break
    return notes + kept


SEARCHES = os.path.join(home.RUNS, "recall", "searches.jsonl")


def log_search(source, question, hits, text, ms, cwd=None, session=None):
    """One line per real search — the hook's and mid-task ones, never the
    tests' — so recall's use can be measured in practice, not only on the
    scorecard: how often it runs, what kind of thing comes back, how much."""
    try:
        os.makedirs(os.path.dirname(SEARCHES), exist_ok=True)
        with open(SEARCHES, "a") as fh:
            fh.write(json.dumps({
                "at": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source,
                "session": session, "where": os.path.basename(cwd or ""),
                "words": len(question.split()), "question": question[:300],
                "hits": [{"kind": kind_of(h).split(",")[0], "how": h.get("how"),
                          "score": h.get("score"),
                          "path": os.path.relpath(h["path"], os.path.expanduser("~"))} for h in hits],
                "chars": len(text), "ms": round(ms)}) + "\n")
    except OSError:
        pass                                  # telemetry never breaks a search


FOLLOWUP_WORDS = 8             # a message this short leans on the one before it
LONG_WORDS = 25                # a message this long may ask several things


def parts_of(message):
    """A long message split where it changes topic: sentences, and "and also",
    "second", "separately" and the like."""
    pieces = re.split(r"[.?!;]\s+|,?\s+(?:and also|also|and separately|separately|second(?:ly)?|"
                      r"first(?:ly)?|and then|plus)\s+", message, flags=re.I)
    return [p.strip() for p in pieces if len(p.split()) >= 3]


def search_message(message, previous=None, k=4, **kw):
    """Search for a message as it arrives in a conversation — the hook's way in.

    A short follow-up ("and the blue one?") is searched together with the message
    before it, which is what it means. A long one is searched whole and in its
    parts, results interleaved, so a second question isn't drowned by the
    first."""
    words = len(message.split())
    if FOLLOWUP and previous and words <= FOLLOWUP_WORDS:
        # The new words alone first, then with the message before for context:
        # searched together only, the longer previous message decided, and
        # "and the blue one?" after a question about the red one brought back
        # the red one. Alone, "and the other one?" finds nothing above the
        # floor, so the combined search fills in.
        return interleave([search(message, k=k, **kw), search(f"{previous} {message}", k=k, **kw)], k)
    parts = parts_of(message) if SPLIT and words >= LONG_WORDS else []
    if len(parts) < 2:
        return search(message, k=k, **kw)
    return interleave([search(message, k=k, **kw)] + [search(p, k=k, **kw) for p in parts], k)


def interleave(runs, k):
    """Several searches merged rank by rank, knowledge notes first."""
    out, seen = [], set()
    for rank in range(max(len(r) for r in runs)):
        for r in runs:
            if rank < len(r) and r[rank]["path"] + r[rank]["text"][:60] not in seen:
                seen.add(r[rank]["path"] + r[rank]["text"][:60])
                out.append(r[rank])
    notes = [h for h in out if any(h["path"].startswith(q) for q in KNOWLEDGE)][:NOTES]
    rest = [h for h in out if h not in notes][:k]
    return notes + rest


def kind_of(h):
    """What a passage is, which says how far to trust it."""
    if any(h["path"].startswith(k) for k in KNOWLEDGE):
        return "knowledge, distilled: check the source before acting on it"
    return {"transcript": "said", "git": "commit"}.get(h["kind"], "written")


# Tried and left out, on the scorecard: skipping near-repeat passages
# (no change at any threshold) and six or eight shorter passages instead of four
# (fewer facts found: cutting passages cuts the facts off).
EACH = 900                     # characters per passage delivered


def as_context(hits, each=None):
    """What the hook pastes above a message: labelled, capped, or nothing.

    A knowledge note is one passage and arrives whole; anything else is cut at
    `each` characters."""
    each = each or EACH
    if not hits:
        return ""
    lines = ["Possibly relevant, from what has been written or said before "
             "(recalled automatically; ignore what does not fit):"]
    for h in hits:
        where = (f"{h['project']} commits" if h["kind"] == "git"
                 else os.path.relpath(h["path"], os.path.expanduser("~")))
        text = h["text"] if kind_of(h).startswith("knowledge") else h["text"][:each]
        lines.append(f"\n[{h['date']} · {kind_of(h)} · {where}]\n{text}")
    return "\n".join(lines)


# --- the resident service --------------------------------------------------

def serve():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    model()                                   # pay the ten seconds once
    load()

    def reindexer():
        # In a child process: embedding grows the model's memory pool and never
        # gives it back, so the work is done somewhere that exits afterwards.
        # The service reloads only when the pass changed something.
        while True:
            time.sleep(config().get("every_minutes", 5) * 60)
            try:
                r = subprocess.run([sys.executable, os.path.abspath(__file__), "index", "--quiet"],
                                   capture_output=True, text=True, timeout=3600)
                counts = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
                if r.returncode:
                    print(f"  ! reindex: {r.stderr.strip()[-300:]}", flush=True)
                elif counts.get("added") or counts.get("pruned") or counts.get("files"):
                    load(force=True)
            except Exception as e:
                print(f"  ! reindex: {e}", flush=True)

    threading.Thread(target=reindexer, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/search":
                t = time.time()
                question = q.get("q", [""])[0]
                cwd, session = (q.get("cwd") or [None])[0], (q.get("session") or [None])[0]
                hits = search_message(question, (q.get("previous") or [None])[0],
                                      k=int(q.get("k", ["4"])[0]), prefer=(q.get("prefer") or [None])[0],
                                      cwd=cwd, session=session,
                                      knowledge=(q.get("knowledge") or ["1"])[0] != "0")
                context = as_context(hits)
                if (q.get("source") or ["hook"])[0] != "test":
                    log_search("hook", question, hits, context, (time.time() - t) * 1000, cwd, session)
                body = json.dumps({"hits": hits, "context": context})
            elif url.path == "/health":
                body = json.dumps({"passages": len(_ids)})
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

    print(f"recall: {len(_ids)} passages, listening on {PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def stats():
    con = db()
    for row in con.execute("SELECT kind, project, COUNT(*) FROM passages "
                           "GROUP BY kind, project ORDER BY 3 DESC LIMIT 20"):
        print(f"  {row[2]:>7}  {row[0]:<11} {row[1]}")
    print(f"  {con.execute('SELECT COUNT(*) FROM passages').fetchone()[0]:>7}  total")
    con.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["index"]:
        files, added, pruned = index(verbose="--quiet" not in args)
        if "--quiet" in args:                 # for the service: what changed, as JSON
            print(json.dumps({"files": files, "added": added, "pruned": pruned}))
    elif args[:1] == ["serve"]:
        serve()
    elif args[:1] == ["stats"]:
        stats()
    elif args[:1] == ["search"] and len(args) > 1:
        # Mid-task: what an agent runs instead of grepping. Wider than the hook
        # (six passages, longer), and logged as "mid-task".
        t = time.time()
        question = " ".join(args[1:])
        hits = search(question, k=6, cwd=os.getcwd(),
                      knowledge=not os.environ.get("RECALL_NO_KNOWLEDGE"))   # the memory test
        text = as_context(hits, each=1500)
        log_search("mid-task", question, hits, text, (time.time() - t) * 1000, os.getcwd())
        print(text or "(nothing relevant)")
    else:
        print(__doc__)
