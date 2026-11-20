r"""Cross-check: the translator's xv_vpkd3d (vmx_unpack.py) against the oracle's TcVmxFloat.vpkd3d, on build box A. Bounded.
Run from a DP checkout with DECOMP_PROJECT set: py -3.12 docs\proofs\2026-11-03\x360-vpkd3d\pk_check.py; PK_MUTATE=tie, trunc or lane
plants a known error. Writes only into its own folder; K and JDK are build box A's paths.

Nearest existing: tools/vabi_state.py - it checks vector register dataflow, not a helper's values against the oracle's executor.
"""
import os, pathlib, subprocess, sys
K = pathlib.Path(r"<projects>\DecompPipeline\.work\th-kit")
W = pathlib.Path(__file__).resolve().parent
JDK = pathlib.Path(r"<tools>\dp\jdk-21.0.12.1+1\bin")
sys.path[:0] = [str(K), str(K / "targets/xbox360_ppc/translator")]
from core import check as core_check, paths  # noqa: E402
from xenontranslate import vmx  # noqa: E402
MUTS = {"tie": ("rest == 0x1000u && (h & 1u)", "rest == 0x1000u"), "trunc": ("return (s32)t;", "return (s32)(t + 0.5);"),
        "lane": ("d.w[3] = hi;", "d.w[2] = hi;")}
def MUT(text):  # PK_MUTATE=name plants a known error, to prove the comparison rejects it
    m = MUTS.get(os.environ.get("PK_MUTATE", ""))
    if m:
        assert m[0] in text
        text = text.replace(m[0], m[1])
    return text
gs = K / "targets/xbox360_ppc/ghidra_scripts"
srcs = [str(gs / f"{n}.java") for n in ("TcVmxOp", "TcVmxTables", "TcVmxDecode", "TcVmxLanes", "TcVmxFloat")] + [str(W / "PkCheck.java")]
(W / "cls").mkdir(exist_ok=True)
subprocess.run([str(JDK / "javac.exe"), "-nowarn", "-d", str(W / "cls")] + srcs, check=True, timeout=300)
exp = subprocess.run([str(JDK / "java.exe"), "-cp", str(W / "cls"), "PkCheck"], check=True, timeout=300, capture_output=True, text=True).stdout
(W / "expected.txt").write_text(exp)
c = ["#include <stdio.h>", "typedef unsigned int u32; typedef int s32; typedef float f32; typedef double f64;", vmx.TYPEDEF,
     MUT(chr(10).join(vmx.helper_text(["xv_vpkd3d"]))), r"""
int main(int argc, char **argv)
{
    FILE *f = fopen(argv[1], "r");
    int t, m, z, i, n = 0, bad = 0;
    xv d, b, e, r;
    while (fscanf(f, "%d %d %d %x %x %x %x %x %x %x %x %x %x %x %x", &t, &m, &z, &d.w[0], &d.w[1], &d.w[2], &d.w[3],
                  &b.w[0], &b.w[1], &b.w[2], &b.w[3], &e.w[0], &e.w[1], &e.w[2], &e.w[3]) == 15) {
        r = xv_vpkd3d(d, b, t, m, z);
        n++;
        for (i = 0; i < 4; i++) if (r.w[i] != e.w[i]) break;
        if (i < 4 && bad++ < 20) printf("DIFF t%d m%d z%d b %08x %08x %08x %08x: oracle %08x %08x %08x %08x, C %08x %08x %08x %08x\n", t, m, z,
            b.w[0], b.w[1], b.w[2], b.w[3], e.w[0], e.w[1], e.w[2], e.w[3], r.w[0], r.w[1], r.w[2], r.w[3]);
    }
    printf("%d cases, %d differ\n", n, bad);
    return bad != 0;
}
"""]
(W / "pk.c").write_text("\n".join(c))
env = core_check.msvc_env(paths.config())
import shutil
E = env
cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))  # as gen_xbox.cfg_env
subprocess.run([cl, "/nologo", "/O1", "/fp:strict", "/W4", "pk.c"], cwd=W, env=E, check=True, timeout=300, capture_output=True)
res = subprocess.run([str(W / "pk.exe"), str(W / "expected.txt")], timeout=120, capture_output=True, text=True)
print(res.stdout[-4000:])
sys.exit(res.returncode)
