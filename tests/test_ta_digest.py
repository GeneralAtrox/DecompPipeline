"""The display-list digest (port/tools/ta_digest.py has the masks and their sources): both halves, the emulator's
(port/tools/hw_emu.lua, run by a stock Lua on synthetic pvr-primitive events) and the port's (port/runtime/ta_digest.c,
compiled with a small driver), hash the same masked fields as a Python reference written from the mask table; a field
a polygon's mode does not use (the offset alpha without per-vertex fog, a flat strip's first two colours, a decal's
base colour, a sprite's d z) changes no digest; the compare aligns strips past one that differs, and tools/lockstep.py
never prints a plain "no divergence" when the lists differ, and says "RAM only" for a recording without digests.
Lua only where an interpreter is found (lua or lua5.4 on PATH, or $LUA), cl only with Visual C++ (test_lockstep's
HAVE_VC): without them those tests skip and say so on stderr: a skip is not a pass.
Run: python -W error tests/test_ta_digest.py
"""
import contextlib
import io
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
import lockstep  # noqa: E402
import ta_digest  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

LUA = os.environ.get("LUA") or shutil.which("lua") or shutil.which("lua5.4")
NO_LUA = ("no Lua interpreter found (lua or lua5.4 on PATH, or $LUA): the hw_emu.lua digest tests are SKIPPED, "
          "not passed")
if not LUA:
    sys.stderr.write(f"*** {NO_LUA}\n")
LANES = ((65599, 4294967291), (31337, 4294967279))
F = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]  # noqa: E731

# Strips as (pcw, tsp, kind, vertices): each vertex x y z u v (floats), base and offset colour (a r g b words).
TEX, OFS, GOUR, POLY, SPRITE = 0x8, 0x4, 0x2, 0x80000000, 0xa0000000
USE_ALPHA, FOG_VERTEX, FOG_NONE = 0x100000, 0x400000, 0x800000  # TSP bit 20; fog (23:22) 01 per vertex, 10 none
SHAD_DECAL, SHAD_MODULATE, SHAD_MODULATE_A = 0, 0x40, 0x80  # TSP shading (7:6)
V = [(1.5, 2.0, 0.25, 0.125, 0.75, 0x80112233, 0x40445566), (3.0, 4.5, 0.5, 0.5, 0.25, 0xff102030, 0x7f405060),
     (5.25, 1.0, 0.75, 0.875, 0.0, 0x01aabbcc, 0x02ddeeff), (6.0, 7.0, 0.125, 0.25, 1.0, 0x33010203, 0x44040506)]
STRIPS = [(POLY | TEX | OFS | GOUR, USE_ALPHA | FOG_NONE | SHAD_MODULATE_A, 2, V[:3]),
          (POLY | TEX | OFS | GOUR, USE_ALPHA | FOG_VERTEX | SHAD_MODULATE_A, 2, V[1:4]),
          (POLY, USE_ALPHA, 2, V[:4]),                                   # flat: colours from the third vertex
          (POLY | TEX | GOUR, USE_ALPHA | SHAD_DECAL, 2, V[:3]),
          (SPRITE | TEX, USE_ALPHA | SHAD_MODULATE, 3, V[:4]),           # sprite a b c d = V[0..3]
          (SPRITE, 0, 3, [(-1.5, -1.0, 0.1, 0, 0, 0x00430000, 0)] + [(-0.5, -v[1], 0.1, 0, 0, 0, 0) for v in V[1:4]])]
OFF = [(-1.5, 2.0) + V[0][2:]] + [(-v[0],) + v[1:] for v in V[1:3]]   # every x negative: off the screen


def expected(pcw, tsp, kind, vs):
    """The fields the mask table keeps, written from it (not from either half)."""
    off = all(F(v[0]) > 0x80000000 for v in vs) or all(F(v[1]) > 0x80000000 for v in vs)
    tex, out = pcw & TEX and not off, []
    bm = 0xffffffff if tsp & USE_ALPHA else 0x00ffffff
    shad = (tsp >> 6) & 3
    bm = 0 if tex and shad == 0 else bm & 0x00ffffff if tex and shad == 1 else bm
    om = (0xffffffff if (tsp >> 22) & 3 == 1 else 0x00ffffff) if tex and pcw & OFS else 0
    if off:
        bm = om = None
    pos = lambda v, z=True: [F(v[0]), F(v[1])] + ([F(v[2])] + ([F(v[3]), F(v[4])] if tex else []) if z else [])
    if kind == 3:
        return pos(vs[0]) + pos(vs[1]) + pos(vs[2]) + pos(vs[3], False) + ([vs[0][5] & bm, vs[0][6] & om]
                                                                            if not off else [])
    for i, v in enumerate(vs):
        out += pos(v) + ([v[5] & bm, v[6] & om] if not off and (pcw & GOUR or i >= 2) else [])
    return out


def strip_hash(fields):
    h = [1, 1]
    for x in fields:
        h = [(h[i] * m + x) % p for i, (m, p) in enumerate(LANES)]
    return h


def lines(strips, lst=0):
    """The reference's D and H lines of one list."""
    acc, hs, offs = [[1, 1], [1, 1], [1, 1]], [], []
    for n, s in enumerate(strips):
        h = strip_hash(expected(*s))
        hs.append(h)
        offs.append(all(v[0] < 0 for v in s[3]) or all(v[1] < 0 for v in s[3]))
        for k in range(min(n, 2) + 1):
            for x in h:
                acc[k] = [(acc[k][i] * m + x) % p for i, (m, p) in enumerate(LANES)]
    hx = lambda h: "%08x%08x" % tuple(h)  # noqa: E731
    return [f"D {lst} {hx(hs[0])} {hx(acc[0])} {hx(acc[1])} {hx(acc[2])}", f"H {lst} " + " ".join(
        "%08x" % h[0] + ("o" if o else "") for h, o in zip(hs, offs))]


class Masks(unittest.TestCase):
    def test_unused_fields_change_nothing(self):
        fog_none, fog_vertex, flat, decal, sprite, dummy = STRIPS
        self.assertEqual(expected(*dummy[:3], [v[:5] + (0, 0) for v in dummy[3]]), expected(*dummy))
        self.assertEqual(expected(*fog_vertex[:3], [v[:5] + (1, 2) for v in OFF]), expected(*fog_vertex[:3], OFF))
        self.assertNotEqual(expected(*fog_vertex[:3], [(1.0,) + OFF[0][1:]] + OFF[1:]), expected(*fog_vertex[:3], OFF))
        alpha = lambda vs: [v[:6] + (v[6] ^ 0xff000000,) for v in vs]  # noqa: E731
        self.assertEqual(expected(*fog_none[:3], alpha(fog_none[3])), expected(*fog_none))
        self.assertNotEqual(expected(*fog_vertex[:3], alpha(fog_vertex[3])), expected(*fog_vertex))
        first2 = [v[:5] + (0, 0) for v in flat[3][:2]] + list(flat[3][2:])
        self.assertEqual(expected(*flat[:3], first2), expected(*flat))
        self.assertEqual(expected(*decal[:3], [v[:5] + (0, v[6]) for v in decal[3]]), expected(*decal))
        d_z = list(sprite[3][:3]) + [(6.0, 7.0, 9.0, 9.0, 9.0) + sprite[3][3][5:]]
        self.assertEqual(expected(*sprite[:3], d_z), expected(*sprite))
        self.assertNotEqual(expected(*sprite[:3], [(0.0,) + sprite[3][0][1:]] + list(sprite[3][1:])),
                            expected(*sprite))


def lua_events(strips):
    """The strips as Flycast's pvr-primitive events (a sprite's vertices in its order d, c, a, b: hw_emu.lua)."""
    out = []
    for pcw, tsp, kind, vs in strips:
        order = [vs[3], vs[2], vs[0], vs[1]] if kind == 3 else vs
        vx = ", ".join("{x_bits=%d, y_bits=%d, z_bits=%d, u_bits=%d, v_bits=%d, base_color_hex='%08x', "
                       "offset_color_hex='%08x'}" % (F(v[0]), F(v[1]), F(v[2]), F(v[3]), F(v[4]),
                                                    (v[5] << 8 | v[5] >> 24) & 0xffffffff,
                                                    (v[6] << 8 | v[6] >> 24) & 0xffffffff) for v in order)
        out.append("cb({render_generation=1, context_address=0, tick=1, primitive_kind=%d, list_type=0, pcw=%d, isp=0, "
                   "tsp=%d, tcw=0, count=%d, vertices={%s}})" % (kind, pcw, tsp, len(vs), vx))
    return "\n".join(out)


@unittest.skipUnless(LUA, NO_LUA)
class EmulatorHalf(unittest.TestCase):
    def test_lua_matches_reference(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ta_lua_"))
        try:
            (d / "h.lua").write_text(
                "bit32 = {band = function(a, b) return math.tointeger(a) & math.tointeger(b) end,\n"
                "         bnot = function(a) return ~math.tointeger(a) & 0xffffffff end}\n"
                "flycast = {research = {subscribe = function(o, fn) cb = fn; return 1 end,\n"
                "                       subscription_stats = function() return nil end}}\n"
                f"LS_HW = [[{d / 'hw.txt'}]]\ndofile([[{KIT / 'tools' / 'hw_emu.lua'}]])\nhw_start()\n"
                + lua_events(STRIPS) + "\nhw_close()\n", encoding="utf-8")
            r = subprocess.run([LUA, str(d / "h.lua")], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = [ln for ln in (d / "hw.txt").read_text().splitlines() if ln[:1] in "DH"]
            self.assertEqual(got, lines(STRIPS))
        finally:
            shutil.rmtree(d, ignore_errors=True)


def c_words(pcw, tsp, kind, vs):
    """The port's parameters of a strip: its header (8 words) and vertex words (type 3: x y z u v base offset; a
    sprite's one 16-word parameter, its colours in the header)."""
    hdr = [pcw, 0, tsp, 0, vs[0][5] if kind == 3 else 0, vs[0][6] if kind == 3 else 0, 0, 0]
    if kind == 3:
        uv = [F(v[3]) & 0xffff0000 | F(v[4]) >> 16 for v in vs[:3]]
        return hdr, [[0] + [F(c) for v in vs[:3] for c in v[:3]] + [F(vs[3][0]), F(vs[3][1]), 0] + uv]
    return hdr, [[0, F(v[0]), F(v[1]), F(v[2]), F(v[3]), F(v[4]), v[5], v[6]] for v in vs]


DRIVER = """#include <stdio.h>
#include "port.h"
#include "lockstep_parts.h"
static const unsigned H[][8] = {%s};
static const unsigned W[][16] = {%s};
static const int N[] = {%s}, S[] = {%s};
int main(void)
{
    int w = 0;
    ta_digest_list(0);
    for (int i = 0; i < (int)(sizeof N / sizeof N[0]); i++) {
        ta_digest_header(H[i], 8, S[i]);
        for (int k = 0; k < N[i]; k++) ta_digest_vertex(W[w++]);
        ta_digest_strip(0);
    }
    ta_digest_write(stdout, 0);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class PortHalf(unittest.TestCase):
    def test_c_matches_reference(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ta_c_"))
        try:
            hs, ws = zip(*[c_words(*s) for s in STRIPS])
            arr = lambda rows: ", ".join("{" + ", ".join(f"0x{x:08x}u" for x in r) + "}" for r in rows)  # noqa: E731
            (d / "t.c").write_text(DRIVER % (arr(hs), arr([w + [0] * (16 - len(w)) for v in ws for w in v]),
                                             ", ".join(str(len(v)) for v in ws),
                                             ", ".join(str(int(s[2] == 3)) for s in STRIPS)), encoding="utf-8")
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "host"}" /I"{rd}" t.c "{KIT / "runtime" / "ta_digest.c"}" /Fet.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            r = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.stdout.splitlines(), lines(STRIPS))
        finally:
            shutil.rmtree(d, ignore_errors=True)


def lst(seq, hs):
    """A list as read: strips (header pcw, count) with their H hashes."""
    e = ta_digest.List(tok=" ".join(f"h {p:x} 0 0 0 {n}" for p, n in seq), lst=0, emu=True, sum=(0, 0, 0))
    e["hs"] = " ".join(hs)
    return e


class Compare(unittest.TestCase):
    def test_strip_diff_aligns(self):
        e = lst([(POLY, 3), (POLY, 4), (POLY, 3), (POLY, 5)], ["a1", "b2", "c3", "d4"])
        p = lst([(POLY, 3), (POLY, 4), (POLY, 3), (POLY, 5)], ["a1", "bX", "c3", "d4"])
        self.assertEqual(ta_digest.strip_diff(e, p, 0), [(1, 1, "fields")])
        p = lst([(POLY, 3), (POLY, 6), (POLY, 3), (POLY, 5)], ["a1", "b2", "c3", "d4"])
        self.assertEqual(ta_digest.strip_diff(e, p, 0), [(1, 1, "vertex count")])
        p = lst([(POLY, 3), (POLY, 3), (POLY, 5)], ["a1", "c3", "d4"])
        self.assertEqual(ta_digest.strip_diff(e, p, 0), [(1, None, "only in Flycast")])
        self.assertEqual(ta_digest.strip_diff(e, e, 0), [])
        q = lst([(POLY, 3), (POLY, 4), (POLY, 3), (POLY, 5)], ["a1", "bXo", "c3", "d4"])  # strip 1 off the screen
        self.assertEqual(ta_digest.strip_diff(e, q, 0, old=True), [(1, 1, "off the screen")])
        self.assertEqual(ta_digest.strip_diff(e, q, 0), [(1, 1, "fields")])
        self.assertEqual(ta_digest.score({0: e}, {0: p}), 1)
        e, p = lst([(POLY, 3)], []), lst([(POLY, 3)], [])  # D lines only (no H): the list's digest
        e["dig"], p["dig"] = ("a", "b", "c", "d"), ("a", "x", "c", "d")
        self.assertEqual(ta_digest.strip_diff(e, p, 0), [(None, None, "vertex digest")])

    def test_read_dump(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ta_dump_"))
        try:
            (d / "x.dump").write_text("R 7\nS 2 0 2 3 828e001e 0\nV 1 2 3\nR 9\nS 0 0 2 3 0 0\nV 4\nEND\n")
            self.assertEqual(len(ta_digest.read_dump(d / "x.dump")), 2)  # no render asked: every strip
            self.assertEqual(ta_digest.read_dump(d / "x.dump", 9), [(0, 0, 2, 3, 0, 0, [4])])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_old_recording_is_ram_only(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ta_cmp_"))
        try:
            (d / "hw_emu.txt").write_text("R 0 1 1\nL 0 1 3 0 0 0 h 80000000 0 0 0 3\n")
            rc, out = ta_digest.check(d / "hw_emu.txt", d / "hw_port.txt", [])
            self.assertIsNone(rc)
            self.assertIn("RAM only", out[0])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_detail_replays_with_the_recordings_flycast(self):
        """detail's replay (hw_check emu) plays the Flycast the recording was made with, which its emu.lua names
        (emu_run writes it) and on its cable (LS_CABLE); an explicit --flycast or --cable wins; a recording that names
        none keeps the default, and says so."""
        from unittest import mock
        import hw_check
        d = pathlib.Path(tempfile.mkdtemp(prefix="ta_detail_"))
        try:
            game, rec = d / "game", d / "rec"
            game.mkdir()
            rec.mkdir()
            (game / "lockstep.spec").write_text("label 1 8c000000 label time\npad 8 8c000010 pad\nrtc 2 8c000040 rtc\n")
            (rec / "emu.txt").write_text("".join(f"{n:08x} {n} {n:08x}" + " 0" * 10 + "\n" for n in (1, 2, 3)))
            exe = "C:/Flycasts/with-hooks/flycast.exe"
            calls = []

            def run(extra, lua):
                calls.clear()
                (rec / "emu.lua").unlink(missing_ok=True)
                if lua is not None:
                    (rec / "emu.lua").write_text(lua)
                with mock.patch.object(lockstep, "port_run", return_value=(0, 0.0, d / "port.log")), \
                        mock.patch.object(hw_check, "main", side_effect=lambda argv: calls.append(argv)), \
                        contextlib.redirect_stdout(io.StringIO()) as out:
                    ta_digest.main(["detail", "--game", str(game), "--rec", str(rec), "--build-dir", "b",
                                    "--frame", "1", "--render", "2", "--"] + extra)
                fly = [c[c.index("--flycast") + 1] for c in calls if "--flycast" in c]
                return fly, out.getvalue()

            lua = f'LS_FLYCAST = "{exe}"\nLS_SPEC = "x"\n'
            self.assertEqual(run([], lua)[0], [exe])
            self.assertEqual(run(["--flycast", "C:/other/flycast.exe"], lua)[0], ["C:/other/flycast.exe"])
            fly, out = run([], 'LS_SPEC = "x"\n')
            self.assertEqual(fly, [])
            self.assertIn("does not name its Flycast", out)
            self.assertEqual(lockstep.recording_flycast(rec / "emu.txt"), None)
            (rec / "emu.lua").write_text(lua)
            self.assertEqual(lockstep.recording_flycast(rec / "emu.txt"), exe)

            # the cable: the recording's (LS_CABLE) unless one is given (argparse keeps the last); none for an old one
            def cable(extra, lua):
                run(extra, lua)
                c = calls[0]
                return c[len(c) - c[::-1].index("--cable")] if "--cable" in c else None
            self.assertEqual(cable([], lua + 'LS_CABLE = "default"\n'), "default")
            self.assertEqual(cable(["--cable", "0"], lua + 'LS_CABLE = "default"\n'), "0")
            self.assertEqual(cable([], lua), None)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_hw_check_emu_plays_the_cable(self):
        """hw_check emu --cable: "default" starts Flycast with no cable entry (its own TV cable), a number sets that
        Dreamcast.Cable; without it emu_run keeps lockstep.CONSOLE (the VGA box)."""
        from unittest import mock
        import hw_check
        d = pathlib.Path(tempfile.mkdtemp(prefix="ta_cable_"))
        try:
            seen = []

            def emu_run(*args, **kw):
                seen.append(kw.get("console"))
                raise RuntimeError("stop")
            for extra in (["--cable", "default"], ["--cable", "3"], []):
                with mock.patch.object(lockstep, "emu_run", side_effect=emu_run), self.assertRaises(RuntimeError):
                    hw_check.main(["emu", "--game", str(d), "--out", str(d / "out")] + extra)
            self.assertEqual(seen, [[], ["config:Dreamcast.Cable=3"], None])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_emu_run_names_its_flycast(self):
        """emu_run writes the exe it starts into the recording's Lua file, where recording_flycast reads it."""
        from unittest import mock
        d = pathlib.Path(tempfile.mkdtemp(prefix="ta_emu_"))
        try:
            game = d / "game"
            game.mkdir()
            exe = d / "fly" / "flycast.exe"
            cfg = {"disc": str(d / "disc.gdi"), "lockstep_sample_pcs": [], "lockstep_rtc_pcs": [],
                   "lockstep_scene_pcs": []}
            with mock.patch.object(lockstep, "load_game", return_value=cfg), \
                    mock.patch.object(lockstep.subprocess, "Popen", side_effect=RuntimeError("no Flycast here")):
                with self.assertRaises(RuntimeError):
                    lockstep.emu_run(game, d / "spec", d / "rec" / "emu.txt", 1, [], exe=str(exe))
            self.assertEqual(lockstep.recording_flycast(d / "rec" / "emu.txt"), exe.resolve().as_posix())
            # and the cable it plays: CONSOLE's VGA box, Flycast's own (console []), or an entry's
            for kw, want, entry in (({}, "0", "Cable=0"), ({"console": []}, "default", None),
                                    ({"entries": ["config:Dreamcast.Cable=3"]}, "3", "Cable=3")):
                popen = []
                with mock.patch.object(lockstep, "load_game", return_value=cfg), \
                        mock.patch.object(lockstep.subprocess, "Popen",
                                          side_effect=lambda argv, **_: popen.append(argv) or 1 / 0):
                    with self.assertRaises(ZeroDivisionError):
                        lockstep.emu_run(game, d / "spec", d / "rec" / "emu.txt", 1, [], exe=str(exe), **kw)
                self.assertEqual(lockstep.recording_flycast(d / "rec" / "emu.txt", "LS_CABLE"), want)
                cables = [e for e in popen[0][2].split(",") if e.startswith("config:Dreamcast.Cable=")]
                self.assertEqual(cables, [f"config:Dreamcast.{entry}"] if entry else [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_ram_clean_lists_differ_not_plain(self):
        res = {"frames": 10, "traced": 10, "pointers": 0, "floats": 0, "float_max": 0.0, "loads": 0, "load_diffs": 0,
               "drawn": 0, "known": 0, "first_time": None, "first": None, "last": 9, "driven": 10}
        for differ, want in ((True, "no RAM divergence (the display lists differ: below)"), (False, "no divergence")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                lockstep.report(res, [], differ)
            last = buf.getvalue().splitlines()[-1]
            self.assertEqual(last, want)


if __name__ == "__main__":
    unittest.main()
