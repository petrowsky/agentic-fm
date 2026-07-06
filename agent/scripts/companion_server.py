#!/usr/bin/env python3
"""
companion_server.py - Lightweight HTTP companion server for agentic-fm.

Lightweight HTTP companion server for shell command execution. FileMaker
calls this server via the native Insert from URL step (curl-compatible).

Usage:
    Start server:
        python agent/scripts/companion_server.py

    Start on custom port:
        python agent/scripts/companion_server.py --port 9000

    FileMaker calls it via Insert from URL:
        POST http://localhost:8765/explode
"""

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Built-in defaults — the bottom of the resolution chain, used when neither a
# CLI flag, an env var, nor companion.json supplies a value (so the server always
# boots). See "Resolution precedence" in agent/docs/COMPANION_SERVER.md.
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ADVERTISE_HOST = "local.hub"
# Idle auto-shutdown: seconds with no requests before the server exits.
# 0 disables it (always-on) — the default, so existing behaviour is unchanged.
DEFAULT_IDLE_TIMEOUT = 0
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/petrowsky/agentic-fm/main/version.txt"

# Canonical companion config (agent/config/companion.json). Optional and gitignored;
# absent/invalid → built-in defaults. It is the single source of truth for the
# server's bind host/port and the address clients use to reach it.
COMPANION_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "companion.json"
)

# Read version from version.txt at the repo root
def _read_local_version() -> str:
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        version_file = os.path.join(here, "..", "..", "version.txt")
        with open(version_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown"

VERSION = _read_local_version()

# ---------------------------------------------------------------------------
# Path helpers (WSL2 / Windows compatibility)
# ---------------------------------------------------------------------------

def _wsl_translate_path(path: str) -> str:
    """Translate FileMaker's Windows-POSIX paths (/C:/...) to WSL2 mount points (/mnt/c/...).

    FileMaker on Windows uses ConvertFromFileMakerPath(...; 1) which produces paths
    like /C:/Users/... instead of the WSL2 equivalent /mnt/c/Users/...
    """
    m = re.match(r"^/([A-Za-z]):(.*)", path)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2)
        return f"/mnt/{drive}{rest}"
    return path


def _load_automation_config() -> dict:
    """Load agent/config/automation.json relative to this script, if present."""
    here = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(here, "..", "config", "automation.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


_automation_config: dict = _load_automation_config()


def _resolve_export_path_for_wsl(solution_name: str) -> str:
    """Resolve the FM export file path when FileMaker sends '?' on WSL2.

    FileMaker on Windows exports via Get(DesktopPath) & Get(FileName) & ".xml".
    When ConvertFromFileMakerPath fails (returns '?'), we reconstruct the path
    by querying Windows for the actual Desktop shell folder via PowerShell
    (handles OneDrive-redirected Desktops), then converting with wslpath.

    Falls back to export_dir_override in automation.json if auto-detection fails.
    """
    # Config override takes precedence — skip auto-detection entirely
    export_dir = _automation_config.get("export_dir_override", "")
    if export_dir:
        path = os.path.join(os.path.expanduser(export_dir), f"{solution_name}.xml")
        log.info("Using export_dir_override from automation.json: %r", path)
        return path

    # Auto-detect via PowerShell + wslpath (WSL2 only).
    # GetFolderPath('Desktop') resolves OneDrive-redirected Desktops correctly;
    # a simple USERPROFILE\Desktop approach misses this common Windows setup.
    try:
        r1 = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                "[Environment]::GetFolderPath('Desktop')",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if r1.returncode == 0:
            win_desktop = r1.stdout.strip()  # e.g. C:\Users\pchov\OneDrive\Desktop
            r2 = subprocess.run(
                ["wslpath", win_desktop],
                capture_output=True, text=True, timeout=5,
            )
            if r2.returncode == 0:
                wsl_desktop = r2.stdout.strip()  # e.g. /mnt/c/Users/pchov/OneDrive/Desktop
                path = os.path.join(wsl_desktop, f"{solution_name}.xml")
                log.info("Auto-detected WSL2 export path via PowerShell+wslpath: %r", path)
                return path
    except Exception as exc:
        log.warning("WSL2 export path auto-detection failed: %s", exc)

    return ""

# ---------------------------------------------------------------------------
# Webviewer process state (module-level, shared across request threads)
# ---------------------------------------------------------------------------

_webviewer_proc: "subprocess.Popen | None" = None
_webviewer_lock = threading.Lock()

# Pending paste job — set by /trigger before firing AppleScript,
# consumed by Agentic-fm Paste via GET /pending.
# Shape: {"target": str, "auto_save": bool}
_pending_job: dict = {}
_pending_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Plug-in capability detection (the AgenticFM commercial plug-in, optional)
# ---------------------------------------------------------------------------
#
# The companion is the single detection broker for the OSS agent. It stats the
# plug-in's macOS Application Support tree (the *installed?* precondition),
# reads the address + bearer token published in preferences.json, then probes
# the plug-in's token-free GET /api/health to resolve the *usable?* verdict
# (installed + reachable + licensed). The result is surfaced in /health.plugin
# so the agent makes exactly one detection call. The verdict is cached briefly
# so repeated /health hits do not re-probe the plug-in on every request.
#
# This is detection only — nothing here makes any OSS feature depend on the
# plug-in. When the plug-in is absent or not usable, the block reports it and
# the agent stays on the pure OSS path.

# NOTE: intentionally a fixed macOS platform path, NOT configurable via companion.json.
# It has exactly one correct value; only `~` varies, resolving to the macOS user running
# the companion — which must be the same user running FileMaker + the plug-in. A relocated
# or cross-user path is a deployment error to document, not a knob. See the plug-in
# detection note in agent/docs/COMPANION_SERVER.md.
APP_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/AgenticFM")
_PLUGIN_CACHE_TTL = 60.0  # seconds; plug-in's own license heartbeat is far slower
_plugin_cache: dict = {}
_plugin_cache_ts: float = 0.0
_plugin_lock = threading.Lock()

# Behavior gates the plug-in enforces on mutating operations — surfaced so the
# OSS agent can respect them rather than stacking a redundant confirmation.
_PLUGIN_GATE_KEYS = (
    "agenticActions", "scriptExecution", "scriptModification",
    "schemaModification", "uiInteraction", "hostCapture",
)


def _load_automation_config() -> dict:
    """Read agent/config/automation.json. Returns {} if missing/invalid."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "config", "automation.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _plugin_http_get_json(url: str, token: str = "", timeout: int = 3) -> dict:
    """GET a JSON document from the plug-in's HTTP server. Raises on failure."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _plugin_read_preferences() -> dict:
    """Read the plug-in's preferences.json (address, token, gates, bindings)."""
    path = os.path.join(APP_SUPPORT_DIR, "preferences.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _plugin_enumerate_solutions() -> list:
    """Enumerate indexed solutions under solutions/<key>/manifest.json."""
    solutions = []
    sol_dir = os.path.join(APP_SUPPORT_DIR, "solutions")
    if not os.path.isdir(sol_dir):
        return solutions
    for key in sorted(os.listdir(sol_dir)):
        manifest_path = os.path.join(sol_dir, key, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        solutions.append({
            "key": key,
            "name": manifest.get("solutionName", ""),
            "catalog": os.path.join(sol_dir, key),
            "files": manifest.get("loaded", []),
            "parsed_at": manifest.get("parsedAt") or manifest.get("parsed_at", ""),
        })
    return solutions


def _probe_plugin() -> dict:
    """Resolve the full plug-in capability block. Never raises.

    Shape:
      {
        "installed": bool,            # App Support tree present
        "usable": bool,               # installed AND reachable AND licensed
        "server": {"reachable", "base", "token", "remote"},
        "license": {"status", "licensed", ...},
        "solutions": [ {key, name, catalog, files, parsed_at}, ... ],
        "repoPath"?, "catalogBaseUrl"?, "gates"?
      }
    """
    block = {"installed": False, "usable": False,
             "server": {}, "license": {}, "solutions": []}

    if not os.path.isdir(APP_SUPPORT_DIR):
        return block
    block["installed"] = True

    try:
        prefs = _plugin_read_preferences()
    except (OSError, ValueError):
        prefs = {}

    if prefs.get("repoPath"):
        block["repoPath"] = prefs["repoPath"]
    if prefs.get("catalogBaseUrl"):
        block["catalogBaseUrl"] = prefs["catalogBaseUrl"]
    gates = {k: prefs[k] for k in _PLUGIN_GATE_KEYS if k in prefs}
    if gates:
        block["gates"] = gates

    # Remote access is the supported posture: a stable port + token in
    # preferences.json. (Localhost-only random-port mode is not targeted.)
    port = prefs.get("remoteAccessPort", 8766)
    token = prefs.get("remoteAccessToken", "")
    remote_on = bool(prefs.get("allowRemoteAccess")) and bool(prefs.get("remoteAccessConfirmed"))
    base = f"http://127.0.0.1:{port}"
    block["server"] = {"reachable": False, "base": base, "token": token, "remote": remote_on}

    # Token-free GET /api/health carries the verdict: licensed + licenseStatus.
    try:
        health = _plugin_http_get_json(f"{base}/api/health", timeout=3)
        block["server"]["reachable"] = True
        licensed = bool(health.get("licensed"))
        lic = {"status": health.get("licenseStatus", ""), "licensed": licensed}
        # Pull richer detail (daysRemaining etc.) — exempt, best-effort.
        try:
            detail = _plugin_http_get_json(f"{base}/api/license", token=token, timeout=3)
            for k in ("status", "edition", "sub", "exp", "daysRemaining"):
                if k in detail:
                    lic[k] = detail[k]
        except Exception:
            pass
        block["license"] = lic
        block["usable"] = bool(licensed)
    except Exception:
        # Installed but server not reachable (or down) — not usable.
        block["license"] = {"status": "unknown", "licensed": False}
        block["usable"] = False

    # Broker the plug-in's self-describing endpoint suite. GET /api/discover is
    # token-free (exempt) and returns the *live*, license-gated catalog: the
    # full endpoint suite when usable, a shrunk list + purchaseUrl when locked.
    # The OSS agent reads this to choose endpoints for the task at hand rather
    # than assuming a fixed set — so the integration never hardcodes (or rots
    # against) the plug-in's API surface. Surfaced even when not usable so the
    # locked-state purchaseUrl is available for the lapsed-license nudge.
    if block["server"].get("reachable"):
        try:
            block["discover"] = _plugin_http_get_json(f"{base}/api/discover", timeout=3)
        except Exception:
            pass

    block["solutions"] = _plugin_enumerate_solutions()
    return block


def _check_plugin(force: bool = False) -> dict:
    """Return the plug-in capability block, cached ~60 s. Never raises."""
    global _plugin_cache, _plugin_cache_ts
    with _plugin_lock:
        now = time.monotonic()
        if not force and _plugin_cache and (now - _plugin_cache_ts) < _PLUGIN_CACHE_TTL:
            return _plugin_cache
        block = _probe_plugin()
        _plugin_cache = block
        _plugin_cache_ts = now
        return block

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("companion_server")
SUBPROCESS_HEARTBEAT_SECONDS = 5


def _stream_pipe(pipe, level, prefix, output_buffer, state):
    """Copy a subprocess pipe to the logger in real time while buffering it."""
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            output_buffer.append(line)
            with state["lock"]:
                state["last_output_at"] = time.monotonic()
            level("%s%s", prefix, line.rstrip("\n"))
    finally:
        pipe.close()


def _run_command_streaming(cmd, *, cwd, env, label):
    """Run a command, stream its output to the server log, and capture it."""
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state = {"last_output_at": time.monotonic(), "lock": threading.Lock()}

    stdout_thread = threading.Thread(
        target=_stream_pipe,
        args=(process.stdout, log.info, f"[{label} stdout] ", stdout_chunks, state),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_stream_pipe,
        args=(process.stderr, log.warning, f"[{label} stderr] ", stderr_chunks, state),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    last_heartbeat_at = time.monotonic()
    while True:
        try:
            return_code = process.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            with state["lock"]:
                silence_for = now - state["last_output_at"]
            if (
                silence_for >= SUBPROCESS_HEARTBEAT_SECONDS
                and now - last_heartbeat_at >= SUBPROCESS_HEARTBEAT_SECONDS
            ):
                log.info(
                    "%s still running... (%ds since last output)",
                    label,
                    int(silence_for),
                )
                last_heartbeat_at = now

    stdout_thread.join()
    stderr_thread.join()

    return {
        "returncode": return_code,
        "stdout": "".join(stdout_chunks),
        "stderr": "".join(stderr_chunks),
    }


# ---------------------------------------------------------------------------
# Idle activity tracking
# ---------------------------------------------------------------------------

_last_activity = time.monotonic()
_activity_lock = threading.Lock()


def _touch_activity() -> None:
    global _last_activity
    with _activity_lock:
        _last_activity = time.monotonic()


def _seconds_since_activity() -> float:
    with _activity_lock:
        return time.monotonic() - _last_activity


def _idle_watchdog(server, idle_timeout: int) -> None:
    """Shut the server down after idle_timeout seconds with no requests."""
    check_interval = min(30, idle_timeout)
    while True:
        time.sleep(check_interval)
        idle = _seconds_since_activity()
        if idle >= idle_timeout:
            log.info("Idle for %.0fs (timeout %ds) — shutting down.", idle, idle_timeout)
            server.shutdown()  # unblocks serve_forever() in the main thread
            return


# ---------------------------------------------------------------------------
# Threading HTTP server (handles concurrent requests)
# ---------------------------------------------------------------------------

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer with thread-per-request concurrency."""
    daemon_threads = True


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class CompanionHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        """Route access log through the standard logger."""
        log.info("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self):
        # /health is a liveness probe — used by monitors and the status-bar
        # marker, which poll it continuously. It must NOT count as activity, or
        # that polling would keep the idle watchdog from ever firing and defeat
        # the auto-shutdown. Every other endpoint is real work and does count.
        if self.path != "/health":
            _touch_activity()
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/pending":
            self._handle_pending_get()
        elif self.path == "/webviewer/status":
            self._handle_webviewer_status()
        elif self.path == "/clipboard":
            self._handle_clipboard_read()
        elif self.path.startswith("/preview/"):
            layout_name = self.path[len("/preview/"):]
            self._handle_preview_get(layout_name)
        elif self.path.startswith("/plugin/"):
            self._handle_plugin_proxy("GET", self.path[len("/plugin/"):])
        elif self.path.startswith("/whoami"):
            self._handle_whoami()
        else:
            self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        _touch_activity()
        if self.path == "/explode":
            self._handle_explode()
        elif self.path == "/context":
            self._handle_context()
        elif self.path == "/debug":
            self._handle_debug()
        elif self.path == "/clipboard":
            self._handle_clipboard()
        elif self.path == "/trigger":
            self._handle_trigger()
        elif self.path == "/pending":
            self._handle_pending_post()
        elif self.path == "/webviewer/start":
            self._handle_webviewer_start()
        elif self.path == "/webviewer/stop":
            self._handle_webviewer_stop()
        elif self.path == "/webviewer/push":
            self._handle_webviewer_push()
        elif self.path == "/lint":
            self._handle_lint()
        elif self.path.startswith("/preview/"):
            layout_name = self.path[len("/preview/"):]
            self._handle_preview_post(layout_name)
        elif self.path.startswith("/plugin/"):
            self._handle_plugin_proxy("POST", self.path[len("/plugin/"):])
        else:
            self._send_json({"error": "Not found"}, status=404)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_health(self):
        self._send_json({"status": "ok", "version": VERSION, "plugin": _check_plugin()})

    def _handle_whoami(self):
        """Resolve per-developer config (repo path, companion host/port) by account.

        Lets `Get agentic-fm path` ask the companion who it's talking to
        instead of hardcoding a chain of `If Get(AccountName) = "..."`
        branches inside the FM script. Reads the optional `users` section of
        automation.json, keyed by the FileMaker account name:

            GET /whoami?account=<name>

        Returns the matching block, or 400 if the account isn't configured
        (or automation.json has no `users` section at all) — callers should
        fall back to the existing folder-picker/cached-global behavior.
        """
        query = urllib.parse.urlparse(self.path).query
        account = (urllib.parse.parse_qs(query).get("account") or [""])[0]
        if not account:
            self._send_json({"error": "Missing required 'account' query parameter"}, status=400)
            return
        users = _load_automation_config().get("users", {})
        user_block = users.get(account)
        if user_block is None:
            self._send_json({"error": f"Account '{account}' not configured"}, status=400)
            return
        self._send_json({"account": account, **user_block})

    def _handle_plugin_proxy(self, method: str, subpath: str):
        """Thin pass-through to the plug-in's HTTP server, bearer token injected.

        Optional convenience for constrained environments (e.g. a container that
        can reach the companion but not the plug-in's port) and to keep a single
        base URL. Direct plug-in access is the baseline; this proxy is inert
        until explicitly called. Refuses with 502 when the plug-in is not usable,
        so the agent's fallback stays self-enforcing.
        """
        block = _check_plugin()
        if not block.get("usable"):
            self._send_json(
                {"success": False, "error": "Plug-in not usable",
                 "plugin": {"installed": block.get("installed", False), "usable": False}},
                status=502,
            )
            return

        server = block.get("server", {})
        base = server.get("base", "").rstrip("/")
        token = server.get("token", "")
        url = f"{base}/{subpath}"

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = None
        if method == "POST":
            body = self._read_body()
            headers["Content-Type"] = self.headers.get("Content-Type", "application/json")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                status = resp.status
                ctype = resp.headers.get("Content-Type", "application/json; charset=utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            ctype = exc.headers.get("Content-Type", "application/json; charset=utf-8")
        except Exception as exc:
            log.warning("Plug-in proxy to %s failed: %s", url, exc)
            self._send_json({"success": False, "error": f"Proxy failed: {exc}"}, status=502)
            return

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle_explode(self):
        # Read and parse request body
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json(
                {"success": False, "exit_code": -1, "error": f"Invalid request: {exc}"},
                status=400,
            )
            return

        # Validate required fields
        missing = [
            f for f in ("solution_name", "export_file_path", "repo_path")
            if not payload.get(f)
        ]
        if missing:
            self._send_json(
                {
                    "success": False,
                    "exit_code": -1,
                    "error": f"Missing required fields: {', '.join(missing)}",
                },
                status=400,
            )
            return

        solution_name = payload["solution_name"]
        export_file_path = payload["export_file_path"]
        repo_path = payload["repo_path"]
        exploder_bin_path = payload.get("exploder_bin_path", "")

        # Expand ~ in paths
        repo_path = os.path.expanduser(repo_path)
        export_file_path = os.path.expanduser(export_file_path)

        # Translate Windows-POSIX paths (/C:/...) to WSL2 mount points (/mnt/c/...).
        # FileMaker on Windows uses ConvertFromFileMakerPath(...; 1) which produces
        # paths like /C:/Users/... that are invalid in WSL2.
        export_file_path = _wsl_translate_path(export_file_path)
        repo_path = _wsl_translate_path(repo_path)

        # If repo_path is "?" it means FileMaker's ConvertFromFileMakerPath failed —
        # this happens on WSL2 when the repo lives on the WSL filesystem and FileMaker
        # on Windows cannot convert the \\wsl.localhost\... UNC path to a POSIX path.
        # Fall back to repo_path_override from agent/config/automation.json.
        if repo_path == "?":
            override = _automation_config.get("repo_path_override", "")
            if override:
                repo_path = os.path.expanduser(override)
                log.info(
                    "repo_path was '?' — using repo_path_override from automation.json: %r",
                    repo_path,
                )
            else:
                self._send_json(
                    {
                        "success": False,
                        "exit_code": -1,
                        "error": (
                            "repo_path is '?' — FileMaker could not convert the repo path to POSIX "
                            "(common on WSL2 when the repo is on the WSL filesystem). "
                            "Add 'repo_path_override' to agent/config/automation.json with the "
                            "absolute POSIX path to your agentic-fm folder (e.g. /home/user/agentic-fm). "
                            "See agent/docs/SANDBOXED_ENVIRONMENT.md for details."
                        ),
                    },
                    status=400,
                )
                return

        # If export_file_path is "?" FileMaker's ConvertFromFileMakerPath failed to
        # produce a POSIX path for the Windows Desktop location. Reconstruct it from
        # the Windows USERPROFILE env var via cmd.exe + wslpath, or from automation.json.
        if export_file_path == "?":
            resolved = _resolve_export_path_for_wsl(solution_name)
            if resolved:
                export_file_path = resolved
            else:
                self._send_json(
                    {
                        "success": False,
                        "exit_code": -1,
                        "error": (
                            "export_file_path is '?' — FileMaker could not convert the Desktop path to POSIX "
                            "and WSL2 auto-detection failed. "
                            "Add 'export_dir_override' to agent/config/automation.json with the WSL2 path "
                            "to your Windows Desktop (e.g. \"/mnt/c/Users/<you>/Desktop\"). "
                            "See agent/docs/SANDBOXED_ENVIRONMENT.md for details."
                        ),
                    },
                    status=400,
                )
                return

        # Build environment for subprocess
        env = os.environ.copy()
        if exploder_bin_path:
            env["FM_XML_EXPLODER_BIN"] = os.path.expanduser(exploder_bin_path)

        # Build command: {repo_path}/fmparse.sh -s "{solution_name}" "{export_file_path}"
        fmparse = os.path.join(repo_path, "fmparse.sh")
        cmd = [fmparse, "-s", solution_name, export_file_path]

        log.info(
            "Running fmparse.sh: solution=%r export=%r cwd=%r",
            solution_name,
            export_file_path,
            repo_path,
        )

        try:
            result = _run_command_streaming(
                cmd,
                cwd=repo_path,
                env=env,
                label="fmparse.sh",
            )

            success = result["returncode"] == 0
            response = {
                "success": success,
                "exit_code": result["returncode"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
            status = 200 if success else 500

            log.info(
                "fmparse.sh exited with code %d", result["returncode"]
            )

        except Exception as exc:
            log.exception("Exception running fmparse.sh: %s", exc)
            response = {
                "success": False,
                "exit_code": -1,
                "error": str(exc),
            }
            status = 500

        self._send_json(response, status=status)

    def _handle_context(self):
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        missing = [f for f in ("repo_path", "context") if not payload.get(f)]
        if missing:
            self._send_json(
                {"success": False, "error": f"Missing required fields: {', '.join(missing)}"},
                status=400,
            )
            return

        repo_path = os.path.expanduser(payload["repo_path"])
        context = payload["context"]

        # Accept context as a pre-serialised string or a parsed object
        if isinstance(context, str):
            try:
                json.loads(context)  # validate only
            except ValueError as exc:
                self._send_json({"success": False, "error": f"Invalid context JSON: {exc}"}, status=400)
                return
            context_str = context
        else:
            context_str = json.dumps(context, indent=2, ensure_ascii=False)

        output_path = os.path.join(repo_path, "agent", "CONTEXT.json")

        # Check context_version and warn if outdated
        CONTEXT_VERSION_CURRENT = 2
        try:
            ctx_data = json.loads(context_str) if isinstance(context_str, str) else context
            ctx_version = ctx_data.get("context_version")
            if ctx_version is None or ctx_version < CONTEXT_VERSION_CURRENT:
                log.warning(
                    "CONTEXT.json has context_version=%s (current is %s). "
                    "Update the Context() custom function in your solution.",
                    ctx_version, CONTEXT_VERSION_CURRENT,
                )
        except (ValueError, TypeError, AttributeError):
            pass

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(context_str)
            log.info("CONTEXT.json written to %s", output_path)
            self._send_json({"success": True, "path": output_path})
        except Exception as exc:
            log.exception("Failed to write CONTEXT.json: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_pending_get(self):
        """Return and clear the pending paste job."""
        global _pending_job
        with _pending_lock:
            job = _pending_job.copy()
            _pending_job = {}
        self._send_json(job)

    def _handle_pending_post(self):
        """Set the pending paste job."""
        global _pending_job
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return
        target = payload.get("target", "")
        auto_save = bool(payload.get("auto_save", False))
        select_all = bool(payload.get("select_all", True))
        with _pending_lock:
            _pending_job = {"target": target, "auto_save": auto_save, "select_all": select_all}
        log.info("Pending job set: target=%r auto_save=%s select_all=%s", target, auto_save, select_all)
        self._send_json({"success": True})

    def _handle_trigger(self):
        """
        Trigger FM Pro to perform a named script via osascript.

        Payload: { "fm_app_name": "FileMaker Pro — ...", "script": "name", "parameter": "..." }
        Returns: { "success": bool, "stdout": str, "stderr": str }
        """
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        fm_app = payload.get("fm_app_name", "FileMaker Pro")
        script = payload.get("script", "")
        parameter = payload.get("parameter", "")
        target_file = payload.get("target_file", "")

        def as_str(s):
            """Escape double-quotes for use inside an AppleScript double-quoted string."""
            return s.replace("\\", "\\\\").replace('"', '\\"')

        # raw_applescript bypasses the FM do script path — no script name required
        raw = payload.get("raw_applescript", "")
        if raw:
            applescript = raw
        elif not script:
            self._send_json({"success": False, "error": "Missing required field: script"}, status=400)
            return
        else:
            # Store the target and auto_save flag in the pending slot so the
            # FM script can retrieve them via GET /pending (AppleScript parameter
            # passing via "given parameter:" is unreliable in FM Pro 22).
            if parameter:
                global _pending_job
                auto_save = bool(payload.get("auto_save", False))
                select_all = bool(payload.get("select_all", True))
                with _pending_lock:
                    _pending_job = {"target": parameter, "auto_save": auto_save, "select_all": select_all}
                log.info("Pending job set: target=%r auto_save=%s select_all=%s", parameter, auto_save, select_all)

            # When target_file is provided, address the specific FM document
            # by name instead of positional document 1. This ensures the
            # correct file is targeted when multiple files are open.
            if target_file:
                doc_clause = f'tell (first document whose name contains "{as_str(target_file)}")'
                log.info("Trigger: targeting document %r", target_file)
            else:
                doc_clause = "tell document 1"
                log.info("Trigger: no target_file — using document 1")

            applescript = (
                f'tell application "{as_str(fm_app)}"\n'
                f'    activate\n'
                f'    {doc_clause}\n'
                f'        do script "{as_str(script)}"\n'
                f'    end tell\n'
                f'end tell'
            )

        try:
            result = subprocess.run(
                ["osascript", "-e", applescript],
                capture_output=True, text=True, timeout=30
            )
            success = result.returncode == 0
            if success:
                log.info("Trigger: ran '%s' in %s", script, fm_app)
            else:
                log.error("Trigger failed: %s", result.stderr)
            self._send_json({
                "success": success,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            })
        except subprocess.TimeoutExpired:
            self._send_json({"success": False, "error": "osascript timed out after 30s"}, status=500)
        except FileNotFoundError:
            self._send_json({"success": False, "error": "osascript not found — is this macOS?"}, status=500)
        except Exception as exc:
            log.exception("Trigger handler error: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_clipboard_read(self):
        """Read FM objects from the macOS clipboard and return as XML."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        clipboard_py = os.path.join(script_dir, "clipboard.py")

        try:
            result = subprocess.run(
                ["python3", clipboard_py, "read"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                log.info("Clipboard read succeeded")
                self._send_json({"success": True, "xml": result.stdout})
            else:
                error = result.stderr.strip() or "No FileMaker objects on clipboard"
                log.warning("Clipboard read failed: %s", error)
                self._send_json(
                    {"success": False, "error": error},
                    status=404 if "No FileMaker" in error else 500,
                )
        except subprocess.TimeoutExpired:
            self._send_json(
                {"success": False, "error": "Clipboard read timed out"},
                status=500,
            )
        except Exception as exc:
            log.exception("Clipboard read error: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_clipboard(self):
        """Accept XML content and write it to the clipboard via clipboard.py (macOS) or clipboard_win.py (WSL2/Windows)."""
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        xml = payload.get("xml", "")
        if not xml:
            self._send_json({"success": False, "error": "Missing required field: xml"}, status=400)
            return

        import platform
        import tempfile
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # On Linux (WSL2), delegate to clipboard_win.py which uses powershell.exe.
        # On macOS, use the original clipboard.py (osascript / NSPasteboard path).
        if platform.system() == "Linux":
            clipboard_py = os.path.join(script_dir, "clipboard_win.py")
        else:
            clipboard_py = os.path.join(script_dir, "clipboard.py")

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write(xml)
                tmp_path = tmp.name

            result = subprocess.run(
                ["python3", clipboard_py, "write", tmp_path],
                capture_output=True, text=True
            )
            os.unlink(tmp_path)

            if result.returncode == 0:
                log.info("Clipboard write succeeded")
                self._send_json({"success": True})
            else:
                log.error("Clipboard write failed: %s", result.stderr)
                self._send_json(
                    {"success": False, "error": result.stderr or "clipboard script returned non-zero"},
                    status=500,
                )
        except Exception as exc:
            log.exception("Clipboard handler error: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_debug(self):
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        # Resolve repo root from script location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(os.path.dirname(script_dir))
        debug_dir = os.path.join(repo_root, "agent", "debug")
        os.makedirs(debug_dir, exist_ok=True)
        output_path = os.path.join(debug_dir, "output.json")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        log.info("Debug output written to %s", output_path)
        self._send_json({"success": True, "path": output_path})

    def _handle_webviewer_status(self):
        global _webviewer_proc
        with _webviewer_lock:
            running = _webviewer_proc is not None and _webviewer_proc.poll() is None
        self._send_json({"running": running})

    def _handle_webviewer_start(self):
        global _webviewer_proc
        try:
            body = self._read_body()
            payload = json.loads(body) if body else {}
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        repo_path = payload.get("repo_path", "")
        if not repo_path:
            self._send_json({"success": False, "error": "Missing required field: repo_path"}, status=400)
            return

        repo_path = os.path.expanduser(repo_path)
        webviewer_path = os.path.join(repo_path, "webviewer")

        with _webviewer_lock:
            if _webviewer_proc is not None and _webviewer_proc.poll() is None:
                self._send_json({"success": True, "status": "already_running"})
                return

            try:
                proc = subprocess.Popen(
                    ["npm", "run", "dev"],
                    cwd=webviewer_path,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                _webviewer_proc = proc
                log.info("Started webviewer (pid=%d) in %s", proc.pid, webviewer_path)
                self._send_json({"success": True, "status": "started", "pid": proc.pid})
            except Exception as exc:
                log.exception("Failed to start webviewer: %s", exc)
                self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_webviewer_stop(self):
        global _webviewer_proc
        with _webviewer_lock:
            if _webviewer_proc is None or _webviewer_proc.poll() is not None:
                self._send_json({"success": True, "status": "not_running"})
                return

            try:
                pgid = os.getpgid(_webviewer_proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                _webviewer_proc = None
                log.info("Stopped webviewer (process group %d)", pgid)
                self._send_json({"success": True, "status": "stopped"})
            except Exception as exc:
                log.exception("Failed to stop webviewer: %s", exc)
                self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_webviewer_push(self):
        """
        Write an agent output payload for the webviewer to pick up via polling.

        Payload: { "type": "preview"|"diff"|"result"|"diagram"|"layout-preview", "content": "...", "before": "...", "styles": "...", "repo_path": "..." }
        Returns: { "success": bool }
        """
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        payload_type = payload.get("type", "")
        if payload_type not in ("preview", "diff", "result", "diagram", "layout-preview"):
            self._send_json({"success": False, "error": f"Unknown type: {payload_type!r}. Must be preview, diff, result, diagram, or layout-preview."}, status=400)
            return

        repo_path = payload.get("repo_path", "")
        if not repo_path:
            self._send_json({"success": False, "error": "Missing required field: repo_path"}, status=400)
            return

        repo_path = os.path.expanduser(repo_path)
        output_path = os.path.join(repo_path, "agent", "config", ".agent-output.json")

        import time
        output = {
            "type": payload_type,
            "content": payload.get("content", ""),
            "before": payload.get("before", ""),
            "timestamp": time.time(),
        }
        # Include optional styles field for layout-preview payloads
        if payload.get("styles"):
            output["styles"] = payload["styles"]

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            log.info("Agent output written to %s (type=%s)", output_path, payload_type)
            self._send_json({"success": True, "path": output_path})
        except Exception as exc:
            log.exception("Failed to write agent output: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_preview_get(self, layout_name: str):
        """
        Serve a layout HTML preview.

        GET /preview/<layout_name>
        Returns: text/html — the preview file, a fallback from .agent-output.json,
                 or a placeholder message.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(os.path.dirname(here))

        # Look for agent/sandbox/{layout_name}_web.html
        html_path = os.path.join(repo_root, "agent", "sandbox", f"{layout_name}_web.html")
        if os.path.isfile(html_path):
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._send_html(content)
                return
            except OSError as exc:
                log.warning("Could not read preview file %s: %s", html_path, exc)

        # Fall back to agent/config/.agent-output.json
        output_json_path = os.path.join(repo_root, "agent", "config", ".agent-output.json")
        if os.path.isfile(output_json_path):
            try:
                with open(output_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                content = data.get("content", "")
                if content:
                    self._send_html(content)
                    return
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Could not read .agent-output.json: %s", exc)

        # Placeholder
        self._send_html(f"<h2>No preview available for {layout_name}</h2>")

    def _handle_preview_post(self, layout_name: str):
        """
        Store a layout HTML preview.

        POST /preview/<layout_name>
        Body: { "html": "...", "solution": "..." }
        Returns: { "success": true, "path": "agent/sandbox/{layout_name}_web.html" }
        """
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        html = payload.get("html", "")
        if not html:
            self._send_json({"success": False, "error": "Missing required field: html"}, status=400)
            return

        here = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(os.path.dirname(here))
        sandbox_dir = os.path.join(repo_root, "agent", "sandbox")
        out_path = os.path.join(sandbox_dir, f"{layout_name}_web.html")
        rel_path = f"agent/sandbox/{layout_name}_web.html"

        try:
            os.makedirs(sandbox_dir, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)
            log.info("Preview written to %s", out_path)
            self._send_json({"success": True, "path": rel_path})
        except Exception as exc:
            log.exception("Failed to write preview: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _handle_lint(self):
        """Lint FileMaker code via FMLint engine.

        POST /lint
        Body: { "content": "...", "format": "xml"|"hr"|null, "tier": 1|2|3|null,
                "disable": ["N003", ...] }
        Returns: LintResult as JSON
        """
        try:
            body = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return

        content = body.get("content", "")
        if not content:
            self._send_json({"error": "Missing 'content' field"}, status=400)
            return

        fmt = body.get("format")
        tier = body.get("tier")
        disabled = body.get("disable", [])

        try:
            # Resolve project root from this script's location
            here = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.join(here, "..", "..")

            sys.path.insert(0, project_root)
            from agent.fmlint import lint

            config = {}
            if disabled:
                config["disable"] = disabled
            if tier is not None:
                config["max_tier"] = tier

            result = lint(
                content,
                fmt=fmt,
                project_root=project_root,
                config=config,
            )
            self._send_json(result.to_dict())
        except Exception as e:
            logging.exception("FMLint error")
            self._send_json({"error": str(e)}, status=500)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, content: str, status: int = 200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _check_for_updates():
    """Fetch the remote version.txt and warn if a newer version is available."""
    try:
        with urllib.request.urlopen(REMOTE_VERSION_URL, timeout=5) as resp:
            remote = resp.read().decode("utf-8").strip()
        if remote and remote != VERSION:
            local_parts = tuple(int(x) for x in VERSION.split(".") if x.isdigit())
            remote_parts = tuple(int(x) for x in remote.split(".") if x.isdigit())
            if remote_parts > local_parts:
                log.warning(
                    "A new version is available: v%s (you have v%s). "
                    "Run 'git pull --ff-only' in your agentic-fm folder to update, "
                    "then restart the server. See UPDATES.md for details.",
                    remote,
                    VERSION,
                )
    except Exception:
        pass  # No network, rate-limited, etc. — fail silently


def _first_int(*candidates) -> int:
    """Return the first candidate coercible to int, skipping None/'' and bad values."""
    for c in candidates:
        if c is None or c == "":
            continue
        try:
            return int(c)
        except (TypeError, ValueError):
            continue
    return 0


def _load_companion_config() -> dict:
    """Read the `companion` block from agent/config/companion.json.

    Fail-open: an absent file returns {} silently (the common case); an unreadable
    or malformed file warns and returns {}. Either way built-in defaults apply and
    the server still boots. Mirrors deploy.py's _load_config discipline.
    """
    try:
        with open(COMPANION_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Could not read companion.json (%s) — using built-in defaults.", exc)
        return {}
    comp = data.get("companion") if isinstance(data, dict) else None
    return comp if isinstance(comp, dict) else {}


def parse_args():
    parser = argparse.ArgumentParser(
        description="agentic-fm companion server — exposes fmparse.sh over HTTP for FileMaker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Defaults are None so an unset flag falls through to env/companion.json/default
    # in main()'s resolution; passing the flag pins the value at the top of the chain.
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "Port to listen on. Overrides COMPANION_PORT and companion.json "
            f"(default: {DEFAULT_PORT})."
        ),
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        help=(
            "Seconds of inactivity before the server shuts itself down. "
            "0 disables auto-shutdown (always-on). Overrides COMPANION_IDLE_TIMEOUT "
            f"and companion.json (default: {DEFAULT_IDLE_TIMEOUT})."
        ),
    )
    return parser.parse_args()


def _shutdown_cleanup(server):
    server.server_close()
    with _webviewer_lock:
        if _webviewer_proc is not None and _webviewer_proc.poll() is None:
            try:
                pgid = os.getpgid(_webviewer_proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                log.info("Stopped webviewer (process group %d)", pgid)
            except Exception as exc:
                log.warning("Failed to stop webviewer on shutdown: %s", exc)


def main():
    args = parse_args()
    comp = _load_companion_config()

    # Server-bind resolution (highest wins): CLI flag / env var → companion.json → default.
    bind_host = (
        os.environ.get("COMPANION_BIND_HOST")
        or comp.get("bind_host")
        or DEFAULT_BIND_HOST
    )
    port = _first_int(
        args.port, os.environ.get("COMPANION_PORT"), comp.get("port"), DEFAULT_PORT
    )
    idle_timeout = _first_int(
        args.idle_timeout, os.environ.get("COMPANION_IDLE_TIMEOUT"),
        comp.get("idle_timeout_seconds"), DEFAULT_IDLE_TIMEOUT,
    )
    advertise_host = comp.get("advertise_host") or DEFAULT_ADVERTISE_HOST

    server = ThreadingHTTPServer((bind_host, port), CompanionHandler)

    log.info("companion_server v%s listening on %s:%d", VERSION, bind_host, port)
    log.info("Clients reach it at http://%s:%d (advertise_host).", advertise_host, port)
    threading.Thread(target=_check_for_updates, daemon=True).start()
    if idle_timeout > 0:
        log.info("Idle auto-shutdown: %ds with no requests.", idle_timeout)
        threading.Thread(target=_idle_watchdog, args=(server, idle_timeout), daemon=True).start()
    else:
        log.info("Idle auto-shutdown: disabled (always-on).")
    log.info("Endpoints: GET /health  GET /clipboard  GET /webviewer/status  GET /preview/<name>  GET|POST /plugin/<path>  POST /explode  POST /context  POST /clipboard  POST /trigger  POST /debug  POST /webviewer/start  POST /webviewer/stop  POST /webviewer/push  POST /preview/<name>")
    log.info("Press Ctrl-C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        _shutdown_cleanup(server)


if __name__ == "__main__":
    main()
