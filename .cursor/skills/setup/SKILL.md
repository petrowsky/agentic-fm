---
name: setup
description: Interactive setup wizard for agentic-fm. Detects what's already configured, walks the user through each remaining step, and verifies completion before proceeding. Use when the developer says "help me set up", "setup", "get started", "onboard", "first time setup", "install agentic-fm", "configure agentic-fm", or is clearly new to the project and needs guidance.
compatibility: Requires Python 3 and xmllint (libxml2). Optionally Node.js for the webviewer. Optionally requests and beautifulsoup4 (via venv) for fetching the FM function reference.
---

# setup

Interactive, resumable setup wizard for agentic-fm. Walks the developer through every step required to go from a fresh clone to a working AI-assisted FileMaker scripting environment.

---

## Step 0: Environment Detection

Before presenting any steps, silently detect what is already in place. Run these checks and record the results — they determine which steps to skip.

### Checks to run

```bash
# Python 3
python3 --version 2>&1

# fm-xml-export-exploder
test -x ~/bin/fm-xml-export-exploder && ~/bin/fm-xml-export-exploder --version 2>&1 || echo "NOT FOUND"

# xmllint
xmllint --version 2>&1

# Node.js (optional — webviewer path)
node --version 2>&1 || echo "NOT FOUND"

# Companion server running?
curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/status 2>/dev/null || echo "NOT RUNNING"

# automation.json exists?
test -f agent/config/automation.json && echo "EXISTS" || echo "NOT FOUND"

# xml_parsed populated?
ls agent/xml_parsed/ 2>/dev/null | head -1 || echo "EMPTY"

# CONTEXT.json exists and is recent?
test -f agent/CONTEXT.json && echo "EXISTS" || echo "NOT FOUND"
```

If running inside a Docker container or non-macOS environment, also try `host.docker.internal:8765` for the companion server check.

### Present a status summary

After running the checks, present a checklist showing what is done and what remains. Example:

> **agentic-fm setup status**
>
> - [x] Python 3 — v3.12.1
> - [ ] fm-xml-export-exploder — not found
> - [x] xmllint — installed
> - [ ] Node.js — not found (only needed for webviewer)
> - [ ] Companion server — not running
> - [ ] FileMaker setup — unknown (no xml_parsed data)
> - [ ] CONTEXT.json — not found
>
> Starting from: **Step 2 — Install fm-xml-export-exploder**

Skip any step whose check passes. Resume from the first incomplete step.

---

## Step 1: Verify Python 3

**Check**: `python3 --version` succeeds.

**If missing**, tell the developer:

> Python 3 is required for clipboard operations, validation, and the companion server. All scripts use the standard library only — no virtual environment needed.
>
> **macOS**: Python 3 ships at `/usr/bin/python3`. For a newer version: `brew install python`
> **Linux**: `sudo apt-get install python3` or your distro's equivalent.

**Verify**: Run `python3 agent/scripts/clipboard.py --help` and confirm it prints usage info.

---

## Step 2: Install fm-xml-export-exploder

**Check**: `~/bin/fm-xml-export-exploder` exists and is executable.

**If missing**, walk through:

> **fm-xml-export-exploder** is a Rust binary that parses FileMaker XML exports into individual files. It is required for the Explode XML workflow.
>
> 1. Download the binary for your platform from [GitHub releases](https://github.com/bc-m/fm-xml-export-exploder/releases/latest)
>    - Bleeding-edge builds: [petrowsky fork](https://github.com/petrowsky/fm-xml-export-exploder/releases)
> 2. Move it to `~/bin/` and make it executable:
>    ```bash
>    mkdir -p ~/bin
>    mv ~/Downloads/fm-xml-export-exploder ~/bin/
>    chmod +x ~/bin/fm-xml-export-exploder
>    ```
> 3. **macOS Gatekeeper**: On first run, macOS will block it. Right-click the binary in Finder, choose **Open**, then authorize in **System Settings > Privacy & Security**.

**Verify**: Run `~/bin/fm-xml-export-exploder --version` and confirm output.

Ask the developer to confirm when done before proceeding.

---

## Step 3: Verify xmllint

**Check**: `xmllint --version` succeeds.

**If missing**, tell the developer:

> **xmllint** is required by `fmcontext.sh` to generate index files from exploded XML.
>
> - **macOS**: Ships with the OS (part of libxml2). Should already be available.
> - **Linux**: `sudo apt-get install libxml2-utils`

This step usually passes automatically on macOS. Move on quickly if it does.

---

## Step 4: Install the Context custom function

**Check**: Cannot be verified from the CLI. Ask the developer.

> Have you already installed the **Context** custom function in your FileMaker solution?

**If no**, walk through:

> The `Context` custom function generates the JSON that powers the AI's awareness of your solution. Install it once per solution:
>
> 1. Open your solution in **FileMaker Pro 21.0+** (19.x/20.x: use `filemaker/Context19.fmfn` / `filemaker/context19.xml` in the steps below)
> 2. Go to **File > Manage > Custom Functions**
> 3. Click **New**
> 4. Name: `Context`
> 5. Add one parameter: `task` (type: Text)
> 6. Open the file `filemaker/Context.fmfn` and paste its entire contents into the calculation editor
> 7. Click **OK** and save
>
> Alternatively, you can install it via the clipboard:
>
> ```bash
> python3 agent/scripts/clipboard.py write filemaker/context.xml
> ```
>
> Then in FileMaker: **File > Manage > Custom Functions** — click in the function list and press **Cmd+V**.

Ask the developer to confirm when done.

---

## Step 5: Install the companion scripts

**Check**: Cannot be verified from the CLI. Ask the developer.

> Have you already installed the **agentic-fm** script folder in your FileMaker solution?

**If no**, present both options:

> The companion scripts handle XML export, context generation, and debugging. Choose one method:
>
> **Option A — Copy from the included .fmp12 file (fastest)**
>
> 1. Open `filemaker/agentic-fm.fmp12` in FileMaker Pro
> 2. Open its Script Workspace — you'll see an **agentic-fm** folder
> 3. Copy the entire **agentic-fm** folder
> 4. Switch to your solution's Script Workspace and paste (**Cmd+V**)
>
> **Option B — Install via clipboard**
>
> ```bash
> python3 agent/scripts/clipboard.py write filemaker/agentic-fm.xml
> ```
>
> Switch to FileMaker, open **Scripts > Script Workspace**, click in the script list, and press **Cmd+V**. The **agentic-fm** folder with all companion scripts will appear.

Ask the developer to confirm when done.

---

## Step 6: Configure the repo path

**Check**: Cannot be verified from the CLI. Ask the developer.

> In FileMaker, run the **Get agentic-fm path** script from the Scripts menu. A folder picker will appear — select the root of this repo (the folder containing `QUICKSTART.md`, `agent/`, `filemaker/`, etc.).
>
> This stores the path in `$$AGENTIC.FM` for the current session. The variable is cleared when the FileMaker file closes, so you'll need to run this again each session — or add a call to it in your solution's startup script (`OnFirstWindowOpen`) to automate it.

Ask the developer to confirm when done.

---

## Step 7: Start the companion server

**Check**: HTTP request to `http://localhost:8765/status` returns a response.

If not running, tell the developer:

> The companion server is a lightweight Python HTTP server that several FileMaker scripts communicate with. Start it in a terminal and keep it running while you work:
>
> ```bash
> python3 agent/scripts/companion_server.py
> ```
>
> It listens on port **8765** by default. Use `--port N` for a different port.

**Verify**: Run the health check again. If running inside Docker, also try `host.docker.internal:8765`.

Once confirmed, proceed.

---

## Step 8: Run Explode XML

**Check**: `agent/xml_parsed/` contains solution data (non-empty directory with subdirectories).

**If empty or missing**, tell the developer:

> Run the **Explode XML** script in FileMaker (from the Scripts menu). This exports your solution as XML and parses it into individual files that the AI agent can reference.
>
> The script will:
>
> 1. Save a Copy as XML of your solution
> 2. Send the export to the companion server
> 3. Parse it into `agent/xml_parsed/` (individual tables, scripts, layouts, etc.)
> 4. Generate index files in `agent/context/` for fast lookup
>
> This takes a few seconds for small solutions, longer for large ones. You'll see progress in the companion server terminal.

**Verify**: Check that `agent/xml_parsed/` now contains subdirectories. List what was found:

```bash
ls agent/xml_parsed/
```

Also verify index files were generated:

```bash
ls agent/context/
```

Report what solution(s) were found. Ask the developer to confirm before proceeding.

---

## Step 9: Choose your workflow

> **How do you want to work with agentic-fm?**
>
> **A. CLI / IDE** — Use Claude Code, Cursor, VS Code, or any terminal-based AI agent. The agent generates fmxmlsnippet XML that you paste into FileMaker. This is the most powerful path with access to the full skill set.
>
> **B. Webviewer** — A visual three-panel editor (Monaco + AI chat) that runs in your browser and can embed directly inside FileMaker. Great if you prefer a visual workflow or are new to CLI tools.
>
> **C. Both** — Set up both paths. They share the same underlying data.

Based on the answer, continue with the appropriate path(s).

---

## Path A: CLI / IDE Setup

### Step A1: Push Context

> Let's verify that context generation works. In FileMaker:
>
> 1. Navigate to a layout you'd like to work with
> 2. Run **Push Context** from the Scripts menu
> 3. When prompted, enter a task description (e.g., "Explore the solution")
> 4. Click OK

**Verify**:

```bash
test -f agent/CONTEXT.json && python3 -c "import json; d=json.load(open('agent/CONTEXT.json')); print(f'Context loaded: {d.get(\"current_layout\",{}).get(\"name\",\"unknown\")} layout, {len(d.get(\"tables\",{}))} tables')"
```

Report what was found (layout name, table count) to confirm it worked.

### Step A2: First session guidance

> You're all set! Here's how to start your first session:
>
> 1. Open this directory in your AI agent (Claude Code, Cursor, etc.)
> 2. Try loading an existing script to see agentic-fm in action:
>    ```
>    Load script "ScriptName" and give me a description of what it does.
>    ```
> 3. When you need to generate or modify scripts that reference fields/layouts, run **Push Context** first on the relevant layout in FileMaker
> 4. The agent writes validated XML to `agent/sandbox/` and loads it onto your clipboard
> 5. In FileMaker Script Workspace: **Cmd+V** to paste
>
> **Every session checklist:**
>
> - Companion server running
> - `$$AGENTIC.FM` set (run **Get agentic-fm path** if needed)
> - **Push Context** run on the target layout

---

## Path B: Webviewer Setup

### Step B1: Verify Node.js

**Check**: `node --version` returns 18+.

**If missing or too old**:

> Node.js 18+ is required for the webviewer dev server.
>
> Install from [nodejs.org](https://nodejs.org) or via Homebrew:
>
> ```bash
> brew install node
> ```

### Step B2: Install dependencies and start

> You can start the webviewer in two ways:
>
> **From FileMaker (easiest):** Run the **Agentic-fm webviewer** script from the Scripts menu. It installs dependencies and starts the dev server automatically.
>
> **From the terminal:**
>
> ```bash
> cd webviewer
> npm install
> npm run dev
> ```
>
> The webviewer will be available at **http://localhost:8080**.

**Verify**: Check that the dev server is responding:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080 2>/dev/null
```

### Step B3: Configure AI provider (optional)

> To use AI chat within the webviewer, configure an AI provider in the settings panel (gear icon), or create `webviewer/.env.local`:
>
> ```env
> AI_PROVIDER=anthropic
> AI_MODEL=sonnet
> ANTHROPIC_API_KEY=your-key-here
> ```
>
> Supported providers: `anthropic`, `openai`, `claude-code` (CLI proxy).
>
> The webviewer works without an AI provider — you can write HR scripts manually and it converts them to fmxmlsnippet automatically.

### Step B4: Embed in FileMaker (optional)

> To embed the webviewer directly inside FileMaker:
>
> 1. Add a **Web Viewer** object to a layout
> 2. Set the URL to `http://localhost:8080`
> 3. Name the object exactly **`agentic-fm`** (required for the bridge)
> 4. A dedicated layout with only the web viewer is recommended
>
> See `webviewer/WEBVIEWER_INTEGRATION.md` for full details.

---

## Step 10: Optional Enhancements

After the core setup is complete, mention these optional next steps:

### automation.json (OData + advanced deployment)

> **Optional:** If you want the agent to trigger FileMaker scripts autonomously (via OData) or use Tier 2/3 auto-paste deployment, create `agent/config/automation.json` from the example template:
>
> ```bash
> cp agent/config/automation.json.example agent/config/automation.json
> ```
>
> Then edit it to add your solution's OData credentials. Run the `schema-build connect` skill for a guided OData setup.

### Startup script automation

> **Recommended:** Add a call to **Get agentic-fm path** in your solution's `OnFirstWindowOpen` script trigger so the repo path is set automatically every time you open the file. This eliminates the need to run it manually each session.

### Custom menus (webviewer only)

> **Optional:** If you embedded the webviewer in FileMaker, the `filemaker/custom_menu/` folder contains an optional custom menu set that adds keyboard shortcuts for Monaco editor actions. See `filemaker/custom_menu/README.md` for integration steps.

### FileMaker function reference (optional)

> **Optional:** Download the official Claris function reference for offline use by the agent.
>
> This requires `requests` and `beautifulsoup4`. Set up a venv first:
>
> ```bash
> python3 -m venv agent/.venv
> source agent/.venv/bin/activate
> pip install requests beautifulsoup4
> ```
>
> Then run the fetch script with the venv active (or prefix with `agent/.venv/bin/python3`):
>
> ```bash
> agent/.venv/bin/python3 agent/docs/filemaker/fetch_docs.py
> ```

---

## Completion

When all applicable steps are done, present a final summary:

> **Setup complete!** Here's what's configured:
>
> | Component                | Status                      |
> | ------------------------ | --------------------------- |
> | Python 3                 | vX.Y.Z                      |
> | fm-xml-export-exploder   | installed                   |
> | xmllint                  | installed                   |
> | Context custom function  | installed                   |
> | Companion scripts        | installed                   |
> | Repo path ($$AGENTIC.FM) | set                         |
> | Companion server         | running on port 8765        |
> | Explode XML              | done — N solution(s) parsed |
> | CONTEXT.json             | generated                   |
> | Workflow                 | CLI/IDE / Webviewer / Both  |
>
> **Quick reference — every session:**
>
> 1. Start companion server: `python3 agent/scripts/companion_server.py`
> 2. In FileMaker: run **Get agentic-fm path** (if not in startup script)
> 3. Navigate to your target layout
> 4. Run **Push Context** with a task description
> 5. Start working with your AI agent
>
> For more details, see `QUICKSTART.md`.
