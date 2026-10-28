"""The kit's VMU LCD (port/sdk/maple_pad.c's pdVmsLcd*) in behaviour: compiled at /W4 /WX with a synthetic game.h and
stub host calls, then run. As the SDK (Shinobi 1.4, Toy Commander's copy at 8c0b6160..8c0b62ee and 8c0c096c..8c0c0a16)
does it: an LCD is ready where --vmu put a card (A1 by default, none with --vmu none); the direction is the SDK's
table at the devices' connection bytes (both 0: 1); a write returns 0 at once, or -2 with no LCD; the block it
sends is the bitmap's bit 3 per dot, in the flag's orientation (SDK_BLOCK below follows the SDK's four loops step
by step); the block reaches the card (logged, shown) at the next peripheral server pass, turned as the player sees
it in the controller. The host's VMU screen (port/runtime/gfx/gfx_vmu.c, compiled with a driver that includes it):
off by default; "side" sits in the bar beside the 4:3 picture and draws nothing where the bar is too narrow; "over"
sits in the picture's corner; the painted panel has each lit dot dark and the others the glass's grid colour
(PANEL_PNG keeps a copy for the eye). Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py
NO_VC says how).

usage: python -W error tests/test_kit_vmulcd.py
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
typedef struct LibPadDevice LibPadDevice;
typedef struct { u32 unit, flag; s8 power, freq, inc; } PDS_VIBPARAM;
"""

DRIVER_C = r"""#include "platform.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
s32 pdVmsLcdIsReady(u32 port);
s32 pdVmsLcdGetDirection(u32 port);
s32 pdVmsLcdWrite(u32 port, const void *bitmap, u32 flag);
void pdInitPeripheral(s32 logic, void *recvBuffer, void *sendBuffer);
void pdExecPeripheralServer(void);

static const char *g_vmu;
const char *host_arg(const char *name) { return strcmp(name, "--vmu") ? NULL : g_vmu; }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("log ");
    vprintf(fmt, ap);
    va_end(ap);
}
u32 host_pads_read(HostPad pad[HOST_PADS]) { memset(pad, 0, sizeof(HostPad) * HOST_PADS); return 1u; }
void host_pad_rumble(u32 player, u32 strength, u32 ms) { (void)player, (void)strength, (void)ms; }
void host_vmu_lcd(u32 card, const u8 *dots)
{
    printf("seen %u ", card);
    for (u32 i = 0; i < 48u * 32u; i++) putchar(dots[i] ? '1' : '0');
    putchar('\n');
}

static u8 bitmap[48 * 32];

int main(void)
{
    pdInitPeripheral(0, NULL, NULL); /* as a game starts the bus */
    for (u32 i = 0; i < 48u * 32u; i++) bitmap[i] = (u8)((i * 7u + i / 48u * 3u) % 5u == 0u ? 0x0f : 0x07);
    printf("ready %d %d %d %d dir %d\n", pdVmsLcdIsReady(1), pdVmsLcdIsReady(2), pdVmsLcdIsReady(7),
           pdVmsLcdIsReady(0), pdVmsLcdGetDirection(1));
    printf("nolcd %d\n", pdVmsLcdWrite(2, bitmap, 0));
    for (u32 flag = 0; flag < 4u; flag++) {
        printf("write %u %d\n", flag, pdVmsLcdWrite(1, bitmap, flag));
        printf("queued\n");
        pdExecPeripheralServer();
        printf("served\n");
        pdExecPeripheralServer();
    }
    g_vmu = "none";
    printf("none %d %d\n", pdVmsLcdIsReady(1), pdVmsLcdWrite(1, bitmap, 0));
    g_vmu = "A1,B2";
    printf("two %d %d %d\n", pdVmsLcdIsReady(1), pdVmsLcdIsReady(8), pdVmsLcdIsReady(7));
    return 0;
}
"""

W, H = 48, 32


def bitmap():
    return [0x0F if (i * 7 + i // W * 3) % 5 == 0 else 0x07 for i in range(W * H)]


def SDK_BLOCK(src, flag):
    """The SDK's conversion, loop for loop: 8c0c09f6 (0, any other flag), 8c0c099e (1), 8c0c096c (2), 8c0c09ca (3)."""
    out, s = [0] * 192, iter(src)

    def byte(first, step):
        b, mask = 0, first
        for _ in range(8):
            if next(s) & 8:
                b |= mask
            mask = mask << 1 if step else mask >> 1
        return b

    if flag == 1:
        at = 5
        for _ in range(32):
            for _ in range(6):
                out[at] = byte(1, 1)
                at -= 1
            at += 12
    elif flag == 2:
        at = 0xBA
        for _ in range(32):
            for _ in range(6):
                out[at] = byte(0x80, 0)
                at += 1
            at -= 12
    elif flag == 3:
        at = 0xC0
        for _ in range(32):
            for _ in range(6):
                at -= 1
                out[at] = byte(1, 1)
    else:
        for k in range(192):
            out[k] = byte(0x80, 0)
    return bytes(out)


PANEL_C = r"""#include <stdio.h>
#include "gfx_vmu.c"
GfxState g_gfx;

int main(int argc, char **argv)
{
    static u8 dots[48 * 32];
    static u32 px[PANEL_W(4) * PANEL_H(4)];
    u32 x = 0, y = 0, s;
    Panel p = {0};
    (void)argc;
    printf("mode %d\n", gfx_vmu_mode());
    gfx_vmu_option("--vmu-screen", "side");
    g_gfx.out_w = 1920, g_gfx.out_h = 1080, g_gfx.frame_x = 240, g_gfx.frame_y = 0;
    s = place(&x, &y);
    printf("side %u %u %u mode %d\n", s, x, y, gfx_vmu_mode());
    g_gfx.frame_x = 0;
    printf("narrow %u\n", place(&x, &y));
    gfx_vmu_option("--vmu-screen", "over");
    s = place(&x, &y);
    printf("over %u %u %u\n", s, x, y);
    for (u32 i = 0; i < 48u * 32u; i++) dots[i] = (u8)((i % 48u + i / 48u) % 3u == 0u);
    memcpy(p.dots, dots, sizeof dots);
    paint(&p, 4, px);
    printf("dot %08x %08x %08x %08x\n", px[16 * PANEL_W(4) + 16], px[16 * PANEL_W(4) + 21], px[19 * PANEL_W(4) + 19],
           px[0]);
    {
        FILE *f = fopen(argv[1], "wb");
        fprintf(f, "P6 %u %u 255\n", PANEL_W(4), PANEL_H(4));
        for (u32 i = 0; i < PANEL_W(4) * PANEL_H(4); i++) fwrite(&px[i], 1, 3, f);
        fclose(f);
    }
    return 0;
}
"""
PANEL_PNG = pathlib.Path(tempfile.gettempdir()) / "vmu_panel.png"


def seen(block):
    """Flycast's screen from a block (core/hw/maple/maple_devs.cpp decodes each row from its last byte, low bit
    first; the overlay then draws the rows bottom up): the block read backwards."""
    dots = [block[i // 8] >> (7 - i % 8) & 1 for i in range(W * H)]
    return "".join(str(d) for d in reversed(dots))


@unittest.skipUnless(HAVE_VC, NO_VC)
class VmuLcd(unittest.TestCase):
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
              f'"{KIT / "sdk" / "maple_pad.c"}" /Fe"{d / "lcd.exe"}"')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        cls.build = r.stdout[-3000:] + r.stderr[-1000:] if r.returncode or "warning" in r.stdout.lower() else ""
        cls.out = "" if cls.build else subprocess.run([str(d / "lcd.exe")], capture_output=True, text=True,
                                                      timeout=60).stdout.splitlines()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def line(self, key):
        self.assertEqual(self.build, "")
        return next(x for x in self.out if x.startswith(key + " "))

    def test_ready_and_direction(self):
        self.assertEqual(self.line("ready"), "ready 1 0 0 0 dir 1")  # A1's card; not A2's pack, B1, the pad
        self.assertEqual(self.line("none"), "none 0 -2")
        self.assertEqual(self.line("two"), "two 1 1 0")               # --vmu A1,B2: ports 1 and 8

    def test_no_lcd(self):
        self.assertEqual(self.line("nolcd"), "nolcd -2")

    def test_blocks(self):
        self.assertEqual(self.build, "")
        src = bitmap()
        logs = [x for x in self.out if x.startswith("log maple: A1 LCD")]
        shown = [x for x in self.out if x.startswith("seen ")]
        self.assertEqual(len(logs), 4)
        self.assertEqual(len(shown), 4)
        for flag in range(4):
            block = SDK_BLOCK(src, flag)
            self.assertEqual(logs[flag], f"log maple: A1 LCD write {flag + 1}: {block.hex()}", flag)
            self.assertEqual(shown[flag], f"seen 0 {seen(block)}", flag)
        self.assertEqual(SDK_BLOCK(src, 3), SDK_BLOCK(src, 0)[::-1].translate(  # 3 is 0 turned: bits reversed too
            bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))))

    def test_delivered_at_the_next_server_pass(self):
        self.assertEqual(self.build, "")
        i = self.out.index("write 0 0")
        self.assertEqual(self.out[i + 1], "queued")                    # nothing reached the card at the write
        self.assertTrue(self.out[i + 2].startswith("log maple: A1 LCD write 1: "))
        self.assertEqual(self.out[i + 4], "served")                    # one delivery per write
        self.assertEqual(self.out[i + 5], "write 1 0")


@unittest.skipUnless(HAVE_VC, NO_VC)
class VmuScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = pathlib.Path(tempfile.mkdtemp())
        d = cls.dir
        (d / "panel.c").write_text(PANEL_C, encoding="utf-8")
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        gfx = KIT / "runtime" / "gfx"
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{d}" /I"{gfx}" /I"{KIT / "runtime"}" /I"{KIT / "host"}" '
              f'/I"{rd}" "{d / "panel.c"}" /Fe"{d / "panel.exe"}" /link d3d11.lib')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        cls.build = r.stdout[-3000:] + r.stderr[-1000:] if r.returncode or "warning" in r.stdout.lower() else ""
        cls.out = [] if cls.build else subprocess.run([str(d / "panel.exe"), str(d / "panel.ppm")], capture_output=True,
                                                      text=True, timeout=60).stdout.splitlines()
        if not cls.build:
            frames_png(d / "panel.ppm", PANEL_PNG)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def line(self, key):
        self.assertEqual(self.build, "")
        return next(x for x in self.out if x.startswith(key + " "))

    def test_modes_and_places(self):
        self.assertEqual(self.line("mode"), "mode 0")              # off by default
        self.assertEqual(self.line("side"), "side 3 36 18 mode 1")  # 168 pixels wide in the 240-pixel bar
        self.assertEqual(self.line("narrow"), "narrow 0")          # no bar: nothing (never over the picture)
        self.assertEqual(self.line("over"), "over 6 18 18")        # the picture's corner, 6-pixel dots at 1080

    def test_painted_dots(self):
        # dot (0, 0) is lit: its cell starts 16 pixels in (4 dots of case and glass at 4 pixels); dot (1, 0) is not;
        # the cell's last row and column are the grid's gap; the corner is the case's rim. Pixels are ABGR.
        self.assertEqual(self.line("dot"), "dot ff3e2c1f ff97ae9a ffa0b8a3 ff90928a")


def frames_png(ppm, png):
    """The panel as a PNG, for the eye."""
    from PIL import Image
    Image.open(ppm).save(png)


if __name__ == "__main__":
    unittest.main()
