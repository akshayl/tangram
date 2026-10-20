# Tangram

Tangram is a framework for building your own personal agent on Claude Code. You paste one block into Claude Code, and it builds the agent on your Mac with you, one step at a time. It includes:

- **A home folder** for the agent: its identity, its rules, and its working memory, in plain text.
- **Projects**, a folder for each, where the agent keeps track of where things stand.
- **Total Recall**, the agent's long-term memory: keyword and vector search over everything you've worked on, and a nightly dream that distils it into knowledge, by subject.
- **A scheduler** for reminders, routines, and background work, including the agent's own upkeep, like the nightly dream.
- **Signal integration**, with support for voice notes and photos.
- **Email integration**: an inbox of the agent's own, for things you forward and things it sends you.

Everything runs on your Mac, and only while it's awake.

## Quick Start

Paste this into Claude Code once:

```text
Install Tangram for me: read https://github.com/akshayl/tangram/blob/main/SETUP.md
and follow it to build my own personal agent, stopping whenever you need me.
If anything goes wrong, offer to write it up as a "Tangram feedback" email
for me to send.
```

The agent explains each step before taking it and does the technical work. You choose its name and personality, and handle the parts that need a person: signing up for a phone number and an email inbox, and entering a code or two.

Requires macOS and a Claude subscription that includes Claude Code.

## What's Here

- `SETUP.md` — the instructions your agent follows to build it.
- `GUIDE.md` — how to use it once it's built.
- `conventions/` — how the agent behaves and what it writes down, each with why.
- `code/` — the Signal listener, the scheduler, email, and Total Recall.
- `CHANGELOG.md` — what changed in each version, written for your agent, which proposes each change to you.
- `LICENSE` — MIT.
