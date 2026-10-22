# Changelog

Written for your agent, not for you. When Tangram has a newer version than the one in your setup's `VERSION` file, your agent reads the entries since then and proposes each change to you, one at a time; you say yes or no. It records each answer so it never asks twice.

Each entry says **what** changed, **why**, and **how to adopt** it. Code under `code/` is copied as it is. A changed idea — a habit, a convention — is merged around anything you have made your own, never over it. Every entry also says what changes in `GUIDE.md`, so the guide stays true.

## 0.2.0

**Memory is checked against the file on disk, not git's copy.** When the dream empties `MEMORY.md` into knowledge, it checks the file afterwards and puts back any it changed in a way other than deleting whole entries. It used to put back git's copy, which loses any fact written since the last commit. *Adopt:* copy `code/dream.py`. *Guide:* no change.

**Memory files are emptied one at a time.** Two memory files can feed the same subject, and two sessions writing one subject at once can lose an edit. *Adopt:* copy `code/dream.py`. *Guide:* no change.

**Housekeeping runs before the dream.** Each night, for the agent's folder and every project that saw activity, a fresh session brings `STATUS.md` and `TODO.md` up to date from the day: what's no longer true comes out, changed facts are corrected, finished to-dos are deleted. It can edit only those two files; a stale line in a `CLAUDE.md` is reported, never edited. Why: `/save` only helps when a session saves, and even then tends to rewrite only the top block, so status files drift. *Adopt:* copy `code/housekeeping.py` and `code/dream.py`; raise the dream row's `timeout` in `schedule.json` to 9000. *Guide:* no change.

**Knowledge notes carry two dates, and the user's word wins.** "Last confirmed" is when a source last showed a claim holds; "Edited" is when the note's wording changed. The weekly tidy picks stale subjects by Edited. The user's own words outrank any agent's account whatever the dates; only a newer word from them, or something observed (a file, a command's output, a commit), replaces theirs. Why: "newer wins" let an agent's later summary overwrite what the user had decided. *Adopt:* copy `code/dream.py`. Existing notes keep working; their dates are corrected as they're next rewritten. *Guide:* no change.

**Every line in `STATUS.md` must pass one test:** is it true today, and would a session starting cold need it to act? Whoever touches the file deletes what fails, rewrites a section whole, and sets "Last updated". *Adopt:* merge the new paragraph in `conventions/conventions.md` and the new sentence in step 3 of `conventions/templates/save.md` (installed as `~/.claude/commands/save.md`) around any changes the user made. *Guide:* no change.

## 0.1.0

The first version: the agent with its habits and conventions, projects, Signal, the scheduler, email, and Total Recall (search and dreaming). Nothing to adopt — this is what setup installs.
