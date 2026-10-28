"""The kit's save file images (port/sdk/bu_vmu.c buMakeBackupFileImage / buAnalyzeBackupFileImage) in behaviour:
compiled at /W4 /WX with a synthetic game.h (the SDK's BUS_BACKUPFILEHEADER as sg_bup.h lays it out) and stub host
calls, then run. The image is checked byte for byte against the VMS file layout built here independently: the
0x80-byte header (VMS comment 0x00, boot ROM comment 0x10, game name 0x30, icon count 0x40, icon speed 0x42,
eye-catch type 0x44, CRC 0x46, data size 0x48), the palette at 0x60, the icons (512 bytes each), the eye-catch (type
1: 8064 bytes, 2: 4544, 3: 2048), the data, zero-padded to whole 512-byte blocks; the CRC is CRC-16/CCITT (XMODEM:
polynomial 0x1021, initial 0, binascii.crc_hqx) over the image's used bytes with the CRC field 0. Then a round trip
(analyze gives back every field, its pointers into the image, result 0) and a corrupted image (one data byte, or the
CRC field itself: nonzero). Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how).

usage: python -W error tests/test_kit_buimage.py
"""
import binascii
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
typedef struct BUS_BACKUPFILEHEADER {
    char vmsComment[18];
    char btrComment[34];
    u8 gameName[16];
    void *iconPalette;
    void *iconData;
    u16 iconCount;
    u16 iconSpeed;
    void *visualData;
    u16 visualType;
    u16 reserved;
    void *saveData;
    u32 saveSize;
} BUS_BACKUPFILEHEADER;
"""

DRIVER_C = r"""#include "game.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
s32 buMakeBackupFileImage(void *buf, const BUS_BACKUPFILEHEADER *header);
s32 buAnalyzeBackupFileImage(BUS_BACKUPFILEHEADER *header, void *buf);

void port_log(const char *fmt, ...) { (void)fmt; }
void host_local_time(HostTime *t) { memset(t, 0, sizeof *t); }
void port_add_vblank_task(PortVblankFn fn, void *arg) { (void)fn, (void)arg; }
PortCodeFn port_code_function(u32 addr) { (void)addr; return NULL; }
int host_save_read(const char *name, void *buf, u32 size) { (void)name, (void)buf, (void)size; return 0; }
int host_save_write(const char *name, const void *buf, u32 size) { (void)name, (void)buf, (void)size; return 1; }
u32 maple_card_sockets(void) { return 0; }

#define SAVE_SIZE 1000u
static u8 palette[32], icons[2 * 512], eye[2048], data[SAVE_SIZE], image[16 * 512];

static void analyze(const char *what)
{
    BUS_BACKUPFILEHEADER h;
    s32 r = buAnalyzeBackupFileImage(&h, image);
    printf("%s %d %.16s|%.32s|%.16s %u %u %u %u %d %d %d %d\n", what, r, h.vmsComment, h.btrComment,
           (const char *)h.gameName, h.iconCount, h.iconSpeed, h.visualType, h.saveSize,
           (int)((u8 *)h.iconPalette - image), (int)((u8 *)h.iconData - image),
           h.visualData ? (int)((u8 *)h.visualData - image) : -1, (int)((u8 *)h.saveData - image));
}

int main(int argc, char **argv)
{
    BUS_BACKUPFILEHEADER h;
    s32 blocks;
    FILE *f;
    (void)argc;
    for (u32 i = 0; i < sizeof palette; i++) palette[i] = (u8)(0xa0u + i);
    for (u32 i = 0; i < sizeof icons; i++) icons[i] = (u8)(i * 7u + 1u);
    for (u32 i = 0; i < sizeof eye; i++) eye[i] = (u8)(i * 13u + 5u);
    for (u32 i = 0; i < SAVE_SIZE; i++) data[i] = (u8)(i * 31u + 3u);
    memset(image, 0xee, sizeof image);
    memset(&h, 0, sizeof h);
    memcpy(h.vmsComment, "FIXTURE SAVE    ", 16);
    memcpy(h.btrComment, "Fixture: the system file        ", 32);
    memcpy(h.gameName, "FIXTURE_GAME    ", 16);
    h.iconPalette = palette, h.iconData = icons, h.iconCount = 2, h.iconSpeed = 10;
    h.visualData = eye, h.visualType = 3, h.saveData = data, h.saveSize = SAVE_SIZE;
    blocks = buMakeBackupFileImage(image, &h);
    printf("blocks %d\n", blocks);
    f = fopen(argv[1], "wb");
    fwrite(image, 1, sizeof image, f);
    fclose(f);
    analyze("analyze");
    image[0x80 + sizeof icons + sizeof eye + 17] ^= 0x40u;
    analyze("data-corrupt");
    image[0x80 + sizeof icons + sizeof eye + 17] ^= 0x40u;
    analyze("restored");
    image[0x46] ^= 0x01u;
    analyze("crc-corrupt");
    return 0;
}
"""

SAVE_SIZE = 1000


def expected_image():
    """The VMS file image, laid out from the format, independently of the kit."""
    palette = bytes(0xA0 + i for i in range(32))
    icons = bytes((i * 7 + 1) & 0xFF for i in range(2 * 512))
    eye = bytes((i * 13 + 5) & 0xFF for i in range(2048))
    data = bytes((i * 31 + 3) & 0xFF for i in range(SAVE_SIZE))
    head = bytearray(0x80)
    head[0x00:0x10] = b"FIXTURE SAVE    "
    head[0x10:0x30] = b"Fixture: the system file        "
    head[0x30:0x40] = b"FIXTURE_GAME    "
    head[0x40:0x42] = (2).to_bytes(2, "little")
    head[0x42:0x44] = (10).to_bytes(2, "little")
    head[0x44:0x46] = (3).to_bytes(2, "little")
    head[0x48:0x4C] = SAVE_SIZE.to_bytes(4, "little")
    head[0x60:0x80] = palette
    used = bytearray(head + icons + eye + data)
    used[0x46:0x48] = binascii.crc_hqx(bytes(used), 0).to_bytes(2, "little")
    blocks = (len(used) + 511) // 512
    return bytes(used) + bytes(blocks * 512 - len(used)), blocks


@unittest.skipUnless(HAVE_VC, NO_VC)
class BackupFileImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = pathlib.Path(tempfile.mkdtemp())
        d = cls.dir
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}, bu_image=True), encoding="utf-8")
        (d / "game.h").write_text(GAME_H, encoding="utf-8")
        (d / "driver.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /D_CRT_SECURE_NO_WARNINGS /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "sdk"}" /I"{rd}" '
              f'"{d / "driver.c"}" "{KIT / "sdk" / "bu_vmu.c"}" "{KIT / "sdk" / "vmu_flash.c"}" /Fe"{d / "bu.exe"}"')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        cls.build = r.stdout[-3000:] + r.stderr[-1000:] if r.returncode or "warning" in r.stdout.lower() else ""
        cls.out = "" if cls.build else subprocess.run([str(d / "bu.exe"), str(d / "image.bin")], capture_output=True,
                                                      text=True, timeout=60).stdout.splitlines()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def line(self, key):
        self.assertEqual(self.build, "", "the driver does not build at /W4 /WX")
        return next(ln for ln in self.out if ln.split()[0] == key)

    def test_crc_is_ccitt(self):
        """binascii.crc_hqx with initial 0 is CRC-16/XMODEM (CCITT polynomial 0x1021): its check value."""
        self.assertEqual(binascii.crc_hqx(b"123456789", 0), 0x31C3)

    def test_layout(self):
        want, blocks = expected_image()
        self.assertEqual(self.line("blocks"), f"blocks {blocks}")
        got = (self.dir / "image.bin").read_bytes()[:len(want)]
        diff = [i for i in range(len(want)) if got[i] != want[i]]
        self.assertEqual(diff[:8], [], f"image differs at {[hex(i) for i in diff[:8]]}")

    def test_round_trip(self):
        icons, eye = 0x80, 0x80 + 1024
        fields = f"FIXTURE SAVE    |Fixture: the system file        |FIXTURE_GAME     2 10 3 {SAVE_SIZE}"
        self.assertEqual(self.line("analyze"), f"analyze 0 {fields} 96 {icons} {eye} {eye + 2048}")
        self.assertEqual(self.line("restored").split()[1], "0")

    def test_corrupted(self):
        self.assertNotEqual(self.line("data-corrupt").split()[1], "0")
        self.assertNotEqual(self.line("crc-corrupt").split()[1], "0")


if __name__ == "__main__":
    unittest.main()
