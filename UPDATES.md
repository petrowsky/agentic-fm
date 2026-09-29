# Keeping agentic-fm Up to Date

## Recent Changes

### Windows clipboard + companion path fixes (2026-09-29)

**Action: optional on macOS; recommended on Windows.**

1. **fmxmlsnippet XML** in `filemaker/agentic-fm.xml` now uses ASCII `<>` instead of Unicode `≠`, so Windows clipboard pastes do not corrupt inequality checks (FileMaker was observed wrapping mojibake `â‰` calculations in `/* ... */`).
2. **`Get agentic-fm path`** re-prompts for the folder when the stored path does not contain `fmparse.sh`, instead of only showing an error.
3. **`Explode XML`** falls back to a manual `/C:/...` → `/c/...` path conversion when `ConvertFromFileMakerPath(...; 1)` returns `?` on Windows.
4. **Companion server** resolves Desktop/export paths on native Windows (not only WSL2), respects `repo_path_override` / `export_dir_override` for invalid paths, and forces UTF-8 for `fmparse`/`fmcontext` subprocesses.

**If you develop on Windows:** copy `agent/config/automation.json.example` to `agent/config/automation.json` and set `repo_path_override` / `export_dir_override` to your local paths, then re-paste the `agentic-fm` script folder from the updated `filemaker/agentic-fm.xml`.

---

### Companion config — `agent/config/companion.json` (2026-07-15, v0.8.1)


**Action: optional — existing setups keep working with no changes.**

The companion server's host, port, and advertise address now resolve from a single optional file, `agent/config/companion.json`.
It is the single source of truth so the server and every client (`deploy.py`, tests) agree on one address.

You only need to create it if you run the companion on a **non-default port or host**, or you want the address consolidated in one place.
Copy `agent/config/companion.json.example` to `agent/config/companion.json`, then edit `companion.port` / `companion.advertise_host` to match your setup.

**If you do nothing:** the server falls back to built-in defaults (bind `127.0.0.1`, port `8765`, advertise `local.hub`), and any existing `companion_url` in your `automation.json` is still honored during the deprecation window.
`companion.json` is gitignored, like `automation.json`.

**How to tell if it applies to you:** you previously passed `--port`, set `COMPANION_BIND_HOST`, or edited `companion_url` in `automation.json`. If none of those, you are on defaults and no action is needed.

---

### Context() custom function v2 (2026-03-27)

**Action required**: Update the `Context()` custom function in your FileMaker solution.

The `Context()` function has been updated to v2 with three fixes:

1. **Tables keyed by TO name** — previously keyed by base table name, causing entries to be overwritten when multiple table occurrences share the same base table. This broke field resolution for the layout's own TO.
2. **Script/layout names with brackets** — names containing `[` or `]` (e.g. `"Prepare for Distribution [OLD]"`) broke `JSONSetElement`'s path parser, causing the entire scripts or layouts section to be empty `{}`. Now built via string concatenation.
3. **Version tracking** — output now includes `"context_version": 2` so tools can detect outdated functions.

**How to update**: Copy the updated function from `agent/sandbox/context.fmfn` into your solution's custom function editor, replacing the existing `Context` function. Then run **Push Context** to regenerate `CONTEXT.json`.

**How to tell if you're affected**: If the webviewer status bar shows "Context() outdated — update to v2", or if your `CONTEXT.json` has empty `scripts: {}` or `layouts: {}`, you need to update.

---

agentic-fm is actively maintained. New features, fixes, and FileMaker catalog improvements are added regularly. Because you downloaded the project as a folder of files on your computer, those updates do not reach you automatically — you need to pull them in manually.

This guide walks you through how to know when an update is available and how to apply it, even if you are not familiar with git or the command line.

---

## The short version

If you use **Claude Code or another AI IDE** (Cursor, VS Code), the AI agent will check for updates automatically when you start a new session and tell you what to do. You do not need to set anything up.

If you want an extra safety net, follow **Option 2** below to watch the project on GitHub so you get an email whenever changes are pushed.

---

## Option 1 — The AI agent checks for you (no setup required)

This is already built in. Every time you open a new Claude Code session and send your first message, the agent quietly checks whether your copy of agentic-fm is up to date. If it is not, it will pause and tell you before doing anything else:

> **agentic-fm update available** — your copy is 3 commit(s) behind the latest version. Run `git pull --ff-only` in your agentic-fm folder to update, then restart your session.

When you see this message, open your terminal, navigate to your agentic-fm folder, and run:

```bash
git pull --ff-only
```

Then close and reopen your Claude Code session. That is all.

> If you are not sure how to open a terminal or navigate to the folder, see [How to open a terminal in your agentic-fm folder](#how-to-open-a-terminal-in-your-agentic-fm-folder) at the bottom of this page.

---

## Option 2 — Get an email when changes are pushed

This is the easiest way to stay informed without doing anything on your computer. GitHub can send you an email whenever new commits are pushed to the main branch — agentic-fm does not use versioned releases, so watching for pushes is the right trigger.

**Steps:**

1. Create a free GitHub account at [github.com](https://github.com) if you do not already have one
2. Go to the agentic-fm project page: [github.com/petrowsky/agentic-fm](https://github.com/petrowsky/agentic-fm)
3. Click the **Watch** button near the top right of the page (it looks like an eye icon)
4. Select **All Activity** from the dropdown menu that appears
5. Click **Apply**

You will now receive an email notification whenever changes are pushed to the project.

**To control where notifications go:**

1. Click your profile picture in the top right corner of GitHub
2. Click **Settings**
3. Click **Notifications** in the left sidebar
4. Under **Email notification preferences**, make sure your email address is confirmed

---

## Option 3 — Check manually before you start working

If you prefer to check on your own schedule, you can run a simple command in the terminal before starting a session.

Open a terminal in your agentic-fm folder (see below if you need help with this), then run:

```bash
git fetch origin --quiet && git status
```

If your copy is up to date, you will see:

```
On branch main
Your branch is up to date with 'origin/main'.
```

If an update is available, you will see:

```
Your branch is behind 'origin/main' by 2 commits, and can be fast-forwarded.
  (use "git pull" to update your local branch)
```

To apply the update, run:

```bash
git pull --ff-only
```

---

## How to apply an update

Whenever you are told an update is available — whether by the AI agent, a GitHub email, or a manual check — applying it is always the same two steps:

**1. Open a terminal in your agentic-fm folder** (see below)

**2. Run:**

```bash
git pull --ff-only
```

That's it. Your files will be updated to the latest version. If the companion server is running, stop it and restart it after pulling.

---

## How to open a terminal in your agentic-fm folder

**In VS Code, Cursor, or any compatible IDE:**

If you already have agentic-fm open as your project folder, the easiest option is to use the built-in terminal — it opens directly in the project root. Go to **Terminal > New Terminal** in the menu bar.

**On macOS:**

1. Open **Finder** and navigate to your agentic-fm folder
2. Right-click (or Control-click) the folder
3. Select **New Terminal at Folder** from the menu

> If you do not see "New Terminal at Folder", go to **System Settings > Privacy & Security > Extensions > Finder Extensions** and enable **Terminal**.

Alternatively, open the **Terminal** app (search for it in Spotlight with `Cmd+Space`), then type:

```bash
cd /path/to/your/agentic-fm
```

Replace `/path/to/your/agentic-fm` with the actual location. For example, if it is in your home folder:

```bash
cd ~/agentic-fm
```

**On Windows (if using WSL or Git Bash):**

Open **Git Bash**, then navigate to the folder:

```bash
cd /c/Users/YourName/agentic-fm
```

---

## Something went wrong

If `git pull --ff-only` gives an error, the most common cause is that you have made changes to files in the agentic-fm folder. Run these two commands to set your changes aside, pull the update, and then restore them:

```bash
git stash
git pull --ff-only
git stash pop
```

If you see a message about conflicts after `git stash pop`, the safest option is to ask the AI agent to help you resolve them.
