"""Leg A of the instruction vote: our translator's C on every instruction test case (tools/x360_vote.py runs it).

Each distinct word sequence is translated once, in the exit-state form with Facts.exit_seed (exit_form.py: every r/f/v
register the code uses is read at entry from the harness, CR/XER/CTR are 0, the final value of every register is
recorded at the return), as `void vote_<n>(void)`. All functions go into a few translation units (identical static
helpers kept once) and ONE cl run builds them with the judge runtime (tc_rt.c, tc_ppc.c, tc_exit.c: the exit record)
and tools/vote_rt.c (the seeds and the dispatch); one run of it executes every case.

Memory cases: the corpus puts its memory at 0x10000000..; the judge runtime has guest RAM only in its own regions, so a
case with MEMORY_IN/OUT has its memory and every input register holding an address in [0x10000000, 0x10100000) moved by
+0x30000000 into the judge heap, and its output registers in the heap moved back ("rebased" in the result).

Per case: "agree", "differ", "nan_payload" (only NaN-vs-NaN bit differences: f registers given in hex, or float lanes of
a vector float op), "crash", or "unsupported" with the reason. Compare rules: r and v by bits, cr by its 32 bits, f by
bits when the corpus gives hex and as a double when it gives a decimal (vote_corpus.f_equal).

Nearest existing: targets/xbox360_ppc/x360_check.run_exit - builds and runs one function's exit-state form on a harness's
vectors; this needs any register as an input, thousands of functions in one build and no harness.
"""
import pathlib
import re
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
X360 = HERE.parent
ROOT = X360.parents[1]
for p in (HERE, X360, X360 / "translator", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import vote_corpus as VC  # noqa: E402
import vote_encode_check as K  # noqa: E402
from xenontranslate import vmx  # noqa: E402
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, TranslateError, translate_function  # noqa: E402

BASE = K.BASE
RUNTIME = X360 / "runtime"
RT_SOURCES = ("tc_rt.c", "tc_ppc.c", "tc_exit.c")
MEM_LO, MEM_HI, MEM_SHIFT = 0x10000000, 0x10100000, 0x30000000
TRAPS = {"tw", "twi", "td", "tdi"}
PREAMBLE = ('#include "tc_rt.h"\n' + vmx.TYPEDEF + "\n"
            "u64 tcv_in_r(u32 n);\nf64 tcv_in_f(u32 n);\nxv tcv_in_v(u32 n);\n")


def is_float_vop(op):
    return (("fp" in op and not op.startswith("vcmp")) or op.startswith("vrfi")
            or op in ("vcfsx", "vcfux", "vcsxwfp128", "vcuxwfp128"))


def mem_case(c):
    return bool(c["mem_in"] or c["mem_out"])


def prepare(cases):
    """Assign every case a function or an unsupported reason. Returns (plan, groups): plan[i] = (fn index or None,
    words or None, reason or None); groups = [words tuple] in function order."""
    plan, index, groups = [], {}, []
    for c in cases:
        if c["errors"]:
            plan.append((None, None, "corpus value unparsed: " + c["errors"][0]))
            continue
        words, why = K.assemble(c)
        if words is None:
            plan.append((None, None, why))
            continue
        ops = [K.D.decode(w).op for w in words]
        if any(o in TRAPS for o in ops):
            plan.append((None, words, "trap: the judge form does not model conditional traps (the case only checks "
                                      "registers unchanged)"))
            continue
        if ops[-1] != "bclr" and "bclr" not in ops:
            plan.append((None, words, "no blr"))
            continue
        if mem_case(c) and not any(k[0] == "r" and MEM_LO <= v < MEM_HI for k, v in c["reg_in"].items()):
            plan.append((None, words, "address made in code: the judge runtime has no RAM at 0x10000000 and only "
                                      "input registers are moved into its heap"))
            continue
        t = tuple(words)
        if t not in index:
            index[t] = len(groups)
            groups.append(t)
        plan.append((index[t], words, None))
    return plan, groups


def translate_group(n, words):
    """(C text or None, refusal reason or None)."""
    code = b"".join(w.to_bytes(4, "big") for w in words) + bytes(0x40)
    name = f"vote_{n}"
    sig = Signature(name, f"void {name}(void)", [], "void")
    try:
        src = translate_function(XexImage(code, BASE), BASE, sig, Facts(exit_state=True, exit_seed=True),
                                 [(BASE, BASE + 4 * len(words))])[0]
    except TranslateError as e:
        return None, f"translator: {e}"
    except Exception as e:  # noqa: BLE001  (a translator crash is a refusal with its reason, never a pass)
        return None, f"translator crashed: {type(e).__name__}: {e}"
    return src, None


def chunks(src):
    """Top-level definitions of a translation: [(first line, text)] without the include and the xv typedef."""
    out, cur = [], []
    for ln in src.splitlines():
        if ln and (ln[0].isalpha() or ln[0] == "#") and cur:
            out.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        out.append(cur)
    res = []
    for c in out:
        text = "\n".join(c).strip()
        if not text or text.startswith("#include") or text == vmx.TYPEDEF:
            continue
        res.append((c[0], text))
    return res


def units(sources, per_unit=250):
    """Translation units: [text]. Identical static helpers once per unit; a same-named helper with other text opens a
    new unit."""
    tus, seen, body, count = [], {}, [], 0
    for src in sources:
        cs = chunks(src)
        clash = any(k.startswith("static") and k in seen and seen[k] != t for k, t in cs)
        if body and (clash or count >= per_unit):
            tus.append(PREAMBLE + "\n" + "\n\n".join(body) + "\n")
            seen, body, count = {}, [], 0
        for k, t in cs:
            if k.startswith("static"):
                if k in seen:
                    continue
                seen[k] = t
            body.append(t)
        count += 1
    if body:
        tus.append(PREAMBLE + "\n" + "\n\n".join(body) + "\n")
    return tus


def named(src):
    """The registers a function's exit record names: {"r": set, "f": set, "v": set}."""
    return {k: {int(x) for x in re.findall(rf"tcx_{k}\((\d+)u,", src)} for k in "rfv"}


def inputs(c):
    """{reg: value} with the memory rebase applied."""
    out = dict(c["reg_in"])
    if mem_case(c):
        for k, v in out.items():
            if k.startswith("r") and MEM_LO <= v < MEM_HI:
                out[k] = v + MEM_SHIFT
    return out


def case_lines(i, c, fn):
    ln = [f"C {i} {fn}"]
    for k, v in inputs(c).items():
        if k[0] == "r":
            ln.append(f"R {k[1:]} {v:x}")
        elif k[0] == "f":
            ln.append(f"F {k[1:]} {v:x}")
        elif k[0] == "v":
            ln.append(f"V {k[1:]} " + " ".join(f"{w:x}" for w in v))
    sh = MEM_SHIFT if mem_case(c) else 0
    for a, data in c["mem_in"]:
        ln.append(f"M {a + sh:x} {data.hex()}")
    for k in dict.fromkeys(n for n, _, _ in c["reg_out"]):
        ln.append(f"O {k}")
    for a, data in c["mem_out"][:16]:
        ln.append(f"Q {a + sh:x} {len(data)}")
    ln.append("E")
    return ln


def parse_out(text):
    """{case id: {"crash": code or None, "o": {name: value}, "q": {addr: bytes}[, "unsupported": reason]}}."""
    res, cur = {}, None
    for ln in text.splitlines():
        p = ln.split()
        if not p:
            continue
        if p[0] == "C":
            cur = res.setdefault(int(p[1]), {"crash": None, "o": {}, "q": {}})
        elif p[0] == "CRASH":
            cur["crash"] = p[1]
        elif p[0] == "U":  # leg B (TcVote.java): the oracle refused or could not finish the case
            cur["unsupported"] = "oracle: " + ln[2:].strip()
        elif p[0] == "O":
            cur["o"][p[1]] = tuple(int(x, 16) for x in p[2:]) if p[1][0] == "v" else int(p[2], 16)
        elif p[0] == "Q":
            cur["q"][int(p[1], 16)] = bytes.fromhex(p[2]) if len(p) > 2 else b""
    return res


def ours(c, got, names):
    """{reg: value} the C leaves in every register the corpus checks. A register the C does not name keeps its input
    (the C cannot change it); one it names is the exit record's value (None if missing)."""
    out = {}
    for k, _, _ in c["reg_out"]:
        if k == "cr":
            out[k] = got["o"].get("cr", 0)
            continue
        if int(k[1:]) not in names[k[0]]:
            out[k] = c["reg_in"].get(k, (0, 0, 0, 0) if k[0] == "v" else 0)
            continue
        v = got["o"].get(k)
        if k[0] == "r" and v is not None and mem_case(c) and MEM_LO + MEM_SHIFT <= v < MEM_HI + MEM_SHIFT:
            v -= MEM_SHIFT
        out[k] = v
    return out


def compare(c, mine, ops, got_mem=None):
    """(status, [(reg, corpus, ours)] differing)."""
    diffs, nanonly = [], True
    fvop = any(is_float_vop(o) for o in ops)
    for k, exp, kind in c["reg_out"]:
        act = mine[k]
        if act is None:
            eq, nan = False, False
        elif k[0] == "f":
            eq, nan = VC.f_equal(exp, act, kind)
        elif k[0] == "v":
            eq = tuple(exp) == tuple(act)
            nan = not eq and fvop and all(e == a or (lane_nan(e) and lane_nan(a)) for e, a in zip(exp, act))
        else:
            eq, nan = exp == act, False
        if not eq:
            diffs.append((k, exp, act))
            nanonly &= nan
    sh = MEM_SHIFT if mem_case(c) else 0
    for a, data in c["mem_out"][:16]:
        act = (got_mem or {}).get(a + sh)
        if act != data:
            diffs.append((f"mem {a:08x}", data.hex(), act.hex() if act is not None else None))
            nanonly = False
    if not diffs:
        return "agree", []
    return ("nan_payload" if nanonly else "differ"), diffs


def lane_nan(w):
    return (w & 0x7F800000) == 0x7F800000 and (w & 0x007FFFFF) != 0


def fmt(v):
    if isinstance(v, tuple):
        return [f"{w:08x}" for w in v]
    if isinstance(v, int):
        return f"{v:016x}"
    return v


def fbits_str(b):
    return f"{b:016x} ({struct.unpack('>d', struct.pack('>Q', b))[0]!r})"


def build(d, sources, cfg):
    """Write the units, the table and the harness into d and build vote.exe with ONE cl run. (ok, message)."""
    import json
    import subprocess
    fma = json.loads((X360 / "target.json").read_text(encoding="utf-8")).get("fma", "two_roundings")
    fused = {"fused": 1, "two_roundings": 0}[fma]
    tus = units(sources)
    files = []
    for k, t in enumerate(tus):
        (d / f"vote_tu{k}.c").write_text(t, encoding="utf-8")
        files.append(f"vote_tu{k}.c")
    n = len(sources)
    table = ['#include "tc_rt.h"'] + [f"void vote_{i}(void);" for i in range(n)]
    table += ["void (*const vote_fns[])(void) = {" + ", ".join(f"vote_{i}" for i in range(n)) + "};",
              f"const int vote_nfns = {n};", ""]
    (d / "vote_table.c").write_text("\n".join(table), encoding="utf-8")
    vc = cfg.get("vcvars")
    if not vc or not pathlib.Path(vc).is_file():
        return False, "no vcvars in config.local.json"
    rsp = [f"/I{RUNTIME}", *files, "vote_table.c", str(HERE / "vote_rt.c"), *(str(RUNTIME / s) for s in RT_SOURCES)]
    (d / "cl.rsp").write_text("\n".join(f'"{x}"' for x in rsp) + "\n", encoding="utf-8")
    vc = str(vc).replace("/", "\\")
    cmd = (f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 /fp:strict /W4 /WX /TC /wd4389 /DTC_FMA_FUSED={fused} '
           f'/Fevote.exe @cl.rsp"')
    try:
        r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, errors="replace", timeout=900)
        rc, out, reason = r.returncode, r.stdout, None
    except subprocess.TimeoutExpired:
        rc, out, reason = -1, "", "timed out after 900 s"
    if rc != 0 or reason:
        errs = [ln for ln in out.splitlines() if "error" in ln or "warning" in ln][:12]
        return False, f"build failed ({reason or rc}): " + " | ".join(errs or out.splitlines()[-8:])
    return True, f"{len(tus)} units, {n} functions"


def run(d, cases, plan):
    """Run vote.exe once on every planned case: {case index: parsed output}."""
    import subprocess
    (d / "image.bin").write_bytes(bytes(0x10000))
    (d / "code.txt").write_text(f"{BASE:08x} {BASE:08x}" + "\n", encoding="utf-8")
    lines = []
    for i, (c, (fn, _, why)) in enumerate(zip(cases, plan)):
        if why is None and fn is not None:
            lines += case_lines(i, c, fn)
    (d / "cases.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = subprocess.run([str(d / "vote.exe"), "image.bin", "code.txt", "cases.txt", "out.txt"], cwd=d,
                       capture_output=True, text=True, errors="replace", timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"vote.exe: {r.returncode}: {(r.stdout + r.stderr)[-500:]}")
    return parse_out((d / "out.txt").read_text(encoding="utf-8"))
