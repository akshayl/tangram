# Tangram

Tangram is a framework for building your own personal agent that learns and evolves as you work together, powered by a coding agent such as Claude Code or Codex. You paste one block into your coding agent, and it builds the agent on your computer with you, one step at a time. It includes:

- **Total Recall**, the agent's memory: the notes it keeps for itself and each project, keyword and vector search over everything you've worked on, and a nightly dream that distils it into knowledge, by subject, with regular checks that keep it current and correct.
- **Signal integration**, with support for voice notes and photos.
- **Email integration**: an inbox of the agent's own, for things you forward and things it sends you.
- **Browser use**: it browses the web headlessly in its own Chrome instance and data folder, with only the logins you give it.
- **A scheduler** for reminders, routines, and background work, including the agent's own upkeep, like the nightly dream. It runs while your computer is awake.
- **A home folder** for the agent: its identity, its rules, and its working memory, in plain text, tracked in Git, and synced to your own private GitHub repository if you choose.
- **Projects**, a folder for each, where the agent keeps track of where things stand.
- **Built-in software updates**: your agent checks for new Tangram releases, explains what's changed, and incorporates improvements you approve while preserving your customizations.

## Quick Start

Paste this into your coding agent:

```text
Install Tangram for me: read https://github.com/akshayl/tangram/blob/main/DESIGN.md
and follow it to build my own personal agent, stopping whenever you need me.
If anything goes wrong, offer to write it up as a "Tangram feedback" email
for me to send.
```

Your coding agent walks you through the entire setup, from choosing a name and customizing its personality and working rules to installing dependencies, configuring integrations, and validating the installation. You provide the preferences, credentials, and account approvals it needs.

**Implementation status:** built and tested with Claude Code on macOS and a Claude subscription. The design supports adaptation to other coding harnesses and operating systems. A Codex adaptation has passed a local functional test; its live integrations (Signal, email, GitHub) are still to be validated.

## Why I Built It

I already use Claude Code for coding and personal projects like my finances, so a personal agent built on it fits naturally into how I work: one agent with shared context across coding, personal projects and everyday tasks. I also wanted a polished experience, control over how it works, the models I choose, and no extra bill on top of my subscription. I built Tangram to bring those benefits together, and enhancing it is how I experiment at the frontier of AI.

**Using what I already pay for.** I already pay for Claude. When I experimented with OpenClaw, my setup incurred model API charges on top of that subscription. Paying twice for access to the same models didn't make sense to me.

**The right context at the right time.** Every new conversation shouldn't require reconstructing earlier decisions. For example, if I worked through something in a personal project in Claude Code, then asked about it later from the Claude app on my phone, it didn't know. Whatever it's doing, and wherever I'm using it, the agent should get the context it needs, from across all my projects, quickly and without my asking. And that context has to be correct and up to date.

**Environment portability.** My Mac is the default, but some tasks could benefit from resources available in the cloud. Choosing another execution environment should preserve my full working context, with access appropriate to that environment.

**Freedom from lock-in.** Switching coding agents or model providers shouldn't mean starting over. If I move from Claude to an OpenAI model, for example, everything the agent has learned and how I've taught it to work with me should come along. Months of accumulated context shouldn't tie me to one company's models or tools.

**Control over behavior and access.** The agent should check claims, disagree when warranted, take initiative, and know when to ask. Corrections should change its future behavior. Monitoring new information and keeping me informed can happen without instructions each time; changing the workflow or deleting tracked items needs my involvement. I also decide which files and accounts it can access, what it can do with them, and where that access is available. Tasks that use my signed-in browser, for example, belong on my own machine.

**Freedom to experiment.** As the field develops, this is a place to experiment with new functionality and approaches and put useful ones to work. Total Recall, the memory system described below, came out of this kind of experimenting. I'm currently experimenting with a Codex agent working alongside my primary Claude Code agent to draw on the combined intelligence of multiple frontier models. Next, I want to try incorporating System One models such as Jev, which make fast, structured decisions, into the agent's core thinking.

## How It Works

### Your Coding Agent as the Foundation

Tangram runs your coding agent headlessly, using its existing tools, permissions, and authenticated account for conversations and background work. Where the CLI supports subscription-backed execution, this reuses your existing subscription without a separate model API bill. You can connect your coding agent to your own email, calendar, and other tools and services as usual, including through MCP. Tangram adds ways to interact with it through Signal and a dedicated email address.

### An Agent That Learns and Adapts

You define how the agent works with you: its personality, how much initiative it takes, how it handles corrections, and when it asks for approval. The agent's personality and working habits evolve as you work together. When you give it feedback, it proposes changes to its behavior and, with your approval, adopts them, since those rules govern every session, including unattended ones.

Each project maintains its status, tasks, and session history, so a new conversation can pick up where the work left off. Behavioral instructions, factual memory, and tasks are maintained separately, making each easier to inspect and correct. You can also ask the coding agent to modify the implementation itself, adding functionality or changing workflows to suit your needs.

### Total Recall: Memory and Maintenance

Total Recall is the agent's memory system. It retains useful context across conversations and projects, retrieves it automatically, and maintains it over time.

**Layered memory.** A compact working-memory file holds immediately useful facts. Longer-lived knowledge is organized by subject into Markdown summaries and focused notes. Project records, conversation transcripts, and execution logs preserve the supporting detail. Keeping the always-loaded layer small leaves room for the task at hand, and the detail is retrieved only when it's relevant.

**Automatic recall.** Before every message, Total Recall's search combines semantic similarity from local vector embeddings with keyword search to retrieve relevant context. You don't have to remember which earlier conversation matters or explicitly ask the agent to search.

Ranking favors curated knowledge, written records, and the current project. Older conversations and commits receive a capped age penalty, reducing the chance of outdated information resurfacing as current. Maintained knowledge isn't demoted simply for being old, and disputed claims receive lower priority. Retrieved notes supplement original evidence rather than displacing it. Relevance thresholds and repetition limits keep results focused, while context already loaded into the session is excluded.

**Nightly learning.** Search alone can return an outdated value as if it were current, and coding agents delete old conversations after a while, so the dream keeps one current, sourced version. It consolidates new conversations, commits, and scheduled work into sourced knowledge. It updates existing notes, replaces superseded facts, and moves durable information out of working memory. Notes record both when a claim was last confirmed and when it was edited, so reorganizing knowledge doesn't make old evidence appear newly verified.

**Ongoing maintenance.** Three jobs keep different parts of the context current:

- **Nightly status cleanup** refreshes active projects' status and task files, removing outdated statements and completed work, since status files drift when a session doesn't save.
- **Weekly knowledge review** revisits neglected subjects, which the dream reaches only when they come up, and checks for duplicates and contradictions across the knowledge collection.
- **Weekly source check** samples saved claims against their evidence, since repeated rewriting can drift from the sources, and triggers a rewrite of affected subjects when it finds errors or superseded facts.

These processes rewrite what the agent knows, unattended, so each can write only to the files it maintains. Changes to behavioral rules are proposed for your approval.

### Messaging and Scheduled Work

Signal lets you work with the agent from your phone, with project-specific chats, photos, and transcribed voice notes. It has the same tools, instructions, and project context as your terminal sessions. Each chat keeps its own session running, so replies start in seconds and a message sent mid-task joins the work in progress. Each night, after the dream, chats start fresh, drawing on memory rather than a long transcript, because a conversation that runs for weeks slows down and compacts in the middle of tasks. Signal was chosen over Telegram for end-to-end encryption.

You can schedule reminders, recurring tasks, and background work, with results delivered through Signal or the agent's email account. Scheduled work needs your computer awake, so keep it plugged in with the lid open. Missed jobs run when it wakes, and monitoring reports service failures. Unattended tasks operate within their agreed scope and permissions.

### Browser Use

The agent runs a separate instance of Chrome with its own data folder, so its logins, cookies, and history are kept apart from any browser you use, and it can reach only the sites you sign it into. You sign in once, and those logins stay on your computer, out of the agent's files and your GitHub repository.

The agent controls Chrome programmatically through Playwright, reading each page as structured text and acting on buttons, links, and fields by name rather than interpreting screenshots, which is faster, more precise, and cheaper. It runs headless by default and opens a visible window when a site blocks headless browsers or needs you for a login or captcha. Where a site offers an API, including the private one its own pages call, the agent reads data from that instead, since data is quicker and less fragile than screens; anything that books or changes something goes through the site's normal pages. Page content is treated as data, so instructions planted in a page (prompt injection) aren't followed.

### Ownership, Continuity, and Recovery

Your agent's instructions, project state, and knowledge live in Markdown files tracked in Git. You can inspect and edit them directly, retain context across sessions, and recover earlier versions.

If you choose, those files sync to your own private GitHub repository, which makes saved context available in other execution environments and coding agents, and keeps it safe if the machine is lost. Credentials and browser sessions remain separate, so transferring context doesn't require transferring the same account access.

### Built-In Updates

Tangram periodically checks for new releases during use. Your agent reads the changelog, explains what's new, and proposes changes for your approval. It then incorporates approved updates around your customizations.

This lets a personalized installation continue receiving improvements without requiring you to track releases and integrate each change yourself.

## What's Here

- [`DESIGN.md`](DESIGN.md) — architecture, required behavior, adaptation points, and validation checks.
- [`SETUP.md`](SETUP.md) — installation instructions, using Claude Code on macOS as the reference implementation.
- [`GUIDE.md`](GUIDE.md) — using the agent after setup.
- [`conventions/`](conventions/) — behavioral instructions and conventions for maintaining project records.
- [`code/`](code/) — messaging, scheduling, email, retrieval, and memory maintenance.
- [`CHANGELOG.md`](CHANGELOG.md) — release changes and adoption instructions for your agent.
- [`LICENSE`](LICENSE) — MIT license.
