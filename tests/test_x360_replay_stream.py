"""The Xbox 360 kit's Too Human stream store (xb_d3d_th.h th_state_store): a SetStreamSource or SetVertexDeclaration replayed from a command
buffer leaves the device's fields alone; one made live writes them, as the library's setters do.

No game data: the function is cut from the kit's header and compiled with a stand-in guest memory."""
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int u32;
typedef unsigned long long u64;
typedef struct { u64 r[8]; } TcArgs;
static unsigned char mem[0x10000];
static u32 rd32(u32 a) { return (u32)mem[a] << 24 | (u32)mem[a + 1] << 16 | (u32)mem[a + 2] << 8 | mem[a + 3]; }
static void wr32(u32 a, u32 v) { mem[a] = (unsigned char)(v >> 24); mem[a + 1] = (unsigned char)(v >> 16); mem[a + 2] = (unsigned char)(v >> 8); mem[a + 3] = (unsigned char)v; }
static void wr8(u32 a, u32 v) { mem[a] = (unsigned char)v; }
FUNC
#define DEV 0x1000u
static void show(const char *what)
{
    printf("%s buf %08x stride %u fetch %08x decl %08x\n", what, rd32(DEV + 0x30acu), mem[DEV + 0x30f0u], rd32(DEV + 8u * 0xefu), rd32(DEV + 0x2e2cu));
}
int main(void)
{
    TcArgs a;
    wr32(0x8000u + 0x18u, 0x0bb002d4u);   /* buffer header A: data address */
    wr32(0x9000u + 0x18u, 0x0bbbcb6cu);   /* buffer header B */
    memset(&a, 0, sizeof a);
    a.r[0] = DEV, a.r[1] = 0, a.r[2] = 0x8000u, a.r[3] = 0, a.r[4] = 12;
    th_state_store(0x8274e658u, &a, 0);   /* live: the recorder sets mesh A's stream */
    show("live");
    a.r[2] = 0x9000u;
    th_state_store(0x8274e658u, &a, 1);   /* the render thread replays a buffer that set B: the device keeps A */
    show("replay");
    a.r[1] = 0x4242u;
    th_state_store(0x82758b08u, &a, 1);   /* a replayed declaration: kept too */
    show("replay-decl");
    th_state_store(0x82758b08u, &a, 0);
    show("live-decl");
    return 0;
}
'''


def cut_function():
    src = (X360 / "port" / "runtime" / "xb_d3d_th.h").read_text(encoding="utf-8")
    start = src.index("static void th_state_store(u32 entry, const TcArgs *a, int replaying)")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def test_replay_is_passed_in():
    d3d = (X360 / "port" / "runtime" / "xb_d3d.c").read_text(encoding="utf-8")
    assert "th_state_store(entry, a, t_replay)" in d3d


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class ReplayStream(unittest.TestCase):
    def run_harness(self, env_old):
        d = tmpdir("x360rs_")
        try:
            (d / "t.c").write_text(HARNESS.replace("FUNC", cut_function()), encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            env = dict(os.environ)
            env.pop("XB_REPLAY_STREAM_STORE", None)
            if env_old:
                env["XB_REPLAY_STREAM_STORE"] = "1"
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_replay_keeps_device(self):
        got = self.run_harness(False)
        self.assertEqual(got[0], "live buf 00008000 stride 3 fetch 0bb002d4 decl 00000000")
        self.assertEqual(got[1], "replay buf 00008000 stride 3 fetch 0bb002d4 decl 00000000")
        self.assertEqual(got[2], "replay-decl buf 00008000 stride 3 fetch 0bb002d4 decl 00000000")
        self.assertEqual(got[3], "live-decl buf 00008000 stride 3 fetch 0bb002d4 decl 00004242")

    def test_old_switch(self):
        got = self.run_harness(True)
        self.assertEqual(got[1], "replay buf 00009000 stride 3 fetch 0bbbcb6c decl 00000000")
