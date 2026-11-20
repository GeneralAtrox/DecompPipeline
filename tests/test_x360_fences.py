"""sync, lwsync and eieio in the port form are real barriers: each is a statement between the accesses it separates,
and a load after one is not answered from a load before it. The judge form (one thread) keeps them as no-ops, so the
judged C does not change. lwarx / stwcx. in the port form are a reservation and a conditional store whose result is
CR0.EQ; tc_rt.h's compare and swap is compiled and run on two threads when cl is available.
usage: python tests/test_x360_fences.py
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
X360 = ROOT / "targets" / "xbox360_ppc"
sys.path.insert(0, str(X360 / "translator"))
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, translate_function  # noqa: E402

BASE = 0x82010000
BARRIERS = [0x80830000,   # lwz r4,0(r3)
            0x7C0004AC,   # sync
            0x80A30000,   # lwz r5,0(r3)
            0x7C2004AC,   # lwsync
            0x90830004,   # stw r4,4(r3)
            0x7C0006AC,   # eieio
            0x90A30008,   # stw r5,8(r3)
            0x4C00012C,   # isync
            0x7C632A14,   # add r3,r3,r5
            0x4E800020]   # blr
INCREMENT = [0x7C801828,  # lwarx r4,0,r3
             0x38840001,  # addi r4,r4,1
             0x7C80192D,  # stwcx. r4,0,r3
             0x4082FFF4,  # bne -12
             0x7C832378,  # mr r3,r4
             0x4E800020]  # blr
MAIN = r'''
#include <windows.h>
#include <stdio.h>
u8 *tc_base;
__declspec(thread) u64 tc_resv_v;
__declspec(thread) u32 tc_resv_s;
u64 fn_82010000(u64 p);
static DWORD WINAPI work(void *p) { (void)p; for (int i = 0; i < 2000000; i++) fn_82010000(0x100); return 0; }
int main(void)
{
    tc_base = VirtualAlloc(0, 0x10000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    HANDLE h[2] = { CreateThread(0, 0, work, 0, 0, 0), CreateThread(0, 0, work, 0, 0, 0) };
    WaitForMultipleObjects(2, h, TRUE, 60000);
    tc_sync(0, 0); tc_lwsync(0, 0); tc_eieio(0, 0);
    tc_resv_s = 0; tc_resv_v = rd32(0x100); tc_stwcx(0x100, 7);          /* no reservation: must fail */
    u32 stale = tc_resv_ok(0x100), after = rd32(0x100);
    tc_lwarx(0x100, 0); tc_stwcx(0x100, after + 1); u32 once = tc_resv_ok(0x100);
    tc_stwcx(0x100, 9); u32 twice = tc_resv_ok(0x100);                   /* the reservation was used up */
    printf("%u %u %u %u %u\n", after, stale, once, twice, rd32(0x100));
    return 0;
}
'''


def translate(words, port):
    code = b"".join(w.to_bytes(4, "big") for w in words)
    sig = Signature("fn_82010000", "u64 fn_82010000(u64 p)", [("p", "u64", "r3", -1)], "u64")
    return translate_function(XexImage(code, BASE), BASE, sig, Facts(port=port), [(BASE, BASE + len(code))])[0]


class Barriers(unittest.TestCase):
    def test_port_form_keeps_each_barrier_between_its_accesses(self):
        src = translate(BARRIERS, True)
        seq = re.findall(r"tc_sync|tc_lwsync|tc_eieio|rd32|wr32", src)
        self.assertEqual(seq, ["rd32", "tc_sync", "rd32", "tc_lwsync", "wr32", "tc_eieio", "wr32"], src)

    def test_judge_form_is_unchanged(self):
        src = translate(BARRIERS, False)
        self.assertNotRegex(src, r"tc_sync|tc_lwsync|tc_eieio")


class Reservation(unittest.TestCase):
    def test_port_form_is_a_conditional_store(self):
        src = translate(INCREMENT, True)
        seq = re.findall(r"tc_lwarx|tc_resv_ok|tc_resv\b|tc_stwcx", src)
        self.assertEqual(seq[:4], ["tc_lwarx", "tc_resv", "tc_stwcx", "tc_resv_ok"], src)

    def test_judge_form_always_succeeds(self):
        src = translate(INCREMENT, False)
        self.assertNotIn("tc_stwcx", src)
        self.assertIn("wr32", src)

    @unittest.skipUnless(shutil.which("cl"), "needs cl (run from a Visual Studio prompt)")
    def test_two_threads_lose_no_increment(self):
        """The translated increment on two host threads, 2,000,000 times each, loses none; a conditional store with no
        reservation fails and stores nothing; one reservation serves one conditional store; the fences compile."""
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "fn.c").write_text(translate(INCREMENT, True), encoding="utf-8")
            (d / "main.c").write_text('#include "tc_rt.h"\n' + MAIN, encoding="utf-8")
            inc = [f"/I{X360 / 'runtime'}", f"/I{X360 / 'port' / 'runtime'}"]
            r = subprocess.run(["cl", "/nologo", "/O1", "/W3", "/DTC_PORT=1", *inc, "main.c", "fn.c", "/Fet.exe"], cwd=d,
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=120).stdout.split()
            self.assertEqual(out, ["4000000", "0", "1", "0", "4000001"])


if __name__ == "__main__":
    unittest.main()
