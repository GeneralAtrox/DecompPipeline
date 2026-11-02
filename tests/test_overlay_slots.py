"""Slot overlays (port/runtime/overlay.c slot lookup, port/tools/overlay_slots.py): one file loaded at several places of
a region, found by identity windows at each slot base, its translator C rebased by the load delta. Synthetic data; the
built test needs Visual Studio's 32-bit tools. Run: python -W error tests/test_port_kit.py (it imports these)
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "port" / "tools"), str(TOOL / "tests")]  # alone, as a first-collected file
import gen_overlays as go  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402
from test_overlays import HOST_C, KIT, SOLO, bytes_c, build, image  # noqa: E402

import subprocess  # noqa: E402


REGION = (0x8c700000, 0x8c800000)
CANON_A, CANON_B = 0x8c740000, 0x8c748000  # slot units written at these bases, loaded at any 0x8000 boundary
SLOT = {"lo": REGION[0], "hi": REGION[1], "align": 0x8000}


def slot_units():
    """Slot units a and b (each one 0x1000 of code at its canonical base), their reads, blocks and windows."""
    units = [{"id": "a", "ranges": [(CANON_A, CANON_A + 0x1000)], "slot": dict(SLOT, canon=CANON_A)},
             {"id": "b", "ranges": [(CANON_B, CANON_B + 0x1000)], "slot": dict(SLOT, canon=CANON_B)}]
    data = [image(11, CANON_A, CANON_A + 0x1000), image(12, CANON_B, CANON_B + 0x1000)]
    return units, [d for d, _ in data], [r for _, r in data]


class OverlaySlots(unittest.TestCase):
    def test_slot_units_group_by_region(self):
        units, _, _ = slot_units()
        self.assertEqual(go.groups(units + [{"ranges": [(0x8c900000, 0x8c900100)]}, {"ranges": [(0x8c780000, 0x8c780010)]}]),
                         [0, 0, 1, 0])

    def test_rivals_compare_at_the_same_offset(self):
        units, data, reads = slot_units()
        riv = go.slots.rivals_of(units, go.groups(units), reads, 0)
        self.assertEqual([(i, r) for i, r, _ in riv], [("b", [(CANON_A, CANON_A + 0x1000)])])
        self.assertEqual(riv[0][2](CANON_A + 0x20, 16), data[1][0x20:0x30])  # b's bytes at a's offset 0x20
        blocks = [(CANON_A + 0x40 * i, CANON_A + 0x40 * i + 0x30) for i in range(16)]
        w = go.pick_windows("a", reads[0], units[0]["ranges"], blocks, riv)
        self.assertTrue(w and all(reads[0](x, 16) == y and riv[0][2](x, 16) != y for x, y in w))

    def test_slot_unit_is_no_rival_of_a_fixed_one(self):
        units, _, reads = slot_units()
        units.append({"id": "f", "ranges": [(CANON_A, CANON_A + 0x1000)], "slot": None})
        self.assertEqual([i for i, _, _ in go.slots.rivals_of(units, go.groups(units), reads + [reads[0]], 0)], ["b"])

    def test_slot_config(self):
        import json
        with tempfile.TemporaryDirectory() as t:
            game = pathlib.Path(t)
            (game / "ws").mkdir()
            (game / "ws" / "project.json").write_text("{}", encoding="utf-8")

            def load(**slot):
                s = {"base": "8c740000", "region": ["8c700000", "8c800000"], "align": "8000"} | slot
                (game / "overlays.json").write_text(json.dumps({"overlays": [
                    {"id": "x", "decomp": "ws", "ranges": [["8c740000", "8c741000"]], "slot": s}]}), encoding="utf-8")
                return go.load_units(game)
            u = load()[0]["slot"]
            self.assertEqual((u["canon"], u["lo"], u["hi"], u["align"]), (0x8c740000, 0x8c700000, 0x8c800000, 0x8000))
            with self.assertRaisesRegex(go.OverlayError, "slot align"):
                load(align="6000")
            with self.assertRaisesRegex(go.OverlayError, "slot base"):
                load(base="8c744000")

    def test_rebase_text(self):
        import overlay_slots
        text = ("/* 0x8c740010u stays */ u32 a = rdf32(0x8c740010u); switch (x) { case 0x8c740020u: break; } "
                "u32 b = 0x0c740030u; u32 c = 0x8c9a030cu; u32 d = 0x8c748000u; u32 e = 0x10u;")
        got = overlay_slots.rebase_text(text, [(0x0c740000, 0x0c748000)])
        self.assertEqual(got, "/* 0x8c740010u stays */ u32 a = rdf32((0x8c740010u + tx_ovl_delta)); switch (x - tx_ovl_delta) { "
                              "case 0x8c740020u: break; } u32 b = (0x0c740030u + tx_ovl_delta); u32 c = 0x8c9a030cu; "
                              "u32 d = 0x8c748000u; u32 e = 0x10u;")
        # a jump table's target carries the delta and its labels do not; a switch on other values is left alone
        text = "sw_ = r0 + 0x8c740020u; switch (sw_) {\n/* j */\ncase 0x8c740024u: break; } switch (y) { case 0x8c9a030cu: break; }"
        self.assertEqual(overlay_slots.rebase_text(text, [(0x0c740000, 0x0c748000)]),
                         "sw_ = r0 + (0x8c740020u + tx_ovl_delta); switch (sw_ - tx_ovl_delta) {\n/* j */\ncase 0x8c740024u: break; } "
                         "switch (y) { case 0x8c9a030cu: break; }")


    def test_rebased_copies(self):
        """tx_ADDR.c of a function in a slot's block includes a rebased copy of its judged file; others keep it."""
        import overlay_slots
        with tempfile.TemporaryDirectory() as t:
            t = pathlib.Path(t)
            ok = {"8c740100": t / "a.c", "8c900000": t / "b.c"}
            for f in ok.values():
                f.write_text("u32 x = rdf32(0x8c740200u);", encoding="utf-8")
            texts = {a: f'#include "tx_rt.h"\n#include "{f.as_posix()}"' for a, f in ok.items()}
            got = overlay_slots.rebased(dict(texts), ok, [(CANON_A, CANON_A + 0x8000)], t / "txsrc")
            self.assertEqual((t / "txsrc" / "8c740100.c").read_text(encoding="utf-8"),
                             "u32 x = rdf32((0x8c740200u + tx_ovl_delta));")
            self.assertIn("txsrc/8c740100.c", got["8c740100"])
            self.assertEqual(got["8c900000"], texts["8c900000"])


SLOT_DRIVER_C = r"""/* Slot units: a and b loaded at several bases of the region; each base's own unit answers. */
#include <stdio.h>
#include <string.h>
#include "port.h"
#include "overlay.h"

void *guest_alloc(void);
extern const unsigned char img_a[], img_b[];

static void at(const char *what, u32 addr)
{
    const PortDispatch *d = overlay_find(addr);
    const PortDispatch *g = overlay_glue(addr);
    int r = d ? ((int (*)(int))d->fn)(5) : -1;
    printf("%s %08x: %s %d delta %08x glue %s\n", what, addr, d ? d->name : "-", r, d ? overlay_delta() : 0, g ? g->name : "-");
}

int main(void)
{
    if (!guest_alloc()) return 3;
    at("empty", 0x8c780100u);
    memcpy((void *)(uintptr_t)0x8c780000u, img_a, 0x1000);
    at("a", 0x8c780100u);
    memcpy((void *)(uintptr_t)0x8c7a0000u, img_b, 0x1000);
    at("b", 0x8c7a0100u);
    memcpy((void *)(uintptr_t)0x8c7b0000u, img_a, 0x1000);
    at("a2", 0x8c7b0100u);
    at("a again", 0x8c780100u);
    memcpy((void *)(uintptr_t)0x8c780000u, img_b, 0x1000); /* b over a's base */
    at("b2", 0x8c780100u);
    at("a2 still", 0x8c7b0100u);
    at("main", 0x8c020000u);
    at("outside", 0x8c800100u);
    overlay_report();
    return 0;
}
"""
SLOT_GLUE_C = r"""
#include "overlay.h"
const PortDispatch A__txglue[] = {{0x8c740100u, (PortFn)0, "Ga"}};
const PortDispatch B__txglue[] = {{0x8c748100u, (PortFn)0, "Gb"}};
"""


class ReadableGlue(unittest.TestCase):
    def test_readable_file_gets_glue(self):
        """A readable file translator C calls answers through the glue: a helper's r0 and r1 (Spawn's 8c5f621c) by
        argument words would arrive as r4 and r5, and faulted the game. Control: no glue without it in `readable`."""
        from test_sync import TMP  # noqa: F401  (sets DECOMP_PROJECT when run alone)
        from unittest import mock
        import gen_port
        import overlay_tx
        reg = type("Reg", (), {"kind": staticmethod(lambda t: "L" if t == "u64" else "i")})
        addr, proto = "8c5f621c", "u64 Rt_DivModU32(u32 divisor, u32 dividend)"
        out = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        args = (out, reg, "A", [], {}, {addr: proto}, {addr: {"params": []}}, {addr: "Rt_DivModU32"}, {})
        wrote = {}
        with mock.patch.object(gen_port, "write_if_changed", lambda p, t: wrote.update({p: t})), \
                mock.patch("core.readable_abi.own_storages", lambda *x: ["r0", "r1"]):
            self.assertEqual(overlay_tx.write(*args)[1], [])
            self.assertEqual(overlay_tx.write(*args, [addr])[1], [addr])
        self.assertIn("Rt_DivModU32((u32)(uintptr_t)a->rlo[0], (u32)(uintptr_t)a->rlo[1])", wrote[out / "txglue.c"])


class OverlaySlotWindows(unittest.TestCase):
    def test_tiny_unit_gets_overlapping_windows(self):
        # 0x30 bytes of code hold three 16-byte windows at stride 16, but four or more at stride 2
        _, c = image(5, *SOLO)
        w = go.pick_windows("c", c, [SOLO], [(SOLO[0], SOLO[0] + 0x30)], [])
        self.assertGreaterEqual(len(w), go.MIN_WINDOWS)
        self.assertEqual(len({a for a, _ in w}), len(w))


@unittest.skipUnless(HAVE_VC, NO_VC)
class OverlaySlotsBuilt(unittest.TestCase):
    def setUp(self):
        self.out = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def run_exe(self, exe):
        r = subprocess.run([str(self.out / exe)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        return r.stdout.splitlines()

    def test_slot_units_load_at_several_bases(self):
        """One file loaded at three bases of a region: each base runs the unit whose bytes it holds (a, b, a again at
        its own base, b over a's), overlay_delta() says how far from its canonical base, and the glue is found at
        the canonical address; outside the region nothing answers."""
        units, data, reads = slot_units()
        grp = go.groups(units)
        wins = []
        for i, u in enumerate(units):
            base = u["slot"]["canon"]
            blocks = [(base + 0x40 * k, base + 0x40 * k + 0x30) for k in range(16)]
            wins.append(go.pick_windows(u["id"], reads[i], u["ranges"], blocks, go.slots.rivals_of(units, grp, reads, i)))
        rows = [[(CANON_A + 0x100, "A__f", "Fa")], [(CANON_B + 0x100, "B__f", "Fb")]]
        files = {"table.c": go.table_c(units, grp, wins, rows, [("A__txglue", 1), ("B__txglue", 1)]),
                 "driver.c": SLOT_DRIVER_C, "host.c": HOST_C, "glue.c": SLOT_GLUE_C,
                 "images.c": bytes_c("img_a", data[0]) + bytes_c("img_b", data[1])}
        for n, t in files.items():
            (self.out / n).write_text(t, encoding="utf-8")
        log = build(self.out, [self.out / n for n in files] + [KIT / "runtime" / "overlay.c"], "slot.exe")
        self.assertEqual(log, "")
        miss = "no loaded unit has a function there; returns 0 (logged once per group)"
        self.assertEqual(self.run_exe("slot.exe"), [
            f"log: OVERLAY MISS: call to 8c780100 in a's range: {miss}", "empty 8c780100: (no loaded overlay) 0 delta "
            "00000000 glue -",
            "log: overlay: a active at 8c780000 (call to 8c780100)", "a 8c780100: Fa 6 delta 00040000 glue Ga",
            "log: overlay: b active at 8c7a0000 (call to 8c7a0100)", "b 8c7a0100: Fb 10 delta 00058000 glue Gb",
            "log: overlay: a active at 8c7b0000 (call to 8c7b0100)", "a2 8c7b0100: Fa 6 delta 00070000 glue Ga",
            "a again 8c780100: Fa 6 delta 00040000 glue Ga",
            "log: overlay: b active at 8c780000 (call to 8c780100)", "b2 8c780100: Fb 10 delta 00038000 glue Gb",
            "a2 still 8c7b0100: Fa 6 delta 00070000 glue Ga", "main 8c020000: - -1 delta 00000000 glue -",
            "outside 8c800100: - -1 delta 00000000 glue -",
            "log: overlays: 2 units, 4 switches, 1 calls with no loaded function"])


if __name__ == "__main__":
    unittest.main()
