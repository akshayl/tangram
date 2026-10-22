# Changelog

Written for your agent, not for you. When Tangram has a newer version than the one in your setup's `VERSION` file, your agent reads the entries since then and proposes each change to you, one at a time; you say yes or no. It records each answer so it never asks twice.

Each entry says **what** changed, **why**, and **how to adopt** it. Code under `code/` is copied as it is. A changed idea — a habit, a convention — is merged around anything you have made your own, never over it. Every entry also says what changes in `GUIDE.md`, so the guide stays true.

## 0.2.0

**Memory is checked against the file on disk, not git's copy.** When the dream empties `MEMORY.md` into knowledge, it checks the file afterwards and puts back any it changed in a way other than deleting whole entries. It used to put back git's copy, which loses any fact written since the last commit. *Adopt:* copy `code/dream.py`. *Guide:* no change.

**Memory files are emptied one at a time.** Two memory files can feed the same subject, and two sessions writing one subject at once can lose an edit. *Adopt:* copy `code/dream.py`. *Guide:* no change.

**A status cleanup runs before the dream.** Each night, for the agent's folder and every project that saw activity, a fresh session brings `STATUS.md` and `TODO.md` up to date from the day: what's no longer true comes out, changed facts are corrected, finished to-dos are deleted. It can edit only those two files; a stale line in a `CLAUDE.md` is reported, never edited. Why: `/save` only helps when a session saves, and even then tends to rewrite only the top block, so status files drift. *Adopt:* copy `code/status_cleanup.py` and `code/dream.py`; raise the dream row's `timeout` in `schedule.json` to 9000. *Guide:* no change.

**Knowledge notes carry two dates, and the user's word wins.** "Last confirmed" is when a source last showed a claim holds; "Edited" is when the note's wording changed. The weekly knowledge review picks stale subjects by Edited. The user's own words outrank any agent's account whatever the dates; only a newer word from them, or something observed (a file, a command's output, a commit), replaces theirs. Why: "newer wins" let an agent's later summary overwrite what the user had decided. *Adopt:* copy `code/dream.py`. Existing notes keep working; their dates are corrected as they're next rewritten. *Guide:* no change.

**Every line in `STATUS.md` must pass one test:** is it true today, and would a session starting cold need it to act? Whoever touches the file deletes what fails, rewrites a section whole, and sets "Last updated". *Adopt:* merge the new paragraph in `conventions/conventions.md` and the new sentence in step 3 of `conventions/templates/save.md` (installed as `~/.claude/commands/save.md`) around any changes the user made. *Guide:* no change.

**The design is written down for any coding agent.** `DESIGN.md` says what each part is for, what must hold however it's built, how to check it, and which parts of `code/` are specific to Claude Code or a Mac. `SETUP.md` stays the tested Claude Code on a Mac implementation; another coding agent follows it and adapts. *Adopt:* nothing to install. If this setup was built on something other than Claude Code on a Mac, read `DESIGN.md`'s "Must hold" lines and check each against what was built. *Guide:* no change.

**An optional private copy on GitHub.** Setup now offers Step 6: private GitHub repositories for the agent's folder and the projects folder. The nightly dream pushes every repository that has a copy on GitHub, after housekeeping and the dream, and never forces; a failed push is reported on Signal. Why: without it, everything the agent learns lives on one Mac, and is lost with it; with it, a new computer or a session elsewhere can start from the same files. *Adopt:* copy `code/dream.py` and merge the new paragraph in `conventions/conventions.md`'s Saving section. Then offer the copy to the user as in `SETUP.md` Step 6; if they decline, nothing else changes. *Guide:* add the "Your Copy on GitHub" section.

**Clearer names, and the source check fixes what it finds.** The weekly audit is now the *source check* and the weekly tidy the *knowledge review*; `dream.py --source-check` and `--review` replace `--audit` and `--tidy`, which still work. A subject with a claim the source check finds wrong is rewritten from its sources the same night, and an ordinary rewrite now settles `Disputed:` lines. The source check no longer messages the user, since there was nothing for them to do; a high error rate goes on the agent's `TODO.md`. *Adopt:* copy `code/dream.py`, `code/status_cleanup.py` and `code/recall.py`; in `schedule.json`, rename the rows `knowledge-audit` and `knowledge-tidy` to `source-check` and `knowledge-review` and their flags to `--source-check` and `--review`. *Guide:* no change.

## 0.1.0

The first version: the agent with its habits and conventions, projects, Signal, the scheduler, email, and Total Recall (search and dreaming). Nothing to adopt — this is what setup installs.
