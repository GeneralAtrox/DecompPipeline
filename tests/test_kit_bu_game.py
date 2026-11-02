"""The kit's VMU program calls (port/sdk/bu_vmu.c buSaveExecFile, buRewriteExecFile, buLoadFileEx, buDefragDisk) and its
stand-in for the BIOS font (port/sdk/sy_font.c), in behaviour: compiled at /W4 /WX with a synthetic game.h and stub host
calls, then run. A game file sits at block 0 in order with its header offset 1, completes with its own op, a second game
is refused, a rewrite changes only the blocks asked for; a glyph is 12x24 or 24x24 and packed the way a game plots it,
and a wide code with no glyph is blank. Only where Visual Studio's 32-bit tools are found.

usage: python -W error tests/test_kit_bu_game.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

GAME_H = """#include <string.h>
#include "rd_rt.h"
#include "platform.h"
typedef void (*SdkProc)(void);
typedef struct BUS_TIME { u16 year; u8 month, day, hour, minute, second, dayofweek; } BUS_TIME;
typedef struct BUS_FILEINFO { u8 b[20]; } BUS_FILEINFO;
typedef struct BUS_DISKINFO { u8 b[0x38]; } BUS_DISKINFO;
u8 *Sdk_BiosFontGetAddress(void);
s32 Sdk_BiosFontLock(void);
void Sdk_BiosFontUnlock(void);
s32 syBtFntGetAddr(s32 fontset, s32 code);
void syBtFntGetInfo(s32 fontset, s32 code, u8 *info);
"""

DRIVER_C = r"""#include "game.h"
#include <stdio.h>
s32 buInit(s32 capacity, s32 drive, void *work, SdkProc func);
void buSetCompleteCallback(SdkProc func);
s32 buStat(s32 drive);
s32 buSaveExecFile(s32 drive, const char *fileName, const void *buffer, u32 blocks, const BUS_TIME *time, u32 flag);
s32 buRewriteExecFile(s32 drive, const char *fileName, const void *buffer, u32 start, u32 blocks);
s32 buLoadFileEx(s32 drive, const char *fileName, void *buffer, u32 start, u32 blocks);
s32 buDefragDisk(s32 drive, void *work);
s32 buGetFileInfo(s32 drive, const char *fileName, BUS_FILEINFO *info);

void port_log(const char *fmt, ...) { (void)fmt; }
void host_local_time(HostTime *t) { memset(t, 0, sizeof *t); }
void port_add_vblank_task(PortVblankFn fn, void *arg) { (void)fn, (void)arg; }
PortCodeFn port_code_function(u32 addr) { (void)addr; return NULL; }
int host_save_read(const char *name, void *buf, u32 size) { (void)name, (void)buf, (void)size; return 0; }
int host_save_write(const char *name, const void *buf, u32 size) { (void)name, (void)buf, (void)size; return 1; }
u32 maple_card_sockets(void) { return 1; }

static s32 g_op, g_stat;
static s32 done(s32 drive, s32 op, s32 stat, u32 param) { (void)drive, (void)param; g_op = op; g_stat = stat; return 0; }
static void settle(const char *what) { buStat(0); printf("%s %x %x\n", what, (unsigned)g_op, (unsigned)g_stat); }

static u8 data[4 * 512], back[512];

static s32 lit(const u8 *p, s32 n)
{
    s32 on = 0;
    for (s32 i = 0; i < n; i++) on += p[i] != 0;
    return on > 0;
}

int main(void)
{
    BUS_TIME t;
    BUS_FILEINFO info;
    u8 size[2];
    const u8 *font;
    s32 i;
    memset(&t, 0, sizeof t);
    for (i = 0; i < 4; i++) memset(data + i * 512, 0x10 + i, 512);
    buInit(0, 1, NULL, NULL);
    buSetCompleteCallback((SdkProc)done);
    buStat(0);
    buSaveExecFile(0, "MY_GAME", data, 4, &t, 0);
    settle("save");
    buGetFileInfo(0, "MY_GAME", &info);
    printf("info %u %u %u\n", info.b[4], info.b[6], info.b[8]);
    buSaveExecFile(0, "OTHER", data, 1, &t, 0);
    settle("second");
    memset(data, 0x77, 512);
    buRewriteExecFile(0, "MY_GAME", data, 2, 1);
    settle("rewrite");
    for (i = 0; i < 4; i++) {
        buLoadFileEx(0, "MY_GAME", back, (u32)i, 1);
        buStat(0);
        printf("block%d %02x\n", i, back[0]);
    }
    printf("loadex %x %x\n", (unsigned)g_op, (unsigned)g_stat);
    buDefragDisk(0, NULL);
    settle("defrag");

    font = Sdk_BiosFontGetAddress();
    printf("lock %d\n", Sdk_BiosFontLock());
    syBtFntGetInfo(1, 'A', size);
    printf("ascii %u %u %d\n", size[0], size[1], lit(font + syBtFntGetAddr(1, 'A'), 36));
    printf("space %d\n", lit(font + syBtFntGetAddr(1, ' '), 36));
    syBtFntGetInfo(2, 0x8260, size);
    printf("wide %u %u %d\n", size[0], size[1], lit(font + syBtFntGetAddr(2, 0x8260), 72));
    printf("kanji %d\n", lit(font + syBtFntGetAddr(3, 0x889f), 72));
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class GameFileAndFont(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = pathlib.Path(tempfile.mkdtemp())
        d = cls.dir
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "game.h").write_text(GAME_H, encoding="utf-8")
        (d / "driver.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{KIT / "sdk" / n}"' for n in ("bu_vmu.c", "vmu_flash.c", "sy_font.c"))
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /D_CRT_SECURE_NO_WARNINGS /I"{d}" /I"{KIT / "runtime"}" '
              f'/I"{KIT / "base"}" /I"{rd}" "{d / "driver.c"}" {srcs} /Fe"{d / "bu.exe"}"')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        cls.build = r.stdout[-3000:] + r.stderr[-1000:] if r.returncode or "warning" in r.stdout.lower() else ""
        cls.out = [] if cls.build else subprocess.run([str(d / "bu.exe")], capture_output=True, text=True,
                                                      timeout=60).stdout.splitlines()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def line(self, key):
        self.assertEqual(self.build, "", "the driver does not build at /W4 /WX")
        return next(ln for ln in self.out if ln.split()[0] == key)

    def test_save_game(self):
        self.assertEqual(self.line("save"), "save d 0")
        self.assertEqual(self.line("info"), "info 4 1 1")  # 4 blocks, a game, its header in block 1

    def test_one_game_a_card(self):
        self.assertEqual(self.line("second"), "second d ffffff09")

    def test_rewrite_only_what_it_asked(self):
        self.assertEqual(self.line("rewrite"), "rewrite 14 0")
        self.assertEqual([self.line(f"block{i}").split()[1] for i in range(4)], ["10", "11", "77", "13"])
        self.assertEqual(self.line("loadex"), "loadex e 0")

    def test_defrag_completes(self):
        self.assertEqual(self.line("defrag"), "defrag 9 0")

    def test_font(self):
        self.assertEqual(self.line("lock"), "lock 0")
        self.assertEqual(self.line("ascii"), "ascii 12 24 1")
        self.assertEqual(self.line("space"), "space 0")
        self.assertEqual(self.line("wide"), "wide 24 24 1")
        self.assertEqual(self.line("kanji"), "kanji 0")


if __name__ == "__main__":
    unittest.main()
