"""How one Haiku attempt's CLI session starts: command line, environment, system prompt, effort, output cap.

Every attempt (raw and readable) pays for what the CLI sends before the first useful token: the system prompt, the
tool definitions, and the task. So the session gets:
- only the tools the job needs (Read, Edit, Write; Bash allowed only `python check.py`), no MCP servers, no slash
  commands or skills, safe and restricted mode (no hooks, plugins' customisations, settings files);
- a short constant system prompt (`agent_system_prompt`: "lean", the default) instead of the CLI's own, which is
  written for interactive coding. It is the same text for every session and project, so it stays a cached prefix;
  the per-function facts (working directory, task) are in the first user message. "cli" keeps the CLI's prompt;
- no CLAUDE.md and no auto-memory from the user profile or parent folders (safe mode, plus the CLI's own switches
  CLAUDE_CODE_DISABLE_CLAUDE_MDS and CLAUDE_CODE_DISABLE_AUTO_MEMORY in its environment).
Nothing here depends on a project.
"""
import json
import os
import sys

from . import paths

SYSTEM_PROMPT = (
    "You are a worker in an automated decompilation pipeline, running headless: no person reads your messages, and "
    "a machine judges your work. The first message is your task; it names your working directory, which holds "
    "every file you need. The file tools are confined to that directory: read files with Read (absolute paths), and "
    "change only work.c, with Write or Edit. Bash runs exactly one command, `python check.py`; anything else is "
    "refused (\"Permission to use Bash has been denied\": run exactly `python check.py` instead, nothing else will "
    "work). Follow the task's procedure and end with the one-line result it asks for.")

AGENT_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

EFFORTS = ("low", "medium", "high", "xhigh", "max")


def effort_value(value):
    """A config or command-line effort: one of EFFORTS, or None for the CLI default ("default", null)."""
    if value in (None, "", "default"):
        return None
    if value not in EFFORTS:
        raise ValueError(f"effort {value!r}: use one of {', '.join(EFFORTS)} or default")
    return value


def readable_effort(cfg, override=None, raw_bytes=None):
    """The effort of a readable attempt: --effort, else config `readable_effort_large` for a function whose raw.c is
    over `readable_large_bytes` (batch 26; raw_bytes), else config `readable_effort`, else `effort` (raw matching)."""
    if override is not None:
        return effort_value(override)
    if raw_bytes is not None and is_large(cfg, raw_bytes) and cfg.get("readable_effort_large") is not None:
        return effort_value(cfg["readable_effort_large"])
    if cfg.get("readable_effort") is not None:
        return effort_value(cfg["readable_effort"])
    return effort_value(cfg.get("effort"))


LARGE_BYTES = 4096


def is_large(cfg, raw_bytes):
    """A large function (batch 26): its raw.c is over config `readable_large_bytes` (default 4096; 0 or null: none
    is). Large ones get `readable_effort_large` and the TASK.md note for long functions."""
    limit = cfg.get("readable_large_bytes", LARGE_BYTES)
    return bool(limit) and raw_bytes > int(limit)


def claude_command(cfg):
    override = os.environ.get("TCD_AGENT_COMMAND")  # JSON list; used for dry runs with tools/fake_agent.py
    if override or cfg.get("agent_command"):
        cmd = list(json.loads(override) if override else cfg["agent_command"])
        return [sys.executable if c == "{python}" else c.replace("{root}", str(paths.TOOL)) for c in cmd]
    lean = (cfg.get("agent_system_prompt") or "lean") == "lean"
    return [
        paths.resolve_exe(cfg["claude"]), "-p", "--model", cfg["model"],
        "--output-format", "stream-json", "--verbose",
        "--safe-mode", "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
        "--permission-mode", "dontAsk",
        "--tools", "Read,Edit,Write,Bash",
        "--allowedTools", "Read,Edit(./work.c),Write(./work.c),Bash(python check.py)",
        "--max-budget-usd", str(cfg["attempt_budget_usd"]),
        *(["--system-prompt", SYSTEM_PROMPT] if lean else []),
        *(["--effort", cfg["effort"]] if cfg.get("effort") else []),  # null = CLI default (adaptive per turn)
    ]


def agent_env():
    """The session's environment: ours, plus the CLI switches that keep CLAUDE.md files and memory out."""
    return dict(os.environ, **AGENT_ENV)


class OutputCap:
    """Counts a session's output tokens (thinking included) from its stream events; set() once they pass cap.

    Used as run_bounded's stop flag together with the caller's own (stop: an Event or None)."""

    def __init__(self, cap, stop=None):
        self.cap, self.stop, self.by_msg, self.hit = cap, stop, {}, False

    def feed(self, ev):
        if not self.cap or ev.get("type") != "assistant":
            return
        msg = ev.get("message") or {}
        out = (msg.get("usage") or {}).get("output_tokens") or 0
        key = msg.get("id") or id(msg)
        self.by_msg[key] = max(self.by_msg.get(key, 0), out)
        if sum(self.by_msg.values()) > self.cap:
            self.hit = True

    def is_set(self):
        return self.hit or bool(self.stop is not None and self.stop.is_set())
