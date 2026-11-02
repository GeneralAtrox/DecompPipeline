"""Where a golden or expected file came from (AGENTS.md "Test rules"): a provenance.json beside the files, read by regress.py
and tools/golden_survey.py.

Nearest existing: oracle_stamp (a harness's stamp) - it names the inputs of one oracle run; goldens have no oracle run.

A record is {"source": ..., "evidence": ...}. The sources:
- original-executable, emulator-capture, disc-decode: original evidence, so a check against the file can support a fidelity
  claim. The record holds the file's sha256 and the evidence's, so a port frame copied over the file no longer matches.
- native-baseline: the port's or the tool's own output. It catches a change and supports no fidelity claim.
Refreshing from the port's output can only make a native baseline: over original evidence it is refused.
"""
import hashlib
import json
import pathlib
import re

FILE = "provenance.json"
NATIVE = "native-baseline"
ORIGINAL = ("original-executable", "emulator-capture", "disc-decode")
# what a run of the port writes: tools/regress --dump-dir, build/<dir>_regress, its log
PORT_OUTPUT = re.compile(r"(^|[\\/])(regress|\w*_regress)[\\/]|(^|[\\/])port\.log$|(^|[\\/])frames[\\/]frame_\d+\.png$", re.I)


class Refused(Exception):
    pass


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class Ledger:
    """The records of the files under root, keyed by their relative paths with forward slashes."""

    def __init__(self, root, file=None):
        self.root, self.file = pathlib.Path(root), pathlib.Path(file) if file else pathlib.Path(root) / FILE
        self.rows = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {}

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def problem(self, key):
        """Why this file's record can't be trusted, or None. A native baseline's bytes aren't pinned: it claims nothing."""
        r = self.rows.get(key)
        if r is None:
            return f"no provenance record in {self.file.name} (tools/golden_survey.py)"
        if r.get("source") == NATIVE:
            return None if r.get("evidence") else "a native baseline with no evidence text"
        if r.get("source") not in ORIGINAL:
            return f"unknown source {r.get('source')!r}"
        if not r.get("evidence") or not r.get("evidence_sha256"):
            return f"source {r['source']} with no evidence"
        p = self.root / key
        if p.exists() and sha256(p) != r.get("sha256"):
            return f"labelled {r['source']}, but the file is not the one recorded: refreshed from something else?"
        return None

    def fidelity(self, key):
        """May a check against this file support a fidelity claim?"""
        return self.problem(key) is None and self.rows[key]["source"] in ORIGINAL

    def native(self, key, evidence):
        """Label a file refreshed from the port's or a tool's own output. Refused over original evidence."""
        r = self.rows.get(key)
        if r and r.get("source") in ORIGINAL:
            raise Refused(f"{key} is {r['source']} evidence ({r.get('evidence')}): the port's output may not replace it")
        self.rows[key] = {"source": NATIVE, "evidence": evidence}

    def original(self, key, source, evidence_file, note=""):
        """Label a file as original evidence, on the file it was taken from."""
        if source not in ORIGINAL:
            raise Refused(f"{source!r} is not original evidence: {', '.join(ORIGINAL)}")
        e = pathlib.Path(evidence_file)
        if PORT_OUTPUT.search(str(e)):
            raise Refused(f"{e} is the port's own output")
        if not e.is_file():
            raise Refused(f"no evidence file {e}")
        self.rows[key] = {"source": source, "evidence": f"{e.name}{': ' + note if note else ''}",
                          "evidence_sha256": sha256(e), "sha256": sha256(self.root / key)}
