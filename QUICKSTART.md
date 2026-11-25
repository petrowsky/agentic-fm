# agentic-fm 🗄️ » 📂 » 🧠 Quickstart

## ⚡ Fastest way to get started

Clone the repo, open it in your AI coding tool, and prompt:

> **"Help me set up agentic-fm"**

The `/setup` skill detects what's already configured, checks each dependency, and walks you through the remaining steps interactively — no need to read ahead. Everything below is the manual reference if you prefer to do it yourself.

---

## 🔀 Two ways to work

agentic-fm supports two complementary workflows — choose based on how you prefer to interact with AI:

**🌐 Webviewer** — A visual three-panel script editor (Monaco + AI chat) that runs in your browser and can be embedded directly inside a FileMaker Web Viewer. The HR-to-XML conversion happens automatically in the browser. Recommended starting point for FileMaker developers not familiar with the CLI or an IDE. See [Webviewer Setup](#webviewer-setup) below.

**🖥️ CLI / IDE** — Claude Code, Cursor, VS Code, or any terminal-based agent. The agent reads `agent/CONTEXT.json`, generates `fmxmlsnippet` XML in `agent/sandbox/`, validates it, and loads it onto the clipboard ready to paste into FileMaker's Script Workspace. This is the most powerful path — CLI agents have access to the full skill set, deeper context awareness, and tighter feedback loops.

---

## 💡 What this does

agentic-fm gives an AI agent structured knowledge of your FileMaker solution — schema, scripts, layouts, relationships — so it can generate reliable `fmxmlsnippet` code that pastes directly into the Script Workspace. You describe what you want; the agent writes the XML; you paste it into FileMaker.

---

## ✅ Prerequisites

1. **FileMaker Pro 19.0+** — 21.0+ for the full `Context.fmfn`. On 19.x/20.x install `filemaker/Context19.fmfn` instead (no `GetTableDDL`; see `filemaker/README.md`)
2. **fm-xml-export-exploder** — Rust binary that parses FileMaker XML exports. Download from [GitHub releases](https://github.com/bc-m/fm-xml-export-exploder/releases/latest)
3. **Python 3** — macOS ships Python 3 at `/usr/bin/python3`. For a newer version: `brew install python`
4. **Node.js 18+** — required only for the webviewer path, not for CLI/IDE-only usage. Install from [nodejs.org](https://nodejs.org) or via `brew install node`
5. **Your AI agent of choice** — Claude Code, Cursor, VS Code + Copilot, etc. (CLI/IDE path only)

> **Python virtual environment**: Only needed if you plan to run `agent/docs/filemaker/fetch_docs.py` to fetch Claris reference documentation. That script auto-installs `requests` and `beautifulsoup4` on first run via pip. The core scripts (`clipboard.py`, `validate_snippet.py`, `companion_server.py`) use the Python standard library only — no venv required.

---

## 📦 Install

### 1. Clone the repo

```bash
git clone https://github.com/petrowsky/agentic-fm.git
cd agentic-fm
```

### 2. Install fm-xml-export-exploder

```bash
mkdir -p ~/bin
mv ~/Downloads/fm-xml-export-exploder ~/bin/
chmod +x ~/bin/fm-xml-export-exploder
```

> If you want the bleeding edge changes to this tool, then you can find them [here](https://github.com/petrowsky/fm-xml-export-exploder/releases)

On first run, macOS Gatekeeper will block it. Right-click the binary in Finder and choose **Open** once to clear the restriction.

### 3. Verify Python 3

```bash
python3 agent/scripts/clipboard.py --help
```

---

## 🗄️ One-time FileMaker setup

Do this once per solution. Follow the steps in order — each item may reference the one before it.

### 1. Install the Context custom function

Custom functions must be installed first because field calculations and scripts may call them by name.

1. Open your solution in FileMaker Pro
2. Go to **File > Manage > Custom Functions**
3. Click **New**, name it `Context`, add one parameter named `task` (Text)
4. Paste the contents of `filemaker/Context.fmfn` into the calculation editor
5. Click **OK**

### 2. Install the companion scripts

With the custom function in place, install the **agentic-fm** script folder. Choose either option:

**Option A — Open the included .fmp12 file (fastest)**

`filemaker/agentic-fm.fmp12` is a pre-built FileMaker file that already contains the **agentic-fm** script folder. Open it in FileMaker, then copy and paste the **agentic-fm** script folder directly into your solution's Script Workspace. This is the quickest path for any FileMaker developer.

**Option B — Install via clipboard**

```bash
python3 agent/scripts/clipboard.py write filemaker/agentic-fm.xml
```

Switch to FileMaker, open **Scripts > Script Workspace**, click in the script list, and press **Cmd+V**. A folder named **agentic-fm** with the companion scripts will appear.

### ⚙️ Configure the repo path

Run **Get agentic-fm path** from the Scripts menu. A folder picker appears — select the root of this repo. The path is stored in `$$AGENTIC.FM` for the session.

> **Note:** `$$AGENTIC.FM` is a global variable and is cleared whenever the FileMaker file is closed. You'll need to run **Get agentic-fm path** again each session — or add a call to it in your solution's startup script so it runs automatically on launch. Any script that requires the path will also prompt you to set it if it is not yet populated.

### 🖥️ Start the companion server

The companion server is a lightweight HTTP server that several FileMaker scripts (Explode XML, Agentic-fm Debug, Agentic-fm webviewer) call via `Insert from URL`. Open a terminal and keep it running while you work:

```bash
python3 agent/scripts/companion_server.py
```

The server listens on port 8765 by default.

### 💥 Explode the XML

Run the **Explode XML** script. This exports your solution's XML and populates `agent/xml_parsed/`. Re-run it any time the schema or scripts change and you want an agent to reference those changes.

---

## 🌐 Webviewer Setup

The webviewer is a visual three-panel editor (script list + Monaco editor + AI chat) that runs in your browser and can be embedded directly in a FileMaker Web Viewer for an integrated experience.

> **Requirements:** Node.js 18+

### ▶️ Start the companion server first

The companion server must be running before you launch the webviewer — it handles all communication between FileMaker and the agent. Open a terminal tab and keep it running while you work:

```bash
python3 agent/scripts/companion_server.py
```

### Launch the webviewer

**From FileMaker (easiest):** Run the **Agentic-fm webviewer** script from the Scripts menu. It installs Node dependencies and starts the dev server automatically, then confirms the URL.

**From the terminal:**

```bash
cd webviewer
npm install
npm run dev
# Open http://localhost:8080
```

Configure your AI provider (Anthropic API key, OpenAI API key, or Claude Code CLI) in the webviewer settings panel. See the [Webviewer page](https://agentic-fm.com/webviewer/) for full embedding instructions and AI provider details.

---

## 🔄 Every session (CLI / IDE)

Each time you sit down to write scripts from the CLI or IDE:

1. **🗺️ Navigate** to the layout you are working on in FileMaker
2. **📤 Run "Push Context"** from the Scripts menu — enter a plain-English task description when prompted. This writes `agent/CONTEXT.json` with the fields, layouts, relationships, and scripts scoped to your current task.
3. **💬 In your CLI or IDE**, open the agentic-fm directory and prompt your agent to generate the script
4. The agent generates an `fmxmlsnippet` file in `agent/sandbox/`, validates it, and loads it onto the clipboard
5. **📋 Switch to FileMaker**, open the Script Workspace, position your cursor, and press **Cmd+V**

> **Companion server not needed here.** Push Context writes directly to the filesystem; `clipboard.py` uses the macOS clipboard directly. The companion server is only required for the webviewer, Explode XML, and the debug script.

---

## 🎯 Your first CLI/IDE session

The fastest way to see agentic-fm in action is to work with a script that already exists in your solution rather than creating one from scratch. Your agent can read, explain, and improve real scripts immediately after Explode XML has run.

### Step 1 — Open your CLI or IDE

Open the agentic-fm directory in your terminal or IDE and start your agent. Then prompt it to load one of your existing scripts by name:

```
Load script "Send Invoice Email" so we can start to optimize it.
Give me a description of what the script does.
```

The agent will locate the script in `agent/xml_parsed/scripts_sanitized/`, read it, and return a plain-English summary of its logic. From there you can ask it to refactor, add error handling, optimize for server execution, or anything else.

### Step 2 — Push context (when you need field or layout awareness)

If your next prompt requires generating or modifying steps that reference fields, layouts, or related tables, navigate to the relevant layout in FileMaker and run **Push Context** with a task description:

```
Optimize the Send Invoice Email script to use JSON for parameter passing
```

This writes `agent/CONTEXT.json` so the agent has the correct field IDs and layout references scoped to your task.

### Step 3 — Paste changes into FileMaker

When the agent produces updated steps, it validates and loads them onto the clipboard automatically. Switch to FileMaker, open the Script Workspace, position your cursor, and press **Cmd+V**.

### Step 4 — Iterate

Keep the conversation going:

```
Add error handling around the Send Mail step and exit gracefully if it fails
```

The agent updates the file, re-validates, and reloads the clipboard. Paste again.

---

## 🔧 Troubleshooting

| Problem                             | Fix                                                                                                      |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `python3: command not found`        | Install Python 3 via [Homebrew](https://brew.sh): `brew install python`                                  |
| Webviewer script shows server error | Start the companion server first: `python3 agent/scripts/companion_server.py`                            |
| Explode XML fails                   | Confirm `~/bin/fm-xml-export-exploder` exists and is executable; confirm the companion server is running |
| CONTEXT.json is empty or missing    | Run **Push Context** again from the correct layout                                                       |
| Paste does nothing in FileMaker     | Confirm the clipboard was loaded — the agent logs `clipboard.py write` output; check for errors          |
| `$$AGENTIC.FM` not set              | Run **Get agentic-fm path** — it is cleared when the FileMaker file is closed                            |
