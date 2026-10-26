"""The readers behind tools/scorecard.py (not a tool itself). Everything here only reads: workspaces' state files, the
DecompPipeline checkout, and (for sources not landed yet) other branches through `git ls-tree` / `git show`.
"""
import fnmatch
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys

from scorecard_records import BUSY, busy_totals, oplog_records, operator_steps, whole_run  # noqa: F401  (the scorecard's)
from scorecard_records import job_days  # noqa: F401  (the scorecard's job registry section)

TOOL = pathlib.Path(__file__).resolve().parents[1]  # DecompPipeline/
if str(TOOL) not in sys.path:
    sys.path.insert(0, str(TOOL))
GIT_TIMEOUT_S = 60
SKIP_DIRS = {".git", "__pycache__", "backup", "retired", "regress", "sandbox", "harness", "matched", "matched_stale",
             "runs", "work", "packets", "oracle", "ghidra", "ghidra_slots", "export_copy", "probe_copy", "build",
             "readable", "node_modules", "extracted", "third_party", "src", "state", "import", "reference"}
MAX_DIRS = 20000   # discovery stops (and says so) past this many folders per root
MAX_DEPTH = 6


def load_json(p, default=None):
    p = pathlib.Path(p)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Sources:
    """Files of the DecompPipeline checkout `tool`, falling back to the `pending` git refs (branches not landed)."""

    def __init__(self, tool=TOOL, pending=()):
        self.tool = pathlib.Path(tool)
        self.pending = list(pending)
        self._trees = {}

    def _git(self, *args):
        r = subprocess.run(["git", "-C", str(self.tool), *args], capture_output=True, timeout=GIT_TIMEOUT_S)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None

    def tree(self, ref):
        if ref not in self._trees:
            out = self._git("ls-tree", "-r", "--name-only", ref)
            self._trees[ref] = out.splitlines() if out is not None else []
        return self._trees[ref]

    def where(self, pattern):
        """"checkout", "branch REF" (first pending ref holding a match) or None, for a glob relative to the checkout."""
        if any(self.tool.glob(pattern)) if any(c in pattern for c in "*?[") else (self.tool / pattern).exists():
            return "checkout"
        for ref in self.pending:
            if any(fnmatch.fnmatchcase(n, pattern) for n in self.tree(ref)):
                return f"branch {ref}"
        return None

    def read(self, rel):
        """(text, where) for a file relative to the checkout, else from the first pending ref holding it, else
        (None, None)."""
        p = self.tool / rel
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace"), "checkout"
        for ref in self.pending:
            if rel in self.tree(ref):
                text = self._git("show", f"{ref}:{rel}")
                if text is not None:
                    return text, f"branch {ref}"
        return None, None

    def worktrees(self):
        """[(path, branch)] of every worktree of the checkout's repository (read-only listing)."""
        out, cur = [], {}
        for line in (self._git("worktree", "list", "--porcelain") or "").splitlines() + [""]:
            if line.startswith("worktree "):
                cur = {"path": line[9:]}
            elif line.startswith("branch "):
                cur["branch"] = line[7:].removeprefix("refs/heads/")
            elif not line and cur:
                out.append((pathlib.Path(cur["path"]), cur.get("branch") or "detached"))
                cur = {}
        return out


def discover(root):
    """(workspace folders under root: project.json + state/ledger.json, truncated?). Never descends into one."""
    found, seen = [], 0
    root = pathlib.Path(root)
    if not root.is_dir():
        return found, False
    base = len(root.parts)
    for top, dirs, names in os.walk(root):
        seen += 1
        if seen > MAX_DIRS:
            return found, True
        t = pathlib.Path(top)
        if "project.json" in names and (t / "state" / "ledger.json").is_file():
            found.append(t)
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                         and len(t.parts) - base < MAX_DEPTH)
    return found, False


def game_root(ws):
    """The game folder a workspace belongs to: the nearest ancestor with port/game.json, else the folder above
    decomp/ or research/, else the workspace itself."""
    ws = pathlib.Path(ws)
    for p in [ws, *ws.parents][:4]:
        if (p / "port" / "game.json").is_file():
            return p
    for p in ws.parents:
        if p.name in ("decomp", "research"):
            return p.parent
    return ws


def scope_size(ws, ledger):
    """(functions to decompile, source): core.audit's game ranges minus the excluded tier, else the ledger's rows."""
    old = os.environ.get("DECOMP_PROJECT")
    try:
        os.environ["DECOMP_PROJECT"] = str(ws)
        from core import audit
        _, _, _, game = audit.load(ws, ledger)
        keep = {a for a, f in game.items() if f.get("tier") != "excluded"}
        return keep, "core.audit game ranges"
    except Exception as e:  # a workspace core.audit cannot read: count the ledger and say so
        return set(ledger), f"ledger rows (core.audit: {type(e).__name__})"
    finally:
        if old is None:
            os.environ.pop("DECOMP_PROJECT", None)
        else:
            os.environ["DECOMP_PROJECT"] = old


NOT_AGENT = ("import", "previous", "translated")  # attempt 0 models no agent ran (core/translate_stage.SOURCES)


def _attempt_totals(rows):
    """The agent's own attempts: count, seconds, USD, tokens; timed / costed / tokened count the attempts that carry
    each figure (one without it adds nothing, and the figure is unmeasured when none carries it)."""
    t = {"attempts": 0, "seconds": 0.0, "timed": 0, "usd": 0.0, "costed": 0, "tokens": 0, "tokened": 0}
    for r in rows:
        for a in r.get("attempts") or []:
            if a.get("model") in NOT_AGENT:
                continue
            t["attempts"] += 1
            if a.get("duration_s") is not None:
                t["seconds"] += a["duration_s"]
                t["timed"] += 1
            if a.get("cost_usd") is not None:
                t["usd"] += a["cost_usd"]
                t["costed"] += 1
            if a.get("tokens_in") is not None or a.get("tokens_out") is not None:
                t["tokens"] += (a.get("tokens_in") or 0) + (a.get("tokens_out") or 0)
                t["tokened"] += 1
    return t


def readable_time(rows):
    """Agent seconds of the readable rows whose every agent attempt carries duration_s (core/refine records it from
    2026-10-22; an older row has none and is left out, never estimated). Attempts with a "kind" (sync, draft, readopt,
    lookalike) are the machine's, and a row with no agent attempt is left out too, so seconds / readable is an upper
    bound of the agent time per readable function."""
    t = {"rows": 0, "readable": 0, "seconds": 0.0}
    for r in rows:
        atts = [a for a in r.get("attempts") or [] if not a.get("kind") and a.get("model") not in NOT_AGENT]
        if not atts or any(a.get("duration_s") is None for a in atts):
            continue
        t["rows"] += 1
        t["readable"] += r.get("status") == "readable"
        t["seconds"] += sum(a["duration_s"] for a in atts)
    return t


def final_audit(state):
    files = sorted(state.glob("final_audit-*.txt"))
    if not files:
        return None
    m = re.search(r"^VERDICT (\w+)", files[-1].read_text(encoding="utf-8", errors="replace"), re.M)
    return {"file": files[-1].name, "verdict": m.group(1) if m else "none"}


def workspace_metrics(ws, with_scope=True):
    """One workspace's figures from state/ (ledger, readable.json, prepare_timing.jsonl, final audit, divergence)."""
    ws = pathlib.Path(ws)
    st = ws / "state"
    proj = load_json(ws / "project.json", {}) or {}
    ledger = (load_json(st / "ledger.json", {}) or {}).get("functions") or {}
    scope, scope_src = scope_size(ws, ledger) if with_scope else (set(ledger), "ledger rows")
    status = {}
    for r in ledger.values():
        status[r.get("status")] = status.get(r.get("status"), 0) + 1
    matched = [a for a, r in ledger.items() if r.get("status") == "matched"]
    rd = load_json(st / "readable.json")
    readable = None
    if isinstance(rd, dict):
        rows = [x for x in rd.values() if isinstance(x, dict)]
        readable = {"rows": len(rows), "readable": sum(1 for x in rows if x.get("status") == "readable"),
                    "verified": sum(1 for x in rows if x.get("status") == "readable" and x.get("verified")),
                    "agent": _attempt_totals(rows), "time": readable_time(rows)}
    prep = []
    pt = st / "prepare_timing.jsonl"
    if pt.is_file():
        for line in pt.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                prep.append(float(json.loads(line)["phases"]["total"]))
            except (ValueError, KeyError, TypeError):
                continue
    div = load_json(st / "divergence.json")
    root = game_root(ws)
    return {"path": str(ws), "name": proj.get("name") or ws.name, "target": proj.get("target"),
            "binary": proj.get("binary_sha256") or proj.get("binary") or str(ws),
            "_scope": scope, "_matched": set(matched), "game_root": str(root),
            "port": (root / "port" / "game.json").is_file(),
            "scenarios": (root / "port" / "divergence_scenarios.json").is_file(),
            "scope": len(scope), "scope_source": scope_src, "rows": len(ledger), "status": status,
            "matched": len(matched), "matched_in_scope": sum(1 for a in matched if a in scope),
            "agent": _attempt_totals(ledger.values()), "readable": readable, "prepare_s": prep,
            "final_audit": final_audit(st), "busy": busy_totals(st), "oplog": oplog_records(st),
            "port_oplog": oplog_records(root / "port" / "state"),
            "divergence": ({"file": str(st / "divergence.json"), "when": div.get("when"),
                            "counts": div.get("counts") or {}} if isinstance(div, dict) else None)}


def median(xs):
    return statistics.median(xs) if xs else None


STATUS_CLOSED = re.compile(r"\bfixed\b|unavoidable|none found", re.I)


def parse_waste_audit(text):
    """{"findings": [(no, finding, status)], "open": [...]} from the audit's findings table: the first with both a #
    and a Status column (a savings table before it has a Status column too)."""
    rows, cols = [], None
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else None
        if cells is None:
            if cols is not None and rows:
                break
            continue
        if cols is None:
            if "Status" in cells and "#" in cells:
                cols = {c: i for i, c in enumerate(cells)}
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        get = lambda k, i=None: cells[cols[k]] if k in cols and cols[k] < len(cells) else ""  # noqa: E731
        rows.append((get("#"), get("Finding"), get("Status").replace("*", "").strip()))
    return {"findings": rows, "open": [r for r in rows if not STATUS_CLOSED.search(r[2])]}


def audit_date(text):
    """The date (YYYY-MM-DD) in an audit's title line, "" without one: what orders audits, as a file name does not
    (WASTE_AUDIT_2.md, the second audit, sorts before WASTE_AUDIT_2026-10-22.md)."""
    first = next((ln for ln in text.splitlines() if ln.startswith("# ")), "")
    m = re.search(r"\d{4}-\d{2}-\d{2}", first)
    return m.group(0) if m else ""


def waste_audits(src, extra=None):
    """[(name, path or ref:path, where, text)] newest first (by the date in the title, then the name): docs/
    WASTE_AUDIT_*.md in the checkout, the pending refs and every other worktree of the repository (an audit still being
    written there is reported as not landed)."""
    out = []
    if extra:
        p = pathlib.Path(extra)
        out.append((p.name, str(p), "given", p.read_text(encoding="utf-8", errors="replace")))
    for p in sorted((src.tool / "docs").glob("WASTE_AUDIT_*.md")):
        out.append((p.name, str(p), "checkout", p.read_text(encoding="utf-8", errors="replace")))
    for ref in src.pending:
        for n in src.tree(ref):
            if fnmatch.fnmatchcase(n, "docs/WASTE_AUDIT_*.md"):
                text, where = src.read(n)
                if text is not None and where != "checkout":
                    out.append((pathlib.Path(n).name, f"{ref}:{n}", f"branch {ref}", text))
    here = src.tool.resolve()
    for wt, branch in src.worktrees():
        if wt.resolve() == here:
            continue
        for p in sorted((wt / "docs").glob("WASTE_AUDIT_*.md")):
            out.append((p.name, str(p), f"worktree {branch} (not landed)",
                        p.read_text(encoding="utf-8", errors="replace")))
    seen, uniq = set(), []
    for item in sorted(out, key=lambda x: (audit_date(x[3]), x[0], x[2] == "checkout"), reverse=True):
        if (item[0], item[3]) not in seen:
            seen.add((item[0], item[3]))
            uniq.append(item)
    return uniq


def mutation_rates(text):
    """{key: {"functions", "mutants", "killed", "rate"}} from tools/mutation_audit/baseline.json, counted as
    tools/mutation_audit.py compare() counts them (mutants dropped by lint or build are left out)."""
    out = {}
    for key, fs in (json.loads(text) or {}).items():
        k = {"functions": 0, "mutants": 0, "killed": 0}
        for rec in fs.values():
            if rec.get("error"):
                continue
            k["functions"] += 1
            for o in rec.get("outcomes") or "":
                if o in "LB":
                    continue
                k["mutants"] += 1
                k["killed"] += o == "K"
        k["rate"] = round(100.0 * k["killed"] / k["mutants"], 1) if k["mutants"] else None
        out[key] = k
    return out


def baseline_functions(spec, projects_dir):
    """(function count, source) of the baseline project, from the file and pattern spec.json names; (None, why)."""
    fs = spec["baseline"]["functions_source"]
    p = pathlib.Path(projects_dir) / fs["path"]
    if not p.is_file():
        return None, f"{fs['path']} not found"
    m = re.search(fs["regex"], p.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return None, f"{fs['path']}: pattern not found"
    return int(m.group(1).replace(",", "")), fs["path"]


def head_of(path):
    """A local git repository's short head, or None."""
    try:
        r = subprocess.run(["git", "-C", str(path), "log", "--oneline", "-1"], capture_output=True,
                           timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.decode("utf-8", "replace").split(" ")[0] if r.returncode == 0 and r.stdout else None
