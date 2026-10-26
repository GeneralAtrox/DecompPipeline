"""Overlays (port/runtime/overlay.c, port/tools/gen_overlays.py): units grouped by shared RAM, identity windows
picked where each unit differs from its rivals inside their ranges, and, built with a driver that maps guest RAM where
the port does, a synthetic two-overlay fixture: calls into the shared range run the overlay whose bytes are in RAM,
nothing when neither is, and a trampoline passes arguments and the return value through. Synthetic data; no Ghidra,
no game workspace. The built tests need Visual Studio's 32-bit tools (test_lockstep.py HAVE_VC).
Run: python -W error tests/test_port_kit.py (it imports these)
"""
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
import gen_overlays as go  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

LO, HI = 0x8c600000, 0x8c601000  # the range A and B share
SOLO = (0x8c700000, 0x8c700400)  # C's, alone in its group


def image(seed, lo=LO, hi=HI):
    """Synthetic code bytes for [lo, hi) and a reader over them (b"" outside)."""
    data = random.Random(seed).randbytes(hi - lo)
    return data, lambda a, n: data[a - lo:a - lo + n] if lo <= a and a + n <= hi else b""


class OverlayGroups(unittest.TestCase):
    def test_overlapping_units_share_a_group(self):
        units = [{"ranges": [(LO, HI)]}, {"ranges": [SOLO]}, {"ranges": [(LO + 0x800, LO + 0x900)]},
                 {"ranges": [(0x8c800000, 0x8c800100), (LO + 0xf00, HI + 0x100)]}]
        self.assertEqual(go.groups(units), [0, 1, 0, 0])


class OverlayWindows(unittest.TestCase):
    blocks = [(LO + 0x40 * i, LO + 0x40 * i + 0x30) for i in range(60)]

    def test_rivals_told_apart(self):
        _, a = image(1)
        _, b = image(2)
        w = go.pick_windows("a", a, [(LO, HI)], self.blocks, [("b", [(LO, HI)], b)])
        self.assertGreaterEqual(len(w), go.NEED)
        self.assertTrue(all(b(x, go.WINDOW) != y and a(x, go.WINDOW) == y for x, y in w))

    def test_only_where_the_rival_writes(self):
        """A rival loaded over part of the range leaves the rest of the unit in RAM: windows there tell nothing."""
        _, a = image(1)
        _, b = image(2, LO, LO + 0x200)
        w = go.pick_windows("a", a, [(LO, HI)], self.blocks, [("b", [(LO, LO + 0x200)], b)])
        self.assertGreaterEqual(sum(1 for x, _ in w if x + go.WINDOW <= LO + 0x200), go.NEED)

    def test_indistinguishable_rival_stops(self):
        data, a = image(1)
        same = bytearray(data)
        same[0x40:0x50] = bytes(16)  # one block differs: one window only
        b = lambda x, n: bytes(same[x - LO:x - LO + n])  # noqa: E731
        with self.assertRaisesRegex(go.OverlayError, "only 1 code window"):
            go.pick_windows("a", a, [(LO, HI)], self.blocks, [("b", [(LO, HI)], b)])

    def test_rival_over_data_only(self):
        """A rival loaded only over the unit's data (its code lies beyond) has nothing to tell apart."""
        _, a = image(1)
        _, b = image(2, LO, LO + 0x400)
        code = [x for x in self.blocks if x[0] >= LO + 0x800]
        w = go.pick_windows("a", a, [(LO, HI)], code, [("b", [(LO, LO + 0x400)], b)])
        self.assertEqual(len(w), go.MIN_WINDOWS)

    def test_solo_unit_gets_windows(self):
        _, c = image(3, *SOLO)
        w = go.pick_windows("c", c, [SOLO], [(SOLO[0], SOLO[1])], [])
        self.assertEqual(len(w), go.MIN_WINDOWS)


DRIVER_C = r"""/* Overlay driver: A or B (or neither) in guest RAM, then calls into the shared range. */
#include <stdio.h>
#include <string.h>
#include "port.h"
#include "overlay.h"

void *guest_alloc(void);
extern const unsigned char img_a[], img_b[], img_c[], img_e[];
int T__f(int x); /* a trampoline to 0x8c600100 */

static void call(const char *what)
{
    const PortDispatch *d = overlay_find(0x8c600100u);
    int r = d ? ((int (*)(int))d->fn)(5) : -1, t = T__f(5);
    printf("%s find %s %d tramp %d\n", what, d ? d->name : "-", r, t);
}

int main(void)
{
    if (!guest_alloc()) return 3;
    call("empty");
    memcpy((void *)(uintptr_t)0x8c600000u, img_a, 0x1000);
    call("a");
    memcpy((void *)(uintptr_t)0x8c600000u, img_b, 0x1000);
    call("b");
    memcpy((void *)(uintptr_t)0x8c600000u, img_a, 0x1000);
    call("a");
    printf("other %s\n", overlay_find(0x8c500000u) ? "found" : "-");
    memcpy((void *)(uintptr_t)0x8c610000u, img_c, 0x1000); /* e over c's data: both run */
    memcpy((void *)(uintptr_t)0x8c610000u, img_e, 0x400);
    int e = ((int (*)(int))overlay_find(0x8c610100u)->fn)(5), c = ((int (*)(int))overlay_find(0x8c610900u)->fn)(5);
    printf("e %d c %d\n", e, c);
    memcpy((void *)(uintptr_t)0x8c610000u, img_c, 0x1000);
    printf("c only %s\n", overlay_find(0x8c610100u)->name);
    overlay_report();
    return 0;
}
"""
HOST_C = r"""/* The overlays' host functions and the runtime's other parts for the driver. */
#include <stdarg.h>
#include <stdio.h>
#include <windows.h>

int A__f(int x) { return x + 1; }
int B__f(int x) { return x * 2; }
int C__g(int x) { return x + 100; }
int E__h(int x) { return x + 200; }
void *guest_alloc(void)
{
    return VirtualAlloc((void *)(UINT_PTR)0x8c000000u, 0x01000000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
}
unsigned guest_fold(unsigned a) { return (a >> 24) == 0x0cu ? a | 0x80000000u : a; }
int guest_is_ram(unsigned a) { return guest_fold(a) - 0x8c000000u < 0x01000000u; }
const void *port_dispatch_find(unsigned a) { (void)a; return NULL; }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("log: ", stdout);
    vprintf(fmt, ap);
    va_end(ap);
}
"""
FAULT_C = r"""/* Calls through guest pointers (dispatch.c's fault path): into overlay RAM and into main. */
#include <stdio.h>
#include <string.h>
#include "port.h"
#include "overlay.h"

void *guest_alloc(void);
extern const unsigned char img_a[], img_b[];

int main(void)
{
    int (*f)(int) = (int (*)(int))(uintptr_t)0x8c600100u, (*m)(int) = (int (*)(int))(uintptr_t)0x8c020000u;
    if (!guest_alloc() || !dispatch_install()) return 4;
    memcpy((void *)(uintptr_t)0x8c600000u, img_a, 0x1000);
    printf("a %d\n", f(5));
    memcpy((void *)(uintptr_t)0x8c600000u, img_b, 0x1000);
    printf("b %d main %d\n", f(5), m(5));
    memset((void *)(uintptr_t)0x8c600000u, 0, 0x1000);
    printf("none %d\n", f(5));
    printf("none again %d\n", f(5));
    overlay_report();
    return 0;
}
"""
HOST_FAULT_C = r"""/* The runtime's other parts for the fault driver: main's dispatch table has one function. */
#include <stdarg.h>
#include <stdio.h>
#include <windows.h>
#include "port_dispatch.h"

int A__f(int x) { return x + 1; }
int B__f(int x) { return x * 2; }
static int M__f(int x) { return x - 1; }
static const PortDispatch main_rows[] = {{0x8c020000u, (PortFn)M__f, "Mf"}};
static const int main_n = 1;
const PortDispatchChunk port_dispatch_chunks[] = {{main_rows, &main_n}};
const int port_dispatch_nchunks = 1;
void *guest_alloc(void)
{
    return VirtualAlloc((void *)(UINT_PTR)0x8c000000u, 0x01000000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
}
u32 guest_fold(u32 a) { return (a >> 24) == 0x0cu ? a | 0x80000000u : a; }
int guest_is_ram(u32 a) { return guest_fold(a) - 0x8c000000u < 0x01000000u; }
int rom_read(EXCEPTION_POINTERS *ep) { (void)ep; return 0; }
void port_stub_report(void) {}
void port_log_flush(void) {}
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("log: ", stdout);
    vprintf(fmt, ap);
    va_end(ap);
}
void port_exit(const char *why, int code)
{
    printf("EXIT: %s\n", why);
    fflush(stdout);
    ExitProcess((UINT)code);
}
"""


def bytes_c(name, data):
    rows = [", ".join(f"0x{x:02x}" for x in data[i:i + 24]) for i in range(0, len(data), 24)]
    return f"const unsigned char {name}[] = {{\n" + ",\n".join(rows) + "\n};\n"


def build(out, files, exe, flags=""):
    rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
    (out / "port_config.h").write_text(game_config.port_config_h(
        {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
         "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
    cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 {flags} /I"{out}" /I"{KIT / "runtime"}" /I"{rd}" '
          + " ".join(f'"{p}"' for p in files) + f' /Fe"{out / exe}" /link /LARGEADDRESSAWARE /DYNAMICBASE:NO '
          '/NXCOMPAT /BASE:0x5a000000')
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=out, capture_output=True, text=True,
                       errors="replace", timeout=300)
    return r.stdout[-3000:] + r.stderr[-1000:] if r.returncode or "warning" in r.stdout.lower() else ""


@unittest.skipUnless(HAVE_VC, NO_VC)
class OverlayBuilt(unittest.TestCase):
    def setUp(self):
        self.out = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def run_exe(self, exe):
        r = subprocess.run([str(self.out / exe)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        return r.stdout.splitlines()

    def test_two_overlays_share_a_range(self):
        """The unit in RAM answers: A's bytes run A__f, B's run B__f (a switch each time, logged), neither runs
        nothing; the trampoline reaches the same function with its argument and result."""
        da, a = image(1)
        db, b = image(2)
        blocks = [(LO + 0x40 * i, LO + 0x40 * i + 0x30) for i in range(60)]
        c0, e1 = LO + 0x10000, LO + 0x10400  # e is loaded over c's data, below c's code
        dc, c = image(5, c0, c0 + 0x1000)
        de, e = image(6, c0, e1)
        units = [{"id": "a", "ranges": [(LO, HI)]}, {"id": "b", "ranges": [(LO, HI)]},
                 {"id": "c", "ranges": [(c0, c0 + 0x1000)]}, {"id": "e", "ranges": [(c0, e1)]}]
        wins = [go.pick_windows("a", a, [(LO, HI)], blocks, [("b", [(LO, HI)], b)]),
                go.pick_windows("b", b, [(LO, HI)], blocks, [("a", [(LO, HI)], a)]),
                go.pick_windows("c", c, [(c0, c0 + 0x1000)], [(c0 + 0x800, c0 + 0x1000)], [("e", [(c0, e1)], e)]),
                go.pick_windows("e", e, [(c0, e1)], [(c0, e1)], [("c", [(c0, c0 + 0x1000)], c)])]
        rows = [[(LO + 0x100, "A__f", "Fa")], [(LO + 0x100, "B__f", "Fb")], [(c0 + 0x900, "C__g", "Gc")],
                [(c0 + 0x100, "E__h", "He")]]
        files = {"table.c": go.table_c(units, go.groups(units), wins, rows),
                 "tramp.c": go.tramp_c("T", {"f": "8c600100"}), "driver.c": DRIVER_C, "host.c": HOST_C,
                 "images.c": "".join(bytes_c(f"img_{k}", v) for k, v in zip("abce", (da, db, dc, de)))}
        for n, t in files.items():
            (self.out / n).write_text(t, encoding="utf-8")
        log = build(self.out, [self.out / n for n in files] + [KIT / "runtime" / "overlay.c"], "ovl.exe")
        self.assertEqual(log, "")
        got = self.run_exe("ovl.exe")
        miss = "no loaded unit has a function there; returns 0 (logged once per group)"
        self.assertEqual(got, [
            f"log: OVERLAY MISS: call to 8c600100 in a's range: {miss}", "empty find (no loaded overlay) 0 tramp 0",
            "log: overlay: a active (call to 8c600100)", "a find Fa 6 tramp 6",
            "log: overlay: b active (call to 8c600100)", "b find Fb 10 tramp 10",
            "log: overlay: a active (call to 8c600100)", "a find Fa 6 tramp 6", "other -",
            "log: overlay: e active (call to 8c610100)", "log: overlay: c active (call to 8c610900)", "e 205 c 105",
            f"log: OVERLAY MISS: call to 8c610100 in c's range: {miss}", "c only (no loaded overlay)",
            "log: overlays: 4 units, 5 switches, 3 calls with no loaded function"])

    def fault_exe(self, flags=""):
        """FAULT_C over units a and b, with dispatch.c's handler; returns its exit code and output."""
        da, a = image(1)
        db, b = image(2)
        blocks = [(LO + 0x40 * i, LO + 0x40 * i + 0x30) for i in range(60)]
        units = [{"id": "a", "ranges": [(LO, HI)]}, {"id": "b", "ranges": [(LO, HI)]}]
        wins = [go.pick_windows("a", a, [(LO, HI)], blocks, [("b", [(LO, HI)], b)]),
                go.pick_windows("b", b, [(LO, HI)], blocks, [("a", [(LO, HI)], a)])]
        files = {"table.c": go.table_c(units, go.groups(units), wins, [[(LO + 0x100, "A__f", "Fa")],
                                                                        [(LO + 0x100, "B__f", "Fb")]]),
                 "driver.c": FAULT_C, "host.c": HOST_FAULT_C,
                 "images.c": bytes_c("img_a", da) + bytes_c("img_b", db)}
        for n, t in files.items():
            (self.out / n).write_text(t, encoding="utf-8")
        runtime = [KIT / "runtime" / n for n in ("overlay.c", "dispatch.c")]
        self.assertEqual(build(self.out, [self.out / n for n in files] + runtime, "fault.exe", flags), "")
        r = subprocess.run([str(self.out / "fault.exe")], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.splitlines()

    def test_call_through_a_guest_pointer(self):
        """A call through a guest pointer faults into dispatch.c, which runs the unit loaded now (a, then b), main's
        function outside overlay RAM, and with nothing loaded returns 0 after one loud log, never main's table."""
        code, got = self.fault_exe()
        self.assertEqual(code, 0, got)
        self.assertEqual(got, [
            "log: overlay: a active (call to 8c600100)", "a 6", "log: overlay: b active (call to 8c600100)",
            "b 10 main 4", "log: OVERLAY MISS: call to 8c600100 in a's range: no loaded unit has a function there; "
            "returns 0 (logged once per group)", "none 0", "none again 0",
            "log: overlays: 2 units, 2 switches, 2 calls with no loaded function"])

    def test_checked_build_stops_at_a_miss(self):
        code, got = self.fault_exe("/DPORT_CHECKED=1")
        self.assertEqual(code, 3, got)
        self.assertEqual(got[-1], "EXIT: overlay: a call into overlay RAM with no loaded function")
        self.assertNotIn("none 0", got)

    def test_no_overlays_links_the_empty_table(self):
        """A game with no overlay table still links (the /alternatename default) and finds none."""
        (self.out / "main.c").write_text('#include <stdio.h>\n#include "overlay.h"\nint main(void)\n{\n'
                                         '    printf("%d %d\\n", port_noverlays, overlay_find(0x8c600100u) != 0);\n'
                                         "    return 0;\n}\n", encoding="utf-8")
        (self.out / "host.c").write_text(HOST_C, encoding="utf-8")
        log = build(self.out, [self.out / "main.c", self.out / "host.c", KIT / "runtime" / "overlay.c"], "none.exe")
        self.assertEqual(log, "")
        self.assertEqual(self.run_exe("none.exe"), ["0 0"])


if __name__ == "__main__":
    unittest.main()
