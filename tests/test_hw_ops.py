"""Listed hardware ops (project.json "hw_ops", core/hw_ops.py, TcHw.java): a function whose only parts C cannot express
are single hardware ops, each listed with evidence, is judged on everything else. Spawn Disp_ApplyMode (8c04f4c0)
reads the system bus revision register 0xa05f689c once and sets GBR; Irq_Init (8c0583b0) sets SR.BL.
1. the listing: each entry needs ops of known kinds and its evidence, a read needs a register with a value in
   target.json "hw_reads"; script arg 11 ("hw:" items, no ",;= ") carries the registers' values, "gbr" and "bl";
2. the classifier: a function stops being excluded only when its listing covers every hardware op the catalog counts
   (hw_refs no more than the listed reads, sys_ops exactly the listed GBR moves); unlisted, nothing changes;
3. the stamp: an unlisted function's is as without the list, a listed one's differs and carries the values;
4. the judge: GB (GBR at exit) is parsed and compared like IM;
5. the wiring: TcHw is locked, every script stays at 400 lines or fewer, TcPrepare reads arg 11 and refuses an unlisted
   op (TcHw.check), TcFold serves listed registers, TcSr allows BL only when listed, both runners parse Y and Z, the
   C headers declare the intrinsics, and the port's register table equals target.json "hw_reads";
7. writes and cache ops (the second step): "write R" is logged and compared as a P4 store (TcExit.p4, tc_p4.c's log,
   compare.named_words), "ocbi" / "ocbp" / "ocbwb" are no-ops on both sides; the classifier counts writes with the
   reads against hw_refs and cache ops with the GBR moves against sys_ops;
6. both runtimes' tc_hw_read32 and GB report (compiled from their source).
The oracle itself needs Ghidra: the prepares of 8c04f4c0 and 8c0583b0 with the listing judge it.
Synthetic data. Run: python -W error tests/test_hw_ops.py.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import agent, compare, hw_ops, oracle_stamp, orch  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
READS = {"a05f689c": ["0000000b", "SB_SBREV"]}
APPLY = {"8c04f4c0": {"ops": ["read a05f689c", "ldc gbr"], "evidence": "reads SB_SBREV at 8c04f514, sets GBR at 8c04f536"}}
IRQ = {"8c0583b0": {"ops": ["ldc sr.bl"], "evidence": "ldc r3,sr at 8c0583c8 sets SR.BL"}}
CFG = {"hw_reads": READS, "hw_ops": dict(APPLY, **IRQ)}


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def row(addr, **kw):
    f = {k: 0 for k in ("insns", "cyclo", "fpu_special", "fp_mode", "jump_tables", "indirect_calls", "callees",
                        "globals", "hw_refs", "sys_ops")}
    f.update(addr=addr, insns=20, cyclo=2)
    f.update(kw)
    return f


class Listing(unittest.TestCase):
    def test_listed(self):
        self.assertEqual(hw_ops.listed(CFG), {"8c04f4c0": ["read a05f689c", "ldc gbr"], "8c0583b0": ["ldc sr.bl"]})
        self.assertEqual(hw_ops.listed({}), {})
        bad = ({"8c04f4c0": {"ops": ["ldc gbr"]}},  # no evidence
               {"8c04f4c0": {"ops": ["ldc gbr"], "evidence": " "}},
               {"8c04f4c0": {"ops": [], "evidence": "x"}},
               {"8c04f4c0": {"ops": ["ldc vbr"], "evidence": "x"}},  # not a listable kind
               {"8c04f4c0": {"ops": ["read a05f6800"], "evidence": "x"}},  # no value in hw_reads
               {"8c04f4c0": {"ops": ["read 5f689c"], "evidence": "x"}},
               ["8c04f4c0"])
        for b in bad:
            with self.assertRaises(ValueError, msg=repr(b)):
                hw_ops.listed({"hw_reads": READS, "hw_ops": b})

    def test_arg(self):
        self.assertEqual(hw_ops.arg(CFG, "8C04F4C0"), "hw:ra05f689c.0000000b_gbr")
        self.assertEqual(hw_ops.arg(CFG, "8c0583b0"), "hw:bl")
        self.assertEqual(hw_ops.arg(CFG, "8c010080"), "-")
        self.assertEqual(hw_ops.arg({}, "8c04f4c0"), "-")
        self.assertFalse(re.search(r"[,;= ]", hw_ops.arg(CFG, "8c04f4c0")))  # analyzeHeadless.bat splits at them


class Classifier(unittest.TestCase):
    def test_covered(self):
        listed = hw_ops.listed(CFG)
        apply_ = row("8c04f4c0", hw_refs=1, sys_ops=1, sr_ops=4, sq_refs=2)  # Spawn's catalog row
        self.assertEqual(orch.tier(apply_), "excluded")  # unlisted: as before
        self.assertEqual(orch.tier(apply_, listed={}), "excluded")
        self.assertEqual(orch.tier(apply_, listed=listed), "hard")  # SR and store queues: modelled, hard
        self.assertEqual(orch.tier(dict(apply_, sys_ops=2), listed=listed), "excluded")  # an op not listed
        self.assertEqual(orch.tier(dict(apply_, hw_refs=2), listed=listed), "excluded")  # a register not listed
        self.assertEqual(orch.tier(row("8c0583b0", sr_ops=3), listed=listed), "hard")
        self.assertEqual(orch.tier(row("8c0583b0", sys_ops=3), listed=listed), "excluded")  # catalog before sr_ops
        self.assertEqual(orch.tier(row("8c05a500", hw_refs=1), listed=listed), "excluded")  # another function
        self.assertEqual(orch.tier(row("8c010000"), listed=listed), "easy")

    def test_scoped(self):
        cat = {"functions": [row("8c04f4c0", hw_refs=1, sys_ops=1), row("8c05a500", hw_refs=1)]}
        sc = {"8c04f4c0", "8c05a500"}
        self.assertEqual({a: f["tier"] for a, f in orch.scoped_functions(cat, sc).items()},
                         {"8c04f4c0": "excluded", "8c05a500": "excluded"})
        self.assertEqual({a: f["tier"] for a, f in orch.scoped_functions(cat, sc, CFG).items()},
                         {"8c04f4c0": "easy", "8c05a500": "excluded"})


class Stamp(unittest.TestCase):
    def test_stamp(self):
        def s(cfg, addr):
            return oracle_stamp.stamp_of(dict(cfg, binary_sha256="ab"), addr, {}, "-", None, 48, 48)
        self.assertEqual(s(CFG, "8c010080"), s({}, "8c010080"))  # unlisted: as without the list
        self.assertNotEqual(s(CFG, "8c04f4c0"), s({}, "8c04f4c0"))  # listed: re-prepared
        other = dict(CFG, hw_reads={"a05f689c": ["0000000c", "another revision"]})
        self.assertNotEqual(s(CFG, "8c04f4c0"), s(other, "8c04f4c0"))  # the register's value is an input


class Judge(unittest.TestCase):
    def test_gbr(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "r.txt"
            p.write_text("V 0 dev\nRET 0 0\nGB 8c1a0000\nE\nV 1 dev\nRET 0 0\nE\n", encoding="utf-8")
            res = compare.parse_results(str(p))
        self.assertEqual(res[0]["gbr"], 0x8C1A0000)
        self.assertNotIn("gbr", res[1])
        self.assertEqual(compare.compare_state({"gbr": 0x8C1A0000}, {"gbr": 0x8C1A0000}), [])
        self.assertEqual(compare.compare_state({}, {"gbr": 5}), [])  # no GB in the original: nothing compared
        wrong = compare.compare_state({"gbr": 0x8C1A0000}, {"gbr": 0})
        self.assertEqual(len(wrong), 1)
        self.assertIn("GBR at exit: expected 0x8c1a0000, got 0x00000000", wrong[0])
        self.assertIn("got nothing", compare.compare_state({"gbr": 1}, {})[0])

    def test_helpers(self):
        self.assertEqual(agent.SPECIAL_HELPER["ldc gbr"], "sh4_ldc_gbr")
        self.assertEqual(agent.SPECIAL_HELPER["stc gbr"], "sh4_stc_gbr")
        src = text(ROOT / "core" / "agent.py")
        self.assertIn("tc_hw_read32(0x{r}u)", src)


class Wiring(unittest.TestCase):
    def test_scripts(self):
        tj = json.loads(text(SH4 / "target.json"))
        self.assertIn("TcHw.java", tj["locked_scripts"])
        for p in SCRIPTS.glob("*.java"):
            self.assertLessEqual(len(text(p).splitlines()), 400, p.name)
        prep = text(SCRIPTS / "TcPrepare.java")
        self.assertIn('TcHw.on = TcHw.parse(a.length > 11 ? a[11] : "-");', prep)
        self.assertIn("TcHw.on.check(this, body)", prep)
        self.assertIn("//  11 \"hw:...\"", prep)
        self.assertIn("TcHw.header()", prep)
        self.assertIn("TcHw.on.read(offset, size)", text(SCRIPTS / "TcFold.java"))
        self.assertIn("TcHw.on != null && TcHw.on.bl ? 2 : 3", text(SCRIPTS / "TcSr.java"))
        self.assertIn("res.gbr = o.reg(emu, \"GBR\")", text(SCRIPTS / "TcExit.java"))
        self.assertIn("TcHw.exit(r)", text(SCRIPTS / "TcVectors.java"))
        self.assertIn("tc_args.append(hw_ops.arg(cfg, addr))", text(ROOT / "core" / "ghidra.py"))

    def test_runtimes(self):
        for h in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h"):
            for decl in ("u32 tc_hw_read32(u32 reg);", "u32 sh4_stc_gbr(void);", "void sh4_ldc_gbr(u32 value);"):
                self.assertIn(decl, text(h), h.name)
        for r in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            src = text(r)
            self.assertIn("line[0] == 'Y'", src, r.name)
            self.assertIn("line[0] == 'Z'", src, r.name)
            self.assertIn("tcrt_report_gbr(out);", src, r.name)

    def test_port_table(self):  # the port reads the values the oracle used
        port = text(ROOT / "port" / "runtime" / "sh4_rt.c")
        table = dict(re.findall(r"\{0x([0-9a-f]{8})u, 0x([0-9a-f]{8})u\}", port.split("k_hw[] = {")[1].split("};")[0]))
        self.assertEqual(table, {k: v[0] for k, v in json.loads(text(SH4 / "target.json"))["hw_reads"].items()})
        self.assertIn("port_extra_state(&g_gbr, sizeof g_gbr);", port)


MAIN = """#include <stdint.h>
#include <stdio.h>
typedef unsigned int u32;
static void fault(const char *k, u32 a) { printf("FAULT %%s %%08x\\n", k, a); }
%s
int main(void)
{
    tcrt_add_hw(0xa05f689cu, 0xbu);
    printf("%%08x\\n", tc_hw_read32(0xa05f689cu));
    tcrt_report_gbr(stdout);
    tcrt_set_gbr_state();
    sh4_ldc_gbr(0x8c1a0000u);
    tcrt_report_gbr(stdout);
    printf("%%08x\\n", sh4_stc_gbr());
    if (%d) printf("%%08x\\n", tc_hw_read32(0xa05f6800u));
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class Runtimes(unittest.TestCase):
    def test_hw(self):
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            for p, unlisted in ((SH4 / "runtime" / "tc_rt.c", 1), (SH4 / "runtime_rd" / "rd_sh4.c", 0)):
                src = text(p)
                block = re.search(r"#define TCRT_MAX_HW 16\n.*?\nvoid tcrt_report_gbr\(FILE \*out\)\n\{.*?\n\}\n", src,
                                  re.S).group(0)
                (d / "p.c").write_text(MAIN % (block, unlisted), encoding="utf-8")
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX p.c"', cwd=d,
                                   capture_output=True, text=True, errors="replace", timeout=300)
                self.assertEqual(r.returncode, 0, p.name + r.stdout[-2000:])
                out = subprocess.run([str(d / "p.exe")], capture_output=True, text=True, timeout=60)
                want = ["0000000b", "GB 8c1a0000", "8c1a0000"] + (["FAULT read a05f6800", "00000000"] if unlisted else [])
                self.assertEqual(out.stdout.split("\n")[:len(want)], want, p.name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


ACK = {"8c058620": {"ops": ["write a05f6900"], "evidence": "mov.l r4,@r1 at 8c058626 acknowledges SB_ISTNRM bits"}}
FLUSH = {"8c024d6e": {"ops": ["ocbwb", "ocbi"], "evidence": "ocbwb @r4 at 8c024d7c, ocbi @r4 at 8c024d86: a DMA buffer"}}
CFG2 = {"hw_reads": READS, "hw_ops": dict(ACK, **FLUSH, **APPLY)}


class Writes(unittest.TestCase):
    def test_listing(self):
        self.assertEqual(hw_ops.listed(CFG2)["8c058620"], ["write a05f6900"])
        self.assertEqual(hw_ops.arg(CFG2, "8c058620"), "hw:wa05f6900")  # a write needs no value in hw_reads
        self.assertEqual(hw_ops.arg(CFG2, "8c024d6e"), "hw:cache")
        both = dict(CFG2, hw_ops={"8c05a540": {"ops": ["read a05f689c", "write a05f689c"], "evidence": "x"}})
        self.assertEqual(hw_ops.arg(both, "8c05a540"), "hw:ra05f689c.0000000b_wa05f689c")
        self.assertEqual(hw_ops.arg(CFG2, "8c04f4c0"), "hw:ra05f689c.0000000b_gbr")  # step one's listings unchanged
        for bad in (["write 5f6900"], ["ocbi @r4"], ["pref"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                hw_ops.listed({"hw_ops": {"8c058620": {"ops": bad, "evidence": "x"}}})

    def test_classifier(self):
        listed = hw_ops.listed(CFG2)
        self.assertEqual(orch.tier(row("8c058620", hw_refs=1), listed=listed), "easy")
        self.assertEqual(orch.tier(row("8c058620", hw_refs=2), listed=listed), "excluded")  # a second register
        self.assertEqual(orch.tier(row("8c024d6e", sys_ops=2), listed=listed), "easy")
        self.assertEqual(orch.tier(row("8c024d6e", sys_ops=3), listed=listed), "excluded")  # an op not listed
        self.assertEqual(orch.tier(row("8c024d6e", sys_ops=2, hw_refs=1), listed=listed), "excluded")
        self.assertEqual(orch.tier(row("8c024d6e", sys_ops=2), listed={}), "excluded")

    def test_judged(self):
        meta = {"globals": ["8c100000"], "hw_ops": {"reads": {}, "writes": ["a05f6900"], "gbr": False}}
        self.assertEqual(compare.named_words(meta), {0x8C100000, 0xA05F6900})
        self.assertEqual(compare.named_words({"globals": ["8c100000"]}), {0x8C100000})  # unlisted: as before
        self.assertTrue(compare.judged(0xA05F6902, named=compare.named_words(meta)))
        self.assertFalse(compare.judged(0xA05F6900, named=compare.named_words({})))

    def test_wiring(self):
        hw = text(SCRIPTS / "TcHw.java")
        self.assertIn('"CacheBlockInvalidate", "CacheBlockPurge", "CacheBlockWriteBack"', hw)
        self.assertIn("|| TcHw.written(lo, hi);", text(SCRIPTS / "TcExit.java"))
        self.assertIn("if (TcHw.written(ea, ea + 1)) continue;", text(SCRIPTS / "TcLoads.java"))
        self.assertIn("TcHw.callbacks(emu);", text(SCRIPTS / "TcOracle.java"))
        self.assertIn("sh4Special.add(TcHw.on.special(in));", text(SCRIPTS / "TcPrepare.java"))
        for m in ("ocbi", "ocbp", "ocbwb"):
            self.assertEqual(agent.SPECIAL_HELPER[m], "sh4_" + m)
        for h in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h", ROOT / "port" / "runtime" / "sh4_rt.c"):
            for name in ("tc_hw_write32(u32 reg, u32 value)", "tc_hw_write16(u32 reg, u16 value)", "sh4_ocbi(u32 addr)",
                         "sh4_ocbp(u32 addr)", "sh4_ocbwb(u32 addr)"):
                self.assertIn(name, text(h), h.name)


P4MAIN = """#include <stdio.h>
#include "tc_rt_internal.h"
int main(void)
{
    tcrt_p4_reset();
    tc_hw_write32(0xa05f6900u, 0x00000008u);
    tc_hw_write16(0xffa0002cu, 0x1234u);
    sh4_ocbi(0x8c100000u);
    sh4_ocbwb(0x8c100000u);
    tcrt_p4_report(stdout);
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class WriteRuntime(unittest.TestCase):
    def test_log(self):  # tc_p4.c, shared by both runtimes (core/readable_check.RUNTIME_SOURCES)
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            (d / "p.c").write_text(P4MAIN, encoding="utf-8")
            src, inc = SH4 / "runtime" / "tc_p4.c", SH4 / "runtime"
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX /I"{inc}" p.c "{src}""', cwd=d,
                               capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            out = subprocess.run([str(d / "p.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.split("\n")[:2], ["W a05f6900 08000000", "W ffa0002c 3412"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
