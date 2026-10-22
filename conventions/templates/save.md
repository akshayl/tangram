---
description: Save context — make it safe to stop here
---

The user may stop the session right after this, so leave the work safe to walk away from. `conventions.md` holds the rules; this is the order to apply them in.

1. **Check before you write.** `git status` — never commit a diff you did not make. `git log` — if commits arrived from another session, read them. If one moves something this session's conclusions rest on, say so and let the user make the call.

2. **Write the session file.** `sessions/YYYY-MM-DD-HHMM-<slug>.md`, never edited once written. If this session already saved once, write a second file with the later timestamp and say which one it continues.

   Record what was decided and why, what was tried and rejected and why — discarded approaches are the part a later session would otherwise re-propose. Say plainly what was left unfinished.

3. **Rewrite `STATUS.md`.** The **Resume Here** block: what was being done, the immediate next step, anything half-finished. Point at `TODO.md` rather than repeating it. Then read the whole file and apply `conventions.md`'s test to every line: delete what fails, rewrite changed sections whole, set "Last updated". If another session has edited this file, add to its work rather than over it.

4. **Update `TODO.md`.** Delete what got done — don't strike it through. Add what this session opened. If nothing changed, leave it and say so.

5. **Update `MEMORY.md`** only for durable facts you could not re-derive from the repo. Not behaviour, not status, not work still to do. Update the entry that already covers something rather than adding a second.

6. **Commit.** Once per decision made or thing built. If the user has chosen to keep a copy on GitHub, push too.

Then say what you saved, what you deliberately did not, and anything still open.
