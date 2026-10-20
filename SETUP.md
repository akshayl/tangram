# Setting up Tangram

These are instructions for you, Claude Code, to follow with the person who pasted the link. You're going to build them their own personal agent, on this Mac, by talking them through it.

## How to run this

**Talk to them, don't just run commands.** Assume they're not technical. Before anything else, tell them what you're going to do together and list the steps below in plain words. Then take one step at a time. Open each one the way the step's heading does — "Step 4. Let's set up email for your agent." — say what it is and why it's worth having, then do it.

**You do every technical part.** They only do what needs a person: choosing names, signing up for accounts, solving a captcha, reading out a code, choosing a PIN, copying a key. Ask for one thing at a time, and say exactly where to click or what to look for.

**Check each step before the next.** Every step ends with a check you run and a "try it" the person does. If either fails, fix it before moving on — never carry on past a step that quietly didn't work. If you can't fix it, offer to send what you have as the feedback report (see the end) and pause there.

**Keep a log.** Write what you did in each step to `<agent folder>/runs/setup.md` as you go — what was installed, what was configured where, anything that went wrong. It's what lets a later session, or Tangram's author, see what happened.

**Get the files.** Download this repository to a temporary folder first (`git clone https://github.com/akshayl/tangram.git /tmp/tangram`, or the zip from GitHub if git isn't there yet). Paths below like `code/` and `conventions/` mean that copy.

## Before the steps: check, and look at what's here

1. **Check you can run commands on this computer.** Run `sw_vers` and `whoami`. If you can't run commands — you're the Claude app or a website, not Claude Code — tell them this needs Claude Code on their Mac, how to get it (claude.com/claude-code), and stop.
2. **This is built for a Mac.** If `sw_vers` fails, say so and stop.
3. **Look at their existing Claude Code setup** — `~/.claude/CLAUDE.md`, `~/.claude/settings.json`, anything in `~/.claude/commands/` — and tell them in a sentence or two what you found.
4. **Back it up:** copy `~/.claude` to `~/.claude-before-tangram`, and tell them that one command puts it all back if they don't like the result.
5. **Merge, never replace.** Their own instructions stay. Everything Tangram adds lives in the agent's folder, so ordinary Claude Code elsewhere on the Mac stays exactly as it was. The only change outside that folder is one hook added to `~/.claude/settings.json` (Step 5), which does nothing outside the agent's folders.
6. **Tools.** You'll need Homebrew (`brew`). If it's missing, it's the one install that asks for their Mac password: explain that, run the installer from brew.sh, and let them type the password. Then `brew install git uv`.

## Step 1. Let's meet your agent

*What it is:* your own agent — a name, a personality, and a folder where it keeps its notes: where things stand, what's left to do, what it has learned, and a log of every session. Everything in it can be undone.

1. Ask what they'd like their agent to be called, and what you should call them. Then ask them to describe its personality in their own words — how it should talk, how direct or playful it is. Tell them they can change it any time.
2. Ask where they'd like the agent's folder and their projects folder; suggest `~/<Name>` and `~/projects`.
3. Make the agent's folder and fill it:
   - `conventions/` — copy `conventions/habits.md` and `conventions/conventions.md`.
   - `code/` — copy all of `code/`.
   - `CLAUDE.md` from `conventions/templates/agent-CLAUDE.md`, filled in with the name, their name and the personality in their words.
   - `STATUS.md`, `TODO.md`, `MEMORY.md` from the templates; an empty `sessions/`, `runs/` and `knowledge/` (with an `index.md` saying "No subjects yet.").
   - `VERSION` — copy the repository's `VERSION`. This is how updates know what this setup is at.
   - `updates.md`, with a one-line heading.
   - `.gitignore` with `runs/` and `code/.venv/`.
   - `git init`, and commit. Tell them: everything here is saved as it changes, so anything can be undone; they never need to think about it.
4. Copy `conventions/templates/save.md` to `~/.claude/commands/save.md` (unless they already have a `/save`; then ask).
5. Make the projects folder, `git init` it and commit, so every project's changes can be undone too.
6. Write `~/.config/tangram/settings.json`: `{"name": ..., "user": ..., "home": "<agent folder>", "projects": "<projects folder>"}`. If another user on this Mac already runs Tangram (check with `lsof -i :7585 -i :7583`), add `"recall_port"` with a free port, and use a free one for the Signal daemon in Step 2 as well; put both in `watch.json`.
7. Read the habits aloud to them in a sentence each, and ask whether they'd like to drop or change any. Edit `conventions/habits.md` to match.
8. Show them what you just built in a few lines.

**Check:** `claude -p "Who are you, and who do you work for?"` run from inside the agent's folder answers with the agent's name and theirs.

**Try it:** ask them to open a new Claude Code window, `cd` into the agent's folder (give them the exact command), start `claude` and ask "who are you?". Then: "tell your agent to set up a project for something you're working on."

## Step 2. Let's give your agent a phone number on Signal

*What it is:* your agent gets its own number on Signal, so it can message you first — a reminder, a finished job, something that needs you — which it can't do from Claude Code alone. You can message it from your phone too, like any other contact, in a chat that's end-to-end encrypted. It can't use your number: it would speak as you and see all your messages.

1. **A number.** They sign up for a free Google Voice number at voice.google.com with their Google account. Walk them through it. If Google says their mobile number is already linked to another Voice account, Voice won't work for them — say so, note it for the feedback report, and stop here. Tell them: Google takes the number back after three months without a call or text, so the agent will remind them every two months to send one.
2. **Install:** `brew install signal-cli whisper-cpp ffmpeg`. Download the voice model (about 550 MB) to `~/.local/share/whisper/ggml-large-v3-turbo-q5_0.bin` from `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin`.
3. **Register the number with Signal.** Signal wants a captcha first: ask them to open https://signalcaptchas.org/registration/generate.html, solve it, and copy the link the "Open Signal" button gives them (right-click, copy link); then tell you "copied". Read it from the clipboard yourself (`pbpaste`) and run `signal-cli -a <number> register --captcha <link>`.
4. **The code.** Signal texts a code to the Voice number. Ask them to read it from voice.google.com, then run `signal-cli -a <number> verify <code>`.
5. **A PIN.** Ask them to choose a PIN they'll keep somewhere safe — it stops anyone who later gets the number from taking over the account. They type it into a command you give them, so you never see it: `! signal-cli -a <number> setPin <their PIN>`.
6. **A name:** `signal-cli -a <number> updateProfile --name "<agent name>"`.
7. **Config:** ask for their own Signal number (international format, e.g. `+14155550123`) and write `~/.config/tangram/signal/config.json`:
   ```json
   {"account": "AGENT NUMBER", "daemon": "127.0.0.1:7583",
    "people": {"THEIR NUMBER": "THEIR NAME"}}
   ```
8. **The listener:** build the agent's Python (`uv sync` in `<agent folder>/code`), fill `code/launchd/tangram.signal.plist` (replace `{{AGENT}}` and `{{HOME}}` with full paths), copy it to `~/Library/LaunchAgents/`, and `launchctl load` it.

9. **Keeping the Mac awake.** Tell them plainly: the agent only works while the Mac is awake, so keep it **plugged in, with the lid open** — the screen can turn off. The listener keeps the Mac from sleeping while it's on power. On battery, or with the lid shut, the Mac sleeps as usual: messages wait, and are answered when it wakes. If they have an external display, the lid can be shut while it's connected and plugged in.

**Check:** `<agent folder>/runs/signal/listen.log` shows it connected, and `pmset -g assertions` lists `caffeinate` preventing sleep while plugged in.

**Try it:** ask them to add the number as a contact named after the agent and message it "hi" from their phone. They should see 👀, then a reply. Then try a voice note.

## Step 3. Let's let your agent do things on a schedule

*What it is:* reminders and routines — "remind me at 10pm to call Mum", "every Monday at 8, tell me what my week looks like". You never set these up yourself; you just ask your agent, and it adds them to its schedule. It also runs the agent's own upkeep at night.

1. Copy `code/schedule.example.json` to `<agent folder>/schedule.json` and `code/watch.example.json` to `<agent folder>/watch.json`, replacing `~/agent` with the agent's folder and `~/projects` with the projects folder.
2. Fill and load `code/launchd/tangram.schedule.plist` as in Step 2.
3. Remind them: the schedule runs while the Mac is awake — plugged in, lid open, as in Step 2. A job missed while it slept runs when it wakes.

**Check:** `code/.venv/bin/python code/schedule.py list` shows the jobs.

**Try it:** ask them to message the agent on Signal: "send me a fun fact about octopuses in two minutes." It should arrive, labelled as scheduled.

## Step 4. Let's set up email for your agent

*What it is:* your agent gets its own email address. You can forward it things — a receipt, a booking, a document — and ask it to look; it can email you, on request or on a schedule. It only reads its inbox when asked, and it never acts on what someone else's email says.

1. Ask them to sign up at https://console.agentmail.to (free) and create an API key. When they've copied the key, they tell you "copied"; you save it from the clipboard yourself, so it's never pasted into the chat: `mkdir -p ~/.config/tangram/agentmail && pbpaste > ~/.config/tangram/agentmail/key`.
2. Check it works: `curl -s -H "Authorization: Bearer $(cat ~/.config/tangram/agentmail/key)" https://api.agentmail.to/v0/inboxes`.
3. Ask what they'd like the address to be (`<something>@agentmail.to`), and create it: `POST https://api.agentmail.to/v0/inboxes` with `{"username": ..., "display_name": "<agent name>"}`. Common names are often taken; the answer suggests alternatives.
4. Write `~/.config/tangram/agentmail/config.json`: `{"inbox": "<address>", "owners": ["<their own email address>"]}` — the owners are whose email the agent may act on.
5. Add the address to the agent's `MEMORY.md`.

**Check:** `code/.venv/bin/python code/mail.py check` runs without error.

**Try it:** the agent emails them hello; they reply. Then they ask the agent "check your email".

## Step 5. Let's give your agent a memory

*What it is:* Total Recall. Before every message, your agent quietly searches everything it has written and everything you've said to it, and puts the most relevant few passages in front of itself — so it remembers things from other conversations without you repeating them. Every night it "dreams": it reads what happened that day and keeps a tidy set of notes, by subject, of what it has learned. Everything stays on this Mac. It gets better the more you use it.

1. Ask one question: should it include their past Claude Code conversations, or only conversations with the agent from now on? Including them gives it something to remember from day one. If "from now on", write `<agent folder>/recall.json` with `{"since": "<today>"}`.
2. Build the index: `code/.venv/bin/python code/recall.py index`. The first build downloads a small search model and can take a while if there's a lot of history — tell them roughly how long as it goes.
3. Fill and load `code/launchd/tangram.recall.plist` as in Step 2.
4. Add the hook to `~/.claude/settings.json`, keeping everything already there: a `UserPromptSubmit` hook running `<agent folder>/code/.venv/bin/python <agent folder>/code/hook.py`. Explain: it runs for every Claude Code session, but does nothing outside the agent's own folders.
5. The nightly dream and its weekly tidy are already in `schedule.json` from Step 3.

**Check:** `curl -s http://127.0.0.1:7585/health` (or the port you chose) answers; a test search (`code/.venv/bin/python code/recall.py search "<their name>"`) returns passages.

**Try it:** in a new session, ask about something said earlier in a different conversation. Tell them: in a few days the agent will show them its first dream. Schedule a reminder for that with them.

## Finishing

1. Commit everything in the agent's folder.
2. Give them a short summary of what they now have, and point them at `GUIDE.md` for how to use it — read them its headings.
3. Send the feedback report (below).

## The feedback report

This is a pilot: Tangram's author wants to know how setup went. At the end — or wherever setup stops, if it can't go on — write `tangram-feedback.md` in the agent's folder (or the temporary folder, if Step 1 isn't done), from `runs/setup.md` and what you saw: each step, what worked, what didn't, where they got stuck or confused, what you had to work out yourself because these instructions were wrong or unclear, and roughly how long it took. Ask them to add anything in their own words. Then open the file for them and ask them to email it — attached or pasted — to akshay.luther@gmail.com with the subject "Tangram feedback". Never send it yourself.
