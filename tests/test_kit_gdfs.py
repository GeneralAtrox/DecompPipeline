"""The kit's gdFs (port/sdk/gdfs.c) in behaviour: compiled at /W4 /WX with a synthetic game.h over a fake disc (one
5-sector file), then run. Covers the streaming calls: Gd_StreamGetStat is the handle's error status (0, or the
bad-handle error); Gd_ServerTick does nothing. test_req_rd32 is the library's gdFsReqRd32: the
sectors it took (fewer at the end of the file, 0 at the end), as games that loop `while (r == 0)` and advance by the
count need. Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped
elsewhere.

usage: python -W error tests/test_kit_gdfs.py   (also from tests/test_tooling.py)
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

GAME_H = """#include "rd_rt.h"
typedef struct GdfsHandle *GDFS;
typedef struct GdfsDirRec *GDFS_DIRREC;
typedef void (*SdkProc)(void *);
#define GDD_DRVSTAT_STANDBY 1
#define GDD_DRVSTAT_PAUSE 2
#define GDD_DRVSTAT_PLAY 3
#define GDD_SEEK_CUR 1
#define GDD_SEEK_END 2
s32 Gd_StreamGetStat(void *stream);
void Gd_ServerTick(void);
"""

# The disc: a root directory at LBA 20 holding MOVIE.SFD, 5 sectors at LBA 100 (its last one partly used). Sector n
# holds the byte n & 0xff.
DRIVER_C = r"""#include "platform.h"
#include <stdio.h>
#include <string.h>
typedef struct GdfsHandle *GDFS;
s32 gdFsInit(s32 maxOpen, void *work, s32 maxDirent, void *dirBuffer);
GDFS gdFsOpen(const char *fileName, void *dirRec);
void gdFsClose(GDFS gdfs);
s32 gdFsReqRd32(GDFS gdfs, s32 sectors, void *buffer);
s32 gdFsTell(GDFS gdfs);
s32 Gd_StreamGetStat(void *stream);
void Gd_ServerTick(void);

void port_log(const char *fmt, ...) { (void)fmt; }
int host_disc_root(u32 *lba, u32 *size) { *lba = 20, *size = 2048; return 1; }
int host_disc_read(u32 lba, u32 count, void *out)
{
    for (u32 i = 0; i < count; i++) memset((u8 *)out + i * 2048u, (int)((lba + i) & 0xff), 2048u);
    return 1;
}
int host_disc_find(u32 lba, u32 size, const char *name, HostDirEnt *out)
{
    (void)lba, (void)size;
    if (strcmp(name, "MOVIE.SFD")) return 0;
    out->lba = 100, out->size = 4 * 2048 + 100, out->is_dir = 0;
    memcpy(out->name, name, strlen(name) + 1);
    return 1;
}
int host_disc_list(u32 lba, u32 size, HostDirEnt *out, int max)
{
    (void)lba, (void)size, (void)out, (void)max;
    return 0;
}
int host_disc_toc(u32 *toc) { (void)toc; return 0; }
int host_cd_play(u32 first, u32 last, u32 repeat) { (void)first, (void)last, (void)repeat; return 0; }
void host_cd_stop(void) {}
void host_cd_pause(int paused) { (void)paused; }
int host_cd_playing(void) { return 0; }

static __declspec(align(32)) u8 buf[8 * 2048 + 32];

int main(void)
{
    GDFS h;
    s32 r;
    printf("init %d\n", gdFsInit(4, NULL, 0, NULL));
    h = gdFsOpen("\\MOVIE.SFD", NULL);
    r = gdFsReqRd32(h, 3, buf);
    printf("first %d tell %d bytes %d %d\n", r, gdFsTell(h), buf[0], buf[3 * 2048 - 1]);
    r = gdFsReqRd32(h, 4, buf);
    printf("clipped %d tell %d byte %d\n", r, gdFsTell(h), buf[2048]);
    printf("end %d\n", gdFsReqRd32(h, 1, buf));
    printf("misaligned %d null %d\n", gdFsReqRd32(h, 1, buf + 4), gdFsReqRd32(NULL, 1, buf));
    printf("stat %d null %d\n", Gd_StreamGetStat(h), Gd_StreamGetStat(NULL));
    Gd_ServerTick();
    gdFsClose(h);
    printf("closed %d %d\n", Gd_StreamGetStat(h), gdFsReqRd32(h, 1, buf));
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class Streaming(unittest.TestCase):
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
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" /I"{rd}" "{d / "driver.c"}" '
              f'"{KIT / "sdk" / "gdfs.c"}" /Fe"{d / "gdfs.exe"}"')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        cls.build = r.stdout[-3000:] + r.stderr[-1000:] if r.returncode or "warning" in r.stdout.lower() else ""
        cls.out = "" if cls.build else subprocess.run([str(d / "gdfs.exe")], capture_output=True, text=True,
                                                      timeout=60).stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def line(self, key):
        self.assertEqual(self.build, "")
        return next(x for x in self.out.splitlines() if x.startswith(key + " "))

    def test_req_rd32(self):
        self.assertEqual(self.line("init"), "init 0")
        self.assertEqual(self.line("first"), "first 3 tell 3 bytes 100 102")  # the sectors taken, then the next
        self.assertEqual(self.line("clipped"), "clipped 2 tell 5 byte 104")   # fewer at the end of the file
        self.assertEqual(self.line("end"), "end 0")
        self.assertEqual(self.line("misaligned"), "misaligned -14 null -10")

    def test_stream_stat(self):
        self.assertEqual(self.line("stat"), "stat 0 null -10")
        self.assertEqual(self.line("closed"), "closed -10 -10")


if __name__ == "__main__":
    unittest.main()
