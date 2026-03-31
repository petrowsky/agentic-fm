# Companion Server

The Companion Server is a lightweight local HTTP server for `agentic-fm`. It acts as the bridge between FileMaker Pro and host-side capabilities that FileMaker cannot perform by itself.

It currently provides 4 broad categories of functionality:

- **Shell command bridge**
  - Runs `fmparse.sh` so FileMaker can trigger XML parsing.

- **Local file write helpers**
  - Writes `CONTEXT.json`, debug payloads, clipboard data, and webviewer output payloads.

- **FileMaker automation bridge**
  - Triggers FileMaker scripts via AppleScript and coordinates pending paste jobs.

- **Live file monitoring and analysis**
  - Watches a local file continuously.
  - Detects changes in near real time.
  - Analyzes appended content.
  - Exposes results by JSON, SSE stream, terminal log output, and a built-in UI.

This is especially useful for monitoring FileMaker's `Import.log` after importing script steps from the clipboard. The watch subsystem now surfaces full import lifecycle events, grouped import sessions, and all non-zero import errors — not only unknown attribute values.

## Why this exists

FileMaker has no built-in, first-party mechanism for:

- **running arbitrary local shell commands**
- **watching local files continuously**
- **streaming live analysis results over HTTP**
- **writing directly to the macOS clipboard**
- **triggering host-side automation outside normal FM scripting**

The Companion Server fills that gap using only Python standard library components.

## Platform scope

- **Primary target**
  - macOS local development

- **Why macOS matters**
  - `osascript` is used for FileMaker automation
  - clipboard integration is macOS-oriented
  - `fmparse.sh` and `fm-xml-export-exploder` are Unix tooling

- **Windows gap**
  - not currently supported as a full equivalent environment

---

## Starting the server

The server is a single Python file with no external Python dependencies.

```bash
# Default port 8765
python3 agent/scripts/companion_server.py

# Custom port
python3 agent/scripts/companion_server.py --port 9000
```

By default it binds to `127.0.0.1`.

Environment variable:

- **`COMPANION_BIND_HOST`**
  - overrides the bind host
  - default: `127.0.0.1`

Startup log output now looks more like this:

```txt
2026-03-29T19:10:00 INFO companion_server v1.0 listening on 127.0.0.1:8765
2026-03-29T19:10:00 INFO Watch UI: http://127.0.0.1:8765/watch/ui
2026-03-29T19:10:00 INFO Endpoints: GET /health  GET /pending  GET /watch/status  GET /watch/results  GET /watch/stream  GET /watch/ui  GET /webviewer/status  POST /explode  POST /context  POST /clipboard  POST /trigger  POST /debug  POST /pending  POST /watch/import-log/start  POST /watch/pick-path  POST /watch/stop  POST /webviewer/start  POST /webviewer/stop  POST /webviewer/push
2026-03-29T19:10:00 INFO Press Ctrl-C to stop.
```

On startup the server also performs a background version check against the upstream `version.txt`. If a newer version is available, it logs a warning.

### Background process (Mac)

To run the server in the background:

```bash
python3 agent/scripts/companion_server.py &
```

To redirect output:

```bash
python3 agent/scripts/companion_server.py > /tmp/companion_server.log 2>&1 &
```

### Auto-start with launchd (Mac)

To start automatically at login, create a launchd plist.

**`~/Library/LaunchAgents/com.agentic-fm.companion-server.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agentic-fm.companion-server</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/yourname/agentic-fm/agent/scripts/companion_server.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/yourname/agentic-fm</string>

    <key>StandardOutPath</key>
    <string>/tmp/companion_server.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/companion_server.log</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.agentic-fm.companion-server.plist
```

Verify it:

```bash
launchctl list | grep agentic-fm
curl http://localhost:8765/health
```

Unload it:

```bash
launchctl unload ~/Library/LaunchAgents/com.agentic-fm.companion-server.plist
```

---

## Endpoint overview

### Health and basic status

| Method | Path       | Purpose                            |
| ------ | ---------- | ---------------------------------- |
| `GET`  | `/health`  | Liveness and version check         |
| `GET`  | `/pending` | Return and clear pending paste job |
| `POST` | `/pending` | Set pending paste job              |

### File watch and live analysis

| Method | Path                      | Purpose                                                    |
| ------ | ------------------------- | ---------------------------------------------------------- |
| `GET`  | `/watch/status`           | Full internal watch state snapshot                         |
| `GET`  | `/watch/results`          | Curated watch results payload                              |
| `GET`  | `/watch/stream`           | SSE stream of live watch updates                           |
| `GET`  | `/watch/ui`               | Built-in HTML UI for watch control and grouped import view |
| `POST` | `/watch/import-log/start` | Start watching `Import.log` with automatic path resolution |
| `POST` | `/watch/pick-path`        | Open a native file or folder picker on the host machine    |
| `POST` | `/watch/stop`             | Stop current watch                                         |

### FileMaker workflow bridge

| Method | Path         | Purpose                                  |
| ------ | ------------ | ---------------------------------------- |
| `POST` | `/explode`   | Run `fmparse.sh`                         |
| `POST` | `/context`   | Write `agent/CONTEXT.json`               |
| `POST` | `/clipboard` | Write XML to macOS clipboard             |
| `POST` | `/trigger`   | Trigger FileMaker script via AppleScript |
| `POST` | `/debug`     | Write runtime debug payload              |

### Webviewer helper endpoints

| Method | Path                | Purpose                                       |
| ------ | ------------------- | --------------------------------------------- |
| `GET`  | `/webviewer/status` | Check whether webviewer dev server is running |
| `POST` | `/webviewer/start`  | Start the webviewer dev server                |
| `POST` | `/webviewer/stop`   | Stop the webviewer dev server                 |
| `POST` | `/webviewer/push`   | Write `.agent-output.json` for the webviewer  |

---

## Endpoints

### GET /health

A lightweight liveness check.

Use this when:

- **FileMaker** wants to confirm the server is reachable before a workflow step
- **a script** wants to sanity-check server availability
- **a developer** wants to confirm the server is running

**Request:**

```
GET http://localhost:8765/health
```

No request body. No required headers.

**Response (200 OK):**

```json
{
  "status": "ok",
  "version": "1.0"
}
```

---

### POST /watch/pick-path

Opens a native file or folder chooser on the host machine running the Companion Server.

This is intended for the built-in Watch UI so local paths can be selected without manually typing them.

**Request body:**

| Field            | Type   | Required | Description                                        |
| ---------------- | ------ | -------- | -------------------------------------------------- |
| `selection_type` | string | No       | `file` or `directory`. Default `file`.             |
| `initial_path`   | string | No       | Initial file or folder hint for the picker dialog. |
| `title`          | string | No       | Custom dialog title.                               |

**Response example:**

```json
{
  "success": true,
  "selected_path": "/Users/yourname/Documents/Import.log",
  "cancelled": false
}
```

Notes:

- tries `tkinter` first if available
- on macOS, falls back to native `osascript` dialogs
- on Windows, falls back to PowerShell dialogs
- if the dialog is cancelled, `selected_path` is empty and `cancelled` is `true`

---

### GET /watch/status

Returns the full current watch state snapshot.

This is the most complete raw status endpoint. It exposes internal tracking fields such as:

- **watch lifecycle**
  - `running`
  - `started_at`
  - `path`
  - `poll_interval`
  - `start_at_end`

- **file tracking**
  - `file_exists`
  - `offset`
  - `last_checked_at`
  - `last_change_at`

- **analysis state**
  - `analyzer`
  - `summary`
  - `recent_events`
  - `last_error`
  - `revision`

**Response example:**

```json
{
  "running": true,
  "path": "/Users/yourname/Documents/Import.log",
  "poll_interval": 0.5,
  "start_at_end": true,
  "started_at": "2026-03-29T17:11:54.102000+00:00",
  "last_checked_at": "2026-03-29T17:12:10.402000+00:00",
  "last_change_at": "2026-03-29T17:12:08.921000+00:00",
  "last_event_at": "2026-03-29T17:12:08.922000+00:00",
  "offset": 4821,
  "file_exists": true,
  "revision": 7,
  "analyzer": {
    "type": "import_log"
  },
  "summary": {
    "events_total": 5,
    "errors_total": 2,
    "matches_by_rule": {
      "import_started": 1,
      "missing_function": 1,
      "missing_field": 1,
      "import_steps_imported": 1,
      "import_completed": 1
    },
    "errors_by_code": {
      "102": 1,
      "1208": 1
    },
    "current_import": {},
    "last_completed_import": {
      "source": "FileName.fmp12",
      "database_name": "FileName.fmp12",
      "script_name": "ScriptName",
      "started_at": "2026-03-27 16:21:28.370 +0100",
      "error_count": 2,
      "imported_steps": 260,
      "completed_at": "2026-03-27 16:21:28.420 +0100",
      "status": "with_errors",
      "errors": [
        {
          "line_number": "31",
          "step_name": "Set Variable",
          "label": "Missing function",
          "code": "1208",
          "message": "Function referred to in the calculation “cf_Param_Get ( \"Prozess\" )” is missing."
        },
        {
          "line_number": "42",
          "step_name": "Set Field",
          "label": "Missing field",
          "code": "102",
          "message": "Field “FertigungFreigabeStatus” missing."
        }
      ]
    },
    "recent_imports": [
      {
        "script_name": "ScriptName",
        "imported_steps": 260,
        "status": "with_errors"
      }
    ],
    "imports_total": 12,
    "imports_with_errors": 2,
    "imports_without_errors": 10,
    "last_error": {
      "rule": "missing_field",
      "message": "Field “FertigungFreigabeStatus” missing."
    }
  },
  "recent_events": [
    {
      "event_type": "import_log_lifecycle",
      "rule": "import_completed",
      "severity": "warn",
      "import_status": "with_errors",
      "imported_steps": 260,
      "error_count": 2,
      "message": "Import completed with 2 errors."
    }
  ],
  "last_error": "",
  "platform": {
    "system": "Darwin",
    "python_platform": "darwin"
  },
  "defaults": {
    "documents_dir": "/Users/yourname/Documents"
  }
}
```

---

### GET /watch/results

Returns a curated watch payload intended for polling clients and the built-in UI.

Compared with `/watch/status`, this omits some lower-level internal fields and focuses on the values most useful to a consumer.

**Response example:**

```json
{
  "running": true,
  "path": "/Users/yourname/Documents/Import.log",
  "poll_interval": 0.5,
  "start_at_end": true,
  "started_at": "2026-03-29T17:11:54.102000+00:00",
  "file_exists": true,
  "revision": 7,
  "analyzer": {
    "type": "import_log"
  },
  "summary": {
    "events_total": 5,
    "errors_total": 2,
    "matches_by_rule": {
      "import_started": 1,
      "missing_function": 1,
      "missing_field": 1,
      "import_steps_imported": 1,
      "import_completed": 1
    },
    "errors_by_code": {
      "102": 1,
      "1208": 1
    },
    "current_import": {},
    "last_completed_import": {
      "source": "FileName.fmp12",
      "database_name": "FileName.fmp12",
      "script_name": "ScriptName",
      "started_at": "2026-03-27 16:21:28.370 +0100",
      "error_count": 2,
      "imported_steps": 260,
      "completed_at": "2026-03-27 16:21:28.420 +0100",
      "status": "with_errors",
      "errors": [
        {
          "line_number": "31",
          "step_name": "Set Variable",
          "label": "Missing function",
          "code": "1208",
          "message": "Function referred to in the calculation “cf_Param_Get ( \"Prozess\" )” is missing."
        }
      ]
    },
    "recent_imports": [
      {
        "script_name": "ScriptName",
        "imported_steps": 260,
        "status": "with_errors"
      }
    },
    "last_error": {
      "rule": "missing_field",
      "message": "Field “FertigungFreigabeStatus” missing."
    }
  },
  "recent_events": [
    {
      "event_type": "import_log_issue",
      "rule": "missing_function",
      "severity": "error",
      "script_name": "ScriptName",
      "script_line": "31",
      "line_number": "31",
      "step_name": "Set Variable",
      "label": "Missing function",
      "code": "1208",
      "message": "Function referred to in the calculation “cf_Param_Get ( \"Prozess\" )” is missing."
    }
  ],
  "last_change_at": "2026-03-29T17:12:08.921000+00:00",
  "last_event_at": "2026-03-29T17:12:08.922000+00:00",
  "last_error": "",
  "platform": {
    "system": "Darwin",
    "python_platform": "darwin"
  },
  "defaults": {
    "documents_dir": "/Users/yourname/Documents"
  }
}
```

---

### GET /watch/stream

Streams live watch updates as **Server-Sent Events**.

This is the push-style alternative to polling `/watch/results`.

Behavior:

- **Event type `results`**
  - sent whenever watch state revision changes
  - payload is the same shape as `/watch/results`

- **Event type `ping`**
  - sent periodically when no state change occurred
  - keeps the connection alive

**Request:**

```txt
GET http://localhost:8765/watch/stream
Accept: text/event-stream
```

**Example stream:**

```txt
id: 12
event: results
data: {"running":true,"path":"/Users/yourname/Documents/Import.log","revision":12,...}

event: ping
data: {"timestamp":"2026-03-29T17:12:20.000000+00:00"}
```

---

### GET /watch/ui

Serves a built-in HTML UI for the watch subsystem.

The UI is now loaded from `agent/scripts/companion_watch_ui.html`, not embedded inline in the Python source.

Open in a browser:

```txt
http://127.0.0.1:8765/watch/ui
```

The page provides:

- **Auto Import.log Watch**
  - choose server or local mode
  - set poll interval
  - provide database path or documents directory
  - browse for the local database folder using a native picker
  - auto-fill the host machine's default Documents directory

- **Live status**
  - current path
  - analyzer
  - revision
  - last change
  - last event

- **Grouped import session view**
  - current import session
  - recent completed import sessions
  - per-import script name and imported step count
  - compact per-error rows with line number first
  - successful imports that show no errors

The page uses:

- **`GET /watch/results`** for refresh
- **`GET /watch/stream`** for live updates
- **`POST /watch/import-log/start`** to begin watching
- **`POST /watch/pick-path`** to open host-native file/folder chooser dialogs
- **`POST /watch/stop`** to stop watching

---

### POST /watch/import-log/start

Starts a watch specifically for FileMaker's `Import.log`, with automatic path resolution.

This is the recommended endpoint for clipboard-import monitoring.

**Request body:**

| Field             | Type             | Required | Description                                                                      |
| ----------------- | ---------------- | -------- | -------------------------------------------------------------------------------- |
| `location`        | string           | No       | `server` or `local`. If omitted, inferred from `database_path` / `database_dir`. |
| `mode`            | string           | No       | Alias for `location`.                                                            |
| `import_log_path` | string           | No       | Explicit full path override. If provided, wins over all inference.               |
| `documents_dir`   | string           | No       | Directory used for `server` mode. Default is the host OS Documents folder.       |
| `database_path`   | string           | No       | Path to a local database directory. The built-in UI fills this with a folder.    |
| `database_dir`    | string           | No       | Explicit local directory containing `Import.log`.                                |
| `poll_interval`   | number           | No       | Poll interval in seconds. Default `0.5`.                                         |
| `start_at_end`    | boolean          | No       | Default `true`.                                                                  |
| `analyzer`        | string or object | No       | Default `import_log`.                                                            |

**Resolution rules:**

- **explicit path override**
  - if `import_log_path` is provided, it is used directly

- **server mode**
  - resolves to `{documents_dir}/Import.log`
  - default documents directory is resolved per host OS
  - macOS and Linux typically resolve to `~/Documents`
  - Windows resolves to the user's real Documents folder when available

- **local mode**
  - resolves to `<database dir>/Import.log`
  - accepts either a file path or a directory path

**Server file example:**

```json
{
  "location": "server",
  "analyzer": "import_log"
}
```

**Local file example:**

```json
{
  "location": "local",
  "database_path": "/Users/yourname/Projects/MySolution",
  "analyzer": "import_log"
}
```

**Response example:**

```json
{
  "success": true,
  "location": "local",
  "resolved_path": "/Users/yourname/Projects/MySolution/Import.log",
  "watch": {
    "running": true,
    "path": "/Users/yourname/Projects/MySolution/Import.log",
    "analyzer": {
      "type": "import_log"
    }
  }
}
```

---

### POST /watch/stop

Stops the current watch if one is running.

**Request body:**

- **none required**
  - an empty body or `{}` is fine

**Response examples:**

```json
{ "success": true, "status": "stopped" }
```

```json
{ "success": true, "status": "not_running" }
```

---

### POST /explode

The primary endpoint. Accepts a JSON payload describing the export to parse, invokes `fmparse.sh` as a subprocess, and returns the exit code and output so FileMaker can detect success or failure.

**Request headers:**

```
Content-Type: application/json
Content-Length: <byte length of body>
```

**Request body — JSON schema:**

| Field | Type | Required | Description |
|---|---|---|---|
| `solution_name` | string | Yes | The solution identifier. Used by `fmparse.sh` as the subfolder name under `xml_exports/` and `agent/xml_parsed/`. Must match the name used when the XML was exported. |
| `export_file_path` | string | Yes | Absolute path to the FileMaker XML export file (or directory of XML exports) on the local machine. Tilde expansion is supported (`~/...`). |
| `repo_path` | string | Yes | Absolute path to the root of the agentic-fm repository. `fmparse.sh` is resolved at `{repo_path}/fmparse.sh`. Tilde expansion is supported. |
| `exploder_bin_path` | string | No | Absolute path to the `fm-xml-export-exploder` binary, if it is not on `PATH`. Passed through to `fmparse.sh` as the `FM_XML_EXPLODER_BIN` environment variable. |

**Example request body:**

```json
{
  "solution_name": "Invoice Solution",
  "export_file_path": "/Users/yourname/Desktop/InvoiceSolution.xml",
  "repo_path": "/Users/yourname/agentic-fm",
  "exploder_bin_path": "~/bin/fm-xml-export-exploder"
}
```

**Success response (200 OK):**

Returned when `fmparse.sh` exits with code `0`.

```json
{
  "success": true,
  "exit_code": 0,
  "stdout": "==> Parsing Invoice Solution\n==> Done.\n",
  "stderr": ""
}
```

**Failure response (500):**

Returned when `fmparse.sh` exits with a non-zero code (e.g. the exploder binary is missing or the export file cannot be read).

```json
{
  "success": false,
  "exit_code": 1,
  "stdout": "==> Parsing Invoice Solution\n",
  "stderr": "ERROR: fm-xml-export-exploder: command not found\n"
}
```

**Validation error response (400):**

Returned when required fields are missing or the request body is not valid JSON.

```json
{
  "success": false,
  "exit_code": -1,
  "error": "Missing required fields: solution_name, repo_path"
}
```

**What the server actually runs:**

The server constructs and executes this command as a subprocess, with `cwd` set to `repo_path`:

```bash
{repo_path}/fmparse.sh -s "{solution_name}" "{export_file_path}"
```

If `exploder_bin_path` is provided, it is injected into the subprocess environment as `FM_XML_EXPLODER_BIN` before the command runs. `fmparse.sh` reads this variable to locate the exploder binary without requiring it to be on `PATH`.

---

### POST /context

Writes a CONTEXT.json file to the agentic-fm project on the host. Called by the Push Context FM script in server mode.

**Request body:**
```json
{ "repo_path": "/absolute/path/to/agentic-fm", "context": "{...}" }
```
`context` may be a pre-serialised JSON string or a parsed object.

**Response:** `{ "success": true, "path": "/path/to/CONTEXT.json" }`

---

### GET /pending

Returns and clears the pending paste job set by `/trigger` or `/pending`.

This is used because FileMaker Pro 22 does not reliably receive AppleScript `do script ... given parameter:` values in all flows.

The pending job may contain:

- **`target`**
  - target script or target name

- **`auto_save`**
  - whether the receiving FM script should save after paste

- **`select_all`**
  - whether the receiving FM script should select all before pasting

**Response example:**

```json
{
  "target": "ScriptName",
  "auto_save": false,
  "select_all": true
}
```

If no pending job exists, the server currently returns an empty object:

```json
{}
```

The job is consumed on read.

---

### POST /pending

Sets the pending paste job directly.

Useful for:

- **testing**
- **custom trigger flows**
- **manual job injection**

**Request body:**

```json
{
  "target": "ScriptName",
  "auto_save": true,
  "select_all": true
}
```

`select_all` defaults to `true` if omitted.

**Response:**

```json
{ "success": true }
```

---

### POST /clipboard

Accepts fmxmlsnippet XML content and writes it to the macOS clipboard using `clipboard.py`. Used by `deploy.py` (Tier 1/2/3) so the agent container can load the clipboard without running `osascript` directly.

**Request body:**
```json
{ "xml": "<?xml version=\"1.0\"?>..." }
```

**Response:** `{ "success": true }`

---

### POST /trigger

Triggers FM Pro on the host to run a named FileMaker script via AppleScript (`osascript`). Used by `deploy.py` for Tier 2/3 automated deployment.

**Request body:**
```json
{
  "fm_app_name": "FileMaker Pro — 22.0.4.406",
  "script": "Agentic-fm Paste",
  "parameter": "TargetScriptName",
  "target_file": "MySolution",
  "auto_save": false,
  "select_all": true
}
```

Additional supported fields:

- **`parameter`**
  - optional
  - stored in the server-side pending job instead of being passed directly to `do script`

- **`target_file`**
  - optional
  - if provided, AppleScript targets the first FileMaker document whose name contains this string

- **`raw_applescript`**
  - optional
  - bypasses the normal FileMaker script trigger template and runs arbitrary AppleScript

`fm_app_name` must match the exact AppleScript application name if a versioned FileMaker app name is required.

The AppleScript template used:
```applescript
tell application "FileMaker Pro — 22.0.4.406"
    activate
    tell document 1
        do script "Agentic-fm Paste"
    end tell
end tell
```

**Response:** `{ "success": bool, "stdout": str, "stderr": str }`

If `target_file` is provided, the server uses a document-targeted AppleScript clause instead of `tell document 1`.

**Parameter passing note:** FM Pro 22 does not reliably receive script parameters via `given parameter:` in `do script`. When `parameter` is provided, the server stores it in an internal pending job before firing AppleScript. The triggered FM script then calls `GET /pending` to retrieve:

- `target`
- `auto_save`
- `select_all`

The pending job is cleared on first read.

**`auto_save` field:** Pass `"auto_save": true` to instruct `Agentic-fm Paste` to save all scripts after paste (via `Perform AppleScript: tell application "System Events" to keystroke "s" using {command down}`). Defaults to `false`.

**`select_all` field:** Pass `"select_all": false` if the receiving FM script should skip select-all before paste. Defaults to `true`.

**Requirements:**
- macOS only (`osascript` must be available on the host)
- FM Pro must be running with the target solution open
- The `fmextscriptaccess` extended privilege (**Allow Apple events and ActiveX to perform FileMaker operations**) must be enabled on the account's privilege set in Manage Security. Without it, `do script` returns a privilege violation error (`-10004`) at runtime.
- For `auto_save`: FileMaker Pro must have Accessibility access granted in System Preferences → Privacy & Security → Accessibility so System Events can send keystrokes.
- For `raw_applescript` override (Tier 3 script creation): include `"raw_applescript": "tell application..."` to execute arbitrary AppleScript instead of the default template.

---

### POST /debug

Accepts a JSON payload of runtime debug state and writes it to `agent/debug/output.json` at the repo root. Called by the Agentic-fm Debug FM script.

**Request body:** Any JSON object (typically `$$DEBUG` variable contents from FM).

**Response:** `{ "success": true, "path": "/path/to/output.json" }`

---

### GET /webviewer/status

Returns whether the companion-managed webviewer dev server is running.

**Response:**

```json
{ "running": true }
```

---

### POST /webviewer/start

Starts the `webviewer` development server by running `npm run dev` in `{repo_path}/webviewer`.

**Request body:**

```json
{ "repo_path": "/absolute/path/to/agentic-fm" }
```

**Response examples:**

```json
{ "success": true, "status": "started", "pid": 12345 }
```

```json
{ "success": true, "status": "already_running" }
```

---

### POST /webviewer/stop

Stops the companion-managed webviewer dev server process group.

**Response examples:**

```json
{ "success": true, "status": "stopped" }
```

```json
{ "success": true, "status": "not_running" }
```

---

### POST /webviewer/push

Writes an agent output payload to:

```txt
{repo_path}/agent/config/.agent-output.json
```

This is used by the webviewer to pick up agent output through polling.

**Request body:**

| Field       | Type   | Required | Description                                                     |
| ----------- | ------ | -------- | --------------------------------------------------------------- |
| `type`      | string | Yes      | One of `preview`, `diff`, `result`, `diagram`, `layout-preview` |
| `content`   | string | No       | Main content payload                                            |
| `before`    | string | No       | Optional secondary text                                         |
| `styles`    | string | No       | Optional styles field, mainly for `layout-preview`              |
| `repo_path` | string | Yes      | Repository root                                                 |

**Example request:**

```json
{
  "type": "result",
  "content": "Import completed",
  "repo_path": "/absolute/path/to/agentic-fm"
}
```

**Response:**

```json
{
  "success": true,
  "path": "/absolute/path/to/agentic-fm/agent/config/.agent-output.json"
}
```

---

## File watch and `Import.log` analysis

The new watch subsystem is designed for this workflow:

1. **A local file changes**
2. **The server detects the change automatically**
3. **New content is analyzed immediately**
4. **Results are exposed through terminal logs, JSON, SSE, and the UI**

### How the watch loop works

- **poll-based watcher**
  - implemented with Python standard library only
  - no external dependency like `watchdog`

- **single active watch**
  - only one file watch runs at a time
  - starting a new one replaces the old one

- **incremental reading**
  - remembers the current file offset
  - reads only newly appended content

- **partial line buffering**
  - incomplete last line is buffered until newline arrives

- **truncation handling**
  - if file size shrinks, offset resets automatically

### Built-in `Import.log` analysis

The import analyzers understand FileMaker `Import.log` lines in the form:

```txt
2026-03-27 16:21:28.380 +0100	ScriptName::31::New Window::LayoutDestination	11	Attribute value “OriginalLayout” unknown.
```

The parser extracts:

- **timestamp**
- **source**
- **script name**
- **script line**
- **step name**
- **attribute name**
- **code**
- **message**
- **unknown value** when the message matches `Attribute value ... unknown.`

It also classifies common FileMaker import error categories such as:

- **missing field**
- **missing table reference**
- **missing function**
- **missing script**
- **missing layout**
- **missing field reference**
- **missing attribute**
- **unknown attribute value**
- **unknown error**

### Import lifecycle tracking

The server also tracks import lifecycle lines such as:

- **`Import of script steps from clipboard started`**
- **`script steps imported : N`**
- **`Import completed`**

This allows it to build:

- **`summary.current_import`**
- **`summary.last_completed_import`**
- **`summary.recent_imports`**
- **error count during an import**
- **imported step count**
- **per-import compact error rows**
- **counts of successful vs failing imports**
- **error counts by FileMaker code**

### Terminal log output

When an issue is detected, the server logs a warning immediately. For import log issues it includes structured detail such as:

- **script**
- **line**
- **step**
- **attribute**
- **unknown value**
- **message**

Example:

```txt
2026-03-29T19:12:08 WARNING File watch match [missing_function]: script=ScriptName line=31 step=Set Variable attribute= value= message=Function referred to in the calculation “cf_Param_Get ( "Prozess" )” is missing.
```

---

## Security

**This project is designed exclusively for local development.** It assumes you are working on your own machine, behind a firewall, on a private network. It is not hardened for production use, multi-user environments, or any network-accessible deployment. Do not use it on a public network or expose any part of it to the internet.

The server binds exclusively to `127.0.0.1` (localhost) by default. It is not reachable from other machines on the network — only processes running on the same machine can connect. No authentication is implemented, which is acceptable because the attack surface is limited to local processes already running under the same user account.

Do not change `BIND_HOST` to `0.0.0.0` or expose the server through a reverse proxy. The `/explode` endpoint executes arbitrary shell scripts with the permissions of the user who started the server.

> **Note for Docker users:** When running the agent in a container, `COMPANION_BIND_HOST=0.0.0.0` is required so the container can reach the host-side server. This is still safe as long as the host machine is on a private, firewalled network — the port should never be forwarded to a public interface.

---

## FileMaker integration

### Explode XML flow

FileMaker can call the Companion Server from an **Insert from URL** step.

Typical explode configuration:

- **URL**
  - `http://localhost:8765/explode`

- **Method**
  - `POST`

- **Headers**
  - `Content-Type: application/json`

- **cURL body**
  - assembled JSON payload

Example payload:

```json
{
  "solution_name": "Invoice Solution",
  "export_file_path": "/Users/yourname/Desktop/InvoiceSolution.xml",
  "repo_path": "/Users/yourname/agentic-fm"
}
```

After Insert from URL completes, the FileMaker script can parse the response JSON and branch on `success`.

### Clipboard-import error monitoring flow

This is the new workflow enabled by the watch subsystem.

Recommended sequence:

1. **Start Import.log watch**
   - call `POST /watch/import-log/start`
   - use `import_log`

2. **Import script steps from clipboard in FileMaker**

3. **Read results**
   - poll `GET /watch/results`
   - or keep `GET /watch/stream` open
   - or inspect `/watch/ui`
   - or watch the terminal where the server is running

This is useful when AI-generated script steps contain unknown parameters, missing references, layout/script/function issues, or any other non-zero import log errors.

### Pending job flow for paste automation

The server-side pending job exists because direct AppleScript parameter passing is unreliable in FM Pro 22.

Typical pattern:

1. **Call `POST /trigger`** with `parameter`, `auto_save`, and optional `select_all`
2. **Server stores pending job**
3. **AppleScript triggers FM script**
4. **FM script calls `GET /pending`**
5. **FM script consumes target/options and continues**

### Watch UI usage

If you want a browser-based monitor during development:

```txt
http://127.0.0.1:8765/watch/ui
```

This is useful when you want a persistent live view without manually polling the JSON endpoints.

The current Watch UI is optimized around grouped import sessions instead of a flat recent-event list.

Each import card shows:

- **script name**
- **import status**
- **imported line count**
- **error count**
- **compact error rows with line number first**
- **a no-error message for successful imports**

---

## Troubleshooting

### Port already in use

```
OSError: [Errno 48] Address already in use
```

Another process — possibly a previous instance of the companion server — is already bound to port 8765. Find and stop it:

```bash
lsof -i :8765
kill <PID>
```

Or start the server on a different port and update the URL in the FileMaker companion script:

```bash
python3 agent/scripts/companion_server.py --port 9000
```

### Server not running — FileMaker shows a connection dialog

If the server is not running when FileMaker executes Insert from URL, FileMaker displays a dialog: *"The URL could not be found."* or a similar network error. This is not a script logic failure — it means the companion server is not listening.

Start the server and retry, or check whether the launchd plist is loaded if auto-start is configured.

### fmparse.sh not found

The server constructs the `fmparse.sh` path as `{repo_path}/fmparse.sh`. If `repo_path` is wrong or the file is missing, `fmparse.sh` will fail to launch and the response will contain:

```json
{
  "success": false,
  "exit_code": -1,
  "error": "[Errno 2] No such file or directory: '/path/to/fmparse.sh'"
}
```

Verify that `repo_path` in the JSON payload matches the actual location of the agentic-fm repository root, and that `fmparse.sh` exists there:

```bash
ls /Users/yourname/agentic-fm/fmparse.sh
```

### Permission denied on fm-xml-export-exploder binary

`fmparse.sh` calls `fm-xml-export-exploder`. If the binary is not executable, the subprocess will fail with exit code 1 and `stderr` will contain a permission error. Fix it:

```bash
chmod +x ~/bin/fm-xml-export-exploder
```

If the binary is in a non-standard location and not on `PATH`, supply its full path in the `exploder_bin_path` field of the request payload.

### `Import.log` watch starts but no events appear

Check these first:

- **wrong file path**
  - use `GET /watch/results` or `/watch/ui` to inspect `path` and `file_exists`

- **watch attached at end of file**
  - default behavior is `start_at_end: true`
  - only lines appended after watch start are analyzed

- **wrong analyzer**
  - `import_log_unknown_attributes` only reports unknown attribute values
  - use `import_log` if you want all non-zero import log issues

- **no newline yet**
  - partial final lines are buffered until newline arrives

### `Import.log` path resolution is wrong

Use `POST /watch/import-log/start` response fields:

- `location`
- `resolved_path`

If needed, override everything with:

```json
{
  "import_log_path": "/absolute/path/to/Import.log"
}
```

### SSE stream does not update

Verify:

- **the watch is actually running**
  - `GET /watch/results`

- **you are connected to `/watch/stream`**
  - browser dev tools network tab helps here

- **state revision is changing**
  - `revision` increments when watch state changes

If no new events occur, periodic `ping` events keep the connection alive.

### Built-in watch UI loads but shows no data

The UI depends on:

- `GET /watch/results`
- `GET /watch/stream`

The path picker buttons also depend on:

- `POST /watch/pick-path`

If those endpoints fail, inspect the browser console and the server terminal log.

### Path picker buttons fail

The Watch UI path picker tries multiple host-native strategies.

Behavior:

- **first choice**
  - `tkinter`, if available in the Python build

- **macOS fallback**
  - `osascript`

- **Windows fallback**
  - PowerShell dialogs

If clicking a picker button still fails:

- **restart the Companion Server**
  - old running code may still be active

- **check the server terminal log**
  - picker failures are logged there

- **enter the path manually**
  - all picker fields still accept direct text input

### `POST /trigger` fails

Common causes:

- **FileMaker not running**
- **wrong `fm_app_name`**
- **target file not open** when using `target_file`
- **missing `fmextscriptaccess` privilege**
- **Accessibility permissions missing** when workflow depends on System Events keystrokes

Typical failures return in `stderr`.

### `/webviewer/start` fails

Check:

- `repo_path` is correct
- `{repo_path}/webviewer` exists
- Node/npm are installed
- the webviewer project can run `npm run dev`

### Diagnosing failures from the server log

When the server is running in the foreground (or writing to a log file), each request produces timestamped output:

```
2026-03-09T14:25:10 INFO 127.0.0.1 - "POST /explode HTTP/1.1" 200 -
2026-03-09T14:25:10 INFO Running fmparse.sh: solution='Invoice Solution' export='/Users/yourname/Desktop/InvoiceSolution.xml' cwd='/Users/yourname/agentic-fm'
2026-03-09T14:25:12 INFO fmparse.sh exited with code 0
```

For watch issues, the server log is also the first place to inspect because watch matches are emitted there immediately.

The `stdout` and `stderr` fields in the response body remain the first place to inspect when `/explode` returns a non-zero exit code.
