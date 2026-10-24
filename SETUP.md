# Setting Up Tangram

These are instructions for you, their coding agent, to follow with the person who pasted the link. You're going to build them their own personal agent, on their computer, by talking them through it.

They're written for Claude Code on a Mac, the implementation that has been built and tested. **If you're a different coding agent, or on another system,** read `DESIGN.md` first, then follow these steps anyway and adapt each one to yours, keeping what `DESIGN.md` says must hold. Use the supplied code wherever it fits.

## How to Run This

**Talk to them, don't just run commands.** Assume they're not technical. Before anything else, tell them what you're going to do together and list the steps below in plain words. Then take one step at a time. Open each one the way the step's heading does — "Step 4. Let's set up email for your agent." — say what it is and why it's worth having, then do it.

**You do every technical part.** They only do what needs a person: choosing names, signing up for accounts, solving a captcha, reading out a code, choosing a PIN, copying a key. Ask for one thing at a time, and say exactly where to click or what to look for.

**Check each step before the next.** Every step ends with a check you run and a "try it" the person does. If either fails, fix it before moving on — never carry on past a step that quietly didn't work. If you can't fix it, offer to send what you have as the feedback report (see the end) and pause there.

**Keep a log.** Write what you did in each step to `<agent folder>/runs/setup.md` as you go — what was installed, what was configured where, anything that went wrong. It's what lets a later session, or Tangram's author, see what happened.

**Get the files.** Download this repository to a temporary folder first (`git clone https://github.com/akshayl/tangram.git /tmp/tangram`, or the zip from GitHub if git isn't there yet). Paths below like `code/` and `conventions/` mean that copy.

## Before the Steps: Check, and Look at What's Here

1. **Check you can run commands on this computer.** Run `whoami`. If you can't run commands — you're a chat app or a website, not a coding agent — tell them this needs a coding agent on their computer, such as Claude Code (claude.com/claude-code), and stop.
2. **Check what you're building on.** `sw_vers` says whether this is a Mac. If you're Claude Code on a Mac, carry on. Otherwise, check `DESIGN.md`'s list of what it needs from a coding agent, tell them what you'll adapt and anything that won't work here, and ask before starting.
3. **Look at their existing Claude Code setup** — `~/.claude/CLAUDE.md`, `~/.claude/settings.json`, anything in `~/.claude/commands/` — and tell them in a sentence or two what you found.
4. **Back it up:** copy `~/.claude` to `~/.claude-before-tangram`, and tell them that one command puts it all back if they don't like the result.
5. **Merge, never replace.** Their own instructions stay, and ordinary Claude Code sessions outside the agent's folders behave exactly as before. Tell them where things go. The agent itself — its rules, notes, memory, knowledge and code — lives in its folder, and their projects in the projects folder. Outside those: keys and settings in `~/.config/tangram/`; a hook in `~/.claude/settings.json` (Step 5) and a `/save` command in `~/.claude/commands/` (Step 1), both of which do nothing outside the agent's folders; the search index in `~/.local/state/tangram/`; background services in `~/Library/LaunchAgents/`; and tools from Homebrew, with Signal's keys and the voice model under `~/.local/share/`.
6. **Tools.** You'll need Homebrew (`brew`). If it's missing, it's the one install that asks for their Mac password: explain that, run the installer from brew.sh, and let them type the password. Then `brew install git uv`.

## Step 1. Let's Meet Your Agent

*What it is:* your own agent — a name, a personality, and a folder where it keeps its notes: where things stand, what's left to do, what it has learned, and a log of every session. Everything in it can be undone.

1. Ask what they'd like their agent to be called, and what you should call them. Then ask them to describe its personality in their own words — how it should talk, how direct or playful it is. Tell them they can change it any time.
2. Ask where they'd like the agent's folder and their projects folder; suggest `~/<Name>` and `~/projects`.
3. Make the agent's folder and fill it:
   - `conventions/` — copy `conventions/habits.md` and `conventions/conventions.md`.
   - `code/` — copy all of `code/`.
   - `CLAUDE.md` from `conventions/templates/agent-CLAUDE.md`, filled in with the name, their name and the personality in their words.
   - `STATUS.md`, `TODO.md`, `MEMORY.md` from the templates, every `{{…}}` placeholder filled; an empty `sessions/`, `runs/` and `knowledge/` (with an `index.md` saying "No subjects yet.").
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

## Step 2. Let's Give Your Agent a Phone Number on Signal

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

## Step 3. Let's Let Your Agent Do Things on a Schedule

*What it is:* reminders and routines — "remind me at 10pm to call Mum", "every Monday at 8, tell me what my week looks like". You never set these up yourself; you just ask your agent, and it adds them to its schedule. It also runs the agent's own upkeep at night.

1. Copy `code/schedule.example.json` to `<agent folder>/schedule.json` and `code/watch.example.json` to `<agent folder>/watch.json`, replacing `~/agent` with the agent's folder and `~/projects` with the projects folder.
2. Fill and load `code/launchd/tangram.schedule.plist` as in Step 2.
3. Remind them: the schedule runs while the Mac is awake — plugged in, lid open, as in Step 2. A job missed while it slept runs when it wakes.

**Check:** `code/.venv/bin/python code/schedule.py list` shows the jobs.

**Try it:** ask them to message the agent on Signal: "send me a fun fact about octopuses in two minutes." It should arrive, labelled as scheduled.

## Step 4. Let's Set Up Email for Your Agent

*What it is:* your agent gets its own email address. You can forward it things — a receipt, a booking, a document — and ask it to look; it can email you, on request or on a schedule. It only reads its inbox when asked, and it never acts on what someone else's email says.

1. Ask them to sign up at https://console.agentmail.to (free) and create an API key. When they've copied the key, they tell you "copied"; you save it from the clipboard yourself, so it's never pasted into the chat: `mkdir -p ~/.config/tangram/agentmail && pbpaste > ~/.config/tangram/agentmail/key`.
2. Check it works: `curl -s -H "Authorization: Bearer $(cat ~/.config/tangram/agentmail/key)" https://api.agentmail.to/v0/inboxes`.
3. Ask what they'd like the address to be (`<something>@agentmail.to`), and create it: `POST https://api.agentmail.to/v0/inboxes` with `{"username": ..., "display_name": "<agent name>"}`. Common names are often taken; the answer suggests alternatives.
4. Write `~/.config/tangram/agentmail/config.json`: `{"inbox": "<address>", "owners": ["<their own email address>"]}` — the owners are whose email the agent may act on.
5. Add the address to the agent's `MEMORY.md`.

**Check:** `code/.venv/bin/python code/mail.py check` runs without error.

**Try it:** the agent emails them hello; they reply. Then they ask the agent "check your email".

## Step 5. Let's Give Your Agent a Memory

*What it is:* the rest of Total Recall, your agent's memory. Step 1 gave it notes; now, before every message, your agent quietly searches everything it has written and everything you've said to it, and puts the most relevant few passages in front of itself — so it remembers things from other conversations without you repeating them. Every night it "dreams": it reads what happened that day and keeps a tidy set of notes, by subject, of what it has learned. Everything stays on this Mac. It gets better the more you use it.

1. Ask one question: should it include their past Claude Code conversations, or only conversations with the agent from now on? Including them gives it something to remember from day one. If "from now on", write `<agent folder>/recall.json` with `{"since": "<now>"}`, the time in UTC to the second (`date -u +%Y-%m-%dT%H:%M:%SZ`): conversations before it, and conversations outside the agent's folders, stay out.
2. Build the index: `code/.venv/bin/python code/recall.py index`. The first build downloads a small search model and can take a while if there's a lot of history — tell them roughly how long as it goes.
3. Fill and load `code/launchd/tangram.recall.plist` as in Step 2.
4. Add the hook to `~/.claude/settings.json`, keeping everything already there: a `UserPromptSubmit` hook running `<agent folder>/code/.venv/bin/python <agent folder>/code/hook.py`. Explain: it runs for every Claude Code session, but does nothing outside the agent's own folders.
5. The nightly status cleanup and dream, and the weekly source check and knowledge review, are already in `schedule.json` from Step 3.

**Check:** `curl -s http://127.0.0.1:7585/health` (or the port you chose) answers; a test search (`code/.venv/bin/python code/recall.py search "<their name>"`) returns passages.

**Try it:** in a new session, ask about something said earlier in a different conversation. Tell them: in a few days the agent will show them its first dream. Schedule a reminder for that with them.

## Step 6. Let's Keep a Copy on GitHub (Optional)

*What it is:* a private copy of your agent's folder and your projects on GitHub, updated every night. Without it, everything your agent learns lives only on this Mac: if the Mac is lost or breaks, it's gone. With it, a new computer can be set up from the copy, and your agent can one day work from somewhere else — another machine, or the cloud — knowing what it knows here. It needs a free GitHub account, and takes about five minutes.

1. Ask whether they'd like it. If not, note it in `runs/setup.md` and tell them they can ask the agent for it any time; skip to Step 7.
2. **An account.** If they don't have one, they sign up at github.com (free). Walk them through it.
3. **Sign in.** `brew install gh`. Signing in needs them, so give them this to type, and tell them it opens a browser where they approve with a code it shows: `! gh auth login --web --git-protocol https --hostname github.com --skip-ssh-key`. Then run `gh auth setup-git` yourself.
4. **What goes up.** Tell them plainly: everything in the agent's folder and the projects folder goes into the copy — notes, memory, knowledge, session files, and any documents they keep in a project. Keys and passwords don't: they live in `~/.config/tangram/`, outside both. If there's anything in a project they'd rather not have on GitHub, even privately, add it to that folder's `.gitignore` now.
5. **Make the copies.** Ask what to call them; suggest `<agent name>` and `<agent name>-projects`. Then `gh repo create <name> --private --source=<agent folder> --push`, and the same for the projects folder. Both must be **private**.
6. **Keeping it current needs nothing more.** The nightly dream pushes both after it runs, and `/save` pushes too. A push never overwrites what's on GitHub; if the copy has changes this Mac doesn't, the agent tells them on Signal.

**Check:** `gh repo view <name> --json visibility` says `PRIVATE` for both, and `git status -sb` in each folder shows it level with GitHub.

**Try it:** they open github.com, find the repository, and see their agent's notes there — visible only to them.

## Step 7. Let's Let Your Agent Use a Browser (Optional)

*What it is:* your agent can look things up and do errands on websites — a barber's open times, a restaurant's availability, a price — in its own Chrome instance and data folder, with only the logins you give it. Your own browser and its accounts stay yours. It asks before buying, booking, submitting or sending anything. Ask whether they'd like it; if not, note it and skip to Finishing.

1. **Chrome and Node.** Check Google Chrome is in `/Applications`; if not, ask them to install it from google.com/chrome. `brew install node` if `npx` is missing.
2. **Its own Chrome data folder:** `mkdir -p ~/.config/tangram/chrome ~/.local/state/tangram/browser`. Chrome keeps logins, cookies and history in a data folder; giving the agent its own makes a separate Chrome instance, apart from any browser they use. Explain that it isn't a profile in their Chrome and won't appear in Chrome's profile picker.
3. **The browser tool, for every session**, in two versions: one with no window, the default, and one that opens a window, for sites that block a hidden browser or need them to sign in.
   `claude mcp add --scope user browser -- npx -y @playwright/mcp@0.0.83 --headless --user-data-dir ~/.config/tangram/chrome --executable-path "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --output-dir ~/.local/state/tangram/browser`
   and the same again named `browser-window`, without `--headless`. The output folder keeps page snapshots out of their folders.
4. **Logins.** Explain: for a site that needs an account, they sign in once in the agent's Chrome, and it stays signed in there, on this Mac — never in the agent's folder or on GitHub. Open it for them with `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --user-data-dir=$HOME/.config/tangram/chrome <site>`; they sign in and close the window. Only what they sign into is open to the agent. They can do this any time; the agent can open it for them.
5. **Tell them what to expect.** While a task runs with no window, a Chrome icon may show in the Dock and look frozen; it isn't — leave it. Upkeep runs at night never get the browser.
6. Restart the Signal listener so chats have the tool: `launchctl kickstart -k gui/$(id -u)/tangram.signal`.

**Check:** `claude mcp list` shows `browser` and `browser-window` connected, and a fresh session asked for the title of example.com reads it with the browser tool.

**Try it:** from their phone, ask for something only a website knows — when a local shop opens tomorrow, or a table for two on Saturday. It should find it, and ask before booking anything.

## Finishing

1. Commit everything in the agent's folder.
2. Give them a short summary of what they now have, and point them at `GUIDE.md` for how to use it — read them its headings.
3. Send the feedback report (below).

## The Feedback Report

This is a pilot: Tangram's author wants to know how setup went. At the end — or wherever setup stops, if it can't go on — write `tangram-feedback.md` in the agent's folder (or the temporary folder, if Step 1 isn't done), from `runs/setup.md` and what you saw: each step, what worked, what didn't, where they got stuck or confused, what you had to work out yourself because these instructions were wrong or unclear, and roughly how long it took. Ask them to add anything in their own words. Then open the file for them and ask them to email it — attached or pasted — to akshay.luther@gmail.com with the subject "Tangram feedback". Never send it yourself.
