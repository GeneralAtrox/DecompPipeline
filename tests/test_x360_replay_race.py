"""The Xbox 360 kit's command buffers and the threads (runtime/xb_d3d_cb.h, xb_d3d.c): a buffer recorded on one thread and run on another must
leave the recorder's device fields alone and must not read them while the recorder changes them.

- A replayed draw reads its fetch constants and constants from its own copy (cb_snap), for every draw kind; the copy's mask is every register
  the recording has set so far, so a register put back to its baseline is set again (the cumulative mask).
- A run inherits the registers its draws did not set from the buffer's baseline when it runs on the device it was recorded on (cb_inherit_from).
- The recording thread does not set the drawing thread's words; a BeginVertices is drawn on its own thread; the render target slots are written when
  the call is made or recorded, not at the run; GetStreamSource reads the device's fields.
- Every write into a device's fields in the runtime is one of a reviewed list: a new one must be checked against replay before it joins it.

No game data: the functions are cut from the kit's headers and compiled with a stand-in guest memory.
Nearest existing: tests/test_x360_replay_stream.py - covers th_state_store only (the vertex-buffer fields), not the rest of the replayed state."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

RT = X360 / "port" / "runtime"

HARNESS = r'''
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef struct { u64 r[8]; } TcArgs;
static unsigned char *tc_base;
static u32 s_brk = 0x100000u;
static u32 xb_alloc(u32 n) { u32 a = s_brk; s_brk += (n + 0xfffu) & ~0xfffu; return a; }
static void xb_stop(const char *a, const char *b, u32 c) { printf("stop %s %s %x\n", a, b, c); exit(3); }
CUT
#define DEV 0x10000u
#define DEV2 0x20000u
static void setreg(u32 dev, unsigned r, float v) { float f[4] = {v, v, v, v}; unsigned k; for (k = 0; k < 4; k++) { u32 w; memcpy(&w, &f[k], 4); tc_base[dev + 0x780u + 16u * r + 4u * k] = (u8)(w >> 24), tc_base[dev + 0x781u + 16u * r + 4u * k] = (u8)(w >> 16), tc_base[dev + 0x782u + 16u * r + 4u * k] = (u8)(w >> 8), tc_base[dev + 0x783u + 16u * r + 4u * k] = (u8)w; } }
static int bit(u32 snap, unsigned r) { return tc_base[snap + CB_SNAP_AT + CB_SNAP_SIZE + (r >> 3)] >> (r & 7) & 1; }
static unsigned first(u32 snap, unsigned r) { return tc_base[snap + CB_SNAP_AT + 0x300u + 16u * r]; }
int main(void)
{
    static CbList l;
    u32 s1, s2, s3;
    tc_base = (unsigned char *)calloc(1, 0x8000000u);
    setreg(DEV, 12, 2.0f);   /* a view-projection set before recording */
    l.baseg = xb_alloc(CB_SNAP_SIZE);
    memcpy(tc_base + l.baseg, tc_base + DEV + CB_SNAP_AT, CB_SNAP_SIZE);
    l.dev = DEV;
    setreg(DEV, 20, 1.0f);              /* draw 1 sets vertex c20 */
    s1 = cb_snap(&l, DEV);
    setreg(DEV, 20, 0.0f);              /* the game puts c20 back to its baseline and sets pixel c3 */
    setreg(DEV, 256 + 3, 4.0f);
    s2 = cb_snap(&l, DEV);
    s3 = cb_snap(&l, DEV);              /* no change: the copy is shared */
    printf("s1 m20=%d m259=%d m12=%d\n", bit(s1, 20), bit(s1, 259), bit(s1, 12));
    printf("s2 m20=%d m259=%d m12=%d c20=%02x\n", bit(s2, 20), bit(s2, 259), bit(s2, 12), first(s2, 20));
    printf("s3 shared=%d\n", s3 == s2);
    printf("inherit same=%s other=%s\n", cb_inherit_from(&l, DEV) == l.baseg - CB_SNAP_AT ? "baseline" : "device",
           cb_inherit_from(&l, DEV2) == DEV2 ? "device" : "baseline");
    return 0;
}
'''


def cut():
    src = (RT / "xb_d3d_cb.h").read_text(encoding="utf-8")
    parts = []
    for pat in (r"^typedef struct \{ u32 entry, snap.*$", r"^#define CB_CHUNKS .*$", r"^#define CB_SNAPS .*$", r"^#define CB_SNAP_CHUNK .*$",
                r"^typedef struct \{ u32 cb, chunk.*$", r"^#define CB_ARENA .*$", r"^#define CB_SNAP_AT .*$", r"^#define CB_SNAP_SIZE .*$"):
        m = re.search(pat, src, re.M)
        assert m, pat
        line = m.group(0)
        if "/*" in line and "*/" not in line[line.rindex("/*"):]:   # a comment that runs onto the next line (CB_ARENA's): keep the code only
            line = line[:line.rindex("/*")].rstrip()
        parts.append(line)
    parts.append(src[src.index("#define CB_POOL_N"):src.index("/* how: 0 a lookup")])   # cb_snap takes its chunks from the pool
    for sig in ("static u32 cb_snap(CbList *l, u32 dev)", "static u32 cb_inherit_from(const CbList *l, u32 run_dev)"):
        start = src.index(sig)
        parts.append(src[start:src.index("\n}\n", start) + 3])
    return "\n".join(parts)


def body(path, sig):
    src = (RT / path).read_text(encoding="utf-8")
    start = src.index(sig)
    return src[start:src.index("\n}\n", start) + 3]


def test_lib_entry_order():
    """In lib_entry: this thread's BeginVertices first, the render target slots before the recording (never at a run), the draw state after it"""
    b = body("xb_d3d.c", "static int lib_entry(")
    flush, cb, slots, rec, state = (b.index(k) for k in ("begin_flush()", "return cb_entry(", "entry == 0x8274e890u && !t_replay", "cb_record(entry, target, a, r3)",
                                                          "draw_entry_state((u32)a->r[0])"))
    assert flush < cb < slots < rec < state
    assert "entry == 0x8274ebf0u && !t_replay" in b
    assert "xb_gpu_dev_words" not in b and "xb_gpu_depth_word" not in b and "s_fetch_dev =" not in b


def test_thread_own_state():
    d3d = (RT / "xb_d3d.c").read_text(encoding="utf-8")
    for name in ("s_begin_pending", "s_begin_data", "s_begin_decl"):
        assert re.search(r"static __declspec\(thread\) [^;]*\b%s\b" % name, d3d), name
    gpu = (RT / "xb_gpu_draw.h").read_text(encoding="utf-8")
    assert re.search(r"static __declspec\(thread\) u32 s_dc;", gpu) and re.search(r"static __declspec\(thread\) u32 s_dw\[3\];", gpu)
    assert "static __declspec(thread) u32 room;" in (RT / "xb_d3d_draw.h").read_text(encoding="utf-8")


def test_every_draw_kind_snapped_and_stream_getter():
    cb = (RT / "xb_d3d_cb.h").read_text(encoding="utf-8")
    assert "target == 0x829fcfc0 || target == 0x821d18f0 || target == 0x821d12f0 ? cb_snap(" in cb
    assert "cb_inherit_from(l, (u32)a->r[0])" in body("xb_d3d_cb.h", "static int cb_entry(")
    g = (RT / "xb_d3d_res.h").read_text(encoding="utf-8")
    g = g[g.index("case 0x8274e778:"):]
    g = g[:g.index("return 1;")]
    assert "rd32(dev + 0x30acu + 4u * s)" in g and "rd8(dev + 0x30f0u + s)" in g


# Every write into a device's fields (the library's copy, which game code reads) and when it runs. A replay of a command buffer runs on another thread
# while the game records the next one into the same fields, so none may run at a run; add a site here only after checking that
REVIEWED = {
    ("xb_d3d.c", "wr32(dev + 0x3168u"): "view_to_device: at a recorded or made SetViewport; a run passes no device (view_apply(t_replay ? 0 : dev))",
    ("xb_d3d.c", "wr32((u32)a->r[0] + 0x3098u"): "SetRenderTarget slot: !t_replay",
    ("xb_d3d.c", "wr32((u32)a->r[0] + 0x30a8u"): "SetDepthStencilSurface slot: !t_replay",
    ("xb_d3d_th.h", "wr32(dev + 0x2e2cu"): "th_state_store: returns first when replaying",
    ("xb_d3d_th.h", "wr32(dev + 0x30acu"): "th_state_store",
    ("xb_d3d_th.h", "wr8(dev + 0x30f0u"): "th_state_store",
    ("xb_d3d_th.h", "wr32(dev + 8u * (0xefu - s)"): "th_state_store",
    ("xb_d3d_res.h", "wr32(dev + 0x2a9cu"): "fence_kick: InsertFence and kicks, never recorded (made at once)",
    ("xb_d3d_res.h", "wr32((u32)a->r[0] + 0x30u"): "device creation and the ring: never recorded",
    ("xb_d3d_res.h", "wr32((u32)a->r[0] + 0x38u"): "device creation",
    ("xb_d3d_res.h", "wr32((u32)a->r[0] + 0x2a90u"): "device creation",
    ("xb_d3d_res.h", "wr32((u32)a->r[0] + 0x2a9cu"): "device creation",
    ("xb_d3d_res.h", "wr32((u32)a->r[0] + 16u * ((target == 0x821ccf78"): "shader constants: if (!t_replay)",
    ("xb_d3d_draw.h", "wr32((u32)a->r[0] + 0x3484u"): "BeginVertices: stops while recording, so never replayed",
    ("xb_d3d_draw.h", "wr32(dev + 60"): "AddRef / Release of the device: never recorded",
    ("xb_d3d_draw.h", "wr32(dev + 0x3098 + 4 * i"): "the game's device reset between frames: never recorded",
    ("xb_d3d_draw.h", "wr32(dev + 0x30a8"): "the game's device reset",
    ("xb_d3d_cb.h", "memcpy(tc_base + (u32)a->r[0] + 16u"): "cb_record: on the recording thread, as the library stores them",
    ("xb_state.c", "wr32(dev + XB_DEV_ZENABLE"): "depth_word: from xb_state_recorded and xb_state_call's !t_replay",
    ("xb_state.c", "wr32(dev + XB_DEV_DEPTHCONTROL"): "depth_word",
    ("xb_state.c", "wr32(dev + 0x10"): "dirty bits: depth_word / dev_dirty",
    ("xb_state.c", "wr32(dev + 0x14"): "dev_dirty",
    ("xb_state.c", "wr32(dev + XB_DEV_COLORMASK_COPY"): "dev_words_set: recorded, made (!t_replay) or at init",
    ("xb_state.c", "wr32(dev + XB_DEV_COLORMASK"): "dev_words_set",
    ("xb_state.c", "wr32(dev + XB_DEV_COLORCONTROL"): "dev_words_set",
    ("xb_state.c", "wr32(dev + XB_DEV_MODECONTROL"): "dev_words_set",
    ("xb_state.c", "wr32(dev + 0x2e4c"): "blend_bit: recorded or made (!t_replay)",
}


def test_device_writes_reviewed():
    found = set()
    pat = re.compile(r"(wr(?:8|16|32)\((?:\(u32\)a->r\[0\]|dev|dv) *\+ *[^,;]*|memcpy\(tc_base \+ \(u32\)a->r\[0\] \+ 16u)")
    for f in ("xb_d3d.c", "xb_d3d_th.h", "xb_d3d_res.h", "xb_d3d_draw.h", "xb_d3d_cb.h", "xb_state.c", "xb_d3d_tex.h", "xb_d3d_vtx.h", "xb_d3d_edram.h"):
        for m in pat.finditer((RT / f).read_text(encoding="utf-8")):
            hit = m.group(0)
            key = max((k for k in REVIEWED if k[0] == f and hit.startswith(k[1])), key=lambda k: len(k[1]), default=None)
            assert key, f"{f}: {hit!r} writes a device's fields and is not reviewed for command buffer runs (REVIEWED)"
            found.add(key)
    assert found == set(REVIEWED), sorted(set(REVIEWED) - found)


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Snapshots(unittest.TestCase):
    def run_harness(self, env_extra):
        d = tmpdir("x360rr_")
        try:
            (d / "t.c").write_text(HARNESS.replace("CUT", cut()), encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            env = dict(os.environ)
            for k in ("XB_SNAP_MASK", "XB_REPLAY_INHERIT"):
                env.pop(k, None)
            env.update(env_extra)
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cumulative_mask_and_inherit(self):
        got = self.run_harness({})
        self.assertEqual(got[0], "s1 m20=1 m259=0 m12=0")
        self.assertEqual(got[1], "s2 m20=1 m259=1 m12=0 c20=00")   # c20 back at its baseline: still set, with the baseline's value
        self.assertEqual(got[2], "s3 shared=1")
        self.assertEqual(got[3], "inherit same=baseline other=device")

    def test_old_switches(self):
        got = self.run_harness({"XB_SNAP_MASK": "diff", "XB_REPLAY_INHERIT": "live"})
        self.assertEqual(got[1], "s2 m20=0 m259=1 m12=0 c20=00")
        self.assertEqual(got[3], "inherit same=device other=device")
