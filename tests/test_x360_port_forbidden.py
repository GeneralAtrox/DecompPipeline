"""The Xbox 360 port kit must not grow a hardware model (docs/RUNTIME_TARGET.md, "New console kits start with no hardware
model"). This fails when the kit's source (targets/xbox360_ppc/port: C, headers, Python, JSON) names one of the forbidden
parts: the Xenos register file, a PM4 packet parser, the eDRAM, a shader microcode interpreter, a Xenon hardware-thread
scheduler, the XMA hardware, or kernel object emulation where a host object does the job. Prose (.md) is not scanned: the
design documents may name them to say they are not built.

If the game forces one of these, stop and report the evidence to the parent session; do not add it as bring-up and do not
change this list to make it pass.
usage: python tests/test_x360_port_forbidden.py
"""
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = ROOT / "targets" / "xbox360_ppc" / "port"
SUFFIXES = {".c", ".h", ".py", ".json", ".ps1"}

FORBIDDEN = {
    "Xenos register file": r"xenos[_ ]?reg|gpu[_ ]?reg(ister)?s?\b|register[_ ]?file|reg[_ ]?file",
    "PM4 parser": r"\bpm4\b|pm4_|type3[_ ]?packet|cp[_ ]?packet|ringbuffer[_ ]?parse|parse[_ ]?ring",
    "eDRAM model": r"edram",
    "shader microcode interpreter": r"ucode[_ ]?interp|microcode[_ ]?interp|interp\w*[_ ]?(ucode|microcode)|shader[_ ]?interp|run[_ ]?ucode",
    "Xenon hardware-thread scheduler": r"hw[_ ]?thread|hardware[_ ]?thread|xenon[_ ]?sched|ppc[_ ]?thread[_ ]?sched|thread[_ ]?affinity[_ ]?map",
    "XMA hardware model": r"xma[_ ]?context|xma[_ ]?hw|xma[_ ]?ring|xma[_ ]?decoder[_ ]?state|xma[_ ]?packet",
    "kernel object emulation": r"kernel[_ ]?object|\bk[_ ]?object\b|object[_ ]?table|handle[_ ]?table|\bxobject\b|ob[_ ]?type[_ ]?",
}
RULES = {name: re.compile(rx, re.I) for name, rx in FORBIDDEN.items()}


def scan(root):
    """[(file, line number, part)] for every forbidden name in the kit source under root."""
    hits = []
    for p in sorted(pathlib.Path(root).rglob("*")):
        if p.suffix.lower() not in SUFFIXES or not p.is_file():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for name, rx in RULES.items():
                if rx.search(line):
                    hits.append((str(p.relative_to(root)), n, name))
    return hits


class Forbidden(unittest.TestCase):
    def test_kit_has_none(self):
        self.assertTrue(KIT.is_dir(), KIT)
        self.assertEqual(scan(KIT), [])

    def test_scanner_finds_each_part(self):
        """Each part is named in a planted file, so the check above can fail."""
        samples = {"Xenos register file": "u32 xenos_regs[0x5000];", "PM4 parser": "void pm4_parse(void);",
                   "eDRAM model": "u8 edram[10 << 20];", "shader microcode interpreter": "void ucode_interp(void);",
                   "Xenon hardware-thread scheduler": "void hw_thread_run(void);", "XMA hardware model": "struct XmaContext;",
                   "kernel object emulation": "static KernelObject table[256];"}
        for part, code in samples.items():
            with tempfile.TemporaryDirectory() as d:
                (pathlib.Path(d) / "x.c").write_text(code + "\n")
                self.assertEqual([h[2] for h in scan(d)], [part], part)


if __name__ == "__main__":
    unittest.main()
