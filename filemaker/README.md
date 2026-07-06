# FileMaker Setup

This folder contains the FileMaker artifacts that connect your solution to the agentic-fm toolchain.

| File               | Purpose                                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------------------------- |
| `Context.fmfn`     | Custom function source — install into your FileMaker solution                                           |
| `context.xml`      | fmxmlsnippet format of the `Context` custom function — alternative install via `clipboard.py`           |
| `agentic-fm.fmp12` | Pre-built FileMaker file containing the companion script group — open and copy/paste into your solution |
| `agentic-fm.xml`   | Companion script group in fmxmlsnippet format — alternative install via `clipboard.py`                  |

---

## Dependencies

### FileMaker Pro 21.0+

The minimum version is **21.0**. Earlier versions lack:

- `GetTableDDL` — used by `Context.fmfn` to discover foreign key relationships
- `While` — used by `Context.fmfn` for iteration
- `Create Data File` / `Open Data File` / `Write to Data File` / `Close Data File` — used by the **Push Context** script to write `CONTEXT.json` to disk

### Companion Server

The **Explode XML** companion script calls `agent/scripts/companion_server.py` — a lightweight HTTP server — to run shell commands from FileMaker without any third-party plugin. FileMaker communicates with it via `Insert from URL`.

**Start the server before running Explode XML:**

```bash
python3 agent/scripts/companion_server.py
```

The server listens on port 8765 by default and uses only Python stdlib — no virtualenv is required. Keep it running in a terminal while you work.

The other two companion scripts (**Get agentic-fm path** and **Push Context**) use only native FileMaker steps and do not require the companion server.

### fm-xml-export-exploder

A Rust binary that parses FileMaker XML exports into individual files. Used by both the **Explode XML** companion script and by `fmparse.sh` when called from the terminal.

**Installation:**

1. Download the macOS binary from the [releases page](https://github.com/bc-m/fm-xml-export-exploder/releases/latest). Bleeding edge versions can be found [here](https://github.com/petrowsky/fm-xml-export-exploder/releases)
2. On first run, macOS Gatekeeper will block it. Right-click the binary and choose **Open** once to clear the restriction. You will need to authorize its execution within **System Settings -> Privacy & Security**.
3. Create `~/bin/` if it does not exist:
   ```bash
   mkdir -p ~/bin
   ```
4. Move the binary there and make it executable:
   ```bash
   mv fm-xml-export-exploder ~/bin/
   chmod +x ~/bin/fm-xml-export-exploder
   ```

> **Why `~/bin`?** The **Explode XML** companion script passes `~/bin/fm-xml-export-exploder` as the `FM_XML_EXPLODER_BIN` environment variable to `fmparse.sh`. If you want to place the binary elsewhere, open the **Explode XML** script in FileMaker and update the `Set Variable [$payload ; JSONSetElement ( "{}" ;[ "exploder_bin_path" ;  ...]` step to reflect your actual path.

When calling `fmparse.sh` directly from the terminal you can also override the path via the environment variable:

```bash
FM_XML_EXPLODER_BIN=/your/path/fm-xml-export-exploder ./fmparse.sh -s "My Solution" /path/to/export.xml
```

### Python 3

Required for `agent/scripts/clipboard.py` (loading snippets onto the FileMaker clipboard), `agent/scripts/validate_snippet.py` (post-generation validation), and `agent/scripts/companion_server.py` (shell execution server). All three use Python stdlib only — no virtualenv is required.

> Note: You are not required to use the clipboard.py with python3. If you are already using another tool such as the MBS or BaseElements plug-in then those methods will still function. The convenience of the clipboard.py and python3 method is such that the agent will automatically put the code it creates onto the clipboard.

Python 3 ships with macOS or can be installed via [Homebrew](https://brew.sh):

```bash
brew install python
```

Run scripts directly — no activation step needed:

```bash
python3 agent/scripts/clipboard.py write filemaker/agentic-fm.xml
python3 agent/scripts/companion_server.py
```

### xmllint

Required by `fmcontext.sh` to extract data from the exploded XML. Ships with macOS as part of libxml2. On Linux:

```bash
apt-get install libxml2-utils
```

---

## Installation Steps

For the full first-run workflow, see the main [QUICKSTART.md](../QUICKSTART.md#-one-time-filemaker-setup), especially the `One-time FileMaker setup` section.

### 1. Install the Context custom function

1. Open your FileMaker solution in FileMaker Pro.
2. Go to **File > Manage > Custom Functions**.
3. Click **New** and create a function named `Context` with one parameter: `task` (type: Text).
4. Copy the entire contents of `filemaker/Context.fmfn` and paste it into the calculation editor.
5. Click **OK** and save.

### 2. Install the companion scripts

Choose either option:

**Option A — Open the included .fmp12 file (fastest)**

Open `filemaker/agentic-fm.fmp12` in FileMaker Pro. Copy the **agentic-fm** script folder from its Script Workspace and paste it directly into your solution's Script Workspace.

**Option B — Install via clipboard**

Load `filemaker/agentic-fm.xml` onto the FileMaker clipboard using `clipboard.py`, then paste into the Script Workspace:

```bash
python3 agent/scripts/clipboard.py write filemaker/agentic-fm.xml
```

Switch to FileMaker, open the **Script Workspace** (**Scripts > Script Workspace**), click in the script list, and press **⌘V**. A folder named **agentic-fm** containing the scripts will appear.

> If you already use another method of clipboard manipulation (e.g. MBS/BaseElements plug-in, AppleScript, et al), `clipboard.py` may be optional here.

> Valuable info: MBS added automatic clipboard conversion for FileMaker XML in version `15.4`; with that converter enabled, the plugin can recognize valid `fmxmlsnippet` text on the clipboard and make it pasteable in FileMaker in the background. See [Copy and paste XML in FileMaker](https://www.mbsplugins.de/archive/2025-08-29/Copy_and_paste_XML_in_FileMake) and [MBS FileMaker Plugin, version 15.4pr4](https://www.mbsplugins.de/archive/2025-08-25/MBS_FileMaker_Plugin_version_1).

### 3. Configure the repo path

Run the **Get agentic-fm path** script once (from the Scripts menu or Script Workspace). A folder picker will appear. Select the root of the agentic-fm repo folder. The path is stored in `$$AGENTIC.FM` and persists for the session.

> This script must be run again each time FileMaker is relaunched, as global variables do not persist across sessions. Consider adding a call to it in your solution's startup script.

### 3.5. Start the companion server

Before running **Explode XML**, start `companion_server.py` in a terminal and leave it running:

```bash
python3 agent/scripts/companion_server.py
```

This server listens on port 8765 and handles the shell command that **Explode XML** issues via `Insert from URL`. You will need to restart it each time you open a new terminal session.

### 4. Run Explode XML

Run the **Explode XML** script to perform the first Save as XML export and populate `agent/xml_parsed/`. Re-run it any time the solution schema or scripts change.

> The script derives the `fmparse.sh` path from the `$$AGENTIC.FM` global variable set by **Get agentic-fm path**, so the repo can be located anywhere on disk.

### 5. Push Context before each AI session

Navigate to the layout you are working on and run the **Push Context** script. A dialog will prompt you for a task description. After you click OK, the context is written to `agent/CONTEXT.json` and you are ready to work with AI.

## Multi-developer setup (optional)

For teams where several developers share one FMS-hosted solution, each from their own machine with their own local repo and companion server, `automation.json` can carry a `users` block keyed by FileMaker account name (`Get(AccountName)`):

```json
"users": {
  "jsmith": {
    "repo_path": "/Users/jsmith/GITs/agentic-fm",
    "companion_host": "192.168.1.42",
    "companion_port": 8765
  }
}
```

The companion server exposes `GET /whoami?account=<name>` to resolve this block — see `agent/config/automation.json.example`. This is the config side of the feature; wiring it into **Get agentic-fm path** (so it calls `/whoami` instead of only relying on the folder-picker dialog) is left as a follow-up FM script change once this direction lands — the existing folder-picker flow keeps working unchanged in the meantime, since `users` is entirely optional.

---

## Optional: agentic-fm web viewer

The agentic-fm web viewer is a browser-based Monaco editor embedded directly in FileMaker. It provides a three-panel interface — script editor, XML preview, and AI chat — without leaving FileMaker Pro.

### Adding the web viewer to a layout

Add a **WebViewer** object to any layout and set its URL to `http://localhost:8080` (the Vite dev server). Name the object exactly **`agentic-fm`** — this name is required for the bridge script and custom menu integration to work correctly.

The web viewer works on any layout, but a **dedicated layout** is strongly recommended:

- Place only the web viewer object on the layout with no other interactive objects
- Make the layout window **resizable** so you can expand the editor to a comfortable size
- A single-object layout ensures the custom menu set (assigned per-layout) applies consistently whenever the editor is in use

See `webviewer/WEBVIEWER_INTEGRATION.md` for full setup and development workflow details.

### Custom menu integration (optional)

The `filemaker/custom_menu/` folder contains an optional custom menu set that adds five editor-aware menus to the layout hosting the web viewer. These menus expose keyboard shortcuts for common Monaco editor actions (comment toggle, indent, move line, find, and more) without requiring the developer to remember key bindings.

See `filemaker/custom_menu/README.md` for the integration steps.

---

## Dependency Summary

| Dependency             | Required By                                            | Where to Get                                                                      |
| ---------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------- |
| FileMaker Pro 21.0+    | Everything                                             | [claris.com](https://www.claris.com)                                              |
| companion_server.py    | Explode XML script                                     | Included — `python3 agent/scripts/companion_server.py`                            |
| fm-xml-export-exploder | Explode XML script, fmparse.sh                         | [GitHub releases](https://github.com/bc-m/fm-xml-export-exploder/releases/latest) |
| Python 3               | clipboard.py, validate_snippet.py, companion_server.py | Ships with macOS or `brew install python`                                         |
| xmllint                | fmcontext.sh                                           | Ships with macOS; `apt-get install libxml2-utils` on Linux                        |
