"""Install the patched SH-4 language "SuperH4:LE:32:tcfix" into the Ghidra install.

The stock SuperH4 SLEIGH files are left untouched; this adds new files beside them:
  SuperH4_tc.sinc, SuperH4_tc_le.slaspec, SuperH4_tc_le.sla, SuperH4_tc.ldefs

Fixes (all five make Ghidra's emulator and decompiler disagree with real SH-4 code):
  1. fschg toggled FPSCR but not the FPSCR_SZ flag every fmov actually tests, so
     paired 64-bit moves after fschg were emulated as 32-bit moves.
  2. frchg toggled an FPSCR_FR flag that nothing reads. Real hardware swaps the
     FR and XF banks; matrix loads (frchg + fmov + frchg) never reached XMTRX.
     The patched frchg swaps fr0-fr15 with xf0-xf15.
  3. fpchg toggled FPSCR but not the FPSCR_PR flag.
  4. bsr never set PR (jsr and bsrf do), so under emulation every bsr callee
     "returned" to the caller's caller.
  5. In the little-endian register file the FR pairs are stored swapped
     (offset order fr1 fr0 fr3 fr2 ...), but fipr/ftrv index vector elements by
     raw offset, so FV0 was read as (fr1, fr0, fr3, fr2) and fipr's result landed
     in FR[n+2]. Real SH-4 defines FVn = FRn..FRn+3 logically. The patched
     fipr/ftrv map element i to offset (i ^ 1) * 4.

Usage: python install_lang.py <ghidra_home>
"""
import pathlib
import re
import subprocess
import sys

FRCHG_OLD = """	if (!( $(FPSCR_PR) == 0 )) goto <skip>;
	$(FPSCR_FR) = !$(FPSCR_FR);
	FPSCR = FPSCR ^ 0x00200000;
	<skip>"""

FSCHG_OLD = """	if (!( $(FPSCR_PR) == 0 )) goto <skip>;
	FPSCR = FPSCR ^ 0x00100000;
	<skip>"""

FSCHG_NEW = """	if (!( $(FPSCR_PR) == 0 )) goto <skip>;
	FPSCR = FPSCR ^ 0x00100000;
	$(FPSCR_SZ) = !$(FPSCR_SZ);
	<skip>"""

FPCHG_OLD = """	FPSCR = FPSCR ^ 0x00080000;
}"""

FPCHG_NEW = """	FPSCR = FPSCR ^ 0x00080000;
	$(FPSCR_PR) = !$(FPSCR_PR);
}"""

BSR_OLD = """:bsr I_1tbranch             is OP_0=0xb & I_1tbranch {

	delayslot(1);"""

BSR_NEW = """:bsr I_1tbranch             is OP_0=0xb & I_1tbranch {

	PR = inst_next;
	delayslot(1);"""

LDEFS = """<?xml version="1.0" encoding="UTF-8"?>

<language_definitions>
  <language processor="SuperH4"
            endian="little"
            size="32"
            variant="tcfix"
            version="1.01"
            slafile="SuperH4_tc_le.sla"
            processorspec="SuperH4.pspec"
            manualindexfile="../manuals/superh4.idx"
            id="SuperH4:LE:32:tcfix">
    <description>SuperH-4 little endian, FPSCR bank/size fixes (DecompPipeline)</description>
    <compiler name="default" spec="SuperH4_le.cspec" id="default"/>
  </language>
</language_definitions>
"""

SLASPEC = """# SuperH4 little endian with FPSCR fixes. See SuperH4_tc.sinc and install_lang.py.

@define ENDIAN "little"

@include "SuperH4_tc.sinc"
"""


def frchg_new():
    swaps = []
    for i in range(16):
        swaps.append(f"\tlocal bank{i}:4 = fr{i};\n\tfr{i} = xf{i};\n\txf{i} = bank{i};")
    return ("""	if (!( $(FPSCR_PR) == 0 )) goto <skip>;
	$(FPSCR_FR) = !$(FPSCR_FR);
	FPSCR = FPSCR ^ 0x00200000;
""" + "\n".join(swaps) + "\n\t<skip>")


def fv_logical(text, mnemonic):
    """Remap raw FV offsets to logical element order inside one instruction's semantics."""
    start = text.index("\n:" + mnemonic + " ")
    end = text.index("\n}\n", start)
    body = text[start:end]
    swap = {"+  0)": "+  4)", "+  4)": "+  0)", "+  8)": "+ 12)", "+ 12)": "+  8)"}
    fixed = re.sub(r"(&:4 FV[NM]_2t )(\+ +\d+\))", lambda m: m.group(1) + swap[m.group(2)], body)
    if fixed == body:
        sys.exit(f"install_lang: no FV offsets found in {mnemonic}")
    return text[:start] + fixed + text[end:]


def replace_once(text, old, new, what):
    if text.count(old) != 1:
        sys.exit(f"install_lang: expected exactly one {what} block, found {text.count(old)}")
    return text.replace(old, new)


def main():
    home = pathlib.Path(sys.argv[1])
    lang = home / "Ghidra/Processors/SuperH4/data/languages"
    sinc = (lang / "SuperH4.sinc").read_text(encoding="utf-8")
    sinc = replace_once(sinc, FRCHG_OLD, frchg_new(), "frchg")
    sinc = replace_once(sinc, FSCHG_OLD, FSCHG_NEW, "fschg")
    sinc = replace_once(sinc, FPCHG_OLD, FPCHG_NEW, "fpchg")
    sinc = replace_once(sinc, BSR_OLD, BSR_NEW, "bsr")
    sinc = fv_logical(sinc, "fipr")
    sinc = fv_logical(sinc, "ftrv")
    (lang / "SuperH4_tc.sinc").write_text(sinc, encoding="utf-8")
    (lang / "SuperH4_tc_le.slaspec").write_text(SLASPEC, encoding="utf-8")
    (lang / "SuperH4_tc.ldefs").write_text(LDEFS, encoding="utf-8")
    sleigh = home / "support" / ("sleigh.bat" if sys.platform == "win32" else "sleigh")
    r = subprocess.run([str(sleigh), str(lang / "SuperH4_tc_le.slaspec")], capture_output=True, text=True, timeout=600)
    print(r.stdout[-2000:], r.stderr[-2000:])
    if r.returncode != 0 or not (lang / "SuperH4_tc_le.sla").exists():
        sys.exit("install_lang: sleigh compile failed")
    print("installed SuperH4:LE:32:tcfix")


if __name__ == "__main__":
    main()
