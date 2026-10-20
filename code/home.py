"""Where everything lives: one agent, one folder.

The agent's folder holds everything that is the agent — its rules, memory,
status, sessions, knowledge, this code, its logs and its job table. Projects
live beside it in their own folder. Keys and settings that must never be
committed live in `~/.config/tangram/`.

Setup writes `~/.config/tangram/settings.json`:

    {"name": "Sprite", "home": "~/Sprite", "projects": "~/projects",
     "user": "Sam", "recall_port": 7585}

`recall_port` is optional. Two setups on one Mac — a second user account, say —
each need their own, since only one can hold a port; the Signal daemon's port
is in its own config for the same reason.

Every script reads its paths from here, so nothing else in the code names a
folder. `TANGRAM_CONFIG` points somewhere else for tests.
"""
import json
import os
import shutil

CONFIG = os.path.expanduser(os.environ.get("TANGRAM_CONFIG") or "~/.config/tangram")


def _settings():
    try:
        with open(os.path.join(CONFIG, "settings.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


SETTINGS = _settings()
NAME = SETTINGS.get("name", "the agent")          # what the agent is called
USER = SETTINGS.get("user", "the user")           # who it works for
AGENT = os.path.expanduser(SETTINGS.get("home") or "~/agent")
PROJECTS = os.path.expanduser(SETTINGS.get("projects") or "~/projects")
CODE = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE = os.path.join(AGENT, "knowledge")
RUNS = os.path.join(AGENT, "runs")                # logs and state worth reading; never committed
# Derived and rebuildable — the search index, timestamps. Under the config
# override when tests set one, so a test never writes the real state.
STATE = (os.path.join(CONFIG, "state") if os.environ.get("TANGRAM_CONFIG")
         else os.path.expanduser("~/.local/state/tangram"))
TRANSCRIPTS = os.path.expanduser("~/.claude/projects")
CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
REPOS = [AGENT, PROJECTS]                         # what git news and dreaming read
RECALL_PORT = int(SETTINGS.get("recall_port") or 7585)


def secret(*parts):
    """A path under the private config folder."""
    return os.path.join(CONFIG, *parts)
